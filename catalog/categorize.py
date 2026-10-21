"""Rule-based categorization. Deterministic, no model calls, so anyone can rerun it
and get the same labels. Edit the tables to match your own projects."""
import re
from collections import Counter

# project -> keywords (matched against path/cwd/title/first prompts, case-insensitive)
PROJECTS = {
    "omega-engine":   ["omega", "aien-serve", "qwen3", "kernel", "gb10", "tok/s", "inference"],
    "aienos":         ["aienos", "aien-os", "bootloader", "uefi", "edk2", "rpi5"],
    "allen":          ["allen"],
    "crumbs":         ["crumb"],
    "pr-desk":        ["pr desk", "pr-desk", "merge", "aien-local-ci", "desk"],
    "architecture":   ["architecture", "adr", "rfc-", "lane"],
    "yardsticks":     ["yardstick", "benchmark", "receipt", "wall-power", "upsc"],
    "hermes-agent":   ["hermes", "gateway", "kanban"],
    "web-sites":      ["aienos.com", "drakestapleton-com", "website", "landing"],
    "spark-hardware": ["spark", "dgx", "nvme", "tailscale", "ups"],
    "translation":    ["translate"],
    "design":         ["design", "css", "screensaver", "theme"],
}
KINDS = [
    ("debugging",   r"\b(bug|error|fail(ed|ing|s)?|broken|traceback|crash|fix(ing)?)\b"),
    ("building",    r"\b(build|implement|create|write|add|port|make)\b"),
    ("benchmarking",r"\b(benchmark|measure|tok/s|latency|throughput|ceiling|profile)\b"),
    ("research",    r"\b(research|investigate|find out|compare|survey|paper|arxiv)\b"),
    ("review-merge",r"\b(review|merge|pull request|\bpr\b|squash|diff)\b"),
    ("planning",    r"\b(plan|roadmap|design|rfc|proposal|strategy)\b"),
    ("ops",         r"\b(ssh|deploy|restart|install|config|server|service|systemd|cron)\b"),
    ("writing",     r"\b(readme|docs?|write up|blog|post|translate)\b"),
]
TAGS = ["python", "rust", "c", "cuda", "mojo", "fp8", "git", "github", "ssh", "ollama", "qwen", "claude", "sqlite", "docker", "linux", "macos"]

def _score(text, table):
    t = text.lower(); c = Counter()
    for k, kws in table.items():
        for w in kws:
            n = t.count(w)
            if n: c[k] += n
    return c

def classify_session(s, msgs):
    users = [m["text"] for m in msgs if m["role"] == "user"][:12]
    head = " ".join([s.get("project_dir") or "", s.get("repo") or "", s.get("title") or ""] + users)
    sc = _score(head, PROJECTS)
    project = sc.most_common(1)[0][0] if sc else "uncategorized"
    sample = " ".join(users[:5]).lower()
    ks = [(len(re.findall(rx, sample)), k) for k, rx in KINDS]
    ks.sort(reverse=True)
    kind = ks[0][1] if ks and ks[0][0] else "misc"
    allt = (head + " " + " ".join(m["text"][:500] for m in msgs[:30])).lower()
    tags = [t for t in TAGS if re.search(r"\b" + re.escape(t) + r"\b", allt)]
    tags += [p for p, _ in sc.most_common(4) if p != project]
    return {"project": project, "kind": kind, "tags": tags[:10]}

def classify_doc(path, text):
    sc = _score(path + " " + text[:3000], PROJECTS)
    project = sc.most_common(1)[0][0] if sc else "uncategorized"
    allt = text[:4000].lower()
    tags = [t for t in TAGS if re.search(r"\b" + re.escape(t) + r"\b", allt)]
    return {"project": project, "tags": tags[:10]}
