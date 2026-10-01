# Memex framework — developer guide

This repo is the **Memex framework**: tools, hooks, schema, skills and templates for a personal LLM Wiki. It holds **no knowledge**. The owner's knowledge lives in a separate **vault** folder with its own local-first git repo (no remote unless the owner attaches one private remote), created by `setup.sh`. `AGENTS.md` links here, so Claude Code, Codex and Hermes all read this file when working on the framework.

- **Asked to set up or install Memex?** Follow `.claude/skills/memex-setup/SKILL.md` (`/memex-setup` in Claude Code, `$memex-setup` in Codex; other agents read the file). It does the whole install and verification without manual steps.
- **Asked to remove Memex, or delete or move the vault?** Follow `.claude/skills/memex-uninstall/SKILL.md` (`/memex-uninstall`), or point the owner at `memex move <path>`. These are the owner's commands: the guard blocks agents from them, so prepare, then hand over the command.
- **Asked to ingest, answer from or maintain the wiki?** You're in the wrong folder: open an agent in the vault (`memex path` prints it) and follow its `AGENTS.md`.

## Layout
| Path | What | Ends up in a vault as |
|---|---|---|
| `schema/AGENTS.md.tmpl` | the wiki schema, with `{{…}}` slots | `AGENTS.md` (+ `CLAUDE.md` link), rendered |
| `schema/rules/`, `schema/domains/*.md` | path rules; one rule body per domain | `.claude/rules/` |
| `schema/domains.json` | domain presets and the default set | domain folders, indexes, the schema's Domains section |
| `skills/`, `agents/` | skills and subagents | `.claude/skills` (+ `.agents/skills`), `.claude/agents` |
| `config/*.tmpl` | Claude Code settings, Codex hooks and rules | `.claude/settings.json`, `.codex/` |
| `templates/` · `docs/` | page templates · Manual, Design Rationale, Architecture, Changelog | `meta/templates/`, `Memex Manual.md`, `meta/docs/` |
| `seed/` | copied **once** when a vault is created (Home, hot, log, `.obsidian`, bases, `.memex/local.md`) | vault-owned files |
| `hooks/guard.py`, `tools/` | code; runs in place from this folder | never copied |
| `.claude/skills/memex-setup/`, `memex-uninstall/` (`.agents/skills` links to them) | this repo's own skills: install Memex on a machine, or remove it cleanly | never copied |

Everything in the right-hand column except `seed/` is **managed**: `memex sync` re-renders it, each vault session re-syncs when the framework changed, and the guard blocks edits in the vault. Read `docs/Architecture.md` before changing anything.

## Rules
- **No knowledge here.** Never create `wiki/ raw/ inbox/ journal/ notes/ outputs/ .memex/` or a vault in this repo. `.gitignore`, the pre-commit hook (`tools/precommit.py`) and CI refuse them. Test fixtures are made up.
- **Standard-library Python 3.9+** on macOS and Linux; no dependencies.
- **One skill set for three agents.** Skills must work in Claude Code, Codex and Hermes; call tools through the `memex` CLI (`memex ctx`, `memex commit`, `memex lint`), never through file paths.
- **Safety first.** Don't weaken the guard, the vault's pre-commit and pre-push hooks, or the local-first rule without the owner's say-so. The vault pushes only to the one private remote the owner attached, only through `memex push`, and attaching or changing the remote, moving and uninstalling stay owner-only. New guard rules need allow *and* deny tests.
- **Keep the schema short** (under ~200 lines rendered). Put detail in rules and skills.
- **Docs follow the code**: README, `docs/`, and a Changelog entry under *Unreleased*.

## Check your change
```bash
python3 tools/test_memex.py   # temporary framework copy + vault, fake HOME and git config
bash -n setup.sh tools/setup-qmd.sh
memex sync                     # render your change into your own vault (a new vault session also does this)
```
