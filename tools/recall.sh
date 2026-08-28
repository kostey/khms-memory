#!/usr/bin/env bash
# KHMS symptom recall: free-text search over card bodies (know/ + the fog archive).
#
# Usage: recall.sh <symptom / error string / identifier ...>
#        recall.sh checksum mismatch on the serial link
#
# WHY THIS EXISTS, and why it is not tag-based:
# precheck.sh needs tags, so using it requires first classifying the situation
# ("I am now debugging device X") — and that self-classification is exactly what
# fails when you are lost, which is when retrieval matters most. A symptom string
# — an error message, a parameter name, an odd number — is an artefact you always
# already have. Paste it here BEFORE root-causing anything, and again before you
# state a hypothesis or propose a change: a proposal is the moment when repeating
# a documented dead end is most expensive, and no hook can fire on a sentence you
# have not said yet.
#
# Zero model tokens. Every query is appended to tools/.recall.log.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: recall.sh <symptom/error/identifier ...>" >&2; exit 2; }
exec python3 "$(dirname "$(readlink -f "$0")")/khms_search.py" "$@"
