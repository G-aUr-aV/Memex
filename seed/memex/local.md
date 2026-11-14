---
type: vault-rules
---
# Vault-specific rules

<!--
Rules for THIS vault only, such as confidentiality rules for a work vault, or a rule you want to relax or
tighten here but not on your other machine. Everything below this comment is copied into AGENTS.md under
"Vault-specific rules", where it takes precedence over the framework's rules. Changes apply at the next
`memex sync` (a new agent session runs it for you). Rules for every vault belong in the framework instead.

Vault-only domains: list them in .memex/vault.json ("domains": ["work", "learning"]) and define them in
.memex/domains.json, e.g. {"domains": {"work": {"extends": "engineering", "title": "Work Index",
"summary": "employer projects, systems and people"}}}. Extra rules for a domain go in .memex/domains/<name>.md.

Commands upkeep may run: /verify never builds or runs tests in your code repos. To let it refresh a fact that
needs a run (a repo's known test failures, say), add a section "## Commands upkeep may run" with one line per
command: the folder, the exact command, and the page it keeps true. Only list commands that change nothing.
-->
