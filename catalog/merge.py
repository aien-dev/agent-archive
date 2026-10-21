#!/usr/bin/env python3
"""merge.py OUT.db IN1.db IN2.db ...   -> one combined catalog.db
Inputs may come from older builds with fewer columns: columns are matched by name, missing ones stay NULL.
A session id seen in an earlier input wins (put the freshest database first); later copies are skipped.
export.py-style readable output lives in export.py."""
import sqlite3, sys, os
out, ins = sys.argv[1], sys.argv[2:]
if os.path.exists(out): os.remove(out)
db = sqlite3.connect(out)
newest = max(ins, key=lambda p: len(sqlite3.connect(p).execute("pragma table_info(messages)").fetchall()))
for (sql,) in sqlite3.connect(newest).execute("select sql from sqlite_master where sql is not null and name not like 'sqlite_%' and name not like 'messages_fts%' and type in ('table','index')"):
    db.execute(sql)

def cols(schema, t):
    return [r[1] for r in db.execute(f"pragma {schema}.table_info({t})")]

for p in ins:
    db.execute("ATTACH ? AS src", (p,))
    sc = [c for c in cols("main", "sessions") if c in cols("src", "sessions")]
    mc = [c for c in cols("main", "messages") if c in cols("src", "messages") and c != "id"]
    dc = [c for c in cols("main", "documents") if c in cols("src", "documents") and c != "id"]
    db.execute("CREATE TEMP TABLE IF NOT EXISTS fresh(id TEXT PRIMARY KEY)"); db.execute("DELETE FROM fresh")
    db.execute("INSERT INTO fresh SELECT id FROM src.sessions WHERE id NOT IN (SELECT id FROM main.sessions)")
    db.execute(f"INSERT INTO sessions({','.join(sc)}) SELECT {','.join(sc)} FROM src.sessions WHERE id IN (SELECT id FROM fresh)")
    db.execute(f"INSERT INTO messages({','.join(mc)}) SELECT {','.join(mc)} FROM src.messages WHERE session_id IN (SELECT id FROM fresh)")
    db.execute(f"INSERT INTO documents({','.join(dc)}) SELECT {','.join(dc)} FROM src.documents")
    for k, n in db.execute("select kind,n from src.redaction_stats").fetchall():
        db.execute("INSERT INTO redaction_stats VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET n=n+excluded.n", (k, n))
    db.commit(); db.execute("DETACH src")
db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, content='messages', content_rowid='id')")
db.execute("INSERT INTO messages_fts(rowid,text) SELECT id,text FROM messages")
db.commit(); db.execute("VACUUM")
for t in ("sessions", "messages", "documents"):
    print(t, db.execute(f"select count(*) from {t}").fetchone()[0])
