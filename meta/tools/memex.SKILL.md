---
name: memex
description: Read and write the owner's Memex knowledge base ({{VAULT}}) from any project, without asking first. Look things up before answering about the owner's projects, people, decisions or past work; capture durable insights (root causes, decisions, gotchas, working commands, stated preferences); queue updates or removals of existing pages. Use when the owner says capture this, save/remember this, add to my wiki or knowledge base, what do I know about, or check Memex, and on your own initiative whenever one of those moments comes up.
---
# memex: use {{VAULT_NAME}} from anywhere

`memex` is pre-approved in Claude Code, Codex and Hermes. Run it yourself; don't ask the owner first. If it isn't on PATH, use `python3 "{{VAULT}}/meta/tools/memex.py"` instead.

## Read
- `memex search <terms>` searches wiki pages by title, alias and text. Add `--in all` to include raw sources, the journal and notes.
- `memex read "<Page>"` prints a page. It takes a page name, `[[link]]` or vault path.
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
- **Queue it (preferred):** `memex capture --action update|supersede|delete --target "<Page>" --title "…" --why "…"`, with the new facts or the reason on stdin. The inbox run applies it with citations and history.
- **Small direct fix** (a wrong fact the owner just corrected, a status that changed, a broken link): read `{{VAULT}}/AGENTS.md` first and follow it. Edit the page surgically, add a Timeline line, append a `wiki/log.md` entry, then run `memex commit "edit: <what>"`. `memex commit` rebuilds the indexes, runs lint and commits only the vault.
- **Remove a file:** `memex rm "<vault path>"` moves a wiki, inbox or outputs file to `.trash/` after a backlink check, and git keeps it. Then log the removal and run `memex commit "delete: <what>"`. Superseding a page is usually better than deleting it.
- The guard blocks writes to `raw/` (immutable), `notes/` and `journal/` (the owner's own) and edits to `> [!mine]` blocks. Don't try to work around it.
- Never push the vault's git repo.
