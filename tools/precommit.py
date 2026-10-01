#!/usr/bin/env python3
"""Git hooks for Memex. setup.sh installs the pre-commit check in two repos; it runs in whichever is committing:
  vault      refuse likely secrets or card numbers, leftover conflict markers, changes to existing raw/ sources
             (raw/ is create-only), and any commit whose author or committer isn't the vault's identity
  framework  refuse knowledge folders, vault config, embedded repositories (a vault added by mistake) and
             likely secrets outside tools/ (whose tests and patterns contain fake ones)
With --pre-push (the vault's pre-push hook): refuse every push unless the owner attached a private remote
(.memex/remote.json), and then allow only `memex push` (MEMEX_PUSH=1) to that URL, with no likely secrets in
the outgoing changes.
Bypass only if you are sure (e.g. redacting a raw file yourself): git commit --no-verify
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import DEFAULT_IDENTITY, FRAMEWORK, KNOWLEDGE_DIRS  # noqa: E402
from secretscan import hits  # noqa: E402

VERB = {"M": "modified", "D": "deleted", "R": "renamed"}
CONFLICT = re.compile(r"(?m)^(<{7} |>{7} )")
SCANNED = (".md", ".txt", ".canvas", ".base", ".json")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def block(title, items, advice, what="pre-commit: commit"):
    print(f"Memex {what} blocked — {title}:\n  " + "\n  ".join(items), file=sys.stderr)
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
    staged = git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines()
    markers = [f for f in staged if f.endswith(SCANNED) and CONFLICT.search(git("show", f":{f}"))]
    if markers:
        block("unresolved merge conflicts", markers,
              "Edit each file: keep both sides' facts, remove the <<<<<<< / ======= / >>>>>>> lines, then commit again.")
    found = secrets(staged)
    if found:
        block("possible secrets", found,
              "Remove them (store a pointer such as '1Password: <item>'), or use --no-verify if this is a false positive.")


ZERO = "0" * 40


def pre_push(top: Path, url: str):
    import remote
    cfg = remote.settings(top)
    if not cfg:
        block("this vault is local-only, so pushing is disabled", [url or "(no URL)"],
              "Its history stays on this machine. The owner can attach a private remote with `memex remote set <url>`.",
              "pre-push: push")
    if not remote.same_repo(url, cfg["url"]):
        block(f"the vault pushes only to the private remote the owner attached ({cfg['url']})", [url],
              "Nothing was pushed.", "pre-push: push")
    if os.environ.get("MEMEX_PUSH") != "1":
        block("push the vault with `memex push` (or let `memex commit` do it)", [url],
              "It checks the remote is still private and syncs first.", "pre-push: push")
    added = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        local_sha = parts[1]
        if local_sha == ZERO:
            block("deleting branches on the remote isn't allowed", [parts[2]], "Nothing was pushed.", "pre-push: push")
        log = git("log", "-p", "--no-color", "--format=", local_sha, "--not", "--remotes=origin")
        added += [l[1:] for l in log.splitlines() if l.startswith("+") and not l.startswith("+++")]
    found = hits("\n".join(added))
    if found:
        block("possible secrets in the commits being pushed", [f"possible {f}" for f in found],
              "Remove them in a new commit (store a pointer such as '1Password: <item>'), then memex push. Nothing was "
              "pushed.", "pre-push: push")


if __name__ == "__main__":
    top = Path(git("rev-parse", "--show-toplevel").strip() or ".").resolve()
    if sys.argv[1:2] == ["--pre-push"]:
        pre_push(top, sys.argv[3] if len(sys.argv) > 3 else "")
    else:
        framework() if top == FRAMEWORK else vault(top)
