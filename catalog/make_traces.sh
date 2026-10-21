#!/bin/bash
# One command rebuild of the private Drake-voice traces (CPU only, lowest priority):
#   catalog (Spark stores + Mac catalog) -> Wispr match -> traces + pairs + stats
# Usage: make_traces.sh [WISPR_DIR]   (default ~/handoffs/wispr; needs WISPR_DIR/history.jsonl and WISPR_DIR/catalog/mac.db)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
w="${1:-$HOME/handoffs/wispr}"
"$here/build_spark.sh" "$w/catalog"
nice -n 19 ionice -c3 python3 "$here/wispr_match.py" "$w/catalog/all.db" "$w/history.jsonl"
nice -n 19 ionice -c3 python3 "$here/traces.py" "$w/catalog/all.db" "$w/history.jsonl" "$w/traces"
nice -n 19 ionice -c3 python3 "$here/stats.py" "$w/traces"
