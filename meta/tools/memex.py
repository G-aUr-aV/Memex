#!/usr/bin/env python3
"""memex: one agent-neutral entry point to this vault, for Claude Code, Codex, Hermes or a plain shell.

  memex path                                  print the vault path
  memex context [--brief]                     what a session starts with: hot.md, recent log, inbox, lint age
  memex search <terms…> [--in wiki|raw|journal|notes|all] [--limit N]
  memex read <page name | [[link]] | vault path>
  memex capture --title T [--domain D] [--why W] [--project P] [--source S]
                [--action create|update|supersede|delete --target PAGE]    (body on stdin)
  memex rm <vault path> [--force]             move a wiki/inbox/outputs file to .trash/ (git keeps history)
  memex commit "<op>: <title>" [--force]      rebuild indexes, lint --quick, commit the vault (never pushes)
  memex hook hermes                           Hermes pre_llm_call hook: adds context on a session's first turn

setup.sh installs this as ~/.local/bin/memex. Otherwise run: python3 <vault>/meta/tools/memex.py …
It writes only inside the vault: new files in inbox/, trash moves out of wiki/inbox/outputs, git commits.
It never edits raw/, notes/ or journal/, and never pushes.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import DOMAINS, GENERATED, LINK, ROOT, SPECIAL, build_resolver, frontmatter, iter_files, rel  # noqa: E402

TOOLS = ROOT / "meta" / "tools"
NOW = datetime.datetime.now()
TODAY = NOW.date()
SCOPES = {"wiki": ["wiki"], "raw": ["raw"], "journal": ["journal"], "notes": ["notes"],
          "all": ["wiki", "raw", "journal", "notes", "inbox", "outputs"]}
RM_ALLOWED = {"wiki", "inbox", "outputs"}
BAD_NAME = re.compile(r"[:\\/#^\[\]|*?\"<>]")


def fail(msg, code=1):
    print(f"memex: {msg}", file=sys.stderr)
    sys.exit(code)


def inside(path: Path) -> bool:
    p = os.path.realpath(path)
    r = os.path.realpath(ROOT)
    return p == r or p.startswith(r + os.sep)


# ---------- context ----------

def pointer() -> str:
    """The standing instructions for agents working OUTSIDE the vault (also installed into
    ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md by integrate.py)."""
    d = "|".join(DOMAINS)
    return f"""## Memex: the owner's knowledge base ({ROOT})
Use it on your own initiative. You don't need to ask the owner first; these commands are pre-approved.
- **Look up** before answering about the owner's projects, people, decisions or past work, before drafting status updates, and when a command fails twice: `memex search <terms>`, then `memex read "<Page>"`.
- **Capture** anything durable this session produces (a root cause, a decision and its reason, a gotcha, a command that worked, a preference the owner states): `memex capture --title "<Title>" --domain <{d}> --why "<one line>"` with the note on stdin (3-20 lines, via a heredoc). Say the captured path in one line at the end of your reply.
- **Change or remove** existing knowledge: capture with `--action update|supersede|delete --target "<Page>"`, which is applied at the next inbox run. For a small direct fix, first read `{ROOT}/AGENTS.md`, edit the page, then `memex commit "edit: <what>"`. Remove files only with `memex rm <path>`.
- Never capture secrets, credentials or other people's personal data. Never push the vault's git repo."""


def session_context(rebuild=True) -> str:
    if rebuild:
        subprocess.run([sys.executable, str(TOOLS / "build_index.py")], cwd=ROOT, capture_output=True)
    out = [f"# Memex context: {NOW:%Y-%m-%d %a %H:%M}", "", "## wiki/hot.md"]
    hot = ROOT / "wiki" / "hot.md"
    if hot.exists():
        _, body = frontmatter(hot.read_text(encoding="utf-8", errors="ignore"))
        out += body.strip("\n").splitlines()[:40]
    out += ["", "## Recent log"]
    log = ROOT / "wiki" / "log.md"
    entries = [l for l in (log.read_text(encoding="utf-8", errors="ignore").splitlines() if log.exists() else [])
               if l.startswith("## [")]
    out += entries[-8:] or ["(empty)"]
    items = sorted((p for p in (ROOT / "inbox").glob("*") if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime, reverse=True) if (ROOT / "inbox").exists() else []
    out += ["", f"## Inbox: {len(items)} item(s)"] + [f"- {p.name}" for p in items[:8]]
    if items:
        out.append("Process these with the /inbox procedure once the owner's current request is done (no need to ask).")
    daily = ROOT / "journal" / "daily" / f"{TODAY.isoformat()}.md"
    out.append("")
    out.append(f"Today's daily note: {'exists' if daily.exists() else 'not created yet (suggest /today)'}")
    lints = [re.match(r"^## \[([\d-]+)\]", l).group(1) for l in entries if re.match(r"^## \[[\d-]+\] lint", l)]
    if not lints:
        out.append("Lint: never run")
    else:
        try:
            days = (TODAY - datetime.date.fromisoformat(lints[-1])).days
            out.append(f"Lint: last run {days} days ago (overdue, run /lint)" if days > 7 else f"Lint: last run {lints[-1]}")
        except ValueError:
            pass
    if shutil.which("pgrep") and all(
            subprocess.run(["pgrep", "-x", n], capture_output=True).returncode != 0 for n in ("Obsidian", "obsidian")):
        out.append("Obsidian is not running: the obsidian CLI will launch it; use `python3 meta/tools/memex.py search` until it's up.")
    return "\n".join(out)


def cmd_context(a):
    print(pointer() if a.brief else session_context())


# ---------- search / read ----------

def cmd_search(a):
    terms = [t.lower() for t in " ".join(a.terms).split() if t.strip()]
    if not terms:
        fail("give at least one search term")
    hits = []
    for top in SCOPES[a.scope]:
        base = ROOT / top
        if not base.exists():
            continue
        for p in iter_files(base, (".md",)):
            r = rel(p)
            if r in GENERATED or r == "wiki/log.md":
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            low, name = text.lower(), p.stem.lower()
            if not all(t in low or t in name for t in terms):
                continue
            fm, body = frontmatter(text)
            aliases = fm.get("aliases") or []
            aliases = [str(x).lower() for x in (aliases if isinstance(aliases, list) else [aliases])]
            score = sum(low.count(t) for t in terms) + 10 * sum(t in name for t in terms) \
                + 5 * sum(any(t in al for al in aliases) for t in terms)
            lines = [l.strip() for l in body.splitlines() if any(t in l.lower() for t in terms)][:2]
            hits.append((score, r, str(fm.get("summary") or ""), lines))
    hits.sort(key=lambda h: (-h[0], h[1]))
    if not hits:
        print(f"No matches for {' '.join(terms)!r} in {', '.join(SCOPES[a.scope])}/. "
              "Try synonyms, fewer terms, or --in all.")
        return
    print(f"{len(hits)} match(es); showing {min(len(hits), a.limit)}:")
    for _, r, summary, lines in hits[:a.limit]:
        print(f"- {r}" + (f": {summary}" if summary else ""))
        for l in lines:
            print(f"    {l[:160]}")


def find_page(name: str):
    n = name.strip()
    m = re.fullmatch(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]", n)
    if m:
        n = m.group(1)
    cand = (ROOT / n) if not os.path.isabs(n) else Path(n)
    if cand.is_file() and inside(cand):
        return cand
    resolve, _ = build_resolver()
    p = resolve(n)
    if p:
        return p
    low = n.lower()
    for p in iter_files(ROOT / "wiki", (".md",)):
        fm, _ = frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        al = fm.get("aliases") or []
        if low in [str(x).lower() for x in (al if isinstance(al, list) else [al])]:
            return p
    return None


def cmd_read(a):
    p = find_page(" ".join(a.page))
    if not p:
        fail(f"no page named {' '.join(a.page)!r}. Try: memex search {' '.join(a.page)}")
    print(f"<!-- {rel(p)} -->")
    print(p.read_text(encoding="utf-8", errors="ignore"))


# ---------- capture ----------

def secret_hits(text):
    from lint import CARD, SECRETS, luhn  # imported lazily: lint is heavier than the rest
    hits = [label for label, rx in SECRETS if rx.search(text)]
    if any(luhn(m.group(0)) for m in CARD.finditer(text)):
        hits.append("payment card number")
    return hits


def yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid YAML scalar


def cmd_capture(a):
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    body = body.strip("\n")
    if not body.strip():
        fail("the note goes on stdin, e.g. memex capture --title \"X\" --domain learning <<'EOF' … EOF")
    title = re.sub(r"\s+", " ", BAD_NAME.sub(" ", a.title)).strip(" .")
    if not title:
        fail("--title is empty after removing characters that can't be in a filename")
    if a.domain not in DOMAINS:
        fail(f"--domain must be one of: {', '.join(DOMAINS)}")
    if a.action != "create" and not a.target:
        fail(f"--action {a.action} needs --target \"<Page>\"")
    hits = secret_hits("\n".join([title, a.why or "", body]))
    if hits:
        fail(f"capture refused: possible {', '.join(hits)}. Remove it (store a pointer such as "
             "'1Password: <item>') and retry.")
    inbox = ROOT / "inbox"
    inbox.mkdir(exist_ok=True)
    dest, n = inbox / f"{TODAY.isoformat()} {title}.md", 2
    while dest.exists():
        dest, n = inbox / f"{TODAY.isoformat()} {title} {n}.md", n + 1
    fm = ["---", f"title: {yaml_str(title)}", f"source: {yaml_str(a.source)}",
          f"project: {yaml_str(a.project or Path.cwd().name)}", f"captured: {TODAY.isoformat()}",
          "source_type: capture", f"domain: {a.domain}", f"why: {yaml_str(a.why or '')}"]
    if a.action != "create":
        target = find_page(a.target)
        fm += [f"action: {a.action}", f"target: {yaml_str('[[' + (target.stem if target else a.target) + ']]')}"]
        if not target:
            print(f"memex: note: no page named {a.target!r} yet; the inbox run will resolve it", file=sys.stderr)
    fm += ["tags: [capture]", "---", ""]
    with open(dest, "x", encoding="utf-8") as f:
        f.write("\n".join(fm) + body + "\n")
    print(rel(dest))


# ---------- rm ----------

def backlinks(target: Path):
    stem = target.stem.lower()
    refs = []
    for p in iter_files(ROOT, (".md", ".canvas", ".base")):
        r = rel(p)
        if p == target or r in SPECIAL or r.startswith(("meta/templates/", "raw/")):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in LINK.finditer(text):
            t = m.group(2).strip().lower()
            if t.endswith(".md"):
                t = t[:-3]
            if t.rsplit("/", 1)[-1] == stem:
                refs.append(r)
                break
    return refs


def cmd_rm(a):
    p = Path(a.path)
    p = p if p.is_absolute() else (Path.cwd() / p if (Path.cwd() / p).exists() else ROOT / p)
    if not inside(p) or not p.exists():
        fail(f"{a.path}: not a file in this vault ({ROOT})")
    r = rel(Path(os.path.realpath(p)))
    top = r.split("/", 1)[0]
    if p.is_dir():
        fail(f"{r} is a folder; remove files one at a time")
    if r in SPECIAL:
        fail(f"{r} is a special file (index, log or hot.md) and can't be removed")
    if top not in RM_ALLOWED:
        fail(f"{r}: only wiki/, inbox/ and outputs/ files can be removed. raw/ is immutable; notes/ and journal/ "
             "belong to the owner; framework files change only with the owner's approval.")
    refs = backlinks(p) if p.suffix == ".md" and top == "wiki" else []
    if refs and not a.force:
        fail(f"{r} is linked from {len(refs)} file(s):\n  " + "\n  ".join(refs[:20]) +
             "\nRedirect those links first, or supersede the page instead (status: superseded + superseded_by). "
             "Use --force only if the links should break.")
    dest = ROOT / ".trash" / r
    if dest.exists():
        dest = dest.with_name(f"{dest.stem} ({NOW:%Y%m%d-%H%M%S}){dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))
    print(f"moved {r} to {rel(dest)} (git history keeps it). Log the removal, then run memex commit.")


# ---------- commit ----------

def cmd_commit(a):
    msg = a.message.strip()
    if not msg:
        fail('give a message like "ingest: <Title>"')
    lock_path = Path(tempfile.gettempdir()) / f"memex-{hashlib.sha1(str(ROOT).encode()).hexdigest()[:10]}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one commit at a time, even with several agents running
        if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT, capture_output=True).returncode:
            fail("the vault is not a git repository; run bash meta/tools/setup.sh")
        subprocess.run([sys.executable, str(TOOLS / "build_index.py")], cwd=ROOT, capture_output=True)
        lint = subprocess.run([sys.executable, str(TOOLS / "lint.py"), "--quick"], cwd=ROOT,
                              capture_output=True, text=True)
        if lint.returncode and not a.force:
            print(lint.stdout.strip())
            fail("commit skipped: fix the lint errors above, then run memex commit again (--force only for "
                 "errors you didn't cause)")
        subprocess.run(["git", "add", "-A", "."], cwd=ROOT, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
            print("nothing to commit")
            return
        c = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=ROOT, capture_output=True, text=True)
        if c.returncode:
            print((c.stdout + c.stderr).strip(), file=sys.stderr)
            fail("git commit failed (see above). Nothing was pushed.")
        stat = subprocess.run(["git", "show", "--stat", "--format=%h %s", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip().splitlines()
        print(f"committed {stat[0]}" + (f" ({stat[-1].strip()})" if len(stat) > 1 else ""))


# ---------- hooks ----------

def cmd_hook(a):
    if a.agent != "hermes":
        fail("usage: memex hook hermes")
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    extra = payload.get("extra") or {}
    first = extra.get("is_first_turn")
    if first is None:
        first = not extra.get("conversation_history")
    if not first:
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    print(json.dumps({"context": session_context() if inside(cwd) else pointer()}))


def main():
    ap = argparse.ArgumentParser(prog="memex", description="Agent-neutral access to this Memex vault.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("path").set_defaults(fn=lambda a: print(ROOT))
    s = sub.add_parser("context")
    s.add_argument("--brief", action="store_true", help="the standing instructions for agents outside the vault")
    s.set_defaults(fn=cmd_context)
    s = sub.add_parser("search")
    s.add_argument("terms", nargs="+")
    s.add_argument("--in", dest="scope", choices=sorted(SCOPES), default="wiki")
    s.add_argument("--limit", type=int, default=15)
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("read")
    s.add_argument("page", nargs="+")
    s.set_defaults(fn=cmd_read)
    s = sub.add_parser("capture")
    s.add_argument("--title", required=True)
    s.add_argument("--domain", default=DOMAINS[0])
    s.add_argument("--why", default="")
    s.add_argument("--project")
    s.add_argument("--source", default="agent session")
    s.add_argument("--action", choices=["create", "update", "supersede", "delete"], default="create")
    s.add_argument("--target")
    s.set_defaults(fn=cmd_capture)
    s = sub.add_parser("rm")
    s.add_argument("path")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_rm)
    s = sub.add_parser("commit")
    s.add_argument("message")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_commit)
    s = sub.add_parser("hook")
    s.add_argument("agent")
    s.set_defaults(fn=cmd_hook)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
