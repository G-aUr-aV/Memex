#!/bin/bash
# Set up this vault on the current machine. Safe to re-run.
#   bash meta/tools/setup.sh              # full setup
#   bash meta/tools/setup.sh --no-capture # skip installing the user-level /memex-capture skill
# What it does (the parts that don't travel with git clone / folder copy):
#   1. makes hooks and tools executable
#   2. git: uses the enclosing repo if there is one, otherwise creates a local repo
#   3. installs the secret-scanning pre-commit hook (only when the vault is the repo root)
#   4. sets a repo-local commit identity for agent commits if none is set
#   5. installs ~/.claude/skills/memex-capture pointing at THIS vault
#   6. regenerates the index and runs a quick lint
#   7. checks Obsidian (registration + command-line interface) and prints what's left to do
set -euo pipefail
V="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="$(basename "$V")"
CAPTURE=1; [ "${1:-}" = "--no-capture" ] && CAPTURE=0
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

# 5. user-level capture skill
if [ "$CAPTURE" = 1 ]; then
  DOMAINS="$(python3 -c "import sys; sys.path.insert(0,'$V/meta/tools'); import memexlib; print(', '.join(memexlib.DOMAINS))")"
  DEST="$HOME/.claude/skills/memex-capture"
  if [ -f "$DEST/SKILL.md" ] && ! grep -qF "$V/inbox" "$DEST/SKILL.md"; then
    say "replacing ~/.claude/skills/memex-capture (it pointed at another vault)"
  fi
  mkdir -p "$DEST"
  sed -e "s|{{VAULT}}|$V|g" -e "s|{{VAULT_NAME}}|$NAME|g" -e "s|{{DOMAINS}}|$DOMAINS|g" \
    "$V/meta/tools/memex-capture.SKILL.md" > "$DEST/SKILL.md"
  say "/memex-capture now writes to $V/inbox"
fi

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
say "4. Open Claude Code on this folder: cd \"$V\" && claude"
