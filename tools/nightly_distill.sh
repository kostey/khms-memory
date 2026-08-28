#!/usr/bin/env bash
# KHMS nightly sweep driver:  deterministic inputs (0 tokens)
#                          -> extract candidates (cheap model)
#                          -> mechanical quote check (0 tokens)
#                          -> consolidate (mid model)
#                          -> memory/inbox/DATE.md
#
# Writes PROPOSALS only. It never touches memory/know/ and never assigns an id;
# that happens at review, by a human or a strong model (spec P6, §9.2).
#
# Configure with environment variables (all optional):
#   KHMS_ROOT           the base            (default: parent of this script's dir)
#   KHMS_AGENT          headless agent CLI  (default: claude)
#   KHMS_SWEEP_MODEL    extract stage model (default: sonnet)
#   KHMS_CONS_MODEL     consolidate model   (default: sonnet)
#   KHMS_TRANSCRIPTS    glob of session transcripts to digest
#                       (default: $HOME/.claude/projects/*/*.jsonl)
#   KHMS_GIT_REPOS      space-separated repo paths whose day's commits are an input
#   SWEEP_TIMEOUT / CONSOLIDATE_TIMEOUT   wall clock caps (default 90m / 100m)
#   NIGHTLY_DRYRUN=1    assemble every input, print the two INPUTS blocks, stop
#
# MODEL CHOICE IS EVIDENCE, NOT FASHION. In the reference deployment the cheapest
# tier produced structurally perfect cards with largely invented detail, and was
# banned from this stage; the strongest tier extracted ~4x more candidates than
# the mid tier on the same window but cost accordingly. Mid tier for both nightly
# stages is affordable only BECAUSE the grounding floor is mechanical: every
# journal MARK must yield a candidate or an explicit "already carded" note, and
# verify_quotes.py greps all grounding. Spend the strong model on the weekly and
# on review instead.
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
ROOT="${KHMS_ROOT:-$(dirname "$HERE")}"
AGENT="${KHMS_AGENT:-claude}"
SWEEP_MODEL="${KHMS_SWEEP_MODEL:-sonnet}"
CONS_MODEL="${KHMS_CONS_MODEL:-sonnet}"
TRANSCRIPTS="${KHMS_TRANSCRIPTS:-$HOME/.claude/projects/*/*.jsonl}"
SWEEP_TIMEOUT="${SWEEP_TIMEOUT:-90m}"
CONSOLIDATE_TIMEOUT="${CONSOLIDATE_TIMEOUT:-100m}"
DRYRUN="${NIGHTLY_DRYRUN:-0}"

STAGING="${NIGHTLY_STAGING:-$ROOT/memory/inbox/.staging}"
DATE="${NIGHTLY_DATE:-$(date +%F)}"
LOG="$ROOT/tools/nightly.log"
LOCK="$ROOT/tools/.nightly.lock"
STAMP="$ROOT/tools/.last-nightly"
mkdir -p "$STAGING"

# One run at a time. NOTE the timeouts below: a stage that hangs while holding
# this lock does not lose one night, it makes every following night exit
# "already running" without doing anything — a silent, cumulative failure.
exec 9>"$LOCK"
flock -n 9 || { echo "$(date -Is) nightly: already running" >> "$LOG"; exit 0; }

# Stamp the START, not the finish: the window this run covers ends when its
# inputs were collected, so recording the completion time silently drops
# everything that happened DURING the run — hours, on a slow night.
RUN_START=$(date -Is)
SINCE=$(cat "$STAMP" 2>/dev/null || echo "1970-01-01")
echo "$RUN_START nightly start (since $SINCE)" >> "$LOG"

# ---------------------------------------------------------- 1. free inputs
DIGEST="$STAGING/nightly-$DATE-digest.txt"
JSRC="$STAGING/nightly-$DATE-journals.md"
GITLOG="$STAGING/nightly-$DATE-git.txt"
SWEEP="$STAGING/nightly-$DATE-sweep.md"
QREPORT="$STAGING/nightly-$DATE-quotecheck.txt"
JOURNAL="$ROOT/journal/$DATE.md"
OUT="$ROOT/memory/inbox/$DATE.md"

# A glob that matches nothing must not kill the night under `set -e`: the journal
# and the git log are still worth distilling, and the empty digest has to be
# ANNOUNCED rather than inferred later from a thin inbox. Check KHMS_TRANSCRIPTS
# when you see this line — it is the most common misconfiguration.
# shellcheck disable=SC2086
if ! python3 "$ROOT/tools/preprocess_transcripts.py" --since "$SINCE" $TRANSCRIPTS \
     > "$DIGEST" 2>> "$LOG"; then
  echo "$(date -Is) WARNING: no transcripts matched '$TRANSCRIPTS' — digest EMPTY" >> "$LOG"
  : > "$DIGEST"
fi

# Journal source = every journal file TOUCHED since the last run, concatenated.
# Passing only today's file is an off-by-one that costs the whole point: at 03:30
# the day being distilled is yesterday, whose journal is complete, while today's
# is empty. Today's file stays as the day-summary APPEND TARGET only.
: > "$JSRC"
find "$ROOT/journal" -name '*.md' -newermt "$SINCE" -print0 2>/dev/null \
  | sort -z | while IFS= read -r -d '' f; do
      printf '\n===== %s =====\n' "${f##*/}" >> "$JSRC"
      cat "$f" >> "$JSRC"
    done

: > "$GITLOG"
for repo in ${KHMS_GIT_REPOS:-}; do
  [ -d "$repo/.git" ] || continue
  printf '\n===== %s =====\n' "$repo" >> "$GITLOG"
  git -C "$repo" log --since="$SINCE" --stat >> "$GITLOG" 2>/dev/null || true
done

# ------------------------------------------- 1b. make the inputs READABLE
# See digest_chunk.py: an oversized input is not "hard to read", it is read as a
# prefix, silently. Non-fatal — a chunker failure falls back to the monolith.
chunk_input() {                       # $1 file  $2 basename  -> echoes a prompt line
  local file="$1" base="$2" index="$STAGING/$2-chunks.txt"
  : > "$index"
  if [ -s "$file" ]; then
    python3 "$ROOT/tools/digest_chunk.py" "$file" "$STAGING" "$base" --index "$index" \
      >> "$LOG" 2>&1 || echo "$(date -Is) chunking $base FAILED (non-fatal)" >> "$LOG"
  fi
  if [ -s "$index" ]; then
    printf '%s, SPLIT INTO READABLE CHUNKS — READ EVERY ONE, IN ORDER:\n%s\n- %s, whole file — GREP ONLY, never read it whole: %s' \
      "$3" "$(sed 's|^|  * |' "$index")" "$3" "$file"
  elif [ -s "$file" ]; then
    printf '%s: %s' "$3" "$file"
  else
    printf '%s: %s — EMPTY for this window. Do not cite it.' "$3" "$file"
  fi
}

DIGEST_INPUT="$(chunk_input "$DIGEST" "nightly-$DATE-digest" "transcript digest (quote as src=digest)")"
GITLOG_INPUT="$(chunk_input "$GITLOG" "nightly-$DATE-git" "the day's git log (quote as src=gitlog)")"

SWEEP_INPUTS="INPUTS:
- $DIGEST_INPUT
- journal, every file touched since the last run (quote as src=journal): $JSRC
- $GITLOG_INPUT
OUTPUT FILE: $SWEEP"

CONS_INPUTS="INPUTS:
- sweep candidates: $SWEEP
- $DIGEST_INPUT
- journal (source of truth, grep it; quote as src=journal): $JSRC
- $GITLOG_INPUT
- today's journal (append the day summary here, nothing else): $JOURNAL
- quote-verification report: $QREPORT
OUTPUT FILE: $OUT"

if [ "$DRYRUN" = "1" ]; then
  echo "=== DRY RUN — inputs assembled in $STAGING, no model call ==="
  for f in "$DIGEST" "$JSRC" "$GITLOG"; do
    printf '%-70s %10d B\n' "$f" "$(stat -c%s "$f" 2>/dev/null || echo 0)"
  done
  printf '\n--- SWEEP INPUTS ---\n%s\n\n--- CONSOLIDATE INPUTS ---\n%s\n' \
    "$SWEEP_INPUTS" "$CONS_INPUTS"
  exit 0
fi

# ------------------------------------------------------------- 2. extract
# `Edit` in the tool list is LOAD-BEARING: a headless run has nobody to grant a
# permission prompt, so without it the stage's only writable path is whole-file
# Write — and a stage that can only rewrite re-emits its entire output on every
# append until it dies on its own timeout. Appending loses at most one card.
TOOLS="Read Write Edit Glob Grep"
rc=0
timeout --signal=TERM --kill-after=60s "$SWEEP_TIMEOUT" \
  "$AGENT" -p "$(cat "$ROOT/tools/prompts/nightly-sweep.md")

$SWEEP_INPUTS" --model "$SWEEP_MODEL" --allowedTools $TOOLS >> "$LOG" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || {
  echo "$(date -Is) sweep FAILED (rc=$rc$([ "$rc" -eq 124 ] && echo " = TIMEOUT after $SWEEP_TIMEOUT"))" >> "$LOG"
  exit 1; }

# 2a. Reassembly: on a big day the stage splits its own output into part files.
# It has no shell, so it cannot merge them; the driver does. Cards already present
# by label are skipped, so a part merged by hand cannot be duplicated.
shopt -s nullglob
PARTS=("$STAGING/nightly-$DATE-sweep-part"*.md "$STAGING/nightly-$DATE-sweep-final.md")
shopt -u nullglob
if [ ${#PARTS[@]} -gt 0 ]; then
  python3 - "$SWEEP" "${PARTS[@]}" <<'PYMERGE' >> "$LOG" 2>&1 || echo "$(date -Is) reassembly FAILED (non-fatal)" >> "$LOG"
import pathlib
import re
import sys
target, parts = pathlib.Path(sys.argv[1]), [pathlib.Path(p) for p in sys.argv[2:]]
CARD = re.compile(r"^#{2,3} +([A-Z]\d+[a-z]?)\b", re.M)
text = target.read_text(encoding="utf-8") if target.exists() else ""
have, added = set(CARD.findall(text)), 0
for p in sorted(parts):
    if not p.exists():
        continue
    t = p.read_text(encoding="utf-8")
    marks = list(CARD.finditer(t))
    for i, m in enumerate(marks):
        if m.group(1) in have:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        text += ("\n" if text and not text.endswith("\n") else "") + t[m.start():end]
        have.add(m.group(1)); added += 1
if added:
    target.write_text(text, encoding="utf-8")
print(f"reassembly: +{added} cards; sweep now holds {len(have)} candidates")
PYMERGE
fi

# ------------------------------------------- 2b. mechanical grounding check
# NOT a gate: a sweep with some ungrounded cards is still worth consolidating,
# and the next stage is instructed to strip exactly what this report names.
# `|| true` is load-bearing under `set -e` — verify_quotes.py exits 1 on any
# finding, and without it the run dies here silently, before the inbox exists.
python3 "$ROOT/tools/verify_quotes.py" "$SWEEP" \
  --source digest="$DIGEST" --source journal="$JSRC" --source gitlog="$GITLOG" \
  > "$QREPORT" 2>&1 || true
echo "$(date -Is) quotecheck: $(tail -1 "$QREPORT")" >> "$LOG"

# --------------------------------------------------------- 3. consolidate
# The consolidate stage is handed THE SAME SOURCES the extract stage had. Until
# it was, its own instruction to check "is this covered by its cited source?" was
# unanswerable by construction — a check whose passing condition held whether or
# not the thing it guarded was healthy.
rc=0
timeout --signal=TERM --kill-after=60s "$CONSOLIDATE_TIMEOUT" \
  "$AGENT" -p "$(cat "$ROOT/tools/prompts/nightly-consolidate.md")

$CONS_INPUTS" --model "$CONS_MODEL" --allowedTools $TOOLS >> "$LOG" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || {
  echo "$(date -Is) consolidate FAILED (rc=$rc$([ "$rc" -eq 124 ] && echo " = TIMEOUT after $CONSOLIDATE_TIMEOUT"))" >> "$LOG"
  exit 1; }

# Re-verify the OUTPUT: a card that lost its grounding on the way through gets
# caught again, and the report is an input to the morning review.
python3 "$ROOT/tools/verify_quotes.py" "$OUT" \
  --source digest="$DIGEST" --source journal="$JSRC" --source gitlog="$GITLOG" \
  > "${OUT%.md}-quotecheck.txt" 2>&1 || true

echo "$RUN_START" > "$STAMP"
echo "$(date -Is) nightly done -> $OUT" >> "$LOG"
