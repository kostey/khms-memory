# Example cards

A complete miniature base for a **fictional home weather station**: an outdoor sensor on a
serial link, a logger host writing CSV, a small daily summary job. Nothing here is real; the
domain exists so you can see every card shape, every level, and a supersession chain without
having to imagine them.

| File | Shows |
|---|---|
| `K-00002.md` | `requirement` — a stated need, with `DONE-CRITERIA` |
| `K-00003.md` | `fact` — exact values, `evidence: measured` |
| `K-00004.md` | `problem→solution` — a diagnosed symptom, tagged `gotcha` |
| `K-00005.md` | `action→outcome` — a **failed** attempt, which is knowledge |
| `K-00006.md` | `principle` — `level: derived`, induced from two observations |
| `K-00007.md` | `decision→rationale` — including what was rejected and why |
| `K-00008.md` | a `policy` that turned out to be wrong: `status: superseded` |
| `K-00009.md` | the `policy` that replaced it: `supersedes` + `SUPERSEDES … BECAUSE` |
| `journal-2026-03-04.md` | a day of MARK anchors as they are written during work |
| `inbox-2026-03-05.md` | what the nightly sweep proposes: temp labels, quotes, flags |

Read them in that order and the whole model is visible in ten minutes.

Things worth noticing, because they are the parts people get wrong:

- **K-00005 is a failure and it is kept.** "We tried the obvious thing and it broke this way" is
  among the most valuable content in a base, and it is exactly what a summarizing memory throws
  away first.
- **K-00006 states its own limits.** A pattern whose `LIMITS:` you cannot write is a pattern you
  have not found yet.
- **K-00008 is still there, in `know/`, marked `superseded`.** Nothing is deleted. The reason it
  was wrong is in K-00009's body, one line, checkable.
- **Nothing in these cards records the current value of a live tunable.** K-00009 says where the
  poll interval is configured, not what it is set to today — a card cannot notice when a value
  moves, and a card that claims a stale value with confidence is worse than no card.
- **Every observation has a `source` you could actually go and look at**, with a locating quote.
