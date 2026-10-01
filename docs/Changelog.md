---
type: doc
---
# Changelog

Notable changes to the Memex framework. The format follows [Keep a Changelog](https://keepachangelog.com/). Dates are ISO.

## Unreleased: 0.3.0, framework and vault split
### Changed (breaking layout)
- **The framework no longer is the vault.** This repo holds code only (`schema/ skills/ agents/ config/ hooks/ tools/ templates/ seed/ docs/`). `setup.sh` (now at the root) creates the vault in a separate folder, asking where. The default is `MemexVault` in the folder you run it from; a vault inside the framework folder is git-ignored there.
- **Managed files are rendered, not copied by hand.** `memex sync` renders the schema (`AGENTS.md` + `CLAUDE.md`), skills, subagents, path rules, Claude Code and Codex settings, templates and docs into the vault. They're excluded from the vault's git, the guard blocks edits to them, and each session re-syncs when the framework changed. Hand edits are backed up to `.memex/backup/`.
- **Per-vault layer**: `.memex/vault.json` (name, domains, commit identity), `.memex/local.md` (vault-only rules, rendered with precedence), `.memex/domains.json` (vault-only domains, e.g. `"extends": "engineering"`).
- **Domains are data**: `schema/domains.json` defines the presets and the default set. Nothing about domain names is hardcoded any more.
- Skills call `memex ctx`, `memex lint` and `memex commit` instead of tool paths.

### Added
- **Vault git (local-only by default)**: repo-local `Memex Agent <memex-agent@localhost>` identity (also `author.*`/`committer.*`), no commit signing, local `core.hooksPath`, a pre-commit hook that also refuses other identities, and a pre-push hook that refuses everything. `memex commit` forces the identity through the environment (beating global and `includeIf` config), refuses to run with a remote or without its own repo, and adds a `Memex-Framework: <version> (<sha>)` trailer.
- The framework's own pre-commit hook and a CI check refuse knowledge folders, vault config and embedded repos.
- `memex init`, `memex sync [--check]`, `memex doctor [--fix]`, `memex ctx`, `memex index`, `memex lint`.
- **`memex-setup` skill** (`.claude/skills/memex-setup`, also `.agents/skills` for Codex): an agent opened in a fresh clone installs Memex end to end. It checks prerequisites, picks the vault folder, runs setup without prompts, puts `memex` on PATH, verifies with doctor, lint and tests, and reports the Obsidian clicks left.
- README rewritten: diagrams (overview, setup flow, rule propagation, guardrails), both setup paths, and a worked first session with real output.
- Setup always installs the `memex` CLI, even when no agent is found.
- Tests check that skills and docs only use real `memex` subcommands and `setup.sh` flags.
- Guard: protects managed files and `.memex/vault.json`, refuses deleting or moving a folder that holds the vault, and blocks `git clean -ff/-x/-X` and `git stash --all` there.
- MIT license.

### Added: automatic memory (Phase 1)
- **Repo-aware recall:** a global SessionStart hook (`memex hook session-start`, Claude Code and Codex; Hermes on its first turn) prints what the wiki knows about the current repo: pages whose `repo:` matches, their open loops and linked decisions. It prints nothing when nothing matches.
- **Session ledger + `/harvest`:** a global SessionEnd hook records each session outside the vault in `.memex/sessions.jsonl`. `memex harvest` turns Claude Code and Codex transcripts (including Codex's JavaScript tool mode) into verbatim, redacted digests; the `/harvest` skill files them and compiles durable knowledge; `/close` runs it first. The `harvest` settings are `enabled`, `exclude` and `min_tool_calls`.
- **Better retrieval:** BM25-ranked `memex search` (with `--json`), `memex read "Page#Heading"`, `memex outline` and `memex related`. Skills use these before the Obsidian CLI.
- **`memex file`:** files an inbox item into `raw/<domain>/` without Obsidian, fixing links if it's renamed.
- **`memex backup`:** verified git bundles, pruning, and reminders in doctor and the session context.
- `repo:` on project pages; `recall`, `harvest` and `backup` settings written into new vaults' `vault.json`.

### Added: sync, move and uninstall
- **Optional private remote:**
  - attaching: `memex remote set <url>` (owner only) attaches one private repository. Public repositories (checked anonymously, again daily) and URLs with credentials are refused. The URL is stored machine-locally in `.memex/remote.json`;
  - syncing: vault sessions pull at start, `memex commit` pushes after every commit, and `memex pull [--merge]`, `memex push` and `memex remote` do it by hand;
  - joining from another machine: `bash setup.sh --vault <folder> --remote <url>` creates a vault that adopts the remote's history;
  - the pre-push hook allows only `memex push` to that URL, with a secret scan of the outgoing commits;
  - conflicts: a conflicting pull is rolled back and reported, `--merge` leaves markers to resolve, and the pre-commit hook refuses leftover markers. The log, `hot.md`, daily notes and indexes union-merge.
- **`memex move <path>`** (owner only): moves the vault and re-points the config, managed files and every agent's permissions, trust and instructions (the old Codex entries are removed).
- **`memex uninstall [--delete-vault --confirm NAME]`** and `bash setup.sh --uninstall` (owner only):
  - first commits, pushes and writes a verified backup bundle;
  - removes every agent block, hook, permission and skill Memex added, plus the CLI, `~/.config/memex`, the framework's hook and exclude lines, and the `# Memex` PATH line (if `~/.local/bin` is otherwise empty);
  - then deletes the vault, or leaves it as plain Markdown + git;
  - finishes with a leftover scan (`python3 tools/vault.py leftovers`). It works even if the vault folder is already gone.
- **`memex-uninstall` skill:** an agent prepares, backs up and verifies, and hands the owner the one command to run.
- **Guard:** agents can't attach or change the remote, move the vault, uninstall, or run raw `git push`/`git remote` changes in the vault. Allow and deny tests cover each case.

### Changed
- **Reading:** read whole pages by default. Search results show each page's length, and outline → section is only for pages over 150 lines.
- **`memex harvest`:** a transcript in a format the parser doesn't recognize is listed as UNPARSED and never skipped as trivial.

### Fixed
- Guard: case-insensitive path matching on macOS (a differently-cased path used to pass), and non-ASCII vault paths in the fail-closed check.
- Secret scans no longer skip `meta/tools/` inside the vault.

## 2026-09-27: Codex and Hermes, autonomous upkeep
### Added
- **Codex and Hermes support** next to Claude Code. The vault's skills are shared through `.agents/skills`, and Codex gets hooks and command rules in `.codex/`. The schema explains `$skill` syntax and how to handle Claude-only features.
- **`memex` CLI** (`meta/tools/memex.py`): `search`, `read`, `context`, `capture`, `rm`, `commit`, plus `hook hermes`. It works the same in every agent and without Obsidian.
- **`integrate.py`**, run by `setup.sh`: for each agent it installs the `memex` command, a global `memex` skill and instructions, permissions to read and write Memex from any project without prompts, and the guard hook. It merges into existing configs, re-runs cleanly, and `--remove-agents` undoes it.
- **Test suite** (`meta/tools/test_memex.py`) and CI on macOS and Linux, Python 3.9 and 3.12.
- Docs: README, Architecture guide, contributing and security policies, issue and PR templates.

### Changed
- **Autonomy**: agents act without asking inside the ownership rules and report afterwards. `/inbox` and `/close` no longer wait for an OK, redactions are applied then reported, `/ask` files reusable answers, and deletions go through `memex rm` (link check, copy in `.trash/`).
- **One guard for every agent**: `guard.py` understands Claude Code, Codex (`apply_patch`, argv shells) and Hermes (`write_file`, `patch`, `terminal`) payloads, and now also checks shell commands.
- The pre-commit hook also refuses changes to existing `raw/` files.
- Every skill finishes with `memex commit` (index, lint, commit) instead of separate steps.
- The session-start context moved from bash to Python, so it works on Linux too.
- The capture skill `memex-capture` was replaced by the broader `memex` skill.

## 2026-09-26: initial framework
- Schema, path rules, 9 skills, 2 subagents, guard and session hooks, page templates, Obsidian dashboards and Web Clipper template, index builder, lint, pre-commit secret scan, setup script, Manual and Design Rationale.
