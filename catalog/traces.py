#!/usr/bin/env python3
"""traces.py CATALOG.db HISTORY.jsonl OUTDIR   -> private Drake-voice trace dataset

One row per Wispr dictation (with text): what the agent was doing before, Drake's words, the agent's
reply and next actions, downstream agent coordination, labels and the cause of anger or corrections.
Writes OUTDIR/traces.jsonl, OUTDIR/pairs/*.jsonl, OUTDIR/STATS.md, OUTDIR/scrub_stats.json, OUTDIR/verify_sample.jsonl.
PRIVATE: the output never leaves the Spark. Every text field goes through redact.py and scrub.py.
"""
import sys, os, json, re, sqlite3, collections, datetime, zlib, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import Redactor
from scrub import Scrubber
from wispr_match import Matcher, load_wispr, epoch, norm
from labels import label, causes, CAUSES

N_BEFORE = 6
HOME = os.path.expanduser("~")
TZ_NOTE = "Wispr stores one offset for every row: 300 minutes, so local time = UTC-5 (US Central daylight time)."

AGENT_URLS = ("chatgpt.com", "gemini.google.com", "grok.com", "claude.ai", "127.0.0.1", "localhost", "spark.", "openrouter.ai", "duck.ai",
              "notebook.google.com", "aistudio.google.com", "chat.deepseek.com", "github.com")
NONAGENT_URLS = ("torn.com", "tornstats.com", "youtube.com", "x.com", "account.proton.me")
APP_NAMES = {
    "com.mitchellh.ghostty": "Ghostty", "com.apple.Terminal": "Terminal", "com.nousresearch.hermes": "Hermes desktop",
    "com.google.antigravity": "Antigravity", "com.t3tools.t3code": "T3 Code", "com.openai.codex": "Codex desktop",
    "com.google.Chrome": "Chrome", "com.meta.endo": "com.meta.endo", "com.hnc.Discord": "Discord",
    "com.google.Chrome.app.hgiemfgfjhalibdoboikeiepnnjapnpc": "DeepSeek Harness (Chrome app)",
    "com.google.Chrome.app.ffcjbkffcbcbgdeiegbajlbgpmdekfja": "Torn (Chrome app)",
    "com.google.Chrome.app.bipppfendfaipmoenbkohnnfcnlfdbhp": "Chrome app bippp... (agent chat, Sep 15-16)",
    "com.anysphere.sand": "com.anysphere.sand (Cursor family)", "com.todesktop.230313mzl4w4u92": "com.todesktop.230313 (Cursor)",
}
AGENT_APPS = {"com.mitchellh.ghostty", "com.apple.Terminal", "com.nousresearch.hermes", "com.google.antigravity", "com.t3tools.t3code",
              "com.openai.codex", "com.meta.endo", "com.google.Chrome.app.hgiemfgfjhalibdoboikeiepnnjapnpc",
              "com.google.Chrome.app.bipppfendfaipmoenbkohnnfcnlfdbhp", "com.anysphere.sand", "com.todesktop.230313mzl4w4u92", "com.microsoft.VSCode"}
NONAGENT_APPS = {"com.hnc.Discord", "com.google.Chrome.app.ffcjbkffcbcbgdeiegbajlbgpmdekfja"}

def domain(url):
    if not url or url == "unknown": return None
    m = re.match(r"^(?:[a-z]+://)?([^/:?#\s]+)", url)   # Wispr keeps only the host ("chatgpt.com"); full URLs are cut to it
    return m.group(1).lower() if m else None

def context_type(d, matched):
    if matched: return "agent-chat"
    app, dom = d.get("app"), domain(d.get("url")) or ""
    if app in NONAGENT_APPS or any(dom.endswith(u) for u in NONAGENT_URLS): return "non-agent"
    if app in AGENT_APPS or any(u in dom for u in AGENT_URLS): return "agent-unmatched"
    return "other"

def local_dt(t): return datetime.datetime.fromtimestamp(t, datetime.timezone.utc) - datetime.timedelta(minutes=300)

def split_of(day):
    h = zlib.crc32(day.encode()) % 10
    return "test" if h == 0 else ("val" if h == 1 else "train")

APOLOGY = re.compile(r"^(?:\s*(?:you(?:'re| are) (?:absolutely |completely )?right|sorry|my (?:apologies|mistake|bad)|i apologi[sz]e|good (?:catch|call)|fair (?:point|enough)|understood|got it)[^.!\n]*[.!\n]\s*)+", re.I)

class Cat:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.sessions = {r["id"]: dict(r) for r in self.db.execute("select * from sessions")}
        for s in self.sessions.values(): s["t0"] = epoch(s.get("started_at"))
        self.children = collections.defaultdict(list)
        for s in self.sessions.values():
            if s.get("parent_id"): self.children[s["parent_id"]].append(s)
        self.launchable = sorted([s for s in self.sessions.values() if not s.get("is_subagent") and s["t0"] and
                                  ((s.get("entrypoint") or "").startswith("sdk") or s.get("entrypoint") == "codex_exec")], key=lambda s: s["t0"])
        self._cache = {}

    def first_user(self, sid):
        """First prompt of a session whatever its author (claude -p and codex exec prompts are written by agents)."""
        s = self.sessions[sid]
        if "first_user" not in s:
            r = self.db.execute("select text from messages where session_id=? and role='user' and author<>'system' order by seq limit 1", (sid,)).fetchone()
            s["first_user"] = (r[0] if r else "") or ""
        return s["first_user"]

    def msgs(self, sid):
        if sid not in self._cache:
            if len(self._cache) > 400: self._cache.clear()
            self._cache[sid] = [dict(r) for r in self.db.execute(
                "select id,seq,role,ts,text,tools,author,tool_detail from messages where session_id=? order by seq", (sid,))]
            for m in self._cache[sid]:
                m["t"] = epoch(m["ts"]); m["detail"] = json.loads(m["tool_detail"]) if m.get("tool_detail") else [[x, ""] for x in (m["tools"] or "").split(",") if x]
        return self._cache[sid]

def short(t, n): t = " ".join((t or "").split()); return t if len(t) <= n else t[:n] + "..."

def turn_after(ms, i):
    """Messages after index i up to (not including) the next human message."""
    out = []
    for m in ms[i + 1:]:
        if m["author"] == "human": break
        out.append(m)
    return out

ID_RX = re.compile(r"\bL\d{3}\b")
BRIEF_RX = re.compile(r"(?:~|/home/\w+)/handoffs/briefs/[\w.\-]+")

def downstream(cat, sid, t_msg, t_end, after):
    tools = [(m, name, arg) for m in after for name, arg in (m["detail"] or [])]
    subagent_calls = [{"tool": n, "args": short(a, 240), "ts": m["ts"]} for m, n, a in tools if n in ("Agent", "Task", "spawn_agent", "delegate_task", "SendMessage", "TaskCreate")]
    launches = [{"args": short(a, 300), "ts": m["ts"]} for m, n, a in tools if n.lower() in ("bash", "exec", "exec_command", "run_terminal_command", "shell", "terminal")
                and re.search(r"claude -p|claude --model|codex exec|grok -p|nohup claude|L\d{3}-launch|lanes\.sh", a)]
    text_all = "\n".join([m["text"] or "" for m in after] + [a for _, _, a in tools])
    ledger_ids = sorted(set(ID_RX.findall(text_all)))
    ledger_cmds = [{"args": short(a, 240), "ts": m["ts"]} for m, n, a in tools if re.search(r"\bledger (?:add|note|done|owner|drop)\b", a)]
    briefs = sorted(set(BRIEF_RX.findall(text_all)))
    board = [{"args": short(a, 200), "ts": m["ts"]} for m, n, a in tools if re.search(r"BOARD\.md|restart-requests\.md|TEAM-MEMORY", a)]
    files = sorted({re.sub(r" \|.*", "", a.split("file_path=", 1)[1]) for m, n, a in tools if n in ("Edit", "Write", "MultiEdit", "NotebookEdit", "search_replace", "write_file") and "file_path=" in a})
    lo, hi = t_msg - 5, (t_end or t_msg + 3600) + 600
    child = [{"session_id": s["id"], "started": s["started_at"], "title": short(s.get("title"), 120), "first_prompt": short(s.get("first_prompt"), 200), "link": "parent"}
             for s in cat.children.get(sid, []) if s["t0"] and lo <= s["t0"] <= hi]
    keys = set(ledger_ids) | {os.path.basename(b) for b in briefs}
    launched = []
    if launches or keys:
        for s in cat.launchable:
            if s["t0"] < lo: continue
            if s["t0"] > hi: break
            fp = cat.first_user(s["id"])
            hit = [k for k in keys if k in fp]
            if hit or (launches and any(norm(fp)[:8] and " ".join(norm(fp)[:8]) in " ".join(norm(l["args"])) for l in launches)):
                launched.append({"session_id": s["id"], "started": s["started_at"], "first_prompt": short(fp, 200), "link": "ref:" + ",".join(hit) if hit else "launch-command"})
    return {"subagent_calls": subagent_calls[:12], "child_sessions": child[:20], "launched_sessions": launched[:20], "launch_commands": launches[:10],
            "ledger_ids": ledger_ids[:20], "ledger_commands": ledger_cmds[:10], "briefs": briefs[:10], "board_posts": board[:10], "files_touched": files[:30]}

def main(cat_path, hist_path, out):
    os.makedirs(os.path.join(out, "pairs"), exist_ok=True)
    R, S = Redactor(HOME), Scrubber()
    def clean(t):
        t, _ = R.clean(t or ""); t, _ = S.clean(t); return t
    cat = Cat(cat_path)
    mlp = os.path.join(out, "model_labels.jsonl")
    ML = {}
    if os.path.exists(mlp):
        for l in open(mlp): d = json.loads(l); ML[d["id"]] = d
    M = Matcher(cat.db, R)
    W = load_wispr(hist_path)
    W = [d for d in W if (d.get("asrText") or d.get("formattedText"))]
    print("dictations with text:", len(W), file=sys.stderr)

    # ---- match, plus the shifted negative control ----
    best = {}
    for d in W:
        m = M.match(d)
        if m: best[d["transcriptEntityId"]] = m
    ctrl = collections.Counter()
    for d in W:
        m = M.match(d, shift=24 * 3600)
        if m: ctrl[m["tier"]] += 1
    by_msg = collections.defaultdict(list)
    for d in W:
        m = best.get(d["transcriptEntityId"])
        if m: by_msg[m["mid"]].append(d)
    parts = {}
    for mid, ds in by_msg.items():
        ds.sort(key=lambda d: d["t"])
        cov = M.coverage(mid, [M.variants(d)[0][1] for d in ds if M.variants(d)]) if len(ds) > 1 or True else None
        for i, d in enumerate(ds): parts[d["transcriptEntityId"]] = {"n": len(ds), "index": i, "coverage": cov}

    # ---- label every human message once (needed for preference pairs and 'drake reaction' fields) ----
    traces, verify_pool = [], []
    for d in W:
        wid = d["transcriptEntityId"]; m = best.get(wid)
        asr, fmt = d.get("asrText") or "", d.get("formattedText") or ""
        lt = local_dt(d["t"]); day = lt.strftime("%Y-%m-%d")
        lab = label(asr + "\n" + fmt)
        row = {"id": wid, "ts_utc": d.get("timestamp"), "local_time": lt.strftime("%Y-%m-%d %H:%M"), "tz_offset_min": d.get("timezoneOffsetMinutes"),
               "day": day, "split": split_of(day), "app": d.get("app"), "app_name": APP_NAMES.get(d.get("app"), d.get("app")),
               "url_domain": domain(d.get("url")), "context": context_type(d, bool(m)),
               "drake": {"asr": clean(asr), "formatted": clean(fmt), "pasted": clean(d.get("pastedText")) if (d.get("pastedText") or "").strip() != fmt.strip() else None,
                         "edited": clean(d.get("editedText")) if d.get("editedText") else None, "words": d.get("numWords"), "status": d.get("status")},
               "labels": sorted(lab), "label_signals": lab, "label_source": "rules-v1"}
        if wid in ML:
            row["model_labels"] = {k2: ML[wid].get(k2) for k2 in ("labels", "cause", "why", "model")}
            row["labels_rules"] = row["labels"]; row["labels"] = ML[wid]["labels"]; row["label_source"] = "model (sonnet) ; rules-v1 kept in labels_rules + label_signals"
        if m:
            s = cat.sessions.get(m["session_id"], {})
            ms = cat.msgs(m["session_id"])
            i = next((k for k, x in enumerate(ms) if x["id"] == m["mid"]), None)
            before, k = [], (i or 0) - 1
            while i is not None and k >= 0 and len([b for b in before if b["author"] == "assistant"]) < N_BEFORE:
                x = ms[k]; k -= 1
                if x["author"] in ("assistant", "agent", "system") and (x["text"] or x["detail"]):
                    before.append({"msg_id": x["id"], "ts": x["ts"], "author": x["author"], "text": clean(short(x["text"], 600 if x["author"] == "assistant" else 200)),
                                   "tools": [[n, clean(short(a, 200))] for n, a in (x["detail"] or [])[:8]]})
                elif x["author"] == "human" and len(before) == 0:
                    pass
            before.reverse()
            after = turn_after(ms, i) if i is not None else []
            nxt = ms[i + 1 + len(after)] if i is not None and i + 1 + len(after) < len(ms) else None
            t_end = nxt["t"] if nxt else (after[-1]["t"] if after else None)
            asst = [x for x in after if x["role"] == "assistant"]
            row.update({
                "session_id": m["session_id"], "machine": s.get("machine"), "source": s.get("source"), "project": s.get("project"),
                "project_dir": s.get("project_dir"), "session_title": clean(short(s.get("title"), 160)), "is_subagent_session": bool(s.get("is_subagent")),
                "match": {k2: m[k2] for k2 in ("tier", "field", "containment", "dt", "score", "confidence", "author", "msg_words", "dict_words")},
                "match_secondary": m.get("secondary"), "message_parts": parts.get(wid),
                "chat_message": {"msg_id": m["mid"], "seq": m["seq"], "ts": ms[i]["ts"] if i is not None else None,
                                 "text": clean(short(ms[i]["text"], 3000)) if i is not None else None},
                "before": before,
                "after": {"reply": clean(short("\n\n".join(x["text"] for x in asst if x["text"]), 2500)),
                          "n_assistant_msgs": len(asst),
                          "tools": [[n, clean(short(a, 200))] for x in after for n, a in (x["detail"] or [])][:40],
                          "duration_s": round(t_end - ms[i]["t"], 1) if (t_end and i is not None and ms[i]["t"]) else None,
                          "next_human": {"msg_id": nxt["id"], "ts": nxt["ts"], "text": clean(short(nxt["text"], 600)), "labels": sorted(label(nxt["text"]))} if nxt else None},
            })
            ds = downstream(cat, m["session_id"], ms[i]["t"] if i is not None and ms[i]["t"] else d["t"], t_end, after)
            for key in ("subagent_calls", "launch_commands", "ledger_commands", "board_posts"):
                for e in ds[key]: e["args"] = clean(e["args"])
            for key in ("child_sessions", "launched_sessions"):
                for e in ds[key]:
                    e["first_prompt"] = clean(e["first_prompt"]); e["title"] = clean(e.get("title"))
            row["downstream"] = ds
            if "anger" in lab or "correction" in lab or {"anger", "correction"} & set(row["labels"]):
                last_a = next((b for b in reversed(before) if b["author"] == "assistant"), None)
                cs = causes(asr + "\n" + fmt, last_a["text"] if last_a else "", [n + " " + a for b in before[-2:] for n, a in b["tools"]])
                row["causes"] = cs or [{"cause": "UNKNOWN", "drake_signal": None, "agent_signal": None}]
                row["cause_ref"] = last_a["msg_id"] if last_a else None
            mlab = row.get("model_labels")
            if {"anger", "correction"} & set(row["labels"]):
                row["cause"] = mlab["cause"] if mlab and mlab.get("cause") not in (None, "none") else row["causes"][0]["cause"]
                row["cause_why"] = mlab.get("why") if mlab else None
            verify_pool.append(row)
        else:
            row.update({"session_id": None, "machine": None, "source": None, "project": None, "match": None})
            if "anger" in lab or "correction" in lab:
                cs = causes(asr + "\n" + fmt, "", [])
                row["causes"] = cs or [{"cause": "UNKNOWN", "drake_signal": None, "agent_signal": None}]
                row["cause_ref"] = None
        traces.append(row)

    with open(os.path.join(out, "traces.jsonl"), "w") as f:
        for r in traces: f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---- pairs ----
    P = lambda name: open(os.path.join(out, "pairs", name), "w")
    n_pairs = collections.Counter()
    with P("voice_sft.jsonl") as f:
        for r in traces:
            if not r["drake"]["asr"]: continue
            ctx = [f"app: {r['app_name']}", f"context: {r['context']}"]
            if r.get("before"):
                la = next((b for b in reversed(r["before"]) if b["author"] == "assistant"), None)
                if la: ctx.append("agent just said: " + short(la["text"], 500))
                acts = [n + " " + a for b in r["before"][-2:] for n, a in b["tools"]][-5:]
                if acts: ctx.append("agent just did: " + " ; ".join(short(a, 120) for a in acts))
            f.write(json.dumps({"id": r["id"], "split": r["split"], "context_type": r["context"], "prompt": "\n".join(ctx), "completion": r["drake"]["asr"]}, ensure_ascii=False) + "\n")
            n_pairs["voice_sft"] += 1
    with P("predictor.jsonl") as f:
        for r in traces:
            if not r.get("before"): continue
            la = next((b for b in reversed(r["before"]) if b["author"] == "assistant"), None)
            f.write(json.dumps({"id": r["id"], "split": r["split"], "agent_said": la["text"] if la else "", "agent_did": [n + " " + a for b in r["before"][-2:] for n, a in b["tools"]][-8:],
                                "labels": r["labels"], "cause": r.get("cause"), "cause_why": r.get("cause_why"), "rule_causes": [c["cause"] for c in r.get("causes", [])],
                                "label_source": r["label_source"], "drake_said": r["drake"]["formatted"]}, ensure_ascii=False) + "\n")
            n_pairs["predictor"] += 1
    with P("denoise.jsonl") as f:
        for r in traces:
            a, b = r["drake"]["asr"], r["drake"]["formatted"]
            if a and b and a.strip() != b.strip():
                f.write(json.dumps({"id": r["id"], "split": r["split"], "context_type": r["context"], "asr": a, "formatted": b}, ensure_ascii=False) + "\n")
                n_pairs["denoise"] += 1

    # preference: over every human-led session (typed and dictated words both count as Drake's reaction)
    dictated = {r["chat_message"]["msg_id"] for r in traces if r.get("chat_message")}
    msg_model = collections.defaultdict(set)      # chat message -> model labels of the dictations in it
    for r in traces:
        if r.get("chat_message") and r.get("model_labels"): msg_model[r["chat_message"]["msg_id"]] |= set(r["labels"])
    def mlabel(x):
        return {k: ["model"] for k in msg_model[x["id"]]} if x["id"] in msg_model else label(x["text"])
    with P("preference.jsonl") as f, P("accepted.jsonl") as g:
        sids = [sid for sid, s in cat.sessions.items() if (s.get("n_user") or 0) >= 2]
        for sid in sids:
            ms = cat.msgs(sid)
            H = [k for k, x in enumerate(ms) if x["author"] == "human"]
            day = local_dt(ms[H[0]]["t"]).strftime("%Y-%m-%d") if H and ms[H[0]]["t"] else "unknown"
            def turn(k):
                a = turn_after(ms, k)
                return clean(short("\n\n".join(x["text"] for x in a if x["role"] == "assistant" and x["text"]), 3000)), [n for x in a for n, _ in (x["detail"] or [])][:30]
            for j in range(len(H) - 1):
                h, h2 = ms[H[j]], ms[H[j + 1]]
                L2 = mlabel(h2)
                reply, rtools = turn(H[j])
                if not reply: continue
                prev_a = next((x for x in reversed(ms[:H[j]]) if x["role"] == "assistant" and x["text"]), None)
                prompt = {"agent_before": clean(short(prev_a["text"], 600)) if prev_a else "", "drake": clean(short(h["text"], 2000))}
                if "praise" in L2 and "anger" not in L2 and "correction" not in L2:
                    g.write(json.dumps({"session_id": sid, "day": day, "split": split_of(day), "prompt": prompt, "reply": reply, "reply_tools": rtools,
                                        "reaction": clean(short(h2["text"], 400)), "reaction_labels": sorted(L2), "dictated": h["id"] in dictated or h2["id"] in dictated}, ensure_ascii=False) + "\n")
                    n_pairs["accepted"] += 1
                if ("anger" in L2 or "correction" in L2) and j + 2 <= len(H) - 1 + 1:
                    fix, ftools = turn(H[j + 1])
                    if not fix: continue
                    L3 = mlabel(ms[H[j + 2]]) if j + 2 < len(H) else {}
                    if "anger" in L3 or "correction" in L3: strength = None
                    elif "praise" in L3 or "approval" in L3: strength = "praised"
                    else: strength = "not_corrected"
                    if not strength: continue
                    prev_tools = [n + " " + a for x in ms[max(0, H[j]):H[j + 1]] for n, a in (x["detail"] or [])][-6:]
                    cs = causes(h2["text"], reply, prev_tools)
                    f.write(json.dumps({"session_id": sid, "day": day, "split": split_of(day), "prompt": prompt,
                                        "rejected": reply, "rejected_tools": rtools,
                                        "drake_correction": clean(short(h2["text"], 1200)), "correction_labels": sorted(L2),
                                        "causes": [c["cause"] for c in cs] or ["UNKNOWN"],
                                        "chosen": APOLOGY.sub("", fix).strip(), "chosen_tools": ftools, "chosen_strength": strength,
                                        "chosen_context": "written after the correction (the correction is in its context)",
                                        "dictated": h["id"] in dictated or h2["id"] in dictated}, ensure_ascii=False) + "\n")
                    n_pairs["preference"] += 1

    # ---- verify sample (stratified by tier and app) ----
    random.seed(378)
    strata = collections.defaultdict(list)
    for r in verify_pool: strata[(r["match"]["tier"], r["app"])].append(r)
    sample, keys = [], sorted(strata, key=lambda k: -len(strata[k]))
    while len(sample) < 50 and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(sample) < 50:
                sample.append(strata[k].pop(random.randrange(len(strata[k]))))
    vs = os.path.join(out, "verify_sample.jsonl")
    if os.path.exists(os.path.join(out, "verify_results.tsv")): vs = os.path.join(out, "verify_sample.new.jsonl")   # keep the hand-checked sample
    with open(vs, "w") as f:
        for r in sample:
            f.write(json.dumps({"id": r["id"], "app": r["app_name"], "tier": r["match"]["tier"], "dt": r["match"]["dt"], "containment": r["match"]["containment"],
                                "dictation": r["drake"]["formatted"], "chat_message": short(r["chat_message"]["text"], 700), "source": r["source"],
                                "session_id": r["session_id"]}, ensure_ascii=False) + "\n")
    json.dump({"scrub": dict(S.stats), "redact": dict(R.stats), "names_tokenized": len(S.name_ids)}, open(os.path.join(out, "scrub_stats.json"), "w"), indent=1)
    json.dump({"pairs": n_pairs, "negative_control_shift_24h": ctrl, "traces": len(traces), "matched": len(best)}, open(os.path.join(out, "build_counts.json"), "w"), indent=1)
    print("traces", len(traces), "matched", len(best), "control+24h", dict(ctrl), "pairs", dict(n_pairs), file=sys.stderr)

if __name__ == "__main__":
    main(*sys.argv[1:4])
