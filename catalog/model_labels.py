#!/usr/bin/env python3
"""model_labels.py prepare TRACES_DIR   -> TRACES_DIR/model/batches/bNNN.jsonl (+ prompt.txt)
   model_labels.py merge   TRACES_DIR   -> TRACES_DIR/model_labels.jsonl (cache keyed by trace id)

Second-pass labels by a model (run by model_label.sh with `claude -p --model sonnet`) for the matched traces.
Only scrubbed text is sent: the agent's last reply and tool calls, and Drake's formatted words.
The cache survives rebuilds; traces.py reads it when present. Rules stay in label_signals for comparison.
"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from labels import CAUSES

BATCH = 30

def short(t, n): t = " ".join((t or "").split()); return t if len(t) <= n else t[:n] + "..."

PROMPT = """You label how Drake (an engineer who directs AI coding agents by voice) reacted to what an agent just did.
For each item you get: what the agent said and did just before, and Drake's words. Output ONE JSON object per item, one per line, nothing else:
{"id": "<id>", "labels": [...], "cause": "<cause id>", "why": "<what the agent did, max 15 words>"}

labels: any of
  anger       Drake is angry or frustrated AT THE AGENT (profanity for emphasis, excitement or jokes is NOT anger)
  correction  he says the agent got something wrong, or redirects it away from what it was doing
  praise      he praises or thanks the agent
  approval    he approves, says yes, go ahead, picks an option
  intent      he states a new goal, idea or vision
  status_ask  he asks for status or progress
Use [] when none fit.

cause: only when labels has anger or correction, else "none". Pick the ONE that best explains what the agent did to trigger it:
%s
  other                       a clear cause not in the list (say it in why)
  UNKNOWN                     the context does not show what triggered it

Items:
"""

def prepare(td):
    T = [json.loads(l) for l in open(os.path.join(td, "traces.jsonl"))]
    have = set()
    cache = os.path.join(td, "model_labels.jsonl")
    if os.path.exists(cache): have = {json.loads(l)["id"] for l in open(cache)}
    items = []
    for t in T:
        if not t.get("match") or t["id"] in have: continue
        la = next((b for b in reversed(t.get("before") or []) if b["author"] == "assistant"), None)
        acts = [n + " " + a for b in (t.get("before") or [])[-2:] for n, a in b["tools"]][-4:]
        items.append({"id": t["id"], "agent_said": short(la["text"], 450) if la else "", "agent_did": [short(a, 120) for a in acts],
                      "drake": short(t["drake"]["formatted"] or t["drake"]["asr"], 600)})
    bd = os.path.join(td, "model", "batches"); os.makedirs(bd, exist_ok=True); os.makedirs(os.path.join(td, "model", "out"), exist_ok=True)
    for f in glob.glob(os.path.join(bd, "*.jsonl")): os.remove(f)
    for i in range(0, len(items), BATCH):
        with open(os.path.join(bd, f"b{i // BATCH:04d}.jsonl"), "w") as f:
            for it in items[i:i + BATCH]: f.write(json.dumps(it, ensure_ascii=False) + "\n")
    tax = "\n".join(f"  {cid:27s} {desc}" for cid, desc, _, _ in CAUSES)
    open(os.path.join(td, "model", "prompt.txt"), "w").write(PROMPT % tax)
    print("items", len(items), "batches", (len(items) + BATCH - 1) // BATCH)

def merge(td):
    ok = {c[0] for c in CAUSES} | {"other", "UNKNOWN", "none"}
    labs = {"anger", "correction", "praise", "approval", "intent", "status_ask"}
    cache = os.path.join(td, "model_labels.jsonl")
    have = {}
    if os.path.exists(cache):
        for l in open(cache): d = json.loads(l); have[d["id"]] = d
    bad = 0
    for f in sorted(glob.glob(os.path.join(td, "model", "out", "*.jsonl"))):
        for l in open(f):
            l = l.strip()
            if not l.startswith("{"): continue
            try: d = json.loads(l)
            except Exception: bad += 1; continue
            if not isinstance(d.get("labels"), list): bad += 1; continue
            d["labels"] = sorted({x for x in d["labels"] if x in labs})
            if d.get("cause") not in ok: d["cause"] = "other"
            if not ({"anger", "correction"} & set(d["labels"])): d["cause"] = "none"
            d["model"] = "claude-sonnet (claude -p --model sonnet)"
            have[d["id"]] = d
    with open(cache, "w") as f:
        for d in have.values(): f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print("model labels", len(have), "unparsed lines", bad)

if __name__ == "__main__":
    {"prepare": prepare, "merge": merge}[sys.argv[1]](sys.argv[2])
