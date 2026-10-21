#!/usr/bin/env python3
"""stats.py TRACES_DIR  -> TRACES_DIR/STATS.md (counts, match rates, labels, causes, time of day, precision check)."""
import sys, os, json, collections, re, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labels import label, causes, CAUSES

out = sys.argv[1]
T = [json.loads(l) for l in open(os.path.join(out, "traces.jsonl"))]
B = json.load(open(os.path.join(out, "build_counts.json")))
SC = json.load(open(os.path.join(out, "scrub_stats.json")))
C, CD = collections.Counter, dict(CAUSES and [(c[0], c[1]) for c in CAUSES])
L = []
w = L.append
def pct(a, b): return f"{100.0 * a / b:.1f}%" if b else "n/a"
def short(t, n): t = " ".join((t or "").split()); return t if len(t) <= n else t[:n] + "..."

# where the transcripts of each Wispr app live (checked 2026-10-21 on the Spark; Mac paths are UNVERIFIED from here)
WHERE = {
    "Ghostty": "Claude Code on the Spark from 2026-09-23 (~/.claude/projects + agent-archive copies), Codex, Grok CLI, Hermes CLI. Before 09-23 the Spark has no Claude Code transcripts (~/.claude/history.jsonl also starts 09-23); Mac Claude Code before 09-24 is gone from mac.db. UNKNOWN: whether a pre-09-23 Claude backup exists. Experiment: `ls -la ~/.claude/projects` on the Mac and any Time Machine copy of ~/.claude from August.",
    "Terminal": "Same stores as Ghostty (Mac Terminal over ssh to the Spark). Sep 1-15 mostly unmatched, same gap.",
    "Hermes desktop": "Mac Hermes state.db (in mac.db, sessions from 2026-10-18). Earlier Hermes desktop chats are not in any state.db found.",
    "Antigravity": "agent-archive gemini records (Mac + Spark Antigravity conversation dbs, protobuf parsed by agent-archive). Unmatched ones: parser keeps only some step types (agent-archive handoff: 'thinking/tool steps not recorded').",
    "T3 Code": "~/.t3/userdata/statev2.sqlite on the Spark (threads 10-12..10-17).",
    "Codex desktop": "Mac Codex Desktop sessions copied to ~/chat-transcripts-archive/.codex__sessions (Jul-Sep).",
    "Chrome": "chatgpt.com (1,299) lives in the OpenAI account: needs a ChatGPT data export (Settings > Data controls > Export data, conversations.json). torn.com (1,486) is a game: non-agent voice samples only. Other sites (gemini, grok.com, notebooklm) live in those accounts.",
    "com.meta.endo": "UNKNOWN. An agent-style chat app from Meta (dictations read like instructions to an agent, Sep 20 - Oct 21). Nothing on the Spark. Experiment: on the Mac, `ls ~/Library/Containers | grep -i endo` and `ls ~/Library/Application\\ Support | grep -i -E 'meta|endo'`; else the Meta account data download.",
    "Discord": "Non-agent (people and bots on Discord). Kept as voice samples, context=non-agent. Full text would need a Discord data package request.",
    "DeepSeek Harness (Chrome app)": "UNKNOWN. A Chrome PWA (Aug 22 - Sep 14), probably the Atlas harness web UI from the Mac (Codex worktrees named atlas-harness). Nothing on the Spark. Experiment: on the Mac, find the PWA origin in ~/Library/Application Support/Google/Chrome/*/Web Applications and read its IndexedDB, or the harness server's own session store.",
    "Torn (Chrome app)": "Game. Non-agent voice samples only.",
    "com.anysphere.sand (Cursor family)": "UNKNOWN. Cursor-family app on the Mac: chats live in ~/Library/Application Support/<app>/User/workspaceStorage/*/state.vscdb (UNVERIFIED path). Not on the Spark.",
    "Chrome app bippp... (agent chat, Sep 15-16)": "UNKNOWN. A Chrome PWA used as an agent chat for two days. Nothing on the Spark. Experiment: on the Mac, look up app id bipppfendfaipmoenbkohnnfcnlfdbhp under ~/Library/Application Support/Google/Chrome/*/Web Applications to get its site, then that site's export.",
    "com.duckduckgo.macos.browser": "UNKNOWN. Browser: the site is not recorded by Wispr for this app (duck.ai chat is likely, UNVERIFIED). Experiment: DuckDuckGo browser history on the Mac for those timestamps.",
    "com.electron.wispr-flow": "Wispr Flow's own window (tests and notes); not an agent chat.",
    "com.todesktop.230313 (Cursor)": "Cursor on the Mac (Aug 16-22): ~/Library/Application Support/Cursor/User/workspaceStorage/*/state.vscdb (UNVERIFIED). Not on the Spark.",
}

n = len(T); M = [t for t in T if t.get("match")]
w("# Drake-voice traces: STATS\n")
w(f"Built from `history.jsonl` ({n:,} dictations with text) joined to the merged chat catalog. All times below are Drake's local time. {B.get('tz_note', '')}Wispr stores one offset (300 min) for every row, so local = UTC-5.\n")
w("## Overview\n")
w(f"- dictations with text: **{n:,}**; matched to an agent chat message: **{len(M):,}** ({pct(len(M), n)})")
ctx = C(t["context"] for t in T)
w("- context: " + ", ".join(f"{k} {v:,}" for k, v in ctx.most_common()))
agent_side = [t for t in T if t["context"] in ("agent-chat", "agent-unmatched")]
w(f"- match rate among dictations spoken into agent apps: **{pct(len(M), len(agent_side))}** ({len(M):,} / {len(agent_side):,})")
nc = B["negative_control_shift_24h"]
w(f"- negative control (every dictation moved 24 h later, same rules): **{sum(nc.values())}** false matches ({', '.join(f'{k} {v}' for k, v in nc.items()) or 'none'}), i.e. a false-match floor of {pct(sum(nc.values()), len(M))} of the real matches")
w(f"- pairs: " + ", ".join(f"{k} {v:,}" for k, v in B["pairs"].items()) + "\n")

w("## Match rate per app\n")
w("| App | Dictations | Matched | Rate | Where its transcripts live |")
w("|---|---:|---:|---:|---|")
apps = C(t["app_name"] for t in T); am = C(t["app_name"] for t in M)
for a, k in apps.most_common(16):
    w(f"| {a} | {k:,} | {am[a]:,} | {pct(am[a], k)} | {WHERE.get(a, '')} |")
rest = sum(k for a, k in apps.most_common()[16:]); w(f"| (other apps) | {rest:,} | {sum(am[a] for a, _ in apps.most_common()[16:]):,} | | |\n")
dom = C(t["url_domain"] for t in T if t["app_name"] == "Chrome")
w("Chrome by site: " + ", ".join(f"{d} {k}" for d, k in dom.most_common(8)) + "\n")

w("## Matches by tier, source and timing\n")
tiers = C(t["match"]["tier"] for t in M)
w("| Tier | Rule | Matches |\n|---|---|---:|")
w(f"| long | >= 8 words, 5-word shingle containment >= 0.5, message within -2 min..+6 h | {tiers['long']:,} |")
w(f"| mid | 5-7 words, 3-word shingle containment >= 0.8, within -2..+5 min | {tiers['mid']:,} |")
w(f"| short | < 5 words, whole message equals the dictation, within -1..+2 min | {tiers['short']:,} |\n")
dts = sorted(t["match"]["dt"] for t in M)
if dts: w(f"Delay from dictation to chat message: median {statistics.median(dts):.0f} s, p90 {dts[int(.9 * len(dts))]:.0f} s, p99 {dts[int(.99 * len(dts))]:.0f} s.\n")
w("By source: " + ", ".join(f"{k} {v:,}" for k, v in C(t["source"] for t in M).most_common()) + "  ")
w("By machine: " + ", ".join(f"{k} {v:,}" for k, v in C(t["machine"] for t in M).most_common()) + "  ")
w("Matched message author: " + ", ".join(f"{k} {v:,}" for k, v in C(t["match"]["author"] for t in M).most_common()) + "\n")
multi = C(t["chat_message"]["msg_id"] for t in M); mm = [k for k, v in multi.items() if v > 1]
w(f"Many-to-one: {len(mm):,} chat messages hold 2 or more dictations ({sum(multi[k] for k in mm):,} dictations); largest {max(multi.values()) if multi else 0}. "
  f"Dictations also sent to a second session (secondary matches): {sum(1 for t in M if t.get('match_secondary')):,}.\n")

w("## Precision check (hand-verified)\n")
vr = os.path.join(out, "verify_results.tsv")
if os.path.exists(vr):
    rows = [l.rstrip("\n").split("\t") for l in open(vr) if l.strip() and not l.startswith("#")]
    v = C(r[1] for r in rows)
    w(f"50 matches drawn at random, stratified by tier and app (`verify_sample.jsonl`), each read by hand: dictation text vs chat message text and delay.\n")
    w(f"- correct: **{v['correct']}**, partial (right message, dictation is only part of it or a near-duplicate): **{v['partial']}**, wrong: **{v['wrong']}** -> precision {pct(v['correct'] + v['partial'], len(rows))}\n")
    cur = {t["id"]: t for t in M}
    kept = [r for r in rows if r[0] in cur]
    kv = C(r[1] for r in kept)
    w(f"- after that check the mid window was cut to +5 min and the short window to +2 min. With the current rules {len(kept)} of the 50 are still matched: "
      f"correct {kv['correct']}, partial {kv['partial']}, wrong {kv['wrong']} -> precision {pct(kv['correct'] + kv['partial'], len(kept))} (strict, correct only: {pct(kv['correct'], len(kept))}).\n")
    w("| id | tier | app | verdict | note |\n|---|---|---|---|---|")
    S = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(out, "verify_sample.jsonl"))}
    for r in rows: w(f"| {r[0][:8]} | {S.get(r[0], {}).get('tier')} | {S.get(r[0], {}).get('app')} | {r[1]} | {r[2] if len(r) > 2 else ''} |")
    w("")
else:
    w("Not done yet: `verify_results.tsv` missing.\n")

w("## Labels\n")
nm = sum(1 for t in M if t.get("model_labels"))
w(f"(The model returned no line for {len(M) - nm} of the {len(M):,} matched items; those keep their rule labels.)\n")
w(f"Two passes. Rules (`labels.py`, keyword rules, every dictation) and a model pass (Sonnet via `claude -p`, scrubbed text only, cached in `model_labels.jsonl`) on the {nm:,} matched traces, which sees what the agent said and did just before. "
  "For matched traces the model labels are final (`labels`), the rule labels stay in `labels_rules`.\n")
lab = C(l for t in T for l in (t.get("labels_rules") or t["labels"])); labm = C(l for t in M for l in t["labels"]); labr = C(l for t in M for l in (t.get("labels_rules") or t["labels"]))
w("| Label | Rules, all dictations | Rules, matched | Model, matched (final) | Both agree, matched |\n|---|---:|---:|---:|---:|")
for l in ("anger", "correction", "praise", "approval", "intent", "status_ask"):
    both = sum(1 for t in M if l in t["labels"] and l in (t.get("labels_rules") or []))
    w(f"| {l} | {lab[l]:,} ({pct(lab[l], n)}) | {labr[l]:,} | {labm[l]:,} ({pct(labm[l], len(M))}) | {both:,} |")
w(f"| (none) | {sum(1 for t in T if not (t.get('labels_rules') or t['labels'])):,} | | {sum(1 for t in M if not t['labels']):,} | |\n")

# rule check against the 142 hand-written correction items from the 2026-10-19 philosophy pass
ph = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phil", "by_correction.txt")
if os.path.exists(ph):
    items = re.findall(r"QUOTE: (.*?)\n.*?AGENT DID: (.*?)\n", open(ph).read(), flags=re.S)
    hit = sum(1 for q, _ in items if {"anger", "correction"} & set(label(q)))
    cz = [causes(q, a, []) for q, a in items]
    known = sum(1 for c in cz if c)
    w(f"Rule check against the {len(items)} correction items from `phil/by_correction.txt` (each is a message a reader marked as a correction): "
      f"labelled anger or correction **{hit}/{len(items)}** ({pct(hit, len(items))}); a cause found for **{known}/{len(items)}** ({pct(known, len(items))}). "
      f"Top causes there: " + ", ".join(f"{k} {v}" for k, v in C(c[0]["cause"] for c in cz if c).most_common(6)) + ".\n")

w("## What the agent did that drew anger\n")
ang = [t for t in M if "anger" in t["labels"]]
cc = C(t.get("cause") or "UNKNOWN" for t in ang)
w(f"{len(ang):,} matched dictations carry the anger label. Cause = the model's pick from the taxonomy in `labels.py` when it labelled the trace, else the first rule that fired:\n")
w("| Cause | What the agent did | Count |\n|---|---|---:|")
for k, v in cc.most_common(): w(f"| {k} | {CD.get(k, {'other': 'a cause outside the list (see cause_why)', 'UNKNOWN': 'context does not show it'}.get(k, ''))} | {v} |")
w("")
for k, v in cc.most_common(8):
    if k in ("UNKNOWN", "other"): continue
    w(f"### {k} ({v})\n")
    ex = [t for t in ang if t.get("cause") == k]
    ex.sort(key=lambda t: (-(t["match"]["confidence"] or 0), -len(t["drake"]["formatted"] or "")))
    for t in ex[:3]:
        la = next((b for b in reversed(t.get("before") or []) if b["author"] == "assistant"), None)
        w(f"- `{t['id'][:8]}` {t['local_time']} {t['app_name']} / {t['source']}: {t.get('cause_why') or ''}")
        w(f"  - agent before: {short(la['text'], 260) if la else '(no reply in view)'}")
        w(f"  - Drake: \"{short(t['drake']['formatted'], 300)}\"")
        w(f"  - after: {short(t['after']['reply'], 200)}")
    w("")
cor = [t for t in M if "correction" in t["labels"]]
w(f"Corrections ({len(cor):,} matched), cause: " + ", ".join(f"{k} {v}" for k, v in C(t.get("cause") or "UNKNOWN" for t in cor).most_common(14)) + "\n")

w("## Downstream coordination after Drake spoke\n")
ds = [t["downstream"] for t in M if t.get("downstream")]
for key, name in (("subagent_calls", "spawned subagents / sent agent messages"), ("child_sessions", "subagent sessions found (parent link)"),
                  ("launched_sessions", "separate agent sessions launched (claude -p / codex exec, linked by brief or ledger id or launch command)"),
                  ("ledger_ids", "ledger ids mentioned"), ("ledger_commands", "ledger add/note/done commands"), ("briefs", "briefs written or named"),
                  ("board_posts", "team board / restart-request posts"), ("files_touched", "files edited")):
    k = sum(1 for d in ds if d.get(key)); w(f"- {name}: {k:,} traces ({pct(k, len(M))})")
w("")

w("## Time of day (local, UTC-5)\n")
w("| Hour | Dictations | Anger | Praise | Correction |\n|---:|---:|---:|---:|---:|")
hr = C(); an = C(); pr = C(); co = C()
for t in T:
    h = int(t["local_time"][11:13]); hr[h] += 1; an[h] += "anger" in t["labels"]; pr[h] += "praise" in t["labels"]; co[h] += "correction" in t["labels"]
for h in range(24): w(f"| {h:02d} | {hr[h]:,} | {an[h]} ({pct(an[h], hr[h])}) | {pr[h]} | {co[h]} |")
w("")
w("## Privacy scrub\n")
w("Every text field went through `redact.py` (secrets, emails, phones, IPs) and `scrub.py` (health sentences, third-party names as stable [NAME_n] tokens, home towns, addresses, money). Counts: "
  + ", ".join(f"{k} {v:,}" for k, v in sorted(SC["scrub"].items())) + f"; names tokenized: {SC['names_tokenized']}. Redactions: " + ", ".join(f"{k} {v:,}" for k, v in sorted(SC["redact"].items())) + "\n")
open(os.path.join(out, "STATS.md"), "w").write("\n".join(L) + "\n")
print("STATS.md written", len(L), "lines")
