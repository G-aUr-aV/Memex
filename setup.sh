#!/bin/bash
# Install Memex on this machine. Safe to re-run.
#   bash setup.sh                          # asks where the vault lives (default: ./MemexVault, in the folder you run this from)
#   bash setup.sh --vault PATH             # use or create the vault at PATH, no prompt
#   bash setup.sh --agents claude,codex    # only wire these agents (auto, all, none, or a list)
#   bash setup.sh --remote URL             # sync the vault with your PRIVATE git repo (on a new machine: join it)
#                 [--unverified]           #   …when its privacy can't be checked (your own server)
#   bash setup.sh --remove-agents          # undo the agent wiring
#   bash setup.sh --uninstall [--delete-vault]   # remove Memex from this machine (see memex uninstall)
# The framework (this folder) holds code only and is safe to push. The vault is a separate folder with its
# own git repo: every commit as "Memex Agent <memex-agent@localhost>", and no remote unless you attach one
# private remote (--remote, or later `memex remote set <url>`).
# What it does:
#   1. creates the vault from seed/, or reuses the configured one, and renders the framework's files into it
#   2. hardens the vault's git repo (local identity, no signing, pre-commit and pre-push hooks)
#   3. with --remote: attaches the private remote, or adopts its history if this vault is brand new
#   4. records the vault in ~/.config/memex/config.json
#   5. installs the framework's own pre-commit hook (refuses knowledge, vaults and secrets)
#   6. wires Claude Code, Codex and Hermes to the vault (tools/integrate.py)
#   7. lints the vault and prints what's left to do
set -euo pipefail
START_DIR="$PWD"
FW="$(cd "$(dirname "$0")" && pwd)"
VAULT=""
AGENTS=auto
REMOTE=""
UNVERIFIED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="${2:?--vault needs a path}"; shift 2 ;;
    --vault=*) VAULT="${1#--vault=}"; shift ;;
    --agents) AGENTS="${2:?--agents needs a value: auto, all, none or e.g. claude,codex}"; shift 2 ;;
    --agents=*) AGENTS="${1#--agents=}"; shift ;;
    --remote) REMOTE="${2:?--remote needs the URL of a private git repository}"; shift 2 ;;
    --remote=*) REMOTE="${1#--remote=}"; shift ;;
    --unverified) UNVERIFIED=1; shift ;;
    --remove-agents) exec python3 "$FW/tools/integrate.py" --remove ;;
    --uninstall) shift; exec python3 "$FW/tools/vault.py" uninstall "$@" ;;
    *) echo "unknown option: $1 (see the top of $0)"; exit 1 ;;
  esac
done
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
command -v git >/dev/null || { echo "git is required"; exit 1; }
exec python3 "$FW/tools/vault.py" setup --default "$START_DIR/MemexVault" --agents "$AGENTS" \
  ${VAULT:+--vault "$VAULT"} ${REMOTE:+--remote "$REMOTE"} ${UNVERIFIED:+--unverified}
