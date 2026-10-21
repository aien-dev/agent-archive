#!/bin/bash
# Label prepared batches with a model, 6 at a time; skips batches that already have output. Usage: model_label.sh TRACES_DIR [MODEL]
set -u
td="${1:-$HOME/handoffs/wispr/traces}"; model="${2:-sonnet}"
one() {
  b="$1"; o="$td/model/out/$(basename "$b")"
  [ -s "$o" ] && return 0
  { cat "$td/model/prompt.txt"; cat "$b"; } | timeout 600 claude -p --model "$model" --output-format text --max-turns 1 --tools "" > "$o.tmp" 2>"$o.err" && mv "$o.tmp" "$o"
}
export -f one; export td model
ls "$td"/model/batches/*.jsonl | xargs -P 6 -I{} bash -c 'one {}'
python3 "$(dirname "$0")/model_labels.py" merge "$td"
