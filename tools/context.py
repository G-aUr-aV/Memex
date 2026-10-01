#!/usr/bin/env python3
"""Print the live context a Memex skill needs (used by the skills' !`memex ctx …` lines).

  memex ctx <ingest|inbox|harvest|lint|today|close|weekly>      (or: python3 tools/context.py …)

One allow-listed command instead of shell pipelines, so skills never abort on a permission
check. Always exits 0 and never writes anything.
"""
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import require_vault  # noqa: E402

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


def main():
    what = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    print(f"Now: {NOW:%Y-%m-%d %A %H:%M} · ISO week {TODAY.isocalendar()[0]}-W{TODAY.isocalendar()[1]:02d}")
    if what in ("ingest", "inbox"):
        inbox(ages=(what == "inbox"))
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
    elif what == "weekly":
        since = TODAY - datetime.timedelta(days=7)
        entries = log_entries(since=since)
        print(f"Log entries since {since.isoformat()}:", *(entries[-40:] or ["none"]), sep="\n")
        notes = sorted(p.name for p in (ROOT / "journal" / "daily").glob("*.md") if p.stem >= since.isoformat())
        print("Daily notes this week:", ", ".join(notes) or "none")
        inbox(limit=5)
    else:
        print("usage: memex ctx <ingest|inbox|harvest|lint|today|close|weekly>")


def run():
    try:
        main()
    except Exception as e:  # never break a skill invocation
        print(f"(context unavailable: {e})")
    sys.exit(0)


if __name__ == "__main__":
    run()
