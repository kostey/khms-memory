#!/usr/bin/env python3
"""KHMS view/index generator. Deterministic, zero model tokens.

Reads memory/know/K-*.md, validates every card against the spec, computes belief
and condense_score (spec §5), and regenerates memory/views/ and MEMORY.md.

Exits 1 on any schema violation (bad YAML, id/filename mismatch, derived card
with no derived_from, observation with no evidence). That is deliberate: a card
that is broken must not reach the index looking fine, and a generator that
"mostly works" is how a base rots quietly.

Run it after every approval, and after any hand edit to a card's status or links.
"""
import datetime
import glob
import math
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P  # noqa: E402

# --- configuration (spec §7) -------------------------------------------------
EVIDENCE_WEIGHT = {"measured": 3, "observed": 2, "reported": 1}
W_ASSUMPTION = 1
W_INDIRECT = 2          # an active derived card cited as support
K_SLOPE = 4             # belief = tanh((support - oppose) / K_SLOPE)
F_UNABSORBED = 0.3
LEVELS = {"observation", "derived", "assumption"}
STATUSES = {"active", "challenged", "refuted", "superseded", "condensed"}
TOP_PATTERNS = 5
RECENT_DAYS = 30
MEMORY_MAX_LINES = 80


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_cards():
    cards = {}
    for path in sorted(glob.glob(os.path.join(P.KNOW, "K-*.md"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            fail(f"{path}: no frontmatter")
        try:
            meta = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            fail(f"{path}: bad YAML: {e}")
        body = m.group(2).strip()
        cid = meta.get("id")
        if cid != os.path.basename(path)[:-3]:
            fail(f"{path}: id '{cid}' != filename")
        for req in ("type", "level", "status", "tags", "scope", "date"):
            if req not in meta:
                fail(f"{cid}: missing '{req}'")
        if meta["level"] not in LEVELS:
            fail(f"{cid}: bad level {meta['level']}")
        if meta["status"] not in STATUSES:
            fail(f"{cid}: bad status {meta['status']}")
        links = meta.get("links") or {}
        for k in ("derived_from", "supports", "contradicts", "refuted_by"):
            links.setdefault(k, [])
            if links[k] is None:
                links[k] = []
        links.setdefault("supersedes", None)
        if meta["level"] == "derived" and not links["derived_from"]:
            fail(f"{cid}: derived card with empty derived_from")
        if meta["level"] == "observation" and "evidence" not in meta:
            fail(f"{cid}: observation without evidence")
        meta["links"] = links
        meta["body"] = body
        lines = body.splitlines()
        title = lines[0] if lines else "(empty)"
        if meta.get("type") == "requirement":       # the WHAT line is the informative one
            title = next((ln for ln in lines if ln.startswith("WHAT:")), title)
        meta["title"] = title[:120]
        cards[cid] = meta
    return cards


def load_registry():
    """tags.md: | tag | group | description | aliases | count |  (count regenerated)."""
    reg, alias = {}, {}
    path = os.path.join(P.VIEWS, "tags.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 4 and cells[0] not in ("tag", ":---", "---") \
                        and not cells[0].startswith(":"):
                    tag, group, desc = cells[0], cells[1], cells[2]
                    aliases = [a.strip() for a in cells[3].split(",")
                               if a.strip() and a.strip() != "-"]
                    if tag and not tag.startswith("#"):
                        reg[tag] = {"group": group, "desc": desc, "aliases": aliases}
                        for a in aliases:
                            alias[a] = tag
    return reg, alias


def weight(card):
    if card["level"] == "observation":
        return EVIDENCE_WEIGHT.get(card.get("evidence"), 1)
    if card["level"] == "assumption":
        return W_ASSUMPTION
    return W_INDIRECT


def compute(cards):
    contra = {cid: set(c["links"]["contradicts"]) for cid, c in cards.items()}
    for cid, c in cards.items():                       # contradiction is symmetric
        for other in c["links"]["contradicts"]:
            if other in contra:
                contra[other].add(cid)
    incoming_support = {cid: set() for cid in cards}
    for cid, c in cards.items():
        for target in c["links"]["supports"]:
            if target in incoming_support:
                incoming_support[target].add(cid)
    today = datetime.date.today()
    for cid, c in cards.items():
        def active(x):
            return x in cards and cards[x]["status"] == "active"
        if c["level"] == "derived":
            sources = set(c["links"]["derived_from"]) | incoming_support[cid]
            support = sum(weight(cards[s]) for s in sources if active(s))
            opp_ids = contra[cid] | set(c["links"]["refuted_by"])
            oppose = sum(weight(cards[o]) for o in opp_ids if active(o))
            c["belief"] = math.tanh((support - oppose) / K_SLOPE)
            c["evi"] = (sum(1 for s in sources if active(s)),
                        sum(1 for o in opp_ids if active(o)))
        else:
            c["belief"] = None
        # condense score — observations only, with conservative protections
        protected = (
            c["level"] != "observation"
            or c["status"] in ("refuted", "condensed")
            or "gotcha" in c["tags"]
            or c["type"] == "fact"
        )
        if protected:
            c["condense"] = 0.0
        else:
            try:
                d = datetime.date.fromisoformat(str(c["date"]))
            except ValueError:
                d = today
            weeks = max(0.0, (today - d).days / 7.0)
            absorbed = any(
                cid in cards[p]["links"]["derived_from"]
                for p in cards
                if cards[p]["level"] == "derived" and cards[p]["status"] == "active")
            c["condense"] = weeks * (1.0 if absorbed else F_UNABSORBED)
    return cards


def card_line(c):
    b = (f" · belief {c['belief']:+.2f} ({c['evi'][0]} for, {c['evi'][1]} against)"
         if c["belief"] is not None else "")
    ev = f" · {c.get('evidence')}" if c.get("evidence") else ""
    return f"- **{c['id']}** [{c['type']}/{c['status']}]{ev}{b} — {c['title']}"


def write_views(cards, reg, alias):
    os.makedirs(os.path.join(P.VIEWS, "topics"), exist_ok=True)
    os.makedirs(os.path.join(P.VIEWS, "by-type"), exist_ok=True)
    unregistered, tagmap = {}, {}
    for c in cards.values():
        for t in c["tags"]:
            t2 = alias.get(t, t)
            if t2 not in reg:
                unregistered[t2] = unregistered.get(t2, 0) + 1
            tagmap.setdefault(t2, []).append(c)

    for old in glob.glob(os.path.join(P.VIEWS, "topics", "*.md")):
        os.remove(old)
    for tag, group in sorted(tagmap.items()):
        lines = ["# topic: " + tag, ""]
        pats = sorted((c for c in group
                       if c["level"] == "derived" and c["status"] == "active"),
                      key=lambda c: -(c["belief"] or 0))
        chal = [c for c in group if c["status"] == "challenged"]
        refu = [c for c in group if c["status"] == "refuted"]
        gotc = [c for c in group if "gotcha" in c["tags"] and c["status"] == "active"]
        obs = [c for c in group if c["level"] in ("observation", "assumption")
               and c["status"] == "active" and "gotcha" not in c["tags"]]
        for name, items in (("Patterns", pats), ("Facts & observations", obs),
                            ("Gotchas", gotc), ("Challenged", chal),
                            ("Refuted & dead ends", refu)):
            if items:
                lines += [f"## {name}", *[card_line(c) for c in items], ""]
        with open(os.path.join(P.VIEWS, "topics", f"{tag}.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    for old in glob.glob(os.path.join(P.VIEWS, "by-type", "*.md")):
        os.remove(old)
    bytype = {}
    for c in cards.values():
        bytype.setdefault(c["type"], []).append(c)
    for t, items in bytype.items():
        fn = re.sub(r"[^a-z0-9]+", "-", t.lower())
        body = "\n".join(card_line(c) for c in sorted(items, key=lambda c: c["id"]))
        with open(os.path.join(P.VIEWS, "by-type", f"{fn}.md"), "w", encoding="utf-8") as f:
            f.write(f"# type: {t}\n\n{body}\n")

    cutoff = (datetime.date.today() - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    rec = [c for c in cards.values() if str(c["date"]) >= cutoff]
    with open(os.path.join(P.VIEWS, "recent.md"), "w", encoding="utf-8") as f:
        f.write("# recent (last %d days)\n\n%s\n" % (
            RECENT_DAYS,
            "\n".join(card_line(c)
                      for c in sorted(rec, key=lambda c: str(c["date"]), reverse=True))))

    lines = ["# Tag registry", ""]
    if unregistered:
        lines += ["## UNREGISTERED (adopt or rename at review)",
                  *[f"- {t} ({n} cards)" for t, n in sorted(unregistered.items())], ""]
    lines += ["| tag | group | description | aliases | cards |", "|---|---|---|---|---|"]
    for tag in sorted(reg):
        r = reg[tag]
        lines.append(f"| {tag} | {r['group']} | {r['desc']} | "
                     f"{', '.join(r['aliases']) or '-'} | {len(tagmap.get(tag, []))} |")
    with open(os.path.join(P.VIEWS, "tags.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    cand = sorted((c for c in cards.values() if c["condense"] > 0),
                  key=lambda c: -c["condense"])
    with open(os.path.join(P.VIEWS, "condense-candidates.md"), "w", encoding="utf-8") as f:
        f.write("# condense candidates (ranked; weekly review input)\n\n" + "\n".join(
            f"- {c['id']} score {c['condense']:.1f} — {c['title']}" for c in cand[:50]) + "\n")

    # reverse correction index: "who corrects ME". The hook builds the same map
    # in memory from the cards it has already loaded (khms_search.
    # reverse_correctors), so this file is not what it reads — it is the
    # human-readable half, and the place a review can see at a glance which
    # corrections are actually wired up rather than merely written.
    rev = {}
    for c in cards.values():
        for tgt in c["links"]["contradicts"]:
            rev.setdefault(tgt, []).append(c)
        for tgt in c["links"]["refuted_by"]:          # the corrected card's own edge
            if tgt in cards:
                rev.setdefault(c["id"], []).append(cards[tgt])
        ss = c["links"]["supersedes"]                 # scalar per spec, list in the wild
        for tgt in (ss if isinstance(ss, list) else [ss] if ss else []):
            rev.setdefault(tgt, []).append(c)
    L = ["# reverse correction index (who corrects whom — generated)", ""]
    for tgt in sorted(rev):
        L.append(f"- **{tgt}** {cards[tgt]['title'][:80] if tgt in cards else '(no such card)'}")
        for cor in sorted({c["id"]: c for c in rev[tgt]}.values(), key=lambda c: c["id"]):
            L.append(f"    ! corrected by {cor['id']} [{cor['status']}] — {cor['title'][:80]}")
    with open(os.path.join(P.VIEWS, "correctors.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    scopes = {}
    for c in cards.values():
        scopes[str(c["scope"])] = scopes.get(str(c["scope"]), 0) + 1
    with open(os.path.join(P.VIEWS, "scopes.md"), "w", encoding="utf-8") as f:
        f.write("# scope tree (branches in use)\n\n" + "\n".join(
            f"- {s} ({n} cards)" for s, n in sorted(scopes.items())) + "\n")
    return tagmap, unregistered


def write_memory_md(cards, reg, tagmap):
    core = [c for c in cards.values() if "core" in c["tags"] and c["status"] == "active"]
    usage = [c for c in core if "khms" in c["tags"] and c["type"] == "policy"]
    usage_body = usage[-1]["body"] if usage else "(bootstrap-digest card not written yet)"
    pats = sorted((c for c in cards.values()
                   if c["belief"] is not None and c["status"] == "active"),
                  key=lambda c: -c["belief"])[:TOP_PATTERNS]
    plans = sorted(glob.glob(os.path.join(P.ROOT, "plans", "*.md")),
                   key=os.path.getmtime, reverse=True)[:2]
    journals = sorted(glob.glob(os.path.join(P.JOURNAL, "*.md")))[-1:]
    groups = {}
    for tag, items in tagmap.items():
        g = reg.get(tag, {}).get("group", "other")
        groups.setdefault(g, []).append(f"{tag} {len(items)}")

    lines = ["# MEMORY — KHMS index (generated by tools/build_views.py — do not edit)", ""]
    lines += ["## 1 How this KB works", usage_body, ""]
    lines += ["## 2 Core"]
    lines += [f"- [{c['id']}](memory/know/{c['id']}.md) — {c['title']}"
              for c in sorted(core, key=lambda c: c["id"])] or ["- (none yet)"]
    lines += ["", "## 3 Current focus"]
    lines += [f"- plans/{os.path.basename(p)}" for p in plans]
    lines += [f"- journal/{os.path.basename(j)}" for j in journals]
    lines += ["", "## 4 Top patterns"]
    lines += [f"- {c['id']} belief {c['belief']:+.2f} — {c['title']}"
              for c in pats] or ["- (none yet)"]
    lines += ["", "## 5 Topics (full registry: memory/views/tags.md)"]
    lines += [f"- {g}: " + ", ".join(sorted(items)) for g, items in sorted(groups.items())]
    text = "\n".join(lines) + "\n"
    with open(P.MEMORY_MD, "w", encoding="utf-8") as f:
        f.write(text)
    return len(text.splitlines())


def main():
    cards = load_cards()
    reg, alias = load_registry()
    cards = compute(cards)
    tagmap, unreg = write_views(cards, reg, alias)
    n_lines = write_memory_md(cards, reg, tagmap)
    n_der = sum(1 for c in cards.values() if c["level"] == "derived")
    print(f"OK: {len(cards)} cards ({n_der} derived), {len(tagmap)} tags "
          f"({len(unreg)} unregistered), MEMORY.md {n_lines} lines")
    if n_lines > MEMORY_MAX_LINES:
        print(f"WARNING: MEMORY.md exceeds {MEMORY_MAX_LINES} lines — it is loaded into "
              f"every context; trim the core set or the digest", file=sys.stderr)


if __name__ == "__main__":
    main()
