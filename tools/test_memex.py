#!/usr/bin/env python3
"""Self-contained tests for the Memex framework (stdlib only; never touches your vault, HOME or git config).

  python3 tools/test_memex.py        # or: python3 -m unittest tools/test_memex.py -v

Each test class copies the framework (tracked and untracked-but-not-ignored files only, so a vault inside
this folder is never copied) into a temporary folder, makes it a git repo, and runs setup.sh with a fake
HOME and a fake global git config that holds a "real" identity plus an includeIf. It then checks the
vault's layout, its local-only git safety, the guard (Claude Code, Codex and Hermes payloads), the memex
CLI, sync, domains, vault locations, and agent integration.
"""
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
               if not k.startswith(("GIT_", "MEMEX_", "CLAUDE_")) and k not in ("HERMES_WRITE_SAFE_ROOT",)}
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
        json.loads((self.V / ".codex/hooks.json").read_text())

    def test_domain_folders_and_indexes(self):
        for d in ("engineering/decisions", "learning/topics", "personal/goals"):
            self.assertTrue((self.V / "wiki" / d).is_dir(), d)
        self.assertTrue((self.V / "wiki/learning/Learning Index.md").exists())
        self.assertIn("[[Idempotency Keys]]", (self.V / "wiki/learning/Learning Index.md").read_text())


class TestGitSafety(Sandbox):
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
        codex_hooks = json.loads((self.home / ".codex/hooks.json").read_text())["hooks"]
        self.assertIn("hook session-end --agent codex", codex_hooks["SessionEnd"][0]["hooks"][0]["command"])
        # re-running setup doesn't duplicate hooks
        run(["bash", self.fw / "setup.sh", "--vault", self.V, "--agents", "all"], self.tmp, self.env)
        hooks = json.loads((self.home / ".claude/settings.json").read_text())["hooks"]
        self.assertEqual([len(hooks[e]) for e in ("PreToolUse", "SessionStart", "SessionEnd")], [1, 1, 1])

    def test_z_remove(self):
        r = run(["bash", self.fw / "setup.sh", "--remove-agents"], self.tmp, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.home / ".local/bin/memex").exists())
        settings = json.loads(text(self.home / ".claude/settings.json") or "{}")
        self.assertNotIn("hooks", settings)
        self.assertNotIn("memex:begin", text(self.home / ".claude/CLAUDE.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
