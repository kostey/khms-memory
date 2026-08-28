#!/usr/bin/env python3
"""Deterministic transcript digest for KHMS distillation. Zero model tokens.

Reads harness session transcripts in JSONL (plain or .gz) — the format Claude
Code writes under ~/.claude/projects/<slug>/<session>.jsonl — and keeps only what
a distillation stage can reason over:

  * user and assistant message text (truncated per message)
  * tool NAMES, in order (what was done, not what it returned)
  * error text from failed tool calls

Everything else — tool outputs, thinking blocks, images — stays in the raw layer.
That is the whole trick behind the sweep costing what it costs: the expensive
model never sees the megabytes, only the shape of the day plus its errors.

Usage: preprocess_transcripts.py [--since ISO_TS] <file-or-glob>... > digest.txt

--since drops messages already covered by the previous run, so a nightly run
distills only its own window. Pass the START time of the previous run, not its
finish: everything that happened DURING a long run belongs to this window, and
stamping the finish silently drops it.
"""
import glob
import gzip
import json
import sys

MSG_CHARS = 2000
ERR_CHARS = 300


def open_any(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def digest_file(path, out, since=""):
    sid = path.split("/")[-1].split(".")[0]
    out.write(f"\n===== SESSION {sid} =====\n")
    with open_any(path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") not in ("user", "assistant"):
                continue
            if since and str(obj.get("timestamp") or "") <= since:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            ts = (obj.get("timestamp") or "")[:16]
            kind = obj["type"].upper()
            if isinstance(content, str):
                if content.strip():
                    out.write(f"[{ts}] {kind}: {content.strip()[:MSG_CHARS]}\n")
                continue
            if not isinstance(content, list):
                continue
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type")
                if bt == "text":
                    txt = (blk.get("text") or "").strip()
                    if txt:
                        out.write(f"[{ts}] {kind}: {txt[:MSG_CHARS]}\n")
                elif bt == "tool_use":
                    out.write(f"[{ts}] TOOL: {blk.get('name', '?')}\n")
                elif bt == "tool_result" and blk.get("is_error"):
                    c = blk.get("content")
                    snippet = c if isinstance(c, str) else (
                        c[0].get("text", "") if isinstance(c, list) and c
                        and isinstance(c[0], dict) else "")
                    out.write(f"[{ts}] TOOL-ERROR: {snippet[:ERR_CHARS]}\n")


def main():
    args = sys.argv[1:]
    since = ""
    if "--since" in args:
        i = args.index("--since")
        since = args[i + 1]
        del args[i:i + 2]
    paths = []
    for pat in args:
        paths.extend(sorted(glob.glob(pat)))
    if not paths:
        print("usage: preprocess_transcripts.py [--since ISO_TS] <file-or-glob>...",
              file=sys.stderr)
        sys.exit(2)
    for p in paths:
        digest_file(p, sys.stdout, since)


if __name__ == "__main__":
    main()
