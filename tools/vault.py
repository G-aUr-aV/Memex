#!/usr/bin/env python3
"""Create, render and check a Memex vault (stdlib only). setup.sh and the memex CLI call into this.

  python3 tools/vault.py setup [--vault PATH] [--default PATH] [--agents …]   what setup.sh runs
  memex init <path>          create a vault from seed/: its own local-only git repo with the pseudo identity
  memex sync [--check]       re-render the managed files from the framework
  memex doctor [--fix]       check the vault's git safety settings and managed files

A vault holds three kinds of files:
  vault-owned  inbox/ raw/ wiki/ journal/ notes/ outputs/ Home.md meta/bases/ meta/lint/ .obsidian/   committed
  vault config .memex/vault.json (name, domains, identity) · .memex/local.md (vault-only rules)
               · .memex/domains.json + .memex/domains/<name>.md (vault-only domains)                     committed
  managed      rendered from the framework on every sync (see outputs()): excluded from the vault's git
               through .git/info/exclude and protected by the guard. Change them in the framework.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memexlib as ml  # noqa: E402
from memexlib import FRAMEWORK as FW, MARKER, VERSION, VaultError  # noqa: E402

MANAGED_DIRS = [".claude/skills", ".claude/agents", ".claude/rules", ".codex", ".agents", "meta/templates", "meta/docs"]
BASE_DIRS = ["inbox", "raw/assets", "journal/daily", "journal/reviews", "notes", "outputs", "meta/lint"]
EXCLUDE_BEGIN, EXCLUDE_END = "# memex:managed begin (rewritten by `memex sync`; add your own lines outside this block)", "# memex:managed end"
PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")
PRE_COMMIT = """#!/bin/sh
# Managed by Memex (tools/vault.py): secret scan, raw/ immutability and the vault's commit identity.
FW={fw}
if [ ! -f "$FW/tools/precommit.py" ]; then
  echo "Memex: the framework isn't at $FW any more, so this commit can't be checked and is blocked." >&2
  echo "Re-run setup.sh from your framework clone (it re-points this hook)." >&2
  exit 1
fi
exec python3 "$FW/tools/precommit.py"
"""
PRE_PUSH = """#!/bin/sh
# Managed by Memex (tools/vault.py).
echo "Memex: this vault is local-only, so pushing is disabled. Its history stays on this machine." >&2
exit 1
"""
FRAMEWORK_PRE_COMMIT = """#!/bin/sh
# Managed by Memex setup: keeps knowledge, vaults and secrets out of the framework repo.
exec python3 "$(git rev-parse --show-toplevel)/tools/precommit.py"
"""


def say(msg=""):
    print(f"  {msg}" if msg else "")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- rendering ----------

def local_rules(v: Path) -> str:
    p = v / ".memex" / "local.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    _, body = ml.frontmatter(text)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"\A\s*# Vault-specific rules[^\n]*\n", "", body).strip()
    return body or "(none yet)"


def domains_section(specs) -> str:
    lines = []
    for d in specs:
        folders = " ".join(f"{f}/" for f in d["folders"])
        note = f" {d['note']}" if d.get("note") else ""
        lines.append(f"- `wiki/{d['name']}/`: {d['summary']}: `{folders}`.{note}")
    return "\n".join(lines)


def variables(v: Path) -> dict:
    specs = ml.domain_specs(v)
    return {
        "VAULT_NAME": str(ml.settings(v).get("name") or v.name),
        "VAULT": str(v),
        "FRAMEWORK": str(FW),
        "VERSION": VERSION,
        "DOMAINS": domains_section(specs),
        "DOMAIN_LIST": ", ".join(d["name"] for d in specs),
        "DOMAIN_MAP": " · ".join(f"[[{d['title']}|{d['name'].capitalize()}]]" for d in specs),
        "LOCAL_RULES": local_rules(v),
        "GUARD": ml.guard_command(),
        "MEMEX": ml.memex_command(),
        "MEMEX_PY": str(FW / "tools" / "memex.py"),
    }


def render(path: Path, var: dict, escape=None) -> bytes:
    text = path.read_text(encoding="utf-8")
    for k, val in var.items():
        text = text.replace("{{" + k + "}}", escape(val) if escape else val)
    left = PLACEHOLDER.findall(text)
    if left:
        raise VaultError(f"{path.relative_to(FW)}: unknown placeholder(s) {', '.join(sorted(set(left)))}")
    return text.encode("utf-8")


def json_escape(s: str) -> str:
    return json.dumps(s)[1:-1]


def domain_rule(v: Path, spec) -> bytes:
    n = spec["name"]
    parts = []
    base = spec.get("rule_base")
    if base and (FW / "schema" / "domains" / f"{base}.md").exists():
        parts.append((FW / "schema" / "domains" / f"{base}.md").read_text(encoding="utf-8").strip())
    local = v / ".memex" / "domains" / f"{n}.md"
    if local.exists():
        parts.append(local.read_text(encoding="utf-8").strip())
    if not parts:
        parts.append(f"# {n.capitalize()} domain\n\n(No domain-specific rules yet.)")
    head = f'---\npaths:\n  - "raw/{n}/**"\n  - "wiki/{n}/**"\n---\n'
    return (head + "\n\n".join(parts) + "\n").encode("utf-8")


def tree(src: Path, dest: str, out: dict):
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.name != ".DS_Store" and "__pycache__" not in p.parts:
            out[f"{dest}/{p.relative_to(src).as_posix()}"] = p.read_bytes()


def outputs(v: Path) -> dict:
    """Every managed file: vault-relative path -> bytes, or ("link", target) for a symlink."""
    var = variables(v)
    out = {"AGENTS.md": render(FW / "schema" / "AGENTS.md.tmpl", var), "CLAUDE.md": ("link", "AGENTS.md")}
    tree(FW / "skills", ".claude/skills", out)
    out[".agents/skills"] = ("link", "../.claude/skills")
    tree(FW / "agents", ".claude/agents", out)
    for f in sorted((FW / "schema" / "rules").glob("*.md")):
        out[f".claude/rules/{f.name}"] = f.read_bytes()
    for spec in ml.domain_specs(v):
        out[f".claude/rules/{spec['name']}.md"] = domain_rule(v, spec)
    out[".claude/settings.json"] = render(FW / "config" / "claude-settings.json.tmpl", var, json_escape)
    out[".codex/hooks.json"] = render(FW / "config" / "codex-hooks.json.tmpl", var, json_escape)
    out[".codex/rules/memex.rules"] = render(FW / "config" / "codex.rules.tmpl", var, json_escape)
    tree(FW / "templates", "meta/templates", out)
    out["Memex Manual.md"] = (FW / "docs" / "Memex Manual.md").read_bytes()
    out["meta/docs/Design Rationale.md"] = (FW / "docs" / "Design Rationale.md").read_bytes()
    out[".obsidian/snippets/memex.css"] = (FW / "seed" / "obsidian" / "snippets" / "memex.css").read_bytes()
    return out


def digest(item) -> str:
    return "link:" + item[1] if isinstance(item, tuple) else sha(item)


def input_hash(outs: dict) -> str:
    h = hashlib.sha256(VERSION.encode())
    for k in sorted(outs):
        h.update(f"{k}\0{digest(outs[k])}\0".encode())
    return h.hexdigest()


# ---------- sync ----------

def state_path(v: Path) -> Path:
    return v / ".memex" / "state.json"


def load_state(v: Path) -> dict:
    try:
        return json.loads(state_path(v).read_text(encoding="utf-8"))
    except Exception:
        return {}


def current(dest: Path):
    if dest.is_symlink():
        return ("link", os.readlink(dest))
    if dest.is_file():
        return dest.read_bytes()
    return None


def backup(v: Path, rel: str, stamp: str):
    src = v / rel
    dest = v / ".memex" / "backup" / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def prune_empty(v: Path, start: Path):
    p = start
    while p != v and p.is_dir() and not any(p.iterdir()):
        p.rmdir()
        p = p.parent


def write_exclude(v: Path, outs: dict):
    git_dir = v / ".git"
    if not git_dir.is_dir():
        return
    lines = []
    for rel in sorted(outs):
        d = next((m for m in MANAGED_DIRS if rel == m or rel.startswith(m + "/")), None)
        entry = f"/{d}/" if d else f"/{rel}"
        if entry not in lines:
            lines.append(entry)
    lines += ["/.memex/state.json", "/.memex/backup/"]
    ex = git_dir / "info" / "exclude"
    text = ex.read_text(encoding="utf-8") if ex.exists() else ""
    text = re.sub(re.escape(EXCLUDE_BEGIN) + r".*?" + re.escape(EXCLUDE_END) + r"\n?", "", text, flags=re.S).rstrip("\n")
    block = "\n".join([EXCLUDE_BEGIN, *lines, EXCLUDE_END])
    ex.parent.mkdir(parents=True, exist_ok=True)
    ex.write_text((text + "\n\n" if text else "") + block + "\n", encoding="utf-8")


def ensure_dirs(v: Path):
    dirs = list(BASE_DIRS)
    for spec in ml.domain_specs(v):
        dirs.append(f"raw/{spec['name']}")
        dirs += [f"wiki/{spec['name']}/{f}" for f in spec["folders"]]
    for d in dirs:
        p = v / d
        p.mkdir(parents=True, exist_ok=True)
        if not any(x for x in p.iterdir() if x.name != ".DS_Store"):
            (p / ".gitkeep").touch()


def tool(v: Path, name: str, *args):
    return subprocess.run([sys.executable, str(FW / "tools" / name), *args], cwd=str(v),
                          env={**os.environ, "MEMEX_VAULT": str(v)}, capture_output=True, text=True)


def is_fresh(v: Path, outs: dict, state: dict) -> bool:
    return state.get("input_hash") == input_hash(outs) and state.get("framework") == str(FW) and \
        all(current(v / r) is not None for r in outs)


def stale(v: Path) -> bool:
    """True if the framework (or the vault's config) changed since the last sync."""
    v = Path(v).resolve()
    return not is_fresh(v, outputs(v), load_state(v))


def sync(v: Path, only_if_stale=False) -> dict:
    """Render the managed files into v (with only_if_stale, only when something changed since the last sync).
    Returns {"stale", "changed", "removed", "backed_up"}."""
    v = Path(v).resolve()
    outs = outputs(v)
    h = input_hash(outs)
    state = load_state(v)
    old = state.get("files", {})
    result = {"stale": not is_fresh(v, outs, state), "changed": [], "removed": [], "backed_up": []}
    if only_if_stale and not result["stale"]:
        return result
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    files = {}
    for rel_, item in sorted(outs.items()):
        dest = v / rel_
        cur = current(dest)
        files[rel_] = digest(item)
        if cur == item:
            continue
        if dest.is_dir() and not dest.is_symlink():
            backup(v, rel_, stamp)
            result["backed_up"].append(rel_)
        elif cur is not None and not (isinstance(cur, tuple) or digest(cur) == old.get(rel_)):
            backup(v, rel_, stamp)  # edited by hand (or not ours): keep a copy, then overwrite
            result["backed_up"].append(rel_)
        elif cur is not None:
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(item, tuple):
            os.symlink(item[1], dest)
        else:
            dest.write_bytes(item)
        result["changed"].append(rel_)
    for rel_ in sorted(set(old) - set(outs)):
        dest = v / rel_
        cur = current(dest)
        if cur is None:
            continue
        if not isinstance(cur, tuple) and digest(cur) != old[rel_]:
            backup(v, rel_, stamp)
            result["backed_up"].append(rel_)
        else:
            dest.unlink()
        result["removed"].append(rel_)
        prune_empty(v, dest.parent)
    write_exclude(v, outs)
    ensure_dirs(v)
    state = {"version": VERSION, "framework": str(FW), "framework_sha": ml.framework_sha(), "input_hash": h,
             "synced_at": datetime.datetime.now().isoformat(timespec="seconds"), "dirs": MANAGED_DIRS, "files": files}
    state_path(v).parent.mkdir(parents=True, exist_ok=True)
    state_path(v).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tool(v, "build_index.py")
    return result


def describe(res: dict) -> str:
    bits = [f"{len(res['changed'])} managed file(s) updated"]
    if res["removed"]:
        bits.append(f"{len(res['removed'])} removed")
    if res["backed_up"]:
        bits.append(f"hand edits backed up to .memex/backup/: {', '.join(res['backed_up'][:5])}")
    return ", ".join(bits)


# ---------- git ----------

def harden_git(v: Path):
    """Make v its own local-only repo that always commits as the vault's identity. Safe to re-run."""
    if not (v / ".git").exists():
        if ml.git(v, "init", "-q", "-b", "main").returncode:
            ml.git(v, "init", "-q")
            ml.git(v, "symbolic-ref", "HEAD", "refs/heads/main")
    ident = ml.identity(v)
    for key, val in (("user.name", ident["name"]), ("user.email", ident["email"]),
                     ("author.name", ident["name"]), ("author.email", ident["email"]),
                     ("committer.name", ident["name"]), ("committer.email", ident["email"]),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false"), ("core.hooksPath", ".git/hooks")):
        r = ml.git(v, "config", "--local", key, val)
        if r.returncode:
            raise VaultError(f"git config {key} failed in {v}: {r.stderr.strip()}")
    hooks = v / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for name, text in (("pre-commit", PRE_COMMIT.format(fw=shlex.quote(str(FW)))), ("pre-push", PRE_PUSH)):
        (hooks / name).write_text(text, encoding="utf-8")
        (hooks / name).chmod(0o755)


def identity_env(v: Path) -> dict:
    ident = ml.identity(v)
    return ml.git_env({"GIT_AUTHOR_NAME": ident["name"], "GIT_AUTHOR_EMAIL": ident["email"],
                       "GIT_COMMITTER_NAME": ident["name"], "GIT_COMMITTER_EMAIL": ident["email"]})


def commit(v: Path, message: str, force=False, check_lint=True) -> str:
    """Rebuild the index, lint, and commit everything in the vault as the vault's identity. Never pushes."""
    v = Path(v).resolve()
    lock_path = Path(tempfile.gettempdir()) / f"memex-{hashlib.sha1(str(v).encode()).hexdigest()[:10]}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one commit at a time, even with several agents running
        top = ml.repo_top(v)
        if top != v:
            raise VaultError(f"{v} has no git repository of its own" + (f" (git would use {top})" if top else "") +
                             ". Refusing to commit so nothing lands in another repo. Run memex doctor --fix.")
        remotes = ml.git(v, "remote").stdout.split()
        if remotes:
            raise VaultError(f"the vault has git remote(s) {', '.join(remotes)}. Memex vaults are local-only; remove "
                             "them (git remote remove <name>) before committing.")
        tool(v, "build_index.py")
        if check_lint:
            lint = tool(v, "lint.py", "--quick")
            if lint.returncode and not force:
                print(lint.stdout.strip())
                raise VaultError("commit skipped: fix the lint errors above, then run memex commit again "
                                 "(--force only for errors you didn't cause)")
        ml.git(v, "add", "-A", ".")
        if ml.git(v, "diff", "--cached", "--quiet").returncode == 0:
            return "nothing to commit"
        msg = f"{message.strip()}\n\nMemex-Framework: {VERSION} ({ml.framework_sha()})\n"
        c = ml.git(v, "commit", "-q", "-F", "-", env=identity_env(v), input=msg)
        if c.returncode:
            raise VaultError(f"git commit failed:\n{(c.stdout + c.stderr).strip()}\nNothing was pushed.")
        stat = ml.git(v, "show", "--stat", "--format=%h %s", "HEAD").stdout.strip().splitlines()
        return f"committed {stat[0]}" + (f" ({stat[-1].strip()})" if len(stat) > 1 else "")


# ---------- init ----------

def ensure_framework_ignores(p: Path):
    if ml.framework_ignores(p):
        return
    ex = Path(ml.git(FW, "rev-parse", "--git-path", "info/exclude").stdout.strip() or ".git/info/exclude")
    ex = ex if ex.is_absolute() else FW / ex
    ex.parent.mkdir(parents=True, exist_ok=True)
    text = ex.read_text(encoding="utf-8") if ex.exists() else ""
    ex.write_text(text.rstrip("\n") + ("\n" if text else "") +
                  f"# Memex vault (local-only; never part of the framework)\n/{p.relative_to(FW).as_posix()}/\n",
                  encoding="utf-8")
    if not ml.framework_ignores(p):
        raise VaultError(f"couldn't make the framework repo ignore {p}")


def check_location(p: Path):
    """Refuse a vault inside another repo; inside the framework clone it must be git-ignored."""
    p = p.resolve()
    if p == FW or ml.inside(FW, p):
        raise VaultError(f"{p} is (or contains) the framework. Pick a separate folder for the vault.")
    top = ml.repo_top(p)
    if top is None or top == p:
        return
    if top == FW:
        ensure_framework_ignores(p)
        return
    raise VaultError(f"{p} is inside another git repository ({top}). A vault must be its own repository; "
                     "pick a folder outside it.")


def seed_graph(v: Path):
    p = v / ".obsidian" / "graph.json"
    g = json.loads(p.read_text(encoding="utf-8"))
    palette = [14701138, 5431473, 11621088, 3900150, 16744272, 9055202]
    groups = [{"query": f"path:wiki/{d['name']}", "color": {"a": 1, "rgb": d.get("color") or palette[i % len(palette)]}}
              for i, d in enumerate(ml.domain_specs(v))]
    g["colorGroups"] = groups + [{"query": "path:notes", "color": {"a": 1, "rgb": 16761095}}]
    p.write_text(json.dumps(g, indent=2) + "\n", encoding="utf-8")


def init(path, name=None) -> Path:
    p = Path(path).expanduser().resolve()
    check_location(p)
    if (p / MARKER).exists():
        raise VaultError(f"{p} is already a Memex vault")
    if p.exists() and any(x for x in p.iterdir() if x.name != ".DS_Store"):
        raise VaultError(f"{p} isn't empty. Pick a new or empty folder for the vault.")
    seed = FW / "seed"
    p.mkdir(parents=True, exist_ok=True)
    (p / ".memex").mkdir()
    settings = {"name": name or p.name, "domains": "default", "identity": dict(ml.DEFAULT_IDENTITY),
                "created": datetime.date.today().isoformat()}
    (p / MARKER).write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    shutil.copy(seed / "memex" / "local.md", p / ".memex" / "local.md")
    shutil.copytree(seed / "obsidian", p / ".obsidian", ignore=shutil.ignore_patterns("memex.css", ".DS_Store"))
    shutil.copytree(seed / "wiki", p / "wiki")
    (p / "meta").mkdir()
    shutil.copytree(seed / "bases", p / "meta" / "bases")
    shutil.copy(seed / "gitignore.seed", p / ".gitignore")
    shutil.copy(seed / "gitattributes.seed", p / ".gitattributes")
    (p / "Home.md").write_bytes(render(seed / "Home.md.tmpl", variables(p)))
    seed_graph(p)
    harden_git(p)
    sync(p)
    commit(p, "setup: create vault", check_lint=False)
    return p


# ---------- doctor ----------

def doctor(v: Path, fix=False):
    """[(ok, message)] for the vault's safety settings; with fix=True, repair what can be repaired first."""
    v = Path(v).resolve()
    if fix:
        check_location(v)
        harden_git(v)
        sync(v)
    ident = ml.identity(v)
    want = f"{ident['name']} <{ident['email']}>"
    out = []

    def cfg(key):
        return ml.git(v, "config", "--local", "--get", key).stdout.strip()

    top = ml.repo_top(v)
    out.append((top == v, "the vault is its own git repository" if top == v else
                f"the vault has no git repository of its own (git sees {top})"))
    out.append((cfg("user.name") == ident["name"] and cfg("user.email") == ident["email"],
                f"commit identity is {want} (repo-local)"))
    out.append((cfg("commit.gpgsign") == "false", "commit signing is off (no personal key)"))
    out.append((cfg("core.hooksPath") == ".git/hooks", "hooks run from the vault's .git/hooks"))
    hook = v / ".git" / "hooks" / "pre-commit"
    ok = hook.exists() and str(FW) in hook.read_text(errors="ignore") and (FW / "tools" / "precommit.py").exists()
    out.append((ok, "pre-commit hook points at this framework"))
    push = v / ".git" / "hooks" / "pre-push"
    out.append((push.exists() and "local-only" in push.read_text(errors="ignore"), "pre-push hook refuses every push"))
    remotes = ml.git(v, "remote").stdout.split()
    out.append((not remotes, "no git remotes" if not remotes else f"git remote(s) configured: {', '.join(remotes)}"))
    log = ml.git(v, "log", "--all", "--format=%an <%ae>%n%cn <%ce>")
    others = sorted({l for l in log.stdout.splitlines() if l.strip()} - {want}) if log.returncode == 0 else []
    out.append((not others, "every commit uses the vault identity" if not others else
                f"commits by other identities in history: {', '.join(others[:3])}"))
    if ml.inside(v, FW):
        out.append((ml.framework_ignores(v), "the framework repo ignores this vault"))
    fresh = not stale(v)
    out.append((fresh, "managed files are up to date" if fresh else "managed files are stale: run memex sync"))
    tracked = ml.git(FW, "ls-files", "--", *ml.KNOWLEDGE_DIRS).stdout.split()
    out.append((not tracked, "the framework repo tracks no knowledge" if not tracked else
                f"the framework repo tracks knowledge files: {', '.join(tracked[:3])}"))
    return out


# ---------- setup (per machine) ----------

def install_framework_hook():
    if not (FW / ".git").is_dir():
        return
    hp = ml.git(FW, "config", "core.hooksPath").stdout.strip()
    if hp and hp != ".git/hooks":
        say(f"NOTE: core.hooksPath is set ({hp}), so the framework's pre-commit hook wasn't installed. "
            f"Add this to your hooks: python3 \"{FW}/tools/precommit.py\"")
        return
    hook = FW / ".git" / "hooks" / "pre-commit"
    if hook.exists() and "tools/precommit.py" not in hook.read_text(errors="ignore"):
        say(f"NOTE: {hook} exists and isn't Memex's; add this line to it: python3 \"{FW}/tools/precommit.py\"")
        return
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(FRAMEWORK_PRE_COMMIT, encoding="utf-8")
    hook.chmod(0o755)
    say("framework pre-commit hook installed (refuses knowledge, vaults and secrets)")


def pick_vault(arg, default) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    cur = ml.load_config().get("vault")
    if cur and (Path(cur) / MARKER).is_file():
        say(f"using the vault already configured: {cur}")
        return Path(cur).resolve()
    default = Path(default).expanduser().resolve()
    if sys.stdin.isatty():
        try:
            ans = input(f"  Where should your vault live? [{default}] ").strip()
        except EOFError:
            ans = ""
        return Path(ans).expanduser().resolve() if ans else default
    return default


def obsidian_notes(v: Path):
    obs = Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if not obs.exists():
        obs = Path.home() / ".config" / "obsidian" / "obsidian.json"
    text = obs.read_text(errors="ignore") if obs.exists() else ""
    steps = []
    if f'"{v}"' in text:
        steps.append("✓ Obsidian already knows this vault")
    else:
        steps.append(f"Obsidian → Open folder as vault → {v}")
    if f'"{FW}"' in text:
        steps.append(f"Obsidian has the framework folder ({FW}) registered as a vault: remove it from the vault list")
    if '"cli":true' not in text:
        steps.append("Obsidian → Settings → General → Command line interface: ON (agents use it for search and backlinks)")
    return steps


def setup(a):
    print(f"Memex {VERSION}: framework at {FW}")
    v = pick_vault(a.vault, a.default or Path.cwd() / "MemexVault")
    if (v / MARKER).is_file():
        check_location(v)
        harden_git(v)
        res = sync(v)
        say(f"vault: {v} ({describe(res) if res['changed'] or res['backed_up'] else 'managed files up to date'})")
    else:
        init(v)
        say(f"vault created: {v}")
        say("  its own git repo: no remote, pushes refused, every commit as "
            f"{ml.identity(v)['name']} <{ml.identity(v)['email']}>")
    cfg = ml.load_config()
    cfg.update({"vault": str(v), "framework": str(FW)})
    ml.save_config(cfg)
    say(f"config: {ml.config_path()}")
    install_framework_hook()
    os.environ["MEMEX_VAULT"] = str(v)
    print(f"Agent wiring ({a.agents}):")
    subprocess.run([sys.executable, str(FW / "tools" / "integrate.py"), "--agents", a.agents], check=False)
    r = tool(v, "lint.py", "--quick")
    errs = [l for l in r.stdout.splitlines() if l.startswith("- ") and l != "- none"]
    say("lint: clean" if not errs else "lint: " + "; ".join(errs[:3]))
    print("\nRemaining steps:")
    for i, s in enumerate(obsidian_notes(v), 1):
        say(f"{i}. {s}")
    say(f"Web Clipper: import {FW}/clipper/Memex Inbox.json and set its vault to '{v.name}'")
    say(f"Open an agent in the vault: cd {shlex.quote(str(v))} && claude   (or codex, or hermes)")
    bin_dir = str(Path.home() / ".local" / "bin")
    if a.agents != "none" and bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        say(f"WARNING: {bin_dir} is not on PATH, and skills call `memex`. Add: export PATH=\"$HOME/.local/bin:$PATH\"")
    say("Backups: the vault has no remote, so this disk holds the only copy. Keep Time Machine on, or run "
        "`git -C <vault> bundle create <drive>/vault.bundle --all` now and then.")
    if ml.inside(v, FW):
        say(f"The vault lives inside the framework folder: never delete or re-clone {FW} without moving the vault out first.")


def main():
    ap = argparse.ArgumentParser(description="Memex vault setup and maintenance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--vault")
    s.add_argument("--default")
    s.add_argument("--agents", default="auto")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the child tools' output
    try:
        setup(a)
    except VaultError as e:
        print(f"memex: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
