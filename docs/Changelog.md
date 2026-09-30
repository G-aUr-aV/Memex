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
- **Local-only vault git**: repo-local `Memex Agent <memex-agent@localhost>` identity (also `author.*`/`committer.*`), no commit signing, local `core.hooksPath`, a pre-commit hook that also refuses other identities, and a pre-push hook that refuses everything. `memex commit` forces the identity through the environment (beating global and `includeIf` config), refuses to run with a remote or without its own repo, and adds a `Memex-Framework: <version> (<sha>)` trailer.
- The framework's own pre-commit hook and a CI check refuse knowledge folders, vault config and embedded repos.
- `memex init`, `memex sync [--check]`, `memex doctor [--fix]`, `memex ctx`, `memex index`, `memex lint`.
- **`memex-setup` skill** (`.claude/skills/memex-setup`, also `.agents/skills` for Codex): an agent opened in a fresh clone installs Memex end to end. It checks prerequisites, picks the vault folder, runs setup without prompts, puts `memex` on PATH, verifies with doctor, lint and tests, and reports the Obsidian clicks left.
- README rewritten: diagrams (overview, setup flow, rule propagation, guardrails), both setup paths, and a worked first session with real output.
- Setup always installs the `memex` CLI, even when no agent is found.
- Tests check that skills and docs only use real `memex` subcommands and `setup.sh` flags.
- Guard: protects managed files and `.memex/vault.json`, refuses deleting or moving a folder that holds the vault, and blocks `git clean -ff/-x/-X` and `git stash --all` there.
- MIT license.

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
