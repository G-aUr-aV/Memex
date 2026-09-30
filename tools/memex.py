#!/usr/bin/env python3
"""memex: one agent-neutral entry point to the owner's vault, for Claude Code, Codex, Hermes or a plain shell.

  memex path                                  print the vault path
  memex context [--brief]                     what a session starts with: hot.md, recent log, inbox, lint age
                                              (also re-renders the vault's managed files if the framework changed)
  memex search <terms…> [--in wiki|raw|journal|notes|all] [--limit N]
  memex read <page name | [[link]] | vault path>
  memex capture --title T [--domain D] [--why W] [--project P] [--source S]
                [--action create|update|supersede|delete --target PAGE]    (body on stdin)
  memex rm <vault path> [--force]             move a wiki/inbox/outputs file to .trash/ (git keeps history)
  memex commit "<op>: <title>" [--force]      rebuild indexes, lint --quick, commit the vault (never pushes)
  memex ctx <ingest|inbox|lint|today|close|weekly>   live context for a skill
  memex index · memex lint [--quick|--report]
  memex init <path> · memex sync [--check] · memex doctor [--fix]
  memex hook hermes                           Hermes pre_llm_call hook: adds context on a session's first turn

setup.sh installs this as ~/.local/bin/memex. Otherwise run: python3 <framework>/tools/memex.py …
The vault comes from $MEMEX_VAULT or ~/.config/memex/config.json. This writes only inside the vault: new
files in inbox/, trash moves out of wiki/inbox/outputs, rendered framework files, and local git commits.
It never edits raw/, notes/ or journal/, and never pushes.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memexlib as ml  # noqa: E402
from memexlib import FRAMEWORK, LINK, VERSION, VaultError, frontmatter, iter_files, rel  # noqa: E402

TOOLS = FRAMEWORK / "tools"
NOW = datetime.datetime.now()
TODAY = NOW.date()
SCOPES = {"wiki": ["wiki"], "raw": ["raw"], "journal": ["journal"], "notes": ["notes"],
          "all": ["wiki", "raw", "journal", "notes", "inbox", "outputs"]}
RM_ALLOWED = {"wiki", "inbox", "outputs"}
BAD_NAME = re.compile(r"[:\\/#^\[\]|*?\"<>]")


def fail(msg, code=1):
    print(f"memex: {msg}", file=sys.stderr)
    sys.exit(code)


def V() -> Path:
    try:
        return ml.vault()
    except VaultError as e:
        fail(str(e), 2)


def inside(path: Path) -> bool:
    p = os.path.realpath(path)
    r = os.path.realpath(V())
    return p == r or p.startswith(r + os.sep)


# ---------- context ----------

def pointer() -> str:
    """The standing instructions for agents working OUTSIDE the vault (also installed into
    ~/.claude/CLAUDE.md and ~/.codex/AGENTS.md by integrate.py)."""
    v = V()
    d = "|".join(ml.domain_names(v))
    return f"""## Memex: the owner's knowledge base ({v})
Use it on your own initiative. You don't need to ask the owner first; these commands are pre-approved.
- **Look up** before answering about the owner's projects, people, decisions or past work, before drafting status updates, and when a command fails twice: `memex search <terms>`, then `memex read "<Page>"`.
- **Capture** anything durable this session produces (a root cause, a decision and its reason, a gotcha, a command that worked, a preference the owner states): `memex capture --title "<Title>" --domain <{d}> --why "<one line>"` with the note on stdin (3-20 lines, via a heredoc). Say the captured path in one line at the end of your reply.
- **Change or remove** existing knowledge: capture with `--action update|supersede|delete --target "<Page>"`, which is applied at the next inbox run. For a small direct fix, first read `{v}/AGENTS.md`, edit the page, then `memex commit "edit: <what>"`. Remove files only with `memex rm <path>`.
- Never capture secrets, credentials or other people's personal data. The vault is local-only: never add a git remote to it or push it."""


def safety_notes(v: Path):
    notes = []
    remotes = ml.git(v, "remote").stdout.split()
    if remotes:
        notes.append(f"WARNING: the vault has git remote(s) ({', '.join(remotes)}); Memex vaults are local-only and "
                     "`memex commit` refuses to run until they're removed.")
    ident = ml.identity(v)
    want = f"{ident['name']} <{ident['email']}>"
    log = ml.git(v, "log", "-n", "300", "--format=%an <%ae>%n%cn <%ce>").stdout.splitlines()
    others = sorted({l for l in log if l.strip()} - {want})
    if others:
        notes.append(f"WARNING: recent vault commits use other identities ({', '.join(others[:3])}); run memex doctor.")
    return notes


def session_context(rebuild=True) -> str:
    v = V()
    import vault as vaultmod
    head = []
    try:
        res = vaultmod.sync(v, only_if_stale=True)
        if res["stale"]:
            msg = f"Framework updated since the last sync: {vaultmod.describe(res)}."
            if "AGENTS.md" in res["changed"]:
                msg += " AGENTS.md changed: re-read it before writing anything."
            head.append(msg)
    except VaultError as e:
        head.append(f"WARNING: couldn't render the framework files: {e}")
    if rebuild:
        subprocess.run([sys.executable, str(TOOLS / "build_index.py")], cwd=v, capture_output=True,
                       env={**os.environ, "MEMEX_VAULT": str(v)})
    name = ml.settings(v).get("name") or v.name
    out = [f"# Memex context: {NOW:%Y-%m-%d %a %H:%M}", f"Vault `{name}` at {v} · framework {VERSION} at {FRAMEWORK}"]
    out += head + safety_notes(v) + ["", "## wiki/hot.md"]
    hot = v / "wiki" / "hot.md"
    if hot.exists():
        _, body = frontmatter(hot.read_text(encoding="utf-8", errors="ignore"))
        out += body.strip("\n").splitlines()[:40]
    out += ["", "## Recent log"]
    log = v / "wiki" / "log.md"
    entries = [l for l in (log.read_text(encoding="utf-8", errors="ignore").splitlines() if log.exists() else [])
               if l.startswith("## [")]
    out += entries[-8:] or ["(empty)"]
    items = sorted((p for p in (v / "inbox").glob("*") if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime, reverse=True) if (v / "inbox").exists() else []
    out += ["", f"## Inbox: {len(items)} item(s)"] + [f"- {p.name}" for p in items[:8]]
    if items:
        out.append("Process these with the /inbox procedure once the owner's current request is done (no need to ask).")
    daily = v / "journal" / "daily" / f"{TODAY.isoformat()}.md"
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
        out.append("Obsidian is not running: the obsidian CLI will launch it; use `memex search` until it's up.")
    return "\n".join(out)


def cmd_context(a):
    if a.brief:
        print(pointer())
        return
    try:
        print(session_context())
    except SystemExit:
        raise
    except Exception as e:  # a session must start even if the context can't be built
        print(f"(Memex context unavailable: {type(e).__name__}: {e}. Run memex doctor.)")


# ---------- search / read ----------

def cmd_search(a):
    v = V()
    terms = [t.lower() for t in " ".join(a.terms).split() if t.strip()]
    if not terms:
        fail("give at least one search term")
    skip = ml.generated(v) | {"wiki/log.md"}
    hits = []
    for top in SCOPES[a.scope]:
        base = v / top
        if not base.exists():
            continue
        for p in iter_files(base, (".md",)):
            r = rel(p)
            if r in skip:
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
    v = V()
    n = name.strip()
    m = re.fullmatch(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]", n)
    if m:
        n = m.group(1)
    cand = (v / n) if not os.path.isabs(n) else Path(n)
    if cand.is_file() and inside(cand):
        return cand
    resolve, _ = ml.build_resolver()
    p = resolve(n)
    if p:
        return p
    low = n.lower()
    for p in iter_files(v / "wiki", (".md",)):
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

def yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid YAML scalar


def cmd_capture(a):
    from secretscan import hits as secret_hits
    v = V()
    domains = ml.domain_names(v)
    domain = a.domain or domains[0]
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    body = body.strip("\n")
    if not body.strip():
        fail("the note goes on stdin, e.g. memex capture --title \"X\" --domain learning <<'EOF' … EOF")
    title = re.sub(r"\s+", " ", BAD_NAME.sub(" ", a.title)).strip(" .")
    if not title:
        fail("--title is empty after removing characters that can't be in a filename")
    if domain not in domains:
        fail(f"--domain must be one of: {', '.join(domains)}")
    if a.action != "create" and not a.target:
        fail(f"--action {a.action} needs --target \"<Page>\"")
    found = secret_hits("\n".join([title, a.why or "", body]))
    if found:
        fail(f"capture refused: possible {', '.join(found)}. Remove it (store a pointer such as "
             "'1Password: <item>') and retry.")
    inbox = v / "inbox"
    inbox.mkdir(exist_ok=True)
    dest, n = inbox / f"{TODAY.isoformat()} {title}.md", 2
    while dest.exists():
        dest, n = inbox / f"{TODAY.isoformat()} {title} {n}.md", n + 1
    fm = ["---", f"title: {yaml_str(title)}", f"source: {yaml_str(a.source)}",
          f"project: {yaml_str(a.project or Path.cwd().name)}", f"captured: {TODAY.isoformat()}",
          "source_type: capture", f"domain: {domain}", f"why: {yaml_str(a.why or '')}"]
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
    special = ml.special()
    refs = []
    for p in iter_files(V(), (".md", ".canvas", ".base")):
        r = rel(p)
        if p == target or r in special or r.startswith(("meta/templates/", "meta/docs/", "raw/")):
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
    v = V()
    p = Path(a.path)
    p = p if p.is_absolute() else (Path.cwd() / p if (Path.cwd() / p).exists() else v / p)
    if not inside(p) or not p.exists():
        fail(f"{a.path}: not a file in this vault ({v})")
    r = rel(Path(os.path.realpath(p)), Path(os.path.realpath(v)))
    top = r.split("/", 1)[0]
    if p.is_dir():
        fail(f"{r} is a folder; remove files one at a time")
    if r in ml.special(v):
        fail(f"{r} is a special file (index, log or hot.md) and can't be removed")
    if top not in RM_ALLOWED:
        fail(f"{r}: only wiki/, inbox/ and outputs/ files can be removed. raw/ is immutable; notes/ and journal/ "
             "belong to the owner; framework files change only in the framework, with the owner's approval.")
    refs = backlinks(p) if p.suffix == ".md" and top == "wiki" else []
    if refs and not a.force:
        fail(f"{r} is linked from {len(refs)} file(s):\n  " + "\n  ".join(refs[:20]) +
             "\nRedirect those links first, or supersede the page instead (status: superseded + superseded_by). "
             "Use --force only if the links should break.")
    dest = v / ".trash" / r
    if dest.exists():
        dest = dest.with_name(f"{dest.stem} ({NOW:%Y%m%d-%H%M%S}){dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dest))
    print(f"moved {r} to {rel(dest)} (git history keeps it). Log the removal, then run memex commit.")


# ---------- commit, tools, vault upkeep ----------

def cmd_commit(a):
    import vault as vaultmod
    if not a.message.strip():
        fail('give a message like "ingest: <Title>"')
    try:
        print(vaultmod.commit(V(), a.message, force=a.force))
    except VaultError as e:
        fail(str(e))


def run_tool(name, *args):
    v = V()
    r = subprocess.run([sys.executable, str(TOOLS / name), *args], cwd=v, env={**os.environ, "MEMEX_VAULT": str(v)})
    sys.exit(r.returncode)


def cmd_ctx(a):
    V()
    import context
    sys.argv = ["context.py", a.skill]
    context.run()


def cmd_init(a):
    import vault as vaultmod
    try:
        v = vaultmod.init(a.path, a.name)
    except VaultError as e:
        fail(str(e), 2)
    print(f"created vault {v}. Run setup.sh to make it the configured vault and wire the agents.")


def cmd_sync(a):
    import vault as vaultmod
    v = V()
    try:
        if a.check:
            stale = vaultmod.stale(v)
            print("managed files are stale: run memex sync" if stale else "managed files are up to date")
            sys.exit(1 if stale else 0)
        res = vaultmod.sync(v)
    except VaultError as e:
        fail(str(e), 2)
    print(vaultmod.describe(res) if res["changed"] or res["removed"] or res["backed_up"] else "up to date")


def cmd_doctor(a):
    import vault as vaultmod
    v = V()
    try:
        checks = vaultmod.doctor(v, fix=a.fix)
    except VaultError as e:
        fail(str(e), 2)
    print(f"Memex {VERSION} · framework {FRAMEWORK} · vault {v}")
    for ok, msg in checks:
        print(f"  {'✓' if ok else '✗'} {msg}")
    bad = [m for ok, m in checks if not ok]
    if bad:
        print("Run memex doctor --fix to repair what can be repaired (identity, hooks, managed files)." if not a.fix
              else "Fix the remaining items by hand (see above).")
    sys.exit(1 if bad else 0)


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
    ap = argparse.ArgumentParser(prog="memex", description="Agent-neutral access to the owner's Memex vault.")
    ap.add_argument("--version", action="version", version=f"memex {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("path").set_defaults(fn=lambda a: print(V()))
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
    s.add_argument("--domain", help="one of the vault's domains (default: the first)")
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
    s = sub.add_parser("ctx", help="live context for a skill")
    s.add_argument("skill")
    s.set_defaults(fn=cmd_ctx)
    sub.add_parser("index").set_defaults(fn=lambda a: run_tool("build_index.py"))
    s = sub.add_parser("lint")
    s.add_argument("--quick", action="store_true")
    s.add_argument("--report", action="store_true")
    s.set_defaults(fn=lambda a: run_tool("lint.py", *(["--quick"] if a.quick else []), *(["--report"] if a.report else [])))
    s = sub.add_parser("init", help="create a new vault (setup.sh does this for you)")
    s.add_argument("path")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("sync", help="re-render the vault's managed files from the framework")
    s.add_argument("--check", action="store_true", help="only report whether they're stale")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("doctor", help="check the vault's git safety settings and managed files")
    s.add_argument("--fix", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("hook")
    s.add_argument("agent")
    s.set_defaults(fn=cmd_hook)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
