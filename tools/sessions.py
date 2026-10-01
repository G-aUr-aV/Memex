"""Session ledger and transcript digests for /harvest (stdlib only).

The global SessionEnd hook (`memex hook session-end`) appends one line per agent session run outside the
vault to <vault>/.memex/sessions.jsonl. /harvest turns each worthwhile session into a digest: each turn's
prompt and the agent's last words in that turn, the files changed, commands and commits, *extracted* from
the transcript and never summarized, with secrets redacted. It files that as a raw source and compiles what's durable.

Transcripts: Claude Code (~/.claude/projects/<project>/<session>.jsonl) and Codex (~/.codex/sessions/…jsonl).
Both are read best-effort: unknown line shapes are skipped, never fatal.
"""
import datetime
import fcntl
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memexlib as ml  # noqa: E402
from secretscan import redact  # noqa: E402

LEDGER = Path(".memex") / "sessions.jsonl"
MAX_PROMPTS, MAX_COMMANDS, MAX_FILES, MAX_PROMPT_CHARS, MAX_ANSWER_CHARS, MAX_FINAL_CHARS = 20, 40, 60, 600, 900, 1500
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "write_file"}
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell", "local_shell_call", "terminal", "container.exec"}
WRAPPER = re.compile(r"^\s*<(system-reminder|command-name|command-message|command-args|local-command-stdout|"
                     r"local-command-caveat|user-prompt-submit-hook|environment_context|user_instructions|"
                     r"permissions instructions|task-notification)\b")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
OPEN_TAG = re.compile(r"^\s*<([A-Za-z_][\w-]*)[^>]*>")
# Codex "code mode": tools are called from JavaScript, e.g. tools.exec_command({cmd:"…", workdir:"…"})
JS_CMD = re.compile(r"exec_command\s*\(\s*\{[^{}]*?\bcmd\s*:\s*(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)", re.S)
JS_CALL = re.compile(r"\b(?:exec_command|apply_patch|write_stdin|web__run)\s*\(")
BAD_NAME = re.compile(r"[:\\/#^\[\]|*?\"<>]")


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------- ledger ----------

def repo_of(cwd) -> str:
    top = ml.repo_top(Path(cwd)) if cwd else None
    return (top or Path(cwd or ".")).name


def excluded(cwd: str, patterns) -> bool:
    p = os.path.realpath(os.path.expanduser(cwd))
    for pat in patterns or []:
        q = os.path.realpath(os.path.expanduser(str(pat)))
        if p == q or p.startswith(q.rstrip(os.sep) + os.sep) or fnmatch.fnmatch(p, os.path.expanduser(str(pat))):
            return True
    return False


def append(v: Path, rec: dict):
    path = v / LEDGER
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def record(v: Path, payload: dict, agent: str) -> bool:
    """Append one ended session to the ledger. False when skipped (harvest off, vault session, excluded)."""
    h = ml.feature("harvest", v)
    cwd = str(payload.get("cwd") or os.getcwd())
    if not h.get("enabled") or ml.inside(cwd, v) or excluded(cwd, h.get("exclude")):
        return False
    append(v, {"event": "session", "ts": now(), "agent": agent, "session_id": str(payload.get("session_id") or ""),
               "cwd": cwd, "repo": repo_of(cwd), "transcript_path": str(payload.get("transcript_path") or ""),
               "reason": str(payload.get("reason") or "")})
    return True


def load(v: Path):
    """Sessions from the ledger, oldest first: the latest record per session id, plus its harvest record."""
    sessions, done = {}, {}
    path = v / LEDGER
    for line in (path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        sid = rec.get("session_id") or ""
        if not sid:
            continue
        if rec.get("event") == "session":
            first = sessions.get(sid, {}).get("first_ts", rec.get("ts"))
            sessions[sid] = {**rec, "first_ts": first}
        elif rec.get("event") == "harvested":
            done[sid] = rec
    return sorted(({**r, "harvested": done.get(s)} for s, r in sessions.items()), key=lambda r: r.get("first_ts") or "")


def find(v: Path, prefix: str):
    hits = [r for r in load(v) if r["session_id"].startswith(prefix)]
    if len(hits) != 1:
        raise ml.VaultError(f"{'no' if not hits else 'several'} session(s) match {prefix!r}; use more of the id "
                            "(memex harvest lists them)")
    return hits[0]


def mark(v: Path, session_id: str, result: str):
    append(v, {"event": "harvested", "ts": now(), "session_id": session_id, "result": result})


# ---------- transcripts ----------

def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text"))
    return ""


def _objects(path: Path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _js_string(lit: str) -> str:
    """Decode a JavaScript string literal well enough for a digest."""
    if lit.startswith('"'):
        try:
            return json.loads(lit)
        except ValueError:
            pass
    body = lit[1:-1]
    return body.replace("\\n", "\n").replace("\\t", "\t").replace("\\'", "'").replace('\\"', '"')


def unwrap(t: str) -> str:
    """Drop injected blocks at the start of a message (<environment_context>…</environment_context>,
    <command-name>…</command-name>, …) and return what the person actually typed, if anything."""
    while True:
        m = OPEN_TAG.match(t)
        close = f"</{m.group(1)}>" if m else ""
        if not m or close not in t:
            return t.strip()
        t = t[t.index(close) + len(close):]


def _shell(cmd) -> str:
    if isinstance(cmd, list):
        cmd = [str(c) for c in cmd]
        if len(cmd) >= 3 and os.path.basename(cmd[0]) in ("bash", "sh", "zsh") and cmd[1].startswith("-"):
            return cmd[2]
        return " ".join(cmd)
    return str(cmd or "")


def digest(path) -> dict:
    """Extract what a session did. Never raises for odd content; a missing file gives an empty digest."""
    d = {"prompts": [], "turns": [], "files": [], "commands": [], "commits": [], "final": "", "tool_calls": 0,
         "started": "", "ended": "", "branch": "", "missing": False, "unparsed": False}
    p = Path(path) if path else None
    if not p or not p.is_file():
        d["missing"] = True
        return d

    def prompt(t):
        t = unwrap(t or "")
        if not t or WRAPPER.match(t) or (d["prompts"] and d["prompts"][-1] == t[:MAX_PROMPT_CHARS]):
            return
        if len(d["prompts"]) < MAX_PROMPTS:
            d["prompts"].append(t[:MAX_PROMPT_CHARS] + ("…" if len(t) > MAX_PROMPT_CHARS else ""))
            d["turns"].append({"prompt": d["prompts"][-1], "answer": ""})

    def answer(t):
        t = (t or "").strip()
        if not t:
            return
        d["final"] = t
        if d["turns"]:  # the agent's last words in each turn usually hold the finding, not just the final reply
            d["turns"][-1]["answer"] = t[:MAX_ANSWER_CHARS] + ("…" if len(t) > MAX_ANSWER_CHARS else "")

    def add_file(f):
        f = str(f or "").strip()
        if f and f not in d["files"] and len(d["files"]) < MAX_FILES:
            d["files"].append(f)

    def shell_cmd(cmd):
        cmd = cmd.strip()
        if not cmd:
            return
        short = cmd.splitlines()[0][:300] + (" …" if len(cmd) > 300 or "\n" in cmd else "")
        if "git commit" in cmd:
            d["commits"].append(short)
        elif len(d["commands"]) < MAX_COMMANDS:
            d["commands"].append(short)

    def code_mode(src):
        calls = len(JS_CALL.findall(src)) or 1
        d["tool_calls"] += calls - 1  # the caller counted one already
        for m in JS_CMD.finditer(src):
            shell_cmd(_js_string(m.group(1)))
        if "*** Begin Patch" in src:
            for f in PATCH_FILE.findall(src.replace("\\n", "\n")):
                add_file(f.strip().strip("\"'`"))

    def tool(name, inp):
        d["tool_calls"] += 1
        name = str(name or "")
        if name == "exec" and isinstance(inp, dict) and isinstance(inp.get("input"), str):
            code_mode(inp["input"])
            return
        if not isinstance(inp, dict):
            inp = {"input": inp}
        text = str(inp.get("input") or inp.get("patch") or inp.get("command") or "")
        if name in EDIT_TOOLS:
            add_file(inp.get("file_path") or inp.get("path") or inp.get("notebook_path"))
        elif name in ("apply_patch", "patch") or "*** Begin Patch" in text:
            for f in PATCH_FILE.findall(text):
                add_file(f)
        elif name in SHELL_TOOLS or name.startswith("shell"):
            shell_cmd(_shell(inp.get("command") if inp.get("command") is not None else inp.get("cmd")))

    records = known = 0
    for obj in _objects(p):
        records += 1
        ts = str(obj.get("timestamp") or "")
        if ts:
            d["started"] = d["started"] or ts
            d["ended"] = ts
        if isinstance(obj.get("message"), dict) and obj.get("type") in ("user", "assistant"):  # Claude Code
            known += 1
            if obj.get("isSidechain") or obj.get("isMeta"):
                continue
            d["branch"] = obj.get("gitBranch") or d["branch"]
            msg = obj["message"]
            content = msg.get("content")
            if msg.get("role") == "user":
                if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                prompt(_text(content))
            elif msg.get("role") == "assistant" and isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        answer(str(b.get("text", "")))
                    elif b.get("type") == "tool_use":
                        tool(b.get("name"), b.get("input") or {})
        elif obj.get("type") == "response_item" and isinstance(obj.get("payload"), dict):  # Codex
            known += 1
            pl = obj["payload"]
            kind = pl.get("type")
            if kind == "message":
                t = _text(pl.get("content"))
                if pl.get("role") == "user":
                    prompt(t)
                elif pl.get("role") == "assistant":
                    answer(t)
            elif kind in ("function_call", "custom_tool_call", "local_shell_call"):
                args = pl.get("arguments") or pl.get("input") or pl.get("action") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"input": args}
                tool(pl.get("name") or kind, args)
    d["final"] = d["final"][:MAX_FINAL_CHARS] + ("…" if len(d["final"]) > MAX_FINAL_CHARS else "")
    d["unparsed"] = records >= 5 and not known  # a format this parser doesn't know: never treat it as trivial
    return d


def render(rec: dict, d: dict, domain: str):
    """(file name, markdown, redacted labels) for a session digest, ready for inbox/."""
    short = rec["session_id"][:8]
    date = (d["started"] or rec.get("first_ts") or now())[:10]
    title = re.sub(r"\s+", " ", BAD_NAME.sub(" ", f"Session {rec.get('repo') or 'unknown'} {short}")).strip()
    fm = ["---", f"title: {json.dumps(title)}", f"source: {json.dumps(rec.get('agent', 'agent') + ' session ' + rec['session_id'])}",
          "source_type: session", f"published: {date}", f"captured: {datetime.date.today().isoformat()}",
          f"domain: {domain}", f"repo: {json.dumps(rec.get('repo') or '')}", f"agent: {rec.get('agent', '')}",
          f"session_id: {json.dumps(rec['session_id'])}", f"tool_calls: {d['tool_calls']}",
          "why: \"harvested automatically from an agent session\"", "tags: [session, capture]", "---", ""]
    body = [f"# {title}", "",
            "> Extracted from the session transcript by `memex harvest` (verbatim excerpts, not a summary).", "",
            "## Session",
            f"- agent: {rec.get('agent', '')} · repo: {rec.get('repo', '')} · branch: {d['branch'] or '-'}",
            f"- folder: {rec.get('cwd', '')}",
            f"- from {d['started'] or '?'} to {d['ended'] or '?'} · {d['tool_calls']} tool calls", ""]
    for i, turn in enumerate(d["turns"], 1):
        body += [f"## Turn {i}", f"**Owner:** {turn['prompt']}", ""]
        if turn["answer"]:
            body += [f"**Agent:** {turn['answer']}", ""]
    if not d["turns"] and d["final"]:
        body += ["## Final message", d["final"], ""]
    sections = [("Files changed", [f"- `{f}`" for f in d["files"]]),
                ("Commands", [f"- `{c}`" for c in d["commands"]]),
                ("Commits", [f"- `{c}`" for c in d["commits"]])]
    for heading, lines in sections:
        if lines:
            body += [f"## {heading}", *lines, ""]
    text, labels = redact("\n".join(fm + body))
    return f"{date} {title}.md", text, labels
