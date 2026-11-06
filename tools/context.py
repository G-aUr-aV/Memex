#!/usr/bin/env python3
"""Print the live context a Memex skill needs (used by the skills' !`memex ctx …` lines).

  memex ctx <ingest|inbox|auto-inbox|harvest|verify|lint|today|close|weekly>   (or: python3 tools/context.py …)

One allow-listed command instead of shell pipelines, so skills never abort on a permission
check. Always exits 0 and never writes anything.
"""
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import git, require_vault  # noqa: E402

ROOT = require_vault()
TODAY = datetime.date.today()
NOW = datetime.datetime.now()


def inbox(limit=20, ages=False):
    d = ROOT / "inbox"
    files = sorted((p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime) if d.exists() else []
    print(f"Inbox: {len(files)} item(s)" + (" (oldest first)" if files else ""))
    for p in files[:limit]:
        if ages:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
            try:
                born = datetime.date.fromisoformat(m.group(1)) if m else datetime.date.fromtimestamp(p.stat().st_mtime)
                age = f" ({(TODAY - born).days}d)"
            except ValueError:
                age = ""
            print(f"- {p.name}{age}")
        else:
            print(f"- {p.name}")
    if len(files) > limit:
        print(f"- … {len(files) - limit} more")


def pending_changes(limit=30):
    """Uncommitted changes outside inbox/: memex commit adds everything, so they would join the next commit."""
    r = git(ROOT, "status", "--porcelain", "-z", "--untracked-files=all")
    if r.returncode:
        print("Uncommitted changes outside inbox/: unknown (git status failed)")
        return
    rows, entries = [], iter(r.stdout.split("\0"))
    for e in entries:
        if not e:
            continue
        if e[0] in "RC":
            next(entries, None)  # a rename's or copy's original path
        if not e[3:].startswith("inbox/"):
            rows.append(f"- {e[:2].strip() or '?'} {e[3:]}")
    print(f"Uncommitted changes outside inbox/: {len(rows) or 'none'}", *rows[:limit], sep="\n")
    if len(rows) > limit:
        print(f"- … {len(rows) - limit} more")


def log_entries(since=None, op=None, last=None):
    f = ROOT / "wiki" / "log.md"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^## \[(\d{4}-\d{2}-\d{2})\] (\S+)", line)
        if not m:
            continue
        if since and m.group(1) < since.isoformat():
            continue
        if op and m.group(2) != op:
            continue
        out.append(line)
    return out[-last:] if last else out


def daily(day):
    return ROOT / "journal" / "daily" / f"{day.isoformat()}.md"


def section(path, heading):
    if not path.exists():
        return []
    lines, grab = [], False
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("## "):
            grab = line[3:].strip().lower() == heading.lower()
            continue
        if grab and line.strip() and line.strip() != "-":
            lines.append(line)
    return lines


def harvest(limit=15):
    import sessions
    from memexlib import feature
    minimum = int(feature("harvest", ROOT)["min_tool_calls"])
    pending = [r for r in sessions.load(ROOT) if not r["harvested"]]
    rows = [(r, sessions.digest(r.get("transcript_path"))) for r in pending]
    real = [(r, d) for r, d in rows if d["unparsed"] or (d["tool_calls"] >= minimum and not d["missing"])]
    print(f"Sessions to harvest: {len(real)} (plus {len(rows) - len(real)} trivial or missing)" if rows
          else "Sessions to harvest: none")
    for r, d in real[:limit]:
        first = (d["prompts"][0] if d["prompts"] else "").replace("\n", " ")[:80]
        print(f"- {r['session_id'][:8]} {(d['started'] or r.get('first_ts') or '')[:10]} {r.get('agent', '')} {r.get('repo', '')} "
              f"({d['tool_calls']} tool calls, {len(d['files'])} files): {first!r}")
    if len(real) > limit:
        print(f"- … {len(real) - limit} more")
    unparsed = sum(1 for _, d in real if d["unparsed"])
    if unparsed:
        print(f"WARNING: {unparsed} of these are in a transcript format Memex doesn't recognize (UNPARSED); "
              "tell the owner and leave them for a framework update.")


def as_list(x):
    return [str(i) for i in x] if isinstance(x, list) else ([str(x)] if x else [])


def repo_paths():
    """repo name → its main checkout, from the session ledger (worktree sessions map to the repo they belong to)."""
    import json
    paths = {}
    ledger = ROOT / ".memex" / "sessions.jsonl"
    for line in (ledger.read_text(encoding="utf-8", errors="ignore").splitlines() if ledger.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        cwd = Path(str(r.get("cwd") or ""))
        if not r.get("repo") or not cwd.is_dir() or "/scratch-workspaces/" in str(cwd):
            continue
        common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common.returncode == 0 and common.stdout.strip():
            top = Path(common.stdout.strip()).parent
            paths[top.name.lower()] = str(top)
    return paths


def verify(limit=40):
    """Pages that state current facts about code, least recently verified first, plus where each repo is checked out."""
    from memexlib import frontmatter, iter_files
    rows = []
    for p in iter_files(ROOT / "wiki", (".md",)):
        rel = p.relative_to(ROOT).as_posix()
        if "/sources/" in rel:
            continue
        fm, _ = frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        kind = str(fm.get("type") or "")
        repos = as_list(fm.get("repo")) + as_list(fm.get("repos")) + as_list(fm.get("applies_to"))
        if str(fm.get("status") or "") in ("superseded", "archived") or not (kind == "system" or (repos and kind in (
                "playbook", "project", "concept", "decision"))):
            continue
        seen = str(fm.get("verified_on") or fm.get("verified") or "")
        rows.append((seen or "0000", p.stem, kind, repos, seen or "never", str(fm.get("updated") or "?")))
    rows.sort()
    print(f"Pages with current-state claims about code: {len(rows)} (least recently verified first)")
    for _, name, kind, repos, seen, updated in rows[:limit]:
        print(f"- {name} ({kind}; repo: {', '.join(repos) or 'none set'}; verified: {seen}; updated: {updated})")
    paths = repo_paths()
    print("Local checkouts (from the session ledger):", *(f"- {k}: {v}" for k, v in sorted(paths.items())) or ["none"],
          sep="\n")


def main():
    what = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    print(f"Now: {NOW:%Y-%m-%d %A %H:%M} · ISO week {TODAY.isocalendar()[0]}-W{TODAY.isocalendar()[1]:02d}")
    if what in ("ingest", "inbox"):
        inbox(ages=(what == "inbox"))
    elif what == "auto-inbox":
        inbox(limit=1000, ages=True)
        pending_changes()
    elif what == "lint":
        last = log_entries(op="lint", last=3)
        print("Last lints:", *(last or ["never"]), sep="\n- ")
    elif what == "today":
        f = daily(TODAY)
        print(f"Daily note: journal/daily/{TODAY.isoformat()}.md {'exists' if f.exists() else 'missing (create it from meta/templates/Daily.md)'}")
        y = daily(TODAY - datetime.timedelta(days=1))
        open_tasks = [l for l in (y.read_text(encoding='utf-8', errors='ignore').splitlines() if y.exists() else []) if l.lstrip().startswith("- [ ]")]
        print("Yesterday's open tasks:", *(open_tasks[:15] or ["none"]), sep="\n")
        inbox(limit=5)
    elif what == "close":
        caps = section(daily(TODAY), "Captures")
        print("Today's captures:", *(caps[:30] or ["none"]), sep="\n")
        harvest(limit=5)
        inbox(ages=True)
    elif what == "harvest":
        harvest()
    elif what == "verify":
        verify()
    elif what == "weekly":
        since = TODAY - datetime.timedelta(days=7)
        entries = log_entries(since=since)
        print(f"Log entries since {since.isoformat()}:", *(entries[-40:] or ["none"]), sep="\n")
        notes = sorted(p.name for p in (ROOT / "journal" / "daily").glob("*.md") if p.stem >= since.isoformat())
        print("Daily notes this week:", ", ".join(notes) or "none")
        inbox(limit=5)
    else:
        print("usage: memex ctx <ingest|inbox|auto-inbox|harvest|verify|lint|today|close|weekly>")


def run():
    try:
        main()
    except Exception as e:  # never break a skill invocation
        print(f"(context unavailable: {e})")
    sys.exit(0)


if __name__ == "__main__":
    run()
