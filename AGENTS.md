# agent-archive (central archive of AI agent chats on the DGX Spark)

> A single Rust binary that collects every AI agent chat on the DGX Spark (Claude Code, Codex, OpenCode, Gemini/Antigravity on Spark and MacBook, Muse) into plain JSONL records plus a verbatim raw mirror of the source files. This repository holds the collector code and small test fixtures only; the archive data itself lives outside the repository. No network access except a read-only `ssh`/`scp` pull from the MacBook.

Shared rules: read https://www.aienos.com/agents/RULES.md first. This file adds what is specific to this repository; where the two differ, this file wins here.

## Scope

- Applies to the whole repository: `src/` (`main.rs` commands and tests, `collectors.rs` parsers, `core.rs` store and agent list, `pb.rs` protobuf decoding), `tests/fixtures/`, `Cargo.toml`.
- Status: in use on the Spark. Code has been stable since the first commit.
- Supports [Aienix](https://github.com/aien-dev/aienix), the core tree of AIEN.

## Installation

```bash
git clone https://github.com/aien-dev/agent-archive.git
cd agent-archive
cargo build --release
cargo test --release
```

Requires a Rust toolchain. `rusqlite` is built with its bundled SQLite, so no system SQLite is needed. No Python.

## Configuration

- `AGENT_ARCHIVE_ROOT` overrides the archive root (default `archive/agent-chats/` under the user's home directory; see `README.md`).
- The Gemini MacBook pull uses the ssh host alias `macbook` from the local ssh config. If the connection fails the pull is skipped with a log line; it runs at most every 5 minutes. `once --no-mac` skips it.

## Usage

```
agent-archive collect                    # loop every 10 s (run detached)
agent-archive once [--no-mac]            # one pass, prints per-agent counts and duration
agent-archive status                     # per agent: last record, today/total, sources, preview
agent-archive query [--agent claude] --since 2026-10-04T00:00:00Z --grep 'regex' --limit 50 [--json]
agent-archive debug-pb FILE              # dump a raw protobuf blob (for Gemini format work)
```

These read real chat stores on the host and write the archive; run them only on the machine the archive belongs to. For code changes, `cargo test --release` is the check.

## Conventions

- Records are append-only, one common schema per message (see `README.md`), de-duplicated on `(agent, host, session, role, src.path, src.pos)`. Losing state may cause re-reads but never duplicates; keep it that way.
- Only conversational text becomes records; tool calls and results stay in `raw/`.
- Source stores are opened read-only (OpenCode database included). Never write to an agent's own chat files.
- Adding a collector: pure `parse_<agent>` in `src/collectors.rs`, a `collect_<agent>` using `drive_jsonl` or a cursor plus `store.emit`, register it in `pass()` in `src/main.rs` and in `AGENTS` in `src/core.rs`, add a fixture and a unit test in the `tests` module of `src/main.rs`.
- The Gemini protobuf field numbers are inferred, not from a schema. Mark any change there UNVERIFIED unless checked against real data.

## Safety and approvals

- Never commit archive data: no `records/`, `raw/`, `latest/` or `state/` content, no real chat transcripts, no copies of agent session files or databases.
- Test fixtures must be small cuts from real samples with all secrets and private data removed: no tokens, API keys, passwords, ssh keys or config, host names or IPs, home-directory paths, names of people, or private conversation content. When in doubt, write a synthetic line in the same format.
- No live database samples in the repository; the OpenCode test builds its own small database.
- Raw chat never goes into Cortex. Cortex gets only short digests plus a pointer to the archive.
- Do not add network access beyond the existing read-only MacBook pull.
- Work on a branch and merge through a pull request.

## Further reading

- `README.md`: layout, record schema, sources and parser status.
- `.crumb`: repository metadata (layer `tooling`).
