"""Second privacy layer for the private Drake-voice traces, on top of redact.py (secrets, emails, phones, IPs).

Scrubs, with counts per kind:
  health   whole sentence replaced when it talks about health, medicine or a body (doctor, meds, diagnosis...)
  name     third-party people: a fixed list seen in the dictations, plus any capitalized name right after
           "my brother/mom/friend/boss..." ; each person gets a stable token [NAME_n]
  place    home-region towns and states
  address  street addresses, PO boxes, state+ZIP
  money    currency amounts, account/routing/card numbers
The name and place lists come from reading the Wispr export (2026-10-21) and are kept in a private local file
(see below). They are not complete; extend them when a hand audit finds a miss.
"""
import re, os, json
from collections import Counter

# The people and places to scrub are private, so they live outside the code in a local JSON file:
#   {"names": [...], "places": [...]}   default ~/handoffs/wispr/scrub_lists.json, or env SCRUB_LISTS
_lists = os.environ.get("SCRUB_LISTS", os.path.expanduser("~/handoffs/wispr/scrub_lists.json"))
_L = json.load(open(_lists)) if os.path.exists(_lists) else {}
NAMES, PLACES = _L.get("names", []), _L.get("places", [])
REL = (r"brother|sister|mom|mother|dad|father|wife|girlfriend|boyfriend|son|daughter|uncle|aunt|cousin|grandma|grandmother|grandpa|"
       r"grandfather|friend|buddy|boss|partner|fianc[eé]e?|nephew|niece|roommate|coworker|lawyer|doctor|accountant|landlord|therapist")
HEALTH = re.compile(r"(?i)\b(?:doctors?|hospital\w*|diagnos\w*|medication\w*|medicine|meds\b|prescri\w*|surger\w*|therapist|therapy|adhd|anxiety|"
                    r"depress(?:ed|ion)|panic attack|illness|symptom\w*|\d+\s?mg\b|dosage|insulin|cancer|tumou?r|injur\w*|emergency room|"
                    r"mental health|psychiatr\w*|xanax|vicodin|adderall|covid|rehab|addict\w*|overdose|seizure|migraine|pregnan\w*|dentist|"
                    r"chronic pain|in pain|my (?:back|knee|head|stomach|chest|tooth|teeth) hurts?|(?:i'?m|i am|got|feeling|been) sick\b(?! of)|"
                    r"medical|health insurance|blood (?:pressure|sugar|test|work)|pills?\b|sober|hangover|masturbat\w*|"
                    r"passed away|funeral|tombstone|(?:he|she|my \w+) died|death of my|hospice)")
HEALTH_OK = re.compile(r"(?i)health ?check|health pulse|node health|disk health|system health|hardware health|medical[- ]grade")
SENT = re.compile(r"[^.!?\n]*[.!?\n]?")
ADDRESS = re.compile(r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|Way|Circle|Cir|"
                     r"Parkway|Pkwy|Place|Pl|Terrace|Highway|Hwy)\b\.?(?:,?\s*(?:Apt|Unit|Suite|#)\s*\w+)?|\bP\.?\s?O\.? Box \d+\b|\b[A-Z]{2}\s\d{5}(?:-\d{4})?\b")
MONEY = re.compile(r"(?i)\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|b|million|billion|thousand|grand)\b)?|\b\d[\d,]*(?:\.\d+)?\s?(?:dollars|bucks|grand|usd)\b|"
                   r"\b(?:account|acct|routing|card|iban|ssn|social security)(?:\s+(?:number|no\.?|#))?\D{0,12}\d[\d\- ]{5,}\d|\b(?:\d{4}[ -]){3}\d{4}\b")

class Scrubber:
    def __init__(self):
        self.stats = Counter()
        self.name_ids = {}
        self.name_rx = re.compile(r"\b(" + "|".join(sorted(set(NAMES), key=len, reverse=True)) + r")\b") if NAMES else re.compile(r"(?!x)x")
        self.rel_rx = re.compile(r"(?i)\b(my|our|his|her)\s+(" + REL + r")((?:,? (?:named|called|is))?,?\s+)([A-Z][a-z]{2,})\b")
        self.place_rx = re.compile(r"\b(" + "|".join(PLACES) + r")\b") if PLACES else re.compile(r"(?!x)x")

    def _name(self, n):
        if n not in self.name_ids: self.name_ids[n] = len(self.name_ids) + 1
        return f"[NAME_{self.name_ids[n]}]"

    def clean(self, text):
        if not text: return text or "", 0
        n0 = sum(self.stats.values())
        def health_sent(m):
            s = m.group(0)
            if s and HEALTH.search(s) and not HEALTH_OK.search(s):
                self.stats["health"] += 1; return "[SCRUBBED:health]" + (s[-1] if s[-1] in ".!?\n" else "")
            return s
        text = SENT.sub(health_sent, text)
        def rel(m):
            if m.group(4) in ("And", "The", "What", "But", "Who", "Was", "Had", "Too", "Because", "About", "Atlas", "Claude", "Spark"): return m.group(0)
            self.stats["name"] += 1
            return m.group(1) + " " + m.group(2) + m.group(3) + self._name(m.group(4))
        text = self.rel_rx.sub(rel, text)
        def nm(m): self.stats["name"] += 1; return self._name(m.group(1))
        text = self.name_rx.sub(nm, text)
        def pl(m): self.stats["place"] += 1; return "[PLACE]"
        text = self.place_rx.sub(pl, text)
        def ad(m): self.stats["address"] += 1; return "[ADDRESS]"
        text = ADDRESS.sub(ad, text)
        def mo(m): self.stats["money"] += 1; return "[MONEY]"
        text = MONEY.sub(mo, text)
        return text, sum(self.stats.values()) - n0
