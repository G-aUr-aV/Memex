#!/usr/bin/env python3
"""Wire the configured vault into the coding agents on this machine, so they read and write Memex from any
project without permission prompts. Run by setup.sh; safe to re-run.

  python3 tools/integrate.py                         # every agent found (claude, codex, hermes)
  python3 tools/integrate.py --agents claude,codex   # just these
  python3 tools/integrate.py --remove                # undo what a previous run added

What it adds (and records in ~/.config/memex/integration.json so re-runs and --remove are clean):
  all      ~/.local/bin/memex, a shim for the framework's tools/memex.py
  claude   ~/.claude/skills/memex · a Memex block in ~/.claude/CLAUDE.md · ~/.claude/settings.json:
           allow `memex`, reads of the vault and edits of wiki/ inbox/ outputs/, plus the guard hook
  codex    ~/.agents/skills/memex · a Memex block in ~/.codex/AGENTS.md · ~/.codex/rules/memex.rules
           (allow `memex`) · the guard hook in ~/.codex/hooks.json · ~/.codex/config.toml: trust the
           vault and add it to sandbox_workspace_write.writable_roots
  hermes   ~/.hermes/skills/memex · a Memex block in ~/.hermes/config.yaml (guard on pre_tool_call,
           context on pre_llm_call) · `hermes skills trust <vault>` for the vault's own skills
Hooks and the shim run code from the framework; permissions and trust point at the vault. One vault per
machine: re-running setup with another vault moves the wiring there.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memex  # noqa: E402
import memexlib as ml  # noqa: E402

ROOT = ml.require_vault()
V = str(ROOT)
NAME = str(ml.settings(ROOT).get("name") or ROOT.name)
DOMAINS = ml.domain_names(ROOT)
FW = str(ml.FRAMEWORK)
HOME = Path.home()
STATE = HOME / ".config" / "memex" / "integration.json"
SHIM = HOME / ".local" / "bin" / "memex"
MARK = "Managed by Memex (tools/integrate.py)"
MD_BEGIN, MD_END = "<!-- memex:begin (managed by Memex setup; re-run the framework's setup.sh to update) -->", "<!-- memex:end -->"
Y_BEGIN, Y_END = "# memex:begin (managed by Memex setup; re-run the framework's setup.sh to update)", "# memex:end"
GUARD = ml.guard_command()
HERMES_CONTEXT = ml.memex_command() + " hook hermes"
CLAUDE_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
CODEX_MATCHER = "apply_patch|Edit|Write|Bash"
notes = []


def say(msg):
    print(f"  {msg}")


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def set_block(path, begin, end, body):
    """Insert, replace or (body=None) remove a marker-delimited block in a text file."""
    text = path.read_text() if path.exists() else ""
    pat = re.compile(re.escape(begin.split(" (")[0]) + r".*?" + re.escape(end) + r"\n?", re.S)
    text = pat.sub("", text).rstrip("\n")
    if body is not None:
        text = (text + "\n\n" if text else "") + f"{begin}\n{body.strip()}\n{end}"
    if text or path.exists():
        write(path, text + "\n" if text else "")


def skill_text():
    t = (ml.FRAMEWORK / "tools" / "memex.SKILL.md").read_text()
    return (t.replace("{{VAULT}}", V).replace("{{VAULT_NAME}}", NAME).replace("{{FRAMEWORK}}", FW)
            .replace("{{DOMAINS}}", ", ".join(DOMAINS)).replace("{{FIRST_DOMAIN}}", DOMAINS[0]))


def install_skill(dest: Path, remove=False):
    f = dest / "SKILL.md"
    if remove:
        if f.exists() and "memex.py" in f.read_text():
            shutil.rmtree(dest)
            say(f"removed {dest}")
        return
    write(f, skill_text())
    say(f"skill: {dest}")


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        notes.append(f"{path} isn't valid JSON; left it alone. Fix it and re-run setup.")
        return None


def drop_hooks(entries, cmds):
    out = []
    for e in entries or []:
        hooks = [h for h in e.get("hooks", []) if h.get("command") not in cmds]
        if hooks:
            out.append({**e, "hooks": hooks})
    return out


# ---------- shim ----------

def shim(remove=False):
    if SHIM.exists() and MARK not in SHIM.read_text(errors="ignore"):
        if not remove:
            notes.append(f"{SHIM} exists and isn't Memex's; left it alone. Agents fall back to python3 {FW}/tools/memex.py.")
        return
    if remove:
        if SHIM.exists():
            SHIM.unlink()
            say(f"removed {SHIM}")
        return
    write(SHIM, f"#!/bin/sh\n# {MARK}: runs the Memex CLI from the framework at {FW}.\nexec {ml.memex_command()} \"$@\"\n")
    SHIM.chmod(0o755)
    say(f"memex CLI: {SHIM}")
    if str(SHIM.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        notes.append(f"{SHIM.parent} is not on your PATH. Add this to your shell profile: export PATH=\"$HOME/.local/bin:$PATH\"")


# ---------- Claude Code ----------

def claude(state, remove=False):
    prev = state.get("claude", {})
    install_skill(HOME / ".claude" / "skills" / "memex", remove)
    set_block(HOME / ".claude" / "CLAUDE.md", MD_BEGIN, MD_END, None if remove else memex.pointer())
    sp = HOME / ".claude" / "settings.json"
    s = load_json(sp)
    if s is None:
        return
    perms = s.setdefault("permissions", {})
    allow = [e for e in perms.get("allow", []) if e not in prev.get("allow", [])]
    s["hooks"] = s.get("hooks", {})
    pre = drop_hooks(s["hooks"].get("PreToolUse"), set(prev.get("hooks", [])) | {GUARD})
    added = {}
    if not remove:
        mine = ["Bash(memex *)", "Bash(memex)", f"Read(/{V}/**)",
                f"Edit(/{V}/wiki/**)", f"Edit(/{V}/inbox/**)", f"Edit(/{V}/outputs/**)"]
        new = [e for e in mine if e not in allow]
        allow += new
        pre.append({"matcher": CLAUDE_MATCHER, "hooks": [{"type": "command", "command": GUARD, "timeout": 15}]})
        added = {"allow": new, "hooks": [GUARD]}
    perms["allow"] = allow
    if pre:
        s["hooks"]["PreToolUse"] = pre
    else:
        s["hooks"].pop("PreToolUse", None)
    if not s["hooks"]:
        s.pop("hooks")
    write(sp, json.dumps(s, indent=2) + "\n")
    state["claude"] = added
    say("Claude Code: ~/.claude/CLAUDE.md block, settings.json permissions + guard hook" if not remove
        else "Claude Code: block, permissions and hook removed")


# ---------- Codex ----------

def toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def codex_config(remove=False):
    cp = HOME / ".codex" / "config.toml"
    text = cp.read_text() if cp.exists() else ""
    if remove:
        if toml_str(V) in text:
            notes.append(f"{cp}: remove {toml_str(V)} from [sandbox_workspace_write] writable_roots and its "
                         f"[projects.{toml_str(V)}] entry if you no longer want them.")
        return
    if re.search(r"(?m)^\s*sandbox_workspace_write\s*[.=]", text):
        notes.append(f"{cp} sets sandbox_workspace_write with dotted or inline keys; add {toml_str(V)} to its "
                     "writable_roots by hand.")
        return
    original, changed = text, False
    if f"projects.{toml_str(V)}" not in text:
        text = text.rstrip("\n") + f"\n\n[projects.{toml_str(V)}]\ntrust_level = \"trusted\"\n"
        changed = True
    m = re.search(r"(?m)^\[sandbox_workspace_write\]\s*$", text)
    if not m:
        text = text.rstrip("\n") + f"\n\n[sandbox_workspace_write]\nwritable_roots = [{toml_str(V)}]\n"
        changed = True
    else:
        nxt = re.search(r"(?m)^\[", text[m.end():])
        end = m.end() + (nxt.start() if nxt else len(text) - m.end())
        section = text[m.end():end]
        wr = re.search(r"(?m)^writable_roots\s*=\s*\[(.*)\]\s*$", section)
        if not wr and "writable_roots" not in section:
            text = text[:m.end()] + f"\nwritable_roots = [{toml_str(V)}]" + text[m.end():]
            changed = True
        elif wr and toml_str(V) not in wr.group(1):
            inner = wr.group(1).strip()
            line = f"writable_roots = [{inner + ', ' if inner else ''}{toml_str(V)}]"
            text = text[:m.end()] + section[:wr.start()] + line + section[wr.end():] + text[end:]
            changed = True
        elif not wr:
            notes.append(f"{cp}: add {toml_str(V)} to writable_roots under [sandbox_workspace_write] by hand "
                         "(it spans several lines, so it was left alone).")
    if changed:
        try:
            import tomllib  # Python 3.11+: refuse to write a config Codex couldn't parse
            tomllib.loads(text)
        except ImportError:
            pass
        except Exception as e:
            notes.append(f"{cp}: the automatic edit wouldn't parse ({e}); left it unchanged. Add the vault to "
                         "writable_roots and trust it by hand.")
            return
        if original:
            write(cp.with_name("config.toml.memex-backup"), original)
        write(cp, text if text.endswith("\n") else text + "\n")


def codex(state, remove=False):
    prev = state.get("codex", {})
    install_skill(HOME / ".agents" / "skills" / "memex", remove)
    set_block(HOME / ".codex" / "AGENTS.md", MD_BEGIN, MD_END, None if remove else memex.pointer())
    rules = HOME / ".codex" / "rules" / "memex.rules"
    if remove:
        if rules.exists() and MARK in rules.read_text():
            rules.unlink()
    else:
        write(rules, f"# {MARK}. Lets agents run the Memex CLI without prompts.\n"
                     "prefix_rule(\n    pattern = [\"memex\"],\n    decision = \"allow\",\n"
                     "    justification = \"Memex CLI: writes only inside the vault, never pushes\",\n)\n")
    hp = HOME / ".codex" / "hooks.json"
    h = load_json(hp)
    if h is not None:
        hooks = h.setdefault("hooks", {})
        pre = drop_hooks(hooks.get("PreToolUse"), set(prev.get("hooks", [])) | {GUARD})
        if not remove:
            pre.append({"matcher": CODEX_MATCHER, "hooks": [{"type": "command", "command": GUARD, "timeout": 15}]})
        if pre:
            hooks["PreToolUse"] = pre
        else:
            hooks.pop("PreToolUse", None)
        if not hooks and set(h) == {"hooks"}:
            hp.unlink(missing_ok=True)
        else:
            write(hp, json.dumps(h, indent=2) + "\n")
    codex_config(remove)
    state["codex"] = {} if remove else {"hooks": [GUARD]}
    say("Codex: ~/.codex/AGENTS.md block, rules, guard hook, trusted vault + writable root" if not remove
        else "Codex: block, rules and hook removed")


# ---------- Hermes ----------

def hermes(state, remove=False):
    install_skill(HOME / ".hermes" / "skills" / "memex", remove)
    cfg = HOME / ".hermes" / "config.yaml"
    text = cfg.read_text() if cfg.exists() else ""
    outside = re.sub(re.escape(Y_BEGIN.split(" (")[0]) + r".*?" + re.escape(Y_END), "", text, flags=re.S)
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731  YAML single-quoted scalar
    block = f"""hooks:
  pre_tool_call:
    - matcher: "write_file|patch|terminal"
      command: {q(GUARD)}
      timeout: 15
  pre_llm_call:
    - command: {q(HERMES_CONTEXT)}
      timeout: 15"""
    if remove:
        if Y_BEGIN.split(" (")[0] in text:
            set_block(cfg, Y_BEGIN, Y_END, None)
    elif re.search(r"(?m)^hooks:", outside):
        notes.append(f"{cfg} already has a hooks: section, so it was left alone. Merge this in by hand:\n" +
                     "\n".join("      " + l for l in block.splitlines()))
    else:
        set_block(cfg, Y_BEGIN, Y_END, block)
        notes.append("Hermes asks once to approve each new shell hook. Start it once with `hermes --accept-hooks` "
                     "to approve the two Memex hooks.")
    if not remove:
        if shutil.which("hermes"):
            r = subprocess.run(["hermes", "skills", "trust", V], capture_output=True, text=True)
            say("Hermes: trusted the vault's skills" if r.returncode == 0 else
                f"Hermes: run `hermes skills trust \"{V}\"` yourself ({(r.stderr or r.stdout).strip()[:120]})")
        else:
            notes.append(f"After installing Hermes, run: hermes skills trust \"{V}\" (loads the vault's /ingest, /ask, …)")
        safe = os.environ.get("HERMES_WRITE_SAFE_ROOT")
        if safe and V not in safe.split(":"):
            notes.append(f"HERMES_WRITE_SAFE_ROOT is set; add {V} to it so Hermes can write the vault.")
    state["hermes"] = {} if remove else {"hooks": [GUARD, HERMES_CONTEXT]}
    say("Hermes: config.yaml hooks, skill" if not remove else "Hermes: hooks and skill removed")


# ---------- main ----------

def detect():
    found = []
    for a in ("claude", "codex", "hermes"):
        if shutil.which(a) or (HOME / f".{a}").exists():
            found.append(a)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agents", default="auto", help="auto (default), all, none, or a list like claude,codex")
    ap.add_argument("--remove", action="store_true", help="undo what a previous run added")
    a = ap.parse_args()
    state = load_state()
    if a.remove:
        agents = [x for x in ("claude", "codex", "hermes") if x in state] if a.agents == "auto" else a.agents.split(",")
    elif a.agents == "auto":
        agents = detect()
    elif a.agents == "all":
        agents = ["claude", "codex", "hermes"]
    elif a.agents == "none":
        agents = []
    else:
        agents = [x.strip() for x in a.agents.split(",") if x.strip()]
    bad = [x for x in agents if x not in ("claude", "codex", "hermes")]
    if bad:
        sys.exit(f"unknown agent(s): {', '.join(bad)} (use claude, codex, hermes)")
    if not a.remove and state.get("vault") and state["vault"] != V:
        say(f"moving the agent wiring from {state['vault']} to this vault")
    wire = {"claude": claude, "codex": codex, "hermes": hermes}
    if a.remove:
        for agent in agents:
            wire[agent](state, True)
            state.pop(agent, None)
        if not any(k in state for k in wire):
            shim(remove=True)
            STATE.unlink(missing_ok=True)
        else:
            save_state(state)
    else:
        shim()  # the CLI is agent-neutral: install it even when no agent is wired
        if not agents:
            say("no agents found or selected; installed only the memex CLI")
        for agent in agents:
            wire[agent](state, False)
        state["vault"] = V
        save_state(state)
    for n in notes:
        say(f"NOTE: {n}")


if __name__ == "__main__":
    main()
