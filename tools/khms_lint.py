#!/usr/bin/env python3
"""Card lint — the checks that must hold BEFORE a card enters know/.

One rule today, and it exists because of a measured failure in the source
deployment:

  A card whose body says it CORRECTS something must carry a graph edge naming
  what it corrects.

A July measurement was carded, correctly, as two `active` cards.  In August the
operator corrected it in person and the correction was carded too — opening with
the word CORRECTED, and with `links: {contradicts: [], supersedes: null}`,
because nothing at approval time asked for more.  Four days later the July cards
were served to the operator as the current state of the machine.  Nothing
malfunctioned: retrieval travels by edges, and there was no edge.  The
correcting card even carried the lesson as prose ("cards must be read with their
date") — which is the point: a lesson written into the very card that failed is
not a mechanism.  The mechanism is this refusal plus the reverse injection in
the hook (build_injection).

The vocabulary is the spec's (§4.10): derived_from / supports / contradicts /
supersedes / refuted_by.  There is no `refines` link; a correction uses
`contradicts` (mutual conflict, symmetric) or `supersedes` (replacement, body
must then read SUPERSEDES K-x BECAUSE …).

Escape hatch, deliberately visible in the record (a measure is a step that can
be seen to have been taken): a card that genuinely corrects something never
carded writes

    NO-CORRECTION-TARGET: <why nothing in the base can be pointed at>

in its body.  That is a statement in an immutable card, not a flag in a script.
"""
import os
import re
import sys
import unicodedata

# The spec's link vocabulary that can express "this card corrects that one".
BACK_EDGES = ("contradicts", "supersedes", "refuted_by")

ESCAPE_RE = re.compile(r"^\s*NO-CORRECTION-TARGET:\s*\S", re.M)
CARD_ID_RE = re.compile(r"\bK-\d{5}\b")

# Correction language, in two tiers, because the word list alone does not
# separate "this card corrects the record" from "we fixed a bolt".  Measured on
# the source deployment's 2187-card base: a flat word list fires on 476 cards
# (22 % of everything, most of them on the operator-language stem for "repair").
# A lint that fires on a fifth of the base gets switched off, and a switched-off
# lint is worse than none, so:
#
#   BLOCK — the KHMS body markers, upper-case, as the spec and the card corpus
#           actually write them (§4.2: "SUPERSEDES K-x BECAUSE"; a correction
#           card opens with "CORRECTED (operator: …)").  Unambiguous.
#   CONTEXT — everything else, only on a line that ALSO talks about the record
#           (a card id, "card", "knowledge base", "claim", and the operator
#           language's equivalents).  "We repaired the cable" does not fire;
#           "correction to card K-NNNNN" does.
#
# Same list of words the rule names, anchored so each means what the rule means.
# Calibrated together: 41 of 2187 cards (1.9 %), the card of the incident among
# them.  The non-English half is the source deployment's operator language
# (Czech) — replace those rows with your own and re-calibrate the same way:
# count the hits over your base before you trust the rule.
BLOCK_PATTERNS = (
    ("CORRECTED", re.compile(r"\bCORRECT(ED|ION|IONS|S)\b")),
    # ACTIVE voice only: "SUPERSEDES K-x" is the replacing card speaking. The
    # passive "SUPERSEDED 2026-03-04 by …" is the OLD card annotating itself,
    # which is a different (and legitimate) shape — it must not be refused.
    ("SUPERSEDES", re.compile(r"\bSUPERSED(ES|ING)\b")),
    ("OPRAVA/KOREKCE", re.compile(r"\bKOREKCE\b|\bOPRAVA\b")),
)
CONTEXT_PATTERNS = (
    ("contrary to", re.compile(r"\bcontrary to\b")),
    ("was wrong", re.compile(r"\b(was|were|is|are) wrong\b")),
    ("no longer true", re.compile(r"\bno longer (true|holds|valid|the case)\b")),
    ("corrected", re.compile(r"\bcorrect(ed|ion|ions|s)\b")),
    ("supersedes", re.compile(r"\bsupersed(es|ing)\b")),
    ("oprava", re.compile(r"\boprav(a|y|u|e|ou|uje|ujeme|eno|ena|il|ili|it)\b")),
    ("korekce", re.compile(r"\bkorekc[ei]\b")),
    ("uz neplati", re.compile(r"\b(uz )?neplat[ií]\b")),
    ("vyvraci", re.compile(r"\bvyvra(ci|til|tila|ceno|tit)\b")),
)
# "this line is about the record itself", diacritics already stripped.
RECORD_CTX = re.compile(
    r"\bK-\d{5}\b|\bkart|\bcard\b|\bbaze\b|\bzaznam|\bknowledge base\b|\btvrzen")


def norm(s):
    """Same normalisation khms_search uses: NFKD, drop combining marks, lower."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def correction_terms(body):
    """Which correction phrases the body uses, in the order they are listed."""
    body = body or ""
    terms = [name for name, rx in BLOCK_PATTERNS if rx.search(body)]
    for line in body.splitlines():
        nl = norm(line)
        if not RECORD_CTX.search(nl):
            continue
        for name, rx in CONTEXT_PATTERNS:
            if rx.search(nl) and name not in terms:
                terms.append(name)
    return terms


def backward_targets(meta_or_links):
    """Card ids this card points at with a correction edge.

    Accepts either a `links` dict or a whole card `meta`. A stray TOP-LEVEL
    `contradicts:` counts here: approve_inbox nests it a few lines later, and
    refusing a card for an edge it does have, merely written one level too high,
    would be the wrong refusal.
    """
    d = meta_or_links or {}
    links = d.get("links") if isinstance(d.get("links"), dict) else {}
    out = []
    for k in BACK_EDGES:
        for src in (links, d):
            v = src.get(k)
            if not v:
                continue
            for item in (v if isinstance(v, list) else [v]):
                item = str(item).strip()
                if item and item.lower() not in ("null", "none", "~") and item not in out:
                    out.append(item)
    return out


def known_card_ids(know_dir):
    try:
        return {f[:-3] for f in os.listdir(know_dir)
                if f.startswith("K-") and f.endswith(".md")}
    except OSError:
        return set()


def check_correction_edges(card_id, meta, body, known_ids=None):
    """None when the card is fine, else the refusal message (one string).

    `known_ids` — ids the edge may point at (know/ plus the ids being approved in
    this same batch).  None disables the existence half of the check.
    """
    terms = correction_terms(body)
    if not terms:
        return None
    if ESCAPE_RE.search(body or ""):
        return None
    targets = backward_targets(meta)
    quoted = ", ".join(f"'{t}'" for t in terms[:4])
    if not targets:
        return (
            f"{card_id}: the body uses correction language ({quoted}) but carries no "
            f"backward edge. A card that corrects another card MUST name it: add "
            f"links.contradicts: [K-xxxxx] (mutual conflict) or links.supersedes: "
            f"K-xxxxx (replacement — body must then contain 'SUPERSEDES K-xxxxx "
            f"BECAUSE: …'), or links.refuted_by: [K-xxxxx]. Without that edge the "
            f"corrected card keeps being served alone, as current, and the "
            f"correction is never reached. "
            f"If nothing in the base can be pointed at, say so in the body: "
            f"'NO-CORRECTION-TARGET: <reason>'.")
    if known_ids is not None:
        missing = [t for t in targets if t not in known_ids]
        if missing:
            return (
                f"{card_id}: correction language ({quoted}) with a backward edge that "
                f"points at nothing: {', '.join(missing)} not in know/ and not in this "
                f"batch. Fix the id or point the edge at a card that exists.")
    return None


def lint_batch(cards, known_ids=None):
    """cards: [(id, meta, body)] → [refusal message]."""
    problems = []
    for cid, meta, body in cards:
        msg = check_correction_edges(cid, meta, body, known_ids)
        if msg:
            problems.append(msg)
    return problems


def _main(argv):
    """CLI: khms_lint.py <know-dir-or-card.md ...> — audit cards already on disk."""
    import glob
    import yaml
    paths = []
    for a in argv or []:
        paths += sorted(glob.glob(os.path.join(a, "K-*.md"))) if os.path.isdir(a) else [a]
    if not paths:
        print(__doc__)
        return 2
    known = {os.path.basename(p)[:-3] for p in paths}
    # An edge may legitimately point into the fog: superseded and condensed cards
    # move to memory/archive/know/. Counting those as "points at nothing" turned
    # a first audit run into 37 false alarms.
    for p in list(paths):
        arch = os.path.join(os.path.dirname(os.path.dirname(p)), "archive", "know")
        if os.path.isdir(arch):
            known |= {f[:-3] for f in os.listdir(arch) if f.endswith(".md")}
            break
    bad = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        msg = check_correction_edges(os.path.basename(p)[:-3], meta, m.group(2), known)
        if msg:
            bad += 1
            print(msg)
    print(f"khms_lint: {bad} card(s) with unlinked correction language / {len(paths)} checked")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
