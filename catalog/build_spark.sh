#!/bin/bash
# Rebuild spark.db from every chat store on the Spark (live + archives), then merge with the Mac catalog.
# CPU only, lowest priority. Usage: build_spark.sh [OUTDIR]
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$HOME/handoffs/wispr/catalog}"
export CATALOG_CLAUDE_ROOTS="$HOME/archive/agent-chats/raw/claude/spark=spark:$HOME/chat-transcripts-archive/spark-backup-20261004-sessions=spark:$HOME/chat-transcripts-archive/.claude__projects/projects=mac"
export CATALOG_RECORDS="$HOME/archive/agent-chats/records"
export CATALOG_T3="$HOME/.t3/userdata/statev2.sqlite"
export CATALOG_CODEX_ROOTS="$HOME/.codex/sessions:$HOME/archive/agent-chats/raw/codex/spark:$HOME/chat-transcripts-archive/.codex__sessions/sessions"
export CATALOG_RECORD_AGENTS="gemini,opencode,muse"
export CATALOG_GROK="$HOME/.grok/sessions"
nice -n 19 ionice -c3 python3 "$here/build_catalog.py" "$out/spark.db.tmp" spark
mv "$out/spark.db.tmp" "$out/spark.db"
nice -n 19 ionice -c3 python3 "$here/merge.py" "$out/all.db" "$out/mac.db" "$out/spark.db"
