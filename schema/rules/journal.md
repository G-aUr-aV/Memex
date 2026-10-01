---
paths:
  - "journal/**"
  - "notes/**"
---
# Journal & notes — The owner's own writing

- `journal/daily/YYYY-MM-DD.md` is the owner's. You may create today's note from `meta/templates/Daily.md` if it doesn't exist, and you may edit **only** the `> [!brief]` callout at the top (used by `/today`). Everything else is read-only (the guard hook enforces this).
- To log a quick capture for the owner, when they ask, use `obsidian daily:append content="- HH:MM …"` (it appends at the end).
- `journal/reviews/` holds the owner's weekly and monthly reviews. `/weekly` prepares a brief in chat and never writes here.
- `notes/` holds the owner's evergreen notes (complete-sentence claim titles). Never write or edit them. Link to them from wiki pages under `## My related thinking` when relevant.
- Journal entries are primary sources: cite them as `([[2026-09-25]])`. Don't ingest daily notes one by one; `/weekly` synthesizes them into `wiki/personal/reflections/` and updates people's `last_contact` from explicit mentions.
- When answering questions, label points that come from the owner's own writing (`notes/`, `journal/`, `[!mine]`) as **[mine]** and points that come from the wiki as **[wiki]**.
