#!/usr/bin/env python3
"""memex audit: is Memex earning its keep?

  memex audit [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--days N] [--json] [--lookups] [--report] [--record]

Reads Claude Code and Codex transcripts and the vault. It reports deterministic
measures for agent sessions outside the vault and the framework:
- use: lookups, captures, the duplicate gate;
- retrieval: zero-hit and partial-only queries, follow-up reads;
- recall: pages opened after session start;
- re-discovery: the same topic captured again;
- cost: Memex's share of tokens;
- latency: time spent writing captures;
- pipeline health and a search benchmark: `meta/audit/search-benchmark.tsv` in the vault, lines of
  `query<TAB>expected page title`.

It's read-only except for:
- `--report`, which writes `meta/audit/<date>.md` in the vault;
- `--record`, which appends the key numbers to `.memex/audit.jsonl` (machine-local), so the next audit shows the trend.

Whether a lookup actually helped needs a reader. `--lookups` lists every lookup with its output, for the /audit
skill to sample and judge. Transcript adapters are best-effort; coverage and incomplete results are reported.
"""
import argparse
import datetime
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memexlib as ml  # noqa: E402
import memex as mx  # noqa: E402
import audit_codex  # noqa: E402
from secretscan import redact  # noqa: E402

MEMEX_CMD = re.compile(r"(?:^|[\s/;&|(`'\"])(?:memex|memex\.py['\"]?)\s+([a-z][\w-]*)")
CHARS_PER_TOKEN_TOOL, CHARS_PER_TOKEN_PROSE = 2.4, 3.2  # calibrated on tool results; prose estimated
KEYS = ("sessions", "lookup_pct", "capture_pct", "captures", "reads", "captures_per_read", "zero_hit_pct",
        "partial_only_pct", "recall_opened_pct", "recaptured", "gate_refusals", "memex_token_pct",
        "capture_seconds_median", "bench_hit1", "bench_hit3")


def ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text", ""))
        elif isinstance(c, dict) and c.get("type") == "tool_result":
            out.append(text_of(c.get("content")))
    return "\n".join(out)


def transcript_roots():
    codex = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    roots = [("claude", Path(os.environ.get("MEMEX_TRANSCRIPTS") or Path.home() / ".claude/projects"))]
    override = os.environ.get("MEMEX_CODEX_TRANSCRIPTS")
    roots += [("codex", Path(override))] if override else [("codex", codex / name)
                                                        for name in ("sessions", "archived_sessions")]
    return roots


def parse(fn: Path, agent="claude") -> dict:
    """One transcript: who, where, when, token usage per API call, tool calls with results, Memex hook output."""
    if agent == "codex":
        return audit_codex.parse(fn, ts, MEMEX_CMD)
    s = {"agent": "claude", "file": str(fn), "subagent": "/subagents/" in str(fn), "sid": None,
         "cwd": None, "first": None, "last": None, "usage": {}, "tools": [], "recall": "",
         "stall": False, "block_chars": 0, "recognized": False, "warnings": []}
    uses, results = {}, {}
    for line in audit_codex.lines(fn):
        try:
            d = json.loads(line)
        except ValueError:
            s["warnings"].append("malformed JSON line")
            continue
        if not isinstance(d, dict):
            continue
        t, when = d.get("type"), ts(d["timestamp"]) if d.get("timestamp") else None
        s["recognized"] |= t in ("user", "assistant", "attachment")
        if when:
            s["first"] = when if s["first"] is None else min(s["first"], when)
            s["last"] = when if s["last"] is None else max(s["last"], when)
        s["sid"] = s["sid"] or d.get("sessionId")
        s["cwd"] = s["cwd"] or d.get("cwd")
        if t == "attachment":
            a = d.get("attachment") or {}
            if a.get("type") == "hook_success" and str(a.get("hookName", "")).startswith(("SessionStart", "UserPromptSubmit")):
                c = a.get("content") or ""
                if "## Memex: what the owner's knowledge base says" in c or "## Memex: pages that match this request" in c:
                    s["recall"] += "\n" + c
                if "Memex inbox looks stalled" in c:
                    s["stall"] = True
            elif a.get("type") == "instructions":
                for f in a.get("files") or []:
                    c = f.get("content") or ""
                    if "## Memex" in c:
                        s["block_chars"] = len(c[c.find("## Memex"):])
        elif t == "assistant":
            m = d.get("message") or {}
            mid = m.get("id") or d.get("uuid")
            u = m.get("usage") or {}
            if u:
                cur = s["usage"].setdefault(mid, {"ts": when, "in": 0, "out": 0})
                cur["in"] = max(cur["in"], (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                                + (u.get("cache_creation_input_tokens") or 0))
                cur["out"] = max(cur["out"], u.get("output_tokens") or 0)
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    uses[c["id"]] = {"id": c["id"], "name": c.get("name"), "input": c.get("input") or {}, "ts": when}
        elif t == "user":
            for c in (d.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    results[c.get("tool_use_id")] = {"rts": when, "text": text_of(c.get("content"))}
    for tid, u in uses.items():
        u.update(results.get(tid) or {"rts": None, "text": ""})
        u["pending"] = tid not in results
        cmd = u["input"].get("command", "") if u["name"] == "Bash" else ""
        m = MEMEX_CMD.search(cmd) if cmd else None
        u["memex"] = m.group(1) if m else None
        u["memex_ops"] = [m.group(1) for m in MEMEX_CMD.finditer(cmd)]
        u["cmd"] = cmd
        s["tools"].append(u)
    s["tools"].sort(key=lambda x: x["ts"] or 0)
    return s


def inside(path, root) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)


def load(v: Path, since: float, until: float, coverage=None):
    coverage = coverage if coverage is not None else {}
    coverage.update({"roots": [], "unreadable": 0, "unsupported": 0, "parse_warnings": 0})
    out = []
    seen_files = set()
    for agent, root in transcript_roots():
        coverage["roots"].append({"agent": agent, "path": str(root), "exists": root.is_dir()})
        for fn in root.rglob("*.jsonl") if root.is_dir() else []:
            key = (agent, str(fn.resolve()))
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                if fn.stat().st_mtime < since:
                    continue
                s = parse(fn, agent)
            except OSError:
                coverage["unreadable"] += 1
                continue
            except (ValueError, TypeError, KeyError, AttributeError):
                coverage["unsupported"] += 1
                continue  # a changed transcript shape cannot break the rest of the audit
            coverage["parse_warnings"] += len(s["warnings"])
            if not s["recognized"]:
                coverage["unsupported"] += 1
                continue
            if s["first"] is None or not (since <= s["first"] < until) or not isinstance(s["cwd"], str):
                continue
            if inside(s["cwd"], v):
                s["kind"] = "vault"
            elif inside(s["cwd"], ml.FRAMEWORK):
                s["kind"] = "framework"
            else:
                s["kind"] = "work"
            out.append(s)
    out.sort(key=lambda s: s["first"])
    seen_tools, seen_msgs = set(), set()  # resumed and forked sessions copy history into new files
    for s in out:
        s["tools_unique"] = [t for t in s["tools"] if (s["agent"], t["id"]) not in seen_tools]
        seen_tools |= {(s["agent"], t["id"]) for t in s["tools"]}
        s["usage_unique"] = {k: u for k, u in s["usage"].items() if (s["agent"], k) not in seen_msgs}
        seen_msgs |= {(s["agent"], k) for k in s["usage"]}
    return out


def operations(tool):
    return tool.get("memex_ops") or ([tool["memex"]] if tool.get("memex") else [])


def session_key(s):
    return s.get("agent", "claude"), s["sid"] or s["file"]


def title_of(cmd: str) -> str:
    m = re.search(r"--title\s+([\"'])(.*?)\1", cmd)
    return m.group(2) if m else ""


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def measure(sessions, v: Path) -> dict:
    work = [s for s in sessions if s["kind"] == "work"]
    main = {}
    for s in work:
        if not s["subagent"]:
            main.setdefault(session_key(s), []).append(s)
    calls = [(s, t) for s in work for t in s["tools_unique"] if t["memex"]]
    looked = {sid for sid, ss in main.items() if any(set(operations(t)) & {"search", "read"} for s in ss for t in s["tools"])}
    captured = {sid for sid, ss in main.items() if any("capture" in operations(t) for s in ss for t in s["tools"])}
    caps = [(s, t) for s, t in calls if "capture" in operations(t)]
    refused = [x for x in caps if "capture refused: Memex may already have this" in x[1]["text"]]
    written = [x for x in caps if not x[1].get("pending") and re.search(r"(?m)^inbox/[^\n]+\.md\s*$", x[1]["text"])]
    searches = [(s, t) for s, t in calls if "search" in operations(t)]
    reads = [(s, t) for s, t in calls if "read" in operations(t)]
    queries = sum(operations(t).count("search") for _, t in searches)
    zero = sum(t["text"].count("No matches for") for _, t in searches)
    partial = sum(t["text"].count("no page has every term") for _, t in searches)
    followed = 0
    for ss in main.values():
        ops = [op for s in ss for t in s["tools_unique"] for op in operations(t)]
        followed += "search" in ops and "read" in ops[ops.index("search") + 1:]
    # recall: did the session open a page it was given?
    recalled = [ss for ss in main.values() if any(s["recall"] for s in ss)]
    opened = 0
    for ss in recalled:
        pages = set(re.findall(r"\[\[([^\]]+)\]\]", "\n".join(s["recall"] for s in ss)))
        asked = " ".join(t["cmd"] for s in ss for t in s["tools"] if set(operations(t)) & {"read", "search"})
        opened += any(p in asked for p in pages)
    # re-discovery: a capture whose title says what an earlier capture (from another session) already said
    recaptured, clusters = 0, {}
    earlier = []
    for s, t in sorted(written, key=lambda x: x[1]["ts"] or 0):
        if re.search(r"--action\s+(update|supersede|delete)", t["cmd"]):
            continue
        title = title_of(t["cmd"])
        words = mx.content_words(title)
        hit = next((e for e in earlier if e[1] != session_key(s) and words and len(words & e[0]) / len(words) >= 0.6), None)
        if hit:
            recaptured += 1
            clusters[hit[2]] = clusters.get(hit[2], 1) + 1
        earlier.append((words, session_key(s), title))
    # cost (tokens): Memex text in context is re-read by every later API call in the same transcript
    total_in = sum(u["in"] for s in work for u in s["usage_unique"].values())
    total_out = sum(u["out"] for s in work for u in s["usage_unique"].values())
    memex_in = memex_out = 0.0
    for s in work:
        calls_ts = sorted(u["ts"] or 0 for u in s["usage_unique"].values())
        n = len(calls_ts)
        memex_in += (s["block_chars"] + len(s["recall"])) / CHARS_PER_TOKEN_PROSE * n
        for t in s["tools_unique"]:
            if not t["memex"]:
                continue
            later = sum(1 for c in calls_ts if c > (t["rts"] or t["ts"] or 0))
            size = (len(t["cmd"]) + len(t["text"])) / CHARS_PER_TOKEN_TOOL
            memex_in += size * later
            memex_out += len(t["cmd"]) / CHARS_PER_TOKEN_TOOL
    # latency: the model's time composing each capture (gap after the previous tool result)
    gaps = []
    for s, t in written:
        prev = [x["rts"] for x in s["tools"] if x["rts"] and t["ts"] and x["rts"] <= t["ts"] and x["id"] != t["id"]]
        if prev and 0 <= t["ts"] - max(prev) < 600:
            gaps.append(t["ts"] - max(prev))
    n_main = len(main)
    capture_count = sum(len(re.findall(r"(?m)^inbox/[^\n]+\.md\s*$", t["text"])) for _, t in written)
    read_count = sum(operations(t).count("read") for _, t in reads)
    return {
        "sessions": n_main, "transcripts": len(work),
        "vault_sessions": len({session_key(s) for s in sessions if s["kind"] == "vault" and not s["subagent"]}),
        "lookup_sessions": len(looked), "lookup_pct": pct(len(looked), n_main),
        "capture_sessions": len(captured), "capture_pct": pct(len(captured), n_main),
        "capture_only_sessions": len(captured - looked),
        "captures": capture_count,
        "gate_refusals": sum(t["text"].count("capture refused: Memex may already have this") for _, t in refused),
        "searches": len(searches), "queries": queries, "reads": read_count,
        "captures_per_read": round(capture_count / read_count, 1) if read_count else None,
        "zero_hit_pct": pct(zero, queries), "partial_only_pct": pct(partial, queries),
        "search_then_read_sessions": followed,
        "recall_sessions": len(recalled), "recall_opened_pct": pct(opened, len(recalled)),
        "stall_alarms": sum(1 for ss in main.values() for s in ss if s["stall"]),
        "recaptured": recaptured,
        "recaptured_topics": sorted(clusters.items(), key=lambda x: -x[1])[:5],
        "memex_token_pct": pct(memex_in, total_in), "memex_output_pct": pct(memex_out, total_out),
        "capture_seconds_median": round(statistics.median(gaps), 1) if gaps else None,
        "capture_minutes_total": round(sum(gaps) / 60, 1),
        "incomplete_memex_calls": sum(bool(t.get("pending")) for _, t in calls),
        "unconfirmed_captures": sum(x not in written and x not in refused and not x[1].get("pending") for x in caps),
        "sessions_without_usage": sum(not any(s["usage"] for s in ss) for ss in main.values()),
    }


def bench(v: Path):
    f = v / "meta" / "audit" / "search-benchmark.tsv"
    if not f.exists():
        return None
    rows = [l.split("\t") for l in f.read_text(encoding="utf-8").splitlines() if "\t" in l and not l.startswith("#")]
    hit1 = hit3 = 0
    misses = []
    for query, want in ((q.strip(), w.strip()) for q, w in rows):
        _, hits, _ = mx.rank(v, query, "default")
        hits, _ = mx.fold_covered(hits)
        titles = [d["title"] for _, d in hits]
        r = titles.index(want) + 1 if want in titles else None
        hit1 += r == 1
        hit3 += bool(r and r <= 3)
        if not (r and r <= 3):
            misses.append(f"{query!r} → {want} (rank {r or '-'})")
    return {"queries": len(rows), "hit1": hit1, "hit3": hit3, "misses": misses}


def health(v: Path) -> dict:
    pages = [p for p in ml.iter_files(v / "wiki", (".md",)) if ml.rel(p) not in ml.special(v)]
    conflicts = unreviewed = systems = verified = 0
    for p in pages:
        fm, body = ml.frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
        conflicts += "> [!conflict]" in body
        unreviewed += not fm.get("reviewed")
        if str(fm.get("type")) == "system" and str(fm.get("status")) not in ("superseded", "archived"):
            systems += 1
            verified += bool(fm.get("verified_on"))
    inbox = [p for p in (v / "inbox").glob("*") if p.is_file() and not p.name.startswith(".")]
    stall = mx.inbox_stall(v)
    hot = ml.git(v, "log", "-1", "--format=%cs", "--", "wiki/hot.md").stdout.strip()
    try:
        import vault as vaultmod
        backup = vaultmod.backup_age(v)
    except Exception:
        backup = None
    return {"pages": len(pages), "unreviewed": unreviewed, "open_conflicts": conflicts,
            "system_pages": systems, "system_pages_verified": verified, "inbox": len(inbox),
            "inbox_stalled_since": f"{stall[1]:%Y-%m-%d}" if stall else None, "half_filed_raw": len(mx.half_filed(v)),
            "hot_md_rewritten": hot or None, "backup_age_days": backup,
            "last_commit": ml.git(v, "log", "-1", "--format=%cs").stdout.strip() or None}


def previous(v: Path, window: str):
    f = v / ".memex" / "audit.jsonl"
    rows = []
    for line in (f.read_text(encoding="utf-8").splitlines() if f.exists() else []):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    rows = [r for r in rows if r.get("window") != window and r.get("schema_version") == 2]
    return rows[-1] if rows else None


def render(r: dict) -> str:
    m, h, b, p = r["metrics"], r["health"], r["bench"], r["previous"]

    def trend(key, unit=""):
        if not p or p.get(key) is None or m.get(key) is None:
            return ""
        return f" (was {p[key]}{unit} in {p['window']})"

    def val(x, unit=""):
        return "n/a" if x is None else f"{x}{unit}"
    out = [f"# Memex audit {r['generated']} · sessions started {r['window']}", "",
           "## Use", f"- Work sessions: {m['sessions']} ({m['transcripts']} transcripts with subagents); vault upkeep "
           f"sessions: {m['vault_sessions']}",
           f"- Looked something up: {m['lookup_sessions']} ({val(m['lookup_pct'], '%')}){trend('lookup_pct', '%')}; "
           f"captured: {m['capture_sessions']} ({val(m['capture_pct'], '%')}){trend('capture_pct', '%')}; captured "
           f"without looking anything up: {m['capture_only_sessions']}",
           f"- Captures written: {m['captures']}{trend('captures')}; refused by the duplicate gate: "
           f"{m['gate_refusals']}; full page reads: {m['reads']}; captures per read: {val(m['captures_per_read'])}"
           f"{trend('captures_per_read')}",
           "", "## Retrieval",
           f"- Search queries: {m['queries']}; no hits: {val(m['zero_hit_pct'], '%')}{trend('zero_hit_pct', '%')}; "
           f"only partial matches: {val(m['partial_only_pct'], '%')}{trend('partial_only_pct', '%')}; sessions that "
           f"read a page after searching: {m['search_then_read_sessions']}",
           f"- Session-start recall: {m['recall_sessions']} sessions; a recalled page opened later: "
           f"{val(m['recall_opened_pct'], '%')}{trend('recall_opened_pct', '%')}; stall alarms shown: {m['stall_alarms']}"]
    if b:
        out.append(f"- Search benchmark: hit@1 {b['hit1']}/{b['queries']}{trend('bench_hit1')}, hit@3 "
                   f"{b['hit3']}/{b['queries']}{trend('bench_hit3')}"
                   + (f"; misses: {'; '.join(b['misses'][:5])}" if b["misses"] else ""))
    out += ["", "## Re-discovery",
            f"- Captures repeating an earlier capture from another session: {m['recaptured']}{trend('recaptured')}"
            + (f"; top: {', '.join(f'{t} (x{n})' for t, n in m['recaptured_topics'])}" if m["recaptured_topics"] else ""),
            "", "## Cost and latency",
            f"- Memex share of input-side tokens: ≈{val(m['memex_token_pct'], '%')}{trend('memex_token_pct', '%')}; "
            f"of output tokens: ≈{val(m['memex_output_pct'], '%')}",
            f"- Time spent writing captures: median {val(m['capture_seconds_median'], ' s')} each, "
            f"{m['capture_minutes_total']} min in total",
            "", "## Agent coverage",
            *[f"- {agent}: {metrics['sessions']} work sessions, {metrics['queries']} search queries, "
              f"{metrics['reads']} reads, {metrics['captures']} captures, "
              f"{metrics['incomplete_memex_calls']} incomplete Memex calls, "
              f"{metrics['unconfirmed_captures']} unconfirmed captures, "
              f"{metrics['sessions_without_usage']} sessions without token usage"
              for agent, metrics in r.get("agents", {}).items()],
            f"- Transcript coverage: {r.get('coverage', {}).get('unsupported', 0)} unsupported, "
            f"{r.get('coverage', {}).get('unreadable', 0)} unreadable, "
            f"{r.get('coverage', {}).get('parse_warnings', 0)} parse warnings. "
            "Token shares and capture latency are estimates; incomplete results do not prove success.",
            "", "## Pipeline (now)",
            f"- Inbox: {h['inbox']} item(s)" + (f", STALLED since {h['inbox_stalled_since']}" if h["inbox_stalled_since"] else "")
            + (f"; {h['half_filed_raw']} raw file(s) filed but not committed" if h["half_filed_raw"] else "")
            + f"; last vault commit {h['last_commit']}",
            f"- Pages: {h['pages']}; unreviewed by the owner: {h['unreviewed']}; open conflicts: {h['open_conflicts']}; "
            f"system pages verified against code: {h['system_pages_verified']}/{h['system_pages']}",
            f"- hot.md last rewritten: {h['hot_md_rewritten'] or 'never'}; backup: "
            + ("none" if h["backup_age_days"] is None else f"{h['backup_age_days']} days old")]
    out += ["", "Whether lookups actually helped needs a reader: `memex audit --lookups` lists them for /audit to judge."]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="memex audit")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lookups", action="store_true", help="list every lookup in the window with its output")
    ap.add_argument("--report", action="store_true", help="also write meta/audit/<date>.md in the vault")
    ap.add_argument("--record", action="store_true", help="append the key numbers to .memex/audit.jsonl")
    a = ap.parse_args(argv)
    v = ml.vault()
    today = datetime.date.today()
    until = datetime.date.fromisoformat(a.until) if a.until else today
    since = datetime.date.fromisoformat(a.since) if a.since else until - datetime.timedelta(days=a.days - 1)
    start = datetime.datetime.combine(since, datetime.time()).timestamp()
    end = datetime.datetime.combine(until + datetime.timedelta(days=1), datetime.time()).timestamp()
    window = f"{since.isoformat()}..{until.isoformat()}"
    coverage = {}
    sessions = load(v, start, end, coverage)
    if a.lookups:
        for s in sessions:
            if s["kind"] != "work":
                continue
            for t in s["tools_unique"]:
                if set(operations(t)) & {"search", "read"}:
                    when = datetime.datetime.fromtimestamp(t["ts"]).strftime("%Y-%m-%d %H:%M") if t["ts"] else "?"
                    cmd, output = redact(t["cmd"])[0][:400], redact(t["text"])[0][:1200]
                    excerpt = f"## {when} {s['agent']} {Path(s['cwd']).name} · {s['file']} · tool {t['id']}\n$ {cmd}\n{output}\n"
                    print(redact(excerpt)[0])
        return 0
    m = measure(sessions, v)
    b = bench(v)
    m["bench_hit1"], m["bench_hit3"] = (b["hit1"], b["hit3"]) if b else (None, None)
    r = {"generated": today.isoformat(), "window": window, "metrics": m, "health": health(v), "bench": b,
         "previous": previous(v, window), "coverage": coverage,
         "agents": {agent: measure([s for s in sessions if s["agent"] == agent], v)
                    for agent in ("claude", "codex")}}
    if a.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(render(r))
    if a.report:
        dest = v / "meta" / "audit" / f"{today.isoformat()}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("---\ntype: audit-report\n---\n" + render(r) + "\n", encoding="utf-8")
        print(f"report written: {ml.rel(dest)}")
    if a.record:
        line = {"date": today.isoformat(), "window": window, "schema_version": 2,
                "agent_coverage": ["claude", "codex"], **{k: m.get(k) for k in KEYS}}
        with open(v / ".memex" / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        print("recorded in .memex/audit.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
