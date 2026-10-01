#!/usr/bin/env python3
"""Create, render and check a Memex vault (stdlib only). setup.sh and the memex CLI call into this.

  python3 tools/vault.py setup [--vault PATH] [--default PATH] [--agents …]   what setup.sh runs
  memex init <path>          create a vault from seed/: its own local-first git repo with the pseudo identity
  memex sync [--check]       re-render the managed files from the framework
  memex doctor [--fix]       check the vault's git safety settings and managed files
  memex move <path>          move the vault and re-point everything at it (owner only)
  memex uninstall            remove Memex from this machine (owner only; see uninstall())

A vault holds three kinds of files:
  vault-owned  inbox/ raw/ wiki/ journal/ notes/ outputs/ Home.md meta/bases/ meta/lint/ .obsidian/   committed
  vault config .memex/vault.json (name, domains, identity) · .memex/local.md (vault-only rules)
               · .memex/domains.json + .memex/domains/<name>.md (vault-only domains)                     committed
  machine      .memex/remote.json (the private remote, if attached) · state.json · sessions.jsonl ·
               backup.json · backup/                                                        git-excluded
  managed      rendered from the framework on every sync (see outputs()): excluded from the vault's git
               through .git/info/exclude and protected by the guard. Change them in the framework.
"""
import argparse
import contextlib
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
# Managed by Memex (tools/vault.py): refuses every push unless the owner attached a private remote, and then
# allows only `memex push` to that one URL, after a secret scan of what's going out.
FW={fw}
if [ ! -f "$FW/tools/precommit.py" ]; then
  echo "Memex: the framework isn't at $FW any more, so this push can't be checked and is blocked." >&2
  exit 1
fi
exec python3 "$FW/tools/precommit.py" --pre-push "$@"
"""
STANDALONE_PRE_PUSH = """#!/bin/sh
# Left by `memex uninstall`: this vault was local-only. Delete this file if you ever want to push it.
echo "This vault is local-only, so pushing is disabled (.git/hooks/pre-push)." >&2
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
    lines += ["/.memex/state.json", "/.memex/backup/", "/.memex/sessions.jsonl", "/.memex/backup.json",
              "/.memex/remote.json"]
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
    """Make v its own repo that always commits as the vault's identity and pushes only through Memex.
    Safe to re-run."""
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
    for name, text in (("pre-commit", PRE_COMMIT.format(fw=shlex.quote(str(FW)))),
                       ("pre-push", PRE_PUSH.format(fw=shlex.quote(str(FW))))):
        (hooks / name).write_text(text, encoding="utf-8")
        (hooks / name).chmod(0o755)


def identity_env(v: Path) -> dict:
    ident = ml.identity(v)
    return ml.git_env({"GIT_AUTHOR_NAME": ident["name"], "GIT_AUTHOR_EMAIL": ident["email"],
                       "GIT_COMMITTER_NAME": ident["name"], "GIT_COMMITTER_EMAIL": ident["email"]})


@contextlib.contextmanager
def locked(v: Path):
    """One writer at a time (commit, pull, move), even with several agents running."""
    lock_path = Path(tempfile.gettempdir()) / f"memex-{hashlib.sha1(str(Path(v).resolve()).encode()).hexdigest()[:10]}.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def commit(v: Path, message: str, force=False, check_lint=True, sync_remote=True) -> str:
    """Rebuild the index, lint, and commit everything in the vault as the vault's identity. Then, if the owner
    attached a private remote, push (a failed push never fails the commit)."""
    import remote
    v = Path(v).resolve()
    with locked(v):
        top = ml.repo_top(v)
        if top != v:
            raise VaultError(f"{v} has no git repository of its own" + (f" (git would use {top})" if top else "") +
                             ". Refusing to commit so nothing lands in another repo. Run memex doctor --fix.")
        remote.check_remotes(v)
        if remote.in_progress(v) == "rebase":
            raise VaultError("a rebase is in progress in the vault; run git rebase --abort, then memex pull")
        merging = remote.in_progress(v) == "merge"
        tool(v, "build_index.py")
        if check_lint:
            lint = tool(v, "lint.py", "--quick")
            if lint.returncode and not force:
                print(lint.stdout.strip())
                raise VaultError("commit skipped: fix the lint errors above, then run memex commit again "
                                 "(--force only for errors you didn't cause)")
        ml.git(v, "add", "-A", ".")
        if ml.git(v, "diff", "--cached", "--quiet").returncode == 0 and not merging:
            result = "nothing to commit"
        else:
            msg = f"{message.strip()}\n\nMemex-Framework: {VERSION} ({ml.framework_sha()})\n"
            c = ml.git(v, "commit", "-q", "-F", "-", env=identity_env(v), input=msg)
            if c.returncode:
                raise VaultError(f"git commit failed:\n{(c.stdout + c.stderr).strip()}\nNothing was pushed.")
            stat = ml.git(v, "show", "--stat", "--format=%h %s", "HEAD").stdout.strip().splitlines()
            result = f"committed {stat[0]}" + (f" ({stat[-1].strip()})" if len(stat) > 1 else "")
            if merging:
                remote.save(v, conflict=[], last_error="")
    cfg = remote.settings(v)
    if sync_remote and cfg and cfg["auto"]:
        try:
            pushed = remote.push(v)
        except VaultError as e:
            pushed = f"push refused: {e}"
        if pushed:
            result += f"; {pushed}"
    return result


def after_pull(v: Path):
    """New commits arrived: refresh what's derived from them (indexes; managed files if vault config changed)."""
    sync(v, only_if_stale=True)
    tool(v, "build_index.py")


# ---------- backup ----------

def backup_age(v: Path):
    """Days since the last `memex backup`, or None if there never was one."""
    try:
        last = json.loads((v / ".memex" / "backup.json").read_text(encoding="utf-8"))["last"]
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(last)).days
    except Exception:
        return None


def push_age(v: Path):
    """Days since the last successful push to the private remote, or None (no remote, or never pushed)."""
    import remote
    try:
        last = remote.load(v)["last_push"] if remote.settings(v) else None
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(last)).days if last else None
    except Exception:
        return None


def bundle(v: Path, to=None, keep=None):
    """Write a verified git bundle of the whole vault history; keep the newest `keep` bundles.
    Returns (bundle path, bundles pruned, uncommitted changes?, same disk as the vault?)."""
    v = Path(v).resolve()
    b = ml.feature("backup", v)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(ml.settings(v).get("name") or v.name)).strip("-") or "vault"
    dest = Path(to or b.get("dir") or (Path.home() / "Memex Backups" / name)).expanduser().resolve()
    if ml.inside(dest, v):
        raise VaultError(f"{dest} is inside the vault; back it up somewhere else")
    if ml.git(v, "rev-parse", "--verify", "HEAD").returncode:
        raise VaultError("the vault has no commits yet")
    dest.mkdir(parents=True, exist_ok=True)
    stamp, n = f"{datetime.datetime.now():%Y%m%d-%H%M%S}", 1
    path = dest / f"{name}-{stamp}.bundle"
    while path.exists():
        n += 1
        path = dest / f"{name}-{stamp}-{n}.bundle"
    r = ml.git(v, "bundle", "create", str(path), "--all")
    if r.returncode or ml.git(v, "bundle", "verify", str(path)).returncode:
        path.unlink(missing_ok=True)
        raise VaultError(f"git bundle failed: {(r.stderr or r.stdout).strip()}")
    old = sorted(dest.glob(f"{name}-*.bundle"), key=lambda f: (f.stat().st_mtime_ns, f.name))  # oldest first
    extra = old[:-int(keep or b.get("keep") or 10)]
    for f in extra:
        f.unlink()
    (v / ".memex" / "backup.json").write_text(json.dumps({"last": datetime.datetime.now().isoformat(timespec="seconds"),
                                                          "path": str(path)}, indent=2) + "\n", encoding="utf-8")
    dirty = bool(ml.git(v, "status", "--porcelain").stdout.strip())
    return path, len(extra), dirty, os.stat(dest).st_dev == os.stat(v).st_dev


# ---------- init ----------

FW_EXCLUDE_NOTE = "# Memex vault (never part of the framework)"


def framework_exclude() -> Path:
    ex = Path(ml.git(FW, "rev-parse", "--git-path", "info/exclude").stdout.strip() or ".git/info/exclude")
    return ex if ex.is_absolute() else FW / ex


def ensure_framework_ignores(p: Path):
    if ml.framework_ignores(p):
        return
    ex = framework_exclude()
    ex.parent.mkdir(parents=True, exist_ok=True)
    text = ex.read_text(encoding="utf-8") if ex.exists() else ""
    ex.write_text(text.rstrip("\n") + ("\n" if text else "") +
                  f"{FW_EXCLUDE_NOTE}\n/{p.relative_to(FW).as_posix()}/\n", encoding="utf-8")
    if not ml.framework_ignores(p):
        raise VaultError(f"couldn't make the framework repo ignore {p}")


def drop_framework_exclude(p: Path = None):
    """Remove the framework exclude entry setup added for vault p (or every such entry when p is None)."""
    ex = framework_exclude()
    if not (FW / ".git").exists() or not ex.exists():
        return
    want = f"/{p.relative_to(FW).as_posix()}/" if p is not None and ml.inside(p, FW) else None
    if p is not None and want is None:
        return
    lines, out, i = ex.read_text(encoding="utf-8").splitlines(), [], 0
    while i < len(lines):
        if lines[i] == FW_EXCLUDE_NOTE and i + 1 < len(lines) and (want is None or lines[i + 1] == want):
            i += 2
            continue
        out.append(lines[i])
        i += 1
    ex.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


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
                "created": datetime.date.today().isoformat(), **{k: dict(val) for k, val in ml.FEATURE_DEFAULTS.items()}}
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
    import remote
    push = v / ".git" / "hooks" / "pre-push"
    rcfg = remote.settings(v)
    ok = push.exists() and "--pre-push" in push.read_text(errors="ignore") and str(FW) in push.read_text(errors="ignore")
    out.append((ok, "pre-push hook allows only `memex push` to the attached remote" if rcfg else
                "pre-push hook refuses every push"))
    try:
        remote.check_remotes(v, rcfg)
        if rcfg:
            st = remote.load(v)
            out.append((st.get("visibility") != "public", f"remote origin → {rcfg['url']} "
                        f"({st.get('visibility', 'visibility not checked')}, checked {st.get('visibility_checked', 'never')})"))
        else:
            out.append((True, "no git remote (local-only)"))
    except VaultError as e:
        out.append((False, str(e)))
    if rcfg:
        st = remote.load(v)
        state = remote.in_progress(v)
        problem = (f"a {state} is in progress" if state else
                   f"sync conflict in {', '.join(st['conflict'][:3])} (memex pull --merge)" if st.get("conflict") else
                   f"last sync error: {st['last_error']}" if st.get("last_error") else "")
        out.append((None if problem else True, problem or f"synced: last pull {st.get('last_pull', 'never')}, "
                    f"last push {st.get('last_push', 'never')}"))
    log = ml.git(v, "log", "--all", "--format=%an <%ae>%n%cn <%ce>")
    others = sorted({l for l in log.stdout.splitlines() if l.strip()} - {want}) if log.returncode == 0 else []
    out.append((not others, "every commit uses the vault identity" if not others else
                f"commits by other identities in history: {', '.join(others[:3])}"))
    if ml.inside(v, FW):
        out.append((ml.framework_ignores(v), "the framework repo ignores this vault"))
    fresh = not stale(v)
    out.append((fresh, "managed files are up to date" if fresh else "managed files are stale: run memex sync"))
    age, warn = backup_age(v), int(ml.feature("backup", v)["warn_days"])
    pushed = push_age(v)
    if pushed is not None and pushed <= warn and (age is None or age > warn):
        out.append((True, f"off-machine copy: pushed to the private remote {pushed} day(s) ago"))
    else:
        out.append((True if age is not None and age <= warn else None,
                    "no backup yet (memex backup)" if age is None else f"last backup {age} day(s) ago" +
                    (" (memex backup)" if age > warn else "")))
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
        say(f"  its own git repo: every commit as {ml.identity(v)['name']} <{ml.identity(v)['email']}>, "
            "no remote, pushes refused")
    if a.remote:
        import remote
        cur = remote.settings(v)
        if cur and remote.same_repo(cur["url"], a.remote):
            say(f"remote: {cur['url']} (already attached)")
        else:
            say("remote: " + remote.attach(v, a.remote, branch=a.branch, yes=True, unverified=a.unverified))
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
    import remote
    if not remote.settings(v):
        say("Backups: with no remote, this disk holds the only copy. Run `memex backup --to <external drive>`, or "
            "attach a private remote: `memex remote set <url>`.")
    if ml.inside(v, FW):
        say(f"The vault lives inside the framework folder: never delete or re-clone {FW} without moving the vault "
            "out first (`memex move <path>`).")


# ---------- move & uninstall (owner only; the guard blocks agents) ----------

INTEGRATION = Path.home() / ".config" / "memex" / "integration.json"
PROFILES = (".zshrc", ".zprofile", ".bash_profile", ".bashrc", ".profile")


def integration_state() -> dict:
    try:
        return json.loads(INTEGRATION.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def wired_agents() -> str:
    return ",".join(a for a in ("claude", "codex", "hermes") if a in integration_state()) or "none"


def move(v: Path, dest) -> list:
    """Move the vault to dest and re-point the config, managed files and agent wiring at it. Returns notes."""
    import remote
    from_env = os.environ.get("MEMEX_VAULT")
    v, dest = Path(v).resolve(), Path(dest).expanduser().resolve()
    if dest == v:
        raise VaultError(f"the vault is already at {v}")
    if ml.inside(dest, v):
        raise VaultError(f"{dest} is inside the vault")
    if dest.exists() and (not dest.is_dir() or any(x for x in dest.iterdir() if x.name != ".DS_Store")):
        raise VaultError(f"{dest} already exists and isn't empty. Give a new (or empty) folder.")
    if remote.in_progress(v):
        raise VaultError("finish the merge or rebase in progress first")
    check_location(dest)
    head = ml.git(v, "rev-parse", "HEAD").stdout.strip()
    with locked(v):
        if dest.exists():
            shutil.rmtree(dest)  # empty apart from .DS_Store (checked above)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(v), str(dest))
    if not (dest / MARKER).is_file() or ml.git(dest, "rev-parse", "HEAD").stdout.strip() != head:
        raise VaultError(f"the move didn't verify: check {dest} (and {v}) by hand before using Memex")
    cfg = ml.load_config()
    cfg["vault"] = str(dest)
    ml.save_config(cfg)
    drop_framework_exclude(v)
    os.environ["MEMEX_VAULT"] = str(dest)
    ml._vault = None
    harden_git(dest)
    sync(dest)
    agents = wired_agents()
    print(f"Agent wiring ({agents}):", flush=True)
    subprocess.run([sys.executable, str(FW / "tools" / "integrate.py"), "--agents", agents], check=False)
    notes = [f"moved {v} → {dest} (history verified, {count_commits(dest)} commits)",
             f"Obsidian: open {dest} as a vault, and remove the old entry from its vault list",
             "Claude Code and Codex ask once to trust the new folder the first time you open an agent there"]
    if from_env:
        notes.append("$MEMEX_VAULT is set in your environment: update it to the new path (or unset it)")
    return notes


def count_commits(v: Path) -> int:
    r = ml.git(v, "rev-list", "--count", "HEAD")
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def detach_vault(v: Path):
    """Leave the vault as plain Markdown + git: no managed files, hooks or machine state from Memex."""
    import remote
    attached = remote.settings(v) is not None
    for r_ in sorted(load_state(v).get("files", {}), reverse=True):
        p = v / r_
        if p.is_symlink() or p.is_file():
            p.unlink()
            prune_empty(v, p.parent)
    for name in ("state.json", "sessions.jsonl", "backup.json", "remote.json"):
        (v / ".memex" / name).unlink(missing_ok=True)
    ex = v / ".git" / "info" / "exclude"
    if ex.exists():
        text = re.sub(re.escape(EXCLUDE_BEGIN) + r".*?" + re.escape(EXCLUDE_END) + r"\n?", "",
                      ex.read_text(encoding="utf-8"), flags=re.S)
        ex.write_text(text.rstrip("\n") + "\n" if text.strip() else "", encoding="utf-8")
    hooks = v / ".git" / "hooks"
    if (hooks / "pre-commit").exists() and "Managed by Memex" in (hooks / "pre-commit").read_text(errors="ignore"):
        (hooks / "pre-commit").unlink()
    if attached:
        (hooks / "pre-push").unlink(missing_ok=True)
    else:
        (hooks / "pre-push").write_text(STANDALONE_PRE_PUSH, encoding="utf-8")
        (hooks / "pre-push").chmod(0o755)


def drop_path_lines() -> list:
    """Remove the `# Memex` PATH line from shell profiles, but only if ~/.local/bin no longer holds anything."""
    bin_dir = Path.home() / ".local" / "bin"
    profiles = [Path.home() / n for n in PROFILES if (Path.home() / n).is_file()]
    marked = [p for p in profiles if any(l.rstrip().endswith("# Memex") and ".local/bin" in l
                                         for l in p.read_text(errors="ignore").splitlines())]
    if not marked:
        return []
    if bin_dir.exists() and any(bin_dir.iterdir()):
        return [f"kept the `# Memex` PATH line in {', '.join(str(p) for p in marked)}: {bin_dir} holds other programs"]
    for p in marked:
        lines = p.read_text(errors="ignore").splitlines(keepends=True)
        p.write_text("".join(l for l in lines if not (l.rstrip().endswith("# Memex") and ".local/bin" in l)))
    return [f"removed the `# Memex` PATH line from {', '.join(str(p) for p in marked)}"]


def uninstall(delete_vault=False, confirm=None):
    """Remove Memex from this machine: agent wiring, CLI, config and framework hooks; then delete the vault
    (after a verified backup bundle) or leave it as plain Markdown + git. Works even if the vault is gone.
    Returns (report lines, leftovers)."""
    import remote
    cfg, state = ml.load_config(), integration_state()
    path = os.environ.get("MEMEX_VAULT") or cfg.get("vault") or state.get("vault")
    v = Path(path).expanduser().resolve() if path else None
    exists = bool(v and (v / MARKER).is_file())
    report = []
    if delete_vault and exists:
        name = str(ml.settings(v).get("name") or v.name)
        if confirm is None and sys.stdin.isatty():
            confirm = input(f"  This deletes {v} and everything in it (a backup bundle is written first).\n"
                            f"  Type the vault's name ({name}) to confirm: ").strip()
        if confirm != name:
            raise VaultError(f"nothing was changed. To delete the vault, confirm with its name: --confirm \"{name}\"")
    if exists:
        if remote.in_progress(v):
            raise VaultError("a merge or rebase is in progress in the vault; finish it first (nothing was changed)")
        if ml.git(v, "status", "--porcelain").stdout.strip():
            report.append("final snapshot: " + commit(v, "uninstall: final snapshot", check_lint=False))
        elif remote.settings(v):
            try:
                report.append("remote: " + (remote.push(v) or "already pushed"))
            except VaultError as e:
                report.append(f"remote: {e}")
        try:
            b, *_ = bundle(v)
            report.append(f"backup: {b} (restore with: git clone \"{b}\" <folder>)")
        except VaultError as e:
            if delete_vault:
                raise VaultError(f"couldn't write the backup bundle ({e}), so nothing was deleted")
            report.append(f"backup skipped: {e}")
    env = {**os.environ}
    if exists:
        env["MEMEX_VAULT"] = str(v)
    else:
        env.pop("MEMEX_VAULT", None)
    r = subprocess.run([sys.executable, str(FW / "tools" / "integrate.py"), "--remove"], env=env,
                       capture_output=True, text=True)
    report += [l.strip() for l in (r.stdout + r.stderr).splitlines() if l.strip()]
    ml.config_path().unlink(missing_ok=True)
    INTEGRATION.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        ml.config_path().parent.rmdir()
    report.append(f"removed {ml.config_path().parent}")
    hook = FW / ".git" / "hooks" / "pre-commit"
    if hook.exists() and "Managed by Memex setup" in hook.read_text(errors="ignore"):
        hook.unlink()
        report.append("removed the framework's pre-commit hook")
    keep_nested = exists and not delete_vault and ml.inside(v, FW)
    if not keep_nested:  # a kept vault inside the framework stays git-ignored there
        drop_framework_exclude()
    report += drop_path_lines()
    if exists and delete_vault:
        shutil.rmtree(v)
        report.append(f"deleted the vault {v}")
    elif exists:
        detach_vault(v)
        report.append(f"kept the vault {v} as plain Markdown + git (no Memex files, hooks or settings left in it)")
    elif v:
        report.append(f"the vault {v} was already gone")
    kept = ["your backups", "the remote repository (if any)", "Obsidian's vault list"]
    if "claude" in state:
        kept.append("Claude Code's own data for the vault in ~/.claude/projects/")
    if "hermes" in state:
        kept.append("Hermes' skill trust for the vault path")
    report.append("Not touched: " + ", ".join(kept) + ".")
    return report, leftovers(v)


def leftovers(v: Path = None) -> list:
    """Places on this machine where Memex wiring is still present (empty after a clean uninstall)."""
    home, found = Path.home(), []

    def has(p, needle):
        return p.is_file() and needle in p.read_text(errors="ignore")

    for p in (home / ".claude" / "CLAUDE.md", home / ".codex" / "AGENTS.md", home / ".hermes" / "config.yaml"):
        if has(p, "memex:begin"):
            found.append(f"{p}: Memex instructions block")
    for p in (home / ".claude" / "settings.json", home / ".codex" / "hooks.json"):
        if has(p, str(FW)) or (v and has(p, str(v))):
            found.append(f"{p}: Memex hooks or permissions")
    if v and has(home / ".codex" / "config.toml", f'"{v}"'):
        found.append(f"{home / '.codex' / 'config.toml'}: trust or writable root for {v}")
    for p in (home / ".codex" / "rules" / "memex.rules", home / ".claude" / "skills" / "memex",
              home / ".agents" / "skills" / "memex", home / ".hermes" / "skills" / "memex",
              home / ".local" / "bin" / "memex", ml.config_path(), INTEGRATION):
        if p.exists() or p.is_symlink():
            found.append(str(p))
    if has(FW / ".git" / "hooks" / "pre-commit", "Managed by Memex setup"):
        found.append(f"{FW}/.git/hooks/pre-commit (the framework's Memex hook)")
    if v and (v / ".memex" / "state.json").exists():
        found.append(f"{v}: managed files from the framework (AGENTS.md, .claude/, …)")
    return found


def main():
    ap = argparse.ArgumentParser(description="Memex vault setup and maintenance")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--vault")
    s.add_argument("--default")
    s.add_argument("--agents", default="auto")
    s.add_argument("--remote", help="attach (or join) the vault's private git remote")
    s.add_argument("--branch", default="main")
    s.add_argument("--unverified", action="store_true", help="the remote's privacy can't be checked (own server)")
    s = sub.add_parser("uninstall")
    s.add_argument("--delete-vault", action="store_true")
    s.add_argument("--confirm")
    s = sub.add_parser("leftovers")
    s.add_argument("--vault", help="the vault that was uninstalled (also checks for Memex files left in it)")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the child tools' output
    try:
        if a.cmd == "setup":
            setup(a)
        elif a.cmd == "uninstall":
            sys.exit(print_uninstall(*uninstall(a.delete_vault, a.confirm)))
        else:
            path = a.vault or ml.load_config().get("vault")
            sys.exit(print_leftovers(leftovers(Path(path).expanduser().resolve() if path else None)))
    except VaultError as e:
        print(f"memex: {e}", file=sys.stderr)
        sys.exit(2)


def print_leftovers(left) -> int:
    if not left:
        print("✓ no Memex wiring left on this machine")
        return 0
    print("Memex wiring still present:")
    for x in left:
        print(f"  ✗ {x}")
    return 1


def print_uninstall(report, left) -> int:
    print("Memex uninstall:")
    for line in report:
        print(f"  {line}")
    return print_leftovers(left)


if __name__ == "__main__":
    main()
