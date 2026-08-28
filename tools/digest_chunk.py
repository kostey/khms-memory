#!/usr/bin/env python3
"""Split a large pipeline input into chunks a reading tool can actually read whole.

WHY: file-reading tools refuse oversized files and truncate oversized slices. A
stage handed one big file therefore reads a self-chosen PREFIX of it and reports
a perfectly normal run — measured once at 15 % of the intended input, with output
that looked fine. Unreadable input does not announce itself; that is the danger.

So: split on line boundaries with a margin under both caps, write the chunks next
to the original, and emit an index the driver pastes into the prompt as an
explicit "read every one of these, in order" list. The monolith stays on disk for
grepping.

Usage:
  digest_chunk.py INPUT OUTDIR BASENAME [--index INDEX_FILE]
                  [--max-bytes N] [--max-tokens N]

Defaults are conservative: 160 kB and ~17,000 estimated tokens per chunk (≈30 %
under the common 256 kB / 25,000-token limits). Tokens are estimated at 3.8
characters per token — an estimate, deliberately pessimistic; if your reader has
different limits, pass them.

Exit 0 and write NO index when the input already fits in one chunk: the caller
then passes the file as-is. Any failure here must be non-fatal for the caller —
falling back to the monolith is what it had before, and a chunker bug must not
cost a night's run.
"""
import argparse
import os
import sys

CHARS_PER_TOKEN = 3.8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("outdir")
    ap.add_argument("basename")
    ap.add_argument("--index")
    ap.add_argument("--max-bytes", type=int, default=160 * 1024)
    ap.add_argument("--max-tokens", type=int, default=17000)
    args = ap.parse_args()

    limit = min(args.max_bytes, int(args.max_tokens * CHARS_PER_TOKEN))
    size = os.path.getsize(args.input)
    if size <= limit:
        print(f"digest_chunk: {args.input} is {size} B, under {limit} B — no chunking")
        return 0

    os.makedirs(args.outdir, exist_ok=True)
    chunks, buf, buflen = [], [], 0
    with open(args.input, encoding="utf-8", errors="replace") as f:
        for line in f:
            if buflen + len(line) > limit and buf:
                chunks.append(buf)
                buf, buflen = [], 0
            buf.append(line)
            buflen += len(line)
    if buf:
        chunks.append(buf)

    paths = []
    width = max(2, len(str(len(chunks))))
    for i, c in enumerate(chunks, 1):
        p = os.path.join(args.outdir, f"{args.basename}-chunk{i:0{width}d}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(c)
        paths.append(p)

    if args.index:
        with open(args.index, "w", encoding="utf-8") as f:
            for i, p in enumerate(paths, 1):
                f.write(f"chunk {i}/{len(paths)}: {p}\n")
    print(f"digest_chunk: {args.input} ({size} B) -> {len(paths)} chunks of <= {limit} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
