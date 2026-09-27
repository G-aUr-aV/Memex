#!/bin/bash
# Set up this vault on the current machine. Safe to re-run.
#   bash meta/tools/setup.sh                        # vault + every agent found (Claude Code, Codex, Hermes)
#   bash meta/tools/setup.sh --agents claude,codex  # only these agents
#   bash meta/tools/setup.sh --agents none          # vault only, no agent wiring
#   bash meta/tools/setup.sh --remove-agents        # undo the agent wiring
# What it does (the parts that don't travel with git clone / folder copy):
#   1. makes hooks and tools executable
#   2. git: uses the enclosing repo if there is one, otherwise creates a local repo
#   3. installs the pre-commit hook (secret scan + raw/ immutability; only when the vault is the repo root)
#   4. sets a repo-local commit identity for agent commits if none is set
#   5. wires the agents to THIS vault via meta/tools/integrate.py: the `memex` CLI, a global skill and
#      instructions, permissions so they read/write Memex without prompts, and the guard hook
#   6. regenerates the index and runs a quick lint
#   7. checks Obsidian (registration + command-line interface) and prints what's left to do
set -euo pipefail
V="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="$(basename "$V")"
AGENTS=auto
case "${1:-}" in
  --agents) AGENTS="${2:?--agents needs a value: auto, all, none or e.g. claude,codex}" ;;
  --agents=*) AGENTS="${1#--agents=}" ;;
  --no-capture) AGENTS=none ;;
  --remove-agents) python3 "$V/meta/tools/integrate.py" --remove; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (see the top of $0)"; exit 1 ;;
esac
say() { printf '  %s\n' "$*"; }
echo "Setting up vault '$NAME' at $V"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
command -v git >/dev/null || { echo "git is required"; exit 1; }

# 1. permissions
chmod +x "$V"/.claude/hooks/*.sh "$V"/.claude/hooks/*.py "$V"/meta/tools/*.py "$V"/meta/tools/*.sh 2>/dev/null || true
say "hooks and tools are executable"

# 2-4. git
if TOP="$(git -C "$V" rev-parse --show-toplevel 2>/dev/null)"; then
  say "git repo found: $TOP"
else
  git -C "$V" init -q; TOP="$V"; say "created a local git repo (no remote)"
fi
if [ "$TOP" = "$V" ]; then
  HOOK="$(git -C "$V" rev-parse --git-path hooks)/pre-commit"
  case "$HOOK" in /*) ;; *) HOOK="$V/$HOOK" ;; esac
  mkdir -p "$(dirname "$HOOK")"
  if [ -f "$HOOK" ] && ! grep -q "meta/tools/precommit.py" "$HOOK"; then
    say "WARNING: an existing pre-commit hook was left alone; add this line to it: python3 \"$V/meta/tools/precommit.py\""
  else
    printf '#!/bin/sh\nexec python3 "$(git rev-parse --show-toplevel)/meta/tools/precommit.py"\n' > "$HOOK"
    chmod +x "$HOOK"; say "secret-scanning pre-commit hook installed"
  fi
  if [ -z "$(git -C "$V" config --local user.name 2>/dev/null)" ] && [ -z "$(git -C "$V" config --global user.name 2>/dev/null)" ]; then
    git -C "$V" config user.name "Memex Agent"; git -C "$V" config user.email "memex-agent@localhost"
    say "repo-local commit identity set (Memex Agent)"
  fi
else
  say "vault sits inside a larger repo ($TOP): pre-commit hook NOT installed there automatically."
  say "to add the secret scan, append to $TOP/.git/hooks/pre-commit:  python3 \"$V/meta/tools/precommit.py\""
fi
if [ -n "$(git -C "$V" remote 2>/dev/null)" ]; then
  say "remotes: $(git -C "$V" remote | tr '\n' ' ') — push and pull are yours; the agent never pushes"
fi

# 5. agents (Claude Code, Codex, Hermes)
echo "Agent wiring ($AGENTS):"
python3 "$V/meta/tools/integrate.py" --agents "$AGENTS"

# 6. index + lint
python3 "$V/meta/tools/build_index.py" | sed 's/^/  /'
python3 "$V/meta/tools/lint.py" --quick | grep -E '^## Errors|^- ' | head -5 | sed 's/^/  /' || true

# 7. Obsidian
OBS_JSON="$HOME/Library/Application Support/obsidian/obsidian.json"
[ -f "$OBS_JSON" ] || OBS_JSON="$HOME/.config/obsidian/obsidian.json"
echo
echo "Remaining steps:"
if [ -f "$OBS_JSON" ] && grep -qF "\"$V\"" "$OBS_JSON"; then
  say "✓ Obsidian already knows this vault"
else
  say "1. Obsidian → Open folder as vault → $V"
fi
if [ -f "$OBS_JSON" ] && grep -q '"cli":true' "$OBS_JSON"; then
  say "✓ Obsidian command-line interface is on"
else
  say "2. Obsidian → Settings → General → Command line interface: ON (the agent uses it for search and backlinks)"
fi
say "3. Web Clipper: import meta/clipper/Memex Inbox.json and set its vault to '$NAME'"
say "4. Open an agent on this folder: cd \"$V\" && claude   (or codex, or hermes)"
say "   From any other project, agents use Memex through the memex skill and CLI (memex --help)."
