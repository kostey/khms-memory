#!/usr/bin/env python3
"""The withheld sections are review plumbing, and the approve stage must not read them.

`verify_relations.py` does not delete a candidate it rejects — it MOVES the whole block
into `## DROPPED (no valid RELATION)` or `## DEFERRED` so a human can rescue it. That is
only worth anything if the stage that WRITES cards refuses to read those sections: a
parser that scans the whole file would approve exactly the candidates the gate rejected,
and the gate would then be worse than nothing.

RED-first record: against the pre-change approve_inbox.py both tests below failed —
the dropped candidate was written into memory/know/ with a real id.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.abspath(__file__))
APPROVE = os.path.join(TOOLS, "approve_inbox.py")
VERIFY = os.path.join(TOOLS, "verify_relations.py")

CARD = """\
### %s: %s

```yaml
id: %s
type: fact
level: observation
status: active
tags: [testing]
scope: universal
evidence: measured
source: 'journal/2026-03-04.md line 1'
date: 2026-03-04
links:
  derived_from: []
  supports: []
  contradicts: []
  supersedes: null
  refuted_by: []
---
%s

**QUOTES:**
- src=journal :: %s
```
"""


class Withheld(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="withheld-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.know = os.path.join(self.root, "memory", "know")
        os.makedirs(self.know)
        os.makedirs(os.path.join(self.root, "tools"))
        self.counter = os.path.join(self.root, "tools", ".next_id")
        open(self.counter, "w").write("90500\n")
        self.inbox = os.path.join(self.root, "inbox.md")

    def approve(self):
        env = dict(os.environ, KHMS_ROOT=self.root)
        return subprocess.run([sys.executable, APPROVE, self.inbox],
                              capture_output=True, text=True, env=env)

    def write(self, text):
        open(self.inbox, "w", encoding="utf-8").write(text)

    def test_a_dropped_candidate_never_becomes_a_card(self):
        self.write(
            "## Cards\n\n"
            + CARD % ("C1", "kept", "C1", "the gateway reboots at 03:00 daily.",
                      "the gateway reboots at 03:00 daily.")
            + "\n## DROPPED (no valid RELATION)\n\n"
            + CARD % ("C2", "dropped", "C2", "the mast is four metres tall.",
                      "the mast is four metres tall."))
        r = self.approve()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        written = sorted(os.listdir(self.know))
        self.assertEqual(len(written), 1, f"expected one card, got {written}")
        body = open(os.path.join(self.know, written[0]), encoding="utf-8").read()
        self.assertIn("gateway reboots", body)
        self.assertNotIn("four metres", body)

    def test_a_deferred_candidate_never_becomes_a_card(self):
        self.write(
            "## Cards\n\n"
            + CARD % ("C1", "kept", "C1", "the gateway reboots at 03:00 daily.",
                      "the gateway reboots at 03:00 daily.")
            + "\n## DEFERRED\n\n"
            + CARD % ("C2", "deferred", "C2", "the mast is four metres tall.",
                      "the mast is four metres tall."))
        r = self.approve()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(os.listdir(self.know)), 1)

    def test_the_withholding_is_reported_not_silent(self):
        self.write(
            "## Cards\n\n"
            + CARD % ("C1", "kept", "C1", "the gateway reboots at 03:00 daily.",
                      "the gateway reboots at 03:00 daily.")
            + "\n## DROPPED (no valid RELATION)\n\n"
            + CARD % ("C2", "dropped", "C2", "the mast is four metres tall.",
                      "the mast is four metres tall."))
        r = self.approve()
        self.assertIn("withheld: 1 candidate", r.stdout)
        self.assertIn("DROPPED", r.stdout)

    def test_a_file_with_no_withheld_section_is_unaffected(self):
        self.write("## Cards\n\n"
                   + CARD % ("C1", "kept", "C1", "the gateway reboots at 03:00 daily.",
                             "the gateway reboots at 03:00 daily."))
        r = self.approve()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("withheld:", r.stdout)
        self.assertEqual(len(os.listdir(self.know)), 1)


class GateWritesWhatApproveRefusesToRead(unittest.TestCase):
    """End to end: the gate moves a candidate, the approve stage then ignores it."""

    def test_round_trip(self):
        root = tempfile.mkdtemp(prefix="roundtrip-")
        self.addCleanup(shutil.rmtree, root, True)
        know = os.path.join(root, "memory", "know")
        os.makedirs(know)
        os.makedirs(os.path.join(root, "tools"))
        open(os.path.join(root, "tools", ".next_id"), "w").write("90500\n")
        open(os.path.join(know, "K-90001.md"), "w").write(
            "---\nid: K-90001\nstatus: active\n---\nbody\n")
        inbox = os.path.join(root, "inbox.md")
        open(inbox, "w", encoding="utf-8").write(
            "## Cards\n\n"
            + CARD % ("C1", "has a relation", "C1",
                      "NEAREST: K-90001 (9.9)\nRELATION: supports K-90001\n"
                      "the gateway reboots at 03:00 daily.",
                      "the gateway reboots at 03:00 daily.")
            + CARD % ("C2", "no relation at all", "C2",
                      "the mast is four metres tall.",
                      "the mast is four metres tall."))
        g = subprocess.run([sys.executable, VERIFY, inbox, "--know", know,
                            "--archive", os.path.join(root, "nonexistent")],
                           capture_output=True, text=True)
        self.assertEqual(g.returncode, 1, g.stdout)          # 1 == something dropped
        self.assertIn("C2: DROP", g.stdout)
        text = open(inbox, encoding="utf-8").read()
        self.assertIn("## DROPPED (no valid RELATION)", text)
        self.assertIn("four metres", text, "the dropped candidate must still be there")

        env = dict(os.environ, KHMS_ROOT=root)
        r = subprocess.run([sys.executable, APPROVE, inbox],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cards = [f for f in os.listdir(know) if f != "K-90001.md"]
        self.assertEqual(len(cards), 1, f"expected exactly one new card, got {cards}")
        self.assertIn("gateway reboots",
                      open(os.path.join(know, cards[0]), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
