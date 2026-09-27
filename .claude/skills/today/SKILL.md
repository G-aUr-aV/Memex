---
name: today
description: Morning brief — create or refresh today's daily note Brief with meetings (plus prep from people/project pages), carry-over tasks, due follow-ups, birthdays and renewals, and suggested focus. Use when the owner says today, morning, brief, plan my day, or starts the day.
---
# /today — morning brief (≤ 5 minutes)

!`python3 meta/tools/context.py today`

1. **Daily note**: if it's missing, create `journal/daily/<today>.md` from `meta/templates/Daily.md`, replacing `{{date:YYYY-MM-DD}}` with today's date. If it exists, edit **only** the `> [!brief]` callout.
2. **Gather** (read-only):
   - Calendar: today's events via the Google Calendar connector if it's connected (skip silently if not). For each meeting whose attendees or topic have a `people/` or `projects/` page, pull 1-2 bullets: last decision, open loops, what the owner owes. Mark 1:1s with a pointer to `/prep <person>`.
   - Carry-over: unchecked tasks from yesterday's note and `obsidian tasks todo` on wiki project pages due or overdue.
   - Due: `obsidian base:query path="meta/bases/Dashboard.base" view="People to reconnect" format=md`, the `Birthdays this month` view, and the `Renewals soon` view (use Grep on frontmatter if Obsidian is closed).
   - Focus: `wiki/hot.md` (current focus and open loops).
   - If it's after 12:00, include only Meetings and Due.
3. **Write the brief** inside the `> [!brief]` callout. Every line starts with `> `, and the block stays under 25 lines:
   `**Focus** (suggest top 3; the owner decides) · **Meetings** (time — title — prep bullets) · **Carry-over** · **Due** (follow-ups, birthdays, renewals) · **Inbox**: N items`.
4. Append `## [YYYY-MM-DD] today | brief` to `wiki/log.md` and run `python3 meta/tools/memex.py commit "today: brief"`. Reply with the brief in chat as well.
