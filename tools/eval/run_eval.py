#!/usr/bin/env python3
"""Retrieval evaluation on a frozen golden set — does recall still do its job?

    run_eval.py --prod                       # score the production path, one line
    run_eval.py --prod --channel lexical     # the same rows, lexical only
    run_eval.py --prod --gold my_set.jsonl --k 5

WHY A GOLDEN SET AND NOT A FEELING. Every retrieval change is an improvement in the
eyes of whoever made it. The only thing that survives that is a frozen set of
(query, expected card) pairs that a change either still answers or does not — and
the pairs worth having are the REAL MISSES: the times the base held the answer and
retrieval did not produce it. Write one row per logged miss, with the id of the card
that should have come back, and the set becomes the record of every hole retrieval
has actually fallen into.

  {"id": "G1", "split": "eval", "source": "miss", "gate": true, "k": 3,
   "labels": ["symptom"], "query": "<what was actually typed>",
   "gold": ["K-NNNNN"], "note": "<why this row exists>"}

`gate: true` means a regression here fails the run (exit 1). `gate: false` means the
row is OPEN DEBT — a miss nothing currently fixes, counted and named but not fatal.
That distinction is the point: a permanently red gate is a gate nobody reads, so a
case no channel can retrieve yet must not be allowed to make every future run red.
Promote a row to gated the day something is supposed to have fixed it.

WHAT `--prod` MEASURES. It scores through exactly the code path `recall.sh` runs, so
the number describes the retrieval an agent actually gets — not a ranking that only
exists inside this script. It loads no model and needs no extra dependency; if you
run a dense daemon it is used, and if you do not, the run is lexical and says so.

The shipped rows live in `examples/golden_set.jsonl` and point at the fictional cards
next to them, so the harness can be run and tested before you have a base of your own.
Write your own file and pass `--gold`; that file is the deliverable, this one only
proves the harness runs.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
import khms_search as ks              # noqa: E402
import khms_recall_hybrid as hyb      # noqa: E402

# The shipped set scores against the fictional cards in examples/, and lives there
# with them: it is example DATA, not tooling. Point --gold at your own file — that
# one is the deliverable, this one only proves the harness runs.
GOLD = os.path.join(os.path.dirname(TOOLS), "examples", "golden_set.jsonl")
CAND = 50


def load_gold(path, split=None):
    rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    return [r for r in rows if split is None or r.get("split") == split]


def lexical_rank(query, cards, glossary, topn=CAND):
    expanded, _ = ks.expand_query(query, glossary)
    res, _ = ks.search(expanded, cards=cards, topn=topn)
    return [c["id"] for _s, _m, c in res]


def hit(ranked, gold, k):
    return any(g in ranked[:k] for g in gold)


def cmd_prod(a):
    rows = load_gold(a.gold, None if a.split == "all" else a.split)
    if not rows:
        print(f"EVAL: no rows in {a.gold} for split={a.split}")
        return 2
    cards = ks.load_cards()
    if not cards:
        print("EVAL: no cards loaded — wrong KHMS_ROOT? A golden set scored against "
              "an empty base reports a perfect failure and means nothing.")
        return 2
    glossary = ks.load_glossary()
    hits, per_row = 0, []
    for r in rows:
        k = int(r.get("k", a.k))
        if a.channel == "lexical":
            ranked = lexical_rank(r["query"], cards, glossary, topn=max(k, CAND))
            channel = "lexical"
        else:
            res, meta = hyb.hybrid_rank(r["query"], cards=cards, topn=max(k, CAND))
            ranked = [c["id"] for _s, _m, c in res]
            channel = meta["mode"]
        ok = hit(ranked, r["gold"], k)
        hits += ok
        rank = next((i + 1 for i, c in enumerate(ranked) if c in r["gold"]), None)
        per_row.append((r, ok, rank, channel))

    miss_rows = [(r, ok, rank) for r, ok, rank, _c in per_row
                 if r.get("source") == "miss"]
    failed = [(r, rank) for r, ok, rank in miss_rows if not ok]
    gated_fail = [(r, rank) for r, rank in failed if r.get("gate")]
    ch = per_row[0][3] if per_row else a.channel

    print(f"EVAL recall@{a.k}: {hits}/{len(rows)} (misses: {len(failed)})"
          f"   [channel={ch}, split={a.split}, miss-cases={len(miss_rows)}, "
          f"gated-fail={len(gated_fail)}]")
    for r, rank in failed:
        print(f"  MISS {r['id']} {'GATE' if r.get('gate') else 'open'}: "
              f"{r['gold']} at rank {rank if rank else '>' + str(CAND)} — "
              f"{r['query'][:70]!r}")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"channel": ch, "split": a.split, "k": a.k,
                       "hits": hits, "n": len(rows),
                       "rows": [{"id": r["id"], "ok": bool(ok), "rank": rank,
                                 "source": r.get("source"),
                                 "gate": r.get("gate", False)}
                                for r, ok, rank, _c in per_row]}, f, indent=1)
        print(f"wrote {a.json_out}")
    return 1 if gated_fail else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prod", action="store_true",
                    help="score through the production recall path (the only mode "
                         "here; a model-comparison harness is deployment-specific)")
    ap.add_argument("--channel", default="hybrid", choices=["hybrid", "lexical"])
    ap.add_argument("--k", type=int, default=3, help="recall@k")
    ap.add_argument("--split", default="eval")
    ap.add_argument("--gold", default=GOLD)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    if not a.prod:
        ap.error("pass --prod (see the module docstring)")
    return cmd_prod(a)


if __name__ == "__main__":
    sys.exit(main())
