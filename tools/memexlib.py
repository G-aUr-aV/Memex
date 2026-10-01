"""Shared helpers for Memex tools (stdlib only).

Memex has two roots, and they never overlap:
  FRAMEWORK  this checkout of the framework: tools, hooks, schema, skills, templates. It holds no knowledge
             and is the repo you push.
  vault()    the owner's knowledge (inbox/ raw/ wiki/ journal/ notes/ outputs/): a separate, local-first git
             repo, found through $MEMEX_VAULT or "vault" in ~/.config/memex/config.json (written by setup.sh).
             It has no remote unless the owner attaches one private remote (`memex remote set`; see remote.py).
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
MARKER = Path(".memex") / "vault.json"
DEFAULT_IDENTITY = {"name": "Memex Agent", "email": "memex-agent@localhost"}
SKIP_DIRS = {".git", ".obsidian", ".trash", ".claude", ".codex", ".agents", ".memex", "node_modules"}
DOMAIN_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# Folders that only ever hold knowledge; the framework repo refuses to track them.
KNOWLEDGE_DIRS = ("wiki", "raw", "inbox", "journal", "notes", "outputs", ".memex")

# type -> allowed folder names (second level under wiki/<domain>/)
TYPE_FOLDERS = {
    "person": {"people"}, "project": {"projects"}, "system": {"systems"}, "decision": {"decisions"},
    "incident": {"incidents"}, "playbook": {"playbooks"}, "concept": {"concepts"}, "entity": {"entities"},
    "topic": {"topics"}, "source": {"sources"}, "synthesis": {"syntheses"}, "review": {"career", "reflections"},
    "area": {"areas"}, "goal": {"goals"}, "idea": {"ideas"},
}
REQUIRED = ("type", "domain", "status", "summary", "created", "updated")
FACTUAL_TYPES = {"concept", "entity", "person", "project", "system", "decision", "incident", "topic", "area", "source"}

LINK = re.compile(r"(!?)\[\[([^\]\|#\^]*)(#\^?[^\]\|]*)?(?:\|([^\]]*))?\]\]")
FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


class VaultError(Exception):
    pass


# ---------- config & git ----------

def config_path() -> Path:
    return Path.home() / ".config" / "memex" / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_config(cfg: dict):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


# Variables git sets for hooks that pin a command to one repo; drop them before touching another repo.
GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")


def git_env(extra=None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS}
    env.update(extra or {})
    return env


def git(cwd, *args, env=None, input=None, timeout=None):
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env or git_env(),
                              input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 124, "", f"timed out after {timeout}s")


def repo_top(path: Path):
    """The git work tree containing path (or its nearest existing ancestor), or None."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    r = git(p, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def inside(child: Path, parent: Path) -> bool:
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


def framework_ignores(path: Path) -> bool:
    """True if the framework repo git-ignores path (so a vault there can never be committed to it)."""
    r = git(FRAMEWORK, "check-ignore", "-q", Path(path).resolve().relative_to(FRAMEWORK).as_posix() + "/")
    return r.returncode == 0


def framework_sha() -> str:
    r = git(FRAMEWORK, "rev-parse", "--short", "HEAD")
    if r.returncode or not r.stdout.strip():
        return "unknown"
    dirty = git(FRAMEWORK, "diff", "--quiet", "HEAD").returncode != 0
    return r.stdout.strip() + ("+dirty" if dirty else "")


def guard_command() -> str:
    """The exact hook command for the guard; the vault's settings and the global ones must match byte for byte."""
    return "python3 " + shlex.quote(str(FRAMEWORK / "hooks" / "guard.py"))


def memex_command() -> str:
    return "python3 " + shlex.quote(str(FRAMEWORK / "tools" / "memex.py"))


# ---------- the vault ----------

def check_vault_path(v: Path):
    v = Path(v).resolve()
    if v == FRAMEWORK:
        raise VaultError(f"{v} is the framework itself. The vault must be a separate folder (setup.sh creates one).")
    if inside(FRAMEWORK, v):
        raise VaultError(f"{v} contains the framework ({FRAMEWORK}); put the vault somewhere else.")
    if inside(v, FRAMEWORK) and not framework_ignores(v):
        raise VaultError(f"{v} is inside the framework clone but not git-ignored there, so its knowledge could be "
                         f"committed to the framework. Re-run setup.sh (it adds the path to {FRAMEWORK}/.git/info/exclude).")


_vault = None


def vault() -> Path:
    """The configured vault. Raises VaultError if none is configured or it isn't a valid vault."""
    global _vault
    if _vault is not None:
        return _vault
    env = os.environ.get("MEMEX_VAULT")
    src = "$MEMEX_VAULT" if env else str(config_path())
    path = env or load_config().get("vault")
    if not path:
        raise VaultError("no vault configured. Run setup.sh from the Memex framework "
                         f"(bash \"{FRAMEWORK}/setup.sh\"), or set MEMEX_VAULT.")
    v = Path(path).expanduser().resolve()
    if not (v / MARKER).is_file():
        raise VaultError(f"{v} (from {src}) is not a Memex vault: {MARKER} is missing. Re-run setup.sh.")
    check_vault_path(v)
    _vault = v
    return v


def require_vault() -> Path:
    """vault(), or exit with the reason (for command-line tools)."""
    try:
        return vault()
    except VaultError as e:
        print(f"memex: {e}", file=sys.stderr)
        sys.exit(2)


def settings(v: Path = None) -> dict:
    v = v or vault()
    try:
        return json.loads((v / MARKER).read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise VaultError(f"{v / MARKER} isn't valid JSON ({e})")


FEATURE_DEFAULTS = {
    # session ledger + /harvest: turn agent sessions in other repos into knowledge
    "harvest": {"enabled": True, "exclude": [], "min_tool_calls": 5},
    # memex backup: git bundles of the vault (the only copy lives on this disk)
    "backup": {"dir": "", "keep": 10, "warn_days": 14},
    # repo-aware recall printed at the start of agent sessions in other repos
    "recall": {"enabled": True, "max_pages": 3},
}


def feature(name: str, v: Path = None) -> dict:
    """A feature's settings from .memex/vault.json, over the defaults above."""
    try:
        own = settings(v).get(name) or {}
    except VaultError:
        own = {}
    return {**FEATURE_DEFAULTS[name], **(own if isinstance(own, dict) else {})}


def identity(v: Path = None) -> dict:
    ident = settings(v).get("identity") or {}
    return {"name": str(ident.get("name") or DEFAULT_IDENTITY["name"]),
            "email": str(ident.get("email") or DEFAULT_IDENTITY["email"])}


# ---------- domains ----------

def framework_domains() -> dict:
    return json.loads((FRAMEWORK / "schema" / "domains.json").read_text(encoding="utf-8"))


def domain_specs(v: Path = None):
    """The vault's active domains, in order. Each spec: name, title, blurb, summary, note, folders, color, rule_base.
    Presets come from the framework's schema/domains.json; a vault may add or override domains in
    .memex/domains.json ({"domains": {"work": {"extends": "engineering", ...}}}) and picks its set with
    "domains" in .memex/vault.json ("default", or absent, means the framework's default set)."""
    v = v or vault()
    fw = framework_domains()
    catalog = {n: {**s, "rule_base": n} for n, s in fw["domains"].items()}
    local_file = v / ".memex" / "domains.json"
    if local_file.exists():
        try:
            local = json.loads(local_file.read_text(encoding="utf-8")).get("domains") or {}
        except Exception as e:
            raise VaultError(f"{local_file} isn't valid JSON ({e})")
        for name, spec in local.items():
            parent = spec.get("extends")
            if parent and parent not in catalog:
                raise VaultError(f"{local_file}: domain {name!r} extends unknown domain {parent!r}")
            base = dict(catalog[parent]) if parent else dict(catalog.get(name, {}))
            base.update({k: val for k, val in spec.items() if k != "extends"})
            base["rule_base"] = base.get("rule_base") if parent or name in catalog else None
            base["local"] = True
            catalog[name] = base
    names = settings(v).get("domains")
    if not names or names == "default":
        names = fw["default"]
    out = []
    for n in names:
        if not DOMAIN_NAME.match(str(n)):
            raise VaultError(f"domain name {n!r} must be lowercase letters, digits or dashes")
        if n not in catalog:
            raise VaultError(f"unknown domain {n!r} in {v / MARKER}; define it in {local_file}")
        spec = {"title": f"{n.capitalize()} Index", "blurb": "", "summary": n, "note": "", "color": None, **catalog[n]}
        if not spec.get("folders"):
            raise VaultError(f"domain {n!r} needs a non-empty \"folders\" list")
        spec["name"] = n
        out.append(spec)
    return out


def domain_names(v: Path = None):
    return tuple(d["name"] for d in domain_specs(v))


def generated(v: Path = None):
    return {"wiki/index.md"} | {f"wiki/{d['name']}/{d['title']}.md" for d in domain_specs(v)}


def special(v: Path = None):
    return generated(v) | {"wiki/log.md", "wiki/hot.md"}


# ---------- files & markdown ----------

def rel(p: Path, root: Path = None) -> str:
    return Path(p).relative_to(root or vault()).as_posix()


def iter_files(base: Path = None, exts=None):
    for dirpath, dirnames, filenames in os.walk(base or vault()):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.startswith("."):
                continue
            if exts and not f.lower().endswith(exts):
                continue
            yield Path(dirpath) / f


def parse_scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        items, buf, depth, q = [], "", 0, None
        for ch in inner:
            if q:
                buf += ch
                if ch == q:
                    q = None
                continue
            if ch in "\"'":
                q = ch
                buf += ch
            elif ch == "[":
                depth += 1
                buf += ch
            elif ch == "]":
                depth -= 1
                buf += ch
            elif ch == "," and depth == 0:
                items.append(parse_scalar(buf))
                buf = ""
            else:
                buf += ch
        if buf.strip():
            items.append(parse_scalar(buf))
        return items
    return v


def frontmatter(text: str):
    """Minimal YAML frontmatter parser: `key: value`, inline lists, and `- item` lists."""
    m = FM.match(text)
    if not m:
        return {}, text
    data, key = {}, None
    for line in m.group(1).split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t", "-")) and key is not None:
            s = line.strip()
            if s.startswith("- "):
                if not isinstance(data.get(key), list):
                    data[key] = []
                data[key].append(parse_scalar(s[2:]))
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            v = re.sub(r"\s+#.*$", "", v) if not v.strip().startswith(("\"", "'")) else v
            data[key] = parse_scalar(v) if v.strip() else ""
    return data, text[m.end():]


def build_resolver():
    """Obsidian-style link resolution: by vault path (with/without extension) or by basename."""
    by_path, by_name = {}, {}
    for p in iter_files():
        r = rel(p)
        low = r.lower()
        by_path[low] = p
        if low.endswith(".md"):
            by_path[low[:-3]] = p
        name = p.name.lower()
        by_name.setdefault(name, []).append(p)
        if name.endswith(".md"):
            by_name.setdefault(name[:-3], []).append(p)

    def resolve(target: str):
        t = target.strip().replace("\\", "/")
        if not t:
            return None
        low = t.lower().lstrip("/")
        if low in by_path:
            return by_path[low]
        if "/" in low:
            # partial path match (suffix)
            cands = [p for k, p in by_path.items() if k.endswith("/" + low) or k.endswith("/" + low + ".md")]
            return cands[0] if cands else None
        c = by_name.get(low)
        return c[0] if c else None

    return resolve, by_name


def headings(text: str):
    out = set()
    for line in text.split("\n"):
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            out.add(norm_heading(m.group(1)))
    return out


def norm_heading(h: str) -> str:
    h = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", h)
    return re.sub(r"\s+", " ", h).strip().lower()


def norm_text(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"[*_`]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()
