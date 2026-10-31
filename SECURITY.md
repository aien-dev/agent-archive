# Security policy

## Reporting a vulnerability

Report privately. Do not open a public issue, pull request or discussion about it.

1. GitHub: open this repository's Security tab and choose "Report a vulnerability". This
   creates a private advisory that only the maintainers can see. If that option is not shown,
   private vulnerability reporting has not been enabled for this repository yet; use email.
2. Email: drake@aienos.com with the subject `[agent-archive] security report`.

Include the commit or release you tested, the component (path), steps to reproduce, and what an
attacker gains. If the report involves a secret, say where it was exposed and do not send the
secret itself. Encrypted mail is not required; do not post proof-of-concept payloads publicly.

## Response targets

This project has one maintainer; the targets below are best effort and stated honestly.

- Acknowledgement: within 3 working days.
- Triage and severity: within 7 days of acknowledgement.
- Fix or mitigation for confirmed issues: aim for 30 days for high and critical, 90 days
  otherwise, with a status update at least every 14 days until closed.
- Disclosure: coordinated. An advisory is published with credit (unless you ask otherwise)
  once the fix is on the default branch. Default embargo 90 days, negotiable.

## Scope

`agent-archive` is a Rust binary that collects AI agent chat stores on one host into an archive of
JSONL records plus a verbatim raw mirror. The archive data lives outside the repository.

In scope:

- Parsers in `src/collectors.rs` and `src/pb.rs` that panic, loop, or write outside the archive
  root on malformed chat files, SQLite databases or protobuf blobs.
- The private-file checks in `src/core.rs`: any path where a symlink, a foreign owner, or group or
  other access is not refused, or where a file is created with a mode wider than `0600` (or `0700`
  for directories), as README states.
- The read-only ssh and scp pull from a second machine (`pull_macbook` in `src/collectors.rs`):
  remote listing output that can steer a write outside `raw/gemini/macbook/`, or a remote path that
  is interpreted by a shell.
- A write to an agent's own source chat files or database, which AGENTS.md says are opened
  read-only.
- Archive data, real transcripts, tokens, host names or home paths committed to the repository,
  including inside `tests/fixtures/`.

Out of scope:

- The agents whose chat stores are read, OpenSSH, SQLite (bundled via `rusqlite`), and the host
  operating system.

## Supported versions

Only the default branch `main`; there is no release (Cargo version `0.1.0`). Reports against older commits are welcome but fixes land on the default branch only.

## Status

README: the collector is in use; this repository documents the source behaviour and does not
establish which binary or on-disk permissions are currently deployed. The Gemini protobuf format is
UNVERIFIED (field numbers inferred from sampling). No certification, no bug bounty. See AUDIT_SCOPE.md for what to review and how to build and test it.
