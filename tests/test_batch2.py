"""Tests for confidence/feedback, handoffs and the notes inbox."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_anchormem import RepoCase  # noqa: E402

from anchormem import confidence, handoff, hooks, inbox, store  # noqa: E402
from anchormem.lint import lint  # noqa: E402


class TestConfidence(unittest.TestCase):
    def test_wilson_needs_several_reports(self):
        self.assertAlmostEqual(confidence.wilson_lower(0, 0), 0.0)
        one, five = confidence.wilson_lower(1, 1), confidence.wilson_lower(5, 5)
        self.assertLess(one, 0.3)
        self.assertGreater(five, 0.5)
        self.assertLess(confidence.wilson_lower(3, 6), confidence.wilson_lower(6, 6))

    def test_freshness_decays(self):
        m = store.Memory(id="x", title="x", verified="2026-01-01", extra={"applied": "8", "last_evidence": "2026-01-01"})
        fresh = confidence.score(m, dt.date(2026, 1, 2))
        old = confidence.score(m, dt.date(2026, 5, 1))
        gone = confidence.score(m, dt.date(2027, 1, 1))
        self.assertGreater(fresh, old)
        self.assertEqual(gone, 0.0)
        self.assertEqual(confidence.band(fresh), "high")
        self.assertIsNone(confidence.score(store.Memory(id="y", title="y")))


class TestFeedback(RepoCase):
    def setUp(self):
        super().setUp()
        self.run_cli("init")
        self.run_cli("add", "Pool capped at 10", "-a", "src/db/pool.py#POOL_SIZE", "-b", "Plan limit.", "--id", "pool")

    def test_applied_and_violated_counts_and_ordering(self):
        for _ in range(4):
            self.assertEqual(self.run_cli("feedback", "pool", "applied")[0], 0)
        _, out, _ = self.run_cli("feedback", "pool", "violated", "--note", "user raised it")
        self.assertIn("4 applied, 1 violated", out)
        mem = store.load(self.root, "pool")
        self.assertEqual((mem.extra["applied"], mem.extra["violated"]), ("4", "1"))
        _, out, _ = self.run_cli("context", "src/db/pool.py")
        self.assertIn("confidence", out)
        self.assertEqual(self.check_json()[0], 0)  # feedback alone doesn't make a memory stale

    def test_outdated_flags_until_verify(self):
        self.run_cli("feedback", "pool", "outdated", "--note", "we upgraded the plan")
        code, mems = self.check_json()
        self.assertEqual((code, mems["pool"]["status"]), (1, "flagged"))
        self.assertIn("we upgraded the plan", mems["pool"]["flagged"])
        _, out, _ = self.run_cli("context", "src/db/pool.py")
        self.assertIn("[STALE: FLAGGED]", out)
        _, out, _ = self.run_cli("check")
        self.assertIn("FLAGGED", out)
        self.run_cli("verify", "pool")
        self.assertEqual(self.check_json()[0], 0)
        self.assertIn("cleared outdated report", (self.root / ".anchormem/log.md").read_text())

    def test_often_violated_lint(self):
        for _ in range(2):
            self.run_cli("feedback", "pool", "violated")
        self.assertIn("often-violated", {f.code for f in lint(self.root, store.load_all(self.root))})


class TestHandoffs(RepoCase):
    def setUp(self):
        super().setUp()
        self.run_cli("init")
        self.commit("init")

    def test_new_defaults_to_uncommitted_files_and_take_once(self):
        self.write("src/db/pool.py", "POOL_SIZE = 25\n")
        self.write("src/db/new_thing.py", "x = 1\n")
        code, out, _ = self.run_cli("handoff", "new", "Raised", "the", "pool", "-n", "update docs", "-q", "ok for prod?",
                                    "--from", "claude-code", "--to", "codex")
        self.assertEqual(code, 0, out)
        h = handoff.load_all(self.root)[0]
        self.assertEqual(sorted(p for p, _ in h.files), ["src/db/new_thing.py", "src/db/pool.py"])
        self.assertEqual((h.summary, h.next_steps, h.questions, h.to), ("Raised the pool", ["update docs"], ["ok for prod?"], "codex"))

        _, out, _ = self.run_cli("handoff", "take", "--as", "claude-code")
        self.assertIn("no pending handoffs", out)  # addressed to codex

        self.write("src/db/pool.py", "POOL_SIZE = 30\n")
        _, out, _ = self.run_cli("handoff", "take", "--as", "codex", "--peek")
        self.assertIn("src/db/pool.py (changed since the handoff)", out)
        self.assertIn("src/db/new_thing.py (unchanged)", out)
        self.run_cli("handoff", "take", "--as", "codex")
        self.assertEqual(handoff.load_all(self.root)[0].status, "accepted")
        _, out, _ = self.run_cli("handoff", "take", "--as", "codex")
        self.assertIn("no pending handoffs", out)
        _, out, _ = self.run_cli("handoff", "list", "--all")
        self.assertIn("taken", out)

    def test_roundtrip_and_expiry(self):
        h = handoff.create(self.root, "Summary line\nmore", "claude-code", files=["README.md"], days=7)
        back = handoff.parse(h.to_text())
        self.assertEqual((back.summary, back.files, back.expires), (h.summary, h.files, h.expires))
        h.expires = "2000-01-01"
        handoff.save(self.root, h)
        self.assertEqual(handoff.load_all(self.root)[0].status, "expired")

    def test_session_start_takes_and_reshows_after_compact(self):
        self.run_cli("handoff", "new", "Half way through billing", "-n", "finish tests", "-f", "README.md")
        first = hooks.session_start_text({"cwd": str(self.root), "session_id": "s1", "source": "startup"})
        self.assertIn("left a handoff for you", first)
        self.assertIn("finish tests", first)
        self.assertEqual(handoff.load_all(self.root)[0].accepted_by, "claude-code")
        again = hooks.session_start_text({"cwd": str(self.root), "session_id": "s1", "source": "compact"})
        self.assertIn("Half way through billing", again)
        other = hooks.session_start_text({"cwd": str(self.root), "session_id": "s2", "source": "startup"})
        self.assertNotIn("Half way through billing", other)


class TestInbox(RepoCase):
    def setUp(self):
        super().setUp()
        self.run_cli("init")

    def test_notes_promote_after_three_similar(self):
        a = "src/db/pool.py#POOL_SIZE"
        self.run_cli("note", a, "pool size is tied to the postgres plan")
        self.run_cli("note", a, "unrelated: this file has no tests")
        _, out, _ = self.run_cli("note", a, "postgres plan limits the pool size")
        self.assertNotIn("keeps coming up", out)
        _, out, _ = self.run_cli("note", a, "pool size limited by postgres plan connections")
        self.assertIn("keeps coming up", out)
        self.run_cli("note", "src/billing/invoice.ts#lineTotal", "pool size postgres plan")  # other anchor

        _, out, _ = self.run_cli("promote")
        self.assertIn("candidate", out)
        self.assertEqual(store.load_all(self.root), [])  # dry run
        self.run_cli("promote", "--apply")
        mems = store.load_all(self.root)
        self.assertEqual(len(mems), 1)
        m = mems[0]
        self.assertEqual((m.origin, str(m.anchors[0][0])), ("agent", a))
        self.assertIn("Noted 3 times", m.body)
        left = inbox.load(self.root)
        self.assertEqual(len(left), 2)
        self.assertIn("unrelated: this file has no tests", [n["text"] for n in left])

    def test_old_notes_and_bad_lines_are_ignored(self):
        f = self.root / store.INBOX
        old = {"ts": "2020-01-01T00:00:00", "anchor": "README.md", "text": "same thing", "by": "agent"}
        f.write_text("\n".join([json.dumps(old)] * 3 + ["{broken"]) + "\n")
        self.assertEqual(len(inbox.load(self.root)), 3)
        self.assertEqual(inbox.candidates(inbox.load(self.root)), [])

    def test_note_rejects_bad_anchor(self):
        code, _, err = self.run_cli("note", "src/db/pool.py#nope", "x")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
