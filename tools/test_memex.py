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
            self.assertIn("local-only", r.stderr)
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
        for doc in (skill, SRC / "README.md"):
            for m in re.finditer(r"bash setup\.sh([^`#\n)]*)", doc.read_text()):
                used |= set(re.findall(r"(--[a-z-]+)", m.group(1)))
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
        codex = (self.home / ".codex/config.toml").read_text()
        self.assertIn(f'[projects."{self.V}"]', codex)
        self.assertIn("memex:begin", (self.home / ".hermes/config.yaml").read_text())

    def test_z_remove(self):
        r = run(["bash", self.fw / "setup.sh", "--remove-agents"], self.tmp, self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.home / ".local/bin/memex").exists())
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertNotIn("hooks", settings)
        self.assertNotIn("memex:begin", (self.home / ".claude/CLAUDE.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
