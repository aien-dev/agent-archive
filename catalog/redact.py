"""Redaction for the catalog. Two layers:
 1. literal secrets harvested from this machine's own key files (never stored in the repo)
 2. regex patterns for common token shapes, key=value secrets, emails, phones, private IPs
Replacement text is [REDACTED:kind] so readers can see something was there.
"""
import os, re, glob, json
from collections import Counter

PATTERNS = [
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai-style-key", re.compile(r"\bsk-(?:proj-|or-v1-)?[A-Za-z0-9_\-]{20,}")),
    ("hf-token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("telegram-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b")),
    ("bearer", re.compile(r"(?i)\b(bearer|token)\s+([A-Za-z0-9_\-\.=]{24,})")),
    ("url-credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/:@]+:[^\s/@]+@")),
    ("secret-assignment", re.compile(
        r"(?i)\b([A-Z0-9_\-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key|desk[_-]?key|token)[A-Z0-9_\-]*)"
        r"(\s*[:=]\s*|\s+is\s+)(['\"]?)([^\s'\"`,;]{8,})\3")),
    ("hex-token", re.compile(r"(?<![0-9a-fA-F/])[0-9a-f]{40}(?![0-9a-fA-F])|(?<![0-9a-fA-F/])[0-9a-f]{64}(?![0-9a-fA-F])")),
    ("headscale-key", re.compile(r"\bhskey-[A-Za-z0-9_\-]{10,}")),
    ("ssh-pubkey", re.compile(r"\b(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+)\s+AAAA[A-Za-z0-9+/=]{20,}")),
    ("tailnet", re.compile(r"\b[a-z0-9\-]+\.tail[0-9a-f]{4,}\.ts\.net\b|\btail[0-9a-f]{6}\b")),
    ("tunnel-url", re.compile(r"https?://[a-z0-9\-]+\.trycloudflare\.com[^\s]*")),
    ("mac-address", re.compile(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")),
    ("one-time-code", re.compile(r"(?i)(one[- ]time code|device code|verification code|enter (?:the )?code)[:\s]+([A-Z0-9]{4}-[A-Z0-9]{4})")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")),
    ("private-ip", re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b")),
]
KEEP_EMAIL = re.compile(r"(?i)(noreply|no-reply|@users\.noreply\.github\.com|@example\.|@anthropic\.com$)")

SECRET_FILES = [
    "~/.hermes/auth.json", "~/.hermes/.env", "~/.config/aien-desk/DESK.key",
    "~/.local/state/aien-local-ci/DESK.key", "~/.claude/.credentials.json",
    "~/.config/gh/hosts.yml", "~/.netrc", "~/.hermes/config.yaml", "~/.claude/settings.json",
]

def harvest(home):
    """Literal secret-looking values from local key files. Returned only in memory."""
    vals = set()
    bl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "blocklist.txt")
    if os.path.isfile(bl):
        for ln in open(bl):
            ln = ln.strip()
            if ln and not ln.startswith("#"): vals.add(ln)
    for p in SECRET_FILES:
        p = os.path.expanduser(p)
        if not os.path.isfile(p): continue
        try: raw = open(p, errors="replace").read()
        except Exception: continue
        if os.path.basename(p) == "DESK.key":
            vals.add(raw.strip()); continue
        # any quoted/long value on a line whose key looks secret
        for m in re.finditer(r"(?i)(?:key|token|secret|password|pass|auth)[\w\-]*[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-\.\/+=]{12,})", raw):
            vals.add(m.group(1))
    # tool env
    for k, v in os.environ.items():
        if re.search(r"(?i)key|token|secret|password", k) and len(v) >= 12:
            vals.add(v)
    return {v for v in vals if len(v) >= 6}

class Redactor:
    def __init__(self, home):
        self.stats = Counter()
        self.literals = sorted(harvest(home), key=len, reverse=True)

    def clean(self, text):
        if not text: return text or "", 0
        n = 0
        for v in self.literals:
            if v in text:
                c = text.count(v); text = text.replace(v, "[REDACTED:local-secret]")
                self.stats["local-secret"] += c; n += c
        for kind, rx in PATTERNS:
            def sub(m, kind=kind):
                nonlocal n
                if kind == "email" and KEEP_EMAIL.search(m.group(0)): return m.group(0)
                if kind == "secret-assignment":
                    val = m.group(4)
                    # skip obvious placeholders / paths / code identifiers
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*(\(\))?", val) and not re.search(r"\d", val): return m.group(0)
                    if val.startswith(("$", "{", "<", "[REDACTED", "/", "~", "http")): return m.group(0)
                    self.stats[kind] += 1; n += 1
                    return f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED:{kind}]{m.group(3)}"
                if kind == "bearer":
                    self.stats[kind] += 1; n += 1
                    return f"{m.group(1)} [REDACTED:bearer]"
                if kind == "one-time-code":
                    self.stats[kind] += 1; n += 1
                    return f"{m.group(1)}: [REDACTED:one-time-code]"
                if kind == "hex-token" and re.search(r"(?i)(commit|sha|hash|blob|tree|digest|[0-9a-f]{7,}\.\.)", text[max(0,m.start()-40):m.start()]): return m.group(0)
                if kind == "url-credentials":
                    self.stats[kind] += 1; n += 1
                    return f"{m.group(1)}[REDACTED:credentials]@"
                self.stats[kind] += 1; n += 1
                return f"[REDACTED:{kind}]"
            text = rx.sub(sub, text)
        return text, n
