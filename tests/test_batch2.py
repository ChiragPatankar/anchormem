"""Tests for confidence/feedback, handoffs and the notes inbox."""
import datetime as dt
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_anchormem import RepoCase, git  # noqa: E402

from anchormem import advice, codemap, confidence, dashboard, handoff, hooks, inbox, seed, store  # noqa: E402
from anchormem.cli import main  # noqa: E402
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


class TestDashboard(RepoCase):
    def test_dashboard_shows_real_state_and_escapes(self):
        self.run_cli("init")
        self.run_cli("add", "Pool <b>capped</b> at 10", "-a", "src/db/pool.py#POOL_SIZE", "-b", "Plan limit.", "--id", "pool")
        self.run_cli("add", "Rounding", "-a", "src/billing/invoice.ts#lineTotal", "-b", "Per line.", "--id", "round")
        self.write("src/db/pool.py", "POOL_SIZE = 25\n")
        self.run_cli("handoff", "new", "Half way", "-f", "README.md")
        self.run_cli("note", "README.md", "readme note")
        out = self.root / "dash.html"
        code, text, _ = self.run_cli("dashboard", "-o", str(out))
        self.assertEqual(code, 0, text)
        page = out.read_text(encoding="utf-8")
        self.assertIn("1 of 2 memories need a look.", page)
        self.assertIn("anchormem verify pool", page)  # the fix, with a copy button
        self.assertIn("Pool &lt;b&gt;capped&lt;/b&gt;", page)  # titles are escaped
        self.assertNotIn("<b>capped</b>", page)
        self.assertLess(page.index("Pool &lt;b&gt;"), page.index("Rounding"))  # stale first
        for bit in ("Half way", "readme note", "src/db/pool.py#POOL_SIZE", "add</b> pool"):
            self.assertIn(bit, page)
        self.run_cli("dashboard")
        self.assertTrue((self.root / ".anchormem/.state/dashboard.html").exists())


class TestFriendly(RepoCase):
    def test_status_before_and_after_init(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("anchormem init", out)
        self.run_cli("init")
        _, out, _ = self.run_cli("status")
        self.assertIn("No memories yet", out)
        self.assertIn("Save your first memory", out)
        self.run_cli("add", "Pool capped", "-a", "src/db/pool.py#POOL_SIZE", "-b", "x", "--id", "pool", "--by", "agent")
        self.write("src/db/pool.py", "POOL_SIZE = 25\n")
        _, out, _ = self.run_cli()
        self.assertIn("1 of 1 memories need a look", out)
        self.assertIn("anchormem verify pool", out)
        self.assertIn("anchormem confirm", out)
        self.assertIn("anchormem hooks install", out)

    def test_next_steps_cover_each_status(self):
        self.run_cli("init")
        self.run_cli("add", "Pool", "-a", "src/db/pool.py#POOL_SIZE", "-b", "x", "--id", "pool")
        self.run_cli("add", "Rounding", "-a", "src/billing/invoice.ts#lineTotal", "-b", "x", "--id", "round")
        self.commit("memories")
        git(self.root, "mv", "src/billing/invoice.ts", "src/billing/lines.ts")
        self.write("src/db/pool.py", "OTHER = 1\n")
        cmds = {st.memory: st.command for st in advice.snapshot(self.root).steps if st.memory}
        self.assertEqual(cmds["round"], "anchormem verify round --follow")
        self.assertTrue(cmds["pool"].startswith("anchormem retire pool"))

    def test_confirm_with_no_ids_walks_through_waiting_ones(self):
        self.run_cli("init")
        for t in ("One", "Two", "Three"):
            self.run_cli("add", t, "-a", "README.md", "-b", "x", "--by", "agent")
        self.run_cli("add", "Mine", "-a", "README.md", "-b", "x", "--by", "user")
        tty = io.StringIO("y\nn\nq\n")
        tty.isatty = lambda: True
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", lambda prompt="": tty.readline()):
            code, out, _ = self.run_cli("confirm")
        self.assertEqual(code, 0)
        self.assertIn("3 memories written by an agent", out)
        self.assertIn("(1/3)", out)
        self.assertIn("stopped", out)
        waiting = [m.id for m in store.load_all(self.root) if m.unconfirmed_agent]
        self.assertEqual(len(waiting), 2)
        self.assertEqual(self.run_cli("confirm", "--yes")[0], 0)
        _, out, _ = self.run_cli("confirm", "--yes")
        self.assertIn("Nothing to approve", out)

    def test_add_does_not_wait_on_an_open_empty_pipe(self):
        self.run_cli("init")
        r, w = os.pipe()
        reader = os.fdopen(r)
        try:
            with mock.patch.object(sys, "stdin", reader):
                start = time.time()
                code, _, _ = self.run_cli("add", "No body", "-a", "README.md")
            self.assertEqual(code, 0)
            self.assertLess(time.time() - start, 5)
        finally:
            reader.close()
            os.close(w)

    def test_live_server_rebuilds_on_change(self):
        self.run_cli("init")
        self.run_cli("add", "Pool", "-a", "src/db/pool.py#POOL_SIZE", "-b", "x", "--id", "pool")
        httpd = dashboard.make_server(self.root, 8800)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            get = lambda p: urllib.request.urlopen(base + p, timeout=10).read().decode()
            page, v1 = get("/"), get("/version")
            self.assertIn('data-live="1"', page)
            self.assertIn(f'data-version="{v1}"', page)
            self.assertEqual(get("/version"), v1)  # stable while nothing changes
            self.write("src/db/pool.py", "POOL_SIZE = 99\n")
            self.assertNotEqual(get("/version"), v1)
            self.assertIn("1 of 1 memories need a look", get("/"))
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestGraph(RepoCase):
    def test_graph_links_memories_to_code_and_files(self):
        self.run_cli("init")
        self.run_cli("add", "Pool", "-a", "src/db/pool.py#POOL_SIZE", "-b", "x", "--id", "pool")
        self.run_cli("add", "Whole file", "-a", "src/db/pool.py", "-b", "x", "--id", "file")
        self.run_cli("add", "Rounding", "-a", "src/billing/invoice.ts#lineTotal", "-a", "src/db/pool.py#POOL_SIZE",
                     "-b", "x", "--id", "round")
        self.write("src/db/pool.py", "POOL_SIZE = 99\n")
        snap = advice.snapshot(self.root)
        g = dashboard.graph_data(snap.results)
        nodes = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(sorted(k for k in nodes if k.startswith("f:")), ["f:src/billing/invoice.ts", "f:src/db/pool.py"])
        self.assertEqual(nodes["a:src/db/pool.py#POOL_SIZE"]["count"], 2)  # shared by two memories
        self.assertEqual(nodes["f:src/db/pool.py"]["status"], "changed")   # worst status wins
        self.assertEqual(nodes["f:src/billing/invoice.ts"]["status"], "ok")
        links = {tuple(l[:2]) for l in g["links"]}
        self.assertIn(("m:file", "f:src/db/pool.py"), links)                # whole-file anchor links straight to the file
        self.assertIn(("a:src/db/pool.py#POOL_SIZE", "f:src/db/pool.py"), links)
        self.assertEqual(sum(1 for l in g["links"] if l[:2] == ["a:src/db/pool.py#POOL_SIZE", "f:src/db/pool.py"]), 1)
        page = dashboard.build(self.root)
        data = page.split('id="graph-data">', 1)[1].split("</script>", 1)[0]
        nodes_on_page = {n["id"]: n for n in json.loads(data)["nodes"]}
        self.assertTrue(set(nodes) <= set(nodes_on_page))       # every memory node is drawn...
        code = [n for n in nodes_on_page.values() if n["status"] == "code"]
        self.assertTrue(code)                                    # ...on top of the repo's other code
        self.assertTrue(all(n["count"] == 0 for n in code))
        self.assertIn('id="m-pool"', page)

    def test_graph_data_cannot_break_out_of_its_script_tag(self):
        self.run_cli("init")
        self.run_cli("add", "</script><img src=x onerror=alert(1)>", "-a", "README.md", "-b", "x", "--id", "evil")
        page = dashboard.build(self.root)
        data = page.split('id="graph-data">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(data)["nodes"][0]["title"], "</script><img src=x onerror=alert(1)>")
        self.assertNotIn("<img src=x", page)


class TestServeDisconnects(RepoCase):
    def test_client_hanging_up_mid_response_prints_nothing(self):
        import contextlib
        import socket
        import struct
        self.run_cli("init")
        for i in range(30):  # a page big enough not to fit in one send
            self.run_cli("add", f"Memory {i} " + "x" * 200, "-a", "README.md", "-b", "y" * 400, "--id", f"m{i}")
        httpd = dashboard.make_server(self.root, 8820)
        err = io.StringIO()
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        with contextlib.redirect_stderr(err):
            t.start()
            try:
                for _ in range(5):
                    c = socket.create_connection(("127.0.0.1", httpd.server_address[1]))
                    c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # hang up hard
                    c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
                    c.close()
                time.sleep(1.5)
                ok = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/version", timeout=10).read()
            finally:
                httpd.shutdown()
                httpd.server_close()
        self.assertEqual(len(ok.strip()), 12)  # still serving
        self.assertNotIn("Traceback", err.getvalue())


class TestCodeMapAndSeed(RepoCase):
    def test_symbols_and_enclosing_function(self):
        py = "class A:\n    def m(self):\n        # NOTE: x\n        return 1\n\ndef f():\n    pass\n"
        self.assertEqual([n for n, _, _ in codemap.list_symbols(py, ".py")], ["A", "A.m", "f"])
        self.assertEqual(codemap.enclosing_symbol(py, ".py", 3), "A.m")
        self.assertIsNone(codemap.enclosing_symbol(py, ".py", 5))
        ts = "export function a(x: number) {\n  return x;\n}\nconst b = (q) => {\n  return q;\n};\nexport class C {\n  m() {}\n}\n"
        self.assertEqual([n for n, _, _ in codemap.list_symbols(ts, ".ts")], ["a", "b", "C"])
        self.assertEqual(codemap.enclosing_symbol(ts, ".ts", 5), "b")

    def test_skeleton_puts_kept_files_first_and_skips_outside_symlinks(self):
        self.write("src/app/big.py", "".join(f"def f{i}():\n    pass\n" for i in range(9)))
        import tempfile
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        outside = Path(other.name) / "outside_secret.py"
        outside.write_text("def secret():\n    # NOTE: should never be read through a symlink\n    pass\n")
        try:
            os.symlink(outside, self.root / "src" / "link.py")
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        sk = dict(codemap.skeleton(self.root, keep=["src/db/pool.py"]))
        self.assertEqual(list(dict(codemap.skeleton(self.root, keep=["src/db/pool.py"])))[0], "src/db/pool.py")
        self.assertEqual(len(sk["src/app/big.py"]), 6)  # capped
        self.assertNotIn("src/link.py", sk)
        self.assertFalse(any("symlink" in f.text for f in seed.scan(self.root)))

    def test_seed_turns_tagged_comments_into_anchored_memories(self):
        self.write("src/db/pool.py", "def get_pool():\n    # WHY: capped at 10 because the hosted plan allows 25\n"
                   "    # connections across 2 workers.\n    # TODO: this is a task, not a fact\n    return 10\n")
        self.write("src/billing/invoice.ts", "export function lineTotal(x) {\n  // NOTE: rounding is per line, not per invoice\n"
                   "  return x;\n}\n// HACK: short\n")
        self.run_cli("init")
        _, out, _ = self.run_cli("seed")
        self.assertIn("2 found", out)
        self.assertEqual(store.load_all(self.root), [])  # dry run writes nothing
        self.run_cli("seed", "--apply")
        mems = {str(m.anchors[0][0]): m for m in store.load_all(self.root)}
        self.assertEqual(set(mems), {"src/db/pool.py#get_pool", "src/billing/invoice.ts#lineTotal"})
        m = mems["src/db/pool.py#get_pool"]
        self.assertEqual(m.title, "Capped at 10 because the hosted plan allows 25 connections across 2 workers")
        self.assertTrue(m.unconfirmed_agent)
        self.assertIn("WHY comment in src/db/pool.py, line 2", m.body)
        self.assertEqual(self.check_json()[0], 0)
        _, out, _ = self.run_cli("seed", "--apply")
        self.assertIn("No new", out)  # running it again doesn't duplicate

    def test_clone_name_is_a_plain_folder_name_for_any_source(self):
        from anchormem.cli import _clone_name
        cases = {"https://github.com/psf/requests.git/": "requests", "git@github.com:owner/my-repo.git": "my-repo",
                 "C:\\Users\\me\\Temp\\tmpabc": "tmpabc", "/home/me/proj/": "proj", "..": "repo", "https://x/a b": "a-b"}
        for src, want in cases.items():
            self.assertEqual(_clone_name(src), want, src)

    def test_seed_finds_real_world_explanations_and_skips_noise(self):
        self.write("src/http/models.py", "\n".join([
            "# Copyright 2024 Someone. Licensed under the Apache License because lawyers must.",
            "import os",
            "",
            "# Import encoding now, to avoid an implicit import later inside a thread.",
            "import encodings",
            "",
            "# Keep the cache small because the hosted plan only gives us 256 MB of memory.",
            "def build_cache():",
            "    return {}",
            "",
            "class Request:",
            "    def prepare(self):",
            "        # Note that prepare_auth must be last so OAuth sees a fully prepared request.",
            "        self.prepare_auth()",
            "        # This must go after prepare_auth, otherwise hooks added by auth are lost.",
            "        self.prepare_hooks()",
            "        # XXX not implemented yet, because nobody needed it",
            "        # pylint: disable=no-member because of the metaclass magic here",
            "        # x = compute(a, b)  # must stay commented out until the migration",
            "        # short",
            "        return self",
        ]) + "\n")
        self.write("tests/test_models.py", "def test_x():\n    # This must never be stored because tests are not the source of truth.\n    pass\n")
        found = {(f.tag, str(f.anchor), f.title) for f in seed.scan(self.root)}
        self.assertEqual(found, {
            ("NOTE", "src/http/models.py#Request.prepare", "prepare_auth must be last so OAuth sees a fully prepared request"),
            ("COMMENT", "src/http/models.py#Request.prepare", "This must go after prepare_auth, otherwise hooks added by auth are lost"),
            ("COMMENT", "src/http/models.py#build_cache", "Keep the cache small because the hosted plan only gives us 256 MB of memory"),
        })
        self.assertEqual({f.tag for f in seed.scan(self.root, reasons=False)}, {"NOTE"})

    def test_short_first_sentence_takes_the_next_one(self):
        f = seed.Found("a.py", 1, "COMMENT", "If length exists, set it. Otherwise, we fall back to chunked encoding. More.", None)
        self.assertEqual(f.title, "If length exists, set it. Otherwise, we fall back to chunked encoding")

    def test_try_clones_seeds_and_cleans_up(self):
        self.write("src/db/pool.py", "def get_pool():\n    # IMPORTANT: never raise above 10 without upgrading the plan\n    return 10\n")
        self.commit("code")
        seen = {}

        def fake_serve(root, port, open_browser=True):
            seen["root"] = root
            seen["mems"] = [m.title for m in store.load_all(root)]
            return 0
        with mock.patch.object(dashboard, "serve", fake_serve):
            code, out, err = self.run_cli("try", str(self.root), "--no-open")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["mems"], ["Never raise above 10 without upgrading the plan"])
        self.assertFalse(seen["root"].exists())               # the temporary clone is gone
        self.assertFalse((self.root / ".anchormem").exists())  # the original repo is untouched
        code, _, err = self.run_cli("try", str(self.root / "nope"), "--no-open")
        self.assertEqual(code, 2)
        self.assertIn("git clone failed", err)


if __name__ == "__main__":
    unittest.main()
