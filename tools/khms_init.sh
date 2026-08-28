#!/usr/bin/env bash
# Create a KHMS base and install the tooling into it. Idempotent.
#
# Usage: khms_init.sh [/path/to/base]      (default: $KHMS_ROOT, else ~/.agent-memory)
#
# Writes NOTHING into memory/know/ — cards only ever arrive through review.
set -euo pipefail

SRC="$(dirname "$(readlink -f "$0")")"          # this repo's tools/
ROOT="${1:-${KHMS_ROOT:-$HOME/.agent-memory}}"

mkdir -p "$ROOT"/{memory/know,memory/views/topics,memory/views/by-type,memory/inbox/.staging,memory/archive/know,journal,plans,archive,tools/prompts}

[ -f "$ROOT/tools/.next_id" ] || echo 1 > "$ROOT/tools/.next_id"

if [ ! -f "$ROOT/memory/views/tags.md" ]; then
  cat > "$ROOT/memory/views/tags.md" <<'EOF'
# Tag registry

| tag | group | description | aliases | cards |
|---|---|---|---|---|
| core | meta | always-loaded card, listed in MEMORY.md §2 | - | 0 |
| khms | meta | the memory system itself | memory | 0 |
| gotcha | meta | a trap: surfaced by precheck.sh, protected from condensation | - | 0 |
EOF
fi

# Copy the tooling. Existing files are overwritten (this is how you upgrade);
# your data — cards, journal, inbox, logs, counter — is never touched.
cp "$SRC"/*.py "$SRC"/*.sh "$ROOT/tools/"
cp "$SRC"/prompts/*.md "$ROOT/tools/prompts/" 2>/dev/null || true
chmod +x "$ROOT"/tools/*.sh "$ROOT"/tools/*.py

if [ ! -f "$ROOT/journal/$(date +%F).md" ]; then
  printf '# %s\n\n' "$(date +%F)" > "$ROOT/journal/$(date +%F).md"
fi

cat <<EOF
KHMS base ready: $ROOT

Next:
  1. export KHMS_ROOT="$ROOT"          (add it to your shell profile)
  2. write the two bootstrap cards — spec/khms-spec.md and spec/bootstrap-digest.md
     from this repo — into $ROOT/memory/inbox/bootstrap.md as candidate cards,
     review them, then:
       $ROOT/tools/approve_inbox.py $ROOT/memory/inbox/bootstrap.md
       $ROOT/tools/build_views.py
  3. wire the hooks: see claude-code/README.md
EOF
