#!/usr/bin/env bash
# KHMS symptom recall: free-text search over card bodies (know/ + the fog archive).
#
# Usage: recall.sh [--lexical] <symptom / error string / identifier ...>
#        recall.sh checksum mismatch on the serial link
#        recall.sh --lexical <...>      # opt OUT of the dense channel
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
# THE DEFAULT IS HYBRID (lexical + dense), because the opt-in was the defect: a
# base written in English and queried in another language needs the cross-lingual
# channel, and the channel that is off by default is the channel that is not there
# when it is needed. Everything runs through khms_recall_hybrid.py, which uses the
# dense channel unless you opt out or the embedding daemon does not answer inside
# KHMS_DENSE_TIMEOUT_MS (150 ms) — and which NAMES the channel that answered, in
# its first line and in tools/.recall.log (`src=cli:hybrid:<model>`,
# `src=cli:lexical:fallback`, `src=cli:lexical:optout`), so a silent fall-back is
# countable instead of invisible. With no daemon installed you get the lexical
# behaviour and one line saying so.
#
# Zero model tokens. Every query is appended to tools/.recall.log.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: recall.sh [--lexical] <symptom/error/identifier ...>" >&2; exit 2; }
exec python3 "$(dirname "$(readlink -f "$0")")/khms_recall_hybrid.py" "$@"
