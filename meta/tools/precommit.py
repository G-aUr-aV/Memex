#!/usr/bin/env python3
"""Git pre-commit guard for Memex: block commits that contain likely secrets or card numbers.
Installed as .git/hooks/pre-commit. Bypass (only if you are sure it's a false positive): git commit --no-verify
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint import SECRETS, CARD, luhn  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
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
