#!/usr/bin/env python3
"""KHMS recall CLI — hybrid (lexical + dense) retrieval, the default channel.

WHY THE DEFAULT AND NOT AN OPTION. The dense channel arrived as strictly opt-in, so the
path everybody actually used stayed lexical. What that cost was then measured: a card
warning about a coordinate-frame convention had been in the base for weeks and did not
fire in three separate incidents, because the operator asks questions in their own
language and the base is written in English. The lexical channel put it at rank 7; the
fused ranking puts it at rank 1. An English base queried in another language owes the
system a working cross-lingual channel, and that channel was switched off by default.
The opt-in WAS the defect.

  recall.sh <symptom/error/identifier ...>   hybrid (default)
  recall.sh --lexical <...>                  lexical only (also: KHMS_DENSE=0)

THE DENSE CHANNEL IS OPTIONAL AND BEST-EFFORT. It talks to a small embedding daemon over
a unix socket — one JSON line in, one out:

    -> {"q": "<query text>", "topn": 50}
    <- {"ok": true, "hits": [["K-NNNNN", 0.71], ["K-NNNNN", 0.63], ...]}
       {"ok": false, "err": "<why>"}

Anything that speaks that protocol works; point KHMS_DENSE_SOCKET at it. If the socket is
missing, the daemon is wedged, or it is slower than KHMS_DENSE_TIMEOUT_MS (default 150 ms),
this falls back to pure lexical — WITH THE FALL-BACK SAID OUT LOUD, in the first printed
line and in the recall log's `src=` field, because a silent fall-back to the channel that
caused the miss is exactly the failure this change is about. With no daemon at all you get
today's lexical behaviour and one honest line saying so.

FUSION: reciprocal rank fusion, score = sum over channels of 1/(k + rank), k=60.
Rank-based, so the two channels' incomparable score scales (idf sums of 10-60 against
cosines of 0-1) never need calibrating. Both candidate lists are capped (top CAND each),
and that cap is what stops the dense channel from flooding an exact-identifier query where
lexical is already right — the specific risk of adding embeddings is that they blur the
exact token that IS the signal.

LIBRARY CONTRACT. `dense_query`, `rrf` and `hybrid_rank` are imported by other tools
(the hook's dense rescue, `nearest_cards.py`, the evaluation harness); keep their
signatures. Only the CLI front end below is about presentation.

DEPTH-1 LINKS. Each of the top hits prints its immediate links — `↪ superseded_by K-x: …`
first — so a card that has been replaced or refuted says so without being opened. Depth 1
and top-N only: the point is to see that a hit is stale, not to walk the graph.
"""
import json
import math
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P    # noqa: E402
import khms_search as ks  # noqa: E402
RRF_K = float(os.environ.get("KHMS_RRF_K", "60"))
CAND = int(os.environ.get("KHMS_RRF_CAND", "50"))
TIMEOUT_MS = float(os.environ.get("KHMS_DENSE_TIMEOUT_MS", "150"))
# Whatever your daemon is; the name only labels the log line. In the reference
# deployment a multilingual embedding model beat an English-only one on cross-lingual
# recall@1 by 65 % to 20 %, which is the whole reason this channel exists.
MODEL = os.environ.get("KHMS_DENSE_MODEL", "dense")
SOCKET = os.environ.get("KHMS_DENSE_SOCKET",
                        os.path.join(P.TOOLS_DIR, f".embed-{MODEL}.sock"))

# Depth-1 links of the top hits, titles only.
LINK_TOPN = int(os.environ.get("KHMS_LINK_TOPN", "3"))
LINK_MAX = int(os.environ.get("KHMS_LINK_MAX", "6"))     # lines per card


def dense_query(text, topn=CAND, timeout_ms=TIMEOUT_MS, sock_path=SOCKET):
    """Returns (hits, err, ms). hits = [(card_id, cosine)] or [] on any failure."""
    t0 = time.time()
    deadline = t0 + timeout_ms / 1000.0
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(max(0.001, deadline - time.time()))
        s.connect(sock_path)
        s.sendall((json.dumps({"q": text, "topn": topn}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            s.settimeout(max(0.001, deadline - time.time()))
            chunk = s.recv(65536)
            if not chunk:
                return [], "eof", (time.time() - t0) * 1000
            buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        if not resp.get("ok"):
            return [], resp.get("err", "not-ok"), (time.time() - t0) * 1000
        return [(h[0], float(h[1])) for h in resp["hits"]], None, (time.time() - t0) * 1000
    except (OSError, socket.timeout, ValueError, KeyError) as e:
        return [], f"{type(e).__name__}", (time.time() - t0) * 1000
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass


def rrf(lex_ids, dense_ids, k=RRF_K):
    """lex_ids/dense_ids: ranked card-id lists. Returns {id: fused_score}."""
    fused = {}
    for rank, cid in enumerate(lex_ids, 1):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
    for rank, cid in enumerate(dense_ids, 1):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank)
    return fused


def hybrid_rank(query_raw, cards=None, topn=8, exclude_meta=False,
                timeout_ms=TIMEOUT_MS, sock_path=SOCKET, rrf_k=RRF_K, cand=CAND):
    """Returns (ranked, meta). ranked = [(fused_score, matched_tokens, card)],
    same shape khms_search.search returns, so printers/consumers are unchanged."""
    if cards is None:
        cards = ks.load_cards()
    expanded, added = ks.expand_query(query_raw, ks.load_glossary())
    lex, _ = ks.search(expanded, cards=cards, exclude_meta=exclude_meta, topn=cand)
    dense, err, ms = dense_query(query_raw, topn=cand, timeout_ms=timeout_ms,
                                 sock_path=sock_path)
    meta = {"glossary": added, "dense_ms": ms, "dense_err": err,
            "dense_n": len(dense), "mode": "lexical" if err else "hybrid"}
    if err or not dense:
        return lex[:topn], meta

    by_id = {c["id"]: c for c in cards}
    pool = {c["id"]: (s, m) for s, m, c in lex}
    fused = rrf([c["id"] for _s, _m, c in lex], [d[0] for d in dense], k=rrf_k)
    out = []
    for cid, fs in sorted(fused.items(), key=lambda kv: -kv[1]):
        c = by_id.get(cid)
        if c is None:
            continue
        if exclude_meta and cid not in pool and ({"khms", "core"} & set(c["tags"])):
            continue
        out.append((fs, pool.get(cid, (0.0, []))[1], c))
        if len(out) >= topn:
            break
    return out, meta


def lexical_rank(query_raw, cards=None, topn=8, exclude_meta=False):
    """The opt-out channel, with the same return shape as hybrid_rank."""
    if cards is None:
        cards = ks.load_cards()
    expanded, added = ks.expand_query(query_raw, ks.load_glossary())
    lex, _ = ks.search(expanded, cards=cards, exclude_meta=exclude_meta, topn=topn)
    meta = {"glossary": added, "dense_ms": 0.0, "dense_err": "opt-out",
            "dense_n": 0, "mode": "lexical"}
    return lex, meta


# ------------------------------------------------------------- depth-1 links
def link_maps(cards):
    """Reverse edges the frontmatter does not carry: who supersedes / who
    contradicts this card. One pass over the loaded cards, no file I/O."""
    superseded_by, contradicted_by = {}, {}
    for c in cards:
        links = c.get("links") or {}
        sup = links.get("supersedes")
        for tgt in (sup if isinstance(sup, list) else [sup] if sup else []):
            superseded_by.setdefault(tgt, []).append(c["id"])
        for tgt in (links.get("contradicts") or []):
            contradicted_by.setdefault(tgt, []).append(c["id"])
    return superseded_by, contradicted_by


def link_lines(c, by_id, superseded_by, contradicted_by, limit=LINK_MAX):
    """Depth-1 links of one card as printable lines, warnings first."""
    links = c.get("links") or {}
    sup = links.get("supersedes")
    kinds = [
        ("refuted_by", links.get("refuted_by") or []),
        ("superseded_by", superseded_by.get(c["id"], [])),
        ("contradicted_by", contradicted_by.get(c["id"], [])),
        ("supersedes", sup if isinstance(sup, list) else [sup] if sup else []),
        ("contradicts", links.get("contradicts") or []),
        ("derived_from", links.get("derived_from") or []),
        ("supports", links.get("supports") or []),
    ]
    out, seen = [], set()
    for kind, ids in kinds:
        for kid in ids:
            if not kid or kid == c["id"] or (kind, kid) in seen:
                continue
            seen.add((kind, kid))
            other = by_id.get(kid)
            title = other["first"][:100] if other else "(not in the base)"
            out.append(f"    ↪ {kind} {kid}: {title}")
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- CLI output
def print_results(results, cards, channel, meta, show_links=True):
    fused = channel == "hybrid"
    by_id = {c["id"]: c for c in cards}
    superseded_by, contradicted_by = link_maps(cards) if show_links else ({}, {})
    df = {t: sum(1 for c in cards if t in c["nbody"])
          for r in results for t in r[1]}
    for i, (score, matched, c) in enumerate(results):
        flags = []
        if c["status"] in ("refuted", "challenged"):
            flags.append("⛔" + c["status"].upper())
        elif c["status"] != "active":
            flags.append(c["status"])
        if "gotcha" in c["tags"]:
            flags.append("⚠gotcha")
        if c["fog"]:
            flags.append("fog")
        flag = (" [" + ",".join(flags) + "]") if flags else ""
        val = f"{score:.4f}" if fused else f"{score:.1f}"
        print(f"{c['id']}{flag} ({val}): {c['first'][:110]}")
        rare = min(sorted(matched), key=lambda t: df.get(t, 0)) if matched else None
        if rare:
            for line in c["body"].splitlines()[1:]:
                if rare in ks.norm(line):
                    print(f"    ↳ {line.strip()[:150]}")
                    break
        if show_links and i < LINK_TOPN:
            for ln in link_lines(c, by_id, superseded_by, contradicted_by):
                print(ln)


def parse_argv(argv, env=None):
    """(query_words, lexical_only). `--dense` is accepted and ignored."""
    env = os.environ if env is None else env
    lexical_only = env.get("KHMS_DENSE", "") == "0"
    words = []
    for a in argv:
        if a == "--dense":
            continue
        if a in ("--lexical", "--no-dense"):
            lexical_only = True
            continue
        words.append(a)
    return words, lexical_only


def main(argv):
    words, lexical_only = parse_argv(argv)
    query_raw = " ".join(words)
    if not query_raw.strip():
        print("usage: recall.sh [--lexical] <symptom/error/identifier ...>",
              file=sys.stderr)
        return 2
    cards = ks.load_cards()

    if lexical_only:
        results, meta = lexical_rank(query_raw, cards=cards)
        channel, src = "lexical(opt-out)", "cli:lexical:optout"
    else:
        results, meta = hybrid_rank(query_raw, cards=cards)
        if meta["dense_err"]:
            channel, src = "lexical(fallback: daemon down)", "cli:lexical:fallback"
        else:
            channel, src = "hybrid", f"cli:hybrid:{MODEL}"
    ks.log_query(query_raw, results, src=src)

    if meta["glossary"]:
        print(f"(glossary: +{' '.join(meta['glossary'])[:120]})")
    if channel == "hybrid":
        print(f"(channel=hybrid | dense {MODEL} {meta['dense_ms']:.0f} ms "
              f"| RRF k={rrf_k_str()})")
    elif channel.startswith("lexical(fallback"):
        print(f"(channel=lexical(fallback: daemon down) "
              f"[{meta['dense_err']}, {meta['dense_ms']:.0f} ms])")
    else:
        print("(channel=lexical(opt-out: --lexical / KHMS_DENSE=0))")

    if not ks.tokens(query_raw):
        print("recall: query has no searchable tokens")
        return 2
    if not results:
        print(f"recall: nothing on record for: {query_raw}")
        return 1
    print_results(results, cards, "hybrid" if channel == "hybrid" else "lexical", meta)
    return 0


def rrf_k_str():
    return f"{RRF_K:g}"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
