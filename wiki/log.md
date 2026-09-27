---
type: log
---
# Memex log
_Append-only. Each entry: `## [YYYY-MM-DD] op | title` + bullets. Last 5: `grep "^## \[" wiki/log.md | tail -5`_

## [2026-09-26] setup | Memex framework
- Schema (CLAUDE.md), path rules, 9 skills, 2 subagents, guard + session hooks, templates, dashboards, index builder, lint, pre-commit secret scan and setup script.
- Domains: engineering, learning, personal. Design rationale: [[Design Rationale]].

## [2026-09-27] schema | Codex and Hermes support, autonomous CRUD
- One guard (`.claude/hooks/guard.py`) for Claude Code, Codex and Hermes edit and shell calls; pre-commit hook now refuses changes to existing raw/ files.
- `meta/tools/memex.py` CLI (search, read, capture, rm, commit, hook) and `integrate.py` (wired in by setup.sh) so agents use Memex from any project without prompts.
- Skills shared via `.agents/skills`; Codex hooks and rules in `.codex/`. Skills no longer wait for an OK; see Autonomy in [[CLAUDE]].
