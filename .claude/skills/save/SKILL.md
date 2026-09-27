---
name: save
description: File the latest answer or the current conversation's conclusions into Memex as a wiki page (synthesis by default; concept or playbook when that fits better), link it from the pages it draws on, regenerate the index, log and commit. Use when the owner says save this, file this, keep this, add this to Memex after an answer or discussion.
argument-hint: "[title] [domain]"
---
# /save — make explorations compound

Target: $ARGUMENTS

1. **Classify** the content:
   - `synthesis`: an answer, comparison, analysis or decision rationale drawn from existing pages. This is the default.
   - `concept`: a genuinely new, reusable idea. It still needs raw citations; if there aren't any, it's a synthesis.
   - `playbook`: a procedure that **actually worked** in this conversation.
   Never classify it as a source.
2. **Domain**: engineering for your own projects, repos, tools and career; learning for transferable knowledge; personal otherwise.
3. **Write** `wiki/<domain>/syntheses/<Title>.md` (or `concepts/` or `playbooks/`) from the matching template:
   - frontmatter: `question`, `basis` (the wiki pages used), `sources` (raw files cited), `filed` date, `reviewed:` empty
   - `> [!summary]` with the answer
   - the body with citations as in the original answer, keeping the reasoning path for long threads rather than just the last reply
   - `## Gaps & confidence`
   - `Origin: filed from conversation on YYYY-MM-DD`
   Link only pages that exist.
4. **Back-link**: add one line under `## Connections` on the 1-3 most relevant basis pages pointing to the new page. If this question took many searches, update or create a hub or topic page that collects the members.
5. **Finish**: append `## [YYYY-MM-DD] save | <Title>` with bullets, then `python3 meta/tools/memex.py commit "save: <Title>"`. Reply with the page link in one line.
