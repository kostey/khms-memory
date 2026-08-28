#!/usr/bin/env bash
# Pre-action check: print active policies, gotchas and refuted/challenged cards for given tags.
#
# Usage: precheck.sh <tag> [tag...]   — a duty before risky actions (deletions,
#        configuration changes, deployments, hardware writes). Zero model tokens.
#
# Every invocation is logged to tools/.precheck.log. That log is the point: the
# rule "check before you act" fails by not firing, and a count of firings against
# the day's risky actions makes the omission visible instead of leaving it to the
# judgement of the moment. khms_hook.py also runs this automatically for a named
# class of dangerous commands — a backstop, not a replacement for the habit.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: precheck.sh <tag> [tag...]" >&2; exit 2; }
HERE="$(dirname "$(readlink -f "$0")")"
ROOT="${KHMS_ROOT:-$(dirname "$HERE")}"

echo "$(date -Is) $*" >> "$ROOT/tools/.precheck.log" 2>/dev/null || true

KHMS_ROOT="$ROOT" python3 - "$@" <<'EOF'
import glob
import os
import re
import sys

import yaml

root = os.environ["KHMS_ROOT"]
tags = set(sys.argv[1:])
hits = []
for path in sorted(glob.glob(os.path.join(root, "memory", "know", "K-*.md"))):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        continue
    meta = yaml.safe_load(m.group(1))
    body = m.group(2).strip()
    if not (set(meta.get("tags") or []) & tags):
        continue
    warn = None
    if meta.get("status") in ("refuted", "challenged"):
        warn = f"!! {meta['status'].upper()}"          # a dead end, kept on purpose
    elif "gotcha" in (meta.get("tags") or []) and meta.get("status") == "active":
        warn = "!  GOTCHA"
    elif meta.get("type") == "policy" and meta.get("status") == "active":
        warn = "=  POLICY"
    if warn:
        first = body.splitlines()[0] if body else ""
        hits.append(f"{warn} {meta['id']}: {first}")
        for line in body.splitlines()[1:4]:
            hits.append(f"      {line}")
print("\n".join(hits) if hits
      else f"precheck: nothing on record for tags: {', '.join(sorted(tags))}")
EOF
