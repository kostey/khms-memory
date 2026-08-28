#!/usr/bin/env python3
"""Approve stage: turn reviewed candidate cards (temp labels) into know/ cards (K ids).

Usage: approve_inbox.py [--dry-run] inbox/2026-03-04.md [more.md ...]

- Parses card blocks (--- yaml --- body), tolerating ```yaml / ```markdown fences.
- Allocates sequential ids from tools/.next_id and rewrites temp cross-references
  inside links, so a candidate that referenced C7 ends up referencing its real id.
- Writes memory/know/K-*.md and prints the mapping.

RUN ONLY AFTER REVIEW (spec P6). This script is the only thing in the system that
writes into know/, and it has no judgement of its own. Validate afterwards with
build_views.py.
"""
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_lint  # noqa: E402
import khms_paths as P  # noqa: E402

CARD_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*?)(?=\n---\s*\n|\Z)", re.S | re.M)


def parse_cards(text, skipped=None):
    cards = []
    if skipped is None:
        skipped = []

    def try_card(front, body):
        try:
            meta = yaml.safe_load(front)
        except yaml.YAMLError as e:
            ident = re.search(r"^id: (\S+)", front, re.M)
            skipped.append((ident.group(1) if ident else "?", str(e).split("\n")[0]))
            print(f"SKIP (bad yaml): {str(e)[:120]} :: {front[:80]!r}", file=sys.stderr)
            return
        if isinstance(meta, dict) and "id" in meta and "type" in meta:
            body = re.split(r"\n## (?:STATS|NEW-TAGS)\b", body)[0].strip()
            body = re.sub(r"\n\*\(.*?\)\*\s*$", "", body, flags=re.S).strip()
            cards.append((meta, body))

    def _take_fenced(m):
        inner = m.group(1)
        fm = re.match(r"(.*?)\n---\n(.*)", inner, re.S)
        if fm and re.search(r"^id: ", fm.group(1), re.M):
            try_card(fm.group(1).strip(), fm.group(2).strip())
            return ""                     # consumed, so the bare pass cannot re-match it
        return inner

    text = re.sub(r"```yaml\n(.*?)```", _take_fenced, text, flags=re.S)
    text = re.sub(r"```(?:markdown)?\n?", "", text)

    for m in CARD_RE.finditer(text):
        front = m.group(1)
        if not re.search(r"^id: ", front, re.M):
            continue                      # a markdown '---' rule, not a card
        try_card(front, m.group(2).strip())
    return cards


def main():
    dry = "--dry-run" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        print(__doc__)
        sys.exit(2)
    all_cards, skipped = [], []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            all_cards += parse_cards(fh.read(), skipped)
    if not all_cards:
        print("no cards found")
        sys.exit(1)

    # A card that fails to parse used to be one stderr line and exit code 0, so a
    # partial import read as a complete one and the dropped cards were never seen
    # again. Silence about what was lost IS the failure: say it loudly, and exit
    # non-zero at the end so no caller can mistake it for a finished run.
    if skipped:
        print(f"\n!! {len(skipped)} card(s) FAILED to parse and were NOT written:")
        for cid, err in skipped:
            print(f"   {cid}: {err}")
        print("   Fix them and re-run with ONLY those cards — the ones below are "
              "already written.")

    # A correction with no edge is a correction nothing can reach: retrieval
    # follows links, so a card that says CORRECTED but names nothing leaves the
    # card it corrects being served alone, as current, for as long as anybody
    # keeps asking about it. Refuse BEFORE any id is allocated, so a refused run
    # leaves know/ and the counter exactly as they were and the reviewer re-runs
    # the whole file once the edge is in.
    known = khms_lint.known_card_ids(P.KNOW) | {str(m["id"]) for m, _ in all_cards}
    problems = khms_lint.lint_batch(
        [(str(m["id"]), m, b) for m, b in all_cards], known)
    if problems:
        print(f"\n!! REFUSED: {len(problems)} card(s) claim to correct something "
              f"without saying what. NOTHING was written, no ids were allocated.")
        for p in problems:
            print(f"   {p}")
        sys.exit(1)

    with open(P.COUNTER) as f:
        cur = int(f.read().strip())
    mapping = {}
    for meta, _ in all_cards:
        mapping[str(meta["id"])] = f"K-{cur:05d}"
        cur += 1

    def remap(v):
        if isinstance(v, list):
            return [mapping.get(str(x), str(x)) for x in v]
        if v is None:
            return None
        return mapping.get(str(v), str(v))

    for meta, body in all_cards:
        old = str(meta["id"])
        meta["id"] = mapping[old]
        links = meta.get("links") or {}
        # A link written as a TOP-LEVEL key (`supports: [K-00042]` instead of
        # `links: {supports: [...]}`) is not an error anywhere — it is silently
        # dropped, because every consumer reads links only from meta["links"].
        # Pop whatever arrives that way so no edge reaches know/ loose.
        for k in ("derived_from", "supports", "contradicts", "refuted_by"):
            stray = meta.pop(k, None)
            vals = list(links.get(k) or [])
            if stray is not None:
                for v in (stray if isinstance(stray, list) else [stray]):
                    if v not in vals:
                        vals.append(v)
            links[k] = remap(vals)
        stray_ss = meta.pop("supersedes", None)
        links["supersedes"] = remap(links.get("supersedes") or stray_ss)
        meta["links"] = links

        order = ["id", "type", "level", "status", "tags", "scope", "evidence",
                 "source", "date", "links"]
        front = {k: meta[k] for k in order if k in meta}
        for k, v in meta.items():
            if k not in front and k != "body":
                front[k] = v
        fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True,
                            default_flow_style=None, width=1000).strip()
        out = f"---\n{fm}\n---\n{body}\n"
        path = os.path.join(P.KNOW, f"{meta['id']}.md")
        if dry:
            print(f"DRY {old} -> {meta['id']}")
        else:
            if os.path.exists(path):
                print(f"ERROR: {path} exists — id counter out of sync", file=sys.stderr)
                sys.exit(1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
    if not dry:
        with open(P.COUNTER, "w") as f:
            f.write(str(cur) + "\n")
        os.makedirs(P.STAGING, exist_ok=True)
        with open(os.path.join(P.STAGING, "last-mapping.txt"), "a", encoding="utf-8") as mf:
            for o, n in mapping.items():
                mf.write(f"{o} {n}\n")
    print(f"{'would write' if dry else 'wrote'} {len(all_cards)} cards "
          f"({files}); counter -> {cur}")
    for o, n in list(mapping.items())[:10]:
        print(f"  {o} -> {n}")
    if len(mapping) > 10:
        print(f"  … +{len(mapping) - 10} more")
    if skipped:
        sys.exit(1)


if __name__ == "__main__":
    main()
