# Measuring the retrieval floor — how to find out whether the hook is paying for itself

Every parameter in `khms_hook.py` is configuration, not law (spec §7). This file is about the
number underneath all of them, which is easy never to ask for: **of everything the hook
injected, how much was used?**

In the reference deployment that question went unasked for months. When it was finally
measured, the answer was uncomfortable enough to change the wiring:

| what was measured | number |
|---|---|
| injections in 30 days | 2755 — 2044 before tool calls, 502 on operator messages, 209 after tool results |
| share of the cards the agent actually cited that came from an injection | **≤ 16 %** |
| relevance of what was injected, judged by hand on a day's sample | **~50 %** |
| injections whose card appeared in one of the next three assistant messages | **6.6 %** overall: 23.2 % on operator messages, 2.8 % before tool calls, **0 of 36** after tool results |
| hook calls per day that loaded the whole card base and then dropped the call on a budget gate | **2324** (~172 ms each) |
| share of the retrieval log that was the hook recording its own non-retrievals | **87 %** |

None of that says retrieval does not work. It says these particular EVENTS, at these
thresholds, on a base of this size, mostly did not — and that the cost was being paid anyway,
on every message and every tool call.

## The tools

- **`tools/inject_cited.py`** — injected → cited, per event and per day. It reads
  `tools/.inject.log` and your session transcripts and asks, for every injection, whether one
  of the next three assistant messages names the card. Point `--transcripts` (or
  `KHMS_TRANSCRIPTS`) at the directory your harness writes them to.
  Read its docstring before quoting a number from it: the citation rate is an UPPER bound
  where sessions overlap, a FLOOR on influence (a card that changed a decision without being
  named is invisible), and records it cannot attribute to a session are reported separately
  rather than counted as unused. Folding "we could not tell" into "it was not used" is how a
  measurement turns into an argument.
- **`tools/khms_debt.py`** — the other half of the same question: how much of the base has
  never been injected and has never been a recall top hit. In the reference deployment 43 % of
  cards had never been either.
- **`tools/.inject.log`** — every decision, including the silences and the reason
  (`dedup`, `cap`, `threshold`). Without it none of the above is computable after the fact.

## Running an experiment

`tools/khms_experiment.json` (example: `tools/khms_experiment.example.json`) switches card
injection off per event, and the hook stamps each log line with the regime that produced it,
so a week's records say which week they belong to:

```json
{
  "since": "2026-03-01",
  "name": "no-ups",
  "inject": {"UserPromptSubmit": false, "PostToolUse": false,
             "PostToolUseFailure": false, "PreToolUse": true},
  "claim_gate": true,
  "review_on": "2026-03-08",
  "why": "one line, so that in a week you know what you were asking"
}
```

Rules that make it an experiment rather than a change of mind:

1. **Fail open in both directions.** A missing, empty or broken file means "behave exactly as
   before". Deleting the file is therefore the complete rollback, and no code changes back.
2. **Write the baseline down BEFORE the switch, with its window.** A comparison against a
   number you reconstruct afterwards from file mtimes is not a comparison.
3. **Name the review date in the file itself**, and the decision rule with it. "We will look at
   it in a week" without a rule becomes "we got used to it".
4. **Check that your metric has a baseline at all.** In the reference deployment the primary
   metric — a marker written into the journal on every correction — had only come into use two
   days before the experiment started, so a week of it would have measured journalling habits
   rather than errors. The fix was a second, objective metric that does not depend on the agent
   writing anything down: corrections in the operator's own messages, counted from the message
   archive. If your metric can go to zero because a channel went quiet, it is not measuring
   what you think.
5. **Two metrics that are complements, read together.** One of them counts what the agent
   admits; the other counts what the operator had to correct. A day with many of the first and
   none of the second is usually a day that ran in a channel the second cannot see — not a
   clean day.

## What the reference deployment did with the answer

`PostToolUse` and `PostToolUseFailure` came OUT of the wiring (see
[claude-code/README.md](../claude-code/README.md)) — 0 of 36 of their injections were ever
cited, and an error string being "a near-perfect query" turned out to be an argument, not a
measurement. `UserPromptSubmit` injection went off for one week as an experiment, with the
directive lines kept: the experiment removes the CARDS, not the duty to say where an answer
came from. `PreToolUse` stayed on, and so did the automatic `precheck.sh` for the risky-command
class, which is not part of the experiment — that path exists for the command that is about to
delete something, and its value does not depend on citation counts.

**Before you copy any of this: those numbers are one deployment's, on a base of ~2400 cards,
with one operator.** The method transfers. The conclusion does not.
