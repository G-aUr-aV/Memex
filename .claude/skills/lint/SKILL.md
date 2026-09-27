---
name: lint
description: Health-check Memex — deterministic checks (broken links, frontmatter, citations, quotes, orphans, stale pages, secrets, backlog) plus judgment checks (contradictions, missing pages, weak synthesis), auto-fix only mechanical issues, write a report, log and commit. Use weekly, after 5+ ingests, or when the owner says lint / health check / clean up.
argument-hint: "[--deep]"
---
# /lint — keep the compounding artifact healthy

!`python3 meta/tools/context.py lint`

Mode: $ARGUMENTS (default: weekly; `--deep`: monthly, with a citation audit)

## 1. Deterministic signals (no judgment needed)
- `python3 meta/tools/lint.py --report` writes `meta/lint/<date>.md` and prints the issues.
- If Obsidian is running, cross-check: `obsidian unresolved counts verbose` (ignore links whose only sources are in `meta/templates/`; those are placeholders), `obsidian orphans`, `obsidian deadends`, `obsidian properties counts sort=count` (misspelled keys), `obsidian tags counts sort=count` (tag sprawl).

## 2. Auto-fix — mechanical only
Broken links with exactly one obvious target, missing required frontmatter you can infer, type/folder mismatches (fix with `obsidian move`), and missing Timeline sections. Regenerate the index. **Never** change facts, delete pages, resolve conflicts, or touch `[!mine]` blocks during lint.

## 3. Judgment report — list for the owner, don't fix
- Contradictions: open `> [!conflict]` callouts, plus pages whose claims disagree (check pages that changed since the last lint: `git log --since="8 days ago" --name-only --format= -- wiki | sort -u`).
- Superseded claims lacking a strike-through or Status, and stale `(as of)` facts older than 90 days on engineering pages.
- Concepts or entities mentioned on 3+ pages without their own page (candidate stubs).
- Orphans worth linking or archiving; synthesis pages whose basis pages changed since they were filed.
- Backlog: raw files never cited, inbox items older than 7 days, and the unreviewed page count.
- Questions worth investigating and sources worth finding (web-searchable gaps).
- `--deep` only: pick 5 random factual pages and have the `fact-checker` subagent trace every claim to its source. Report the supported ratio and fix clear misquotes surgically (log each one).

## 4. Finish
Append the judgment list to the report file under `## For the owner`. Append `## [YYYY-MM-DD] lint | <n> issues, <m> auto-fixed` with bullets to `wiki/log.md`, then `python3 meta/tools/memex.py commit "lint: <date>"` (add `--force` if the only errors left are ones you listed for the owner). Reply with a summary of 10 lines or fewer and the report link.
