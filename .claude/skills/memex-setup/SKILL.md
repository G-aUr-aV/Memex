---
name: memex-setup
description: Install Memex on this machine end to end, with no manual steps. Checks prerequisites, creates or reuses the local-only vault (default MemexVault in the current folder), hardens its git repo, wires Claude Code, Codex and Hermes, puts `memex` on PATH, verifies everything with doctor, lint and the test suite, and reports the few Obsidian steps only a person can click. Use when the owner says set up / install / configure Memex, run setup, get Memex working, or has just cloned this repo.
argument-hint: "[vault path] [--agents claude,codex,hermes]"
allowed-tools: Bash(bash setup.sh *), Bash(memex *), Bash(python3 --version), Bash(python3 tools/test_memex.py), Bash(git --version), Bash(git status *)
---
# /memex-setup — set up Memex on this machine

Arguments: $ARGUMENTS (optional: a vault path, and `--agents` with a comma-separated list)

Run everything from the **framework root**, the folder that holds `setup.sh`. Do every step yourself without asking. Stop only for a failure you can't fix, and then say exactly what the owner must do. `setup.sh` is idempotent, so re-running it after a fix is always safe.

## 1. Preflight
- `python3 --version` must be 3.9 or newer, and `git --version` must work. If either is missing, stop: give the install command (macOS: `xcode-select --install` or `brew install python git`; Debian/Ubuntu: `sudo apt install python3 git`) and don't try to install system packages yourself.
- `git status --porcelain` must not list `wiki/ raw/ inbox/ journal/ notes/ outputs/ .memex/` or a vault folder. If it does, stop: knowledge must never be committed to the framework.
- If `~/.config/memex/config.json` exists, read its `"vault"`: this machine is already set up, and step 3 will reuse and repair it.

## 2. Choose the vault folder
Use the first of these that applies:
1. a path the owner gave (in `$ARGUMENTS` or the conversation);
2. the vault already recorded in `~/.config/memex/config.json`;
3. the default, `"$PWD/MemexVault"` (the framework root, where setup is run). The framework git-ignores it.

If the chosen path exists but isn't a vault (no `.memex/vault.json`) and isn't empty, don't touch it. Use `MemexVault` next to the framework folder instead, and if that's taken too, stop and ask. Never choose a folder inside another git repository.

## 3. Run setup (non-interactive)
```bash
bash setup.sh --vault "<vault path>"                 # add --agents claude,codex if the owner named agents
```
With no terminal attached it never prompts. It:
- creates the vault's folders and seed files;
- renders the schema, skills, rules and agent settings into it;
- makes the vault a local-only git repo (Memex Agent identity, pre-commit and pre-push hooks);
- records the vault in `~/.config/memex/config.json`;
- installs the framework's own pre-commit hook;
- wires every agent it finds.

If it fails, read the error. It names the fix (for example, "inside another git repository" means pick another path). Apply the fix and run it again.

## 4. Put `memex` on PATH
If `command -v memex` finds nothing, add `export PATH="$HOME/.local/bin:$PATH"  # Memex` to the login shell's profile: `~/.zshrc` for zsh, `~/.bash_profile` (macOS) or `~/.bashrc` (Linux) for bash. Add it only if a `.local/bin` PATH line isn't already there, and mention the change in the report. For the rest of this run, call `"$HOME/.local/bin/memex"`.

## 5. Verify: all must pass
1. `memex doctor`: every line ✓. On any ✗, run `memex doctor --fix` once and re-check. It covers the identity, signing, hooks, the missing remote, the commit history, managed files, and the framework tracking no knowledge.
2. `memex context`: the first lines name the vault.
3. `memex lint --quick`: 0 errors.
4. `python3 tools/test_memex.py`: `OK` (about 15 s; it runs in a temporary folder with a fake HOME and never touches the real vault). Skip it only if the owner asked for speed.
5. `git status --porcelain` in the framework: still no vault or knowledge files.

## 6. Report (≤ 12 lines)
- the vault path, and whether it was created or reused;
- the commit identity, and that the vault has no remote and refuses pushes;
- the agents wired, and any PATH change;
- the verification results.

Then list only what a person has to do, in this order. Skip any that are already done (setup prints ✓ for them):
1. Obsidian → *Open folder as vault* → the vault path. Remove the framework folder from Obsidian's vault list if it's there.
2. Obsidian → Settings → General → *Command line interface*: on.
3. Optional: the Web Clipper extension → import `clipper/Memex Inbox.json` and set its vault.
4. Hermes only: start it once as `hermes --accept-hooks`.

End with how to start: `cd "<vault>" && claude` (or `codex`, or `hermes`), then clip Karpathy's LLM Wiki gist and run `/ingest` on it.

## Never
- push, or add a git remote to the vault;
- create, edit or commit knowledge in the framework repo;
- edit agent configs by hand; `setup.sh` merges them and `bash setup.sh --remove-agents` undoes them;
- move, rename or delete an existing vault. If the owner wants a different location, tell them to move the folder themselves and re-run `bash setup.sh --vault <new path>`.
