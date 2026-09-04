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

# WITHHELD SECTIONS. `verify_relations.py` does not delete a candidate it rejects: it
# MOVES the whole block — yaml fence, body, quotes and all — into `## DEFERRED` or
# `## DROPPED (no valid RELATION)` at the end of the file, so a human can rescue it by
# moving it back. That is only true as long as this stage refuses to read those
# sections; a parser that scans the whole file would approve exactly the candidates the
# gate rejected, and the gate would be worse than nothing.
TRAILER_RE = re.compile(r"^## +(DEFERRED|DROPPED)\b.*$", re.M | re.I)

# THE `**LINKS:**` BODY LINE IS A REAL CHANNEL, and it has to be parsed here or the
# prompt that asks for it is lying. The consolidate stage writes links as one line in
# the body — it cannot edit frontmatter reliably across a whole file — and unless this
# stage folds that line into `links:`, every edge a stage spotted is dropped silently:
# no error, no warning, just a knowledge graph that never grows an edge. Only the five
# link types exist; anything else on the line (a plausible-looking `related=`) is
# reported and dropped, because inventing a sixth link type here would put edges into
# cards that nothing else can read.
LINKS_LINE_RE = re.compile(r"^\s*\*\*LINKS:\*\*\s*(.+?)\s*$", re.M)
LINK_KV_RE = re.compile(r"(\w+)\s*=\s*\[([^\]]*)\]")
LINK_KEYS = ("derived_from", "supports", "contradicts", "refuted_by", "supersedes")


def fold_links_line(meta, body, unknown=None):
    """-> body without the LINKS line, with its edges merged into meta['links']."""
    m = LINKS_LINE_RE.search(body)
    if not m:
        return body
    links = meta.get("links") or {}
    if not isinstance(links, dict):
        links = {}
    for key, vals in LINK_KV_RE.findall(m.group(1)):
        ids = [v.strip() for v in vals.split(",") if v.strip()]
        if key not in LINK_KEYS:
            if unknown is not None:
                unknown.append((str(meta.get("id", "?")), key))
            continue
        if key == "supersedes":
            links["supersedes"] = ids[0] if ids else links.get("supersedes")
            continue
        cur = list(links.get(key) or [])
        for v in ids:
            if v not in cur:
                cur.append(v)
        links[key] = cur
    meta["links"] = links
    return (body[:m.start()] + body[m.end():]).strip()


def cut_trailers(text):
    """-> (text above the first withheld section, heading of that section or None)."""
    m = TRAILER_RE.search(text)
    return (text[:m.start()], m.group(0).strip()) if m else (text, None)


def parse_cards(text, skipped=None, unknown_links=None):
    cards = []
    if skipped is None:
        skipped = []
    if unknown_links is None:
        unknown_links = []

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
            body = fold_links_line(meta, body, unknown_links)
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


FRONT_ORDER = ["id", "type", "level", "status", "tags", "scope", "evidence",
               "source", "date", "links"]


def dump_card(meta, body):
    """One card file, with a stable and readable key order. Shared by the writer
    below and by mark_superseded, so a card rewritten by the second one comes out
    in the same shape as a card written by the first."""
    front = {k: meta[k] for k in FRONT_ORDER if k in meta}
    for k, v in meta.items():
        if k not in front and k != "body":
            front[k] = v
    fm = yaml.safe_dump(front, sort_keys=False, allow_unicode=True,
                        default_flow_style=None, width=1000).strip()
    return f"---\n{fm}\n---\n{body.strip()}\n"


def mark_superseded(edges):
    """Close the OTHER half of a `supersedes` edge, on the target card.

    An edge written on the new card alone leaves the old one `status: active`, so
    retrieval keeps serving it as current — and the injection layer, which cannot
    read intentions, serves it as the state of the world. Measured in the reference
    deployment: cards corrected in August kept their active July predecessors and
    were quoted back to the operator as current, correctly, by the rules as they
    then stood. The review had been closing this by hand, one card at a time, after
    approve_inbox.py had already run. This is that hand edit, done by the tool that
    already knows both ids.

    Deliberately narrow: only `supersedes`, only a target that exists in know/, only
    one that is still `active`. `supports` and `contradicts` change no status here —
    inventing a status policy is not this script's job, and a tool that silently
    re-statuses cards on a link it guessed at is worse than the hand edit.
    """
    changed, notes = 0, []
    for new_id, targets in edges:
        for tgt in targets:
            path = os.path.join(P.KNOW, f"{tgt}.md")
            if not os.path.exists(path):
                notes.append(f"   {new_id} supersedes {tgt}: no such card in know/ "
                             f"— target NOT marked")
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
            if not m:
                notes.append(f"   {tgt}: unparseable card — NOT marked")
                continue
            try:
                meta = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError as e:
                notes.append(f"   {tgt}: bad yaml ({str(e)[:60]}) — NOT marked")
                continue
            status = str(meta.get("status", "active"))
            if status != "active":
                notes.append(f"   {tgt}: status is '{status}', not active — left as "
                             f"it is; the review decides")
                continue
            meta["status"] = "superseded"
            links = meta.get("links") or {}
            if not isinstance(links, dict):
                links = {}
            links["superseded_by"] = new_id
            meta["links"] = links
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(dump_card(meta, m.group(2)))
            os.replace(tmp, path)
            changed += 1
            notes.append(f"   {tgt}: status -> superseded, superseded_by {new_id}")
    return changed, notes


def main():
    dry = "--dry-run" in sys.argv
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        print(__doc__)
        sys.exit(2)
    all_cards, skipped = [], []
    withheld, unknown_links = [], []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            head, trailer = cut_trailers(fh.read())
        if trailer:
            n = len(parse_cards(open(f, encoding="utf-8").read())) - len(parse_cards(head))
            withheld.append((f, trailer, n))
        all_cards += parse_cards(head, skipped, unknown_links)
    for f, trailer, n in withheld:
        print(f"withheld: {n} candidate(s) under '{trailer}' in {f} — they were moved "
              f"there by the relation gate, not deleted. To approve one, move its block "
              f"back under '## Cards' first.")
    for cid, key in unknown_links:
        print(f"LINKS line: dropped '{key}=' on {cid} — the only link types are "
              f"{', '.join(LINK_KEYS)}. Say the rest in prose, where a human reads it.")
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

    supersede_edges = []
    for meta, body in all_cards:
        old = str(meta["id"])
        meta["id"] = mapping[old]
        links = meta.get("links") or {}
        # A link written as a TOP-LEVEL key (`supports: [K-NNNNN]` instead of
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

        out = dump_card(meta, body)
        ss = links["supersedes"]
        targets = [str(t) for t in (ss if isinstance(ss, list) else [ss])
                   if t and str(t).startswith("K-")]
        if targets:
            supersede_edges.append((meta["id"], targets))
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
        n_marked, notes = mark_superseded(supersede_edges)
        if notes:
            print(f"\nsupersedes edges: {n_marked} target card(s) marked superseded")
            for n in notes:
                print(n)
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
