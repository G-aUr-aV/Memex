---
name: prep
description: Prepare the owner for a 1:1, meeting, or conversation about a person or project — context, what happened since last time, open loops both ways, wins to share, asks, and questions — in ≤250 words from Memex. Use when the owner says prep me for…, I'm meeting X, 1:1 with Y, status of project Z before a meeting.
argument-hint: "<person | project | meeting title>"
---
# /prep — meeting prep in ≤250 words

Target: $ARGUMENTS

1. Resolve the target through the section indexes and `memex search $ARGUMENTS` (titles and aliases rank highest). If it's ambiguous, ask.
2. Read the target page and `memex related "<Page>"`. Then read the last 3 meeting sources that mention it, `decisions/` with `status: proposed` involving it, and the open loops on both the target page and the owner's side. For a person, also read the projects you share and the preferences recorded on their page.
3. Output (≤250 words, cite pages):
   - **Context**: their role and goals, or the project goal and current status (as of).
   - **Since last time**: dated bullets.
   - **Open loops**: what the owner owes · what they owe.
   - **Wins to share**: from worklogs and the brag doc.
   - **Asks / feedback to give**
   - **3 questions to ask**
4. Don't write files unless the owner asks. Offer to save the prep to `outputs/Prep <Name> YYYY-MM-DD.md`. After the meeting, suggest `/ingest` for the notes.
