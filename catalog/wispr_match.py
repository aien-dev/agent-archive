#!/usr/bin/env python3
"""wispr_match.py catalog.db [flow.sqlite | history.jsonl] [--shift-hours N]

Cross-reference Wispr Flow dictation history against the catalog's human messages.

PRIVACY: Wispr records dictation into every app (browsers, chat apps, games...), most of it
unrelated to the project. This script publishes NOTHING from Wispr by itself. A dictation
is kept only when its words appear in a human message that is already in a project chat
session. Everything else is only counted (per app), never stored. Audio, screenshots and
on-screen text (axText, textboxContents) are never read.

Matching (v2, 2026-10-21): text plus time.
  - A dictation lands in a chat as a human message seconds to minutes after it was spoken, so only
    messages inside a time window after the dictation are candidates.
  - Tiers: long (>= 8 words): 5-word shingle containment >= 0.5, window up to LONG_WINDOW.
           mid (5-7 words): 3-word shingle containment >= 0.8, window MID_WINDOW (5 min).
           short (< 5 words): whole message equals the dictation, window SHORT_WINDOW (2 min).
  - score = containment x time factor; the best candidate wins, other sessions above threshold are kept
    as secondary matches (Drake often sends one ask to several agents).
  - Many dictations can land in one message (he dictates in bursts, then presses enter): the
    message-side pass records which dictations cover which part of the message.
  - --shift-hours N moves every dictation N hours forward: a negative control (false-match floor).
Library use: from wispr_match import load_wispr, Matcher
"""
import re, sqlite3, sys, os, datetime, collections, json, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import Redactor

K = 5  # words per shingle (long tier)
LONG_WINDOW = (-120, 6 * 3600)      # seconds relative to the dictation
MID_WINDOW = (-120, 5 * 60)        # hand check 2026-10-21: a generic 5-word phrase matched 24 min late
SHORT_WINDOW = (-60, 2 * 60)       # p90 delay is 35 s; 'Try again.' matched 8 min late in another app

def norm(t): return re.findall(r"[a-z0-9']+", (t or "").lower())
def shingles(w, k=K): return {" ".join(w[i:i+k]) for i in range(max(0, len(w) - k + 1))}

def epoch(ts):
    if not ts: return None
    if isinstance(ts, (int, float)): return float(ts) / (1000.0 if ts > 1e11 else 1.0)
    s = str(ts).strip().replace(" +00:00", "+00:00").replace("Z", "+00:00")
    if " " in s and "T" not in s: s = s.replace(" ", "T", 1)
    try: return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError: return None

def load_wispr(path):
    """Rows from flow.sqlite (Mac) or the history.jsonl export. Only text fields are read."""
    out = []
    if path.endswith(".jsonl"):
        for ln in open(path):
            d = json.loads(ln)
            out.append(d)
    else:
        flow = sqlite3.connect(f"file:{path}?mode=ro", uri=True); flow.row_factory = sqlite3.Row
        for r in flow.execute("select transcriptEntityId,timestamp,timezoneOffsetMinutes,app,url,status,asrText,formattedText,editedText,pastedText,numWords from History"):
            out.append(dict(r))
    for d in out:
        d["t"] = epoch(d.get("timestamp"))
        d["numWords"] = d.get("numWords") or 0
    return [d for d in out if d["t"] is not None]

def tfactor(dt):
    if dt < -120: return 0.0
    if dt <= 900: return 1.0
    if dt <= 2 * 3600: return 0.9
    return 0.8

class Matcher:
    def __init__(self, cat, R=None):
        self.R = R or Redactor(os.path.expanduser("~"))
        self.msgs = []   # (t, id, session_id, seq, author, words)
        for mid, sid, seq, text, ts, author in cat.execute(
                "select id,session_id,seq,text,ts,author from messages where role='user' and author in ('human','agent') and text<>''"):
            t = epoch(ts)
            if t is None: continue
            self.msgs.append((t, mid, sid, seq, author, norm(text)))
        self.msgs.sort()
        self.times = [m[0] for m in self.msgs]
        self.cache = {}

    def _sh(self, i, k):
        key = (i, k)
        if key not in self.cache: self.cache[key] = shingles(self.msgs[i][5], k)
        return self.cache[key]

    def variants(self, d):
        seen, out = set(), []
        for f in ("pastedText", "editedText", "formattedText", "asrText"):
            v = d.get(f)
            if not v: continue
            w = norm(self.R.clean(v)[0])
            if w and tuple(w) not in seen: seen.add(tuple(w)); out.append((f, w))
        return out

    def match(self, d, shift=0.0):
        """Best match for one dictation: dict(mid, session_id, seq, score, containment, dt, tier, field, secondary)."""
        t0 = d["t"] + shift
        cands = []
        for field, w in self.variants(d):
            n = len(w)
            if n >= 8: tier, k, thr, win = "long", K, 0.5, LONG_WINDOW
            elif n >= 5: tier, k, thr, win = "mid", 3, 0.8, MID_WINDOW
            else: tier, k, thr, win = "short", None, 1.0, SHORT_WINDOW
            lo = bisect.bisect_left(self.times, t0 + win[0]); hi = bisect.bisect_right(self.times, t0 + win[1])
            dsh = shingles(w, k) if k else None
            for i in range(lo, hi):
                t, mid, sid, seq, author, mw = self.msgs[i]
                dt = t - t0
                if tier == "short":
                    if mw != w: continue
                    cont = 1.0
                else:
                    if len(mw) < k: continue
                    msh = self._sh(i, k)
                    hit = sum(1 for s in dsh if s in msh)
                    cont = hit / len(dsh)
                    if cont < thr: continue
                sc = cont * tfactor(dt) + (0.01 if author == "human" else 0.0)
                cands.append({"mid": mid, "session_id": sid, "seq": seq, "score": round(sc, 4), "containment": round(cont, 3),
                              "dt": round(dt, 1), "tier": tier, "field": field, "author": author, "msg_words": len(mw), "dict_words": n})
        if not cands: return None
        cands.sort(key=lambda c: (-c["score"], abs(c["dt"])))
        best = cands[0]
        sec, seen = [], {best["session_id"]}
        for c in cands[1:]:
            if c["session_id"] in seen: continue
            seen.add(c["session_id"]); sec.append({k: c[k] for k in ("mid", "session_id", "seq", "score", "dt")})
        best["secondary"] = sec[:5]
        best["confidence"] = round(min(1.0, best["score"]) * {"long": 1.0, "mid": 0.9, "short": 0.6 + 0.08 * best["dict_words"]}[best["tier"]], 3)
        return best

    def coverage(self, mid, dict_word_lists):
        """Share of a message's words covered by the dictations matched to it (many-to-one)."""
        i = next((j for j, m in enumerate(self.msgs) if m[1] == mid), None)
        if i is None: return None
        mw = self.msgs[i][5]
        if not mw: return None
        covered = [False] * len(mw)
        for w in dict_word_lists:
            k = min(3, len(w))
            dsh = shingles(w, k)
            for j in range(len(mw) - k + 1):
                if " ".join(mw[j:j+k]) in dsh:
                    for x in range(j, j + k): covered[x] = True
        return round(sum(covered) / len(mw), 3)

def main(argv):
    cat = sqlite3.connect(argv[0])
    src = argv[1] if len(argv) > 1 and not argv[1].startswith("--") else os.path.expanduser("~/Library/Application Support/Wispr Flow/flow.sqlite")
    shift = float(argv[argv.index("--shift-hours") + 1]) * 3600 if "--shift-hours" in argv else 0.0
    M = Matcher(cat)
    rows = load_wispr(src)
    cat.executescript("""
DROP TABLE IF EXISTS dictations;
CREATE TABLE dictations(id INTEGER PRIMARY KEY, wispr_id TEXT, session_id TEXT, seq INTEGER, message_id INTEGER, ts TEXT, app TEXT,
  asr_text TEXT, formatted_text TEXT, overlap REAL, redactions INTEGER, tier TEXT, dt REAL, confidence REAL, secondary TEXT);
DROP TABLE IF EXISTS wispr_counts;
CREATE TABLE wispr_counts(app TEXT PRIMARY KEY, total INTEGER, matched INTEGER);
""")
    tot, mat = collections.Counter(), collections.Counter()
    for d in rows:
        if not (d.get("asrText") or d.get("formattedText")): continue
        app = d.get("app"); tot[app] += 1
        m = M.match(d, shift)
        if not m: continue
        mat[app] += 1
        a, n1 = M.R.clean(d.get("asrText") or ""); f, n2 = M.R.clean(d.get("formattedText") or "")
        cat.execute("insert into dictations(wispr_id,session_id,seq,message_id,ts,app,asr_text,formatted_text,overlap,redactions,tier,dt,confidence,secondary) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d.get("transcriptEntityId"), m["session_id"], m["seq"], m["mid"], d.get("timestamp"), app, a, f, m["containment"], n1 + n2,
                     m["tier"], m["dt"], m["confidence"], json.dumps(m["secondary"])))
    for app, n in tot.items():
        cat.execute("insert into wispr_counts values(?,?,?)", (app, n, mat[app]))
    cat.commit()
    print("dictations considered:", sum(tot.values()), " matched to project chats:", sum(mat.values()), " shift_h:", shift / 3600)
    for app, n in tot.most_common(12): print(f"  {app:55s} {n:6d} -> kept {mat[app]}")

if __name__ == "__main__":
    main(sys.argv[1:])
