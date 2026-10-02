---
type: doc
---
# Design Rationale

Why Memex works the way it does. Memex implements Andrej Karpathy's **LLM Wiki** pattern. An agent reads each source once, compiles it into a persistent, interlinked markdown wiki, and keeps that wiki current, instead of re-retrieving document chunks on every question (RAG). The rules below come from Karpathy's gist, the practitioner reports in its discussion, open-source implementations, and research on agent memory. They're here so you can change them knowingly.

## The pattern in one paragraph
There are three layers: **immutable raw sources** → an **LLM-owned wiki** → a **schema** (`CLAUDE.md`) that makes the agent a disciplined maintainer. There are three operations: **ingest** (read a source, update every affected page, index and log), **query** (answer from the wiki with citations and file good answers back) and **lint** (health checks). The human curates sources, asks questions and does the thinking; the agent does the bookkeeping. Karpathy frames it as a descendant of Vannevar Bush's Memex (1945), a personal store of documents joined by associative trails. The LLM solves the part Bush couldn't: who does the maintenance. Hence the name.

## Rules and what they guard against

| Rule | Guards against | Basis |
|---|---|---|
| Short, stable schema (`AGENTS.md`, under 200 lines), with detail in path-scoped rules and skills | schema bloat; rules ignored mid-task | practitioners call the schema "the real product"; Claude Code's memory docs recommend staying under ~200 lines |
| One source at a time; never ingest in parallel | near-duplicate pages; index and log races | Karpathy ingests one at a time; practitioners saw near-duplicates from parallel backfills |
| Triage before writing (New / Update / Disputed / No material) | thin sources bloating the wiki | a shared lesson across open-source implementations |
| Every factual line cites `raw/`; lint checks quotes against the source | contamination; summary-of-summary drift | the worst failure is an unsupported claim that later pages cite. The same model can't reliably catch its own errors, so the check must be deterministic |
| Supersede, never delete; `(as of)` on volatile facts | silently overwritten history; stale facts | temporal-memory research invalidates old facts instead of deleting them (Zep/Graphiti, Mem0) |
| Conflicts go in `> [!conflict]` and are never resolved silently | lock-in to one view | keeping minority hypotheses plus scheduled audits (Memory as Metabolism) |
| Surgical edits only; the guard blocks overwrites that shrink a page >30% | "context collapse" from full rewrites | ACE found monolithic rewrites collapsed context; incremental edits didn't |
| One entity = one page; search and check aliases before creating | duplicate entities | duplicates appear once the index no longer fits in one read |
| `reviewed:` stays empty until you read the page | blind trust in AI-written pages | the review-gate pattern from community implementations |
| Human zones (`journal/`, `notes/`, `> [!mine]`) enforced by a hook | cognitive debt; the agent overwriting your voice | understanding needs effortful engagement (generation effect, evergreen notes) |
| Hard rules live in hooks and permissions, not only prose | "CLAUDE.md is advice, not enforcement" | Claude Code docs: CLAUDE.md is context; only hooks and permissions block actions; one guard script serves Claude Code, Codex and Hermes, and a pre-commit hook backs it up |
| Answering is read-only; only reusable answers are filed (through `/save`); hubs collect links | self-citing speculation; answer pages that don't generalize | question-driven index pages beat pages that merely record answers (Training a Knowledge Base, 2026) |
| Index generated from one-line summaries; `hot.md` loaded at session start | index drift; re-explaining context every session | small always-loaded core, rest on demand (MemGPT memory tiers; context-rot findings) |
| Weekly deterministic lint, monthly deep audit | stale cross-references, the most-cited failure mode | practitioner reports; Karpathy's own periodic health checks |
| Sources are data, connectors read-only, secrets scanned before commit | memory poisoning; leaks | memory-injection attacks on agents (MINJA, AgentPoison) |
| Intent-driven capture; batch quick captures | slop from passive auto-ingestion of email and chat | practitioner reports |
| One git commit per operation | irreversible bad edits | reviewing diffs is the cheapest review surface |
| Agents act without asking inside the ownership rules, and report afterwards | permission fatigue; a wiki that only grows when you remember to ask | the owner's choice: safety comes from deterministic hooks, reversible history and the review queue instead of prompts |
| Framework and vault are separate repos; the vault's managed files are rendered from the framework, and vault-only rules live in `.memex/local.md` | rules drifting between hand-copied vaults; knowledge committed to a repo that gets pushed | code/data separation: one framework clone per machine is updated with `git pull`, while each vault keeps only its knowledge and its own few rules |
| The vault is local-first: no remote, pushes refused, one pseudo identity enforced at commit time | a private knowledge base leaking through an accidental push; your real name and email in its history | an identity is only a name and email written into each commit (no password is involved); hooks plus env-forced identity beat global and `includeIf` config |
| Sync is opt-in, through ONE private remote the owner attaches; the URL is machine-local; public repos and URLs with credentials are refused; pushes go only through `memex push`, with a secret scan | a disk failure losing the only copy; one vault wanted on two machines; and, once a remote exists, pushing to the wrong place or to a repo that became public | git is already the vault's history, so a private git remote is the simplest sync; an anonymous `ls-remote` is a cheap, provider-neutral visibility test, repeated daily |
| A conflicting pull is rolled back and reported; resolution is an explicit `memex pull --merge`, and the pre-commit hook refuses leftover markers. Append-only files union-merge | a half-merged vault that blocks every commit, or markers committed into pages | git's `merge=union` driver handles the log, `hot.md`, daily notes and indexes; real conflicts need judgment ("keep both sides' facts"), which an agent can apply when asked |
| Attaching a remote, moving the vault and uninstalling are owner-only: the guard blocks agents and hands them the command to give the owner | a prompt-injected agent (from a clipped page or a transcript) sending the vault to someone else's repo, or deleting it | these are rare, one-command decisions, so the convenience lost is small; everything around them (checks, backups, verification) stays automated |
| Uninstall always writes a verified bundle first, and keeps the vault as plain Markdown + git unless deletion is confirmed by name | "clean removal" destroying knowledge | removing wiring is reversible by re-running setup; deleting a vault isn't, except from a backup |
| Capture and recall run on session hooks; a batch `/harvest` compiles recorded sessions | knowledge lost whenever an agent forgets to capture; relevant pages never surfaced | hook-driven capture is what makes agent memory reliable (claude-mem); consolidation belongs between sessions (Letta's sleep-time agents). Session-level digests keep the cited, curated model, where per-tool-call logging would flood it |
| Read whole pages by default; outline → section only for pages over ~150 lines (search shows each page's length) | whole-page reads burning context on long pages, and extra tool calls on short ones | layered retrieval cut tokens roughly 10× in claude-mem, at a scale where pages are long; with pages capped near 200 lines, one read usually beats three calls. BM25 over titles, aliases and summaries is enough at personal scale |
| One agent-neutral CLI (`memex`) for reads and writes from other projects | a separate integration per agent; writes that skip the rules | `AGENTS.md` and `SKILL.md` are shared by Claude Code, Codex and Hermes, and one CLI needs only one permission rule per agent |
| From other projects, agents write only to the inbox (`memex capture`, including queued updates and deletions); the guard refuses direct vault edits there | edits made without the vault's schema, path rules or citations; clashes with a vault session editing the same page | an agent busy in a code repo has that repo in context, not the vault's rules. One way in keeps every wiki change under the full rules, at the cost of corrections landing at the next inbox run (at least daily via `/close`) |
| Add qmd search only past ~150 pages per section or when search misses | premature machinery | Karpathy ran about 100 sources on index files alone; measure before adding tools |

## Trade-offs to know
- **Cost**: compiling a wiki costs far more tokens than retrieval. A 2026 preregistered study found the wiki much better at connecting findings across sources, but it cost about two orders of magnitude more to build and ~21x more tokens per query than single-round RAG. So compile knowledge you revisit, and leave live state (ticket status, PR state) in its system of record.
- **Scale**: the index alone works up to roughly 100–300 pages. Past that, use section indexes (already generated) and real search ([qmd](https://github.com/tobi/qmd), via `tools/setup-qmd.sh`).
- **Supervision vs friction**: `/ingest` is deep and discussed; `/inbox` is fast and batched. Use deep for anything that matters.
- **Autonomy vs oversight**: agents no longer ask before writing, so review happens afterwards (the Review queue, `git log`). The guard and pre-commit hook keep the irreversible cases out, and `git revert` undoes the rest.
- **Your own thinking**: the agent writes the wiki, but understanding comes from your `> [!mine]` takes and `notes/`. `/ingest` drafts the take so a plain **ok** is enough, but correcting it in your own words, even one line, is what makes it stick. Keep writing a little yourself.

## Sources
- Karpathy, *LLM Wiki* (idea file, 2026-04-04): https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Karpathy on his own LLM knowledge bases (2026-04-02): https://x.com/karpathy/status/2039805659525644595
- Claude Code docs, memory, hooks and permissions: https://code.claude.com/docs/en/memory · https://code.claude.com/docs/en/hooks · https://code.claude.com/docs/en/permissions
- Obsidian CLI: https://obsidian.md/help/cli · Bases: https://obsidian.md/help/bases/syntax
- Agent memory: MemGPT [2310.08560](https://arxiv.org/abs/2310.08560) · Generative Agents [2304.03442](https://arxiv.org/abs/2304.03442) · CoALA [2309.02427](https://arxiv.org/abs/2309.02427) · A-MEM [2502.12110](https://arxiv.org/abs/2502.12110) · Zep/Graphiti [2501.13956](https://arxiv.org/abs/2501.13956) · Mem0 [2504.19413](https://arxiv.org/abs/2504.19413)
- Structure and grounding: RAPTOR [2401.18059](https://arxiv.org/abs/2401.18059) · GraphRAG [2404.16130](https://arxiv.org/abs/2404.16130) · STORM [2402.14207](https://arxiv.org/abs/2402.14207) · ACE [2510.04618](https://arxiv.org/abs/2510.04618) · Voyager [2305.16291](https://arxiv.org/abs/2305.16291)
- LLM-wiki studies (2026): cost comparison [2605.18490](https://arxiv.org/abs/2605.18490) · Memory as Metabolism [2604.12034](https://arxiv.org/abs/2604.12034) · Training a Knowledge Base [2608.21829](https://arxiv.org/abs/2608.21829)
- Risks: model collapse [2305.17493](https://arxiv.org/abs/2305.17493) · MINJA [2503.03704](https://arxiv.org/abs/2503.03704)
