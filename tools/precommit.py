#!/usr/bin/env python3
"""Git pre-commit check for Memex. setup.sh installs it in two repos, and it runs in whichever one is committing:
  vault      refuse likely secrets or card numbers, changes to existing raw/ sources (raw/ is create-only), and
             any commit whose author or committer isn't the vault's identity (.memex/vault.json)
  framework  refuse knowledge folders, vault config, embedded repositories (a vault added by mistake) and
             likely secrets outside tools/ (whose tests and patterns contain fake ones)
Bypass only if you are sure (e.g. redacting a raw file yourself): git commit --no-verify
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import DEFAULT_IDENTITY, FRAMEWORK, KNOWLEDGE_DIRS  # noqa: E402
from secretscan import hits  # noqa: E402

VERB = {"M": "modified", "D": "deleted", "R": "renamed"}
SCANNED = (".md", ".txt", ".canvas", ".base", ".json")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def block(title, items, advice):
    print(f"Memex pre-commit: commit blocked — {title}:\n  " + "\n  ".join(items), file=sys.stderr)
    print(advice, file=sys.stderr)
    sys.exit(1)


def secrets(files):
    found = []
    for f in files:
        if not f.endswith(SCANNED):
            continue
        found += [f"{f}: possible {label}" for label in hits(git("show", f":{f}"))]
    return found


def framework():
    changes = [l.split("\t") for l in git("diff", "--cached", "--raw", "--no-renames").splitlines() if "\t" in l]
    gitlinks = [c[-1] for c in changes if c[0].split()[1] == "160000"]
    if gitlinks:
        block("embedded git repositories (a vault?) staged in the framework", gitlinks,
              "Unstage them (git rm --cached <path>) and add the folder to .git/info/exclude; vaults stay separate.")
    staged = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines()
    knowledge = [f for f in staged if f.split("/", 1)[0] in KNOWLEDGE_DIRS or f.endswith(".memex/vault.json")]
    if knowledge:
        block("knowledge files staged in the framework repo", knowledge,
              "The framework holds no knowledge: unstage them (git rm --cached <file>). Knowledge lives in your vault.")
    found = secrets([f for f in staged if not f.startswith("tools/")])
    if found:
        block("possible secrets", found, "Remove them, or use --no-verify if this is a false positive.")


def ident(var):
    m = re.match(r"^(.*) <(.*)> \d+ [+-]\d{4}$", git("var", var).strip())
    return (m.group(1), m.group(2)) if m else None


def vault(top: Path):
    try:
        cfg = json.loads((top / ".memex" / "vault.json").read_text(encoding="utf-8")).get("identity") or {}
    except Exception:
        cfg = {}
    want = (cfg.get("name") or DEFAULT_IDENTITY["name"], cfg.get("email") or DEFAULT_IDENTITY["email"])
    wrong = [f"{var.split('_')[1].lower()}: {got[0]} <{got[1]}>" if got else f"{var}: unknown"
             for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT") if (got := ident(var)) != want]
    if wrong:
        block(f"this vault only takes commits from {want[0]} <{want[1]}>", wrong,
              "Commit with `memex commit \"<op>: <title>\"`, or fix the vault's identity with `memex doctor --fix`.")
    raw_hits = []
    for line in git("diff", "--cached", "--name-status", "-M").splitlines():
        status, *paths = line.split("\t")
        if status[:1] in "MDR" and paths and paths[0].startswith("raw/"):
            raw_hits.append(f"{paths[0]} ({VERB[status[0]]})")
    if raw_hits:
        block("raw/ sources are immutable", raw_hits,
              "Restore them (git restore --staged --worktree <file>) and annotate the wiki source page instead.")
    found = secrets(git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines())
    if found:
        block("possible secrets", found,
              "Remove them (store a pointer such as '1Password: <item>'), or use --no-verify if this is a false positive.")


if __name__ == "__main__":
    top = Path(git("rev-parse", "--show-toplevel").strip() or ".").resolve()
    framework() if top == FRAMEWORK else vault(top)
