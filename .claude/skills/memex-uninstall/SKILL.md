---
name: memex-uninstall
description: Remove Memex from this machine cleanly. That covers every agent instruction, hook, permission and skill it installed, the memex CLI and its config, and the vault: either delete it (after a verified backup) or keep it as plain Markdown + git. The agent checks, backs up and verifies; the owner runs the single removal command, because the guard never lets an agent delete or move a vault. Use when the owner says uninstall / remove Memex, delete the vault, start over, or clean up Memex.
argument-hint: "[keep | delete]"
allowed-tools: Bash(memex path), Bash(memex remote), Bash(memex doctor), Bash(memex push), Bash(memex backup*), Bash(memex commit *), Bash(python3 tools/vault.py leftovers*), Bash(git status *)
---
# /memex-uninstall: remove Memex from this machine

Arguments: $ARGUMENTS (`keep` or `delete` the vault; ask if not given)

Run from the framework root, the folder that holds `setup.sh`. You prepare and verify; **the owner runs the removal command**. The guard blocks agents from it, so a misled agent can never delete a vault. Don't try to work around that.

## 1. See what's installed
- `memex path` (the vault), `memex remote` (synced with a private remote?) and `memex doctor`. If `memex` isn't found, read `~/.config/memex/config.json`. The vault folder may already be gone; the uninstall handles that.
- `python3 tools/vault.py leftovers` lists every place Memex is wired in: agent instructions, hooks, permissions, skills, the CLI and config.

## 2. Make sure nothing is lost
1. Uncommitted work in the vault: `memex commit "uninstall: final snapshot"`.
2. With a remote: `memex push`, then check that `memex remote` shows `Ahead 0`.
3. `memex backup`, and note the bundle path. The uninstall writes one more bundle itself before it deletes anything.

## 3. Ask which way, then hand over one command
Unless the owner already said, ask: keep the vault or delete it? Then give them exactly one command to run in their own terminal:
- **Keep the vault** (stop using Memex, keep the knowledge as plain Markdown + git): `memex uninstall`
- **Delete the vault too**: `memex uninstall --delete-vault --confirm "<vault name>"` (the `name` in `.memex/vault.json`)

`bash setup.sh --uninstall` (plus `--delete-vault`) does the same if `memex` isn't on PATH.

Say in two lines what it does:
- commits anything pending, pushes, and writes a verified backup bundle;
- removes the agent blocks, hooks, permissions and skills, the CLI, `~/.config/memex`, and the framework's hooks;
- then deletes the vault, or strips Memex's files and hooks from it.

## 4. Verify, after they ran it
- `python3 tools/vault.py leftovers --vault "<vault path>"` must print ✓. If it lists anything, report it, and offer to remove those exact Memex blocks or files (nothing else).
- **Report in ≤8 lines:**
  - what was removed;
  - the backup bundle and how to restore it (`git clone "<bundle>" <folder>`).
- **What only the owner can do:**
  - remove the vault from Obsidian's vault list;
  - optionally delete Claude Code's own data for the vault in `~/.claude/projects/` (its sessions and memory);
  - delete the private remote repository on its host, if they want it gone;
  - delete this framework folder, or keep it to set Memex up again with `bash setup.sh`.

## Never
- Run `memex uninstall`, or delete or move the vault yourself (with `rm`, scripts or anything else).
- Delete backup bundles or the remote repository.
- Edit Obsidian's or Claude Code's internal files.
