import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anchormem.anchors import Anchor, find_symbol, fingerprint  # noqa: E402
from anchormem.cli import main  # noqa: E402
from anchormem import store  # noqa: E402


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                   cwd=root, check=True, capture_output=True)


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.old = os.getcwd()
        os.chdir(self.root)
        git(self.root, "init", "-q")
        self.write("src/db/pool.py", """
            import os

            POOL_SIZE = 10

            def get_pool():
                return create_pool(size=POOL_SIZE)
            """)
        self.write("src/billing/invoice.ts", """
            export function lineTotal(qty: number, price: number): number {
              return Math.round(qty * price * 100) / 100;
            }

            export class Invoice {
              total(): number {
                return 0;
              }
            }
            """)
        self.write("README.md", "hello\n")
        self.commit("initial")

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def write(self, rel, text):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text).lstrip("\n"))

    def commit(self, msg):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", msg)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def check_json(self):
        code, out, _ = self.run_cli("check", "--json")
        return code, {m["id"]: m for m in json.loads(out)["memories"]}


class TestAnchors(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(Anchor.parse("./a/b.py#f"), Anchor("a/b.py", symbol="f"))
        self.assertEqual(Anchor.parse("a.py:L3-L9"), Anchor("a.py", start=3, end=9))
        self.assertEqual(str(Anchor.parse("a.py:4")), "a.py:4")
        self.assertEqual(Anchor.parse("a.py"), Anchor("a.py"))
        with self.assertRaises(ValueError):
            Anchor.parse("a.py:9-3")

    def test_fingerprint_ignores_whitespace(self):
        self.assertEqual(fingerprint("def f():\n    return 1\n"), fingerprint("  def f():\n\n        return 1   "))
        self.assertNotEqual(fingerprint("return 1"), fingerprint("return 2"))

    def test_python_nested_symbol_with_decorator(self):
        src = "class A:\n    @property\n    def b(self):\n        return 1\n\n    def c(self):\n        pass\n"
        self.assertEqual(find_symbol(src, "A.b", ".py"), (2, 4))
        self.assertIsNone(find_symbol(src, "A.zzz", ".py"))

    def test_brace_language_blocks(self):
        src = 'const X = 5;\nfunction f(a) {\n  if (a) { return "}"; }\n  return 0;\n}\nclass K {\n  m() {\n    return 1;\n  }\n}\n'
        self.assertEqual(find_symbol(src, "X", ".js"), (1, 1))
        self.assertEqual(find_symbol(src, "f", ".js"), (2, 5))
        self.assertEqual(find_symbol(src, "K.m", ".js"), (7, 9))


class TestStore(unittest.TestCase):
    def test_roundtrip(self):
        mem = store.Memory(id="x", title="Title: with colon", body="Body.",
                           anchors=[(Anchor.parse("a.py#f"), "abcdef123456"), (Anchor.parse("b.py:1-2"), None)],
                           created="2026-11-10", verified="2026-11-10", commit="abc1234", extra={"owner": "team"})
        back = store.parse(mem.to_text())
        self.assertEqual((back.id, back.title, back.body, back.commit, back.extra),
                         ("x", "Title: with colon", "Body.", "abc1234", {"owner": "team"}))
        self.assertEqual(back.anchors, mem.anchors)

    def test_bad_file_reports_line(self):
        with self.assertRaises(store.StoreError):
            store.parse("---\nid: x\nnonsense line\n---\n")


class TestWorkflow(RepoCase):
    def test_init_writes_agent_rules_once(self):
        self.assertEqual(self.run_cli("init")[0], 0)
        self.run_cli("init")
        text = (self.root / "AGENTS.md").read_text()
        self.assertEqual(text.count("<!-- anchormem -->"), 1)
        self.assertTrue((self.root / ".anchormem/memories").is_dir())

    def test_full_lifecycle(self):
        self.run_cli("init")
        code, out, _ = self.run_cli("add", "Pool capped at 10", "--anchor", "src/db/pool.py#POOL_SIZE",
                                    "--body", "Postgres plan limit.")
        self.assertEqual(code, 0, out)
        self.run_cli("add", "Invoices round per line", "-a", "src/billing/invoice.ts#lineTotal")
        self.run_cli("add", "Readme exists", "-a", "README.md")
        self.commit("memories")

        code, mems = self.check_json()
        self.assertEqual(code, 0)
        self.assertTrue(all(m["status"] == "ok" for m in mems.values()))

        # whitespace-only edits are not changes
        self.write("src/db/pool.py", (self.root / "src/db/pool.py").read_text().replace("POOL_SIZE = 10", "POOL_SIZE  =  10"))
        self.assertEqual(self.check_json()[0], 0)

        # a real change flags only the memory anchored to it
        self.write("src/db/pool.py", (self.root / "src/db/pool.py").read_text().replace("10", "25"))
        code, mems = self.check_json()
        self.assertEqual(code, 1)
        self.assertEqual(mems["pool-capped-10"]["status"], "changed")
        self.assertEqual(mems["invoices-round-per-line"]["status"], "ok")

        # context shows the stale warning to the agent
        _, out, _ = self.run_cli("context", "src/db")
        self.assertIn("[STALE: CHANGED]", out)
        self.assertIn("Postgres plan limit.", out)
        self.assertNotIn("Invoices round", out)

        # verify records the new fingerprint
        self.assertEqual(self.run_cli("verify", "pool-capped-10")[0], 0)
        self.assertEqual(self.check_json()[0], 0)

    def test_moved_file_and_follow(self):
        self.run_cli("init")
        self.run_cli("add", "Rounding rule", "-a", "src/billing/invoice.ts#lineTotal")
        self.commit("memory")
        git(self.root, "mv", "src/billing/invoice.ts", "src/billing/lines.ts")
        self.commit("rename")

        code, mems = self.check_json()
        self.assertEqual(code, 1)
        res = mems["rounding-rule"]["anchors"][0]
        self.assertEqual(res["status"], "moved")
        self.assertEqual(res["moved_to"], ["src/billing/lines.ts#lineTotal"])

        self.assertEqual(self.run_cli("verify", "rounding-rule")[0], 1)  # needs --follow
        self.assertEqual(self.run_cli("verify", "rounding-rule", "--follow")[0], 0)
        self.assertEqual(self.check_json()[0], 0)
        self.assertIn("src/billing/lines.ts#lineTotal", store.load(self.root, "rounding-rule").to_text())

    def test_symbol_moved_to_other_file_without_git_rename(self):
        self.run_cli("init")
        self.run_cli("add", "Pool getter", "-a", "src/db/pool.py#get_pool")
        self.write("src/db/pool.py", "import os\n\nPOOL_SIZE = 10\n")
        self.write("src/db/connections.py", "def get_pool():\n    return create_pool(size=10)\n")
        _, mems = self.check_json()
        self.assertEqual(mems["pool-getter"]["anchors"][0]["moved_to"], ["src/db/connections.py#get_pool"])

    def test_missing_symbol(self):
        self.run_cli("init")
        self.run_cli("add", "Pool getter", "-a", "src/db/pool.py#get_pool")
        self.write("src/db/pool.py", "import os\n")
        _, mems = self.check_json()
        self.assertEqual(mems["pool-getter"]["status"], "missing")
        self.assertEqual(self.run_cli("verify", "pool-getter")[0], 1)

    def test_hand_written_anchor_is_unverified(self):
        self.run_cli("init")
        (self.root / ".anchormem/memories/hand.md").write_text("---\nid: hand\ntitle: Hand written\nanchors:\n  - README.md\n---\nx\n")
        code, mems = self.check_json()
        self.assertEqual((code, mems["hand"]["status"]), (1, "unverified"))
        self.run_cli("verify", "hand")
        self.assertEqual(self.check_json()[0], 0)

    def test_add_rejects_bad_anchor(self):
        self.run_cli("init")
        code, _, err = self.run_cli("add", "Nope", "-a", "src/db/pool.py#does_not_exist")
        self.assertEqual(code, 2)
        self.assertIn("does not resolve", err)

    def test_paths_relative_to_subdirectory(self):
        self.run_cli("init")
        os.chdir(self.root / "src/db")
        code, out, _ = self.run_cli("add", "From subdir", "-a", "pool.py#POOL_SIZE")
        self.assertEqual(code, 0, out)
        self.assertIn("src/db/pool.py#POOL_SIZE", store.load(self.root, "subdir").to_text())
        _, out, _ = self.run_cli("context", "pool.py")
        self.assertIn("From subdir", out)

    def test_no_init_gives_clear_error(self):
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 2)
        self.assertIn("anchormem init", err)


if __name__ == "__main__":
    unittest.main()
