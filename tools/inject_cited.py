#!/usr/bin/env python3
"""injected → cited: was a card the hook injected ever actually used?

The floor injects. Whether anything it injected was USED is a different question, and
in the reference deployment it had never been asked: 2755 injections in 30 days (2044
of them before tool calls, 502 on operator messages, 209 after tool results) and no
measurement of what any of them changed. Injection is not free — it spends context on
every message — so "does this event pay for itself?" has to be answerable per event
before the budget can be tuned. This answers it per injection.

    inject_cited.py --since 14
    inject_cited.py --since 14 --json
    inject_cited.py --inject-log F --transcripts DIR --since 9999   # fixtures

You need session transcripts for this: point `--transcripts` (or KHMS_TRANSCRIPTS) at
the directory where your harness writes them, one JSON-lines file per session.

METHOD — and what it cannot know:
  * .inject.log carries NO session id. A record is attributed to a transcript whose own
    first/last timestamps bracket it (± COVER_MARGIN). When two sessions were live at
    the same moment the record is `ambiguous` and counts as cited if it was cited in
    ANY of them: the citation rate is therefore an UPPER bound. Records with no
    covering transcript are `unattributed` and are NOT counted as uncited — they get
    their own line, because folding "we could not tell" into "it was not used" is how
    a measurement becomes an argument.
  * cited = an injected card id appears in one of the next <=3 assistant messages of
    that session (text, thinking or tool input — the whole JSON line is scanned). A
    card that changed a decision without being named is invisible here, so the number
    is a FLOOR on influence and a CEILING on nothing.
  * transcripts run to 100 MB+; this streams them line by line and keeps only
    (timestamp, card ids) per assistant message.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P  # noqa: E402

LOG = P.INJECT_LOG
# Where your harness keeps session transcripts (JSON lines, one file per session).
TRANSCRIPTS = os.environ.get("KHMS_TRANSCRIPTS", "")
LINE = re.compile(r"^(\S+) \| (\w+) \| q=(.*) \| nhits=(\d+) \| top=(\S+) \| (.*)$")
KID = re.compile(r"K-\d{5}")
COVER_MARGIN = 600.0        # s a session may start after / end before a record
TURNS = 3                   # assistant messages that count as "the next turns"
# Live transcripts are compact JSON ("k":"v"); a hand-written fixture is not.
# Match both rather than making the fixture the only shape this tool can read.
TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]{10,40})"')
ASSIST_RE = re.compile(r'"type"\s*:\s*"assistant"')


def load_injects(path, since_days):
    """(t, day, event, [K-ids]) for every INJECT line inside the window."""
    cutoff = (datetime.date.today()
              - datetime.timedelta(days=since_days)).isoformat()
    out = []
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError as exc:
        # A missing audit log is a state, not a crash — but it is never "0 % of
        # injections were used": say which file was not there.
        print(f"no injection log to read: {exc}")
        return out
    with f:
        for ln in f:
            if "| INJECT [" not in ln:
                continue
            if ln[:10] < cutoff:
                continue
            m = LINE.match(ln.rstrip("\n"))
            if not m:
                continue
            ts, event, _q, _n, _top, act = m.groups()
            ids = KID.findall(act.split("]")[0])
            if not ids:
                continue
            try:
                t = datetime.datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            out.append((t, ts[:10], event, ids))
    out.sort()
    return out


def scan_transcript(path, lo, hi):
    """[(t, {K-ids})] for the assistant messages of one session, plus the span
    the file covers. Streaming: one line at a time, nothing accumulated but the
    K-ids actually present."""
    msgs = []
    first = last = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if '"timestamp"' not in ln:
                continue
            m = TS_RE.search(ln)
            if not m:
                continue
            try:
                t = datetime.datetime.fromisoformat(
                    m.group(1).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            first = t if first is None else min(first, t)
            last = t if last is None else max(last, t)
            if not (lo <= t <= hi) or not ASSIST_RE.search(ln):
                continue
            msgs.append((t, set(KID.findall(ln))))
    msgs.sort(key=lambda r: r[0])
    return msgs, first, last


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=14, help="days")
    ap.add_argument("--inject-log", default=LOG)
    ap.add_argument("--transcripts", default=TRANSCRIPTS,
                    help="directory of session transcripts (JSON lines); "
                         "defaults to $KHMS_TRANSCRIPTS")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    injects = load_injects(a.inject_log, a.since)
    if not injects:
        print("no INJECT records in the window")
        return 0
    lo = injects[0][0] - COVER_MARGIN
    hi = injects[-1][0] + 3600.0

    sessions = {}
    if not a.transcripts:
        print("no transcript directory: pass --transcripts DIR or set "
              "KHMS_TRANSCRIPTS. Without transcripts there is nothing to check "
              "the injections against.")
        return 2
    for p in sorted(glob.glob(os.path.join(a.transcripts, "*.jsonl"))):
        if os.path.getmtime(p) < lo - 86400:
            continue
        msgs, first, last = scan_transcript(p, lo, hi)
        if first is None:
            continue
        sessions[os.path.basename(p)[:-6]] = (first, last, msgs)

    by_event = collections.defaultdict(lambda: [0, 0])
    by_day = collections.defaultdict(lambda: [0, 0])
    unattributed = ambiguous = 0
    for t, day, event, ids in injects:
        covering = [s for s in sessions.values()
                    if s[0] - COVER_MARGIN <= t <= s[1] + COVER_MARGIN]
        if not covering:
            unattributed += 1
            continue
        if len(covering) > 1:
            ambiguous += 1
        cited = False
        for _first, _last, msgs in covering:
            nxt = [m for m in msgs if m[0] > t][:TURNS]
            if any(set(ids) & kids for _ts, kids in nxt):
                cited = True
                break
        by_event[event][0] += 1
        by_day[day][0] += 1
        if cited:
            by_event[event][1] += 1
            by_day[day][1] += 1

    inj = sum(v[0] for v in by_event.values())
    cit = sum(v[1] for v in by_event.values())
    rep = {
        "window_days": a.since, "sessions_scanned": len(sessions),
        "total": {"injected": inj, "cited": cit,
                  "pct": round(100.0 * cit / max(1, inj), 1),
                  "unattributed": unattributed, "ambiguous": ambiguous},
        "by_event": {k: {"injected": v[0], "cited": v[1],
                         "pct": round(100.0 * v[1] / max(1, v[0]), 1)}
                     for k, v in sorted(by_event.items())},
        "by_day": {k: {"injected": v[0], "cited": v[1],
                       "pct": round(100.0 * v[1] / max(1, v[0]), 1)}
                   for k, v in sorted(by_day.items())},
    }
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(f"# injected → cited — last {a.since} d, {len(sessions)} transcripts, "
          f"{inj} attributed injections "
          f"({unattributed} unattributed, {ambiguous} ambiguous)")
    print(f"{'event':<22}{'injected':>9}{'cited':>7}{'%':>7}")
    for k, v in rep["by_event"].items():
        print(f"{k:<22}{v['injected']:>9}{v['cited']:>7}{v['pct']:>6}%")
    print(f"{'TOTAL':<22}{inj:>9}{cit:>7}{rep['total']['pct']:>6}%")
    print(f"{'day':<22}{'injected':>9}{'cited':>7}{'%':>7}")
    for k, v in rep["by_day"].items():
        print(f"{k:<22}{v['injected']:>9}{v['cited']:>7}{v['pct']:>6}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
