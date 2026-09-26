---
name: weekly
description: Friday weekly review — engineering worklog and brag-doc evidence from GitHub (read-only), PR review lessons, goals scoring, people follow-ups, journal reflection synthesis, light lint, hot.md refresh, and a review brief with candidate insights for the owner. Use when the owner says weekly, week review, Friday review.
---
# /weekly — Friday review (≈30 minutes)

!`python3 meta/tools/context.py weekly`

Do the steps in order and show progress. Every connector call here is **read-only**.

1. **Engineering evidence** (GitHub, read-only; skip if `gh` isn't installed or authenticated): `gh search prs --author=@me --updated=">=<7 days ago>" --json repository,title,url,state`, `gh search prs --reviewed-by=@me --updated=">=<7 days ago>"`, `gh search issues --involves=@me --updated=">=<7 days ago>"`, then `gh pr view <url> --comments` on notable PRs. Save a **minimal snapshot** to `raw/engineering/<Monday date> Weekly Snapshot <YYYY-Www>.md`: repo, title, the owner's role, outcome, link, and notable review comments paraphrased in 1 line each. No diffs. Also use this week's commits in local repos the owner mentions, if asked.
2. **Worklog**: `wiki/engineering/career/Worklog <YYYY-Www>.md` with sections Shipped · Reviews given · Design & decisions · Learned. Every bullet cites the snapshot or a wiki page. Skip it in weeks with no engineering activity.
3. **Brag doc**: append impact + evidence bullets to `wiki/engineering/career/Brag Doc <YYYY>.md` (create it from the template if missing).
4. **PR lessons**: cluster recurring reviewer feedback into `wiki/engineering/career/PR Review Lessons.md` and refresh its "pre-PR checklist".
5. **Goals**: propose key-result scores (0.0-1.0) on `wiki/personal/goals/` pages for the owner to confirm.
6. **Reflection**: read this week's `journal/daily/*.md` and write `wiki/personal/reflections/Week <YYYY-Www>.md`: wins, energy patterns, recurring themes, lessons. Quote the owner's words with links to the dated notes, update people's `last_contact` from explicit mentions, and end with 3 questions for the owner. No interpretation of feelings.
7. **People**: list `People to reconnect` and `Birthdays this month` (Dashboard.base), and suggest up to 3 reach-outs.
8. **Lint-lite**: run `python3 meta/tools/lint.py`, fix mechanical errors, and list the rest.
9. **hot.md**: rewrite it for next week.
10. **Brief for the owner** (in chat; the owner writes their own review in `journal/reviews/<YYYY-Www>.md`):
    - the inbox backlog
    - the review queue (pages changed this week with empty `reviewed:`)
    - open conflicts
    - stale engineering facts (systems never verified against code)
    - up to 5 **candidate insights** as complete-sentence claim titles for `notes/`, each with 2-3 supporting links
    - flashcards awaiting approval
11. Build the index, log `## [YYYY-MM-DD] weekly | <YYYY-Www>` with bullets, and commit.
