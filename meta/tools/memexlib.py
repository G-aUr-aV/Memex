"""Shared helpers for Memex tools (stdlib only)."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", ".obsidian", ".trash", ".claude", "node_modules"}
DOMAINS = ("engineering", "learning", "personal")
GENERATED = {"wiki/index.md", "wiki/engineering/Engineering Index.md", "wiki/learning/Learning Index.md",
             "wiki/personal/Personal Index.md"}
SPECIAL = GENERATED | {"wiki/log.md", "wiki/hot.md"}

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


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def iter_files(base: Path = ROOT, exts=None):
    for dirpath, dirnames, filenames in os.walk(base):
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
