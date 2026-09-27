---
type: doc
---
# Changelog

Notable changes to the Memex framework. The format follows [Keep a Changelog](https://keepachangelog.com/). Dates are ISO.

## Unreleased
### Added
- MIT license.

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
