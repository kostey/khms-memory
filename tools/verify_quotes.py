#!/usr/bin/env python3
"""Mechanically verify that candidate cards are grounded in their sources.

WHY THIS EXISTS
---------------
A distillation stage once produced a set of structurally perfect cards whose
factual detail was largely invented: a verification that never ran, log strings
that appear nowhere, constants off by a factor of four, an inverted causal claim.
Every one of them was fluent, plausible and wrong — and they would have been
retrieved later and believed.

Asking a model not to fabricate is a request. This is a check: a card may only
state a specific value if it supplies a verbatim quote containing it, and that
quote must actually occur in the source file it names. Both conditions are greps.
No amount of fluent prose satisfies them without the source really saying so.

FORMAT EXPECTED IN CANDIDATE FILES
----------------------------------
Cards are separated by a heading whose label is a capital letter plus digits
(`## C1:` from the extract stage, `### C1` from the consolidate stage). Each card
ends with a quotes block:

    **QUOTES:**
    - src=journal :: MARK[solved] checksum errors were a loose ground
    - src=digest  :: sensor 3 reinitialised on attempt 5

`src=` names a key passed with --source NAME=PATH.

USAGE
    verify_quotes.py cards.md --source digest=/path/digest.txt \\
                              --source journal=/path/journals.md [--strict]

Exit 0 when every quote resolves; 1 when any quote is unverifiable (always), or —
with --strict — when any unsupported specific remains. The report is written to
stdout and is an INPUT to the next stage, which is instructed to act on it
mechanically: unfound quote → the claim goes; unbacked specific → the specific
goes; no quotes block → the card goes.
"""
import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P  # noqa: E402

# The temp-label prefix is ANY capital letter, not just C: a day too big for one
# pass gets chunked into A1.., B1.., C1.., and a checker hardcoded to C matches
# none of them — i.e. it is absent exactly on the days it matters most.
CARD_RE = re.compile(r"^#{2,3}\s+([A-Z]\d+[a-z]?)\b(.*)$", re.M)
QUOTE_RE = re.compile(r"^\s*-\s*src=([\w-]+)\s*::\s*(.+?)\s*$", re.M)

# Specifics a card may not state without a quote behind them.
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
# A leading slash only starts a path when it opens a token — matching mid-word
# turns every "this/that" shorthand in prose into a phantom file name.
IDENT_RE = re.compile(
    r"(?:(?<![\w.])/[\w/]+"
    r"|\w+\.(?:py|sh|c|cpp|hpp|yaml|yml|json|toml|ini|conf|service|timer|md)\b"
    r"|\w+_\w+(?:_\w+)*)")
# Lines that are metadata, not claims: frontmatter keys, the link line, the quote
# block. A specific that appears only in metadata is not a claim about the world.
SKIP_LINE_RE = re.compile(
    r"^\s*(id|type|level|status|tags|scope|evidence|source|date|links|derived_from|"
    r"supports|contradicts|refuted_by|supersedes)\s*:|^\s*-\s*src=|^\*\*QUOTES"
    r"|^\*\*LINKS|^\*\*UNGROUNDED", re.I)
# Trailing sections belong to the FILE, not to the last card. Without this cut the
# last card silently inherits every number in "## Dropped: 3 candidates" and gets
# reported as fabricating them — a checker that cries wolf gets ignored, which is
# the only way a checker like this actually fails.
TAIL_RE = re.compile(r"^##+\s+(flagged|dropped|suspected|notes|summary)\b", re.I | re.M)


def norm(s):
    """Whitespace and quote style are normalised; WORDING IS NOT. A quote may be
    re-wrapped by an editor without becoming unverifiable, but it may not be
    tidied up, paraphrased or retyped from memory."""
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("„", '"'), ("—", "-"), ("–", "-"), (" ", " ")):
        s = s.replace(a, b)
    # A decimal separator is a LANGUAGE difference, not a value difference: a source
    # written in one locale says 0,15 and a card written in another says 0.15, and
    # compared raw every translated number looked ungrounded against its own verbatim
    # quote — a whole false-positive class. Both sides pass through here, so the
    # normalisation is symmetric and hides no real mismatch.
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    # Markdown emphasis and code ticks are typography, not content: a source writes
    # **30 Hz** where the card quotes 30 Hz, and both are the same sentence. Same for
    # hyphen runs — an em dash normalises to "-" above while a git log spells the same
    # dash "--". Both produced dozens of false FAILs before they were normalised, and
    # every one of them was traced to genuinely present text.
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"-{2,}", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


# ------------------------------------------------------- quote classification
# Verbatim means verbatim — but a long passage often has to be shortened, and the
# honest way to shorten one is to MARK the omission. So a quote is graded rather
# than matched: EXACT, WRAPPED (the source only re-typeset it), ELIDED (a MARKED
# `...`, `…`, `[...]` whose every remaining segment is present, in order) or
# ABSENT. Only ABSENT is fatal. An UNMARKED splice is not an elision and is not a
# tidy-up: the checker sees a sentence the source does not contain and calls it
# ABSENT, which is the right answer, because a spliced quote asserts something
# nobody wrote.
MARK_RE = re.compile(r"\[([^\[\]]*)\]|\.{3,}|…")
# A bracketed span longer than this is not an editorial completion, it is a claim;
# it stays literal text and must be found in the source like anything else.
INSERT_MAX = 20
# An elision mark absorbs the punctuation used to weld the two halves together;
# that punctuation belongs to the card, not to either passage. Digits and letters
# are never stripped.
SEG_EDGE = " \t\"'/,;:|-.()[]{}<>*→«»"
# Floor under the elision rule. Segments this short carry no evidence — "a... b"
# would match nearly any source, which would turn the marked-elision allowance
# into a way to quote nothing at all.
MIN_SEG = 4
MIN_EVIDENCE = 24


class Source:
    """A source text kept in both forms: raw for EXACT, normalised for the rest."""

    __slots__ = ("raw", "normed")

    def __init__(self, text):
        self.raw = text
        self.normed = norm(text)


def split_marked(nq):
    """Split a NORMALISED quote at its elision/insertion marks.

    -> (segments, marked). `marked` says whether any mark was seen at all: an
    unmarked quote must not get the piecewise allowance, and that is exactly the
    difference between a compressed quote and a spliced one.
    """
    segs, last, marked = [], 0, False
    for m in MARK_RE.finditer(nq):
        if m.group(0).startswith("["):
            inner = m.group(1).strip()
            if not re.fullmatch(r"\.{2,}|…", inner) and len(inner) > INSERT_MAX:
                continue            # long bracketed span: content, not a mark
        segs.append(nq[last:m.start()])
        last = m.end()
        marked = True
    segs.append(nq[last:])
    return [t for t in (x.strip(SEG_EDGE) for x in segs) if t], marked


def find_pieces(segs, hay, tries=20):
    """Find every segment in `hay`, in order and without overlap.

    -> (ok, max_gap). The gap is reported because an elision that jumps tens of
    kilobytes has welded together two unrelated passages, and the reviewer should
    see that even when the match is technically valid. The first segment is retried
    at each of its occurrences (bounded), so a common opening phrase cannot make a
    valid quote look absent.
    """
    if not segs:
        return False, -1
    starts, i = [], hay.find(segs[0])
    while i >= 0 and len(starts) < tries:
        starts.append(i)
        i = hay.find(segs[0], i + 1)
    for s0 in starts:
        pos, gap, ok = s0 + len(segs[0]), 0, True
        for seg in segs[1:]:
            j = hay.find(seg, pos)
            if j < 0:
                ok = False
                break
            gap = max(gap, j - pos)
            pos = j + len(seg)
        if ok:
            return True, gap
    return False, -1


def classify(quote, source):
    """-> (class, detail, vouching_text). vouching_text is what this quote may
    vouch for in the unbacked-specifics scan; empty when ABSENT."""
    q = quote.strip()
    if not q:
        return "ABSENT", "empty quote", ""
    if q in source.raw:
        return "EXACT", "", q
    # Enclosing quotation marks are the card's punctuation, not the source's.
    nq = norm(q).strip(" \"'")
    if not nq:
        return "ABSENT", "empty quote", ""
    if nq in source.normed:
        return "WRAPPED", "", q
    segs, marked = split_marked(nq)
    if marked and segs:
        if min(len(x) for x in segs) < MIN_SEG or sum(len(x) for x in segs) < MIN_EVIDENCE:
            return "ABSENT", "elided segments too short to carry evidence", ""
        ok, gap = find_pieces(segs, source.normed)
        if ok:
            # The segments vouch, joined by a separator no specific can span: an
            # elision must not manufacture an adjacency the source never had.
            return ("ELIDED",
                    f"{len(segs)} segment{'s' if len(segs) != 1 else ''}, "
                    f"max gap {gap} chars",
                    " || ".join(segs))
    return "ABSENT", "", ""


def split_cards(text):
    marks = list(CARD_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.start():end]
        tail = TAIL_RE.search(body)
        if tail:
            body = body[:tail.start()]
        yield m.group(1), body


def specifics(body):
    """Numbers and identifiers stated in the card body, outside metadata lines."""
    out = set()
    for line in body.splitlines():
        if SKIP_LINE_RE.search(line) or line.startswith("#"):
            continue
        for m in NUMBER_RE.finditer(line):
            out.add(m.group(0))
        for m in IDENT_RE.finditer(line):
            out.add(m.group(0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cards")
    ap.add_argument("--source", action="append", default=[],
                    metavar="NAME=PATH", help="repeatable; NAME is what src= may say")
    ap.add_argument("--strict", action="store_true",
                    help="also exit 1 when a specific is backed by no quote")
    args = ap.parse_args()

    with open(args.cards, encoding="utf-8", errors="replace") as f:
        text = f.read()

    sources = {}
    # `src=K-NNNNN` resolves to that card automatically, so the weekly stage — which
    # reasons over existing cards rather than transcripts — is checkable with no
    # extra wiring. It has misattributed a card before ("card X says Y" credited to
    # the wrong id), and that claim is exactly as checkable as any other quote.
    for m in re.finditer(r"src=(K-\d{5})\b", text):
        cid = m.group(1)
        if cid in sources:
            continue
        for root in (P.KNOW, P.ARCHIVE_KNOW):
            path = os.path.join(root, f"{cid}.md")
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    sources[cid] = Source(f.read())
                break
        else:
            print(f"WARN: quote names card {cid}, which is in neither "
                  f"memory/know nor the archive")
    for spec in args.source:
        name, _, path = spec.partition("=")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                sources[name] = Source(f.read())
        except OSError as e:
            print(f"WARN source {name}: {e}")
            sources[name] = Source("")

    n_cards = n_quotes = n_bad_quote = n_unknown_src = n_noquotes = n_unbacked = 0
    for label, body in split_cards(text):
        n_cards += 1
        quotes = QUOTE_RE.findall(body)
        if not quotes:
            n_noquotes += 1
            print(f"{label}: FAIL no QUOTES block — the card is ungrounded, drop it")
            continue
        good = []
        for src, quote in quotes:
            n_quotes += 1
            q = norm(quote)
            if src not in sources:
                n_unknown_src += 1
                print(f"{label}: FAIL quote names unknown source '{src}' :: {quote[:80]}")
                continue
            if len(q) < 8:
                print(f"{label}: WARN quote too short to verify :: {quote[:80]}")
                continue
            kind, detail, vouches = classify(quote, sources[src])
            if kind != "ABSENT":
                good.append(norm(vouches))
                if kind != "EXACT":
                    print(f"{label}: {kind} quote in {src}"
                          f"{' (' + detail + ')' if detail else ''} :: {quote[:80]}")
                continue
            n_bad_quote += 1
            # Never suggest re-labelling to a source that happens to contain it:
            # that converts a grounding failure into a false provenance, which is
            # worse. Report where it was found; the stage decides.
            found_in = [name for name, blob in sources.items()
                        if name != src and classify(quote, blob)[0] != "ABSENT"]
            extra = f" (text IS present in: {', '.join(found_in)})" if found_in else ""
            note = f" [{detail}]" if detail else ""
            print(f"{label}: FAIL quote not found in {src}{extra}{note} :: {quote[:100]}")
        backing = " ".join(good)
        unbacked = sorted(s for s in specifics(body) if norm(s) not in backing)
        if unbacked:
            n_unbacked += len(unbacked)
            print(f"{label}: UNBACKED specifics (remove them, or drop the card if they "
                  f"were the content): {', '.join(unbacked[:12])}")

    print(f"SUMMARY cards={n_cards} quotes={n_quotes} unverified={n_bad_quote} "
          f"unknown-source={n_unknown_src} no-quotes={n_noquotes} "
          f"unbacked-specifics={n_unbacked}")
    hard = n_bad_quote + n_unknown_src + n_noquotes
    sys.exit(1 if hard or (args.strict and n_unbacked) else 0)


if __name__ == "__main__":
    main()
