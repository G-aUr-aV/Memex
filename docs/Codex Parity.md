---
type: doc
---
# Claude Code and Codex memory parity

## Scope and decisions

Both agents use the same Memex CLI, skills, ranking, capture gate, verification procedure and recall policy.
This change closes two integration gaps: Codex prompt recall and Codex usage audits. It does not change
search ranking, resolve knowledge conflicts or introduce an LLM dependency.

1. Install `UserPromptSubmit` for Codex using the existing `memex hook prompt` command. Codex supplies
   `prompt`, `cwd` and `session_id`, and accepts plain stdout as model context, like Claude Code. Keep the
   shared two-page limit, source exclusion, conservative relevance test and session suppression. Preserve
   Codex's three-second SessionEnd timeout and unrelated hooks on refresh/removal.
2. Keep transcript adapters separate from shared metrics. Normalize Codex session metadata, shell calls,
   code-mode shell commands, tool outputs and usage records into the audit's existing session shape.
   Do not execute transcript text. Missing results remain unknown; unsupported shapes are visible in
   coverage rather than silently presented as proof of no activity.
3. Discover Claude transcripts under `~/.claude/projects` and Codex sessions/archived sessions under
   `$CODEX_HOME` (default `~/.codex`). Preserve `MEMEX_TRANSCRIPTS` as the Claude override and add
   `MEMEX_CODEX_TRANSCRIPTS` for isolated Codex input. Namespace deduplication by agent and call/response
   identity so resumed history is counted once and unrelated agents cannot collide.
4. Report aggregate and per-agent metrics, source coverage, incomplete tool results and estimated costs.
   Audit the same completed calls whether made directly or through the Codex code-mode wrapper. Prefer
   response usage records; use legacy token-count events only when those records are absent. Never sum
   cumulative token snapshots as independent requests. Redact published lookup excerpts.

## Validation and rollout

- Test fresh install, upgrade without duplicate handlers, preservation of unrelated hooks and exact removal.
- Exercise Codex-shaped prompt events, no-match/vault-session behavior, session-start suppression and the
  same page limit as Claude Code. The recall engine remains shared.
- Use made-up transcript fixtures for direct calls, code mode, multiple shell commands, completed/failed/
  pending results, resumed history, malformed lines, archived sessions and agent identity collisions.
- Keep existing Claude metrics and harvest tests passing; verify new JSON coverage and human reports.
- Run the complete framework suite, shell/JSON/whitespace checks and managed-file sync verification.
- Refresh only the installed integration. Inspect the installed Codex hook loader and its exact trust state;
  preserve trust review rather than bypassing it. A synthetic hook test proves payload handling; loader
  inspection proves installation. Report whether a new real user prompt has exercised the hook separately.
- Run a read-only local audit to prove Codex sessions are discovered. Do not publish transcript contents or
  treat token estimates as measured answer quality.

Contract: [official Codex hooks](https://learn.chatgpt.com/docs/hooks). Codex transcript formats are not a
stable API; adapters are best-effort and coverage is part of the result.
