---
name: ask
description: Answer a question from Memex with citations — read-only. Searches the wiki (then raw/ and journal/ if needed), cites pages and sources, states gaps and confidence, and offers to /save the answer. Use for "what do I know about…", "what did we decide about…", comparisons, summaries, "when did I…", or any question about the owner's work, learning or life.
argument-hint: "<question>"
---
# /ask — read-only, cited

Question: $ARGUMENTS

1. **Orient**: `wiki/hot.md` (in context), `wiki/index.md`, the relevant section index. Pick candidate pages.
2. **Search**: `memex search <key terms>` (ranked; `--in all` adds raw, journal and notes), trying 2-3 phrasings and synonyms. `obsidian search:context query="…" path=wiki` is an alternative when Obsidian is running. Search `notes/` and `journal/` when the question is about the owner's own views or history.
3. **Read** the best pages with `memex read "<Page>"`. For a page over 150 lines (search shows the length), run `memex outline "<Page>"` first and read only the sections you need with `memex read "<Page>#<Heading>"`. Use `memex related "<Page>"` when you need its neighbours. For exact numbers, dates or quotes, re-open the cited raw section.
4. **Answer**:
   - Lead with the direct answer, then the supporting points. Each point cites `[[Wiki Page]]` and, for facts, the raw source `([[raw basename#Heading|src]])`.
   - Label points from the owner's own writing **[mine]** and points from the wiki **[wiki]**.
   - Pick the format the question calls for: prose, a comparison table, a timeline, a checklist, or a Marp deck / Mermaid diagram / canvas if asked.
   - End with **Gaps & confidence**: what Memex doesn't know, any stale `(as of)` facts, and sources worth adding.
   - If the answer isn't in Memex, say so plainly after searching index and full text. Answer from general knowledge only if the owner wants that, and label it as such.
5. **High stakes** (a decision, something the owner will send to others, health or finance): before answering, have the `fact-checker` subagent verify each claim against its cited source, and drop or flag unsupported ones.
6. **Answering doesn't write files.** If the answer is reusable (a comparison, analysis, decision rationale, or an answer that took more than 3 searches), file it yourself with the `/save` procedure and end with the new page's link. Don't save one-line lookups or answers that are mostly general knowledge.
