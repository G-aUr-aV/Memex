# Engineering domain: the owner's own projects, open source, tools, career

**Generalizable knowledge has one canonical page, in `wiki/learning/`** (e.g. `Idempotency Keys`). Engineering pages (the decision, incident, system or project) hold the project-specific application and link to it.

**What gets its own page** (prefer fewer, richer pages)
- **Project** (`projects/`): side projects, OSS contributions, experiments. Include goal, status (as of), milestones, decisions, risks, tasks and repo link, and set `repo:` (the git remote `owner/name`, or the folder name) so agents working in that repo get this page at session start. Create one only when the project is named or the owner confirms it.
- **System** (`systems/`): a repo, service, library or piece of infra you build or run. Include purpose, interfaces, dependencies, key flows, gotchas and the repo path or URL. Set `verified_at_commit` and `verified_on` only when the claims were checked against the code; otherwise leave them empty (lint reports "never verified"). Repo-specific coding rules belong in the repo's own CLAUDE.md. Graphify reports saved under `raw/engineering/` may seed these pages.
- **Person** (`people/`): collaborators, maintainers and mentors who own an action or decision or recur across 2+ sources. Record only professional facts they stated explicitly.

**Planning notes, design discussions, meetings**: write the source page `sources/<Title> YYYY-MM-DD.md` with the key points, decisions and action items. Then run a decision pass: `decisions/NNNN <Title>.md` in Nygard ADR form (Context / Decision / Consequences / Evidence with ≥1 verbatim quote / Alternatives). Use the next free 4-digit id. Statuses: `proposed` (includes deferred proposals, plus uncertain or implicit decisions; set `confidence: low` and add a question for the owner), `accepted`, `rejected`, `superseded`, `deprecated`. A reversal sets `superseded_by` / `supersedes`. If the repo keeps an official ADR, link it in `external_adr:`. The owner's action items become `- [ ]` tasks on the project page; other people's go to their `## Open loops`.

**Incidents** (`incidents/<Title> YYYY-MM-DD.md`): outages or bugs in things you run. Include summary, incident timeline, impact, root cause, what went well or badly, action items and similar incidents. Update the matching runbook in `playbooks/`; create a new one only from steps that demonstrably worked.

**Playbooks**: only procedures that actually worked (setup guides, release steps, debugging recipes). Link the source episode, record failures seen, and set `verified:` to the date it last worked.

**Career** (`career/`): `Worklog YYYY-Www.md`, `Brag Doc YYYY.md` (Julia Evans sections), `PR Review Lessons.md` (recurring review feedback turned into a pre-PR checklist), interview prep and STAR stories.

**Concepts** (`concepts/`): terms specific to your own projects. General tech concepts belong in `wiki/learning/concepts/`.
