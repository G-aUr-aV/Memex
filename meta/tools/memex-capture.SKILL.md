---
name: memex-capture
description: Save a learning, decision, debugging insight, snippet or idea from the CURRENT session (in any repo) into the owner's {{VAULT_NAME}} inbox ({{VAULT}}/inbox) for later ingest. Use when the owner says capture this, save to Memex, remember this in my wiki, add to my knowledge base.
argument-hint: "[short title]"
---
# /memex-capture — drop an insight into {{VAULT_NAME}}

Write exactly ONE new file: `{{VAULT}}/inbox/<YYYY-MM-DD> <Title>.md`. Use today's date and a short Title Case title from `$ARGUMENTS`, or infer one. Never overwrite an existing file.

```
---
title: <Title>
source: claude-code session
project: <basename of the current working directory>
captured: <YYYY-MM-DD>
source_type: capture
domain: <one of: {{DOMAINS}}>
why: <one line: why this is worth keeping>
tags: [capture]
---
<3-20 lines: the insight itself, stated plainly, with minimal context: repo/service, the problem, what worked, what didn't, commands or links. Put code in fenced blocks and keep it short.>
```

Rules: never include secrets, tokens, credentials, personal IDs, other people's personal data, or URLs containing tokens. Don't ingest now: {{VAULT_NAME}}'s `/inbox` or `/ingest` will compile it later. Reply with the file path only.
