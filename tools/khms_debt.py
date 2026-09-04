#!/usr/bin/env python3
"""Contradiction debt — what was FOUND against what the base actually records.

    khms_debt.py [--since DAYS] [--root DIR] [--cluster-min N]

WHY THIS EXISTS
---------------
Measured in the reference deployment: a contradiction sweep judged 250 of 2951
candidate pairs and confirmed 75 contradictions, while the whole base carried 8
superseded + 7 refuted + 23 challenged cards (1.6 %). Contradictions were being FOUND
and not being WRITTEN AS EDGES — and nothing printed that gap, so nothing acted on it.
The same audit measured that 1041 of 2415 cards (43 %) had never been injected and had
never been a recall top hit: a base growing faster than it is read.

This prints both, from artefacts that already exist (the cards themselves, the two
retrieval logs, and — if you run one — the contradiction-triage proposals and their
`.applied.md` ledgers). Zero model tokens, no writes anywhere. Wire it into your weekly
report and run it by hand at review.

A NUMBER THAT ONLY COUNTS DECISIONS WOULD LIE BY OMISSION, so three separate
denominators are printed: pairs generated vs pairs judged (what the model budget never
looked at), confirmed contradictions vs decided ones (what the review never reached),
and cards vs cards ever touched (what retrieval never served).

THE TRIAGE STAGE ITSELF IS NOT PART OF THIS REPOSITORY — it is a deployment-specific
sweep that pairs cards and asks a model which of two contradicts the other. If you do
not run one, the ledger section reports that it found none and the BASE and REACH
sections stand on their own.
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P  # noqa: E402

ROW_RE = re.compile(
    r"^###\s+(K-\d{5})\s*[—–-]\s*action\s+\*{0,2}([A-Z_]+)\*{0,2}"
    r"[^\n]*?\(contradicted by\s+(K-\d{5})", re.M)
VERDICT_RE = re.compile(r"^ROW\s+(\d+):\s*(APPLY|REJECT|SKIP)\b", re.M)
GEN_RE = re.compile(r"candidate pairs generated:\s*\*{0,2}(\d+)", re.I)
SENT_RE = re.compile(r"sent to the model:\s*\*{0,2}(\d+)", re.I)
NOTADJ_RE = re.compile(r"not adjudicated:\s*\*{0,2}(\d+)", re.I)
CLUSTER_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s*[—–-]\s*(K-\d{5}.*)$", re.M)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CARD_ID_RE = re.compile(r"K-\d{5}")
INJECT_RE = re.compile(r"INJECT \[([^\]]*)\]")
TOP_RE = re.compile(r"top=(K-\d{5})")
STATUS_RE = re.compile(r"^status:\s*(\S+)", re.M)
LINKS_BLOCK_RE = re.compile(r"^links:\s*$(.*?)^(?:\S|\Z)", re.M | re.S)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def triage_files(root, since_days):
    """-> (proposals {date: [paths]}, applied {date: [paths]}, n_preview)."""
    pats = [os.path.join(root, "memory", "inbox", "*-triage*.md"),
            # a deployment may move processed inboxes into the archive; both
            # shapes are supported so this tool does not go blind the day that
            # happens.
            os.path.join(root, "memory", "archive", "inbox", "**",
                         "*-triage*.md")]
    proposals, applied, preview = {}, {}, 0
    cutoff = None
    if since_days:
        cutoff = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    for pat in pats:
        for path in sorted(glob.glob(pat, recursive=True)):
            base = os.path.basename(path)
            m = DATE_RE.search(base)
            date = m.group(1) if m else "?"
            if cutoff and date != "?" and date < cutoff:
                continue
            if base.endswith("-triage.preview.md"):
                preview += 1
            elif base.endswith("-triage.applied.md"):
                applied.setdefault(date, []).append(path)
            else:
                proposals.setdefault(date, []).append(path)
    return proposals, applied, preview


def scan_triage(proposals, applied):
    out = {"rows": 0, "applied": 0, "rejected": 0, "skipped": 0,
           "generated": 0, "judged": 0, "unjudged": 0, "cov_date": "-",
           "clusters": {}, "undecided_dates": []}
    for date, paths in sorted(proposals.items()):
        rows = 0
        for path in paths:
            text = read(path)
            rows += len(ROW_RE.findall(text))
            # COVERAGE IS NOT SUMMABLE.  Every night regenerates the candidate
            # pairs over the whole base, so adding "not adjudicated" across
            # nights counts the same untouched pair once per night and inflates
            # the backlog by the number of files.  The newest file IS the
            # current backlog; older ones are its history.
            cov = {}
            for rx, key in ((GEN_RE, "generated"), (SENT_RE, "judged"),
                            (NOTADJ_RE, "unjudged")):
                m = rx.search(text)
                if m:
                    cov[key] = int(m.group(1))
            if cov and date >= out["cov_date"]:
                out["cov_date"] = date
                out.update(cov)
            for subject, ids in CLUSTER_RE.findall(text):
                members = set(CARD_ID_RE.findall(ids))
                cur = out["clusters"].get(subject, set())
                out["clusters"][subject] = cur | members
        out["rows"] += rows
        verdicts = 0
        for path in applied.get(date, []):
            text = read(path)
            for _n, kind in VERDICT_RE.findall(text):
                verdicts += 1
                out[{"APPLY": "applied", "REJECT": "rejected",
                     "SKIP": "skipped"}[kind]] += 1
        if rows > verdicts:
            out["undecided_dates"].append((date, rows - verdicts))
    out["undecided"] = sum(n for _d, n in out["undecided_dates"])
    return out


def scan_base(root):
    statuses, edges, ids = {}, {"supersedes": 0, "refuted_by": 0,
                               "contradicts": 0}, set()
    fog = 0
    for sub in (("memory", "know"), ("memory", "archive", "know")):
        for path in sorted(glob.glob(os.path.join(root, *sub, "K-*.md"))):
            text = read(path)
            cid = os.path.basename(path)[:-3]
            ids.add(cid)
            fog += ("archive" in sub)
            m = STATUS_RE.search(text)
            st = m.group(1).strip() if m else "?"
            statuses[st] = statuses.get(st, 0) + 1
            lb = LINKS_BLOCK_RE.search(text)
            block = lb.group(1) if lb else ""
            for key in edges:
                km = re.search(rf"^\s+{key}:\s*(.*)$", block, re.M)
                if km and CARD_ID_RE.search(km.group(1)):
                    edges[key] += len(CARD_ID_RE.findall(km.group(1)))
    return statuses, edges, ids, fog


def scan_reach(root, ids):
    """The audit's definition: a card is TOUCHED if it was ever injected or was
    ever a recall top hit."""
    touched = set()
    inj = read(os.path.join(root, "tools", ".inject.log"))
    for blob in INJECT_RE.findall(inj):
        touched |= set(CARD_ID_RE.findall(blob))
    touched |= set(TOP_RE.findall(inj))
    touched |= set(TOP_RE.findall(read(os.path.join(root, "tools",
                                                    ".recall.log"))))
    return touched & ids


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=P.ROOT,
                    help="KHMS base to report on (default: $KHMS_ROOT)")
    ap.add_argument("--since", type=int, default=0,
                    metavar="DAYS", help="only triage files this recent "
                                         "(0 = all of them)")
    ap.add_argument("--cluster-min", type=int, default=5,
                    help="report a subject cluster from this many cards up")
    a = ap.parse_args(argv)

    proposals, applied, n_preview = triage_files(a.root, a.since)
    t = scan_triage(proposals, applied)
    statuses, edges, ids, fog = scan_base(a.root)
    touched = scan_reach(a.root, ids)
    n_files = sum(len(v) for v in proposals.values())
    n_app = sum(len(v) for v in applied.values())

    window = f"last {a.since} days" if a.since else "all triage files"
    print(f"KHMS contradiction debt — {dt.date.today().isoformat()} ({window})")
    print()
    print("TRIAGE LEDGER (memory/inbox + memory/archive/inbox)")
    if not n_files:
        print("  no triage files found — either you do not run a contradiction "
              "sweep (it is not part of this repository) or its proposals live "
              "elsewhere. The two sections below do not depend on it.")
    print(f"  files: {n_files} triage · {n_app} applied"
          f"{f' · {n_preview} preview (ignored)' if n_preview else ''}")
    print(f"  confirmed contradictions: {t['rows']}")
    print(f"    applied:       {t['applied']}")
    print(f"    rejected:      {t['rejected']}")
    print(f"    skipped:       {t['skipped']}")
    print(f"    never decided: {t['undecided']}"
          + (f"  ({', '.join(f'{d}:{n}' for d, n in t['undecided_dates'][:6])})"
             if t["undecided_dates"] else ""))
    print(f"  pairs judged: {t['judged']} · unjudged: {t['unjudged']} "
          f"(generated: {t['generated']}) — newest sweep {t['cov_date']}")
    print("  a pair that was not adjudicated is NOT a pair that was cleared")
    print()
    print(f"BASE ({len(ids) - fog} in memory/know + {fog} in "
          f"memory/archive/know — the fog)")
    print("  cards: " + " · ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    print("  correction edges: " + " · ".join(f"{k} {v}"
                                              for k, v in sorted(edges.items())))
    hard = edges["supersedes"] + edges["refuted_by"]
    print(f"  DEBT: {t['rows']} confirmed contradictions vs {hard} "
          f"supersede/refute edges in the whole base")
    print()
    print("REACH (tools/.inject.log + tools/.recall.log — injected OR top hit)")
    n_never = len(ids) - len(touched)
    pct = (100.0 * n_never / len(ids)) if ids else 0.0
    print(f"  cards never touched: {n_never} / {len(ids)} ({pct:.1f} %)")
    print()
    big = sorted(((len(v), k) for k, v in t["clusters"].items()
                  if len(v) >= a.cluster_min), reverse=True)
    print(f"CLUSTERS (>= {a.cluster_min} cards on one subject, as the triage "
          f"files name them)")
    if not big:
        print("  none")
    for n, subject in big[:15]:
        print(f"  {n:3d}  {subject}")
    if len(big) > 15:
        print(f"  … +{len(big) - 15} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
