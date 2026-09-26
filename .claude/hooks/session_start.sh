#!/bin/bash
# Memex SessionStart hook: stdout is injected into Claude's context. Keep it short and fast.
V="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$V" 2>/dev/null || exit 0

# Indexes are generated: rebuild quietly so they are fresh after a git pull/merge.
python3 meta/tools/build_index.py >/dev/null 2>&1

echo "# Memex context — $(date '+%F %a %H:%M')"
echo
echo "## wiki/hot.md"
awk 'NR==1 && /^---$/ {fm=1; next} fm && /^---$/ {fm=0; next} !fm' wiki/hot.md 2>/dev/null | head -40
echo
echo "## Recent log"
grep '^## \[' wiki/log.md 2>/dev/null | tail -8
echo
n=$(find inbox -type f ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
echo "## Inbox: $n item(s)"
ls -1t inbox 2>/dev/null | grep -v '^\.' | head -8
echo
today="journal/daily/$(date +%F).md"
[ -f "$today" ] && echo "Today's daily note: exists ($today)" || echo "Today's daily note: not created yet (suggest /today)"

last=$(grep -E '^## \[[0-9-]+\] lint' wiki/log.md 2>/dev/null | tail -1 | sed -E 's/^## \[([0-9-]+)\].*/\1/')
if [ -z "$last" ]; then
  echo "Lint: never run"
else
  days=$(( ( $(date +%s) - $(date -j -f %F "$last" +%s 2>/dev/null || date +%s) ) / 86400 ))
  [ "$days" -gt 7 ] && echo "Lint: last run $days days ago (overdue, suggest /lint)" || echo "Lint: last run $last"
fi
pgrep -x Obsidian >/dev/null 2>&1 || echo "Obsidian is not running: the obsidian CLI will launch it; use Grep for searches until it's up."
exit 0
