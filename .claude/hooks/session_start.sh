#!/bin/sh
# Memex session-start hook (Claude Code SessionStart, Codex SessionStart). Stdout is added to the
# agent's context: hot.md, recent log, inbox, lint age. The logic lives in memex.py so every agent
# (Hermes gets it through `memex hook hermes`) sees the same thing.
V="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
exec python3 "$V/meta/tools/memex.py" context 2>/dev/null
