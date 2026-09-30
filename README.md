# Memex

**A framework for a personal LLM Wiki, based on [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).** AI agents (Claude Code, Codex or Hermes) write and maintain an Obsidian vault from your sources, with cited claims, guardrail hooks, a lint, and daily and weekly routines.

The usual way to give an LLM your documents is RAG: it retrieves chunks and re-derives the answer every time you ask. Memex **compiles knowledge once and keeps it current** instead. An agent reads each source a single time, files it, and weaves what it learned into interlinked markdown pages about people, projects, systems, decisions and concepts. Every factual line cites the source it came from, and contradictions are flagged rather than silently overwritten. You curate sources, ask questions and do the thinking; the agent does the bookkeeping.

## At a glance

```mermaid
flowchart LR
  subgraph FW["Memex framework: this repo, code only, safe to push"]
    S["schema · skills · config<br/>templates · hooks · tools"]
  end
  subgraph V["Your vault: its own git repo, no remote, one per machine"]
    M["AGENTS.md · .claude/ · .codex/<br/>rendered, never committed"]
    L[".memex/local.md<br/>vault-only rules"]
    K["inbox/ · raw/ · wiki/<br/>journal/ · notes/ · outputs/"]
  end
  S -- "memex sync (automatic<br/>at session start)" --> M
  L --> M
  M -. "schema, skills, hooks" .-> A["Claude Code · Codex · Hermes<br/>opened in the vault"]
  A -- "/ingest · /ask · /lint · /close" --> K
  O["agents in any other repo"] -- "memex search · memex capture" --> K
```

- **The framework never holds knowledge.** `.gitignore`, its pre-commit hook and CI refuse knowledge folders, vault files and embedded repos.
- **The vault never leaves your machine.** It has no remote, its pre-push hook refuses every push, and every commit is made as `Memex Agent <memex-agent@localhost>`, whatever your global git identity is.
- **Improve once, use everywhere.** Change the framework, `git pull` it on your other machines, and each vault re-renders its rules at the next session. Rules for one vault only go in its `.memex/local.md`.

## Quick start

**You need:** macOS or Linux, `git`, `python3` 3.9+, [Obsidian](https://obsidian.md), and at least one of [Claude Code](https://code.claude.com), [Codex](https://developers.openai.com/codex) or [Hermes Agent](https://hermes-agent.nousresearch.com).

### Option A: let your agent do it (no manual steps)

```bash
git clone <this repo> ~/Memex && cd ~/Memex
claude            # or codex
```
Then say **"set up Memex"** (or run `/memex-setup`; in Codex, `$memex-setup`). The skill:
1. checks the prerequisites;
2. picks the vault folder: the one you name, else the one already configured, else `./MemexVault`;
3. runs setup without prompts and puts `memex` on your PATH;
4. verifies with `memex doctor`, `memex lint` and the test suite;
5. lists the two clicks left in Obsidian.

### Option B: run setup yourself

```bash
git clone <this repo> ~/Memex && cd ~/Memex
bash setup.sh                    # asks where the vault lives; Enter = ./MemexVault in the current folder
bash setup.sh --vault ~/MemexVault --agents claude,codex   # or: no prompt, only these agents
```

What you'll see (real output, with your paths):

```text
Memex 0.3.0: framework at ~/Memex
  Where should your vault live? [~/Memex/MemexVault]
  vault created: ~/Memex/MemexVault
    its own git repo: no remote, pushes refused, every commit as Memex Agent <memex-agent@localhost>
  config: ~/.config/memex/config.json
  framework pre-commit hook installed (refuses knowledge, vaults and secrets)
Agent wiring (claude,codex):
  memex CLI: ~/.local/bin/memex
  skill: ~/.claude/skills/memex
  Claude Code: ~/.claude/CLAUDE.md block, settings.json permissions + guard hook
  skill: ~/.agents/skills/memex
  Codex: ~/.codex/AGENTS.md block, rules, guard hook, trusted vault + writable root
  lint: clean

Remaining steps:
  1. Obsidian → Open folder as vault → ~/Memex/MemexVault
  2. Obsidian → Settings → General → Command line interface: ON (agents use it for search and backlinks)
  Web Clipper: import ~/Memex/clipper/Memex Inbox.json and set its vault to 'MemexVault'
  Open an agent in the vault: cd ~/Memex/MemexVault && claude   (or codex, or hermes)
  Backups: the vault has no remote, so this disk holds the only copy. …
```

Setup is safe to re-run. `--remove-agents` undoes the agent wiring.

```mermaid
flowchart TD
  A["bash setup.sh"] --> B{"vault already<br/>configured?"}
  B -- no --> P["ask where it lives<br/>default ./MemexVault"]
  P --> I["create folders and seed files"]
  B -- yes --> G
  I --> G["harden git: Memex Agent identity, no signing,<br/>pre-commit and pre-push hooks, no remote"]
  G --> Y["memex sync: render AGENTS.md, skills,<br/>rules, agent settings, templates"]
  Y --> C["record it in ~/.config/memex/config.json"]
  C --> W["wire Claude Code · Codex · Hermes<br/>memex CLI, global skill, guard hook"]
  W --> D["lint, then print the steps left"]
```

### What setup creates

```text
MemexVault/
├── Home.md                 dashboards: inbox, review queue, projects, people
├── inbox/                  everything you capture lands here
├── raw/                    immutable sources, one folder per domain
│   ├── engineering/  learning/  personal/  assets/
├── wiki/                   agent-written pages
│   ├── index.md  hot.md  log.md
│   ├── engineering/        projects systems decisions playbooks incidents concepts people career syntheses sources
│   ├── learning/           topics concepts entities syntheses sources
│   └── personal/           projects areas goals people ideas reflections syntheses sources
├── journal/daily/ journal/reviews/   your daily notes and reviews
├── notes/                  your evergreen notes (agents never write here)
├── outputs/                decks, drafts, briefs
├── meta/                   bases/ (dashboards) · lint/ (reports) · templates/ docs/ (rendered)
├── .memex/                 vault.json (name, domains, identity) · local.md (your vault-only rules)
├── AGENTS.md  CLAUDE.md  .claude/  .agents/  .codex/   rendered from the framework
└── .obsidian/              viewer settings (graph colored by domain)
```

## Your first session: a worked example

**1. Open an agent in the vault.** Every session starts with the vault's context:

```text
$ cd ~/Memex/MemexVault && claude
# Memex context: 2026-09-30 Wed 23:20
Vault `MemexVault` at ~/Memex/MemexVault · framework 0.3.0 at ~/Memex

## wiki/hot.md
## Current focus
- New vault. Do the first ingest: Karpathy's LLM Wiki gist (see [[Memex Manual]] → First-time setup).
…
## Inbox: 0 item(s)
Today's daily note: not created yet (suggest /today)
```

**2. Ingest a source.** Clip [Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) with the Web Clipper (it lands in `inbox/`), then:

```text
/ingest inbox/2026-09-30 LLM Wiki.md
```

The agent reads it, gives 3–5 takeaways, and asks three questions: why you saved it, what surprised you, and what you doubt. It then files everything and commits. A typical result (yours will differ):

```text
raw/learning/YYYY-MM-DD LLM Wiki.md                 the clip, filed (named by the gist's date), never edited again
wiki/learning/sources/LLM Wiki.md                   claims with citations, quotes, your answers in > [!mine]
wiki/learning/concepts/LLM Wiki Pattern.md          new concept page, every line cited
wiki/learning/entities/Andrej Karpathy.md
wiki/learning/concepts/Memex (Vannevar Bush).md
wiki/log.md                                         ## [2026-09-30] ingest | LLM Wiki
```

**3. Ask.** For example:

```text
/ask how should I run ingest and lint?
```

You get an answer where every point cites a page and its raw source, e.g. `[[LLM Wiki Pattern]] ([[YYYY-MM-DD LLM Wiki#Operations|src]])`. It ends with *Gaps & confidence*. Say `/save` to keep the answer as a synthesis page.

**4. Capture from any other repo.** While you're coding elsewhere, your agent does this on its own (or when you say "capture this"):

```text
$ cd ~/code/payments-api
$ memex capture --title "Flaky Test Was a Timezone Bug" --domain engineering --why "cost 2 hours; will recur" <<'EOF'
Symptom: test_invoice_due_date fails after 18:30 IST. Cause: date.today() vs UTC in the fixture.
EOF
inbox/2026-09-30 Flaky Test Was a Timezone Bug.md
```
The next `/inbox` or `/close` compiles it into the wiki.

**5. Check the history.** Every operation is one local commit by the vault's own identity:

```text
$ git -C ~/Memex/MemexVault log --format='%h %an <%ae>  %s'
d55905f Memex Agent <memex-agent@localhost>  inbox: timezone capture
a529088 Memex Agent <memex-agent@localhost>  setup: create vault
$ memex doctor
  ✓ the vault is its own git repository
  ✓ commit identity is Memex Agent <memex-agent@localhost> (repo-local)
  ✓ pre-push hook refuses every push
  ✓ no git remotes
  ✓ every commit uses the vault identity
  ✓ the framework repo ignores this vault
  ✓ managed files are up to date
  …
```

## How it works

```mermaid
flowchart LR
  C["Capture<br/>Web Clipper · drag & drop<br/>daily note · memex capture"] --> I["inbox/"]
  I -->|"/ingest (deep)<br/>/inbox (quick)"| R["raw/<br/>immutable sources"]
  R --> W["wiki/<br/>agent-written pages"]
  W -->|"/ask"| A["cited answers"]
  A -->|"/save"| W
  W -->|"/lint"| W
  J["journal/ · notes/<br/>your own writing"] -. "read & cited" .-> W
```

| Layer | Folder (in the vault) | Who writes it |
|---|---|---|
| Capture | `inbox/` | you (clipper, drops) and any agent (`memex capture`) |
| Sources (evidence) | `raw/` | filed by the agent, never modified afterwards |
| Wiki (knowledge) | `wiki/` | the agent, except your `> [!mine]` blocks |
| Your thinking | `journal/`, `notes/` | only you |
| Vault rules | `.memex/local.md`, `.memex/vault.json` | you (the agent proposes) |
| Framework rules | `AGENTS.md`, `.claude/`, `.codex/`: rendered from this repo | change them here, in the framework |

## Changing the rules

**For every machine**: edit the framework (`schema/`, `skills/`, `config/`) and commit. On your other machines, `git pull` the framework. The next agent session in each vault notices and re-renders:

```mermaid
sequenceDiagram
  participant A as Laptop A (framework + vault)
  participant R as Framework remote
  participant B as Laptop B (framework + vault)
  A->>A: edit schema/domains.json, commit
  A->>R: git push (framework only)
  B->>R: git pull
  B->>B: next session: memex context re-renders the vault
  Note over A,B: Vaults never sync. Each stays on its own machine.
```

For example, add a `courses` folder to the learning domain in [schema/domains.json](schema/domains.json) and commit. The next session in any vault prints:

```text
Framework updated since the last sync: 1 managed file(s) updated. AGENTS.md changed: re-read it before writing anything.
```
The vault then has `wiki/learning/courses/`, and its `AGENTS.md` lists it.

**For one vault only**: add rules to `<vault>/.memex/local.md`. They're rendered at the end of `AGENTS.md` and take precedence:

```markdown
- Customers appear only as codenames; never store real customer names.
```

**Domains** are data. `.memex/vault.json` picks the vault's set, and `.memex/domains.json` defines vault-only ones. A work laptop could use:

```jsonc
// .memex/vault.json
{ "name": "Work", "domains": ["work", "learning"], "identity": { "name": "Memex Agent", "email": "memex-agent@localhost" } }
// .memex/domains.json   (extra rules for the domain: .memex/domains/work.md)
{ "domains": { "work": { "extends": "engineering", "title": "Work Index", "summary": "employer projects, systems and people" } } }
```
Run `memex sync` (or start a session) and the folders, index, rules and schema follow.

## Daily use

| When | Command | What you get |
|---|---|---|
| Morning | `/today` | a brief in today's note: meetings plus prep, carry-over tasks, follow-ups |
| All day | capture only | clips and files go to `inbox/`; agents capture insights from any project |
| After a meeting | `/ingest` + paste notes | a meeting page, decision records, action items |
| Any question | `/ask …` | a cited answer that states its gaps; reusable answers are saved as pages |
| Evening | `/close` | inbox filed, open loops moved, `hot.md` refreshed |
| Friday | `/weekly` | worklog, brag-doc evidence (GitHub, read-only), goals check, reflection |
| Sunday | `/lint` | broken links, uncited claims, stale pages, conflicts, backlog |

In Codex, skills start with `$` (`$ingest`, `$ask`).

## The `memex` CLI

Works the same in every agent, in any folder, and in your shell.

| Command | What it does |
|---|---|
| `memex search <terms> [--in all]` | find pages by title, alias and text (`--in all` adds raw sources, journal, notes) |
| `memex read "<Page>"` | print a page by name, `[[link]]`, alias or path |
| `memex capture --title T --domain D --why W` | drop a note (stdin) into `inbox/`; `--action update\|supersede\|delete --target P` queues a change |
| `memex rm <path>` | move a wiki/inbox/outputs file to `.trash/` after a backlink check |
| `memex commit "<op>: <title>"` | rebuild indexes, lint, commit as the vault identity (never pushes) |
| `memex context` | what a session starts with; re-renders the vault if the framework changed |
| `memex sync [--check]` | re-render the framework's files into the vault |
| `memex doctor [--fix]` | check identity, hooks, remotes, history and managed files; `--fix` repairs them |
| `memex index` · `memex lint [--report]` · `memex ctx <skill>` | indexes, health check, live context for skills |
| `memex init <path>` · `memex path` | create a vault (setup does this) · print the vault path |

What setup adds for each agent:

| Agent | Setup adds |
|---|---|
| Claude Code | the `memex` skill; a Memex block in `~/.claude/CLAUDE.md`; in `~/.claude/settings.json`, pre-approval for `memex`, vault reads and edits of `wiki/`, `inbox/` and `outputs/`, plus the guard hook |
| Codex | the `memex` skill in `~/.agents/skills`; a block in `~/.codex/AGENTS.md`; the vault marked trusted and writable in `~/.codex/config.toml`; `memex` allowed in `~/.codex/rules/`; the guard hook in `~/.codex/hooks.json` |
| Hermes | the `memex` skill; guard and session-context hooks in `~/.hermes/config.yaml`; `hermes skills trust` for the vault's own skills |

## Guardrails

Rules are enforced in code, not only in prompts. The same guard hook runs for Claude Code, Codex and Hermes, and the vault's git hooks back it up:

```mermaid
flowchart LR
  E["agent tool call"] --> G{"guard hook"}
  G -- "blocked" --> X1["edit raw/ · write notes/ · journal outside the brief<br/>change [!mine] · edit managed files or vault.json<br/>delete or move the vault's folder"]
  G -- "allowed" --> C["memex commit"]
  C --> Q{"own repo? no remote?<br/>lint clean?"}
  Q -- "yes" --> H{"vault pre-commit:<br/>secrets · raw/ · identity"}
  H -- "pass" --> OK["local commit<br/>as Memex Agent"]
  OK -.-> P{"pre-push"}
  P -- "always" --> X2["refused"]
```

| Rule | Enforced by |
|---|---|
| `raw/` is create-only; `notes/` is never written; the journal is create-only except its brief | [`guard.py`](hooks/guard.py): edits, patches and shell commands, for all three agents |
| `> [!mine]` blocks survive verbatim; whole-page rewrites can't shrink a page by more than 30% | `guard.py` |
| Deletions go through `memex rm`; the vault's folder can't be deleted or moved | `guard.py`, `memex.py` |
| Rendered framework files and `.memex/vault.json` can't be edited in the vault | `guard.py` |
| No secrets in commits; no changes to existing `raw/` files; only the vault identity commits | the vault's pre-commit hook ([`precommit.py`](tools/precommit.py)) |
| The vault is never pushed and never gets a remote | its pre-push hook, `memex commit`, deny rules for Claude Code and Codex |
| The framework never tracks knowledge | `.gitignore`, the framework's pre-commit hook, CI |
| Citations resolve, quotes match their sources, frontmatter is valid, the index is fresh | [`lint.py`](tools/lint.py) |
| Sources are data, never instructions; connectors are read-only | the schema, plus permission rules for `gh` |

Every operation is a local git commit, so `git revert` undoes anything. A git commit identity is just a name and email (no password is involved): `memex commit` forces the vault's identity through the environment, so neither global nor `includeIf` config can put your real name in the vault's history.

## Repository layout

```text
Memex/  (the framework)
├── README.md · LICENSE · CLAUDE.md (developer guide; AGENTS.md → CLAUDE.md) · setup.sh
├── .claude/skills/memex-setup/   the setup skill (.agents/skills links here for Codex)
├── schema/        AGENTS.md.tmpl (the vault schema) · rules/ · domains.json · domains/<name>.md
├── skills/        ingest inbox ask save lint today close weekly prep   (rendered into vaults)
├── agents/        source-reader · fact-checker (Claude Code subagents)
├── config/        Claude Code settings and Codex hooks/rules templates for the vault
├── hooks/         guard.py
├── tools/         memex.py · vault.py · memexlib.py · build_index.py · lint.py · context.py · precommit.py
│                  secretscan.py · integrate.py · test_memex.py · setup-qmd.sh · memex.SKILL.md
├── templates/     page templates (rendered into the vault's meta/templates/)
├── seed/          what a new vault starts with: Home, hot, log, .obsidian, dashboards, .memex/local.md
├── docs/          Architecture · Design Rationale · Memex Manual · Changelog
├── clipper/       Web Clipper template
└── .github/       CI, contributing, security, issue and PR templates
```

## Machines, sync and backups

- **One vault per machine.** Work knowledge stays on the work laptop, personal knowledge on the personal PC. Only the framework moves between them, with `git pull`.
- **Back the vault up.** With no remote, the disk holds the only copy. Keep Time Machine on, or now and then run `git -C <vault> bundle create <drive>/vault.bundle --all`.
- **If the vault lives inside the framework folder** (the default when you run setup from there), move it out before deleting or re-cloning the framework. The guard stops agents from deleting that folder.

## Troubleshooting

- **`memex: command not found`**: add `~/.local/bin` to PATH (`export PATH="$HOME/.local/bin:$PATH"`), or use `python3 <framework>/tools/memex.py`.
- **Commands like `/ingest` are missing**: open the agent *in the vault folder*, not the framework. In Codex, trust the folder; in Hermes, run `hermes skills trust <vault>`.
- **`memex commit` refuses**: it won't commit with a git remote, or when the vault isn't its own repo. Run `memex doctor`, then `memex doctor --fix`.
- **"Memex guard: … rendered from the Memex framework"**: that file comes from the framework. Change it there, or put a vault-only rule in `.memex/local.md`.

More in the [Memex Manual](docs/Memex%20Manual.md) (also rendered into every vault).

## Docs and contributing

- [docs/Architecture.md](docs/Architecture.md): how the pieces fit, the invariants to keep, and how to make common changes. Start here if you, or your coding agent, are changing the framework.
- [docs/Design Rationale.md](docs/Design%20Rationale.md): why each rule exists, and the trade-offs (cost, scale, autonomy vs oversight).
- [docs/Changelog.md](docs/Changelog.md) · [Contributing](.github/CONTRIBUTING.md) · [Security policy](.github/SECURITY.md)

```bash
python3 tools/test_memex.py   # temporary framework copy and vault, fake HOME and git config; never touches yours
```

## Credits

- Andrej Karpathy, [*LLM Wiki*](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): the pattern this framework implements.
- Vannevar Bush, *As We May Think* (1945): the original Memex, a personal store of documents joined by associative trails. The LLM finally takes care of the maintenance.

## License

[MIT](LICENSE) © 2026 Gaurav
