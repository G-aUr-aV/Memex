---
name: close
description: End-of-day wrap-up — harvest today's agent sessions, process today's captures (inbox and daily-note Captures), ingest today's meeting notes, update people/project pages and open loops, rewrite wiki/hot.md, and prompt the owner's 3-line reflection. Use when the owner says close, wrap up, end of day, done for today.
allowed-tools: Bash(memex *)
---
# /close — end of day (≈10 minutes)

!`memex ctx close`

0. **Sessions**: run the `/harvest` procedure first (max 10), so today's agent sessions in other repos become knowledge.
1. **Meetings**: ingest today's meeting notes and transcripts that are in inbox/ or in this conversation via the `/ingest` procedure (quick mode unless the owner wants to discuss). Engineering meetings and planning notes get the decisions pass. In the final report, ask whether any other meeting has notes.
2. **Captures**: for each line under today's `## Captures` that holds a decision, TIL, idea or follow-up, decide where it belongs (e.g., TIL → learning concept; decision → engineering decision page; idea → personal idea; follow-up → project or person open loop) and apply it with a citation to the daily note `([[YYYY-MM-DD]])`. Never edit the daily note itself.
3. **Inbox**: run the `/inbox` procedure (max 10 items). Leave `deep` items for tomorrow.
4. **Open loops**: move the owner's unfinished commitments onto the relevant project or person pages as `- [ ]` tasks.
5. **hot.md**: rewrite `wiki/hot.md` (≤40 lines): Current focus · Active projects (one line each, as of today) · Open loops · Recent decisions (last 7 days) · Waiting on.
6. Log `## [YYYY-MM-DD] close | <n> sources, <m> captures`, then `memex commit "close: <date>"`.
7. End by prompting the owner to write 3 lines under `## Reflection` in the daily note (learned / surprised by / tomorrow). **Don't write it for them.**
