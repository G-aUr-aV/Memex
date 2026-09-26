#!/bin/bash
# Optional: add qmd (local hybrid BM25 + vector + rerank search, fully on-device) once this vault outgrows
# index + Obsidian search (≈150+ pages per section, or when a search misses a page you know exists).
# Downloads ~2 GB of local models into ~/.cache/qmd. Run it yourself when you decide you need it:
#   bash meta/tools/setup-qmd.sh
set -euo pipefail
V="$(cd "$(dirname "$0")/../.." && pwd)"
SLUG="$(basename "$V" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"
# qmd's native sqlite module must run on the same node that installed it (needs Node >= 22).
NODE="$(command -v node)"; NODE_DIR="$(dirname "$NODE")"; NPM="$NODE_DIR/npm"; QMD="$NODE_DIR/qmd"
"$NODE" -e 'process.exit(parseInt(process.versions.node) >= 22 ? 0 : 1)' || { echo "qmd needs Node >= 22 (found $("$NODE" -v))"; exit 1; }

"$NPM" install -g @tobilu/qmd
"$NODE" "$QMD" doctor
"$NODE" "$QMD" collection add "$V/wiki" --name "$SLUG-wiki"
"$NODE" "$QMD" collection add "$V/raw" --name "$SLUG-raw" --mask "**/*.md"
"$NODE" "$QMD" context add "qmd://$SLUG-wiki" "$(basename "$V"): LLM-maintained wiki pages. Prefer these."
"$NODE" "$QMD" context add "qmd://$SLUG-raw" "$(basename "$V"): immutable raw sources — ground truth for citations."
"$NODE" "$QMD" update
"$NODE" "$QMD" embed

# Register as an MCP server for Claude Code (user scope) with absolute paths:
claude mcp add --scope user qmd -- "$NODE" "$QMD" mcp || true

cat <<EOF
qmd is ready. Next steps:
  1. In CLAUDE.md "Retrieval order", add:  qmd query \$'lex: <keywords>\nvec: <paraphrase>' -c $SLUG-wiki -n 10
  2. Keep the index fresh: add an async Stop hook running \`qmd update && qmd embed\` (see the qmd README).
EOF
