#!/usr/bin/env python3
"""KHMS search core — ONE scoring implementation, two consumers:

  - recall.sh      the manual CLI: free text in, ranked cards out
  - khms_hook.py   the harness hook layer: injects cards into the live session

They share this module on purpose. When the CLI and the automatic injection score
differently, the automation quietly develops its own idea of what is relevant and
nobody notices until a card that "was right there" never appeared.

Scoring: idf per query token, first line ×3, tag ×1.5, whole-phrase bonus +10,
archived ("fog") card ×0.6. A stateless scan over a few thousand cards costs
~0.1 s and needs no index that can go stale; swap the internals for SQLite FTS5
when the base outgrows that, without changing the habit. Zero model tokens.
"""
import glob
import math
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P  # noqa: E402

TOPN = 8

_WORD = re.compile(r"[a-z0-9_/.\-]+")
_LINK_KEYS = ("derived_from", "supports", "contradicts", "refuted_by")


def norm(s):
    """Lowercase, strip diacritics, normalise fancy punctuation. Diacritics are
    stripped so that a query typed without them still matches text that has
    them — the common case whenever the operator's language is not English."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    for a, b in (("’", "'"), ("‘", "'"), ("‚", "'"),
                 ("“", '"'), ("”", '"'), ("„", '"'),
                 ("—", "-"), ("–", "-")):
        s = s.replace(a, b)
    return s.lower()


def tokens(s):
    return [t for t in _WORD.findall(norm(s))
            if len(t) >= 3 or re.fullmatch(r"0x[0-9a-f]+|\d+", t)]


def _parse_links(front):
    links = {k: [] for k in _LINK_KEYS}
    links["supersedes"] = None
    m = re.search(r"^links:\n((?:[ \t]+.*\n?)*)", front, re.M)
    if not m:
        return links
    block = m.group(1)
    for k in _LINK_KEYS:
        km = re.search(rf"^\s+{k}:\s*\[([^\]]*)\]", block, re.M)
        if km:
            links[k] = [x.strip() for x in km.group(1).split(",") if x.strip()]
    sm = re.search(r"^\s+supersedes:\s*(\S+)", block, re.M)
    if sm and sm.group(1) not in ("null", "~", "[]"):
        links["supersedes"] = sm.group(1)
    return links


def load_cards():
    """Live cards first, then the archive ("fog"). Archived cards are searchable
    on purpose: a superseded or condensed card is still the cheapest way to find
    out that something was already tried."""
    cards = []
    for path in sorted(glob.glob(os.path.join(P.KNOW, "K-*.md"))) + \
            sorted(glob.glob(os.path.join(P.ARCHIVE_KNOW, "K-*.md"))):
        fog = os.sep + "archive" + os.sep in path
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            continue
        front, body = m.groups()

        def fm(key, default="", _front=front):
            mm = re.search(rf"^{key}:\s*(.+)$", _front, re.M)
            return mm.group(1).strip() if mm else default

        tg = re.search(r"^tags:\s*\[([^\]]*)\]", front, re.M)
        body = body.strip()
        cards.append({
            "id": fm("id", path),
            "status": fm("status", "?"),
            "level": fm("level", "observation"),
            "tags": [t.strip() for t in (tg.group(1) if tg else "").split(",") if t.strip()],
            "ntags": norm(tg.group(1)) if tg else "",
            "links": _parse_links(front),
            "body": body,
            "nbody": norm(body),
            "first": body.splitlines()[0] if body else "",
            "fog": fog,
            "path": path,
        })
    return cards


def reverse_correctors(cards):
    """card id -> [cards that supersede or contradict it]. Consumers merge this
    with the card's own links.refuted_by, so that a refuted card is never served
    without whatever corrected it."""
    rev = {}
    for c in cards:
        links = c.get("links") or {}
        # .get, not [], because this map is now consulted for EVERY served card,
        # on the live hook path: one card with a link block this parser did not
        # fill must not cost the whole injection.
        for tgt in (links.get("contradicts") or []):
            rev.setdefault(tgt, []).append(c)
        sup = links.get("supersedes")
        for tgt in (sup if isinstance(sup, list) else [sup] if sup else []):
            rev.setdefault(tgt, []).append(c)
    return rev


def load_glossary(path=None):
    """OPTIONAL bridge for a base written in one language and queried in another
    (spec P7 / §4.11). Lines: '<query-language stem, no diacritics><TAB><base-language words>'.
    '#' starts a comment. Absent file = no expansion, which is the default."""
    gl = {}
    try:
        with open(path or P.GLOSSARY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                src, dst = line.split("\t", 1)
                gl[norm(src.strip())] = dst.strip()
    except OSError:
        pass
    return gl


def expand_query(query_raw, glossary):
    """Append base-language equivalents for stems found in the query. Stem match:
    a glossary key that a query token starts with, so inflected forms all hit the
    same key. Returns (expanded_query, added_terms)."""
    if not glossary:
        return query_raw, []
    added = []
    for t in set(tokens(query_raw)):
        for key, dst in glossary.items():
            if t.startswith(key) and dst not in added:
                added.append(dst)
    return (query_raw + " " + " ".join(added)).strip() if added else query_raw, added


def search(query_raw, cards=None, exclude_meta=False, topn=TOPN):
    """Returns (results, cards); results are (score, matched_tokens, card).

    exclude_meta drops the base's cards ABOUT ITSELF (tags khms/core) unless the
    query is itself about the memory system — otherwise every question about the
    work gets answered with the memory system's own documentation."""
    if cards is None:
        cards = load_cards()
    q_tokens = tokens(query_raw)
    q_phrase = re.sub(r"\s+", " ", norm(query_raw)).strip()
    if not q_tokens:
        return [], cards

    meta_ok = bool(re.search(r"khms|memory|card|recall|remember", norm(query_raw)))
    pool = cards
    if exclude_meta and not meta_ok:
        pool = [c for c in cards if not ({"khms", "core"} & set(c["tags"]))]

    df = {t: sum(1 for c in cards if t in c["nbody"]) for t in set(q_tokens)}
    n = len(cards)
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 0.1 for t in df}

    results = []
    for c in pool:
        nfirst = norm(c["first"])
        score, matched = 0.0, []
        for t in set(q_tokens):
            if t in c["nbody"]:
                w = idf[t]
                if t in nfirst:
                    w *= 3.0
                if t in c["ntags"]:
                    w *= 1.5
                score += w
                matched.append(t)
        if q_phrase and len(q_phrase) > 12 and q_phrase in c["nbody"]:
            score += 10.0
        if c["fog"]:
            score *= 0.6
        if score > 0:
            results.append((score, matched, c))
    results.sort(key=lambda r: -r[0])
    return results[:topn], cards


def log_query(query_raw, results, src="cli"):
    """Every query is logged. This is what makes "the base was consulted" an
    auditable fact rather than a claim in a reply."""
    import datetime
    top = f"{results[0][2]['id']}:{results[0][0]:.1f}" if results else "-"
    try:
        with open(P.RECALL_LOG, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"
                     f" | {query_raw} | nhits={len(results)} | top={top} | src={src}\n")
    except OSError:
        pass


def main_cli(argv):
    query_raw = " ".join(argv)
    expanded, added = expand_query(query_raw, load_glossary())
    results, cards = search(expanded)          # CLI keeps the FULL pool
    log_query(query_raw, results, src="cli")
    if added:
        print(f"(glossary: +{' '.join(added)[:120]})")
    if not tokens(query_raw):
        print("recall: query has no searchable tokens")
        return 2
    if not results:
        print(f"recall: nothing on record for: {query_raw}")
        return 1
    df = {t: sum(1 for c in cards if t in c["nbody"]) for r in results for t in r[1]}
    for score, matched, c in results:
        flags = []
        if c["status"] in ("refuted", "challenged"):
            flags.append("!" + c["status"].upper())
        elif c["status"] != "active":
            flags.append(c["status"])
        if "gotcha" in c["tags"]:
            flags.append("gotcha")
        if c["fog"]:
            flags.append("fog")
        flag = (" [" + ",".join(flags) + "]") if flags else ""
        print(f"{c['id']}{flag} ({score:.1f}): {c['first'][:110]}")
        rare = min(sorted(matched), key=lambda t: df.get(t, 0)) if matched else None
        if rare:
            for line in c["body"].splitlines()[1:]:
                if rare in norm(line):
                    print(f"    -> {line.strip()[:150]}")
                    break
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:]))
