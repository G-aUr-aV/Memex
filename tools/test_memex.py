#!/usr/bin/env python3
"""Self-contained tests for the Memex framework (stdlib only; never touches your vault, HOME or git config).

  python3 tools/test_memex.py        # or: python3 -m unittest tools/test_memex.py -v

Each test class copies the framework (tracked and untracked-but-not-ignored files only, so a vault inside
this folder is never copied) into a temporary folder, makes it a git repo, and runs setup.sh with a fake
HOME and a fake global git config that holds a "real" identity plus an includeIf. It then checks the
vault's layout, its local-only git safety, the guard (Claude Code, Codex and Hermes payloads), the memex
CLI, sync, domains, vault locations, and agent integration.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
PY = sys.executable
IDENT = "Memex Agent <memex-agent@localhost>"
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
FAKE_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"  # matches the AWS pattern; split so this file never matches itself


def text(path: Path) -> str:
    """A file's text, or "" if it doesn't exist (uninstall deletes files that held only Memex's lines)."""
    return path.read_text() if path.exists() else ""


def run(args, cwd, env, stdin=None):
    return subprocess.run([str(a) for a in args], cwd=str(cwd), env=env, input=stdin, capture_output=True, text=True)


def copy_framework(dest: Path):
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=SRC,
                       capture_output=True, text=True)
    files = [f for f in r.stdout.split("\0") if f] if r.returncode == 0 else None
    if files is None:  # not a git checkout (e.g. a release tarball)
        shutil.copytree(SRC, dest, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "MemexVault"))
        return
    for f in files:
        src = SRC / f
        if not (src.exists() or src.is_symlink()):
            continue  # deleted in the working tree
        out = dest / f
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), out)
        else:
            shutil.copy2(src, out)


class Sandbox(unittest.TestCase):
    """A framework copy, a fake HOME and a vault created by setup.sh."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="memex-test-")).resolve()
        cls.fw = cls.tmp / "fw"
        cls.home = cls.tmp / "home"
        cls.V = cls.tmp / "My Vault"
        copy_framework(cls.fw)
        (cls.home / ".codex").mkdir(parents=True)
        gitconfig = cls.tmp / "gitconfig"
        gitconfig.write_text(f'[user]\n\tname = Real Person\n\temail = real@example.com\n'
                             f'[includeIf "gitdir:{cls.tmp}/"]\n\tpath = {cls.tmp}/gitconfig-inc\n')
        (cls.tmp / "gitconfig-inc").write_text("[user]\n\temail = included@example.com\n")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("GIT_", "MEMEX_", "CLAUDE_")) and k not in ("HERMES_WRITE_SAFE_ROOT", "CODEX_HOME")}
        env.update({"HOME": str(cls.home), "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
                    "PATH": f"{cls.home}/.local/bin:{os.environ['PATH']}"})
        cls.env = env
        run(["git", "init", "-q", "-b", "master"], cls.fw, env)
        run(["git", "add", "-A"], cls.fw, env)
        r = run(["git", "commit", "-qm", "framework"], cls.fw, env)
        assert r.returncode == 0, r.stdout + r.stderr
        r = run(["bash", cls.fw / "setup.sh", "--vault", cls.V, "--agents", "all"], cls.tmp, env)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.setup_out = r.stdout
        for rel, text in SEED.items():
            p = cls.V / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        r = cls.memex("commit", "test: seed")
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def memex(cls, *args, stdin=None, cwd=None, env=None):
        return run([PY, cls.fw / "tools" / "memex.py", *args], cwd or cls.V, env or cls.env, stdin)

    def git(self, *args, cwd=None, env=None):
        return run(["git", *args], cwd or self.V, env or self.env)

    def guard(self, payload, env=None):
        payload.setdefault("cwd", str(self.V))
        return run([PY, self.fw / "hooks" / "guard.py"], self.V, env or self.env, json.dumps(payload))

    def assertDenied(self, payload, env=None):
        r = self.guard(payload, env)
        self.assertEqual(r.returncode, 2, f"expected a block: {payload}\n{r.stderr}")
        return r.stderr

    def assertAllowed(self, payload, env=None):
        r = self.guard(payload, env)
        self.assertEqual(r.returncode, 0, f"expected allow: {payload}\n{r.stderr}")


class TestVaultLayout(Sandbox):
    def test_marker_and_rendered_schema(self):
        self.assertTrue((self.V / ".memex" / "vault.json").is_file())
        agents = (self.V / "AGENTS.md").read_text()
        self.assertIn("This vault is **My Vault**", agents)
        self.assertIn("## Vault-specific rules", agents)
        self.assertIn(str(self.fw), agents)
        self.assertNotIn("{{", agents)
        self.assertEqual(os.readlink(self.V / "CLAUDE.md"), "AGENTS.md")
        self.assertEqual(os.readlink(self.V / ".agents" / "skills"), "../.claude/skills")
        self.assertIn("!`memex ctx ingest`", (self.V / ".claude/skills/ingest/SKILL.md").read_text())
        rule = (self.V / ".claude/rules/learning.md").read_text()
        self.assertTrue(rule.startswith('---\npaths:\n  - "raw/learning/**"'))
        self.assertTrue((self.V / "meta/templates/wiki/Concept.md").exists())
        self.assertTrue((self.V / "meta/docs/Design Rationale.md").exists())

    def test_managed_files_are_not_tracked(self):
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertIn(PAGE, tracked)
        self.assertIn(".memex/vault.json", tracked)
        for managed in ("AGENTS.md", "CLAUDE.md", ".claude/settings.json", "Memex Manual.md", ".memex/state.json"):
            self.assertNotIn(managed, tracked)
        self.assertFalse([l for l in tracked if l.startswith((".claude/", ".codex/", "meta/templates/"))])
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_framework_repo_untouched(self):
        self.assertEqual(self.git("status", "--porcelain", cwd=self.fw).stdout, "")
        self.assertEqual(self.git("ls-files", "wiki", "raw", "inbox", "journal", "notes", "outputs", ".memex",
                                  cwd=self.fw).stdout, "")

    def test_config_and_hook_commands(self):
        cfg = json.loads((self.home / ".config/memex/config.json").read_text())
        self.assertEqual(Path(cfg["vault"]), self.V)
        vault_hook = json.loads((self.V / ".claude/settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        global_hook = json.loads((self.home / ".claude/settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(vault_hook["command"], global_hook["command"])  # identical, so Claude Code runs it once
        self.assertIn(str(self.fw / "hooks" / "guard.py"), vault_hook["command"])
        hooks = json.loads((self.home / ".claude/settings.json").read_text())["hooks"]
        self.assertTrue(hooks["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("hook prompt"))
        json.loads((self.V / ".codex/hooks.json").read_text())

    def test_domain_folders_and_indexes(self):
        for d in ("engineering/decisions", "learning/topics", "personal/goals"):
            self.assertTrue((self.V / "wiki" / d).is_dir(), d)
        self.assertTrue((self.V / "wiki/learning/Learning Index.md").exists())
        self.assertIn("[[Idempotency Keys]]", (self.V / "wiki/learning/Learning Index.md").read_text())


class TestGitSafety(Sandbox):
    def test_scoped_recovery_with_empty_inbox_preserves_owner_work(self):
        """The new recovery context and existing scoped commits must work together."""
        saved_inbox = {p: p.read_bytes() for p in (self.V / "inbox").iterdir() if p.is_file()}
        graph = self.V / ".obsidian/graph.json"
        saved_graph = graph.read_text()
        log = self.V / "wiki/log.md"
        note = self.V / "notes/Owner Draft.md"
        raw = "raw/learning/2026-10-03 Recovered Backoff.md"
        page = "wiki/learning/concepts/Recovered Backoff.md"
        try:
            for inbox in saved_inbox:
                inbox.unlink()
            (self.V / raw).write_text(
                "---\ntitle: Recovered Backoff\ndomain: learning\nsource_type: capture\n---\n"
                "Backoff spaces retries apart.\n")
            graph.write_text(saved_graph + "\n")
            note.write_text("Owner's staged draft\n")
            self.assertEqual(self.git("add", "notes/Owner Draft.md").returncode, 0)
            note.write_text("Owner's further unstaged edit\n")
            context = self.memex("ctx", "auto-inbox")
            self.assertEqual(context.returncode, 0, context.stderr)
            self.assertIn("Inbox: 0 item(s)", context.stdout)
            self.assertIn(raw, context.stdout)
            self.assertIn("filed but not committed", self.memex("context").stdout)
            (self.V / page).write_text(
                "---\ntype: concept\ndomain: learning\nstatus: active\nsummary: Backoff spaces retries apart.\n"
                "created: 2026-10-03\nupdated: 2026-10-03\nreviewed:\n---\n"
                "> [!summary]\n> Backoff spaces retries apart.\n\n> [!mine] My take\n> (none yet)\n\n"
                "## Content\n- Backoff spaces retries apart ([[2026-10-03 Recovered Backoff|src]]).\n\n"
                "## Sources\n- [[2026-10-03 Recovered Backoff]]\n\n---\n## Timeline\n"
                "- 2026-10-03 | [[2026-10-03 Recovered Backoff]] | Recovered.\n")
            log.write_text(log.read_text() + "\n## [2026-10-03] inbox | auto: 1 items\n"
                           "- Recovered a filed capture into [[Recovered Backoff]].\n")
            result = self.memex("commit", "inbox: auto 1 items", "--only", raw, page, "wiki/log.md")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            changed = set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines())
            self.assertEqual(changed, {raw, page, "wiki/log.md", "wiki/index.md", "wiki/learning/Learning Index.md"})
            self.assertEqual(self.git("show", ":notes/Owner Draft.md").stdout, "Owner's staged draft\n")
            self.assertEqual(note.read_text(), "Owner's further unstaged edit\n")
            self.assertIn(".obsidian/graph.json", self.git("diff", "--name-only").stdout)
            self.assertNotIn("filed but not committed", self.memex("context").stdout)
        finally:
            for inbox, body in saved_inbox.items():
                inbox.write_bytes(body)
            graph.write_text(saved_graph)
            self.git("reset", "-q", "--", "notes/Owner Draft.md")
            note.unlink(missing_ok=True)

    def test_scoped_commit_preserves_unrelated_edits_and_staging(self):
        graph = self.V / ".obsidian/graph.json"
        original = graph.read_text()
        capture = self.V / "inbox/Scoped Capture.md"
        staged = self.V / "inbox/Unrelated Staged.md"
        try:
            graph.write_text(original + "\n")
            staged.write_text("Unrelated owner's work\n")
            self.git("add", "inbox/Unrelated Staged.md")
            capture.write_text("Durable capture\n")
            r = self.memex("commit", "inbox: scoped", "--only", "inbox/Scoped Capture.md")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()
            self.assertEqual(changed, ["inbox/Scoped Capture.md"])
            self.assertIn("inbox/Unrelated Staged.md", self.git("diff", "--cached", "--name-only").stdout)
            self.assertIn(".obsidian/graph.json", self.git("diff", "--name-only").stdout)
            before = self.git("rev-parse", "HEAD").stdout
            r = self.memex("commit", "inbox: scoped no-op", "--only", "inbox/Scoped Capture.md")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing to commit", r.stdout)
            self.assertEqual(before, self.git("rev-parse", "HEAD").stdout)
        finally:
            graph.write_text(original)
            self.git("reset", "-q", "--", "inbox/Unrelated Staged.md")
            staged.unlink(missing_ok=True)

    def test_scoped_commit_includes_indexes_and_tracked_inbox_removal(self):
        self.memex("capture", "--title", "Scoped Filed", "--domain", "learning", stdin="A reusable fact\n")
        found = next((self.V / "inbox").glob("* Scoped Filed.md"))
        r = self.memex("commit", "inbox: capture", "--only", found.relative_to(self.V).as_posix())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        old = found.relative_to(self.V).as_posix()
        r = self.memex("file", old, "--domain", "learning", "--name", "2026-10-03 Scoped Filed.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        page = "wiki/learning/concepts/Scoped Fact.md"
        (self.V / page).write_text(
            "---\ntype: concept\ndomain: learning\nstatus: active\nsummary: A scoped reusable fact.\n"
            "created: 2026-10-03\nupdated: 2026-10-03\nreviewed:\n---\n"
            "> [!summary]\n> A reusable fact.\n\n> [!mine] My take\n> (none yet)\n\n"
            "## Content\n- A reusable fact ([[2026-10-03 Scoped Filed|src]]).\n\n"
            "## Sources\n- [[2026-10-03 Scoped Filed]]\n\n---\n## Timeline\n"
            "- 2026-10-03 | [[2026-10-03 Scoped Filed]] | Created.\n")
        r = self.memex("commit", "inbox: filed", "--only", old, "raw/learning/2026-10-03 Scoped Filed.md", page)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(old, self.git("ls-files").stdout.splitlines())
        self.assertIn("raw/learning/2026-10-03 Scoped Filed.md", self.git("ls-files").stdout.splitlines())
        changed = self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()
        self.assertIn("wiki/index.md", changed)
        self.assertIn("wiki/learning/Learning Index.md", changed)
        self.assertIn("[[Scoped Fact]]", (self.V / "wiki/learning/Learning Index.md").read_text())

    def test_scoped_commit_rejects_broad_paths_and_escaping_symlink(self):
        link = self.V / "inbox/escape.md"
        outside = self.tmp / "outside.md"
        outside.write_text("outside\n")
        link.symlink_to(outside)
        try:
            for name in (".", "wiki", "../outside.md", str(outside), "inbox/escape.md"):
                r = self.memex("commit", "inbox: refused", "--only", name)
                self.assertNotEqual(r.returncode, 0, name)
                self.assertIn("individual files inside the vault", r.stderr)
        finally:
            link.unlink()

    def test_local_config(self):
        get = lambda k: self.git("config", "--local", "--get", k).stdout.strip()  # noqa: E731
        self.assertEqual((get("user.name"), get("user.email")), ("Memex Agent", "memex-agent@localhost"))
        self.assertEqual(get("commit.gpgsign"), "false")
        self.assertEqual(get("core.hooksPath"), ".git/hooks")
        self.assertEqual(self.git("remote").stdout.strip(), "")

    def test_every_commit_uses_the_vault_identity(self):
        env = {**self.env, "GIT_AUTHOR_NAME": "Real Person", "GIT_AUTHOR_EMAIL": "real@example.com"}
        r = self.memex("capture", "--title", "Env Identity", "--domain", "learning", stdin="note\n", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.memex("commit", "inbox: env identity", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        who = set(self.git("log", "--format=%an <%ae>%n%cn <%ce>").stdout.split("\n")) - {""}
        self.assertEqual(who, {IDENT})
        self.assertIn("Memex-Framework: 0.3.0", self.git("log", "-1", "--format=%B").stdout)

    def test_manual_commit_as_someone_else_is_refused(self):
        (self.V / "inbox" / "manual.md").write_text("x\n")
        self.git("add", "inbox/manual.md")
        env = {**self.env, "GIT_AUTHOR_NAME": "Real Person", "GIT_AUTHOR_EMAIL": "real@example.com"}
        r = self.git("commit", "-qm", "manual", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only takes commits from Memex Agent", r.stderr)
        self.git("rm", "-q", "--cached", "inbox/manual.md")
        (self.V / "inbox" / "manual.md").unlink()

    def test_push_is_refused(self):
        bare = self.tmp / "bare.git"
        self.git("init", "-q", "--bare", str(bare), cwd=self.tmp)
        r = self.git("push", str(bare), "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("local-only", r.stderr)

    def test_commit_refuses_with_a_remote(self):
        self.git("remote", "add", "origin", "https://example.com/vault.git")
        try:
            r = self.memex("commit", "x: y")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("weren't attached with `memex remote set`", r.stderr)
        finally:
            self.git("remote", "remove", "origin")

    def test_precommit_blocks_raw_changes_and_secrets(self):
        (self.V / RAW).write_text(SEED[RAW] + "edited\n")
        self.git("add", RAW)
        r = self.git("commit", "-qm", "edit raw")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("raw/ sources are immutable", r.stderr)
        self.git("restore", "--staged", "--worktree", RAW)
        leak = self.V / "wiki/learning/concepts/Leak.md"
        leak.write_text(f"key {FAKE_KEY}\n")
        self.git("add", str(leak))
        r = self.git("commit", "-qm", "leak")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("possible AWS access key", r.stderr)
        self.git("rm", "-q", "--cached", str(leak))
        leak.unlink()

    def test_doctor_is_clean(self):
        r = self.memex("doctor")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("✗", r.stdout)


class TestLocations(Sandbox):
    def fresh_env(self, name):
        home = self.tmp / name
        home.mkdir()
        return {**self.env, "HOME": str(home)}

    def test_default_is_memexvault_in_the_current_folder(self):
        where = self.tmp / "elsewhere"
        where.mkdir()
        env = self.fresh_env("home-default")
        r = run(["bash", self.fw / "setup.sh", "--agents", "none"], where, env, stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((where / "MemexVault" / ".memex" / "vault.json").exists())
        shim = Path(env["HOME"]) / ".local/bin/memex"  # the CLI is installed even with no agent wired
        self.assertEqual(Path(run([shim, "path"], self.tmp, env).stdout.strip()), (where / "MemexVault").resolve())

    def test_vault_inside_the_framework_is_ignored_there(self):
        env = self.fresh_env("home-nested")
        r = run(["bash", self.fw / "setup.sh", "--agents", "none"], self.fw, env, stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        nested = self.fw / "MemexVault"
        self.assertTrue((nested / ".memex" / "vault.json").exists())
        self.assertEqual(self.git("status", "--porcelain", cwd=self.fw).stdout, "")
        self.assertEqual(self.git("check-ignore", "-q", "MemexVault/", cwd=self.fw).returncode, 0)
        # another name inside the framework gets added to .git/info/exclude
        r = self.memex("init", str(self.fw / "Other Vault"), env=env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git("status", "--porcelain", cwd=self.fw).stdout, "")
        # the guard protects the framework folder that holds the vault
        genv = {**env, "MEMEX_VAULT": str(nested)}
        for cmd in (f"rm -rf '{self.fw}'", "git clean -ffdx", "git clean -fdX", "git stash --all", "git -C . clean -xf"):
            self.assertDenied({"tool_name": "Bash", "cwd": str(self.fw), "tool_input": {"command": cmd}}, genv)
        for cmd in ("git clean -fd", "git status", "git stash -u"):
            self.assertAllowed({"tool_name": "Bash", "cwd": str(self.fw), "tool_input": {"command": cmd}}, genv)
        # a vault whose own .git went missing must not commit into the framework repo
        shutil.move(str(nested / ".git"), str(self.tmp / "nested-git"))
        try:
            r = self.memex("commit", "x: y", env=genv)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no git repository of its own", r.stderr)
            self.assertEqual(self.git("status", "--porcelain", cwd=self.fw).stdout, "")
        finally:
            shutil.move(str(self.tmp / "nested-git"), str(nested / ".git"))

    def test_refuses_other_repos_and_the_framework(self):
        other = self.tmp / "other-repo"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        r = self.memex("init", str(other / "vault"), cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("inside another git repository", r.stderr)
        r = self.memex("init", str(self.fw), cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)

    def test_refuses_a_folder_that_is_not_empty(self):
        taken = self.tmp / "taken"
        taken.mkdir()
        (taken / "notes.txt").write_text("mine\n")
        r = run(["bash", self.fw / "setup.sh", "--vault", taken, "--agents", "none"], self.tmp,
                self.fresh_env("home-taken"), stdin="")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("isn't empty", r.stderr)
        self.assertEqual(sorted(p.name for p in taken.iterdir()), ["notes.txt"])

    def test_framework_precommit_refuses_knowledge(self):
        p = self.fw / "wiki" / "x.md"
        p.parent.mkdir()
        p.write_text("knowledge\n")
        self.git("add", "-f", "wiki/x.md", cwd=self.fw)
        r = self.git("commit", "-qm", "oops", cwd=self.fw)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("knowledge files staged in the framework", r.stderr)
        self.git("rm", "-q", "--cached", "wiki/x.md", cwd=self.fw)
        shutil.rmtree(p.parent)


class TestSync(Sandbox):
    def test_idempotent(self):
        self.memex("sync")
        r = self.memex("sync")
        self.assertEqual(r.stdout.strip(), "up to date")
        self.assertEqual(self.memex("sync", "--check").returncode, 0)

    def test_hand_edit_is_backed_up_and_replaced(self):
        skill = self.V / ".claude/skills/ask/SKILL.md"
        original = skill.read_text()
        skill.write_text(original + "\nhand edit\n")
        r = self.memex("sync")
        self.assertIn("backed up", r.stdout)
        self.assertEqual(skill.read_text(), original)
        self.assertTrue(list((self.V / ".memex/backup").glob("*/.claude/skills/ask/SKILL.md")))

    def test_framework_change_reaches_the_vault_at_session_start(self):
        src = self.fw / "skills/ask/SKILL.md"
        original = src.read_text()
        src.write_text(original + "\nnew framework rule\n")
        try:
            r = self.memex("context")
            self.assertIn("Framework updated", r.stdout)
            self.assertIn("new framework rule", (self.V / ".claude/skills/ask/SKILL.md").read_text())
        finally:
            src.write_text(original)
            self.memex("sync")

    def test_local_rules_are_rendered(self):
        local = self.V / ".memex/local.md"
        original = local.read_text()
        local.write_text(original + "\n- Customers appear only as codenames.\n")
        try:
            self.memex("sync")
            agents = (self.V / "AGENTS.md").read_text()
            self.assertIn("- Customers appear only as codenames.", agents.split("## Vault-specific rules")[1])
        finally:
            local.write_text(original)
            self.memex("sync")

    def test_vault_domains(self):
        settings = self.V / ".memex/vault.json"
        original = settings.read_text()
        cfg = json.loads(original)
        cfg["domains"] = ["work", "learning"]
        settings.write_text(json.dumps(cfg))
        (self.V / ".memex/domains.json").write_text(json.dumps(
            {"domains": {"work": {"extends": "engineering", "title": "Work Index", "summary": "employer work"}}}))
        (self.V / ".memex/domains").mkdir()
        (self.V / ".memex/domains/work.md").write_text("- Customers appear only as codenames.\n")
        try:
            r = self.memex("sync")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((self.V / "wiki/work/decisions").is_dir())
            self.assertTrue((self.V / "wiki/work/Work Index.md").exists())
            self.assertFalse((self.V / "wiki/personal/Personal Index.md").exists())
            rule = (self.V / ".claude/rules/work.md").read_text()
            self.assertIn('"wiki/work/**"', rule)
            self.assertIn("Engineering domain", rule)
            self.assertIn("codenames", rule)
            self.assertFalse((self.V / ".claude/rules/personal.md").exists())
            self.assertIn("`wiki/work/`: employer work", (self.V / "AGENTS.md").read_text())
            ok = self.memex("capture", "--title", "Work Note", "--domain", "work", stdin="note\n")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            bad = self.memex("capture", "--title", "Personal Note", "--domain", "personal", stdin="note\n")
            self.assertNotEqual(bad.returncode, 0)
            for f in (self.V / "inbox").glob("* Work Note.md"):
                f.unlink()
        finally:
            settings.write_text(original)
            (self.V / ".memex/domains.json").unlink()
            shutil.rmtree(self.V / ".memex/domains")
            self.memex("sync")
        self.assertTrue((self.V / "wiki/personal/Personal Index.md").exists())
        self.assertTrue((self.V / ".claude/rules/personal.md").exists())


class TestGuard(Sandbox):
    def edit(self, path, old, new):
        return {"tool_name": "Edit", "tool_input": {"file_path": str(self.V / path), "old_string": old, "new_string": new}}

    def test_raw_notes_journal(self):
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / RAW), "content": "x"}})
        self.assertAllowed({"tool_name": "Write", "tool_input": {"file_path": str(self.V / "raw/learning/2026-09-30 New.md"), "content": "x"}})
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / "notes/Mine.md"), "content": "x"}})
        self.assertAllowed(self.edit(JOURNAL, "> old brief", "> new brief"))
        self.assertDenied(self.edit(JOURNAL, "- my thought", "- rewritten"))

    def test_mine_blocks_and_shrinking(self):
        self.assertDenied(self.edit(PAGE, "> Use them on every payment endpoint.", "> Use them sometimes."))
        self.assertAllowed(self.edit(PAGE, "## Content", "## Details"))
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / PAGE), "content": "---\ntype: concept\n---\n"}})

    def test_deletes_and_moves(self):
        self.assertIn("memex rm", self.assertDenied({"tool_name": "Bash", "tool_input": {"command": f"rm '{OTHER}'"}}))
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": f"mv '{OTHER}' wiki/x.md"}})
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": "git clean -fd"}})
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": f"echo x > '{RAW}'"}})
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": f"rm -rf '{self.V}'"}, "cwd": str(self.tmp)})

    def test_case_insensitive_paths(self):
        if sys.platform != "darwin":
            self.skipTest("case-insensitive filesystem only")
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": f"rm '{OTHER.lower()}'"}})

    def test_managed_and_owner_files(self):
        self.assertIn("rendered from the Memex framework",
                      self.assertDenied(self.edit(".claude/skills/ask/SKILL.md", "read-only", "writable")))
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / ".claude/skills/new/SKILL.md"), "content": "x"}})
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / "AGENTS.md"), "content": "x"}})
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": "sed -i '' 's/a/b/' CLAUDE.md"}})
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / ".memex/vault.json"), "content": "{}"}})
        self.assertAllowed({"tool_name": "Write", "tool_input": {"file_path": str(self.V / ".memex/local.md"), "content": "x"}})

    def test_codex_and_hermes_payloads(self):
        patch = f"*** Begin Patch\n*** Delete File: {self.V / OTHER}\n*** End Patch\n"
        self.assertDenied({"tool_name": "apply_patch", "tool_input": {"command": patch}})
        patch = f"*** Begin Patch\n*** Update File: {self.V / RAW}\n@@\n-Idempotency keys let clients retry safely.\n+changed\n*** End Patch\n"
        self.assertDenied({"tool_name": "apply_patch", "tool_input": {"command": patch}})
        self.assertDenied({"tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", f"rm '{OTHER}'"]}})
        self.assertDenied({"tool_name": "terminal", "tool_input": {"command": f"rm '{OTHER}'"}})
        self.assertDenied({"tool_name": "write_file", "tool_input": {"path": str(self.V / "notes/x.md"), "content": "x"}})

    def test_owner_only_operations(self):
        bash = lambda c, cwd=None: {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": str(cwd or self.V)}  # noqa: E731
        for cmd in ("memex remote set git@github.com:me/v.git --yes", "memex remote remove", "memex move ~/elsewhere",
                    "memex uninstall --delete-vault --confirm x", f"python3 '{self.fw}/tools/memex.py' remote set x",
                    f"bash '{self.fw}/setup.sh' --remote git@github.com:me/v.git", "bash setup.sh --uninstall",
                    "bash setup.sh --remove-agents", f"python3 '{self.fw}/tools/vault.py' uninstall",
                    "git push origin main", "MEMEX_PUSH=1 git push", "git remote add origin x",
                    "git remote set-url origin x", "git config remote.origin.url x", f"cd /tmp && git -C '{self.V}' push"):
            in_vault = cmd.startswith(("git", "MEMEX", "cd"))  # the rest are denied from any folder
            self.assertDenied(bash(cmd, None if in_vault else self.tmp))
        self.assertIn("run this in their terminal", self.assertDenied(bash("memex uninstall")))
        for cmd in ("memex remote", "memex pull", "memex pull --merge", "memex push", "memex commit 'x: y'",
                    "git remote -v", "git config --get remote.origin.url", "bash setup.sh --vault x"):
            self.assertAllowed(bash(cmd))
        self.assertAllowed(bash("git push origin main", self.tmp))  # other repos are the agent's business
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / ".memex/remote.json"), "content": "{}"}})
        self.assertDenied({"tool_name": "terminal", "tool_input": {"command": "memex move /tmp/x"}})

    def test_agents_outside_the_vault_only_capture(self):
        out = str(self.tmp)  # a session in another project
        bash = lambda c: {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": out}  # noqa: E731
        patch = f"*** Begin Patch\n*** Add File: {self.V / 'wiki/learning/concepts/New.md'}\n+x\n*** End Patch\n"
        for payload in (
                {"tool_name": "Write", "cwd": out, "tool_input": {"file_path": str(self.V / "wiki/learning/concepts/New.md"), "content": "x"}},
                {"tool_name": "Edit", "cwd": out, "tool_input": {"file_path": str(self.V / PAGE), "old_string": "## Content", "new_string": "## Details"}},
                {"tool_name": "Write", "cwd": out, "tool_input": {"file_path": str(self.V / "inbox/Direct.md"), "content": "x"}},
                {"tool_name": "apply_patch", "cwd": out, "tool_input": {"command": patch}},
                {"tool_name": "write_file", "cwd": out, "tool_input": {"path": str(self.V / "outputs/x.md"), "content": "x"}},
                bash(f"echo x >> '{self.V / PAGE}'"), bash(f"sed -i '' 's/a/b/' '{self.V / OTHER}'"),
                bash(f"cp notes.txt '{self.V}/wiki/x.md'"), bash(f"mv '{self.V / OTHER}' old.md"),
                bash(f"cd '{self.V}' && echo x > wiki/y.md"), bash(f"rm '{self.V / OTHER}'"),
                bash(f"memex rm '{OTHER}'"), bash("memex file 'inbox/2026-09-27 Some Capture.md' --domain learning"),
                bash("obsidian move path=wiki/a.md to=wiki/b.md")):
            self.assertIn("memex capture", self.assertDenied(payload))
        for cmd in ("memex capture --title X --domain learning --why y", "memex search retry", f"cat '{self.V / PAGE}'",
                    "memex read 'Idempotency Keys'", "obsidian search query=retry", "echo x > notes.txt"):
            self.assertAllowed(bash(cmd))
        self.assertAllowed(self.edit(PAGE, "## Content", "## Details"))  # the same edit from a vault session
        self.assertAllowed({"tool_name": "Bash", "tool_input": {"command": f"echo x > '{self.V}/inbox/x.md'"}})

    def test_outside_the_vault_and_without_config(self):
        self.assertAllowed({"tool_name": "Write", "tool_input": {"file_path": str(self.tmp / "elsewhere.md"), "content": "x"}})
        self.assertAllowed({"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "cwd": str(self.tmp)})
        empty = self.tmp / "no-config-home"
        empty.mkdir(exist_ok=True)
        self.assertAllowed({"tool_name": "Write", "tool_input": {"file_path": str(self.V / RAW), "content": "x"}},
                           {**self.env, "HOME": str(empty)})


class TestCLI(Sandbox):
    def test_path_search_read(self):
        self.assertEqual(Path(self.memex("path").stdout.strip()), self.V)
        self.assertIn(PAGE, self.memex("search", "idempotency").stdout)
        self.assertIn("Use them on every payment endpoint", self.memex("read", "Idempotency Key").stdout)

    def test_capture(self):
        r = self.memex("capture", "--title", "Timezone Bug", "--domain", "learning", "--why", "recurs", stdin="cause: TZ\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.V / r.stdout.strip()).read_text()
        self.assertIn("domain: learning", text)
        (self.V / r.stdout.strip()).unlink()
        r = self.memex("capture", "--title", "Leak", stdin=f"key {FAKE_KEY}\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("capture refused", r.stderr)

    def test_capture_refuses_a_likely_duplicate(self):
        page = self.V / "wiki/learning/concepts/Retry Budget Exhaustion.md"
        page.write_text("---\ntype: concept\ndomain: learning\nsummary: Workers stop retrying when the budget runs out.\n"
                        "---\nx\n")
        made = []
        try:
            args = ["capture", "--title", "Retry budget exhaustion in workers", "--domain", "learning"]
            r = self.memex(*args, stdin="note\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("capture refused: Memex may already have this", r.stderr)
            self.assertIn("concepts/Retry Budget Exhaustion.md", r.stderr)
            for extra in (["--new"], ["--action", "update", "--target", "Retry Budget Exhaustion"]):
                r = self.memex(*args, *extra, stdin="note\n")
                self.assertEqual(r.returncode, 0, r.stderr)
                made.append(self.V / r.stdout.strip())
        finally:
            page.unlink()
            for p in made:
                p.unlink()

    def test_context_flags_a_stale_hot_md(self):
        self.assertNotIn("may be out of date", self.memex("context").stdout)
        hot = self.V / "wiki/hot.md"
        saved = hot.read_text()
        hot.write_text(saved + "\n")
        try:
            r = self.memex("commit", "close: old hot", env={**self.env, "GIT_COMMITTER_DATE": "2026-01-01T12:00:00"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("(Last rewritten 2026-01-01", self.memex("context").stdout)
        finally:
            hot.write_text(saved)
            self.memex("commit", "close: hot")
        self.assertNotIn("may be out of date", self.memex("context").stdout)

    def test_file_moves_inbox_items_into_raw(self):
        (self.V / "inbox/clip.md").write_text("---\ntitle: Clip\n---\nbody\n")
        page = self.V / "wiki/learning/concepts/Links Clip.md"
        page.write_text("---\ntype: concept\n---\nSee [[clip#Intro|the clip]] and ![[clip]].\n")
        r = self.memex("file", "inbox/clip.md", "--domain", "learning", "--name", "2026-09-29 Great Clip.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "raw/learning/2026-09-29 Great Clip.md")
        self.assertIn("[[2026-09-29 Great Clip#Intro|the clip]] and ![[2026-09-29 Great Clip]]", page.read_text())
        (self.V / "inbox/again.md").write_text("x\n")
        self.assertNotEqual(self.memex("file", "inbox/again.md", "--domain", "learning", "--name",
                                       "2026-09-29 Great Clip.md").returncode, 0)  # raw/ is create-only
        self.assertNotEqual(self.memex("file", PAGE, "--domain", "learning").returncode, 0)  # only inbox items
        self.assertNotEqual(self.memex("file", "inbox/again.md", "--domain", "nope").returncode, 0)
        page.unlink()
        (self.V / "inbox/again.md").unlink()

    def test_rm_checks_backlinks(self):
        r = self.memex("rm", PAGE)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("linked from", r.stderr)
        extra = self.V / "wiki/learning/concepts/Unlinked.md"
        extra.write_text("---\ntype: concept\n---\nx\n")
        r = self.memex("rm", "wiki/learning/concepts/Unlinked.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.V / ".trash/wiki/learning/concepts/Unlinked.md").exists())

    def test_ctx_lint_and_context(self):
        self.assertIn("Inbox:", self.memex("ctx", "ingest").stdout)
        r = self.memex("lint", "--quick")
        self.assertEqual(r.returncode, 0, r.stdout)
        out = self.memex("context").stdout
        self.assertIn("Vault `My Vault`", out)
        self.assertIn("## wiki/hot.md", out)
        self.assertNotIn("WARNING", out)

    def test_ctx_auto_inbox_lists_everything_and_pending_changes(self):
        made = [self.V / f"inbox/2026-10-01 Item {i:02d}.md" for i in range(25)]
        for p in made:
            p.write_text("x\n")
        page = self.V / "wiki/learning/concepts/Half Done.md"
        page.write_text("---\ntype: concept\n---\nx\n")
        try:
            out = self.memex("ctx", "auto-inbox").stdout
            self.assertIn("2026-10-01 Item 24.md", out)
            self.assertNotIn("more", out)
            self.assertIn("Uncommitted changes outside inbox/: 1", out)
            self.assertIn("?? wiki/learning/concepts/Half Done.md", out)
        finally:
            for p in made + [page]:
                p.unlink()
        self.assertIn("Uncommitted changes outside inbox/: none", self.memex("ctx", "auto-inbox").stdout)

    def test_hermes_hook(self):
        r = self.memex("hook", "hermes", stdin=json.dumps({"cwd": str(self.tmp), "extra": {"is_first_turn": True}}))
        self.assertIn("memex search", json.loads(r.stdout)["context"])


class TestRetrieval(Sandbox):
    def test_bm25_ranks_titles_first(self):
        out = self.memex("search", "idempotency").stdout.splitlines()
        self.assertTrue(out[1].startswith(f"- {PAGE}"), out)  # title match beats the body mention in OTHER
        self.assertIn(OTHER, "\n".join(out))
        self.assertIn("partial matches", self.memex("search", "idempotency", "zebra").stdout)
        hits = json.loads(self.memex("search", "retry", "--json").stdout)
        self.assertEqual(hits[0]["path"], OTHER)

    def test_search_covers_the_inbox_and_labels_it(self):
        out = self.memex("search", "hello").stdout
        self.assertIn("inbox/2026-09-27 Some Capture.md", out)
        self.assertIn("[inbox: captured, not compiled or reviewed yet]", out)
        self.assertIn("No matches", self.memex("search", "hello", "--in", "wiki").stdout)
        hits = json.loads(self.memex("search", "hello", "--json").stdout)
        self.assertTrue(hits[0]["inbox"])

    def test_search_folds_sources_a_compiled_page_cites(self):
        made = {
            "wiki/learning/sources/Backoff Article 2026-09-21.md":
                "---\ntype: source\ndomain: learning\nraw: \"[[2026-09-21 Backoff Article]]\"\nsummary: Backoff "
                "spreads retries.\n---\nBackoff spreads retries out.\n",
            "wiki/learning/concepts/Backoff.md":
                "---\ntype: concept\ndomain: learning\nsummary: Waiting longer between retries.\nsources: "
                "[\"[[2026-09-21 Backoff Article]]\"]\n---\nBackoff spreads retries ([[2026-09-21 Backoff Article|src]]).\n",
        }
        for rel_path, body in made.items():
            (self.V / rel_path).write_text(body)
        try:
            out = self.memex("search", "backoff").stdout
            self.assertIn("1 source page(s) folded into the pages that cite them", out)
            self.assertIn("concepts/Backoff.md", out)
            self.assertNotIn("sources/Backoff Article", out)
            self.assertIn("sources/Backoff Article", self.memex("search", "backoff", "--all-sources").stdout)
        finally:
            for rel_path in made:
                (self.V / rel_path).unlink()

    def test_compiled_pages_rank_above_sources_and_exact_titles_first(self):
        made = {
            "wiki/learning/sources/Jitter Note 2026-09-21.md":
                "---\ntype: source\ndomain: learning\nsummary: Jitter spreads retries.\n---\nJitter spreads retries.\n",
            "wiki/learning/concepts/Jitter.md":
                "---\ntype: concept\ndomain: learning\nsummary: Random delay added to retries.\n---\n"
                + "Jitter spreads retries over time.\n" + "Background filler about distributed systems.\n" * 6,
        }
        for k in range(7):
            made[f"wiki/learning/sources/Zeta Note {k} 2026-09-2{k}.md"] = f"---\ntype: source\n---\nzeta {k}\n"
        for rel_path, body in made.items():
            (self.V / rel_path).write_text(body)
        try:
            out = self.memex("search", "jitter").stdout.splitlines()
            self.assertTrue(out[1].startswith("- wiki/learning/concepts/Jitter.md"), out)  # exact title, long page
            out = self.memex("search", "jitter", "retries").stdout.splitlines()
            self.assertTrue(out[1].startswith("- wiki/learning/concepts/Jitter.md"), out)  # compiled beats source
            out = self.memex("search", "zeta").stdout
            self.assertIn("7 match(es); showing 5 (--limit 7 for all)", out)
            self.assertEqual(out.count("\n- "), 5)
        finally:
            for rel_path in made:
                (self.V / rel_path).unlink()

    def test_sections_and_outline(self):
        r = self.memex("read", "Idempotency Keys#Content")
        self.assertIn("## Content", r.stdout)
        self.assertIn("retry safely", r.stdout)
        self.assertNotIn("## Sources", r.stdout)
        self.assertNotEqual(self.memex("read", "Idempotency Keys#Nope").returncode, 0)
        self.assertIn("- Content (", self.memex("outline", "Idempotency Keys").stdout)

    def test_related(self):
        out = self.memex("related", "Idempotency Keys").stdout
        self.assertIn(f"## Links here\n- {OTHER}", out)
        self.assertIn(RAW, out)


def claude_transcript(path: Path, extra_calls=4):
    lines = [
        {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "gitBranch": "fix/tz",
         "message": {"role": "user", "content": "Fix the flaky invoice test"}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta noise"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Looking."}, {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -k invoice"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "FAILED"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "tests/test_invoice.py", "old_string": "a", "new_string": "b"}}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": 'git commit -m "fix: freeze time in invoice test"'}}]}},
        *[{"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": f"ls step{i}"}}]}} for i in range(extra_calls)],
        {"type": "assistant", "timestamp": "2026-09-30T10:20:00Z", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"Root cause: date.today() vs UTC in the fixture. Found {FAKE_KEY} in .env too."}]}},
    ]
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))


def codex_transcript(path: Path):
    call = lambda cmd: {"timestamp": "2026-09-30T11:01:00Z", "type": "response_item", "payload": {  # noqa: E731
        "type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["bash", "-lc", cmd]})}}
    lines = [
        {"timestamp": "2026-09-30T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-1"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Add retry backoff"}]}},
        call("pytest tests/"), call("rg retry"), call("ls src"), call("cat src/retry.py"),
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                              "input": "*** Begin Patch\n*** Update File: src/retry.py\n@@\n-a\n+b\n*** End Patch\n"}},
        # current Codex "code mode": tools are called from JavaScript inside an exec call
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "input":
            'const r = await tools.exec_command({cmd:"npm test -- retry",workdir:"/x"}); text(JSON.stringify(r))'}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<recommended_plugins>\nsome plugins\n</recommended_plugins>\nAlso add jitter"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Added exponential backoff."}]}},
    ]
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))


class TestSessions(Sandbox):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = cls.tmp / "code" / "payments-api"
        cls.repo.mkdir(parents=True)
        run(["git", "init", "-q"], cls.repo, cls.env)
        run(["git", "remote", "add", "origin", "git@github.com:acme/payments-api.git"], cls.repo, cls.env)
        (cls.tmp / "code" / "other").mkdir()
        system = cls.V / "wiki/engineering/systems/Payments API.md"
        system.write_text("---\ntype: system\ndomain: engineering\nstatus: active\nsummary: Invoicing service; owns due "
                          "dates and retries.\ncreated: 2026-09-01\nupdated: 2026-09-20\nrepo: acme/payments-api\n---\n"
                          "## Open loops\n- Rahul owes: the retry budget numbers\n\n## Tasks\n- [ ] Freeze time in tests\n")
        (cls.V / "wiki/engineering/decisions/0001 Use UTC Everywhere.md").write_text(
            "---\ntype: decision\ndomain: engineering\nstatus: accepted\nsummary: Store and compare times in UTC.\n"
            "created: 2026-09-02\nupdated: 2026-09-02\n---\nApplies to [[Payments API]].\n")
        cls.transcripts = cls.tmp / "transcripts"
        cls.transcripts.mkdir()
        claude_transcript(cls.transcripts / "claude.jsonl")
        codex_transcript(cls.transcripts / "codex.jsonl")
        claude_transcript(cls.transcripts / "tiny.jsonl", extra_calls=0)
        (cls.transcripts / "tiny.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")

    def hook(self, event, payload, agent=None):
        args = ["hook", event] + (["--agent", agent] if agent else [])
        return self.memex(*args, stdin=json.dumps(payload), cwd=self.tmp)

    def ledger(self):
        p = self.V / ".memex/sessions.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_recall_at_session_start(self):
        out = self.hook("session-start", {"cwd": str(self.repo)}).stdout
        self.assertIn("[[Payments API]]", out)
        self.assertIn("open: Freeze time in tests", out)
        self.assertIn("open: Rahul owes", out)
        self.assertIn("[[0001 Use UTC Everywhere]] (accepted)", out)
        self.assertLessEqual(len(out.splitlines()), 15)
        self.assertEqual(self.hook("session-start", {"cwd": str(self.tmp / "code" / "other")}).stdout, "")
        self.assertEqual(self.hook("session-start", {"cwd": str(self.V)}).stdout, "")

    def test_recall_puts_playbooks_first_via_applies_to(self):
        book = self.V / "wiki/engineering/playbooks/Running Payments Tests.md"
        book.write_text("---\ntype: playbook\ndomain: engineering\nstatus: active\nsummary: How to run the suite.\n"
                        "applies_to: [payments-api]\nrecall: pytest needs TZ=UTC or the invoice tests fail\n"
                        "verified: 2026-09-25\ncreated: 2026-09-25\nupdated: 2026-09-25\n---\nSteps.\n")
        try:
            out = self.hook("session-start", {"cwd": str(self.repo)}).stdout
            self.assertIn("Before building, testing or running anything here:\n- [[Running Payments Tests]] "
                          "(playbook, verified 2026-09-25): pytest needs TZ=UTC or the invoice tests fail", out)
            self.assertLess(out.index("Running Payments Tests"), out.index("[[Payments API]]"))
            self.assertIn("Background (may be out of date", out)
            self.assertIn("[[Payments API]] (system, updated 2026-09-20, not verified against code)", out)
            self.assertLessEqual(len(out.splitlines()), 15)
            self.assertTrue(out.rstrip().splitlines()[-1].startswith("More:"))
            book.write_text(book.read_text().replace(
                "recall: pytest needs TZ=UTC or the invoice tests fail\n",
                "recall:\n" + "".join(f"- step {i}: venv/bin/python -m pytest\n" for i in range(1, 8))))
            out = self.hook("session-start", {"cwd": str(self.repo)}).stdout  # a list: first item inline, 5 at most
            self.assertIn("(playbook, verified 2026-09-25): step 1: venv/bin/python -m pytest\n  - step 2: ", out)
            self.assertIn("  - step 5: ", out)
            self.assertNotIn("step 6", out)
            self.assertLessEqual(len(out.splitlines()), 15)
        finally:
            book.unlink()

    def test_prompt_hook_names_matching_pages_once_per_session(self):
        ask = {"cwd": str(self.repo), "session_id": "prompt-sess",
               "prompt": "The Payments API retries twice; can you check the invoice due dates?"}
        out = self.hook("prompt", ask).stdout
        self.assertIn("## Memex: pages that match this request\n- [[Payments API]] (system", out)
        self.assertEqual(self.hook("prompt", ask).stdout, "")  # once per session
        self.assertEqual(self.hook("prompt", {**ask, "session_id": "other-sess",
                                              "prompt": "rename the button colour on the login screen"}).stdout, "")
        self.assertEqual(self.hook("prompt", {**ask, "cwd": str(self.V), "session_id": "vault-sess"}).stdout, "")
        self.assertIn("[[Payments API]]", self.hook("session-start", {"cwd": str(self.repo), "session_id": "s2"}).stdout)
        self.assertEqual(self.hook("prompt", {**ask, "session_id": "s2"}).stdout, "")  # shown at start already
        # a subagent's report or a task notice is not the owner asking: no pages, and none used up for the session
        for machine in (f"Another session sent a message:\n<agent-message from=\"a1\">{ask['prompt']}</agent-message>",
                        f"<task-notification><result>{ask['prompt']}</result></task-notification>",
                        f"<system-reminder id=\"r1\">{ask['prompt']}</system-reminder>\nrename the button"):
            self.assertEqual(self.hook("prompt", {**ask, "session_id": "s3", "prompt": machine}).stdout, "")
        self.assertIn("[[Payments API]]", self.hook("prompt", {**ask, "session_id": "s3"}).stdout)
        self.assertEqual(self.git("check-ignore", "-q", ".memex/shown/s2").returncode, 0)  # machine-local

    def test_codex_prompt_event_uses_shared_recall_and_limits(self):
        made = []
        for name in ("Invoice Backoff", "Invoice Retry", "Invoice Calendar"):
            p = self.V / f"wiki/engineering/concepts/{name}.md"
            p.write_text(f"---\ntype: concept\nstatus: active\nsummary: {name}.\n---\n")
            made.append(p)
        try:
            payload = {"session_id": "codex-prompt", "turn_id": "turn-1", "hook_event_name": "UserPromptSubmit",
                       "cwd": str(self.repo), "prompt": "Compare Invoice Backoff, Invoice Retry and Invoice Calendar"}
            out = self.hook("prompt", payload, "codex")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("## Memex: pages that match this request", out.stdout)
            self.assertEqual(out.stdout.count("- [["), 2)
            again = self.hook("prompt", {**payload, "turn_id": "turn-2"}, "codex").stdout
            self.assertNotEqual(out.stdout, again)  # unseen third page can arrive; shown pages cannot repeat
            first_pages = set(re.findall(r"\[\[([^\]]+)\]\]", out.stdout))
            self.assertFalse(first_pages & set(re.findall(r"\[\[([^\]]+)\]\]", again)))
            self.assertEqual(self.hook("prompt", {**payload, "cwd": str(self.V)}, "codex").stdout, "")
        finally:
            for p in made:
                p.unlink()

    def test_audit_measures_use_retrieval_rediscovery_and_trend(self):
        proj = self.tmp / "cc-projects" / "-code-payments-api"
        proj.mkdir(parents=True, exist_ok=True)
        usage = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 50}

        def session(name, sid, cwd, calls, recall=""):
            lines = [{"type": "attachment", "sessionId": sid, "cwd": cwd, "timestamp": "2026-09-30T10:00:00Z",
                      "attachment": {"type": "hook_success", "hookName": "SessionStart:startup", "content": recall}}]
            for i, (cmd, res) in enumerate(calls):
                tid = f"{sid}-t{i}"
                lines += [{"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": f"2026-09-30T10:{10 + i * 2:02d}:00Z",
                           "message": {"id": f"{sid}-m{i}", "usage": usage, "content": [
                               {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}]}},
                          {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": f"2026-09-30T10:{11 + i * 2:02d}:00Z",
                           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": res}]}}]
            lines.append({"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": "2026-09-30T11:00:00Z",
                          "message": {"id": f"{sid}-end", "usage": usage, "content": [{"type": "text", "text": "done"}]}})
            (proj / name).write_text("".join(json.dumps(l) + "\n" for l in lines))

        cap = "memex capture --title \"Invoice timezone bug fixed by UTC\" --domain engineering --why w <<'EOF'\nx\nEOF"
        session("a.jsonl", "sess-a", str(self.repo), [
            ("memex search refund window", "No matches for 'refund window' in wiki, inbox/. Try synonyms"),
            ('memex read "Payments API"', "<!-- wiki/engineering/systems/Payments API.md -->\nbody " + FAKE_KEY),
            (cap, "inbox/2026-09-30 Invoice timezone bug fixed by UTC.md")],
            recall="## Memex: what the owner's knowledge base says about `payments-api`\n- [[Payments API]] (system): x")
        session("b.jsonl", "sess-b", str(self.repo), [
            (cap.replace("by UTC", "by UTC fixture"), "inbox/2026-09-30 Invoice timezone bug fixed by UTC fixture.md"),
            (cap, "memex: capture refused: Memex may already have this:\n  - inbox/x.md")])
        session("c.jsonl", "sess-c", str(self.V), [])
        bench = self.V / "meta/audit/search-benchmark.tsv"
        bench.parent.mkdir(parents=True, exist_ok=True)
        bench.write_text("# query<TAB>page\npayments api\tPayments API\n")
        env = {**self.env, "MEMEX_TRANSCRIPTS": str(self.tmp / "cc-projects")}
        record, report = self.V / ".memex/audit.jsonl", self.V / f"meta/audit/{datetime.date.today().isoformat()}.md"
        try:
            r = self.memex("audit", "--since", "2026-09-30", "--until", "2026-09-30", "--json", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = json.loads(r.stdout)["metrics"]
            want = {"sessions": 2, "vault_sessions": 1, "lookup_sessions": 1, "lookup_pct": 50.0, "capture_sessions": 2,
                    "captures": 2, "gate_refusals": 1, "queries": 1, "zero_hit_pct": 100.0, "reads": 1,
                    "recall_sessions": 1, "recall_opened_pct": 100.0, "recaptured": 1, "bench_hit1": 1, "bench_hit3": 1}
            self.assertEqual({k: m[k] for k in want}, want)
            self.assertGreater(m["memex_token_pct"], 0)
            r = self.memex("audit", "--since", "2026-09-30", "--until", "2026-09-30", "--record", "--report", env=env)
            self.assertIn("Looked something up: 1 (50.0%)", r.stdout)
            self.assertTrue(report.exists())
            self.assertEqual(len(record.read_text().splitlines()), 1)
            self.assertEqual(self.git("check-ignore", "-q", ".memex/audit.jsonl").returncode, 0)  # machine-local
            out = self.memex("audit", "--since", "2026-09-29", "--until", "2026-09-30", env=env).stdout
            self.assertIn("(was 50.0% in 2026-09-30..2026-09-30)", out)
            self.assertIn("Search benchmark: hit@1 1/1", out)
            listed = self.memex("audit", "--since", "2026-09-30", "--until", "2026-09-30", "--lookups", env=env).stdout
            self.assertIn("a.jsonl · tool sess-a-t0\n$ memex search refund window\nNo matches", listed)
            self.assertNotIn("capture", listed)  # lookups only
            self.assertNotIn(FAKE_KEY, listed)
            self.assertIn("REDACTED", listed)
        finally:
            for p in (bench, record, report):
                if p.exists():
                    p.unlink()

    def test_audit_lists_missed_queries_unopened_recall_and_most_opened_files(self):
        proj = self.tmp / "cc-hot" / "-code-payments-api"
        proj.mkdir(parents=True, exist_ok=True)
        recall = "## Memex: what the owner's knowledge base says about `payments-api`\n- [[Payments API]] (system): x"
        for i in range(5):
            sid, cwd = f"hot-{i}", str(self.repo)
            calls = [("Bash", {"command": "sed -n '1,40p' src/app.py && cat https://example.com/x.py README"}),
                     ("Read", {"file_path": str(self.repo / "src" / "app.py")}),
                     ("Read", {"file_path": "/etc/hosts.json"}),
                     ("Bash", {"command": "memex search refund window"})]
            lines = [{"type": "attachment", "sessionId": sid, "cwd": cwd, "timestamp": "2026-10-05T10:00:00Z",
                      "attachment": {"type": "hook_success", "hookName": "SessionStart:startup", "content": recall}}]
            for j, (name, inp) in enumerate(calls):
                tid = f"{sid}-t{j}"
                res = "No matches for 'refund window' in wiki, inbox/. Try synonyms" if "memex" in str(inp) else "ok"
                lines += [{"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": f"2026-10-05T10:{10 + j}:00Z",
                           "message": {"id": f"{sid}-m{j}", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}},
                          {"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": f"2026-10-05T10:{10 + j}:30Z",
                           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": res}]}}]
            (proj / f"{sid}.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
        env = {**self.env, "MEMEX_TRANSCRIPTS": str(self.tmp / "cc-hot")}
        r = self.memex("audit", "--since", "2026-10-05", "--until", "2026-10-05", "--json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = json.loads(r.stdout)["metrics"]
        self.assertEqual(m["zero_hit_queries"], [["refund window", 5]])
        self.assertEqual(m["recall_unopened"], [["Payments API", 5]])
        self.assertEqual(m["hot_files"], {"payments-api": {"sessions": 5, "files": [["src/app.py", 5]]}})  # in-repo only
        out = self.memex("audit", "--since", "2026-10-05", "--until", "2026-10-05", env=env).stdout
        self.assertIn("- Searched for and not found: 'refund window' (x5)", out)
        self.assertIn("never opened: [[Payments API]] (5 sessions)", out)
        self.assertIn("- payments-api (5 sessions): `src/app.py` (5)", out)

    def test_harvest_skip_captured(self):
        ledger = self.V / ".memex/sessions.jsonl"
        saved = ledger.read_text() if ledger.exists() else None
        path = self.transcripts / "captured.jsonl"
        calls = [{"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": c}}]}}
            for c in ["ls", "pytest", "git diff", "cat a.py", "memex capture --title 'Retry fix' <<'EOF'\nx\nEOF"]]
        path.write_text("".join(json.dumps(l) + "\n" for l in [
            {"type": "user", "timestamp": "2026-09-30T10:00:00Z", "message": {"role": "user", "content": "fix retries"}},
            *calls]))
        try:
            self.hook("session-end", {"session_id": "cap-sess-0009", "transcript_path": str(path), "cwd": str(self.repo)},
                      "claude")
            self.assertIn("cap-sess", self.memex("harvest").stdout)
            self.assertIn("marked 1 session(s) that captured inline as skipped", self.memex("harvest", "--skip-captured").stdout)
            self.assertNotIn("cap-sess", self.memex("harvest").stdout)
        finally:
            path.unlink()
            if saved is None:
                ledger.unlink(missing_ok=True)
            else:
                ledger.write_text(saved)

    def test_ctx_verify_lists_pages_and_checkouts(self):
        ledger = self.V / ".memex/sessions.jsonl"
        saved = ledger.read_text() if ledger.exists() else None
        try:
            self.hook("session-end", {"session_id": "verify-sess", "cwd": str(self.repo)}, "claude")
            today = datetime.date.today().isoformat()
            for name, updated, checked in (("Current Runbook", today, today), ("Changed Runbook", today, "2026-09-01")):
                (self.V / f"wiki/engineering/playbooks/{name}.md").write_text(
                    f"---\ntype: playbook\nstatus: active\napplies_to: [payments-api]\nupdated: {updated}\n"
                    f"verified: {checked}\n---\nSteps.\n")
            out = self.memex("ctx", "verify").stdout
            self.assertIn("Due for /verify: 2 of 3", out)
            self.assertIn("- Changed Runbook (playbook; repo: payments-api): DUE, changed " + today, out)
            self.assertIn("- Payments API (system; repo: acme/payments-api): DUE, never verified", out)
            self.assertLess(out.index("Changed Runbook"), out.index("Payments API"))  # playbooks first
            self.assertIn(f"Current (not due): Current Runbook (checked {today})", out)
            self.assertIn(f"- payments-api: {self.repo}", out)
        finally:
            for name in ("Current Runbook", "Changed Runbook"):
                (self.V / f"wiki/engineering/playbooks/{name}.md").unlink(missing_ok=True)
            if saved is None:
                ledger.unlink(missing_ok=True)
            else:
                ledger.write_text(saved)

    def test_half_filed_raw_is_reported(self):
        half = self.V / "raw/learning/2026-09-02 Half Filed.md"
        half.write_text("---\ntitle: Half Filed\n---\nx\n")
        snap = self.V / "raw/learning/2026-09-02 Code check payments abc1234.md"  # a /verify run in progress
        snap.write_text("---\ntitle: Code check\nsource_type: code-snapshot\n---\nx\n")
        try:
            ctx = self.memex("context").stdout
            self.assertIn("WARNING: 1 file(s) in raw/ are filed but not committed", ctx)
            self.assertIn("raw/learning/2026-09-02 Half Filed.md", ctx)
            self.assertNotIn("Code check payments", ctx)
        finally:
            half.unlink()
            snap.unlink()
        self.assertNotIn("filed but not committed", self.memex("context").stdout)

    def test_stall_alarm_only_when_no_commit_followed_a_waiting_capture(self):
        import time
        other = {"cwd": str(self.tmp / "code" / "other")}
        cfg = self.V / ".memex/vault.json"
        saved = cfg.read_text()
        data = json.loads(saved)
        data["recall"] = {**data.get("recall", {}), "stall_hours": 0.0005}  # 1.8 s
        cfg.write_text(json.dumps(data))
        item = self.V / "inbox/2026-09-01 Forgotten Capture.md"
        try:
            time.sleep(2.1)  # the capture must arrive after the last commit's second
            item.write_text("---\ntitle: Forgotten\n---\nx\n")
            time.sleep(2.2)
            out = self.hook("session-start", other).stdout
            self.assertIn("Memex inbox looks stalled: 1 capture(s) have waited since", out)
            self.assertIn("Tell the owner once", out)
            self.assertIn("WARNING: the inbox looks stalled: 1 capture(s)", self.memex("context").stdout)
            # an inbox run that commits anything has seen the capture: leaving it for the owner is not a stall
            self.assertEqual(self.memex("commit", "inbox: auto 0 items").returncode, 0)
            self.assertEqual(self.hook("session-start", other).stdout, "")
            self.assertNotIn("looks stalled", self.memex("context").stdout)
        finally:
            cfg.write_text(saved)
            if item.exists():
                item.unlink()
            self.memex("commit", "test: cleanup")

    def test_ledger_harvest_and_digests(self):
        import time
        start = time.time()
        self.hook("session-end", {"session_id": "claude-sess-0001", "transcript_path": str(self.transcripts / "claude.jsonl"),
                                  "cwd": str(self.repo), "reason": "prompt_input_exit"}, "claude")
        self.assertLess(time.time() - start, 1.5)
        self.hook("session-end", {"session_id": "codex-sess-0002", "transcript_path": str(self.transcripts / "codex.jsonl"),
                                  "cwd": str(self.repo)}, "codex")
        self.hook("session-end", {"session_id": "tiny-sess-0003", "transcript_path": str(self.transcripts / "tiny.jsonl"),
                                  "cwd": str(self.repo)}, "claude")
        self.hook("session-end", {"session_id": "vault-sess", "cwd": str(self.V)}, "claude")  # vault sessions are skipped
        self.assertEqual([r["session_id"] for r in self.ledger()], ["claude-sess-0001", "codex-sess-0002", "tiny-sess-0003"])
        self.assertEqual(self.ledger()[0]["repo"], "payments-api")

        listing = self.memex("harvest").stdout
        self.assertIn("claude-s", listing)
        self.assertIn("codex-se", listing)
        self.assertIn("1 trivial hidden", listing)
        self.assertIn("tiny-ses", self.memex("harvest", "--all").stdout)
        self.assertIn("Sessions to harvest: 2", self.memex("ctx", "harvest").stdout)

        r = self.memex("harvest", "--digest", "claude-sess")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AWS access key", r.stderr)
        text = (self.V / r.stdout.strip()).read_text()
        for want in ("Fix the flaky invoice test", "tests/test_invoice.py", "pytest -k invoice",
                     "fix: freeze time in invoice test", "Root cause: date.today()", "[REDACTED: AWS access key]",
                     "source_type: session", "branch: fix/tz", "## Turn 1\n**Owner:** Fix the flaky invoice test",
                     "**Agent:** Root cause: date.today()"):
            self.assertIn(want, text)
        for unwanted in (FAKE_KEY, "meta noise", "/clear"):
            self.assertNotIn(unwanted, text)
        (self.V / r.stdout.strip()).unlink()

        r = self.memex("harvest", "--digest", "codex-sess")
        text = (self.V / r.stdout.strip()).read_text()
        for want in ("Add retry backoff", "src/retry.py", "pytest tests/", "Added exponential backoff.",
                     "npm test -- retry", "Also add jitter"):
            self.assertIn(want, text)
        self.assertNotIn("environment_context", text)
        self.assertNotIn("recommended_plugins", text)
        (self.V / r.stdout.strip()).unlink()

        self.assertIn("marked 1", self.memex("harvest", "--skip-trivial").stdout)
        self.memex("harvest", "--done", "claude-sess")
        self.memex("harvest", "--done", "codex-sess", "--result", "skipped")
        self.assertIn("Nothing to harvest", self.memex("harvest").stdout)

    def test_excluded_folders_are_not_recorded(self):
        settings = self.V / ".memex/vault.json"
        original = settings.read_text()
        cfg = json.loads(original)
        cfg["harvest"]["exclude"] = [str(self.tmp / "code" / "other")]
        settings.write_text(json.dumps(cfg))
        try:
            before = len(self.ledger())
            self.hook("session-end", {"session_id": "secret-1", "cwd": str(self.tmp / "code" / "other")}, "claude")
            self.assertEqual(len(self.ledger()), before)
        finally:
            settings.write_text(original)

    def test_ledger_is_owner_only_for_agents(self):
        self.assertDenied({"tool_name": "Write", "tool_input": {"file_path": str(self.V / ".memex/sessions.jsonl"), "content": ""}})

    def test_unknown_transcript_format_is_never_skipped(self):
        weird = self.transcripts / "weird.jsonl"
        weird.write_text("".join(json.dumps({"kind": "turn", "n": i, "text": "did things"}) + "\n" for i in range(8)))
        self.hook("session-end", {"session_id": "weird-sess-0009", "transcript_path": str(weird), "cwd": str(self.repo)}, "claude")
        self.assertIn("UNPARSED", self.memex("harvest").stdout)
        self.memex("harvest", "--skip-trivial")
        out = self.memex("harvest").stdout
        self.assertIn("weird-se", out)
        self.assertIn("doesn't recognize", out)
        self.assertIn("UNPARSED", self.memex("ctx", "harvest").stdout)
        self.memex("harvest", "--done", "weird-sess", "--result", "skipped")


class TestBackup(Sandbox):
    def test_bundle_restore_and_warnings(self):
        self.assertIn("! no backup yet", self.memex("doctor").stdout)
        self.assertIn("Backup: none yet", self.memex("context").stdout)
        dest = self.tmp / "backups"
        for _ in range(3):
            r = self.memex("backup", "--to", str(dest), "--keep", "2")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bundles = sorted(dest.glob("*.bundle"))
        self.assertEqual(len(bundles), 2)
        restored = self.tmp / "restored"
        self.assertEqual(self.git("clone", "-q", str(bundles[-1]), str(restored), cwd=self.tmp).returncode, 0)
        self.assertIn("test: seed", self.git("log", "--format=%s", cwd=restored).stdout)
        self.assertIn("✓ last backup 0 day(s) ago", self.memex("doctor").stdout)
        self.assertNotIn("Backup:", self.memex("context").stdout)
        self.assertNotEqual(self.memex("backup", "--to", str(self.V / "outputs")).returncode, 0)


class TestRemote(Sandbox):
    """Machine A (the sandbox vault) and machine B (another HOME) syncing through one bare repository."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bare = cls.tmp / "remote.git"
        run(["git", "init", "-q", "--bare", "-b", "main", cls.bare], cls.tmp, cls.env)
        cls.home_b = cls.tmp / "home-b"
        cls.home_b.mkdir()
        cls.env_b = {**cls.env, "HOME": str(cls.home_b), "PATH": f"{cls.home_b}/.local/bin:{os.environ['PATH']}"}
        cls.VB = cls.tmp / "Vault B"

    def bare_log(self):
        return self.git("--git-dir", str(self.bare), "log", "--format=%s", "main", cwd=self.tmp).stdout

    def mb(self, *args, stdin=None):
        return self.memex(*args, stdin=stdin, cwd=self.VB, env=self.env_b)

    def test_a_urls_and_refusals(self):
        sys.path.insert(0, str(SRC / "tools"))
        import remote
        for bad in ("https://me:ghp_secret@github.com/me/v.git", "https://me@github.com/me/v.git"):
            with self.assertRaises(Exception):
                remote.check_url(bad)
        self.assertEqual(remote.check_url("git@github.com:me/v.git"), "git@github.com:me/v.git")
        self.assertEqual(remote.https_form("git@github.com:Me/Vault.git"), "https://github.com/Me/Vault.git")
        self.assertEqual(remote.https_form("ssh://git@gitlab.com:22/me/v.git"), "https://gitlab.com/me/v.git")
        self.assertIsNone(remote.https_form(str(self.bare)))
        self.assertTrue(remote.same_repo("git@github.com:me/v.git", "https://github.com/me/v"))
        self.assertFalse(remote.same_repo("git@github.com:me/v.git", "git@github.com:me/w.git"))
        self.assertEqual(remote.visibility(str(self.bare)), "local")
        r = self.memex("remote", "set", "https://me:tok@github.com/me/v.git", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("token", r.stderr)
        r = self.memex("remote", "set", str(self.bare))  # no terminal and no --yes
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--yes", r.stderr)
        self.assertEqual(self.git("remote").stdout.strip(), "")
        self.assertIn("local-only", self.memex("remote").stdout)

    def test_b_attach_push_and_pre_push(self):
        r = self.memex("remote", "set", str(self.bare), "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("attached", r.stdout)
        self.assertIn("test: seed", self.bare_log())
        self.assertEqual(self.git("ls-files", ".memex/remote.json").stdout, "")  # machine-local
        doc = self.memex("doctor")
        self.assertNotIn("✗", doc.stdout, doc.stdout)
        self.assertIn("remote origin →", doc.stdout)
        self.assertIn("off-machine copy", doc.stdout)
        self.memex("capture", "--title", "Synced Note", "--domain", "learning", stdin="note\n")
        r = self.memex("commit", "inbox: synced note")
        self.assertIn("pushed to origin/main", r.stdout)
        self.assertIn("inbox: synced note", self.bare_log())
        r = self.git("push", "origin", "main")  # raw pushes are refused, even to the attached remote
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("memex push", r.stderr)
        other = self.tmp / "other.git"
        self.git("init", "-q", "--bare", str(other), cwd=self.tmp)
        r = self.git("push", str(other), "main", env={**self.env, "MEMEX_PUSH": "1"})
        self.assertIn("pushes only to", r.stderr)
        leak = self.V / "wiki/learning/concepts/Leak.md"  # a secret that got past pre-commit stops at push
        leak.write_text(f"key {FAKE_KEY}\n")
        self.git("add", str(leak))
        self.git("commit", "-q", "--no-verify", "-m", "leak")
        self.assertIn("possible secrets", self.memex("push").stdout)
        self.assertNotIn("leak", self.bare_log())
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.assertIn("nothing to push", self.memex("push").stdout)

    def test_c_second_machine_joins_and_syncs(self):
        r = run(["bash", self.fw / "setup.sh", "--vault", self.VB, "--remote", self.bare, "--agents", "none"],
                self.tmp, self.env_b, stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("joined the vault", r.stdout)
        self.assertTrue((self.VB / PAGE).exists())
        self.assertIn(str(self.VB), (self.VB / "AGENTS.md").read_text())  # rendered for this machine
        self.assertNotIn("✗", self.mb("doctor").stdout)
        self.mb("capture", "--title", "From B", "--domain", "learning", stdin="b\n")
        self.assertIn("pushed", self.mb("commit", "inbox: from b").stdout)
        self.assertIn("Sync: pulled 1 commit(s)", self.memex("context").stdout)  # A's next session
        self.assertTrue(list((self.V / "inbox").glob("* From B.md")))
        for vault, memex, who in ((self.V, self.memex, "a"), (self.VB, self.mb, "b")):  # both append to the log
            log = vault / "wiki" / "log.md"
            log.write_text(log.read_text().rstrip("\n") + f"\n\n## [2026-10-01] note | from {who}\n")
            r = memex("commit", f"log: from {who}")
            self.assertIn("pushed", r.stdout, r.stdout + r.stderr)  # B's push is rejected, rebases (union merge), pushes
        self.assertIn("from a", (self.VB / "wiki/log.md").read_text())
        self.memex("pull")
        self.assertIn("from b", (self.V / "wiki/log.md").read_text())

    def test_d_conflict_is_reported_then_resolved(self):
        a, b = self.V / OTHER, self.VB / OTHER
        a.write_text(a.read_text().replace("Related:", "Related (A):"))
        self.assertIn("pushed", self.memex("commit", "edit: a").stdout)
        b.write_text(b.read_text().replace("Related:", "Related (B):"))
        r = self.mb("commit", "edit: b")
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("Related (B):", b.read_text())  # nothing changed here
        self.assertNotIn("<<<<<<<", b.read_text())
        self.assertIn("CONFLICT", self.mb("context").stdout)
        r = self.mb("pull", "--merge")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(OTHER, r.stdout)
        self.assertIn("<<<<<<<", b.read_text())
        r = self.mb("commit", "merge: sync")  # markers still in: refused
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unresolved merge conflicts", r.stdout + r.stderr)
        b.write_text(re.sub(r"<<<<<<< [^\n]*\n(.*?)=======\n.*?>>>>>>> [^\n]*\n", r"\1", b.read_text(), flags=re.S)
                     .replace("Related (B):", "Related (A, B):"))
        r = self.mb("commit", "merge: sync")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pushed", r.stdout)
        self.assertNotIn("✗", self.mb("doctor").stdout)
        self.memex("pull")
        self.assertIn("Related (A, B):", a.read_text())

    def test_e_offline_keeps_commits_local(self):
        away = self.tmp / "remote-away.git"
        shutil.move(str(self.bare), str(away))
        try:
            self.memex("capture", "--title", "Offline", "--domain", "learning", stdin="x\n")
            r = self.memex("commit", "inbox: offline")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("push failed", r.stdout)
            self.assertIn("Sync: offline", self.memex("context").stdout)
        finally:
            shutil.move(str(away), str(self.bare))
        self.assertIn("pushed", self.memex("commit", "retry").stdout)
        self.assertIn("inbox: offline", self.bare_log())

    def test_f_remove_makes_it_local_only_again(self):
        self.assertIn("local-only again", self.mb("remote", "remove").stdout)
        self.assertEqual(self.git("remote", cwd=self.VB).stdout.strip(), "")
        r = self.git("push", str(self.bare), "main", cwd=self.VB)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("local-only", r.stderr)


class TestMove(Sandbox):
    def test_move_repoints_everything(self):
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "x").write_text("x")
        r = self.memex("move", str(busy), cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("isn't empty", r.stderr)
        other = self.tmp / "repo"
        other.mkdir()
        self.git("init", "-q", cwd=other)
        self.assertIn("inside another git repository", self.memex("move", str(other / "v"), cwd=self.tmp).stderr)
        head = self.git("rev-parse", "HEAD").stdout
        dest = self.tmp / "Moved" / "Vault"
        r = self.memex("move", str(dest), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.V.exists())
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=dest).stdout, head)
        self.assertEqual(Path(self.memex("path", cwd=self.tmp).stdout.strip()), dest)
        agents = (dest / "AGENTS.md").read_text()
        self.assertIn(str(dest), agents)
        self.assertNotIn(str(self.V), agents)
        allow = json.loads((self.home / ".claude/settings.json").read_text())["permissions"]["allow"]
        self.assertIn(f"Read(/{dest}/**)", allow)
        self.assertNotIn(f"Read(/{self.V}/**)", allow)
        codex = (self.home / ".codex/config.toml").read_text()
        self.assertIn(f'"{dest}"', codex)
        self.assertNotIn(f'"{self.V}"', codex)
        self.assertIn(str(dest), (self.home / ".claude/CLAUDE.md").read_text())
        self.assertNotIn("✗", self.memex("doctor", cwd=dest).stdout)


class TestUninstall(Sandbox):
    def uninstall(self, *args, env=None):
        return run([PY, self.fw / "tools" / "vault.py", "uninstall", *args], self.tmp, env or self.env, stdin="")

    def leftovers(self, vault=None, env=None):
        return run([PY, self.fw / "tools" / "vault.py", "leftovers", *(["--vault", vault] if vault else [])],
                   self.tmp, env or self.env)

    def test_a_keep_the_vault(self):
        self.assertNotEqual(self.leftovers().returncode, 0)  # wired up: plenty to find
        head = self.git("rev-parse", "HEAD").stdout
        r = self.uninstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ no Memex wiring left", r.stdout)
        self.assertEqual(self.leftovers(self.V).returncode, 0)
        for gone in (".local/bin/memex", ".claude/skills/memex", ".agents/skills/memex", ".hermes/skills/memex",
                     ".codex/rules/memex.rules", ".config/memex"):
            self.assertFalse((self.home / gone).exists(), gone)
        self.assertNotIn("memex:begin", text(self.home / ".claude/CLAUDE.md"))
        self.assertNotIn(str(self.fw), text(self.home / ".claude/settings.json"))
        self.assertNotIn(str(self.V), text(self.home / ".codex/config.toml"))
        for empty in (".claude/settings.json", ".claude/CLAUDE.md", ".codex/AGENTS.md", ".agents"):
            self.assertFalse((self.home / empty).exists(), f"{empty}: held only Memex's lines, so it's removed")
        self.assertFalse((self.fw / ".git/hooks/pre-commit").exists())
        self.assertTrue((self.V / PAGE).exists())  # knowledge and history kept
        self.assertEqual(self.git("merge-base", "--is-ancestor", head.strip(), "HEAD").returncode, 0)
        for gone in ("AGENTS.md", "CLAUDE.md", ".claude/skills", ".memex/state.json", ".git/hooks/pre-commit"):
            self.assertFalse((self.V / gone).exists(), gone)
        self.assertIn("local-only", (self.V / ".git/hooks/pre-push").read_text())
        self.assertTrue(list((self.home / "Memex Backups").rglob("*.bundle")))

    def test_b_delete_the_vault(self):
        v2 = self.tmp / "Second Vault"
        r = run(["bash", self.fw / "setup.sh", "--vault", v2, "--agents", "all"], self.tmp, self.env, stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.uninstall("--delete-vault", "--confirm", "wrong name")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--confirm "Second Vault"', r.stderr)
        self.assertTrue((v2 / ".memex/vault.json").exists())
        self.assertTrue((self.home / ".local/bin/memex").exists())  # nothing was changed
        claude_md, settings = self.home / ".claude/CLAUDE.md", self.home / ".claude/settings.json"
        claude_md.write_text("# My own instructions\n\n" + claude_md.read_text())  # the owner's lines must survive
        cfg = json.loads(settings.read_text())
        cfg["theme"] = "dark"
        cfg["permissions"]["allow"].append("Bash(ls *)")
        settings.write_text(json.dumps(cfg))
        r = self.uninstall("--delete-vault", "--confirm", "Second Vault")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(v2.exists())
        self.assertEqual(claude_md.read_text().strip(), "# My own instructions")
        self.assertEqual(json.loads(settings.read_text()), {"theme": "dark", "permissions": {"allow": ["Bash(ls *)"]}})
        bundles = list((self.home / "Memex Backups" / "Second-Vault").glob("*.bundle"))
        self.assertTrue(bundles)
        restored = self.tmp / "restored-v2"
        self.assertEqual(self.git("clone", "-q", str(bundles[0]), str(restored), cwd=self.tmp).returncode, 0)
        self.assertEqual(self.leftovers(v2).returncode, 0)

    def test_c_vault_already_deleted_by_hand(self):
        v3 = self.tmp / "Third Vault"
        r = run(["bash", self.fw / "setup.sh", "--vault", v3, "--agents", "all"], self.tmp, self.env, stdin="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        shutil.rmtree(v3)
        r = self.uninstall()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already gone", r.stdout)
        self.assertEqual(self.leftovers(v3).returncode, 0)


class TestAuditAdapters(unittest.TestCase):
    """Small parser fixtures: no installed agents, model calls or real transcripts."""

    def setUp(self):
        sys.path.insert(0, str(SRC / "tools"))
        import audit
        self.audit = audit
        self.tmp = Path(tempfile.mkdtemp(prefix="memex-audit-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fixture(self, calls, extra=(), name="codex.jsonl", sid="same-session"):
        lines = [{"type": "session_meta", "timestamp": "2026-09-30T10:00:00Z",
                  "payload": {"id": sid, "cwd": str(self.tmp / "code")}}]
        for i, (tool, args, output) in enumerate(calls):
            lines.append({"type": "response_item", "timestamp": f"2026-09-30T10:{i + 1:02d}:00Z",
                          "payload": {"type": "custom_tool_call" if tool == "exec" else "function_call",
                                      "name": tool, "call_id": f"t{i}", "input": args} if tool == "exec" else
                          {"type": "function_call", "name": tool, "call_id": f"t{i}", "arguments": json.dumps(args)}})
            if output is not None:
                lines.append({"type": "response_item", "timestamp": f"2026-09-30T10:{i + 1:02d}:01Z",
                              "payload": {"type": "function_call_output", "call_id": f"t{i}", "output": output}})
        lines += list(extra)
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(l) + "\n" for l in lines))
        return path

    def measured(self, path):
        s = self.audit.parse(path, "codex")
        s.update(kind="work", tools_unique=s["tools"], usage_unique=s["usage"])
        return s, self.audit.measure([s], self.tmp)

    def test_codex_direct_calls_and_modern_usage(self):
        extra = [
            {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [
                {"type": "input_text", "text": "## Memex: pages that match this request\n- [[Payments API]]"}]}},
            {"type": "token_usage_record", "timestamp": "2026-09-30T10:05:00Z", "payload": {
                "response_id": "r1", "usage": {"input_tokens": 100, "cached_input_tokens": 80,
                                                "output_tokens": 10, "reasoning_output_tokens": 4}}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 9999, "output_tokens": 999}}}},
        ]
        s, m = self.measured(self.fixture([
            ("exec_command", {"cmd": "memex search invoice"}, "No matches for 'invoice'"),
            ("shell", {"command": ["bash", "-lc", 'memex read "Payments API"']}, "page"),
            ("exec_command", {"cmd": "memex capture --title 'Invoice UTC fix'"}, "inbox/Invoice UTC fix.md\n"),
        ], extra))
        self.assertEqual((m["sessions"], m["queries"], m["reads"], m["captures"]), (1, 1, 1, 1))
        self.assertEqual((m["zero_hit_pct"], m["search_then_read_sessions"], m["recall_opened_pct"]), (100.0, 1, 100.0))
        self.assertEqual(s["usage"]["r1"]["in"], 100)  # cached tokens already included
        self.assertEqual(s["usage"]["r1"]["out"], 10)  # reasoning already included

    def test_codex_code_mode_bundles_and_capture_outcomes(self):
        code = "; ".join("await tools.exec_command({cmd:" + json.dumps(cmd) + "})" for cmd in (
            "memex search retry", 'memex read "Payments API"; memex read "Retry Budget"',
            'memex capture --title "Retry jitter fixed"'))
        output = json.dumps([{"output": "No matches for retry"}, {"output": "page"},
                             {"output": "inbox/Retry jitter fixed.md\n", "exit_code": 0}])
        _, m = self.measured(self.fixture([("exec", code, [{"type": "text", "text": output}])]))
        self.assertEqual((m["queries"], m["reads"], m["captures"], m["search_then_read_sessions"]), (1, 2, 1, 1))
        self.assertEqual(m["captures_per_read"], 0.5)
        _, m = self.measured(self.fixture([
            ("exec_command", {"cmd": "memex capture --title 'Refused capture'"}, "capture refused: Memex may already have this"),
            ("exec_command", {"cmd": "memex capture --title 'Failed capture'"}, "permission denied"),
            ("exec_command", {"cmd": "memex capture --title 'Pending capture'"}, None),
        ]))
        self.assertEqual((m["captures"], m["gate_refusals"], m["incomplete_memex_calls"], m["unconfirmed_captures"]), (0, 1, 1, 1))

    def test_codex_async_result_is_attached_to_original_call(self):
        _, m = self.measured(self.fixture([
            ("exec_command", {"cmd": "memex capture --title 'Finished capture'"},
             json.dumps({"session_id": 42, "output": "", "exit_code": None})),
            ("write_stdin", {"session_id": 42, "chars": ""},
             json.dumps({"output": "inbox/Finished capture.md\n", "exit_code": 0})),
        ]))
        self.assertEqual((m["captures"], m["incomplete_memex_calls"]), (1, 0))

    def test_codex_legacy_usage_deltas_not_cumulative_sum(self):
        extra = [{"type": "event_msg", "timestamp": f"2026-09-30T10:0{i}:00Z", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"input_tokens": inp, "output_tokens": out}}}}
                 for i, (inp, out) in enumerate(((100, 10), (100, 10), (180, 30)))]
        s, _ = self.measured(self.fixture([], extra))
        self.assertEqual(sum(u["in"] for u in s["usage"].values()), 180)
        self.assertEqual(sum(u["out"] for u in s["usage"].values()), 30)

    def test_audit_discovery_resume_agent_namespaces_and_coverage(self):
        from unittest.mock import patch
        codex = self.tmp / "codex-home"
        original = self.fixture([("exec_command", {"cmd": "memex search retry"}, "No matches for retry")],
                                name="codex-home/sessions/a.jsonl")
        resumed = codex / "archived_sessions/resumed.jsonl"
        resumed.parent.mkdir(parents=True)
        resumed.write_text(original.read_text())
        (codex / "sessions/unknown.jsonl").write_text('{"unknown": 1}\n')
        (codex / "sessions/changed-shape.jsonl").write_text(json.dumps({
            "type": "event_msg", "payload": {"type": "token_count", "info": "unsupported shape"}}) + "\n")
        original.write_text(original.read_text() + 'not-json\n')
        claude = self.tmp / "claude"
        claude.mkdir()
        (claude / "a.jsonl").write_text("\n".join(json.dumps(x) for x in [
            {"type": "assistant", "sessionId": "same-session", "cwd": str(self.tmp / "code"),
             "timestamp": "2026-09-30T10:00:00Z", "message": {"content": [
                 {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "memex search retry"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t0", "content": "No matches for retry"}]}},
        ]))
        coverage = {}
        with patch.dict(os.environ, {"CODEX_HOME": str(codex), "MEMEX_TRANSCRIPTS": str(claude), "MEMEX_CODEX_TRANSCRIPTS": ""}):
            ss = self.audit.load(self.tmp / "vault", self.audit.ts("2026-09-30"), self.audit.ts("2026-10-01"), coverage)
        m = self.audit.measure(ss, self.tmp)
        self.assertEqual((m["sessions"], m["queries"]), (2, 2))
        self.assertEqual((coverage["unsupported"], coverage["parse_warnings"]), (2, 1))
        self.assertEqual(len(ss), 3)  # two Codex transcript copies, one logical session

    def test_prompt_hook_upgrade_and_removal_preserve_unrelated_handlers(self):
        import integrate
        unrelated = {"hooks": [{"type": "command", "command": "owner-policy"}]}
        hooks = {"UserPromptSubmit": [unrelated]}
        previous = integrate.merge_hooks(hooks, [], integrate.CODEX_MATCHER, "codex")
        integrate.merge_hooks(hooks, previous, integrate.CODEX_MATCHER, "codex")
        prompt = hooks["UserPromptSubmit"]
        self.assertEqual(len(prompt), 2)
        self.assertEqual(prompt[0], unrelated)
        self.assertIn("hook prompt", prompt[1]["hooks"][0]["command"])
        self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        integrate.merge_hooks(hooks, previous, integrate.CODEX_MATCHER, "codex", remove=True)
        self.assertEqual(hooks, {"UserPromptSubmit": [unrelated]})


class TestSkills(unittest.TestCase):
    """Skills and docs must only use commands and flags that exist (no sandbox needed)."""

    def test_memex_commands_in_skills_exist(self):
        usage = subprocess.run([PY, SRC / "tools" / "memex.py", "--help"], capture_output=True, text=True).stdout
        commands = set(re.search(r"\{([a-z,]+)\}", usage).group(1).split(","))
        files = list((SRC / "skills").glob("*/SKILL.md")) + list((SRC / ".claude" / "skills").glob("*/SKILL.md")) + \
            [SRC / "tools" / "memex.SKILL.md", SRC / "schema" / "AGENTS.md.tmpl", SRC / "README.md"]
        for f in files:
            for cmd in re.findall(r"`memex ([a-z]+)", f.read_text()):
                self.assertIn(cmd, commands, f"{f.relative_to(SRC)} uses unknown `memex {cmd}`")

    def test_setup_skill_matches_setup_sh(self):
        skill = SRC / ".claude" / "skills" / "memex-setup" / "SKILL.md"
        self.assertTrue(skill.exists())
        self.assertTrue(skill.read_text().startswith("---\nname: memex-setup\n"))
        self.assertEqual(os.readlink(SRC / ".agents" / "skills"), "../.claude/skills")
        setup = (SRC / "setup.sh").read_text()
        used = set()
        for doc in (skill, SRC / "README.md", SRC / ".claude" / "skills" / "memex-uninstall" / "SKILL.md"):
            for m in re.finditer(r"bash setup\.sh([^`#\n)]*)", doc.read_text()):
                used |= set(re.findall(r"(--[a-z-]+)", m.group(1).split("--uninstall")[0]))
        self.assertIn("--vault", used)
        for flag in used:
            self.assertIn(f"{flag})", setup, f"docs use unknown setup.sh flag {flag}")


class TestIntegration(Sandbox):
    def test_agent_wiring(self):
        shim = (self.home / ".local/bin/memex").read_text()
        self.assertIn(str(self.fw / "tools" / "memex.py"), shim)
        self.assertIn(str(self.V), (self.home / ".claude/skills/memex/SKILL.md").read_text())
        self.assertIn("memex:begin", (self.home / ".claude/CLAUDE.md").read_text())
        allow = json.loads((self.home / ".claude/settings.json").read_text())["permissions"]["allow"]
        self.assertIn(f"Read(/{self.V}/**)", allow)
        self.assertFalse([e for e in allow if e.startswith("Edit(")], allow)  # other projects write via memex capture
        self.assertNotIn("small direct fix", (self.home / ".claude/CLAUDE.md").read_text().lower())
        codex = (self.home / ".codex/config.toml").read_text()
        self.assertIn(f'[projects."{self.V}"]', codex)
        self.assertIn("memex:begin", (self.home / ".hermes/config.yaml").read_text())
        hooks = json.loads((self.home / ".claude/settings.json").read_text())["hooks"]
        self.assertIn("hook session-start", hooks["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("hook session-end --agent claude", hooks["SessionEnd"][0]["hooks"][0]["command"])
        self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 10)
        codex_hooks = json.loads((self.home / ".codex/hooks.json").read_text())["hooks"]
        self.assertIn("hook session-end --agent codex", codex_hooks["SessionEnd"][0]["hooks"][0]["command"])
        self.assertIn("hook prompt", codex_hooks["UserPromptSubmit"][0]["hooks"][0]["command"])
        self.assertEqual(codex_hooks["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        self.assertEqual(codex_hooks["SessionStart"][0]["hooks"][0]["timeout"], 10)
        self.assertEqual(codex_hooks["PreToolUse"][0]["hooks"][0]["timeout"], 15)
        # Refresh an existing installation with the old SessionEnd timeout.
        codex_hooks["SessionEnd"][0]["hooks"][0]["timeout"] = 10
        (self.home / ".codex/hooks.json").write_text(json.dumps({"hooks": codex_hooks}))
        # re-running setup doesn't duplicate hooks
        r = run(["bash", self.fw / "setup.sh", "--vault", self.V, "--agents", "all"], self.tmp, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        hooks = json.loads((self.home / ".claude/settings.json").read_text())["hooks"]
        self.assertEqual([len(hooks[e]) for e in ("PreToolUse", "SessionStart", "SessionEnd")], [1, 1, 1])
        self.assertEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 10)
        codex_hooks = json.loads((self.home / ".codex/hooks.json").read_text())["hooks"]
        self.assertEqual([len(codex_hooks[e]) for e in ("PreToolUse", "SessionStart", "SessionEnd", "UserPromptSubmit")], [1, 1, 1, 1])
        self.assertEqual(codex_hooks["SessionEnd"][0]["hooks"][0]["timeout"], 3)

    def test_codex_session_end_within_timeout(self):
        payload = {"session_id": "codex-timeout-test", "cwd": str(self.fw),
                   "hook_event_name": "SessionEnd", "reason": "other"}
        r = subprocess.run([PY, self.fw / "tools/memex.py", "hook", "session-end", "--agent", "codex"],
                           cwd=self.fw, env=self.env, input=json.dumps(payload), capture_output=True, text=True,
                           timeout=3)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")
        ledger = [json.loads(line) for line in (self.V / ".memex/sessions.jsonl").read_text().splitlines()]
        recorded = [rec for rec in ledger if rec.get("session_id") == payload["session_id"]]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["agent"], "codex")

    def test_z_remove(self):
        r = run(["bash", self.fw / "setup.sh", "--remove-agents"], self.tmp, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.home / ".local/bin/memex").exists())
        settings = json.loads(text(self.home / ".claude/settings.json") or "{}")
        self.assertNotIn("hooks", settings)
        self.assertNotIn("memex:begin", text(self.home / ".claude/CLAUDE.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
