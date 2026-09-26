---
name: close
description: End-of-day wrap-up — process today's captures (inbox and daily-note Captures), ingest today's meeting notes, update people/project pages and open loops, rewrite wiki/hot.md, and prompt the owner's 3-line reflection. Use when the owner says close, wrap up, end of day, done for today.
---
# /close — end of day (≈10 minutes)

!`python3 meta/tools/context.py close`

1. **Meetings**: ask the owner whether any of today's meetings have notes or transcripts to ingest (they can paste them or drop them in inbox/). Ingest each one via the `/ingest` procedure; engineering meetings and planning notes get the decisions pass.
2. **Captures**: for each line under today's `## Captures` that holds a decision, TIL, idea or follow-up, propose where it belongs (e.g., TIL → learning concept; decision → engineering decision page; idea → personal idea; follow-up → project or person open loop). After the owner's OK, apply it with a citation to the daily note `([[YYYY-MM-DD]])`. Never edit the daily note itself.
3. **Inbox**: run the `/inbox` procedure (max 10 items). Leave `deep` items for tomorrow.
4. **Open loops**: move the owner's unfinished commitments onto the relevant project or person pages as `- [ ]` tasks.
5. **hot.md**: rewrite `wiki/hot.md` (≤40 lines): Current focus · Active projects (one line each, as of today) · Open loops · Recent decisions (last 7 days) · Waiting on.
6. Build the index, log `## [YYYY-MM-DD] close | <n> sources, <m> captures`, run lint `--quick`, and commit.
7. End by prompting the owner to write 3 lines under `## Reflection` in the daily note (learned / surprised by / tomorrow). **Don't write it for them.**
