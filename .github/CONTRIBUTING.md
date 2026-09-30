# Contributing to Memex

Thanks for helping improve the framework. This repo is the Memex framework: it holds no knowledge, and `setup.sh` creates a separate vault. Contributions change its **tools, hooks, skills, schema and docs**, never anyone's personal content.

## Before you start
- Read [docs/Architecture.md](../docs/Architecture.md) to see how the pieces fit and which invariants to keep, and [docs/Design Rationale.md](../docs/Design%20Rationale.md) to see why each rule exists.
- For anything bigger than a fix, open an issue first so we can agree on the approach.

## Working with a coding agent
The repo's `CLAUDE.md` / `AGENTS.md` is a developer guide for the framework. The wiki schema itself is `schema/AGENTS.md.tmpl`, which is rendered into vaults. Test your change against your own vault with `memex sync`.

## Ground rules
- **Standard-library Python only**, 3.9 or newer, on macOS and Linux. Don't add dependencies to the core tools.
- **Keep the three agents in parity.** A change to skills, the guard or setup should work for Claude Code, Codex and Hermes, or say clearly where it can't.
- **Safety first.** Never weaken the guard, the pre-commit hook or the "never push" rule without discussion. New guard rules need allow *and* deny tests.
- **No knowledge or personal data in the repo**: no vault folders, real names, employers, customers, tokens or sample content taken from someone's real vault. Test fixtures are made up. The pre-commit hook and CI refuse knowledge folders.
- **Keep the schema short** (`schema/AGENTS.md.tmpl`, under ~200 lines rendered). Put detail in `schema/rules/`, `schema/domains/` and skills.
- **Docs follow the code.** Update the README or `docs/` in the same PR when behavior changes, and add an entry under *Unreleased* in [docs/Changelog.md](../docs/Changelog.md).

## Checks
```bash
python3 tools/test_memex.py   # layout, git safety, sync, domains, guard, CLI, integration (temporary copies, fake HOME)
bash -n setup.sh tools/setup-qmd.sh
```
CI runs these on every push and pull request.

## Commit style
Use short, imperative [Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`, `test: …`. One logical change per commit.
