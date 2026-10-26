# Audit scope

Written for an outside, independent security audit. Revision described:
`e102eb6bf3434b0f510c595cbaad097c9e29a1ca` on `main`, 2026-10-26. Everything below is taken from the repository's own files at
that revision; items marked NOT_RUN were not executed while writing this document.

## What this repository is

A single Rust binary that collects AI agent chats (Claude Code, Codex, OpenCode, Gemini or
Antigravity, Muse) on one host into plain JSONL records plus a verbatim raw mirror of the source
files. The only network access is a read-only `ssh` and `scp` pull from a second machine reached
through an ssh host alias. The repository holds the collector (`src/main.rs` 492 lines,
`src/collectors.rs` 351, `src/core.rs` 370, `src/pb.rs` 86) and three small fixtures
(`tests/fixtures/{claude,codex,muse}.jsonl`). The archive itself is outside the repository. Status:
in use. Licence AGPL-3.0-or-later.

## What to review, in priority order

1. `src/core.rs` private-file layer: `private_check`, `private_metadata`, `private_dir`,
   `private_existing_dir`, `private_open`, `private_read_to_string`, `private_file`,
   `private_append`, `private_copy`, `atomic_write`. README: archive directories `0700`, files
   `0600`; symlinks, foreign owners and group or other access are refused on reads and writes.
   Check for time-of-check to time-of-use gaps between the metadata check and the open, and that
   every write path goes through these helpers. `libc` is used only for the effective user ID.
2. `pull_macbook` in `src/collectors.rs`. Runs `ssh` with `BatchMode=yes`, `ConnectTimeout=5`,
   and a ControlMaster socket at `/tmp/agent-archive-mac-%C`, executes a fixed remote `find ... stat`
   script, then parses each output line by splitting on spaces into four fields (subdirectory,
   path, size, mtime). The first field and the file name from the remote path are joined into the
   local destination under `raw/gemini/macbook/`, and the remote path is passed to
   `scp macbook:{path}`. Check: whether a hostile or compromised remote can choose a destination
   outside the mirror (for example a first field containing `..`), whether remote paths are
   interpreted by the remote shell during `scp`, and the use of a shared `/tmp` control socket path.
3. Parsers: `parse_<agent>` in `src/collectors.rs`; protobuf decoding `fields` in `src/pb.rs`
   (Gemini step blobs, field numbers inferred, not from a schema). Check bounds on varints and
   length-delimited fields and behaviour on truncated input.
4. SQLite access (OpenCode database, Gemini per-conversation `.db`): opened read-only per AGENTS.md.
   Check the open flags and that copies are made before parsing live files.
5. `Store` in `src/core.rs`: de-duplication on `(agent, host, session, role, src.path, src.pos)`,
   state cursors in `state/<agent>.json`, `latest/` views rewritten with temp file plus rename.
   `Record::date` slices `ts[..10]`; check that every `ts` reaching it is normalised.
6. `query` in `src/main.rs`: takes a user regex (`--grep`) over archive records; check regex
   resource limits.
7. `tests/fixtures/`: confirm no secrets, host names, home paths or private conversation content
   (AGENTS.md rule).

`.github/workflows/ci.yml` (added by the audit-readiness change) runs `cargo build --locked
--all-targets` and `cargo test --locked` on a GitHub-hosted runner and publishes an SPDX SBOM
artifact; actions pinned by commit sha, read-only token.

## Trust boundaries

- Untrusted input: the contents of agent chat stores (JSONL, SQLite, protobuf blobs), which hold
  model and user text; the remote listing and files returned by the ssh pull.
- Trusted: the local user account that owns the archive root, the local ssh configuration and its
  keys (not in the repository), OpenSSH and the bundled SQLite.
- Sensitive output: the archive holds full chat transcripts. README: raw chat never goes into
  Cortex; only short digests and a pointer.

## Threat model summary

- Assets: confidentiality of the archived transcripts; integrity of source chat stores.
- Attackers considered: another local user who tries to read the archive or swap a path with a
  symlink; malformed source files; the remote machine in the pull.
- Defended: private modes and ownership checks on every archive path, with refusal instead of
  silent fallback; read-only opens of source stores; `BatchMode=yes` ssh.
- Not defended / explicitly out of scope: a compromised local account that owns the archive;
  the protobuf format is inferred and may mis-parse; README notes Mac `-shm` files are not pulled,
  so uncommitted remote data may be missed. No encryption at rest is implemented.

## Build from source at a pin

Needs a Rust toolchain (version not pinned; no `rust-toolchain.toml`). `rusqlite` 0.32 builds its
bundled SQLite, so no system SQLite is needed (AGENTS.md). `Cargo.lock` is committed (63 packages).

```sh
cargo build --release
```

## Run every test

NOT_RUN while writing this document.

```sh
cargo test --release
```

The unit tests live in the `tests` module of `src/main.rs` (15 `#[test]` functions), including
parser tests on the fixtures, a synthetic Gemini step, an OpenCode database the test builds itself,
incremental passes and de-duplication after state loss, the capped `latest` window, private modes
under a permissive umask, and refusal of unsafe roots, descendants, state files, duplicate records,
history records, and `query` or `status` on unsafe paths. Expected: `test result: ok`.

`collect`, `once`, `status`, `query` and `debug-pb` read real chat stores and write the archive;
AGENTS.md says to run them only on the machine the archive belongs to. NOT_RUN on a plain host.
`once --no-mac` skips the ssh pull.

## Known limitations and NOT_RUN

- Gemini protobuf field numbers are UNVERIFIED (README).
- The ssh pull path has no unit test.
- Deployed binary and on-disk permissions are not established by this repository (README).
- Existing archives created before the permission checks need a manual repair (README gives
  `find` and `chmod` commands).

## Dependencies and provenance

- `Cargo.lock` present. Direct dependencies: `serde_json` 1, `rusqlite` 0.32 (bundled SQLite),
  `chrono` 0.4, `regex` 1, `libc` 0.2 (README: locked at 0.2.190).
- Fetched from crates.io at build time.
- At run time: `ssh` and `scp` for the optional pull.
- No GitHub Actions. No vendored code beyond the SQLite bundled by `rusqlite`.

## Evidence

No receipts are stored in this repository. Archive records are append-only by design (AGENTS.md:
"Records are append-only ... Losing state may cause re-reads but never duplicates").
