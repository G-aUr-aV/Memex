---
type: doc
---
# Changelog

Notable changes to the Memex framework. The format follows [Keep a Changelog](https://keepachangelog.com/). Dates are ISO.

## Unreleased: 0.3.0, framework and vault split
### Added: hybrid search, off by default
- **`memex search --engine hybrid`** (or `"search": {"engine": "hybrid"}` in `.memex/vault.json`) fuses the keyword ranking with a ranking by meaning, using reciprocal rank fusion. One embedding per wiki page comes from a local Ollama model (`embeddinggemma` by default) and is kept in the machine-local `.memex/vectors.json`; `memex index --vectors` builds it and a search re-embeds the few pages that changed. The URL must be `localhost`, proxies are ignored and redirects refused. When the model or the index can't be used, search says so on stderr and returns the keyword results. Recall and the capture duplicate gate still use keywords only.
- **`memex audit` compares the engines** on `meta/audit/search-benchmark.tsv`: top-1 and top-3 hits, by kind when the file has a third column, no-answer queries (`-` as the page) answered without a warning, and median and p95 time. The default stays `keyword` until a vault's own numbers favour hybrid.
- **Search says when Memex probably has no answer.** If even the best page has under half of the query's terms, the result says so and each hit shows how many terms it has. Superseded and archived pages are listed below current ones and labelled.

### Fixed
- **Recall never shows half a command as a whole one.** A `recall:` line over 240 characters is cut at a word and ends in `…` (it used to stop mid-word with no mark), and a block over its 14 lines says how many were cut.
- **`memex audit` counts a recalled page as read only when `memex read` printed it after it was shown.** A search that mentioned it, a failed read, an earlier read or its name inside another command no longer count. The three fix lists are worded as leads, and queries, capture titles and paths taken from transcripts are redacted in the terminal, `--json` and the saved report.
- **Secrets are screened at both doors.** `memex capture` scans `--source`, `--project` and `--target` as well as the note, and `memex file` refuses an inbox item with a likely secret before it reaches `raw/`, leaving it in `inbox/` and naming only the kind.
- **`/verify` records what an approved command ran on**: branch, commit and whether the tree had local changes, and uses the result only for that commit and state.
- Generated indexes, `.memex/state.json` and `.memex/remote.json` are replaced in one step, so an index rebuild at session start can't hand a concurrent commit a half-written file.
- **Prompt recall ignores turns the harness started.** A subagent's report or a background-task notice (`<agent-message>`, `<task-notification>`) no longer gets pages matched against its text, which also used up those pages for the rest of the session. System reminders that carry attributes are stripped like plain ones.
- Preserve SSH ownership and host-key diagnostics in remote-sync errors instead of hiding them behind Git's generic fatal message.
- **Codex memory parity:** install the shared prompt-recall hook for Codex as well as Claude Code, preserving exact hook trust review, the two-page budget, session suppression and the three-second SessionEnd timeout. `memex audit` now reads both agents' transcripts, including Codex code-mode and asynchronous calls, reports combined/per-agent metrics and coverage, and avoids counting resumed history, cached tokens or cumulative usage twice. Lookup excerpts are redacted; incomplete captures are not credited as writes. Combined-agent trends use a new record version.
- Keep the Python 3.9 Linux CI job on Ubuntu 24.04: `ubuntu-latest` moved to Ubuntu 26.04, where `setup-python` cannot install Python 3.9. Python 3.12 still tests `ubuntu-latest`; both Python versions continue to test macOS.
- Reconciled the independently added `/auto-inbox` workflows: retain full-inbox context, interrupted-run recovery and `/verify` coordination while preserving scoped commits and unrelated staged or unstaged changes. Recovery is checked before exiting for an empty inbox; review-only runs never file or commit recovery candidates.
- Codex's global `SessionEnd` hook now requests its supported 3-second timeout instead of 10 seconds, removing the timeout-clamping warning. Setup refreshes existing installations with the corrected value; other hook timeouts are unchanged.

### Changed
- **Shorter instructions for agents outside the vault.** The Memex block in `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` is about half as long; the details live in the `memex` skill. Re-run `setup.sh` to refresh it.

### Changed (breaking layout)
- **The framework no longer is the vault.** This repo holds code only (`schema/ skills/ agents/ config/ hooks/ tools/ templates/ seed/ docs/`). `setup.sh` (now at the root) creates the vault in a separate folder, asking where. The default is `MemexVault` in the folder you run it from; a vault inside the framework folder is git-ignored there.
- **Managed files are rendered, not copied by hand.** `memex sync` renders the schema (`AGENTS.md` + `CLAUDE.md`), skills, subagents, path rules, Claude Code and Codex settings, templates and docs into the vault. They're excluded from the vault's git, the guard blocks edits to them, and each session re-syncs when the framework changed. Hand edits are backed up to `.memex/backup/`.
- **Per-vault layer**: `.memex/vault.json` (name, domains, commit identity), `.memex/local.md` (vault-only rules, rendered with precedence), `.memex/domains.json` (vault-only domains, e.g. `"extends": "engineering"`).
- **Domains are data**: `schema/domains.json` defines the presets and the default set. Nothing about domain names is hardcoded any more.
- Skills call `memex ctx`, `memex lint` and `memex commit` instead of tool paths.

### Added
- **`recall:` may be a short list.** A playbook's `recall:` can hold up to 5 lines (the test command, the interpreter, the database). Session start prints them all, so a session can use them without a lookup; the 14-line budget still holds.
- **`memex audit` names what to fix:** the queries that found nothing, the pages shown at session start that no session opened, and the files most work sessions opened in each repo. `/audit` carries them into its verdict.
- **Upkeep may run commands the vault lists.** `/verify` stays read-only on code repos, except for commands under "Commands upkeep may run" in the vault's `.memex/local.md`, for facts that need a run.
- `/auto-inbox`: scheduled material-value review and serial quick ingestion, with a read-only review mode, preserved deferred/no-material entries, no deletions and checks for overlapping wiki work. `/ingest` now states the shared admission test explicitly: compile source-supported knowledge the owner will revisit, with a clear purpose and a material addition or correction.
- `memex commit --only <files...>` commits a batch's named vault files and generated indexes while preserving unrelated staged and unstaged edits. Default commits are unchanged; scoped commits retain identity, lint and hooks, and refuse directories, paths escaping the vault and partial merge resolutions.
- **Vault git (local-only by default)**: repo-local `Memex Agent <memex-agent@localhost>` identity (also `author.*`/`committer.*`), no commit signing, local `core.hooksPath`, a pre-commit hook that also refuses other identities, and a pre-push hook that refuses everything. `memex commit` forces the identity through the environment (beating global and `includeIf` config), refuses to run with a remote or without its own repo, and adds a `Memex-Framework: <version> (<sha>)` trailer.
- The framework's own pre-commit hook and a CI check refuse knowledge folders, vault config and embedded repos.
- `memex init`, `memex sync [--check]`, `memex doctor [--fix]`, `memex ctx`, `memex index`, `memex lint`.
- **`memex-setup` skill** (`.claude/skills/memex-setup`, also `.agents/skills` for Codex): an agent opened in a fresh clone installs Memex end to end. It checks prerequisites, picks the vault folder, runs setup without prompts, puts `memex` on PATH, verifies with doctor, lint and tests, and reports the Obsidian clicks left.
- README rewritten: diagrams (overview, setup flow, rule propagation, guardrails), both setup paths, and a worked first session with real output.
- Setup always installs the `memex` CLI, even when no agent is found.
- Tests check that skills and docs only use real `memex` subcommands and `setup.sh` flags.
- Guard: protects managed files and `.memex/vault.json`, refuses deleting or moving a folder that holds the vault, and blocks `git clean -ff/-x/-X` and `git stash --all` there.
- MIT license.

### Added: unattended inbox
- **`/auto-inbox` skill** for scheduled runs: reviews every inbox capture for material value (`auto` · `review` · `no-material`), quick-ingests up to 10 clear keepers serially without asking, and leaves the rest in `inbox/` with a reason. It never deletes, switches to review-only when `wiki/` or `raw/` already has uncommitted changes, and starts its report with a one-line headline a scheduler can show. In Claude Code it runs on Claude Sonnet 5.5 at medium effort (`model:` and `effort:` frontmatter), whatever the session's model; other agents ignore those fields.
- `memex ctx auto-inbox`: the whole inbox (no 20-item cap) plus the vault's uncommitted changes outside `inbox/`.

### Changed: /verify checks what's due, not just the oldest 3
- `memex ctx verify` marks a page **due** when it was never verified, changed after its last check, or was last checked over 30 days ago. Due pages are listed by exposure (playbooks, which recall shows first, then repo-tied pages), then by reason and age; pages that aren't due are summarised on one line.
- `/verify` with no argument now checks the first 6 due pages (`all` or a number N also work) and does nothing when nothing is due, so unchanged pages aren't re-checked for the sake of it.
- **Runs don't trample each other.**
  - `/verify` stops with "SKIPPED: another vault run in progress" when `wiki/` or `raw/` has uncommitted changes.
  - `/auto-inbox` stops with "SKIPPED: /verify in progress" when a code snapshot is uncommitted, instead of mistaking it for a half-filed capture.
  - The session context's half-filed warning ignores code snapshots too.

### Added: task-aware recall, no source page per capture, upkeep helpers
- **Prompt recall** (`memex hook prompt`, installed as Claude Code's `UserPromptSubmit` hook).
  - Each prompt can bring up to 2 compiled pages that it names (a title or alias phrase, or a single-word alias) or is clearly about. "Clearly about" means its words in the page's title, aliases and summary are rare among compiled pages: summed IDF ≥ 6.5, with 2+ words found in ≤2 pages.
  - Pasted blocks, URLs, path prefixes, package names and generic words are ignored, and a page a session was already shown (at start or earlier) never repeats.
  - On 70 real first prompts it offered pages for 28, all on topic, and nothing for prompts with no matching page. It takes about 20 ms. `recall.prompt: false` turns it off.
- **Captures skip the source page.** Schema, `/ingest` and `/auto-inbox`: a `source_type: capture` note is compiled straight into its canonical pages, which cite its raw file. It gets a source page only when no compiled page fits, or for redaction or embedded-instruction warnings; real sources keep theirs. Before this, 108 source pages sat against 18 compiled pages, and 77 of them were near-copies.
- **`memex harvest --skip-captured`** marks sessions that already ran `memex capture` as skipped, so `/harvest` covers the sessions that captured nothing. `/harvest` runs it first.
- **`/verify`** is pinned to Sonnet 5.5 at medium effort in Claude Code, like `/auto-inbox`, for cheap scheduled runs.

### Added: `memex audit` and `/audit`, so "is Memex helping?" has numbers
- **`memex audit`** (`tools/audit.py`) reads Claude Code transcripts (`~/.claude/projects`, or `$MEMEX_TRANSCRIPTS`) and the vault for a window (`--days N`, default 14, or `--since`/`--until`). It reports, for sessions outside the vault and the framework:
  - use: % of sessions that looked up or captured, captures written vs refused by the duplicate gate, captures per full read;
  - retrieval: zero-hit and partial-only queries, follow-up reads, recalled pages opened later, stall alarms;
  - re-discovery: captures repeating an earlier capture from another session;
  - cost and latency: Memex's share of input and output tokens, time spent writing captures;
  - pipeline health: inbox, half-filed raw, conflicts, verified system pages, unreviewed pages, hot.md age, backup;
  - the vault's search benchmark, `meta/audit/search-benchmark.tsv` (`query<TAB>expected page`), with hit@1 and hit@3.
- `--record` appends the key numbers to the machine-local `.memex/audit.jsonl`, and each audit prints "was … in <window>" against the last recorded one. `--report` writes `meta/audit/<date>.md`; `--json` prints everything; `--lookups` lists every lookup with its output. Read-only otherwise, and runs in about 2 s.
- **`/audit` skill**: the numbers, then a judged sample of real lookups (relevance, used, impact, stale) with the 2026-11-06 audit's rubric, written into the report with a short verdict, then logged and committed.

### Changed: fewer, better captures, and claims checked against the code (audit follow-up)
- **Capture discipline.** The standing instructions (the CLAUDE.md / AGENTS.md block and the `memex` skill) now say:
  - capture only durable, *settled* knowledge, and search first;
  - add to what's already there with `--action update`;
  - put "as of" on volatile facts;
  - look things up before building, testing or running in a repo;
  - treat Memex as a lead to check against the code.
  - "Say the captured path at the end of your reply" is gone.
- **Duplicate gate.** `memex capture` refuses a new capture whose title says what an existing page, source or inbox item already says (≥60% of its words). It names the match, and for a source page the compiled page to update instead. `--new` overrides.
- **Folded sources.** `memex search` folds away source pages that a compiled page ranked above them already cites; `--all-sources` lists them.
- **`/verify` skill + `memex ctx verify`.** Check system pages and playbooks against the code they describe, read-only on the code.
  - The evidence is saved as a verbatim code snapshot in `raw/`, which stale claims are struck against.
  - Summaries are refreshed, and `verified_on`/`verified_at_commit` are filled in.
  - Code that settles an open conflict is attached as evidence, but the owner decides.
  - The ctx lists pages least recently verified first, plus each repo's main checkout from the session ledger.
- **Summaries follow the body.** The schema and `/ingest` now rewrite `summary:` and `> [!summary]` whenever an update changes what a page says is current.
- **Stale `hot.md` is flagged.** The vault session context marks `wiki/hot.md` when it hasn't been rewritten for over 7 days.

### Changed: retrieval and recall reach the moment of need (from the 2026-11-06 usage audit)
- **Search covers the inbox.** `memex search` now searches `wiki/` plus `inbox/` by default, and labels inbox hits "captured, not compiled or reviewed yet". Before this, knowledge was invisible for up to days after capture. `--in wiki` keeps the old scope; `--in inbox` is new.
- **Ranking.**
  - Compiled pages get a ×1.5 prior and per-capture source pages and inbox items ×0.8. BM25 length normalisation used to rank short dated sources above the canonical page.
  - A page whose title or an alias equals the query comes first.
  - `wiki/hot.md` is no longer searched.
  - The default limit is 5, and the header says how to see the rest.
  - On the audit's 25-query benchmark, hit@1 went from 9 to 22 and hit@3 from 18 to 23.
- **Recall.**
  - Matches `applies_to:` as well as `repo:`/`repos:`, and never picks source pages.
  - Lists playbooks first under "Before building, testing or running anything here", with an optional one-line `recall:` field (else the summary); other pages follow as background.
  - Each line says when the page was verified, or "not verified against code" for system pages.
  - The `More:` footer is never cut off.
- **Stall alarm.**
  - When the oldest inbox capture has waited longer than `recall.stall_hours` (default 24), SessionStart in other repos prints one line asking the agent to tell the owner.
  - The vault session context warns too, and also lists `raw/` files that were filed but never committed.
- **`/auto-inbox` resumes a run that died.** Untracked `raw/` files with no uncommitted wiki changes are compiled as the oldest items instead of forcing review-only forever. Any other dirty state makes the headline start with `auto-inbox: STALLED:`.

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
- **`/ingest` no longer interviews you.** It sends one message with the takeaways, a drafted take (why it's here, surprising, doubtful; or what to emphasize, for engineering) and the page plan, then waits for **ok** or your changes. An approved draft is stored in `> [!mine]`, headed `(agent's draft, approved YYYY-MM-DD)`. Your own words replace it wherever you give any.
- **Other projects write only through the inbox.** Agents outside the vault capture (`memex capture`, with `--action update|supersede|delete` for existing pages) and never edit vault files. The "small direct fix" instruction and the global `Edit` permissions for the vault are gone. The guard refuses file-tool and shell writes, `memex rm`/`memex file` and `obsidian` writes into the vault from sessions outside it.
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
