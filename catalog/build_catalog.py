#!/usr/bin/env python3
"""Build a SQLite catalog of AI chat sessions and agent files on THIS machine.

Usage: build_catalog.py OUT.db MACHINE_NAME

Optional extra stores (env), for archived or non-Claude chats:
  CATALOG_CLAUDE_ROOTS="DIR[=machine]:DIR[=machine]"  more Claude Code project trees (archives, backups).
                       Sessions are deduplicated by id; the largest copy wins.
  CATALOG_RECORDS="DIR"   agent-archive common records (DIR/<agent>/<day>.jsonl), agents in CATALOG_RECORD_AGENTS
                          (default codex,gemini,opencode,muse; claude comes from the raw transcripts above)
  CATALOG_T3="FILE"       T3 Code state (statev2.sqlite)

Reads (whatever exists):
  ~/.hermes/state.db                 Hermes chat sessions + messages
  ~/.claude/projects/**/*.jsonl      Claude Code transcripts (incl. sub-agents)
  ~/handoffs/**/*.md                 agent handoff notes
  ~/.claude/{CLAUDE.md,foundation.md,skills/*/SKILL.md,plans/*.md}   agent files

Keeps: what the human typed, what the assistant said, tool NAMES.
Drops: thinking blocks, tool output, raw API request blobs.
Every text goes through redact.py before it touches the database.
"""
import glob, json, os, re, sqlite3, sys, hashlib, datetime
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import Redactor
from categorize import classify_session, classify_doc

HOME = os.path.expanduser("~")
MAX_MSG = 60_000      # chars kept per message
MAX_DOC = 400_000     # chars kept per document

SCHEMA = """
CREATE TABLE sessions(
  id TEXT PRIMARY KEY, machine TEXT, source TEXT, project_dir TEXT, repo TEXT,
  title TEXT, model TEXT, started_at TEXT, ended_at TEXT,
  n_user INTEGER, n_assistant INTEGER, n_tool_calls INTEGER, user_chars INTEGER, n_injected INTEGER,
  project TEXT, kind TEXT, tags TEXT, first_prompt TEXT, is_subagent INTEGER,
  parent_id TEXT, entrypoint TEXT, src_path TEXT);
CREATE TABLE messages(
  id INTEGER PRIMARY KEY, session_id TEXT, seq INTEGER, role TEXT, ts TEXT,
  text TEXT, tools TEXT, redactions INTEGER, author TEXT, sender TEXT, tool_detail TEXT);
CREATE TABLE documents(
  id INTEGER PRIMARY KEY, machine TEXT, path TEXT, doc_type TEXT, title TEXT,
  modified TEXT, chars INTEGER, project TEXT, tags TEXT, text TEXT, redactions INTEGER);
CREATE TABLE redaction_stats(kind TEXT PRIMARY KEY, n INTEGER);
CREATE INDEX m_sess ON messages(session_id, seq);
CREATE INDEX m_role ON messages(role);
"""

def iso(x):
    if x is None: return None
    try:
        if isinstance(x, (int, float)):
            return datetime.datetime.fromtimestamp(x, datetime.timezone.utc).isoformat(timespec="seconds")
        return str(x)
    except Exception:
        return None

TOOL_KEYS = ("command", "file_path", "path", "pattern", "skill", "query", "url", "description", "subagent_type", "model", "expr", "prompt")

def tool_short(name, inp, n=240):
    """One short line saying what a tool call did: the command, the file, the subagent brief. Redacted later."""
    if not isinstance(inp, dict): return str(inp)[:n]
    parts = []
    for k in TOOL_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(f"{k}={' '.join(v.split())[:n if k in ('command', 'prompt') else 120]}")
    if not parts:
        for k, v in inp.items():
            if isinstance(v, (str, int, float)): parts.append(f"{k}={' '.join(str(v).split())[:120]}")
            if len(parts) >= 2: break
    return " | ".join(parts)[:n * 2]

def blocks_text(content, detail=None):
    """Return (text, [tool names]) from a Claude content value. detail, if a list, gets [name, short args] per call."""
    if isinstance(content, str):
        return content, []
    texts, tools = [], []
    for b in content or []:
        if not isinstance(b, dict): continue
        t = b.get("type")
        if t == "text": texts.append(b.get("text", ""))
        elif t == "tool_use":
            tools.append(b.get("name", "?"))
            if detail is not None: detail.append([b.get("name", "?"), tool_short(b.get("name"), b.get("input"))])
        # thinking + tool_result intentionally dropped
    return "\n".join(texts), tools

NOISE_U = ("<environment_context", "# AGENTS.md", "<user_instructions", "<permissions", "<INSTRUCTIONS", "<turn_aborted")
NOISE = ("<local-command", "<command-name", "<command-message", "<task-notification", "This session is being continued", "[Cross-session", "Caveat:")

def author_of(d, t, text):
    """human = typed by a person. agent = prompt written by another agent/SDK. system = harness noise."""
    if t == "assistant": return "assistant"
    o = d.get("origin") or {}
    kind = o.get("kind") if isinstance(o, dict) else None
    head = text.lstrip()[:60]
    if kind == "human" and not head.startswith(NOISE): return "human"
    if kind == "human" and head.startswith("<command-"): return "human"   # slash command the human typed
    if kind == "task-notification" or head.startswith(NOISE) or d.get("promptSource") == "system": return "system"
    if d.get("promptSource") == "sdk" or d.get("turnOrigin") == "sdk": return "agent"
    if d.get("turnOrigin") == "human" and not head.startswith(NOISE): return "human"
    return "agent"

DROP_SESSIONS, DROP_MSGS = set(), set()
_dp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "drops.txt")
if os.path.isfile(_dp):
    for ln in open(_dp):
        p = ln.split()
        if len(p) == 2 and p[0] == "session": DROP_SESSIONS.add(p[1])
        if len(p) == 2 and p[0] == "msg": DROP_MSGS.add(p[1])

def strip_wrappers(text):
    # drop injected system reminders / command wrappers: they are not the human's words
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
    return text.strip()

def main(out, machine):
    if os.path.exists(out): os.remove(out)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    R = Redactor(HOME)
    mid = [0]

    def add_session(s, msgs):
        if not msgs: return
        for k, m in enumerate(msgs): m["seq"] = k
        if s["id"] in DROP_SESSIONS: return
        msgs = [m for m in msgs if f"{s['id']}#{m['seq']}" not in DROP_MSGS and not any(f"{s['id'].split(':')[-1][:8]}#{m['seq']}".endswith(d.split(':')[-1]) for d in DROP_MSGS if len(d.split(':')[-1].split('#')[0])==8)]
        users = [m for m in msgs if m["author"] == "human" and m["text"]]
        asst = [m for m in msgs if m["role"] == "assistant"]
        first = users[0]["text"][:400] if users else ""
        cls = classify_session(s, msgs)
        db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            s["id"], s.get("machine") or machine, s["source"], s.get("project_dir"), s.get("repo"),
            R.clean(s.get("title") or "")[0], s.get("model"), s.get("started"), s.get("ended"),
            len(users), len(asst), sum(len(m["tools"]) for m in msgs),
            sum(len(m["text"]) for m in users),
            sum(1 for m in msgs if m["author"] in ("agent","system")),
            cls["project"], cls["kind"], ",".join(cls["tags"]), first, int(bool(s.get("sub"))),
            s.get("parent"), s.get("entrypoint"), s.get("src_path")))
        for i, m in enumerate(msgs):
            mid[0] += 1
            i = m["seq"]
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (mid[0], s["id"], i, m["role"], m["ts"], m["text"], ",".join(m["tools"]), m["red"], m["author"], m.get("sender"),
                        json.dumps(m["detail"]) if m.get("detail") else None))

    def mk(role, text, ts, tools, author, detail=None):
        cap = {"human": MAX_MSG, "assistant": MAX_MSG, "agent": 20_000, "system": 3_000}[author]
        text = strip_wrappers(text)[:cap]
        text, n = R.clean(text)
        if detail:
            red = []
            for name, short in detail:
                short, k = R.clean(short); n += k; red.append([name, short])
            detail = red
        return {"role": role, "text": text, "ts": ts, "tools": tools, "red": n, "author": author, "seq": 0, "detail": detail}

    # ---- Hermes ----
    hdb = os.path.join(HOME, ".hermes", "state.db")
    if os.path.exists(hdb):
        h = sqlite3.connect(f"file:{hdb}?mode=ro", uri=True)
        h.row_factory = sqlite3.Row
        for s in h.execute("SELECT * FROM sessions"):
            msgs = []
            for r in h.execute("SELECT role,content,tool_name,tool_calls,timestamp FROM messages WHERE session_id=? ORDER BY timestamp,id", (s["id"],)):
                if r["role"] == "tool": continue
                tools, detail = [], []
                if r["tool_calls"]:
                    try:
                        for c in json.loads(r["tool_calls"]):
                            fn = c.get("function", {}); tools.append(fn.get("name", "?"))
                            try: args = json.loads(fn.get("arguments") or "{}")
                            except Exception: args = {"args": fn.get("arguments")}
                            detail.append([fn.get("name", "?"), tool_short(fn.get("name"), args)])
                    except Exception: pass
                if r["role"] in ("user", "assistant", "desktop", "cron", "subagent"):
                    role = r["role"] if r["role"] in ("user", "assistant") else "user"
                    author = {"user": "human", "assistant": "assistant", "desktop": "system", "cron": "system", "subagent": "agent"}[r["role"]]
                    mm = mk(role, r["content"] or "", iso(r["timestamp"]), tools, author, detail)
                    mm["sender"] = {"human": "drake", "assistant": "hermes/" + (s["model"] or "?"), "agent": "hermes-subagent", "system": "hermes-" + r["role"]}[author]
                    msgs.append(mm)
            keys = s.keys()
            add_session({"id": "hermes:" + s["id"], "source": "hermes/" + (s["source"] or ""),
                         "project_dir": s["cwd"] if "cwd" in keys else None,
                         "repo": s["git_repo_root"] if "git_repo_root" in keys else None,
                         "title": s["title"], "model": s["model"],
                         "started": iso(s["started_at"]), "ended": iso(s["ended_at"])}, msgs)

    # ---- Claude Code ----
    # live tree first, then archive/backup trees; one session id = one session, the largest copy wins
    roots = [(os.path.join(HOME, ".claude", "projects"), machine)]
    for r in filter(None, os.environ.get("CATALOG_CLAUDE_ROOTS", "").split(":")):
        d, _, mach = r.partition("=")
        roots.append((os.path.expanduser(d), mach or machine))
    best = {}
    for root, mach in roots:
        for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            try: size = os.path.getsize(f)
            except OSError: continue
            if "-Users-" in f and mach == machine and machine != "mac": mach_f = "mac"   # Mac project tree copied over
            else: mach_f = mach
            key = (mach_f, os.path.basename(f)[:-6])
            if key not in best or size > best[key][1]: best[key] = (f, size, mach_f)
    print("claude transcripts:", len(best), "unique of roots", [r for r, _ in roots], file=sys.stderr)
    for (mach_f, sid), (f, _, _) in sorted(best.items(), key=lambda kv: kv[1][0]):
        title, cwd, branch, model, sub = None, None, None, None, "subagents" in f
        parent, entry = None, None
        msgs = []
        try:
            for line in open(f, errors="replace"):
                if '"type":"user"' not in line and '"type":"assistant"' not in line and '"ai-title"' not in line and '"queued_command"' not in line: continue
                try: d = json.loads(line)
                except Exception: continue
                t = d.get("type")
                if t == "ai-title": title = d.get("aiTitle")
                a = d.get("attachment") if t == "attachment" else None
                if isinstance(a, dict) and a.get("type") == "queued_command" and isinstance(a.get("prompt"), str):
                    # typed while the agent was busy: stored as an attachment, not a user line
                    q = a["prompt"]
                    o = a.get("origin") if isinstance(a.get("origin"), dict) else {}
                    mode = a.get("commandMode", "prompt")
                    if (o.get("kind") == "human" or a.get("humanTurn")) and mode == "prompt" and not q.lstrip().startswith(NOISE): au = "human"
                    elif mode == "task-notification" or q.lstrip().startswith(NOISE): au = "system"
                    else: au = "agent"
                    mm = mk("user", q, a.get("timestamp") or d.get("timestamp"), [], au)
                    mm["sender"] = "drake" if au == "human" else "claude-code/queued/" + str(mode)
                    msgs.append(mm); continue
                if t not in ("user", "assistant"): continue
                m = d.get("message") or {}
                if not isinstance(m, dict): continue
                cwd = cwd or d.get("cwd"); branch = branch or d.get("gitBranch"); entry = entry or d.get("entrypoint")
                if sub and not parent and d.get("sessionId"): parent = f"claude:{mach_f}:{d['sessionId']}"
                if t == "assistant": model = model or m.get("model")
                detail = []
                text, tools = blocks_text(m.get("content"), detail)
                if t == "user" and not text.strip(): continue   # pure tool_result
                if not text.strip() and not tools: continue
                au = author_of(d, t, text)
                mm = mk(t, text, d.get("timestamp"), tools, au, detail)
                if au == "assistant": mm["sender"] = "claude-code/" + (m.get("model") or model or "?") + ("/subagent" if sub else "")
                elif au == "human": mm["sender"] = "drake"
                elif au == "agent":
                    o = d.get("origin") or {}
                    mm["sender"] = "claude-code/" + (d.get("entrypoint") or "?") + "/" + (d.get("promptSource") or (o.get("kind") if isinstance(o, dict) else None) or "agent")
                else: mm["sender"] = "claude-code/harness/" + (d.get("turnOrigin") or d.get("promptSource") or "system")
                msgs.append(mm)
        except Exception as e:
            print("skip", f, e, file=sys.stderr); continue
        proj = os.path.basename(os.path.dirname(f)) if not sub else os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(f))))
        stamps = [m["ts"] for m in msgs if m["ts"]]
        add_session({"id": f"claude:{mach_f}:{sid}", "source": "claude-code", "project_dir": cwd or proj, "machine": mach_f,
                     "repo": branch, "title": title, "model": model, "sub": sub, "parent": parent, "entrypoint": entry,
                     "src_path": f.replace(HOME, "~"),
                     "started": min(stamps) if stamps else None, "ended": max(stamps) if stamps else None}, msgs)
        db.commit() if mid[0] % 5000 < 50 else None

    # ---- agent-archive common records (codex, gemini = Antigravity, opencode, muse) ----
    rec_root = os.environ.get("CATALOG_RECORDS")
    if rec_root:
        NOISE_U = ("<environment_context", "# AGENTS.md", "<user_instructions", "<permissions", "<INSTRUCTIONS", "<turn_aborted")
        for agent in os.environ.get("CATALOG_RECORD_AGENTS", "codex,gemini,opencode,muse").split(","):
            by = {}
            for p in sorted(glob.glob(os.path.join(os.path.expanduser(rec_root), agent, "*.jsonl"))):
                for line in open(p, errors="replace"):
                    try: r = json.loads(line)
                    except Exception: continue
                    if r.get("role") not in ("user", "assistant") or not (r.get("text") or "").strip(): continue
                    by.setdefault((r.get("host") or machine, r.get("session")), []).append(r)
            src = {"gemini": "antigravity"}.get(agent, agent)
            for (host, sess), rs in by.items():
                rs.sort(key=lambda r: (r.get("ts") or "", (r.get("src") or {}).get("pos") or 0))
                msgs = []
                for r in rs:
                    text = r["text"]
                    if r["role"] == "assistant": au = "assistant"
                    elif text.lstrip().startswith(NOISE_U) or text.lstrip().startswith(NOISE): au = "system"
                    else: au = "human"
                    mm = mk(r["role"], text, r.get("ts"), [], au)
                    mm["sender"] = "drake" if au == "human" else f"{src}/{r.get('model') or '?'}"
                    msgs.append(mm)
                stamps = [m["ts"] for m in msgs if m["ts"]]
                mach_r = {"macbook": "mac", "spark-archive": machine}.get(host, host)
                add_session({"id": f"{src}:{mach_r}:{sess}", "source": src, "project_dir": rs[0].get("cwd"), "machine": mach_r,
                             "model": next((r.get("model") for r in rs if r.get("model")), None),
                             "src_path": ((rs[0].get("src") or {}).get("path") or "").replace(HOME, "~"),
                             "started": min(stamps) if stamps else None, "ended": max(stamps) if stamps else None}, msgs)
            db.commit()

    # ---- Codex raw sessions (Spark ~/.codex + archives, Mac Codex Desktop copies) ----
    croots = [r for r in os.environ.get("CATALOG_CODEX_ROOTS", "").split(":") if r]
    if croots:
        best = {}
        for r in croots:
            for f in glob.glob(os.path.join(os.path.expanduser(r), "**", "rollout-*.jsonl"), recursive=True):
                k = os.path.basename(f)
                if k not in best or os.path.getsize(f) > os.path.getsize(best[k]): best[k] = f
        print("codex transcripts:", len(best), file=sys.stderr)
        for k, f in sorted(best.items()):
            meta, msgs = {}, []
            for line in open(f, errors="replace"):
                try: d = json.loads(line)
                except Exception: continue
                p_ = d.get("payload") or {}; ty = d.get("type"); pt = p_.get("type"); ts = d.get("timestamp")
                if ty == "session_meta":
                    if not meta: meta = p_          # forks replay their parents' meta lines; the first one is this session
                    continue
                exec_ = meta.get("originator") == "codex_exec" or meta.get("source") == "exec"
                sub_ = isinstance(meta.get("source"), dict) and "subagent" in meta.get("source")
                q = None
                if ty == "event_msg" and pt == "user_message" and (p_.get("message") or "").strip(): q, kind = p_["message"], "event"
                elif ty == "response_item" and pt == "message" and p_.get("role") == "user":
                    q, kind = "\n".join(c.get("text", "") for c in p_.get("content") or [] if isinstance(c, dict)), "item"
                if q is not None and q.strip():
                    if q.lstrip().startswith(NOISE_U) or q.lstrip().startswith(NOISE): au = "system"
                    else: au = "agent" if (exec_ or sub_) else "human"
                    mm = mk("user", q, ts, [], au); mm["sender"] = "drake" if au == "human" else "codex/" + au; mm["kind"] = kind
                    msgs.append(mm)
                elif ty == "response_item" and pt == "message" and p_.get("role") == "assistant":
                    text = "\n".join(c.get("text", "") for c in p_.get("content") or [] if isinstance(c, dict))
                    if text.strip():
                        mm = mk("assistant", text, ts, [], "assistant"); mm["sender"] = "codex/" + str(meta.get("model") or meta.get("model_provider") or "?")
                        msgs.append(mm)
                elif ty == "response_item" and pt in ("function_call", "custom_tool_call", "local_shell_call"):
                    name = p_.get("name") or pt
                    raw = p_.get("arguments") or p_.get("input") or p_.get("action") or ""
                    try: args = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception: args = {"command": raw}
                    if isinstance(args, dict) and isinstance(args.get("cmd"), (str, list)): args["command"] = args["cmd"] if isinstance(args["cmd"], str) else " ".join(args["cmd"])
                    short = tool_short(name, args if isinstance(args, dict) else {"command": str(args)})
                    if msgs and msgs[-1]["role"] == "assistant" and msgs[-1]["author"] == "assistant":
                        msgs[-1]["tools"].append(name); short_r, n_ = R.clean(short); msgs[-1]["red"] += n_
                        msgs[-1]["detail"] = (msgs[-1].get("detail") or []) + [[name, short_r]]
                    else:
                        mm = mk("assistant", "", ts, [name], "assistant", [[name, short]]); mm["sender"] = "codex"
                        msgs.append(mm)
            if any(m.get("kind") == "event" for m in msgs):   # newer logs carry the typed text as an event; drop the duplicate items
                msgs = [m for m in msgs if m.get("kind") != "item"]
            cwd = meta.get("cwd") or ""
            mach = "mac" if cwd.startswith("/Users/") else machine
            src_ = meta.get("source")
            par = ((src_.get("subagent") or {}).get("thread_spawn") or {}).get("parent_thread_id") if isinstance(src_, dict) and isinstance(src_.get("subagent"), dict) else None
            stamps = [m["ts"] for m in msgs if m["ts"]]
            add_session({"id": f"codex:{mach}:{meta.get('id') or k}", "source": "codex", "project_dir": cwd, "model": meta.get("model"), "machine": mach,
                         "sub": bool(par), "parent": f"codex:{mach}:{par}" if par else None,
                         "entrypoint": meta.get("originator"), "src_path": f.replace(HOME, "~"),
                         "started": min(stamps) if stamps else None, "ended": max(stamps) if stamps else None}, msgs)
        db.commit()

    # ---- Grok CLI sessions (~/.grok/sessions/<cwd>/<id>/chat_history.jsonl + events.jsonl for turn times) ----
    groot = os.environ.get("CATALOG_GROK")
    if groot:
        for f in sorted(glob.glob(os.path.join(os.path.expanduser(groot), "*", "*", "chat_history.jsonl"))):
            sdir = os.path.dirname(f); sid = os.path.basename(sdir)
            turns = []
            ev = os.path.join(sdir, "events.jsonl")
            if os.path.exists(ev):
                for line in open(ev, errors="replace"):
                    if '"turn_started"' not in line: continue
                    try: turns.append(json.loads(line).get("ts"))
                    except Exception: pass
            msgs, ti, cur, sub_ = [], -1, None, False
            for line in open(f, errors="replace"):
                try: d = json.loads(line)
                except Exception: continue
                ty = d.get("type")
                if ty == "system": sub_ = "subagent" in str(d.get("content"))[:200]; continue
                c = d.get("content")
                text = c if isinstance(c, str) else "\n".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
                if ty == "user":
                    if d.get("synthetic_reason") or text.lstrip().startswith(("<user_info", "<system-reminder")): continue
                    ti += 1; cur = turns[ti] if ti < len(turns) else cur
                    au = "agent" if sub_ else "human"
                    mm = mk("user", text, cur, [], au); mm["sender"] = "drake" if au == "human" else "grok/parent"
                    msgs.append(mm)
                elif ty == "assistant":
                    detail = []
                    try:
                        for tc in json.loads(d["tool_calls"]) if isinstance(d.get("tool_calls"), str) else (d.get("tool_calls") or []):
                            try: args = json.loads(tc.get("arguments") or "{}")
                            except Exception: args = {}
                            detail.append([tc.get("name", "?"), tool_short(tc.get("name"), args)])
                    except Exception: pass
                    if not text.strip() and not detail: continue
                    mm = mk("assistant", text, cur, [x[0] for x in detail], "assistant", detail); mm["sender"] = "grok/" + str(d.get("model_id") or "?")
                    msgs.append(mm)
            stamps = [m["ts"] for m in msgs if m["ts"]]
            add_session({"id": f"grok:{machine}:{sid}", "source": "grok", "project_dir": os.path.basename(os.path.dirname(sdir)).replace("%2F", "/"),
                         "sub": sub_, "src_path": f.replace(HOME, "~"),
                         "started": min(stamps) if stamps else None, "ended": max(stamps) if stamps else None}, msgs)
        db.commit()

    # ---- T3 Code (threads in statev2.sqlite) ----
    t3 = os.environ.get("CATALOG_T3")
    if t3 and os.path.exists(os.path.expanduser(t3)):
        tdb = sqlite3.connect(f"file:{os.path.expanduser(t3)}?mode=ro", uri=True)
        threads = {tid: (title, prov, created) for tid, title, prov, created in
                   tdb.execute("select thread_id,title,default_provider,created_at from orchestration_v2_projection_threads")}
        by = {}
        for tid, role, created, pj in tdb.execute("select thread_id,role,created_at,payload_json from orchestration_v2_projection_messages order by created_at"):
            try: d = json.loads(pj)
            except Exception: continue
            by.setdefault(tid, []).append((role, created, d))
        for tid, rows in by.items():
            msgs = []
            for role, created, d in rows:
                text = d.get("text") or ""
                if not text.strip(): continue
                if role == "assistant": au = "assistant"
                elif d.get("createdBy") == "user": au = "human"
                elif d.get("notification"): au = "system"
                else: au = "agent"
                mm = mk("assistant" if role == "assistant" else "user", text, created, [], au)
                mm["sender"] = "drake" if au == "human" else f"t3/{threads.get(tid, ('', '?'))[1]}/{d.get('createdBy')}"
                msgs.append(mm)
            title, prov, created = threads.get(tid, (None, None, None))
            stamps = [m["ts"] for m in msgs if m["ts"]]
            add_session({"id": f"t3:{machine}:{tid}", "source": "t3code", "title": title, "model": prov,
                         "src_path": t3, "started": min(stamps) if stamps else None, "ended": max(stamps) if stamps else None}, msgs)
        db.commit()

    # ---- Documents ----
    patterns = [
        ("handoff", "handoffs/**/*.md"),
        ("agent-config", ".claude/CLAUDE.md"), ("agent-config", ".claude/foundation.md"),
        ("skill", ".claude/skills/*/SKILL.md"), ("skill", ".hermes/skills/**/SKILL.md"),
        ("plan", ".claude/plans/*.md"), ("memory", ".hermes/memories/*.md"),
        ("agent-config", ".hermes/SOUL.md"),
    ]
    seen = set()
    for dtype, pat in patterns:
        for p in sorted(glob.glob(os.path.join(HOME, pat), recursive=True)):
            if p in seen or not os.path.isfile(p) or ".bak" in p: continue
            seen.add(p)
            try: raw = open(p, errors="replace").read()[:MAX_DOC]
            except Exception: continue
            text, n = R.clean(raw)
            title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), os.path.basename(p))[:200]
            c = classify_doc(p, text)
            db.execute("INSERT INTO documents(machine,path,doc_type,title,modified,chars,project,tags,text,redactions) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (machine, p.replace(HOME, "~"), dtype, title, iso(os.path.getmtime(p)), len(text), c["project"], ",".join(c["tags"]), text, n))

    for k, v in R.stats.items():
        db.execute("INSERT OR REPLACE INTO redaction_stats VALUES(?,?)", (k, v))
    db.commit()
    for t in ("sessions", "messages", "documents"):
        print(t, db.execute(f"select count(*) from {t}").fetchone()[0])
    print("redactions", dict(R.stats))
    db.execute("VACUUM"); db.close()

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
