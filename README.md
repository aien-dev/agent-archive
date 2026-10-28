# agent-archive

One central, long-term archive of every AI agent chat on the DGX Spark: Claude Code, Codex, OpenCode,
Gemini/Antigravity (Spark and MacBook) and Muse. Plain JSONL files, one common record per message,
plus a verbatim mirror of the original source files. Single Rust binary, no Python, no network except a
read-only `ssh`/`scp` pull from the MacBook.

Supports [Aienix](https://github.com/aien-dev/aienix), the core tree of AIEN. It is a tool for the people and agents
building AIEN: it keeps their chats so past work can be searched later.

**Status:** the collector is in use. This repository documents the source behavior; it does not
establish which binary or on-disk permissions are currently deployed.

## Layout

Archive root: `~/archive/agent-chats/` (override with `AGENT_ARCHIVE_ROOT`).

The collector creates the archive root and every archive subdirectory with mode `0700`, and new
records, raw copies, latest views, state files, and temporary files with mode `0600`. It rejects
unsafe roots and top-level directories, and checks each descendant directory or file before use:
symlinks, foreign owners, and group or other access are refused. This covers reads as well as
writes: state cursors, the de-duplication scan, the history behind the latest views, pulled
MacBook copies, and `status` and `query` refuse an unsafe path with an error (exit 1 for the
commands) instead of treating it as absent. Use an owner-controlled parent for a custom root; do
not put it under a directory another local user can replace.

Before upgrading an existing archive, stop its collector and inspect its ownership and symlinks
without printing transcript contents. With `root` set to the archive path, resolve any foreign
ownership or symlink intentionally, then restrict existing entries:

```sh
find "$root" \( -type l -o ! -user "$(id -un)" \) -print
find "$root" -type d -exec chmod 700 {} +
find "$root" -type f -exec chmod 600 {} +
```

Do not run the mode repair on a path you do not own or on a live collector. The first command
should print no entries before restarting. Existing files are not silently changed at startup.

The lockfile pins `libc` 0.2.190, used here only for the effective user ID check. At an Omega
cutover, replace this host call with an explicit owner-identity host interface; retain the
mode and ownership refusal behavior.

| Path | What |
|---|---|
| `records/<agent>/<YYYY-MM-DD>.jsonl` | common records, append-only, date = message timestamp (UTC) |
| `raw/<agent>/<host>/<original relative path>` | verbatim copies of the source files (JSONL appended byte for byte as they grow; sqlite files copied when they change) |
| `latest/<agent>.txt`, `latest/<agent>.jsonl` | rolling window, last 20000 records, rewritten atomically (temp file + rename). `.txt` lines look like `[ts session8 role] text` with newlines shown as `\n`; text is cut at 8000 characters (`...[truncated]`) |
| `state/<agent>.json` | per-source cursors (byte offsets, row cursors, file signatures) |

State loss only causes re-reads: records are de-duplicated on `(agent, host, session, role, src.path, src.pos)`
and the raw mirror never writes bytes it already has.

## Record schema

```json
{"ts":"2026-10-04T20:00:00.000Z","agent":"claude|codex|opencode|gemini|muse","host":"spark|macbook",
 "session":"<id>","role":"user|assistant|system","text":"...","model":null,"cwd":null,
 "src":{"path":"...","pos":123}}
```

`src.pos` is the byte offset of the source line (JSONL sources), the step index (Gemini) or a stable hash of the
part id (OpenCode). Only conversational text is turned into records; tool calls and results stay in `raw/`.

## Commands

```
agent-archive collect                    # loop every 10 s (run detached)
agent-archive once [--no-mac]            # one pass, prints per-agent counts and duration
agent-archive status                     # per agent: last record, today/total, sources, preview
agent-archive query [--agent claude] --since 2026-10-04T00:00:00Z --grep 'regex' --limit 50 [--json]
agent-archive debug-pb FILE              # dump a raw protobuf blob (for Gemini format work)
```

## Sources and parsers

| Agent | Source | Status |
|---|---|---|
| claude | `~/.claude/projects/**/*.jsonl`, lines `type` user/assistant, text blocks only | unit-tested on `tests/fixtures/claude.jsonl` |
| codex | `~/.codex/sessions/**/*.jsonl`, `response_item` messages (`developer` becomes `system`) | unit-tested on `tests/fixtures/codex.jsonl` |
| opencode | `~/.local/share/opencode/opencode.db` (opened read-only), `text` parts; raw mirror is `raw/opencode/spark/opencode-rows.jsonl` (one JSON line per text part, with its message JSON) because the database is live | unit-tested against a small database the test builds with the tables and columns the query reads; no sample of the live database is stored in this repo |
| gemini | per-conversation sqlite `.db` in `~/.gemini/antigravity{,-cli,-ide}/conversations` (Spark) and, via `ssh` + `scp` to the host alias `macbook` (an entry in the local ssh config), `~/.gemini/antigravity{,-cli,-ide}/conversations` on the Mac into `raw/gemini/macbook/` | UNVERIFIED format: step blobs are protobuf with no schema available; field numbers (user text 19.3.1, reply 20.1, time 5.1) were inferred from sampling. Reply "thinking" text (20.3) is not turned into records. Mac WAL files are pulled but a `-shm` is not, so uncommitted Mac data may be missed |
| muse | `~/.local/share/muse/sessions/**/session.jsonl`; user text from `runtime.user_intent.accepted`, replies from `assistant_message_committed` | partly verified (unit-tested on `tests/fixtures/muse.jsonl`): the original design note named `turn.input.user` / `run.output.delta`, but those did not occur in the 81 sessions on disk when this parser was written, so the events above are used instead. Framed `retained_frame` lines are only kept in raw |

The Mac pull is skipped (with a log line) if the ssh connection fails, and runs at most every 5 minutes.
`~/.gemini/antigravity-cli/history.jsonl` is command history only and is mirrored raw.

## Adding a collector

1. Add `parse_<agent>(line, path, pos, host) -> Vec<Record>` in `src/collectors.rs` (pure function).
2. For JSONL sources call `drive_jsonl(...)` from a `collect_<agent>` function; for databases keep a cursor with
   `store.cursor/set_cursor` and call `store.emit(record, check_dup)`.
3. Register it in `pass()` in `src/main.rs`, add the name to `AGENTS` in `src/core.rs`.
4. Cut a small fixture from a real sample (no secrets) into `tests/fixtures/` and add a unit test in the `tests` module of `src/main.rs`.

## Cortex rule

Raw chat never goes into Cortex (house rule). Cortex gets only short digests plus a pointer to this archive, later.

## License

AGPL-3.0-or-later.

## Human-Directed Agentic Engineering

Development follows Human-Directed Agentic Engineering (HDAE): humans define goals,
architecture, constraints, acceptance criteria, and stopping points; agents implement,
execute checks, diagnose failures, and revise within those boundaries. Human review
retains responsibility for claims, acceptance, and release decisions. This describes
the development method, not a change to runtime autonomy or historical evidence.

For the documented method and a systems-software case study, see Gregory M. Kurtzer
(2026), *Ginux: Building an Independent Kernel That Runs Linux Userspace through
Human-Directed Agentic Engineering*, technical report, v1, Zenodo,
<https://doi.org/10.5281/zenodo.23682548>.
