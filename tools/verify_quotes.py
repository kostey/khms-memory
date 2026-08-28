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
import re
import sys
import unicodedata

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
    return re.sub(r"\s+", " ", s).strip().lower()


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

    sources = {}
    for spec in args.source:
        name, _, path = spec.partition("=")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                sources[name] = norm(f.read())
        except OSError as e:
            print(f"WARN source {name}: {e}")
            sources[name] = ""

    with open(args.cards, encoding="utf-8", errors="replace") as f:
        text = f.read()

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
            if q in sources[src]:
                good.append(q)
            else:
                n_bad_quote += 1
                # Never suggest re-labelling to a source that happens to contain
                # it: that converts a grounding failure into a false provenance,
                # which is worse. Report where it was found; the stage decides.
                found_in = [s for s, blob in sources.items() if s != src and q in blob]
                extra = f" (text IS present in: {', '.join(found_in)})" if found_in else ""
                print(f"{label}: FAIL quote not found in {src}{extra} :: {quote[:100]}")
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
