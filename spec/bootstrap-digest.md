# Bootstrap digest — how to work with this knowledge base

Copy this into a `policy` card (`level: derived`, `tags: [khms, core]`,
`derived_from: [<the spec card's id>]`). `build_views.py` puts its body into `MEMORY.md` §1, so
this text is what every future session reads first. Keep it short enough to stay there.

---

RULE — how to work with this knowledge base:

- **Retrieval ladder, cheapest first:** `MEMORY.md` (already in context) →
  `memory/views/topics/<tag>.md` → `tools/precheck.sh <tags>` → `tools/recall.sh <free text>` /
  grep `memory/know/` → indexes → fog (`memory/archive/`, raw) → **external sources** (manuals,
  documentation, forums, the web).
- **External verification is mandatory** when asserting facts no `measured` card covers.
- **Before risky actions** (deletions, configuration changes, deployments, hardware writes):
  `tools/precheck.sh` with the relevant tags — it prints active policies, gotchas and refuted
  dead ends, at zero model cost.
- **Before root-causing, before stating a hypothesis, and before proposing a change:**
  `tools/recall.sh` with the artifact you actually have (the error text, the identifier, the
  odd number) or with the proposal itself. An automatic injection is a pointer, not an answer:
  if a card is relevant, open it whole and follow its links.
- **Before design or architecture work:** read the core cards (`MEMORY.md` §2) and the topic
  views of everything involved.
- **Capture as it happens:** append `MARK[kind] one line (sess id)` to today's journal on every
  decision, stated requirement, failed attempt and solved problem. A full inline card only for
  `measured` and immediately useful knowledge; the nightly sweep distills the rest.
- **Cards are immutable after approval:** a correction is a new card plus `supersedes` (body:
  `SUPERSEDES K-x BECAUSE: …`). Never edit, never delete. Refuted knowledge stays visible as a
  dead-end warning — that is what stops the third re-derivation.
- **Jobs only propose** (into `memory/inbox/`, with temporary labels). IDs come from
  `tools/.next_id` at review, and reviews end with a short report to the operator.
- **"Nothing on record" is a valid answer.** Say it instead of filling the gap with something
  that reads well.

LIMITS: the full definitions live in the spec card; this digest never overrides it.
