---
name: memex
description: Read the owner's Memex knowledge base ({{VAULT}}) and add to it from any project, without asking first. Look things up before answering about the owner's projects, people, decisions or past work; capture durable insights (root causes, decisions, gotchas, working commands, stated preferences); queue updates or removals of existing pages. Use when the owner says capture this, save/remember this, add to my wiki or knowledge base, what do I know about, or check Memex, and on your own initiative whenever one of those moments comes up.
---
# memex: use {{VAULT_NAME}} from anywhere

`memex` is pre-approved in Claude Code, Codex and Hermes. Run it yourself; don't ask the owner first. From here you read the vault and add to its inbox; a session in the vault does the rest. If it isn't on PATH, use `python3 "{{FRAMEWORK}}/tools/memex.py"` instead.

## Read
- `memex search <terms>` searches wiki pages by title, alias and text. Add `--in all` to include raw sources, the journal and notes.
- `memex read "<Page>"` prints a page (name, `[[link]]`, alias or path). Search shows each page's length: for pages over 150 lines, `memex outline "<Page>"` lists the headings and `memex read "<Page>#<Heading>"` prints one section. `memex related "<Page>"` shows what links in and out.
- When a session starts in a repo that has a project or system page (`repo:` in its frontmatter), you get that page's summary, open loops and decisions automatically.
- `memex context` shows the current focus, open loops, recent log and inbox.
- Cite what you use as `[[Page]]`. If Memex has nothing, say so and continue.

## Create (the default way to add knowledge)
Capture when this session produced something worth keeping: a root cause, a decision and its reason, a gotcha, a command or procedure that worked, a preference or fact the owner stated.

```bash
memex capture --title "Flaky Test Was a Timezone Bug" --domain {{FIRST_DOMAIN}} --why "cost 2 hours; will recur" <<'EOF_NOTE'
repo: <name>. Symptom: … Cause: … Fix: … Didn't work: …
EOF_NOTE
```
- `--domain` is one of: {{DOMAINS}}. `--project` defaults to the current folder's name.
- Write 3-20 plain lines with minimal context. Keep code blocks short.
- Never include secrets, tokens, credentials, personal IDs, other people's personal data or URLs with tokens. The command refuses obvious ones.
- End your reply with one line naming the captured file. Captures are compiled into wiki pages at the vault's next `/inbox` run.

## Update and delete
- **Queue it:** `memex capture --action update|supersede|delete --target "<Page>" --title "…" --why "…"`, with the new facts or the reason on stdin. That covers a wrong fact the owner just corrected, a status that changed, a page that should go. The vault's next inbox run applies it with citations, a Timeline line and history.
- **The inbox is the only way in from another project.** Don't edit, move or delete vault files yourself, with file tools, shell commands, `memex rm` or `memex file`. The guard refuses them outside the vault, because only a session in the vault works under its full rules.
- Never add a git remote to the vault or push it. `memex remote set|remove`, `memex move` and `memex uninstall` are the owner's (the guard blocks them).
