#!/usr/bin/env python3
"""Wire the configured vault into the coding agents on this machine, so they read and write Memex from any
project without permission prompts. Run by setup.sh; safe to re-run.

  python3 tools/integrate.py                         # every agent found (claude, codex, hermes)
  python3 tools/integrate.py --agents claude,codex   # just these
  python3 tools/integrate.py --remove                # undo what a previous run added

What it adds (and records in ~/.config/memex/integration.json so re-runs and --remove are clean):
  all      ~/.local/bin/memex, a shim for the framework's tools/memex.py
  claude   ~/.claude/skills/memex · a Memex block in ~/.claude/CLAUDE.md · ~/.claude/settings.json:
           allow `memex`, reads of the vault and edits of wiki/ inbox/ outputs/, the guard hook, and the
           session hooks (repo-aware recall at SessionStart, the /harvest ledger at SessionEnd)
  codex    ~/.agents/skills/memex · a Memex block in ~/.codex/AGENTS.md · ~/.codex/rules/memex.rules
           (allow `memex`) · the guard and session hooks in ~/.codex/hooks.json · ~/.codex/config.toml: trust the
           vault and add it to sandbox_workspace_write.writable_roots
  hermes   ~/.hermes/skills/memex · a Memex block in ~/.hermes/config.yaml (guard on pre_tool_call,
           context on pre_llm_call) · `hermes skills trust <vault>` for the vault's own skills
Hooks and the shim run code from the framework; permissions and trust point at the vault. One vault per
machine: re-running setup with another vault (or `memex move`) moves the wiring there. --remove works even
after the vault folder is gone (the path is recorded in integration.json).
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

ROOT = V = NAME = DOMAINS = None  # set by use_vault()
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


def use_vault(path=None):
    """Point the module at the configured vault, or (for --remove) at the path recorded in the state file."""
    global ROOT, V, NAME, DOMAINS
    if path is None:
        ROOT = ml.vault()
        NAME, DOMAINS = str(ml.settings(ROOT).get("name") or ROOT.name), ml.domain_names(ROOT)
    else:
        ROOT, NAME, DOMAINS = Path(path), Path(path).name, []
    V = str(ROOT)


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
    if text:
        write(path, text + "\n")
    elif path.exists():
        path.unlink()  # the Memex block was all it held


def skill_text():
    t = (ml.FRAMEWORK / "tools" / "memex.SKILL.md").read_text()
    return (t.replace("{{VAULT}}", V).replace("{{VAULT_NAME}}", NAME).replace("{{FRAMEWORK}}", FW)
            .replace("{{DOMAINS}}", ", ".join(DOMAINS)).replace("{{FIRST_DOMAIN}}", DOMAINS[0]))


def prune(d: Path, stop=HOME):
    """Remove d and its parents while they're empty (up to, not including, stop)."""
    while d != stop and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def install_skill(dest: Path, remove=False):
    f = dest / "SKILL.md"
    if remove:
        if f.exists() and "memex.py" in f.read_text():
            shutil.rmtree(dest)
            say(f"removed {dest}")
        prune(dest.parent)
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


def session_hooks(agent):
    """Global hooks for every session on this machine: repo-aware recall at start, the ledger at the end."""
    return {"SessionStart": f"{ml.memex_command()} hook session-start",
            "SessionEnd": f"{ml.memex_command()} hook session-end --agent {agent}"}


def merge_hooks(hooks, prev_cmds, matcher, agent, remove=False):
    """Drop every hook entry Memex added before, then add the current guard and session hooks (unless removing).
    Returns the commands installed, for the state file."""
    wanted = {"PreToolUse": {"matcher": matcher, "hooks": [{"type": "command", "command": GUARD, "timeout": 15}]}}
    for event, cmd in session_hooks(agent).items():
        wanted[event] = {"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}
    ours = set(prev_cmds) | {GUARD} | set(session_hooks(agent).values())
    for event in wanted:
        entries = drop_hooks(hooks.get(event), ours)
        if not remove:
            entries.append(wanted[event])
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    return [] if remove else [GUARD, *session_hooks(agent).values()]


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
    added = {}
    if not remove:
        mine = ["Bash(memex *)", "Bash(memex)", f"Read(/{V}/**)",
                f"Edit(/{V}/wiki/**)", f"Edit(/{V}/inbox/**)", f"Edit(/{V}/outputs/**)"]
        new = [e for e in mine if e not in allow]
        allow += new
        added = {"allow": new}
    added["hooks"] = merge_hooks(s["hooks"], prev.get("hooks", []), CLAUDE_MATCHER, "claude", remove)
    perms["allow"] = allow
    if not s["hooks"]:
        s.pop("hooks")
    if remove:
        if not perms["allow"]:
            perms.pop("allow")
        if not perms:
            s.pop("permissions")
    if s:
        write(sp, json.dumps(s, indent=2) + "\n")
    elif sp.exists():
        sp.unlink()  # nothing but what Memex added
    state["claude"] = added if not remove else {}
    say("Claude Code: ~/.claude/CLAUDE.md block, settings.json permissions, guard + session hooks" if not remove
        else "Claude Code: block, permissions and hooks removed")


# ---------- Codex ----------

def toml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def codex_drop(path: str) -> bool:
    """Remove what codex_config() added for path: its [projects."…"] trust entry and its writable root."""
    cp = HOME / ".codex" / "config.toml"
    text = cp.read_text() if cp.exists() else ""
    q = toml_str(path)
    if q not in text:
        return False
    new = re.sub(r"(?m)^\[projects\." + re.escape(q) + r"\]\n(?:trust_level = \"trusted\"\n?)?(?=\[|\Z|\n)", "", text)
    m = re.search(r"(?m)^writable_roots\s*=\s*\[(.*)\]\s*$", new)
    if m and q in m.group(1):
        items = [i.strip() for i in m.group(1).split(",") if i.strip() and i.strip() != q]
        new = new[:m.start()] + f"writable_roots = [{', '.join(items)}]" + new[m.end():]
    new = re.sub(r"(?m)^\[sandbox_workspace_write\]\nwritable_roots = \[\]\n?", "", new)
    new = re.sub(r"\n{3,}", "\n\n", new).strip("\n")
    if q in new:
        notes.append(f"{cp}: remove {q} from it by hand (it's in a form setup didn't write).")
    if new:
        write(cp, new + "\n")
    else:
        cp.unlink()
    return True


def codex_config(remove=False, old=None):
    cp = HOME / ".codex" / "config.toml"
    if old and old != V and codex_drop(old):
        say(f"Codex: dropped the old vault path {old} from config.toml")
    if remove:
        codex_drop(V)
        return
    text = cp.read_text() if cp.exists() else ""
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
    old_vault = state.get("vault")
    install_skill(HOME / ".agents" / "skills" / "memex", remove)
    set_block(HOME / ".codex" / "AGENTS.md", MD_BEGIN, MD_END, None if remove else memex.pointer())
    rules = HOME / ".codex" / "rules" / "memex.rules"
    if remove:
        if rules.exists() and MARK in rules.read_text():
            rules.unlink()
        prune(rules.parent)
    else:
        owner_only = "".join(
            f"prefix_rule(\n    pattern = [{', '.join(json.dumps(w) for w in ['memex', *sub])}],\n    decision = \"forbidden\",\n"
            "    justification = \"Owner only: it sends the vault elsewhere, moves it or removes it\",\n)\n"
            for sub in (["remote", "set"], ["remote", "remove"], ["move"], ["uninstall"]))
        write(rules, f"# {MARK}. Lets agents run the Memex CLI without prompts.\n"
                     "prefix_rule(\n    pattern = [\"memex\"],\n    decision = \"allow\",\n"
                     "    justification = \"Memex CLI: writes only inside the vault; pushes only to the owner's private remote\",\n)\n"
                     + owner_only)
    hp = HOME / ".codex" / "hooks.json"
    h = load_json(hp)
    if h is not None:
        hooks = h.setdefault("hooks", {})
        installed = merge_hooks(hooks, prev.get("hooks", []), CODEX_MATCHER, "codex", remove)
        if not hooks and set(h) == {"hooks"}:
            hp.unlink(missing_ok=True)
        else:
            write(hp, json.dumps(h, indent=2) + "\n")
    codex_config(remove, old_vault)
    state["codex"] = {} if remove else {"hooks": installed if h is not None else []}
    say("Codex: ~/.codex/AGENTS.md block, rules, guard + session hooks, trusted vault + writable root" if not remove
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
    try:
        use_vault()
    except ml.VaultError as e:
        if not a.remove:
            sys.exit(f"memex: {e}")
        if not state.get("vault"):
            print("  nothing to remove: no vault configured and no record of earlier wiring")
            return
        use_vault(state["vault"])
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
