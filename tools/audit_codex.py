"""Read Codex JSONL into the audit's common session shape. Never execute transcript text."""
import json
import re

import sessions


def lines(fn):
    with fn.open(encoding="utf-8", errors="ignore") as stream:
        yield from stream


def process_state(raw):
    text = str(raw or "")
    sid = re.search(r'''["']session_id["']\s*:\s*(\d+)|Process running with session ID (\d+)''', text)
    complete = bool(re.search(r'''["']exit_code["']\s*:\s*\d+|Process exited with code''', text))
    return (int(next(x for x in sid.groups() if x)) if sid else None), complete


def output_text(value):
    """Unwrap direct results and code-mode text/JSON envelopes without counting metadata as stdout."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return output_text(parsed) if isinstance(parsed, (dict, list)) else value
    if isinstance(value, list):
        return "\n".join(output_text(x) for x in value)
    if isinstance(value, dict):
        for key in ("output", "content", "value", "text"):
            if key in value:
                return output_text(value[key])
    return ""


def commands(name, args):
    name = str(name).rsplit(".", 1)[-1]
    if name == "exec":
        code = args if isinstance(args, str) else args.get("input", "") if isinstance(args, dict) else ""
        return [sessions._js_string(m.group(1)) for m in sessions.JS_CMD.finditer(code)]
    if not isinstance(args, dict):
        return []
    if name in sessions.SHELL_TOOLS or name.startswith("shell"):
        return [sessions._shell(args.get("command", args.get("cmd")))]
    return []


def parse(fn, timestamp, memex_cmd):
    s = {"agent": "codex", "file": str(fn), "subagent": False, "sid": None, "cwd": None,
         "first": None, "last": None, "usage": {}, "tools": [], "recall": "", "stall": False,
         "block_chars": 0, "warnings": [], "recognized": False}
    uses, results, modern, legacy = {}, {}, {}, []
    for index, line in enumerate(lines(fn)):
        try:
            d = json.loads(line)
        except ValueError:
            s["warnings"].append("malformed JSON line")
            continue
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p, kind = d["payload"], d.get("type")
        when = timestamp(d.get("timestamp") or p.get("timestamp"))
        if when is not None:
            s["first"] = when if s["first"] is None else min(s["first"], when)
            s["last"] = when if s["last"] is None else max(s["last"], when)
        if kind == "session_meta":
            s["recognized"] = True
            s["sid"] = p.get("id") or p.get("session_id")
            s["cwd"] = p.get("cwd")
            s["subagent"] = "subagent" in json.dumps(p.get("source") or p.get("thread_source") or "")
        elif kind == "turn_context":
            s["cwd"] = s["cwd"] or p.get("cwd")
        elif kind == "token_usage_record" and isinstance(p.get("usage"), dict):
            u = p["usage"]
            # Codex input_tokens already includes cached tokens; output_tokens includes reasoning.
            mid = p.get("response_id")
            if mid:
                cur = modern.setdefault(mid, {"ts": when, "in": 0, "out": 0})
                cur["in"] = max(cur["in"], u.get("input_tokens") or 0)
                cur["out"] = max(cur["out"], u.get("output_tokens") or 0)
        elif kind == "event_msg" and p.get("type") == "token_count":
            u = (p.get("info") or {}).get("total_token_usage")
            if isinstance(u, dict):
                legacy.append((when, u.get("input_tokens") or 0, u.get("output_tokens") or 0))
        elif kind == "response_item":
            t = p.get("type")
            if t in ("function_call", "custom_tool_call", "local_shell_call"):
                s["recognized"] = True
                tid = p.get("call_id") or p.get("id") or f"line-{index}"
                args = p.get("arguments", p.get("input", p.get("action", {})))
                if isinstance(args, str) and t != "custom_tool_call":
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                cmds = commands(p.get("name") or t, args)
                code = args if isinstance(args, str) else ""
                polls = re.findall(r"write_stdin\s*\(\s*\{[^{}]*?\bsession_id\s*:\s*(\d+)", code)
                poll = args.get("session_id") if isinstance(args, dict) and str(p.get("name")).endswith("write_stdin") else None
                if not cmds and "exec_command" in code:
                    s["warnings"].append("unrecognized code-mode shell arguments")
                uses[tid] = {"id": tid, "name": p.get("name"), "input": args, "ts": when,
                             "poll_session": int(polls[0]) if len(polls) == 1 else poll,
                             "cmd": "\n".join(cmds), "memex_ops": [m.group(1) for cmd in cmds
                                                                     for m in memex_cmd.finditer(cmd)]}
            elif t in ("function_call_output", "custom_tool_call_output"):
                results[p.get("call_id")] = {"rts": when, "text": output_text(p.get("output")),
                                               "raw_output": p.get("output")}
            elif t == "message" and p.get("role") in ("user", "developer", "system"):
                text = sessions._text(p.get("content"))
                headers = ("## Memex: what the owner's knowledge base says", "## Memex: pages that match this request")
                if any(h in text for h in headers):
                    s["recall"] += "\n" + text
                    s.setdefault("recalls", []).append((when, text))
                if "Memex inbox looks stalled" in text:
                    s["stall"] = True
                begin = text.find("<!-- memex:begin")
                if begin >= 0:
                    end = text.find("<!-- memex:end -->", begin)
                    s["block_chars"] = max(s["block_chars"], len(text[begin:end if end >= 0 else None]))
    s["usage"] = modern
    if not modern:
        previous = (0, 0)
        for when, inp, out in legacy:
            if inp < previous[0] or out < previous[1]:
                continue  # a replay/reset is not another model request
            delta = (inp - previous[0], out - previous[1])
            if any(delta):
                s["usage"][f"total:{s['sid']}:{inp}:{out}"] = {"ts": when, "in": delta[0], "out": delta[1]}
            previous = (inp, out)
    running = {}
    for tid, tool in uses.items():
        result = results.get(tid)
        tool.update(result or {"rts": None, "text": ""})
        sid, complete = process_state((result or {}).get("raw_output"))
        tool["pending"] = result is None or (sid is not None and not complete)
        original = running.get(tool.pop("poll_session", None))
        if original is not None and result is not None:
            original["text"] += "\n" + tool["text"]
            original["rts"] = tool["rts"]
            original["pending"] = not complete
        if sid is not None and tool["pending"] and original is None:
            running[sid] = tool
        tool["memex"] = tool["memex_ops"][0] if tool["memex_ops"] else None
        tool.pop("raw_output", None)
        s["tools"].append(tool)
    s["tools"].sort(key=lambda x: x["ts"] or 0)
    return s
