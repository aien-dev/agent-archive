#!/usr/bin/env python3
"""export.py catalog.db OUTDIR  -> human-readable repo content
  OUTDIR/catalog/sessions.csv, documents.csv
  OUTDIR/transcripts/<project>/<date>_<slug>_<id>.md
  OUTDIR/documents/<doc_type>/<project>/<name>.md
  OUTDIR/STATS.md
"""
import csv, os, re, sqlite3, sys, shutil, hashlib
from collections import Counter

db_path, out = sys.argv[1], sys.argv[2]
db = sqlite3.connect(db_path)
for d in ("catalog", "transcripts", "documents"):
    shutil.rmtree(os.path.join(out, d), ignore_errors=True)
os.makedirs(os.path.join(out, "catalog"), exist_ok=True)

def slug(s, n=48):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:n].strip("-")
    return s or "untitled"

# ---- dictations (matched to project chats only; see wispr_match.py) ----
try:
    dic = {(s, q): (a, f, app, ts) for s, q, a, f, app, ts in db.execute(
        "select session_id,seq,asr_text,formatted_text,app,ts from dictations")}
except sqlite3.OperationalError:
    dic = {}
def _n(t): return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

# ---- sessions ----
cols = [r[1] for r in db.execute("pragma table_info(sessions)")]
rows = db.execute("select * from sessions order by started_at").fetchall()
paths = {}
with open(os.path.join(out, "catalog", "sessions.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(cols + ["transcript"])
    for r in rows:
        d = dict(zip(cols, r))
        date = (d["started_at"] or "undated")[:10]
        name = d["title"] or d["first_prompt"] or "untitled"
        short = hashlib.sha1(d["id"].encode()).hexdigest()[:6]
        sub = "subagents/" if d["is_subagent"] else ""
        rel = f"transcripts/{d['project']}/{sub}{date}_{slug(name)}_{short}.md"
        paths[d["id"]] = rel
        w.writerow(list(r) + [rel])

for r in rows:
    d = dict(zip(cols, r)); rel = paths[d["id"]]
    p = os.path.join(out, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(f"# {d['title'] or (d['first_prompt'] or 'untitled')[:80]}\n\n")
        f.write(f"- **machine:** {d['machine']}  \n- **source:** {d['source']}  \n- **model:** {d['model']}  \n")
        f.write(f"- **started:** {d['started_at']}  \n- **ended:** {d['ended_at']}  \n")
        f.write(f"- **project:** {d['project']}  \n- **kind:** {d['kind']}  \n- **tags:** {d['tags']}  \n")
        f.write(f"- **messages:** {d['n_user']} human, {d['n_injected']} agent/system-injected, {d['n_assistant']} assistant, {d['n_tool_calls']} tool calls\n\n---\n\n")
        for author, ts, text, tools, seq in db.execute("select author,ts,text,tools,seq from messages where session_id=? order by seq", (d["id"],)):
            who = {"human": "Human", "assistant": "Assistant", "agent": "Agent prompt (written by another agent/SDK)", "system": "System/harness message"}[author]
            f.write(f"### {who}" + (f" · {ts}" if ts else "") + "\n\n")
            if text.strip():
                # fence-safe: indent nothing, but neutralise headings that would break outline
                f.write(re.sub(r"(?m)^(#{1,3}) ", r"\\\1 ", text) + "\n\n")
            dd = dic.get((d["id"], seq))
            if dd and author == "human":
                a, fm, app, dts = dd
                f.write("> 🎙 _dictated with Wispr Flow. Raw speech-to-text, before cleanup:_\n>\n")
                f.write("\n".join("> " + ln for ln in (a or fm).splitlines()) + "\n\n")
            if tools:
                c = Counter(tools.split(","))
                f.write("_tools: " + ", ".join(f"{k}×{v}" if v > 1 else k for k, v in c.items()) + "_\n\n")

# ---- documents ----
dcols = [r[1] for r in db.execute("pragma table_info(documents)")]
with open(os.path.join(out, "catalog", "documents.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow([c for c in dcols if c != "text"] + ["file"])
    for r in db.execute("select * from documents order by path"):
        d = dict(zip(dcols, r))
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", d["path"].lstrip("~/"))[-110:]
        rel = f"documents/{d['doc_type']}/{d['project']}/{d['machine']}__{base}"
        if not rel.endswith(".md"): rel += ".md"
        p = os.path.join(out, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as g: g.write(d["text"])
        w.writerow([d[c] for c in dcols if c != "text"] + [rel])

if dic:
    with open(os.path.join(out, "catalog", "dictations.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["session_id", "seq", "timestamp", "app", "raw_asr", "formatted"])
        for (s, q), (a, fm, app, dts) in sorted(dic.items(), key=lambda kv: kv[1][3]):
            w.writerow([s, q, dts, app, a, fm])

# ---- stats ----
q = lambda s: db.execute(s).fetchall()
with open(os.path.join(out, "STATS.md"), "w") as f:
    tot = q("select count(*),sum(n_user),sum(n_assistant),sum(n_tool_calls),sum(user_chars),sum(n_injected) from sessions")[0]
    f.write("# Catalog stats\n\n")
    f.write(f"- sessions: **{tot[0]:,}**\n- human messages: **{tot[1]:,}** ({tot[4]:,} characters typed by a person)\n- agent/system-injected messages (not human): **{tot[5]:,}**\n- assistant messages: **{tot[2]:,}**\n- tool calls: **{tot[3]:,}**\n")
    if dic: f.write(f"- human messages with a matching Wispr Flow dictation: **{len(dic):,}**\n")
    f.write(f"- documents (handoffs, skills, agent config, plans): **{q('select count(*) from documents')[0][0]:,}**\n")
    f.write(f"- span: {q('select min(started_at),max(started_at) from sessions where started_at is not null')[0]}\n\n")
    for title, sql in [("By project", "select project,count(*) as sessions,sum(n_user) as human_msgs,sum(n_assistant) as assistant_msgs from sessions group by 1 order by 2 desc"),
                       ("By kind of work", "select kind,count(*) from sessions group by 1 order by 2 desc"),
                       ("By machine", "select machine,count(*) from sessions group by 1 order by 2 desc"),
                       ("By source", "select source,count(*) from sessions group by 1 order by 2 desc"),
                       ("Documents by type", "select doc_type,count(*) from documents group by 1 order by 2 desc"),
                       ("Redactions applied", "select kind,n from redaction_stats order by n desc")]:
        rs = q(sql); hdr = [c[0] for c in db.execute(sql).description]
        f.write(f"## {title}\n\n| " + " | ".join(hdr) + " |\n|" + "---|" * len(hdr) + "\n")
        for r in rs: f.write("| " + " | ".join(str(x) for x in r) + " |\n")
        f.write("\n")
print("exported", len(rows), "sessions")
