# Security policy

## Reporting a vulnerability
Please report security problems **privately** through this repository's **Security → Report a vulnerability** page (GitHub private vulnerability reporting). Don't open a public issue. Include steps to reproduce and the agent involved (Claude Code, Codex or Hermes).

## What's in scope
- **Guard bypasses**: a way for an agent to modify `raw/`, write `notes/`, change the journal outside its brief, alter `> [!mine]` blocks, or delete vault files without `memex rm`, through any agent's tools. Note that the shell-command checks are documented as best-effort; the pre-commit hook is the backstop.
- **Pre-commit bypasses**: secrets or `raw/` changes reaching a commit without `--no-verify`.
- **Prompt injection**: a crafted source (clip, PDF, capture) that makes the documented skills take actions outside the ownership table.
- **Setup and integration**: `integrate.py` corrupting agent configs, granting broader permissions than documented, or writing outside the files it lists.
- **Leaks**: tools that send vault content anywhere (they shouldn't; everything is local).

## Security model in brief
Memex is local-first. Tools use only the Python standard library, never make network calls (except optional `defuddle` and read-only `gh`), and never push. Agents act without prompts *inside* the vault's ownership rules. Those rules are enforced by `.claude/hooks/guard.py` for all three agents, by `meta/tools/precommit.py` at commit time, and by git history. The content you put in the vault is sent to whichever model provider your agent uses, so follow the schema's *Never store* list.
