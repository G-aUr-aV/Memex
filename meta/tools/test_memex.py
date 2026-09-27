#!/usr/bin/env python3
"""Self-contained tests for the Memex toolchain (stdlib only; never touches this vault or your HOME).

  python3 meta/tools/test_memex.py        # or: python3 -m unittest meta/tools/test_memex.py -v

Each run copies the tools and hooks into a temporary vault with a fake HOME, seeds a few pages,
and checks: the guard (Claude Code, Codex and Hermes payloads), the memex CLI, the pre-commit
hook, lint, and integrate.py install / re-run / remove.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
PY = sys.executable
RAW = "raw/learning/2026-09-20 Idempotency Article.md"
PAGE = "wiki/learning/concepts/Idempotency Keys.md"
OTHER = "wiki/learning/concepts/Retry Storms.md"
JOURNAL = "journal/daily/2026-09-27.md"
SEED = {
    RAW: "---\ntitle: Idempotency Article\nsource: https://example.com\ncaptured: 2026-09-20\nsource_type: article\n"
         "domain: learning\n---\n## Keys\nIdempotency keys let clients retry safely.\n",
    PAGE: "---\ntype: concept\ndomain: learning\nstatus: active\nsummary: Client-supplied keys that make retries "
          "safe by deduplicating requests on the server.\ncreated: 2026-09-20\nupdated: 2026-09-20\n"
          "aliases: [Idempotency Key]\nsources: [\"[[2026-09-20 Idempotency Article]]\"]\nreviewed:\n---\n"
          "> [!summary]\n> Keys that make retries safe.\n\n> [!mine] My take\n> Use them on every payment endpoint.\n\n"
          "## Content\n- Idempotency keys let clients retry safely ([[2026-09-20 Idempotency Article#Keys|src]]).\n\n"
          "## Sources\n- [[2026-09-20 Idempotency Article]]\n\n---\n## Timeline\n"
          "- 2026-09-20 | [[2026-09-20 Idempotency Article]] | created\n",
    OTHER: "---\ntype: concept\ndomain: learning\nstatus: active\nsummary: Retries amplifying load; mitigated with "
           "backoff and idempotency keys.\ncreated: 2026-09-20\nupdated: 2026-09-20\nuncited_ok: true\n---\n"
           "Related: [[Idempotency Keys]].\n",
    JOURNAL: "---\ndate: 2026-09-27\n---\n> [!brief]\n> old brief\n\n## Captures\n- my thought\n",
    "inbox/2026-09-27 Some Capture.md": "---\ntitle: Some Capture\n---\nhello\n",
}


def run(args, cwd, env=None, stdin=None):
    return subprocess.run(args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True)


class Vault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="memex-test-"))
        cls.V = cls.tmp / "My Vault"
        cls.home = cls.tmp / "home"
        ignore = shutil.ignore_patterns(".git", ".obsidian", ".trash", "__pycache__", "*.pyc")
        shutil.copytree(SRC, cls.V, ignore=ignore, symlinks=True)
        for d in ("inbox", "raw", "notes", "outputs"):
            for f in (cls.V / d).rglob("*"):
                if f.is_file() and f.name != ".gitkeep":
                    f.unlink()
        for rel, text in SEED.items():
            p = cls.V / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (cls.home / ".codex").mkdir(parents=True)
        cls.env = {**os.environ, "HOME": str(cls.home), "PATH": f"{cls.home}/.local/bin:{os.environ['PATH']}"}
        cls.env.pop("CLAUDE_PROJECT_DIR", None)
        run(["git", "init", "-q"], cls.V)
        run(["git", "config", "user.name", "Test"], cls.V)
        run(["git", "config", "user.email", "test@localhost"], cls.V)
        r = run(["bash", "meta/tools/setup.sh", "--agents", "all"], cls.V, cls.env)
        assert r.returncode == 0, r.stdout + r.stderr
        run(["git", "add", "-A", "."], cls.V)
        r = run(["git", "commit", "-q", "-m", "seed"], cls.V, cls.env)
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def memex(self, *args, stdin=None, cwd=None):
        return run([PY, str(self.V / "meta/tools/memex.py"), *args], cwd or self.V, self.env, stdin)


class GuardTest(Vault):
    def guard(self, payload):
        payload.setdefault("cwd", str(self.V))
        return run([PY, str(self.V / ".claude/hooks/guard.py")], "/", self.env, json.dumps(payload)).returncode

    def P(self, rel):
        return str(self.V / rel)

    def check(self, cases):
        for name, expected, payload in cases:
            with self.subTest(name):
                self.assertEqual(self.guard(payload), expected, name)

    def test_claude_code(self):
        P = self.P
        self.check([
            ("write new raw", 0, {"tool_name": "Write", "tool_input": {"file_path": P("raw/learning/2026-09-27 New.md"), "content": "x"}}),
            ("write existing raw", 2, {"tool_name": "Write", "tool_input": {"file_path": P(RAW), "content": "x"}}),
            ("edit raw", 2, {"tool_name": "Edit", "tool_input": {"file_path": P(RAW), "old_string": "Keys", "new_string": "K"}}),
            ("edit mine", 2, {"tool_name": "Edit", "tool_input": {"file_path": P(PAGE), "old_string": "> Use them on every payment endpoint.", "new_string": "> x"}}),
            ("extend mine", 2, {"tool_name": "Edit", "tool_input": {"file_path": P(PAGE), "old_string": "> Use them on every payment endpoint.", "new_string": "> Use them on every payment endpoint.\n> more"}}),
            ("edit wiki", 0, {"tool_name": "Edit", "tool_input": {"file_path": P(PAGE), "old_string": "## Content", "new_string": "## Details"}}),
            ("shrink wiki", 2, {"tool_name": "Write", "tool_input": {"file_path": P(PAGE), "content": SEED[PAGE][:200] + "\n> [!mine] My take\n> Use them on every payment endpoint.\n"}}),
            ("journal brief", 0, {"tool_name": "Edit", "tool_input": {"file_path": P(JOURNAL), "old_string": "> old brief", "new_string": "> new"}}),
            ("journal captures", 2, {"tool_name": "Edit", "tool_input": {"file_path": P(JOURNAL), "old_string": "- my thought", "new_string": "- x"}}),
            ("notes", 2, {"tool_name": "Write", "tool_input": {"file_path": P("notes/Idea.md"), "content": "x"}}),
            ("outside vault", 0, {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x.md", "content": "x"}}),
        ])

    def test_shell(self):
        V = str(self.V)
        sh = lambda c, cwd=V: {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": cwd}  # noqa: E731
        self.check([
            ("rm raw", 2, sh(f'rm "{RAW}"')),
            ("rm glob", 2, sh("rm raw/learning/*.md")),
            ("rm wiki dir", 2, sh("rm -rf wiki/learning")),
            ("cd then rm", 2, sh(f'cd wiki && rm "{OTHER[5:]}"')),
            ("mv", 2, sh('mv "inbox/2026-09-27 Some Capture.md" raw/learning/')),
            ("obsidian move filing", 0, sh('obsidian move path="inbox/2026-09-27 Some Capture.md" to="raw/learning/2026-09-27 Some Capture.md"')),
            ("obsidian move raw", 2, sh(f'obsidian move path="{RAW}" to="raw/x.md"')),
            ("obsidian delete", 2, sh('obsidian delete file="Retry Storms"')),
            ("redirect into journal", 2, sh(f'echo x > "{JOURNAL}"')),
            ("append into raw", 2, sh(f'echo x >> "{RAW}"')),
            ("sed -i raw", 2, sh(f"sed -i '' 's/a/b/' \"{RAW}\"")),
            ("git rm raw", 2, sh(f'git rm "{RAW}"')),
            ("cp over raw", 2, sh(f'cp /tmp/x "{RAW}"')),
            ("git clean in vault", 2, sh("git clean -fd")),
            ("rm vault parent", 2, sh(f'rm -rf "{self.V.parent}"', "/tmp")),
            ("nested bash -lc", 2, sh(f"bash -lc 'rm \"{RAW}\"'")),
            ("memex rm", 0, sh(f"python3 meta/tools/memex.py rm '{OTHER}'")),
            ("read raw", 0, sh(f'cat "{RAW}" | grep Keys > /tmp/memex-test-out.txt')),
            ("outside rm", 0, sh("rm -rf /tmp/whatever build/", "/tmp")),
            ("git clean elsewhere", 0, sh("git clean -fd", "/tmp")),
            ("unbalanced quote", 0, sh("echo 'hi")),
        ])

    def test_codex(self):
        patch = lambda body: {"tool_name": "apply_patch", "tool_input": {"command": f"*** Begin Patch\n{body}\n*** End Patch"}, "cwd": str(self.V)}  # noqa: E731
        self.check([
            ("patch mine", 2, patch(f"*** Update File: {PAGE}\n@@\n > [!mine] My take\n-> Use them on every payment endpoint.\n+> x")),
            ("patch wiki", 0, patch(f"*** Update File: {PAGE}\n@@\n-## Content\n+## Details")),
            ("add new raw", 0, patch("*** Add File: raw/learning/2026-09-27 X.md\n+hello")),
            ("add over raw", 2, patch(f"*** Add File: {RAW}\n+hello")),
            ("delete wiki", 2, patch(f"*** Delete File: {OTHER}")),
            ("update raw", 2, patch(f"*** Update File: {RAW}\n@@\n-Idempotency keys let clients retry safely.\n+x")),
            ("journal brief", 0, patch(f"*** Update File: {JOURNAL}\n@@\n-> old brief\n+> new")),
            ("unverifiable mine", 2, patch(f"*** Update File: {PAGE}\n@@\n-no such line\n+x")),
            ("argv shell", 2, {"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", f'rm "{RAW}"']}, "cwd": str(self.V)}),
        ])

    def test_hermes(self):
        P = self.P
        h = lambda tool, ti: {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": ti, "cwd": "/tmp"}  # noqa: E731
        self.check([
            ("write notes", 2, h("write_file", {"path": P("notes/x.md"), "content": "x"})),
            ("patch brief", 0, h("patch", {"mode": "replace", "path": P(JOURNAL), "old_string": "> old brief", "new_string": "> b"})),
            ("patch mine", 2, h("patch", {"mode": "replace", "path": P(PAGE), "old_string": "every payment", "new_string": "no"})),
            ("v4a mine", 2, h("patch", {"mode": "patch", "patch": f"*** Begin Patch\n*** Update File: {P(PAGE)}\n@@\n-> Use them on every payment endpoint.\n+> x\n*** End Patch"})),
            ("terminal rm raw", 2, h("terminal", {"command": f'rm "{P(RAW)}"'})),
            ("terminal outside", 0, h("terminal", {"command": "npm test"})),
        ])


class CliTest(Vault):
    def test_search_and_read(self):
        r = self.memex("search", "idempotency")
        self.assertIn(PAGE, r.stdout)
        r = self.memex("read", "Idempotency Key")  # alias
        self.assertIn("[!mine]", r.stdout)

    def test_capture(self):
        r = self.memex("capture", "--title", "Flaky: Test/TZ", "--domain", "engineering", stdin="Cause: TZ.\n", cwd="/tmp")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / r.stdout.strip()).exists())
        r = self.memex("capture", "--title", "Leak", "--domain", "learning", stdin="ghp_" + "a" * 36 + "\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("refused", r.stderr)
        r = self.memex("capture", "--title", "Upd", "--domain", "learning", "--action", "update", "--target", "Idempotency Key", stdin="x\n")
        self.assertIn('target: "[[Idempotency Keys]]"', (self.V / r.stdout.strip()).read_text())

    def test_rm(self):
        self.assertEqual(self.memex("rm", PAGE).returncode, 1)  # has backlinks
        self.assertEqual(self.memex("rm", RAW).returncode, 1)   # immutable
        cap = self.V / "inbox" / "2026-09-27 To Remove.md"
        cap.write_text("x\n")
        self.assertEqual(self.memex("rm", str(cap.relative_to(self.V))).returncode, 0)
        self.assertTrue((self.V / ".trash/inbox/2026-09-27 To Remove.md").exists())

    def test_commit_blocks_raw_changes(self):
        raw = self.V / RAW
        original = raw.read_text()
        raw.write_text(original + "tampered\n")
        try:
            r = self.memex("commit", "test: tamper")
            self.assertEqual(r.returncode, 1)
            self.assertIn("immutable", r.stderr)
        finally:
            run(["git", "reset", "-q", "--", RAW], self.V)
            raw.write_text(original)

    def test_hermes_hook(self):
        payload = {"cwd": str(self.V / "wiki"), "extra": {"is_first_turn": True}}
        self.assertIn("# Memex context", json.loads(self.memex("hook", "hermes", stdin=json.dumps(payload)).stdout)["context"])
        payload = {"cwd": "/tmp", "extra": {"is_first_turn": False}}
        self.assertEqual(self.memex("hook", "hermes", stdin=json.dumps(payload)).stdout.strip(), "")

    def test_lint_clean(self):
        r = run([PY, "meta/tools/lint.py", "--quick"], self.V, self.env)
        self.assertEqual(r.returncode, 0, r.stdout)


class IntegrateTest(Vault):
    def test_install_rerun_remove(self):
        home = self.home
        settings = json.loads((home / ".claude/settings.json").read_text())
        self.assertIn("Bash(memex *)", settings["permissions"]["allow"])
        self.assertTrue((home / ".local/bin/memex").exists())
        for f in (".claude/skills/memex/SKILL.md", ".agents/skills/memex/SKILL.md", ".hermes/skills/memex/SKILL.md"):
            self.assertTrue((home / f).exists(), f)
        run([PY, "meta/tools/integrate.py", "--agents", "all"], self.V, self.env)  # re-run: no duplicates
        self.assertEqual((home / ".claude/CLAUDE.md").read_text().count("memex:begin"), 1)
        self.assertEqual((home / ".codex/hooks.json").read_text().count("guard.py"), 1)
        try:
            import tomllib
            tomllib.loads((home / ".codex/config.toml").read_text())
        except ImportError:
            pass
        r = run([PY, "meta/tools/integrate.py", "--remove"], self.V, self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((home / ".local/bin/memex").exists())
        self.assertNotIn("memex:begin", (home / ".claude/CLAUDE.md").read_text())
        run([PY, "meta/tools/integrate.py", "--agents", "all"], self.V, self.env)  # restore for other tests


if __name__ == "__main__":
    unittest.main(verbosity=2)
