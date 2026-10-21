"""Rule labels for Drake's messages and the cause of each anger or correction.

Deterministic keyword rules, no model calls, so a rerun gives the same labels. Every label keeps the
signals that fired, so a reader (or a later model pass) can audit it. Profanity alone is not anger:
Drake swears in praise too ("impressed the fuck out of me").

Cause taxonomy: the 13 correction themes from phil/themes_correction.json (2026-10-19 philosophy pass)
plus the brief's list (asked approval, ran a test too early, dismissed an idea without proof, repeated an
old result, stalled, refused, wrong model) and a few the data added. Each cause has rules on Drake's words
and on what the agent did just before (its last reply and tool calls).
"""
import re

def rx(*alts): return re.compile(r"(?i)\b(?:" + "|".join(alts) + r")")

PROFANITY = rx(r"fuck\w*", r"shit\w*", r"damn\w*", r"goddamn\w*", r"bullshit", r"wtf", r"crap\w*", r"bitch\w*",
               r"asshole", r"dumbass", r"piss(?:ed)?", r"hell\b")
FRUSTRATION = rx(r"why (?:are|did|do|would|the hell|the fuck) (?:you|it|this|we)\b", r"what (?:the hell|the fuck|are you doing)",
                 r"are you (?:kidding|serious|stupid|dumb)", r"how many times", r"i (?:already )?told you", r"stop (?:doing|asking|it|that|wasting)",
                 r"this is (?:ridiculous|stupid|insane|unacceptable|bullshit)", r"seriously\?", r"ridiculous", r"stupid", r"idiot\w*",
                 r"frustrat\w*", r"annoy\w*", r"pissed", r"sick of", r"tired of", r"come on\b", r"(?:what|why) is (?:this|it) (?:taking|doing)",
                 r"you(?:'re| are) (?:wrong|lying|not listening)", r"waste of", r"wasting (?:my|our)? ?time", r"unbelievable")
PRAISE = rx(r"good job", r"great (?:job|work)", r"nice (?:job|work|one)", r"well done", r"awesome", r"amazing", r"perfect\b", r"love (?:it|this|that)",
            r"proud", r"impress\w*", r"thank(?:s| you)", r"beautiful", r"excellent", r"fantastic", r"incredible", r"exactly (?:what|the)",
            r"nailed it", r"brilliant", r"stunning", r"that's (?:great|awesome|perfect|it)", r"good work", r"hell yeah", r"let's go\b")
CORRECTION = rx(r"^\s*no\b", r"^\s*nope", r"^\s*not\b", r"^\s*stop\b", r"^\s*wait\b", r"^\s*actually\b", r"don't\b", r"do not\b", r"instead\b",
                r"that(?:'s| is) not", r"not what i", r"wrong\b", r"i (?:said|meant|told you|asked)", r"you should(?:n't| not)? have", r"rather than",
                r"never (?:do|use|ask)", r"undo\b", r"revert\b", r"go back\b", r"why did you", r"you (?:missed|forgot|ignored)", r"not (?:that|this) one",
                r"i didn't (?:say|ask|want)")
INTENT = rx(r"i want\b", r"i'd like", r"i would like", r"what if\b", r"(?:new|an|my) idea", r"imagine\b", r"vision\b", r"we should\b", r"let's (?:build|make|create|start)",
            r"i think we (?:should|need|could)", r"the goal\b", r"eventually\b", r"in the future", r"i need you to (?:build|make|create|design|write|set up)",
            r"i want you to (?:build|make|create|design|write)", r"long[- ]term", r"the plan is")
STATUS = rx(r"status\b", r"where are we", r"how(?:'s| is) (?:it|that|this|the) (?:going|coming)", r"what(?:'s| is) (?:happening|going on|the progress|left)",
            r"progress\b", r"update me", r"(?:is|are) (?:it|they|you) (?:done|finished|ready)", r"did (?:it|that|you) (?:work|finish|pass)",
            r"what happened", r"how far", r"what are you (?:doing|working on)", r"\beta\b", r"how long (?:will|until|is)", r"where (?:is|are) (?:it|we|the)",
            r"any (?:news|update)", r"what's the (?:latest|state)", r"what did (?:you|they) (?:find|do)")
APPROVAL = rx(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|sounds good|perfect|correct|right)\b", r"go ahead", r"approved?\b", r"ship it", r"merge (?:it|them)",
              r"do it\b", r"proceed\b", r"go for it", r"let's do it", r"(?:option|candidate) (?:one|two|three|[123abc])\b", r"that works", r"green light",
              r"you have my (?:ok|approval|permission)", r"please do\b", r"make it so")

LABELS = [("anger", None), ("praise", PRAISE), ("correction", CORRECTION), ("intent", INTENT), ("status_ask", STATUS), ("approval", APPROVAL)]

def label(text):
    """Multi-label dict: {label: [signals]}. Anger needs frustration, or profanity without praise."""
    t = text or ""
    out = {}
    prof = [m.group(0) for m in PROFANITY.finditer(t)]
    frus = [m.group(0) for m in FRUSTRATION.finditer(t)]
    pra = [m.group(0) for m in PRAISE.finditer(t)]
    if frus or (prof and not pra): out["anger"] = (frus + prof)[:6]
    for name, r in LABELS[1:]:
        hits = [m.group(0) for m in r.finditer(t)]
        if hits: out[name] = hits[:6]
    if "anger" in out and "praise" in out and not frus: del out["anger"]
    if "praise" in out and "anger" in out and frus and len(pra) <= 1 and pra[0].lower().startswith("thank"): del out["praise"]
    return out

# cause id -> (description, regex on Drake's words, regex on the agent's last reply/tool calls or None)
CAUSES = [
    ("asked_approval", "asked Drake to approve or pick when it could decide",
     rx(r"(?:why|stop|quit|don't|do not) (?:are you |keep )?ask\w*", r"you don't need (?:my|to ask)", r"just do it", r"stop (?:asking|checking with me)",
        r"why do you need my", r"don't ask (?:me|permission)", r"you decide", r"use your (?:best )?judgment"),
     rx(r"(?:do you want|would you like|should i|shall i|want me to|which (?:option|one) (?:do you|would you)|let me know (?:if|which|whether)|your call|"
        r"please (?:confirm|approve)|(?:approve|confirm)\?|option [12ab]\b|before i (?:proceed|continue)|can i go ahead)")),
    ("made_drake_do_work", "handed Drake work or commands to run",
     rx(r"why (?:are|do) you (?:asking|want|need|telling) me to", r"i(?:'m| am) not (?:going|gonna) (?:to )?(?:run|do|type)", r"(?:you|just) do it yourself",
        r"i don't know how to", r"i can't (?:read|run|do) (?:that|this|code)", r"why would i (?:run|do)", r"you have (?:access|the tools)"),
     rx(r"you(?:'ll)? (?:need|have) to (?:run|type|open|paste|click)", r"(?:please )?run (?:this|the following)", r"in your terminal", r"paste (?:this|the following)",
        r"on your (?:mac|machine), run")),
    ("jargon_unclear", "explained in jargon or unclearly",
     rx(r"what does (?:that|this|it) mean", r"plain english", r"i don't understand", r"too technical", r"jargon", r"(?:in|use) (?:simple|plain|normal) (?:words|terms|english)",
        r"explain (?:it|that|this) (?:to me|like)", r"i have no idea what", r"speak (?:english|normal)"), None),
    ("test_too_early_or_slow", "ran tests before the job was done, or ran slow, serial or redundant tests",
     rx(r"stop (?:running|testing)", r"(?:don't|do not|why are you) (?:run|test)\w*", r"test\w* (?:before|again|too early|too much|every)", r"(?:the )?job (?:isn't|is not) done",
        r"not (?:done|finished) yet", r"in parallel", r"serial\w*", r"one at a time", r"20 cores", r"queue"),
     rx(r"cargo test", r"make test", r"pytest", r"\bbench\w*", r"\bgate\d?\b", r"qualif\w*", r"run_tests?", r"npm test")),
    ("dismissed_without_proof", "dismissed an idea or objected without proof (hedging, negativity)",
     rx(r"proof", r"prove (?:it|that)", r"evidence", r"how do you know", r"you(?:'re)? (?:just )?assum\w*", r"don't assume", r"says who", r"try it (?:first|before)",
        r"(?:it|that) is possible", r"don't be (?:so )?negative", r"(?:you said|why) (?:it )?(?:can't|couldn't|impossible)", r"stop (?:hedging|being negative)", r"physical proof"),
     rx(r"not (?:possible|feasible|realistic)", r"impossible", r"can't be done", r"won't work", r"unrealistic", r"(?:i'd|i would) (?:advise|recommend) against",
        r"not recommended", r"too risky", r"isn't (?:possible|feasible)")),
    ("repeated_old_result", "repeated an old or stale result",
     rx(r"same (?:thing|result|answer|number|output|error)", r"already (?:told|said|did|know|gave)", r"(?:old|stale|outdated) (?:result|number|data|info|plan|notes)",
        r"that's from (?:yesterday|before|last)", r"you keep (?:saying|giving|repeating)", r"(?:again|repeat\w*)\?", r"that's not new"), None),
    ("stalled", "stalled, idled or stopped early",
     rx(r"stuck", r"stall\w*", r"taking (?:so|too|this) long", r"why is (?:it|this) taking", r"nothing (?:is )?happen\w*", r"\bidle\b", r"sitting (?:there|around)",
        r"why did you stop", r"don't stop", r"keep going", r"hurry", r"still (?:not|waiting|nothing)", r"hours? (?:ago|later|now)", r"waiting (?:on|for) (?:nothing|you)",
        r"(?:are you|is it) (?:still )?(?:alive|working|there)"), None),
    ("refused", "refused or said it could not",
     rx(r"why (?:won't|can't) you", r"you refused", r"just do (?:it|what i said)", r"you can do (?:it|this|that)", r"you(?:'re| are) allowed"),
     rx(r"\bi can(?:'|no)t\b", r"i(?:'m| am) (?:not able|unable)", r"\bi won't\b", r"i(?:'m| am) not going to", r"against (?:my|the) (?:rules|policy|guidelines)",
        r"i must (?:decline|refuse)", r"not (?:able|permitted|allowed) to")),
    ("wrong_model", "used the wrong model or an expensive model for the job",
     rx(r"wrong model", r"(?:use|switch to|why (?:are you )?using|not) (?:opus|fable|sonnet|haiku|codex|astra|gpt|gemini|grok)\b", r"which model", r"cheaper model",
        r"(?:opus|fable|sonnet|haiku) (?:for|on) (?:this|that)"), None),
    ("delegated_instead_of_doing", "spawned workers for work it should do itself",
     rx(r"do it yourself", r"stop (?:spawning|delegating|launching)", r"why (?:are|did) you (?:spawn|delegat|launch)\w*", r"too many (?:agents|workers)", r"you do the (?:hard|work)",
        r"don't (?:delegate|pawn)", r"pawn\w* off"), rx(r"\bAgent\b", r"\bTask\b", r"claude -p", r"spawn\w*", r"delegat\w*")),
    ("reported_instead_of_fixing", "reported, planned or summarized instead of fixing",
     rx(r"(?:just|then|go) fix (?:it|that|them)", r"don't just (?:report|tell|list|plan)", r"stop (?:planning|summariz\w*|reporting)", r"why didn't you fix", r"fix it\b",
        r"i don't want a (?:report|summary|plan)", r"actually do (?:it|the work)"), None),
    ("overclaimed_or_unverified", "claimed done or true without proof (overclaimed, stale status, unverified)",
     rx(r"you (?:lied|said it was (?:done|working|fixed)|claimed)", r"(?:it's|it is|that's) not (?:actually )?(?:done|working|fixed|true|real)", r"did you (?:actually )?(?:check|test|verify|run)",
        r"hallucinat\w*", r"made (?:that|it) up", r"(?:that's|is that) (?:a )?(?:lie|real|true)\b", r"fake\b", r"rerun", r"verify"), None),
    ("uncoordinated_agents", "agents not coordinating (duplicate or conflicting work)",
     rx(r"another (?:agent|chat|session)", r"other agents?", r"(?:already|someone(?: else)?) (?:is )?(?:working|doing)", r"duplicate\w*", r"conflict\w*", r"step(?:ping)? on",
        r"two agents", r"coordinat\w*", r"talk to each other", r"blind to each other"), None),
    ("outside_or_toy_stack", "used outside tools or toy stand-ins instead of our own stack",
     rx(r"our own (?:engine|stack|code|model|kernel)", r"third[- ]party", r"vllm", r"llama\.?cpp", r"ollama", r"\bpython\b", r"not ours", r"outside (?:tool|library|engine)",
        r"\btoy\b", r"\bstubs?\b", r"\bmock\w*", r"fake data", r"simulat\w*", r"close to the metal", r"native"), None),
    ("context_or_credit_waste", "wasted context, tokens or usage credits",
     rx(r"context (?:window|size|bloat)", r"\btokens?\b", r"credits?", r"usage limit", r"rate limit", r"expensive", r"wasting (?:money|credits|tokens)"), None),
    ("drifted_off_task", "drifted off task or worked on the wrong thing",
     rx(r"off task", r"\bfocus\b", r"side (?:quest|issue|task)", r"not what i (?:asked|wanted|said)", r"that's not what", r"wrong thing", r"why are you (?:working on|doing|looking at) (?:that|this)",
        r"stay on", r"get back to", r"distract\w*"), None),
    ("ignored_instruction", "ignored a standing rule or what Drake already said",
     rx(r"i (?:already )?(?:told|asked|said)", r"how many times", r"didn't (?:listen|follow|read)", r"read (?:the|my) (?:instructions|rules|brief|message)", r"like i said", r"as i said",
        r"i just said", r"you(?:'re| are) not listening"), None),
    ("destructive_action", "deleted, overwrote or broke something",
     rx(r"delet\w*", r"wiped", r"removed (?:my|the|all)", r"you broke", r"broke (?:it|the|my)", r"destroy\w*", r"overwr\w*", r"lost (?:my|the|all)", r"killed (?:my|the)"), None),
]
CAUSE_IDS = [c[0] for c in CAUSES]

def causes(drake_text, before_text, before_tools):
    """Causes for an anger/correction message. Returns list of {cause, drake_signal, agent_signal}; [] means UNKNOWN.
    A cause with an agent-side rule needs Drake's words to fire; the agent side, when it also fires, is kept as evidence."""
    out = []
    agent_side = (before_text or "") + "\n" + "\n".join(before_tools or [])
    for cid, _desc, drx, arx in CAUSES:
        dm = drx.search(drake_text or "")
        if not dm: continue
        am = arx.search(agent_side) if arx else None
        out.append({"cause": cid, "drake_signal": dm.group(0), "agent_signal": am.group(0) if am else None})
    out.sort(key=lambda c: c["agent_signal"] is None)   # causes backed by what the agent did come first
    return out
