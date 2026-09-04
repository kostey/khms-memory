#!/usr/bin/env bash
# KHMS weekly synthesis driver: card-header digest (0 tokens) -> synthesis
# proposals (strong model) -> mechanical quote check -> memory/inbox/DATE-weekly.md
#
# This stage reasons over EXISTING cards, so its failure mode is misattribution —
# crediting a rule to the wrong card — which is as checkable as any other quote:
# verify_quotes.py resolves a src=K-xxxxx label to that card's own file. It gets
# the strong model for the same reason: finding the pattern that three scattered
# observations share is the one job here that a cheaper tier does badly.
#
# Writes proposals only; never touches memory/know/. Spec §9.4.
#
#   KHMS_ROOT / KHMS_AGENT / KHMS_WEEKLY_MODEL (default: opus) / WEEKLY_TIMEOUT
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
ROOT="${KHMS_ROOT:-$(dirname "$HERE")}"
AGENT="${KHMS_AGENT:-claude}"
MODEL="${KHMS_WEEKLY_MODEL:-opus}"
TIMEOUT="${WEEKLY_TIMEOUT:-90m}"
LOG="${WEEKLY_LOG:-$ROOT/tools/nightly.log}"
STAGING="$ROOT/memory/inbox/.staging"
# The same review-capacity cap the nightly uses, smaller: a weekly proposes patterns
# over the whole base, and a review that cannot read them all approves them all.
WEEKLY_MAX_CARDS="${WEEKLY_MAX_CARDS:-10}"
DATE=$(date +%F)
mkdir -p "$STAGING"

echo "$(date -Is) weekly start" >> "$LOG"

# 0-token headers digest: one line per card. This is the input that lets a model
# see the whole base at once without reading it.
HDR="$STAGING/weekly-$DATE-headers.txt"
KHMS_ROOT="$ROOT" python3 - > "$HDR" <<'EOF'
import glob
import os
import re

import yaml

root = os.environ["KHMS_ROOT"]
for p in sorted(glob.glob(os.path.join(root, "memory", "know", "K-*.md"))):
    with open(p, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        continue
    meta = yaml.safe_load(m.group(1))
    body = m.group(2).strip().splitlines()
    title = body[0][:100] if body else ""
    print(f"{meta['id']}|{meta['type']}|{meta['level']}|{meta['status']}|"
          f"{','.join(meta.get('tags') or [])}|{meta.get('scope', '')}|{title}")
EOF

# The week's inboxes, concatenated into ONE file — because it is also handed to
# the verifier. A stage may cite exactly the sources the checker holds: hand a
# stage an input without handing the checker the same input and every quote from
# it comes back "unknown source", for a source the stage was told to use.
INBOXES=$(ls "$ROOT"/memory/inbox/2*.md 2>/dev/null | tail -7 | tr '\n' ' ' || true)
INBOXCAT="$STAGING/weekly-$DATE-inboxes.md"
: > "$INBOXCAT"
for f in $INBOXES; do
  printf '\n===== %s =====\n' "${f##*/}" >> "$INBOXCAT"
  cat "$f" >> "$INBOXCAT"
done

# Same readability caps as the nightly: the headers digest is this stage's
# PRIMARY input and grows with the base until it stops being readable whole.
HDR_INDEX="$STAGING/weekly-$DATE-headers-chunks.txt"
: > "$HDR_INDEX"
python3 "$ROOT/tools/digest_chunk.py" "$HDR" "$STAGING" "weekly-$DATE-headers" \
  --index "$HDR_INDEX" >> "$LOG" 2>&1 \
  || echo "$(date -Is) headers chunking FAILED (non-fatal)" >> "$LOG"
if [ -s "$HDR_INDEX" ]; then
  HDR_INPUT="card headers digest, SPLIT INTO READABLE CHUNKS — READ EVERY ONE, IN ORDER
$(sed 's|^|  * |' "$HDR_INDEX")
- card headers digest, whole file — GREP ONLY: $HDR"
else
  HDR_INPUT="card headers digest (quote as src=headers): $HDR"
fi

OUT="$ROOT/memory/inbox/$DATE-weekly.md"
rc=0
timeout --signal=TERM --kill-after=60s "$TIMEOUT" \
  "$AGENT" -p "$(cat "$ROOT/tools/prompts/weekly-synthesis.md")

INPUTS:
- $HDR_INPUT
- condense candidates (ranked): $ROOT/memory/views/condense-candidates.md
- this week's inbox files, concatenated (quote as src=inbox): $INBOXCAT
- this week's inbox files, individually: ${INBOXES:-none}
MAX CARDS: $WEEKLY_MAX_CARDS
OUTPUT FILE: $OUT" --model "$MODEL" --allowedTools Read Write Edit Glob Grep >> "$LOG" 2>&1 || rc=$?
[ "$rc" -eq 0 ] || { echo "$(date -Is) weekly FAILED (rc=$rc)" >> "$LOG"; exit 1; }

# `|| true` load-bearing under `set -e`, as in the nightly: the report is an input
# to review, not a gate.
python3 "$ROOT/tools/verify_quotes.py" "$OUT" \
  --source headers="$HDR" --source inbox="$INBOXCAT" \
  > "${OUT%.md}-quotecheck.txt" 2>&1 || true
echo "$(date -Is) weekly quotecheck: $(tail -1 "${OUT%.md}-quotecheck.txt")" >> "$LOG"

# --------------------------------- merge pressure, the nightly's gate applied here
# Until this existed, the weekly wrote proposals that named no existing card at all —
# a stage whose whole subject is the EXISTING base, proposing into it blind.
#
# ONE ASYMMETRY WITH THE NIGHTLY, DELIBERATE. The nightly annotates its SWEEP file
# first, so its consolidate stage can SEE the NEAREST ids while it writes; the weekly
# has no intermediate candidate file, so the annotation can only run afterwards — and
# it runs WITHOUT `--only-missing`, i.e. it always recomputes. `--only-missing` exists
# to protect a line carried through from a sweep; here there is no such line, and the
# only thing it could protect is a NEAREST line the model wrote about ITSELF, which is
# precisely the evidence its own `RELATION: new` would then be judged against.
#
# NON-FATAL throughout. This script runs under `set -euo pipefail` and
# verify_relations.py exits 1 whenever it drops anything, so an unguarded call would
# kill the weekly for doing its job. Hence the `if` context and the `|| true`s. The
# gate is SKIPPED if the annotation fails: a RELATION check with no NEAREST lines drops
# every `new` candidate for the TOOL's reason instead of the candidate's.
WRREPORT="$STAGING/weekly-$DATE-relations.txt"
echo "$(date -Is) weekly === nearest === annotating $OUT" >> "$LOG"
if python3 "$ROOT/tools/nearest_cards.py" "$OUT" >> "$LOG" 2>&1; then
  echo "$(date -Is) weekly === verify_relations === gate over $OUT (max $WEEKLY_MAX_CARDS)" >> "$LOG"
  python3 "$ROOT/tools/verify_relations.py" "$OUT" \
    --max "$WEEKLY_MAX_CARDS" > "$WRREPORT" 2>&1 || true   # a report, not a gate
  echo "$(date -Is) weekly === verify_relations === $(tail -1 "$WRREPORT")" >> "$LOG"
  grep -E '^!! MASS DROP' "$WRREPORT" >> "$LOG" || true
  grep -E '^[A-Z][0-9]+: (DROP|DEFER|FLAG)' "$WRREPORT" >> "$LOG" || true
else
  echo "$(date -Is) weekly === verify_relations === SKIPPED — nearest FAILED, and a RELATION gate without NEAREST lines would drop candidates for the tool's own reason" >> "$LOG"
fi

# Completion stamp. Alert when it goes stale: a weekly that died must not be able
# to hide behind "no proposal file, so nothing is pending".
date -Is > "$ROOT/tools/.last-weekly"
echo "$(date -Is) weekly done -> $OUT" >> "$LOG"
