> This folder is the Python chat-catalog pipeline (first published as aien-chat-catalog from the Mac), extended for ledger L378
> on the Spark. It reads the agent-archive records and raw copies this repo collects. **Code only: no transcripts, no Wispr
> dictations, no traces or training pairs are ever committed here; that data stays on the owner's machine.**

# aien-chat-catalog

The raw record of how AIEN was built with AI agents: every chat session and agent file from the project, categorized and cataloged in a database, with the human's words kept as typed.

Nothing here is polished. The prompts are the actual prompts, typos and all. The point is that a person (not a programmer, an engineer) built this by talking to agents, and you can read exactly how, then try to repeat it.

Numbers for this snapshot are in [STATS.md](STATS.md).

## What is in here

| Path | What it is |
|---|---|
| `transcripts/<project>/` | One readable markdown file per chat session. Human, assistant, and tool names. |
| `transcripts/<project>/subagents/` | Sessions run by background agents that the main agent spawned. |
| `documents/` | Agent files: handoff notes, skills, agent config (`CLAUDE.md`, `foundation.md`), plans, memory. |
| `catalog/sessions.csv` | One row per session: project, kind of work, tags, model, dates, counts, first prompt, link to transcript. |
| `catalog/documents.csv` | One row per agent file. |
| `catalog.db` (release asset) | The same data as a SQLite database with full-text search. See "Query it". |
| `build_catalog.py`, `redact.py`, `categorize.py`, `merge.py`, `export.py` | The pipeline that made all of this. |

## Who wrote each message

Agents talk to each other a lot, so every message carries an `author`:

- **human**: typed by a person. This is the part to read if you want to see how the work was directed.
- **assistant**: the model's reply.
- **agent**: a prompt written by another agent or an SDK script (for example a reviewer brief).
- **system**: harness noise (task notifications, "session continued" summaries, command output). Truncated.

Counting only `human` is the honest measure of what the person actually typed.

## How it was made

1. `build_catalog.py` runs on each machine and reads Hermes `state.db`, Claude Code transcripts (`~/.claude/projects/**/*.jsonl`), and agent files (`~/handoffs`, skills, plans, config). It keeps what was said and which tools were called. It drops thinking blocks, tool output, and raw API request dumps.
2. Every text goes through `redact.py` before it is written anywhere (see below).
3. `categorize.py` labels each session with a project, a kind of work, and tags using plain keyword rules. No model is involved, so the labels are reproducible. Edit the tables for your own projects.
4. `merge.py` combines the per-machine databases. `export.py` writes the markdown and CSV you see here.

To replicate on your own machine:

```
python3 build_catalog.py mine.db my-machine
python3 merge.py catalog.db mine.db
python3 export.py catalog.db ./out
```

## Query it

```
sqlite3 catalog.db
sqlite> select project, count(*) from sessions group by 1 order by 2 desc;
sqlite> select s.title, m.text from messages m join sessions s on s.id=m.session_id
        where m.author='human' and m.id in (select rowid from messages_fts where messages_fts match 'benchmark') limit 10;
```

## Redaction, and what it cannot promise

Redacted before publishing: API keys and tokens (GitHub, Anthropic, OpenAI-style, AWS, Google, Slack, Telegram, Hugging Face, JWT, bearer), private keys, `key=value` style secrets, URL credentials, email addresses, phone numbers, private and Tailscale IP addresses, and any literal secret value found in the local key files of the machine that built the catalog. Placeholders look like `[REDACTED:kind]`. Counts per kind are in STATS.md.

Pattern-based redaction is not perfect. If you find something that should not be public, open an issue or email the owner and it will be removed and rotated.

## License

AGPL-3.0, same as the other aien-dev repositories.

## Voice traces (code only: the data stays on the owner's machine)

The same pipeline can join Wispr Flow dictation history to these chats, to study how the person talks to agents and to
build private training pairs. **None of that data is in this repository and none of it is ever published**: the dictations
include personal life, health, family and money, far beyond the project. Only the code is here.

| Script | Does |
|---|---|
| `build_spark.sh` | rebuilds a machine catalog from every chat store found (Claude Code live + archived copies, messages typed while the agent was busy, Codex, Grok CLI, T3 Code, agent-archive records for Antigravity/opencode/muse, Hermes), then merges it with other machines' catalogs (`merge.py` now matches columns by name and keeps the first copy of a session) |
| `wispr_match.py` | text + time matching of dictations to human messages (tiers by length, time windows, many-to-one, a shifted-time negative control) |
| `traces.py` | one trace per dictation: what the agent did before, the words, the reply and next actions, subagents and agents launched after |
| `labels.py`, `model_labels.py`, `model_label.sh` | reaction labels (anger, correction, praise, approval, intent, status) and what the agent did to cause anger or a correction: keyword rules, then an optional cached model pass |
| `scrub.py` | second privacy layer on top of `redact.py`: health, third-party names, places, addresses, money. Its name and place lists are private and live outside the repo (`SCRUB_LISTS`) |
| `stats.py` | match rates, label and cause counts, time of day |
| `make_traces.sh` | all of the above in one command |
