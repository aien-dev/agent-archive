# agent-archive

One central, long-term archive of every AI agent chat on the DGX Spark: Claude Code, Codex, OpenCode,
Gemini/Antigravity (Spark and MacBook) and Muse. Plain JSONL files, one common record per message,
plus a verbatim mirror of the original source files. Single Rust binary, no Python, no network except a
read-only `ssh`/`scp` pull from the MacBook.

## Layout

Archive root: `~/archive/agent-chats/` (override with `AGENT_ARCHIVE_ROOT`).

| Path | What |
|---|---|
| `records/<agent>/<YYYY-MM-DD>.jsonl` | common records, append-only, date = message timestamp (UTC) |
| `raw/<agent>/<host>/<original relative path>` | verbatim copies of the source files (JSONL appended byte for byte as they grow; sqlite files copied when they change) |
| `latest/<agent>.txt`, `latest/<agent>.jsonl` | rolling window, last 20000 records, rewritten atomically (temp file + rename). `.txt` lines look like `[ts session8 role] text` with newlines shown as `\n` |
| `state/<agent>.json` | per-source cursors (byte offsets, row cursors, file signatures) |

State loss only causes re-reads: records are de-duplicated on `(agent, host, session, role, src.path, src.pos)`
and the raw mirror never writes bytes it already has.

## Record schema

```json
{"ts":"2026-10-04T20:00:00.000Z","agent":"claude|codex|opencode|gemini|muse","host":"spark|macbook",
 "session":"<id>","role":"user|assistant|system|tool","text":"...","model":null,"cwd":null,
 "src":{"path":"...","pos":123}}
```

`src.pos` is the byte offset of the source line (JSONL sources), the step index (Gemini) or a stable hash of the
part id (OpenCode). Only conversational text is turned into records; tool calls and results stay in `raw/`.

## Commands

```
agent-archive collect                    # loop every 10 s (run detached)
agent-archive once [--no-mac]            # one pass, prints per-agent counts and duration
agent-archive status                     # per agent: last record, today/total, sources, preview
agent-archive query --agent claude --since 2026-10-04T00:00:00Z --grep 'regex' --limit 50 [--json]
agent-archive debug-pb FILE              # dump a raw protobuf blob (for Gemini format work)
```

## Sources and parsers

| Agent | Source | Status |
|---|---|---|
| claude | `~/.claude/projects/**/*.jsonl`, lines `type` user/assistant, text blocks only | verified on live data |
| codex | `~/.codex/sessions/**/*.jsonl`, `response_item` messages (`developer` becomes `system`) | verified on live data |
| opencode | `~/.local/share/opencode/opencode.db` (opened read-only), `text` parts; raw mirror is `raw/opencode/spark/opencode-rows.jsonl` (the rows, verbatim JSON) because the database is live | verified on live data |
| gemini | per-conversation sqlite `.db` in `~/.gemini/antigravity-{cli,ide}/conversations` (Spark) and, via `ssh macbook` + `scp`, `~/.gemini/antigravity{,-cli,-ide}/conversations` on the Mac into `raw/gemini/macbook/` | UNVERIFIED format: step blobs are protobuf with no schema available; field numbers (user text 19.3.1, reply 20.1, time 5.1) were inferred from sampling. Reply "thinking" text (20.3) is not turned into records. Mac WAL files are pulled but a `-shm` is not, so uncommitted Mac data may be missed |
| muse | `~/.local/share/muse/sessions/**/session.jsonl`; user text from `runtime.user_intent.accepted`, replies from `assistant_message_committed` | partly verified: the original design note named `turn.input.user` / `run.output.delta`, but those do not occur in the 81 sessions on disk, so the events above are used instead. Framed `retained_frame` lines are only kept in raw |

The Mac pull is skipped (with a log line) if `ssh macbook` fails, and runs at most every 5 minutes.
`~/.gemini/antigravity-cli/history.jsonl` is command history only and is mirrored raw.

## Adding a collector

1. Add `parse_<agent>(line, path, pos, host) -> Vec<Record>` in `src/collectors.rs` (pure function).
2. For JSONL sources call `drive_jsonl(...)` from a `collect_<agent>` function; for databases keep a cursor with
   `store.cursor/set_cursor` and call `store.emit(record, check_dup)`.
3. Register it in `pass()` in `src/main.rs`, add the name to `AGENTS` in `src/core.rs`.
4. Cut a small fixture from a real sample (no secrets) into `tests/fixtures/` and add a unit test.

## Cortex rule

Raw chat never goes into Cortex (house rule). Cortex gets only short digests plus a pointer to this archive, later.

## License

AGPL-3.0-or-later.
