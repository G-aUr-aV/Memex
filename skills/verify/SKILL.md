---
name: verify
description: Check the wiki's current-state claims (system pages, playbooks, project pages) against the code they describe, read-only on the code. Records the evidence as a verbatim raw snapshot, strikes stale claims with a citation, refreshes summaries, fills verified_on and verified_at_commit, and reports. Use weekly, after big merges, when a page looks out of date, or when the owner says verify, check the wiki against the code, or is this page still true.
argument-hint: "[page name | repo name] (default: the 3 pages verified longest ago)"
allowed-tools: Bash(memex *)
---
# /verify — wiki claims vs the code

!`memex ctx verify`

Target: $ARGUMENTS. A page name checks that page; a repo name checks every listed page for that repo. Otherwise take the first 3 pages above, which are the ones verified longest ago. Work without asking, then report.

The code repos are **read-only**. Never check out, switch, stash, pull, commit, reset, build or run tests in them, and never open `.env` files or secrets. Read other branches with `git -C <repo> show <ref>:<path>`, `git -C <repo> grep -n <pattern> <ref>` and `git -C <repo> log`. Follow the owner's own instructions about each repo, e.g. files they've said not to read.

## 1. Pick claims
For each page, find its repo in the checkouts listed above; if it isn't listed, say so and skip the page. Record the ref you check against: the default branch or the latest `release/*` branch, plus the branch the page names if it still exists (`git -C <repo> branch -a --list`). Then pick 3–8 **load-bearing current-state claims**:
- file paths, classes and methods, endpoints, config values, commands;
- and especially status: "uncommitted", "on branch X", "merged", "planned", "decided".

History sections ("on 10-19 we tried…") are not current-state claims; leave them.

## 2. Check each claim
Use one read-only command per claim, and classify it:
- `accurate`;
- `stale` (was true, the code moved on);
- `wrong`;
- `unverifiable` (say why: branch gone, external system, needs runtime).

For "uncommitted" or "on branch X" claims, search every local ref before calling them gone: `git log --all -S<symbol> --oneline`, `git branch -a --contains <sha>`, `git stash list`. If the work is in no local ref, say exactly that ("not in any local branch or stash as of YYYY-MM-DD"). Never call it lost or deleted.

## 3. Write the evidence (raw snapshot)
Create one raw file per repo per run: `raw/engineering/YYYY-MM-DD Code check <repo> <short-sha>.md`.
- **Frontmatter:** `title`, `source: "git <repo path> @ <ref> <short-sha>"`, `author: code`, `published` and `captured` (today), `source_type: code-snapshot`, `domain: engineering`.
- **Body:** a `## <Page name>` heading per page, then for each claim the exact command and its verbatim output, trimmed to the relevant lines (≤15), in a fenced block.
- No paraphrase in raw/, and nothing you didn't run. Redact anything secret-looking (`[REDACTED: <kind>]`).

## 4. Update the pages
Use surgical Edits and AGENTS.md's integrity rules.
- **Stale or wrong claim:** `~~old claim~~ superseded YYYY-MM-DD by ([[<snapshot>#<Page>|src]])`, then the corrected claim with the same citation.
- **Current state changed:** rewrite `summary:` and `> [!summary]` too.
- **Owner-reviewed claim or `> [!mine]` correction:** never change it. Add `> [!conflict]` quoting both sides.
- **Open `> [!conflict]`:** if the code settles it, add the evidence under it ("Code check YYYY-MM-DD: …" with the citation), but leave the decision to the owner.
- **Fully checked page** (every checked claim is accurate or now corrected): set `verified_on: YYYY-MM-DD` and `verified_at_commit: <repo>@<short-sha>`, or `verified:` on playbooks. Otherwise leave them as they were, and say why in the report.
- Add a Timeline line on every page you touch, and set `updated` if content changed.

## 5. Finish
1. Append ONE log entry, `## [YYYY-MM-DD] lint | verify: <pages>`, with one bullet per page: claims checked, accurate, stale, wrong, unverifiable → what changed.
2. Then `memex commit "lint: verify <pages>"`.
3. Report in ≤10 lines:
   - per page, the counts and what changed;
   - work that no local ref holds, as a heads-up for the owner;
   - conflicts the code settles, waiting for the owner;
   - pages left unverified, and why.
