#!/usr/bin/env python3
"""memex: one agent-neutral entry point to the owner's vault, for Claude Code, Codex, Hermes or a plain shell.

  memex path                                  print the vault path
  memex context [--brief]                     what a session starts with: hot.md, recent log, inbox, lint age
                                              (also re-renders the vault's managed files if the framework changed)
  memex search <terms…> [--in wiki|raw|journal|notes|all] [--limit N] [--json]   BM25-ranked
  memex read <page name | [[link]] | alias | vault path>[#Heading]      a page, or just one section
  memex outline <page> · memex related <page>          headings with sizes · links in and out, co-cited pages
  memex capture --title T [--domain D] [--why W] [--project P] [--source S]
                [--action create|update|supersede|delete --target PAGE]    (body on stdin)
  memex file inbox/<file> --domain D [--name "YYYY-MM-DD Title.md"]   file an inbox item into raw/<domain>/
  memex rm <vault path> [--force]             move a wiki/inbox/outputs file to .trash/ (git keeps history)
  memex commit "<op>: <title>" [--force]      rebuild indexes, lint --quick, commit the vault (then push, if
                                              the owner attached a private remote)
  memex ctx <ingest|inbox|lint|today|close|weekly>   live context for a skill
  memex index · memex lint [--quick|--report]
  memex harvest [--digest ID | --done ID | --skip-trivial]   sessions recorded at session end, for /harvest
  memex backup [--to DIR]                     git bundle of the vault (restore with git clone)
  memex pull [--merge] · memex push · memex remote   sync with the private remote, if one is attached
  memex init <path> · memex sync [--check] · memex doctor [--fix]
  owner only (the guard blocks agents):  memex remote set <url> | memex remote remove
                                         memex move <path> · memex uninstall [--delete-vault --confirm NAME]
  memex hook session-start|session-end|hermes  agent hooks: repo recall at start, session ledger at end

setup.sh installs this as ~/.local/bin/memex. Otherwise run: python3 <framework>/tools/memex.py …
The vault comes from $MEMEX_VAULT or ~/.config/memex/config.json. This writes only inside the vault: new
files in inbox/, trash moves out of wiki/inbox/outputs, rendered framework files, and git commits. It never
edits raw/, notes/ or journal/, and pushes only to the private remote the owner attached (see remote.py).
"""
import argparse
import datetime
import json
import math
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
- **Change or remove** existing knowledge: capture with `--action update|supersede|delete --target "<Page>"` and say what's wrong and why in the note. A session in the vault applies it at its next inbox run.
- **The inbox is the only way in from here.** Don't edit, move or delete files in the vault yourself; the guard refuses, so use `memex capture`.
- Sessions are recorded when they end and compiled by `/harvest`, but capture decisive findings right away: the harvest only sees what the transcript shows.
- Never capture secrets, credentials or other people's personal data. Never add a git remote to the vault or push it."""


def safety_notes(v: Path):
    import remote
    notes = []
    try:
        remote.check_remotes(v)
    except VaultError as e:
        notes.append(f"WARNING: {e} `memex commit` refuses to run until this is fixed.")
    ident = ml.identity(v)
    want = f"{ident['name']} <{ident['email']}>"
    log = ml.git(v, "log", "-n", "300", "--format=%an <%ae>%n%cn <%ce>").stdout.splitlines()
    others = sorted({l for l in log if l.strip()} - {want})
    if others:
        notes.append(f"WARNING: recent vault commits use other identities ({', '.join(others[:3])}); run memex doctor.")
    import vault as vaultmod
    age, warn = vaultmod.backup_age(v), int(ml.feature("backup", v)["warn_days"])
    pushed = vaultmod.push_age(v)
    if pushed is not None and pushed <= warn:
        pass  # the private remote holds an off-machine copy
    elif age is None:
        notes.append("Backup: none yet, and no remote, so this disk is the only copy: run `memex backup`.")
    elif age > warn:
        notes.append(f"Backup: last one {age} days ago: run `memex backup`.")
    return notes


def session_context(rebuild=True) -> str:
    v = V()
    import remote
    import vault as vaultmod
    head = []
    try:
        line = remote.session_line(v)  # pull before anything reads the vault
        if line:
            head.append(line)
    except Exception as e:
        head.append(f"Sync WARNING: {type(e).__name__}: {e}")
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


# ---------- search / read / outline / related ----------

WORD = re.compile(r"[a-z0-9]+")
FIELD_WEIGHTS = {"title": 3.0, "aliases": 3.0, "summary": 2.0, "headings": 1.5, "body": 1.0}
K1, B = 1.2, 0.75
LONG_PAGE = 150  # read longer pages section by section


def stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def words(s: str):
    return [stem(w) for w in WORD.findall(s.lower())]


def as_list(x):
    return [str(i) for i in x] if isinstance(x, list) else ([str(x)] if x else [])


def docs(v: Path, scope: str):
    skip = ml.generated(v) | {"wiki/log.md"}
    for top in SCOPES[scope]:
        base = v / top
        if not base.exists():
            continue
        for p in iter_files(base, (".md",)):
            r = rel(p)
            if r in skip:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            fm, body = frontmatter(text)
            heads = [l.lstrip("#").strip() for l in body.splitlines() if l.startswith("#")]
            yield {"path": r, "title": p.stem, "fm": fm, "body": body, "fields": {
                "title": words(p.stem), "aliases": words(" ".join(as_list(fm.get("aliases")))),
                "summary": words(str(fm.get("summary") or "")), "headings": words(" ".join(heads)),
                "body": words(body)}}


def rank(v: Path, query: str, scope="wiki"):
    """BM25 over weighted fields (title and aliases ×3, summary ×2, headings ×1.5, body ×1). Pages that contain
    every term come first; if none do, pages with some of the terms are returned instead."""
    terms = list(dict.fromkeys(words(query)))
    corpus = list(docs(v, scope))
    if not terms or not corpus:
        return terms, [], False
    n = len(corpus)
    lengths = [sum(len(d["fields"][f]) * w for f, w in FIELD_WEIGHTS.items()) for d in corpus]
    avg = (sum(lengths) / n) or 1.0
    df = {t: sum(1 for d in corpus if any(t in d["fields"][f] for f in FIELD_WEIGHTS)) for t in terms}
    scored = []
    for d, length in zip(corpus, lengths):
        score, matched = 0.0, 0
        for t in terms:
            tf = sum(d["fields"][f].count(t) * w for f, w in FIELD_WEIGHTS.items())
            if not tf:
                continue
            matched += 1
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg))
        if matched:
            scored.append((matched, score, d))
    full = [s for s in scored if s[0] == len(terms)]
    pool, partial = (full, False) if full else (scored, True)
    pool.sort(key=lambda s: (-s[1], s[2]["path"]))
    return terms, [(round(s, 3), d) for _, s, d in pool], partial


def snippet(body: str, terms) -> str:
    best, score = "", 0
    for line in body.splitlines():
        ws = set(words(line))
        hit = sum(t in ws for t in terms)
        if hit > score and line.strip() and not line.startswith("---"):
            best, score = line.strip(), hit
    return best[:160]


def cmd_search(a):
    v = V()
    query = " ".join(a.terms)
    terms, hits, partial = rank(v, query, a.scope)
    if not terms:
        fail("give at least one search term")
    if a.json:
        print(json.dumps([{"path": d["path"], "title": d["title"], "summary": str(d["fm"].get("summary") or ""),
                           "lines": d["body"].count("\n") + 1, "score": s, "snippet": snippet(d["body"], terms)}
                          for s, d in hits[:a.limit]], indent=2))
        return
    if not hits:
        print(f"No matches for {query!r} in {', '.join(SCOPES[a.scope])}/. Try synonyms, fewer terms, or --in all.")
        return
    note = " (no page has every term; showing partial matches)" if partial else ""
    print(f"{len(hits)} match(es){note}; showing {min(len(hits), a.limit)}:")
    for _, d in hits[:a.limit]:
        summary = str(d["fm"].get("summary") or "")
        print(f"- {d['path']} ({d['body'].count(chr(10)) + 1} lines)" + (f": {summary}" if summary else ""))
        s = snippet(d["body"], terms)
        if s and s != summary:
            print(f"    {s}")
    print(f"Next: memex read \"<Page>\". For pages over {LONG_PAGE} lines, memex outline \"<Page>\" first, then "
          "memex read \"<Page>#<Heading>\" for the sections you need.")


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
        if low in [x.lower() for x in as_list(fm.get("aliases"))]:
            return p
    return None


def split_ref(ref: str):
    """"Page#Heading" -> ("Page", "Heading"); block refs (#^id) and plain names keep the whole name."""
    m = re.fullmatch(r"!?\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", ref.strip())
    ref = m.group(1) if m else ref.strip()
    if "#" in ref and not ref.split("#", 1)[1].startswith("^"):
        page, heading = ref.split("#", 1)
        return page.strip(), heading.strip()
    return ref, None


def section(text: str, heading: str):
    lines, out, level = text.splitlines(), None, 0
    want = ml.norm_heading(heading)
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m and out is not None and len(m.group(1)) <= level:
            break
        if m and out is None and ml.norm_heading(m.group(2)) == want:
            out, level = [line], len(m.group(1))
            continue
        if out is not None:
            out.append(line)
    return "\n".join(out).rstrip() + "\n" if out else None


def page_or_fail(ref: str) -> Path:
    p = find_page(ref)
    if not p:
        fail(f"no page named {ref!r}. Try: memex search {ref}")
    return p


def cmd_read(a):
    page, heading = split_ref(" ".join(a.page))
    p = page_or_fail(page)
    text = p.read_text(encoding="utf-8", errors="ignore")
    if heading:
        part = section(text, heading)
        if part is None:
            heads = [l for l in text.splitlines() if re.match(r"^#{1,6}\s", l)]
            fail(f"{rel(p)} has no heading {heading!r}. Headings: " + "; ".join(h.lstrip('# ') for h in heads[:30]))
        print(f"<!-- {rel(p)}#{heading} -->")
        print(part, end="")
        return
    print(f"<!-- {rel(p)} -->")
    print(text)


def cmd_outline(a):
    p = page_or_fail(" ".join(a.page))
    text = p.read_text(encoding="utf-8", errors="ignore")
    fm, body = frontmatter(text)
    print(f"{rel(p)} · {fm.get('type', '')} · updated {fm.get('updated', '?')} · {text.count(chr(10))} lines")
    if fm.get("summary"):
        print(f"summary: {fm['summary']}")
    lines = body.splitlines()
    heads = [(i, l) for i, l in enumerate(lines) if re.match(r"^#{1,6}\s", l)]
    for k, (i, l) in enumerate(heads):
        nxt = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        print(f"{'  ' * (len(l) - len(l.lstrip('#')) - 1)}- {l.lstrip('#').strip()} ({nxt - i - 1} lines)")
    print(f"Read one section: memex read \"{p.stem}#<Heading>\"")


def outlinks(p: Path):
    resolve, _ = ml.build_resolver()
    _, body = frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
    out = []
    for m in LINK.finditer(body):
        dest = resolve(m.group(2).rstrip("\\"))
        if dest and dest != p and dest not in out:
            out.append(dest)
    return out


def summary_of(p: Path) -> str:
    fm, _ = frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
    return str(fm.get("summary") or "")


def cmd_related(a):
    v = V()
    p = page_or_fail(" ".join(a.page))
    outs = outlinks(p)
    sources = [o for o in outs if rel(o).startswith(("raw/", "journal/"))]
    pages = [o for o in outs if rel(o).startswith("wiki/") and rel(o) not in ml.special(v)]
    backs = [v / r for r in backlinks(p) if r.startswith("wiki/")]
    cocited = []
    if sources:
        for q in iter_files(v / "wiki", (".md",)):
            if q == p or rel(q) in ml.special(v):
                continue
            shared = [s for s in outlinks(q) if s in sources]
            if shared:
                cocited.append((len(shared), q))
        cocited.sort(key=lambda x: (-x[0], rel(x[1])))
    print(f"Related to {rel(p)}:")
    for label, items in (("Links here", backs), ("Links to", pages), ("Cites", sources),
                         ("Cites the same sources", [q for _, q in cocited[:10]])):
        if items:
            print(f"## {label}")
            for q in items[:15]:
                s = summary_of(q) if rel(q).startswith("wiki/") else ""
                print(f"- {rel(q)}" + (f": {s}" if s else ""))
    if not (backs or pages or sources or cocited):
        print("(no links in or out yet)")


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


# ---------- file ----------

def relink(v: Path, old: str, new: str):
    """Point [[old]] links (with #heading or |alias) at [[new]] in wiki/, inbox/ and outputs/ (never raw/ or notes/)."""
    pat = re.compile(r"(!?\[\[)" + re.escape(old) + r"(\.md)?(?=[\]#|])", re.I)
    changed = []
    for top in ("wiki", "inbox", "outputs"):
        for q in iter_files(v / top, (".md", ".canvas")) if (v / top).exists() else []:
            text = q.read_text(encoding="utf-8", errors="ignore")
            new_text = pat.sub(lambda m: m.group(1) + new, text)
            if new_text != text:
                q.write_text(new_text, encoding="utf-8")
                changed.append(rel(q))
    return changed


def cmd_file(a):
    """File an inbox item into raw/<domain>/ (the move /ingest, /inbox and /harvest do), without Obsidian."""
    v = V()
    src = Path(a.path)
    src = src if src.is_absolute() else (Path.cwd() / src if (Path.cwd() / src).exists() else v / src)
    if not src.is_file() or not inside(src) or rel(src).split("/", 1)[0] != "inbox":
        fail(f"{a.path}: not a file in {v / 'inbox'}")
    if a.domain not in ml.domain_names(v):
        fail(f"--domain must be one of: {', '.join(ml.domain_names(v))}")
    name = (a.name or src.name).strip()
    if BAD_NAME.sub("", name) != name or name.startswith("."):
        fail(f"{name!r} can't be a file name (no : / \\ # ^ [ ] | * ? \" < >)")
    if Path(name).suffix == "" and src.suffix:
        name += src.suffix
    dest = v / "raw" / a.domain / name
    if dest.exists():
        fail(f"{rel(dest)} already exists; raw/ is create-only, so pick another name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    fixed = relink(v, src.stem, dest.stem) if dest.stem != src.stem else []
    print(rel(dest))
    if fixed:
        print(f"memex: updated links in {len(fixed)} file(s): {', '.join(fixed[:5])}", file=sys.stderr)


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
        print(f"  {'✓' if ok else '!' if ok is None else '✗'} {msg}")
    bad = [m for ok, m in checks if ok is False]
    if bad:
        print("Run memex doctor --fix to repair what can be repaired (identity, hooks, managed files)." if not a.fix
              else "Fix the remaining items by hand (see above).")
    sys.exit(1 if bad else 0)


# ---------- harvest & backup ----------

def cmd_harvest(a):
    import sessions
    v = V()
    try:
        if a.done:
            rec = sessions.find(v, a.done)
            sessions.mark(v, rec["session_id"], a.result)
            print(f"marked {rec['session_id'][:8]} as {a.result}")
            return
        if a.digest:
            rec = sessions.find(v, a.digest)
            d = sessions.digest(rec.get("transcript_path"))
            if d["missing"]:
                fail(f"the transcript for {rec['session_id'][:8]} is gone ({rec.get('transcript_path')}); "
                     f"mark it: memex harvest --done {rec['session_id'][:8]} --result skipped")
            name, text, labels = sessions.render(rec, d, ml.domain_names(v)[0])
            dest = v / "inbox" / name
            dest.parent.mkdir(exist_ok=True)
            if not dest.exists():
                dest.write_text(text, encoding="utf-8")
            print(rel(dest))
            if labels:
                print(f"memex: redacted in the digest: {', '.join(labels)}", file=sys.stderr)
            return
    except VaultError as e:
        fail(str(e))
    h = ml.feature("harvest", v)
    pending = [r for r in sessions.load(v) if not r["harvested"]]
    rows, trivial = [], []
    for r in pending:
        d = sessions.digest(r.get("transcript_path"))
        row = (r, d)
        real = d["unparsed"] or (d["tool_calls"] >= int(h["min_tool_calls"]) and not d["missing"])
        (rows if real or a.all else trivial).append(row)
    if a.skip_trivial:
        for r, d in trivial:
            sessions.mark(v, r["session_id"], "skipped")
        print(f"marked {len(trivial)} trivial or missing session(s) as skipped")
        return
    if not rows:
        print("Nothing to harvest." + (f" ({len(trivial)} trivial session(s); memex harvest --all lists them, "
                                        "--skip-trivial clears them.)" if trivial else ""))
        return
    print(f"{len(rows)} session(s) to harvest, oldest first" +
          (f"; {len(trivial)} trivial hidden (--all shows them)" if trivial and not a.all else "") + ":")
    for r, d in rows[:a.limit]:
        first = (d["prompts"][0] if d["prompts"] else "").replace("\n", " ")[:80]
        state = ("transcript missing" if d["missing"] else
                 "UNPARSED: transcript format not recognized; the framework may need an update" if d["unparsed"] else
                 f"{d['tool_calls']} tool calls, {len(d['files'])} files")
        print(f"- {r['session_id'][:8]}  {(d['started'] or r.get('first_ts') or '')[:10]}  {r.get('agent', '')}  {r.get('repo', '')}  "
              f"({state})  {first!r}")
    print("Next: memex harvest --digest <id> writes the session's digest into inbox/ (see /harvest).")
    unparsed = [r for r, d in rows if d["unparsed"]]
    if unparsed:
        print(f"WARNING: {len(unparsed)} session(s) are in a transcript format this framework doesn't recognize, so "
              "their digests would be empty. Tell the owner (the agent's log format probably changed) and leave them "
              "for a framework update; don't mark them done.")


def cmd_backup(a):
    import vault as vaultmod
    v = V()
    try:
        path, pruned, dirty, same_disk = vaultmod.bundle(v, a.to, a.keep)
    except VaultError as e:
        fail(str(e))
    print(f"backup written: {path}" + (f" (removed {pruned} older bundle(s))" if pruned else ""))
    if dirty:
        print("note: uncommitted changes aren't in the bundle; run memex commit first to include them.")
    if same_disk:
        print("note: the backup is on the same disk as the vault. That protects against mistakes, not against losing "
              "the disk: set \"backup\": {\"dir\": \"/Volumes/<drive>/…\"} in .memex/vault.json or pass --to.")
    print(f"restore: git clone \"{path}\" <new folder>, then bash setup.sh --vault <new folder>")


# ---------- hooks ----------

def cmd_pull(a):
    import remote
    try:
        msg = remote.pull(V(), merge=a.merge)
    except VaultError as e:
        fail(str(e))
    print(msg)
    sys.exit(1 if msg.startswith("CONFLICT") else 0)


def cmd_push(a):
    import remote
    try:
        print(remote.push(V()) or ("nothing to push" if remote.settings(V()) else
                                   "no remote: the vault is local-only (the owner can attach one with memex remote set)"))
    except VaultError as e:
        fail(str(e))


def cmd_remote(a):
    import remote
    v = V()
    try:
        if a.action == "set":
            if not a.url:
                fail("give the URL: memex remote set <url>")
            print(remote.attach(v, a.url, branch=a.branch, yes=a.yes, unverified=a.unverified))
        elif a.action == "remove":
            print(remote.detach(v))
        else:
            print(remote.status(v))
    except VaultError as e:
        fail(str(e))


def cmd_move(a):
    import vault as vaultmod
    try:
        notes = vaultmod.move(V(), a.dest)
    except VaultError as e:
        fail(str(e), 2)
    for n in notes:
        print(f"  {n}")


def cmd_uninstall(a):
    import vault as vaultmod
    try:
        report, left = vaultmod.uninstall(a.delete_vault, a.confirm)
    except VaultError as e:
        fail(str(e), 2)
    sys.exit(vaultmod.print_uninstall(report, left))


def norm_repo(s: str) -> str:
    s = re.sub(r"\.git$", "", str(s).strip().lower().rstrip("/"))
    s = re.sub(r"^[a-z+]+://", "", s)
    s = re.sub(r"^[^@/]+@", "", s).replace(":", "/")
    parts = [x for x in s.split("/") if x]
    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else "")


def repo_identity(cwd: Path):
    top = ml.repo_top(cwd)
    name = (top or cwd).name
    remote = ml.git(top, "remote", "get-url", "origin").stdout.strip() if top else ""
    return name, norm_repo(remote) if remote else ""


TASK_PREFIX = re.compile(r"^- (\[ \] )?")


def recall(cwd: Path) -> str:
    """What the wiki knows about the repo at cwd (for SessionStart in other repos); "" when nothing matches."""
    v = V()
    if not ml.feature("recall", v).get("enabled"):
        return ""
    name, slug = repo_identity(cwd)
    keys = {name.lower(), re.sub(r"[-_.]+", " ", name.lower())}
    special = ml.special(v)
    found = []
    for p in iter_files(v / "wiki", (".md",)):
        if rel(p) in special:
            continue
        fm, body = frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        if str(fm.get("status", "")) in ("superseded", "archived"):
            continue
        score = 0
        for r in as_list(fm.get("repo")) + as_list(fm.get("repos")):
            nr = norm_repo(r)
            score = max(score, 3 if slug and nr == slug else 2 if nr and nr.split("/")[-1] == name.lower() else 0)
        if not score and keys & ({p.stem.lower()} | {x.lower() for x in as_list(fm.get("aliases"))}):
            score = 1
        if score:
            found.append((score, str(fm.get("updated") or ""), p, fm, body))
    if not found:
        return ""
    found.sort(key=lambda x: (x[0], x[1]), reverse=True)  # best match, then most recently updated
    lines = [f"## Memex: what the owner's knowledge base says about `{name}`"]
    for _, _, p, fm, body in found[:int(ml.feature("recall", v).get("max_pages", 3))]:
        lines.append(f"- [[{p.stem}]] ({fm.get('type', 'page')}, updated {fm.get('updated', '?')}): {fm.get('summary', '')}")
        loops = [l.strip() for l in body.splitlines() if l.strip().startswith("- [ ]")][:2]
        grab, bullets = False, []
        for l in body.splitlines():
            if l.startswith("## "):
                grab = l[3:].strip().lower() == "open loops"
                continue
            if grab and l.strip().startswith("- ") and l.strip() != "-":
                bullets.append(l.strip())
        lines += ["  - open: " + TASK_PREFIX.sub("", x) for x in (loops + bullets)[:3]]
        decisions = []
        for r in backlinks(p):
            q = v / r
            if "/decisions/" in r:
                dfm, _ = frontmatter(q.read_text(encoding="utf-8", errors="ignore"))
                decisions.append(f"[[{q.stem}]] ({dfm.get('status', '?')})")
        if decisions:
            lines.append(f"  - decisions: {', '.join(decisions[:3])}")
    lines.append("More: `memex read \"<Page>\"` · `memex related \"<Page>\"`. Capture what you learn with "
                 "`memex capture`; this session is also recorded for /harvest when it ends.")
    return "\n".join(lines[:15])


def hook_payload():
    try:
        return {} if sys.stdin.isatty() else (json.load(sys.stdin) or {})
    except Exception:
        return {}


def cmd_hook(a):
    payload = hook_payload()
    cwd = Path(payload.get("cwd") or os.getcwd())
    try:
        v = ml.vault()
    except VaultError:
        return  # no vault on this machine: hooks stay silent
    if a.event == "session-end":
        import sessions
        try:
            sessions.record(v, payload, a.agent or "agent")
        except Exception:
            pass  # never get in the way of an agent exiting
        return
    if a.event == "session-start":
        if not inside(cwd):
            try:
                text = recall(cwd)
            except Exception:
                text = ""
            if text:
                print(text)
        return
    if a.event == "hermes":
        extra = payload.get("extra") or {}
        first = extra.get("is_first_turn")
        if first is None:
            first = not extra.get("conversation_history")
        if not first:
            return
        if inside(cwd):
            context = session_context()
        else:
            try:
                context = "\n\n".join(x for x in (pointer(), recall(cwd)) if x)
            except Exception:
                context = pointer()
        print(json.dumps({"context": context}))


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
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("read", help='a page by name, [[link]], alias or path; "Page#Heading" prints one section')
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
    s = sub.add_parser("file", help="move an inbox item into raw/<domain>/ (create-only), fixing links if renamed")
    s.add_argument("path")
    s.add_argument("--domain", required=True)
    s.add_argument("--name", help='new file name, e.g. "2026-09-30 Some Article.md" (default: keep it)')
    s.set_defaults(fn=cmd_file)
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
    s = sub.add_parser("hook", help="agent hooks: session-start (repo recall), session-end (ledger), hermes")
    s.add_argument("event", choices=["session-start", "session-end", "hermes"])
    s.add_argument("--agent", help="claude, codex or hermes (recorded in the session ledger)")
    s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("outline", help="a page's summary and headings, with section sizes")
    s.add_argument("page", nargs="+")
    s.set_defaults(fn=cmd_outline)
    s = sub.add_parser("related", help="backlinks, outlinks, cited sources and pages citing the same sources")
    s.add_argument("page", nargs="+")
    s.set_defaults(fn=cmd_related)
    s = sub.add_parser("harvest", help="agent sessions recorded at session end, for /harvest")
    s.add_argument("--all", action="store_true", help="include trivial sessions")
    s.add_argument("--digest", metavar="ID", help="write the session's digest into inbox/")
    s.add_argument("--done", metavar="ID", help="mark a session harvested")
    s.add_argument("--result", choices=["filed", "skipped"], default="filed")
    s.add_argument("--skip-trivial", action="store_true", help="mark trivial and missing sessions as skipped")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_harvest)
    s = sub.add_parser("backup", help="write a git bundle of the vault (restore with git clone)")
    s.add_argument("--to", help="folder for the bundle (default: backup.dir in .memex/vault.json)")
    s.add_argument("--keep", type=int, help="bundles to keep (default: backup.keep)")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("pull", help="bring in the private remote's commits (vault sessions do this at start)")
    s.add_argument("--merge", action="store_true", help="after a reported conflict: merge, leaving markers to resolve")
    s.set_defaults(fn=cmd_pull)
    sub.add_parser("push", help="push the vault to its private remote (memex commit does this)").set_defaults(fn=cmd_push)
    s = sub.add_parser("remote", help="the vault's private remote: status; set/remove are owner only")
    s.add_argument("action", nargs="?", choices=["status", "set", "remove"], default="status")
    s.add_argument("url", nargs="?")
    s.add_argument("--branch", default="main")
    s.add_argument("--yes", action="store_true", help="don't ask to confirm")
    s.add_argument("--unverified", action="store_true", help="the remote's privacy can't be checked (own server)")
    s.set_defaults(fn=cmd_remote)
    s = sub.add_parser("move", help="move the vault and re-point config, files and agents at it (owner only)")
    s.add_argument("dest")
    s.set_defaults(fn=cmd_move)
    s = sub.add_parser("uninstall", help="remove Memex from this machine (owner only)")
    s.add_argument("--delete-vault", action="store_true", help="also delete the vault, after a backup bundle")
    s.add_argument("--confirm", metavar="NAME", help="the vault's name, to confirm --delete-vault")
    s.set_defaults(fn=cmd_uninstall)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
