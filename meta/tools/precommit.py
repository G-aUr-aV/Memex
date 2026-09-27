#!/usr/bin/env python3
"""Git pre-commit guard for Memex, the backstop for every agent and editor:
  - block commits that contain likely secrets or card numbers
  - block commits that modify, rename or delete an existing raw/ source (raw/ is create-only)
Installed as .git/hooks/pre-commit. Bypass (only if you are sure, e.g. redacting a raw file yourself):
git commit --no-verify
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint import SECRETS, CARD, luhn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
changed = subprocess.run(["git", "diff", "--cached", "--name-status", "-M", "--", "."],
                         cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
VERB = {"M": "modified", "D": "deleted", "R": "renamed"}
raw_hits = []
for line in changed:
    status, *paths = line.split("\t")
    if status[:1] in "MDR" and paths and paths[0].startswith(prefix + "raw/"):
        raw_hits.append(f"{paths[0][len(prefix):]} ({VERB[status[0]]})")
if raw_hits:
    print("Memex pre-commit: commit blocked — raw/ sources are immutable:\n  " + "\n  ".join(raw_hits), file=sys.stderr)
    print("Restore them (git restore --staged --worktree <file>) and annotate the wiki source page instead.", file=sys.stderr)
    sys.exit(1)
files = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                       cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
hits = []
for f in files:
    if not f.endswith((".md", ".txt", ".canvas", ".base", ".json")) or "meta/tools/" in f:
        continue
    blob = subprocess.run(["git", "show", f":{f}"], cwd=ROOT, capture_output=True, text=True).stdout
    for label, rx in SECRETS:
        if rx.search(blob):
            hits.append(f"{f}: possible {label}")
    if any(luhn(m.group(0)) for m in CARD.finditer(blob)):
        hits.append(f"{f}: possible payment card number")
if hits:
    print("Memex pre-commit: commit blocked — possible secrets:\n  " + "\n  ".join(hits), file=sys.stderr)
    print("Remove them (store a pointer such as '1Password: <item>'), or use --no-verify if this is a false positive.", file=sys.stderr)
    sys.exit(1)
