---
type: doc
---
# Architecture

This guide is for anyone changing the **framework**: its tools, hooks, skills and schema. It's written for people and for coding agents. Agents *maintaining a wiki* follow the vault's `AGENTS.md` instead. Why each rule exists is in [[Design Rationale]].

## Two repos: framework and vault

```
Memex/                      FRAMEWORK: code only. Clone it on every machine; push it wherever you like.
  schema/ skills/ agents/ config/ templates/ docs/ seed/ hooks/ tools/ setup.sh
<anywhere>/MemexVault/      VAULT: the owner's knowledge. Its own git repo: no remote, pushes refused,
                            every commit as "Memex Agent <memex-agent@localhost>". One per machine.
  inbox/ raw/ wiki/ journal/ notes/ outputs/ Home.md meta/bases/ meta/lint/ .obsidian/   vault-owned (committed)
  .memex/vault.json · .memex/local.md · .memex/domains.json · .memex/domains/*.md          vault config (committed)
  AGENTS.md CLAUDE.md .claude/ .codex/ .agents/ meta/templates/ meta/docs/ Memex Manual.md  managed (rendered, not committed)
```

- **Why split.** Framework improvements (a new domain, a relaxed rule, a better skill) reach every vault by pulling the framework, and knowledge can never end up in a repo that has a remote.
- **One vault per machine.** `~/.config/memex/config.json` records `{"vault", "framework"}`. Every tool resolves the vault from `$MEMEX_VAULT` first, then that file (`memexlib.vault()`).
- **The vault may sit inside the framework folder** (setup's default when run from there). The framework's `.gitignore` covers `/MemexVault/`, and `memex init` adds any other in-framework path to `.git/info/exclude`. `memexlib` refuses a vault that the framework doesn't ignore. The guard refuses to delete or move a folder that holds the vault, and blocks `git clean -ff/-x/-X` and `git stash --all` there.

## Components

| Piece | File(s) | Role | Invoked by |
|---|---|---|---|
| Schema | `schema/AGENTS.md.tmpl` | ownership table, page conventions, integrity rules, procedures; `{{VAULT_NAME}} {{VAULT}} {{FRAMEWORK}} {{DOMAINS}} {{LOCAL_RULES}}` … | rendered to the vault's `AGENTS.md` by `memex sync` |
| Domains | `schema/domains.json`, `schema/domains/<name>.md`; vault: `.memex/domains.json`, `.memex/domains/<name>.md` | folders, index title, blurb, graph color and path rule per domain; `vault.json` picks the set | `memexlib.domain_specs()` → index, lint, capture, schema, rules |
| Path rules | `schema/rules/*.md` + one per domain | per-folder rules (`paths:` frontmatter is generated) | Claude Code loads `.claude/rules/` by path; other agents are told to read them |
| Skills, subagents | `skills/<name>/SKILL.md`, `agents/*.md` | ingest, inbox, ask, save, lint, today, close, weekly, prep; source-reader, fact-checker | `/name` (Claude Code, Hermes), `$name` (Codex) |
| Agent config | `config/*.tmpl` | vault permissions, SessionStart and guard hooks for Claude Code and Codex | rendered into `.claude/settings.json`, `.codex/` |
| Render & sync | `tools/vault.py` | `outputs()` is the render map; `sync()` writes it, backs up hand edits, updates `.git/info/exclude` and `.memex/state.json` | `memex sync`, `memex context` (session start), setup |
| Git safety | `tools/vault.py` (`harden_git`, `commit`), `tools/precommit.py` | local identity, no signing, local hooksPath, pre-commit and pre-push hooks; commits with the identity in the env | `memex init`, setup, `memex commit`, `git commit` |
| Guard | `hooks/guard.py` | blocks tool calls that break the ownership table, touch managed or owner-only files, or endanger the vault's folder | every agent's pre-tool hook (vault and global config) |
| CLI | `tools/memex.py` | `path context search read capture rm commit ctx index lint init sync doctor hook` | agents in any project, skills, hooks, the shell |
| Index, lint, context | `tools/build_index.py`, `tools/lint.py`, `tools/context.py` | generated indexes; deterministic checks; live context for skills | `memex index`, `memex lint`, `memex ctx`, `memex commit` |
| Secrets | `tools/secretscan.py` | the patterns shared by lint, capture and pre-commit | — |
| Setup | `setup.sh` → `tools/vault.py setup` → `tools/integrate.py` | per machine: pick or create the vault, harden it, record the config, framework pre-commit, agent wiring | the owner, once per machine (safe to re-run) |
| Global skill | `tools/memex.SKILL.md` | template for the `memex` skill installed for each agent | `integrate.py` |
| Tests | `tools/test_memex.py` | framework copy + vault in a temp folder, fake HOME and git config | contributors, CI |

## Three kinds of vault files

- **Managed** files come from the framework on every sync. They are listed in `.memex/state.json`, excluded through a marked block in `.git/info/exclude`, and the guard refuses edits to them.
  - If someone edits one by hand, sync backs it up to `.memex/backup/<time>/` and overwrites it. Refusing would stall sync, because Obsidian rewrites links in files when pages are renamed.
  - Files the framework no longer renders are removed.
- **Vault config** lives in `.memex/` and is committed.
  - `vault.json` holds the name, `domains` (`"default"` follows the framework's default set) and `identity`. Agents may not write it.
  - `local.md` holds vault-only rules, rendered at the end of `AGENTS.md` with precedence over the framework's rules.
  - `domains.json` and `domains/*.md` define vault-only domains (`"extends": "engineering"` reuses a preset and its rule).
- **Vault-owned** files are the knowledge plus `Home.md`, `.obsidian/` and `meta/bases/`. They are seeded once from `seed/` and never touched by sync.

**Propagation.** `memex context` runs at every session start (Claude Code and Codex SessionStart; Hermes `pre_llm_call`). It hashes the render inputs, re-syncs if they changed, and tells the agent to re-read `AGENTS.md` when it did. Sync renders the framework's working tree as-is, so you can test a framework change by opening a vault session.

## One write operation, end to end

```mermaid
sequenceDiagram
  participant O as Owner
  participant A as Agent (in the vault)
  participant G as hooks/guard.py
  participant M as memex (tools/)
  participant Git as vault git + precommit.py
  O->>A: /ingest inbox/…md
  A->>M: !`memex ctx ingest` (skill), memex context (session start: sync if stale)
  A->>G: each Edit / Write / apply_patch / shell call
  G-->>A: allow, or exit 2 with the reason
  A->>A: append wiki/log.md entry
  A->>M: memex commit "ingest: <Title>"
  M->>M: own repo? no remote? → build_index, lint --quick
  M->>Git: git add -A && git commit (identity in env, Memex-Framework trailer)
  Git-->>M: refused if secrets, raw/ changes or another identity
```

## Enforcement layers

Each layer catches what the one above can miss. Prose alone is advice.

1. **Schema and skills**: what agents should do.
2. **Permissions**: what runs without a prompt. Claude Code allow/ask/deny lists (the vault's settings deny `git push` and `git remote add`), and Codex `.rules` files; Hermes only prompts for dangerous shell commands.
3. **Guard hook**: deterministic, checked before each tool call, the same for all three agents. Shell checks are best-effort heuristics.
4. **Vault git hooks**:
   - `pre-commit` refuses secrets, `raw/` changes and any author or committer other than the vault identity;
   - `pre-push` refuses everything.
   - Both are stubs in `.git/hooks` that run the framework's code. A local `core.hooksPath` stops a global one from bypassing them, and they fail closed if the framework moved.
5. **`memex commit`**: sets the identity through `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, which beat every config (includeIf, `author.*`). It refuses to run with a remote, or when the vault isn't its own repo root.
6. **Audit**: `memex doctor` (and the session context) lists any commit by another identity, which catches `--no-verify` and cherry-picks.
7. **Framework side**: `.gitignore`, the framework's own pre-commit hook (knowledge folders, `vault.json`, embedded repos, secrets) and a CI check.
8. **Lint** catches content problems after the fact; **git history** makes every operation revertible.

## Agent integration

| | Claude Code | Codex | Hermes |
|---|---|---|---|
| Schema | `CLAUDE.md` → `AGENTS.md` | `AGENTS.md` | `AGENTS.md` |
| Vault skills | `.claude/skills` | `.agents/skills` | `.agents/skills` (after `hermes skills trust`) |
| Vault hooks | `.claude/settings.json` | `.codex/hooks.json` (trusted project) | none per project; global `~/.hermes/config.yaml` |
| Guard payload | `tool_name` Edit/Write/MultiEdit/NotebookEdit/Bash | `apply_patch` (patch text in `tool_input.command`), `Bash` (string or argv) | `write_file`, `patch` (replace or V4A patch), `terminal` |
| Block signal | exit 2 + stderr | exit 2 + stderr | exit 2 + stderr |
| No-prompt access from other projects | allow rules in `~/.claude/settings.json` | `~/.codex/rules/memex.rules` + writable root in `~/.codex/config.toml` | file tools aren't gated; hooks are approved once |

The vault's guard hook command is byte-identical to the global one (`memexlib.guard_command()`), so Claude Code runs it once. Hook commands use absolute framework paths. Permission rules use the `memex` shim, because paths with spaces don't match reliably in Bash rules.

## Invariants: keep these true

- **The framework holds no knowledge**, and **a vault never has a remote** and only has commits by its own identity.
- **Standard library only** Python, 3.9 or newer, on macOS and Linux.
- **Tools write only inside the vault**, except `integrate.py` (agent config), setup (`~/.config/memex/`, the framework's `.git/hooks` and `.git/info/exclude`). **Nothing ever pushes.**
- **Rendering is deterministic** (same inputs, same bytes), and so are the generated indexes.
- **The guard fails open with no vault and outside the vault, and closed inside it**: an internal error blocks only calls that mention the vault. A new rule must never slow down or block unrelated work.
- **The rendered `AGENTS.md` stays under ~200 lines.** Put detail in path rules and skills.
- **One skill set.** A skill must make sense to all three agents: no Claude-only tool names in its steps. Skills reach tools only through `memex …`.
- **Every skill ends in `memex commit`**, never raw `git commit`.
- **`integrate.py` merges; it never overwrites.** It records what it added in `~/.config/memex/integration.json`, so re-runs don't duplicate and `--remove` is exact.

## Common changes

- **Add or change a domain**: edit `schema/domains.json` (folders, title, blurb, color; add it to `default` to give it to every vault that follows the defaults), and add or edit `schema/domains/<name>.md`. Nothing else is hardcoded. A vault-only domain goes in the vault's `.memex/domains.json` instead.
- **Change a rule for every vault**: edit `schema/AGENTS.md.tmpl`, `schema/rules/` or a skill. For one vault only, edit that vault's `.memex/local.md`.
- **Add a guard rule**: edit `hooks/guard.py` (`check_write`, `check_edit`, `check_delete`, `check_move`, `check_owned`, `check_git` or `check_shell`). Add allow *and* deny cases for each agent's payload format to `tools/test_memex.py`.
- **Add a skill**: create `skills/<name>/SKILL.md` with `name` and `description` frontmatter. If it needs live data, add a branch to `tools/context.py` and call it as `` !`memex ctx <name>` `` (with `allowed-tools: Bash(memex *)`). Finish with a log entry and `memex commit`. List it in the schema's *Operations* and the Manual's command table.
- **Add a managed file**: add it to `outputs()` in `tools/vault.py` (and to `MANAGED_DIRS` if it's a folder).
- **Support another agent**:
  1. teach `guard.py`'s `operations()` its tool payloads and add tests;
  2. add a function to `integrate.py` that installs the skill, its instructions and hooks, and records them in the state file;
  3. document it in the README and Manual tables.
- **Change page types**: update `TYPE_FOLDERS` in `tools/memexlib.py`, add a template in `templates/wiki/`, and update the schema's type ↔ folder list.

## Testing

```bash
python3 tools/test_memex.py   # temp framework copy + vault; fake HOME and global git config; never touches yours
bash -n setup.sh tools/setup-qmd.sh
```
CI (`.github/workflows/ci.yml`) runs these on macOS and Linux, plus a check that no knowledge is tracked.
