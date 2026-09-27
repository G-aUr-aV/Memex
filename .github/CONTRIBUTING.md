# Contributing to Memex

Thanks for helping improve the framework. Memex is an empty, ready-to-use vault. Contributions change its **tools, hooks, skills, schema and docs**, never anyone's personal content.

## Before you start
- Read [meta/docs/Architecture.md](../meta/docs/Architecture.md) to see how the pieces fit and which invariants to keep, and [meta/docs/Design Rationale.md](../meta/docs/Design%20Rationale.md) to see why each rule exists.
- For anything bigger than a fix, open an issue first so we can agree on the approach.

## Working with a coding agent
The repo's `CLAUDE.md` / `AGENTS.md` tells agents to *maintain a wiki*, and to treat framework files as needing approval. When you use an agent to change the framework, say so up front, for example: *"We're developing the Memex framework itself; follow meta/docs/Architecture.md."*

## Ground rules
- **Standard-library Python only**, 3.9 or newer, on macOS and Linux. Don't add dependencies to the core tools.
- **Keep the three agents in parity.** A change to skills, the guard or setup should work for Claude Code, Codex and Hermes, or say clearly where it can't.
- **Safety first.** Never weaken the guard, the pre-commit hook or the "never push" rule without discussion. New guard rules need allow *and* deny tests.
- **No personal data in the repo**: no real names, employers, customers, tokens or sample content taken from someone's real vault. Test fixtures are made up.
- **Keep `CLAUDE.md` short** (under ~200 lines). Put detail in `.claude/rules/` and skills.
- **Docs follow the code.** Update the README, `Memex Manual.md` or `meta/docs/` in the same PR when behavior changes, and add an entry under *Unreleased* in [meta/docs/Changelog.md](../meta/docs/Changelog.md).

## Checks
```bash
python3 meta/tools/test_memex.py   # guard, CLI, pre-commit, integration (temporary vault, fake HOME)
python3 meta/tools/lint.py          # the vault itself: 0 errors, 0 warnings
bash -n meta/tools/setup.sh meta/tools/setup-qmd.sh .claude/hooks/session_start.sh
```
CI runs these on every push and pull request.

## Commit style
Use short, imperative [Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`, `test: …`. One logical change per commit.
