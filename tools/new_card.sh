#!/usr/bin/env bash
# Print the next card id(s) and advance the counter. Usage: new_card.sh [n]
# The counter is the single writer of ids (spec §4.1); ids are never reused, so
# the range is committed BEFORE anything is printed — a reader that dies mid-pipe
# must not be able to hand out the same id twice.
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
ROOT="${KHMS_ROOT:-$(dirname "$HERE")}"
C="$ROOT/tools/.next_id"
N="${1:-1}"
CUR=$(cat "$C")
echo "$((CUR + N))" > "$C"
for ((i=0; i<N; i++)); do
  printf 'K-%05d\n' "$((CUR + i))"
done
