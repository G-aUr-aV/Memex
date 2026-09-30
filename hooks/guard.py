#!/usr/bin/env python3
"""Memex pre-tool guard: one script for Claude Code, Codex and Hermes.

Wiring (rendered into the vault by `memex sync`, and installed globally by setup.sh):
  Claude Code  PreToolUse     Edit|Write|MultiEdit|NotebookEdit|Bash  <vault>/.claude/settings.json, ~/.claude/settings.json
  Codex        PreToolUse     apply_patch|Edit|Write|Bash             <vault>/.codex/hooks.json, ~/.codex/hooks.json
  Hermes       pre_tool_call  write_file|patch|terminal               ~/.hermes/config.yaml

It enforces the ownership table in the vault's AGENTS.md for paths inside THE VAULT ($MEMEX_VAULT, or
"vault" in ~/.config/memex/config.json), whatever directory the agent is working in. Paths outside the
vault pass, and with no vault configured every call passes.
  notes/     the owner's writing: never written, moved or deleted by an agent
  raw/       create-only: existing sources are immutable
  journal/   create-only, except edits confined to the `> [!brief]` callout; never deleted
  wiki/      `> [!mine]` blocks survive verbatim; full overwrites may not shrink a page by >30%
  content    deleting files in wiki/ inbox/ outputs/ goes through `memex rm` (trash + backlink check)
  managed    files rendered from the framework by `memex sync` (AGENTS.md, .claude/skills, …) are never
             changed inside the vault; .memex/vault.json and .memex/state.json belong to the owner and the tools
  container  the vault, or a folder holding it (such as the framework clone), is never deleted or moved;
             where the vault sits inside that folder, `git clean -ff/-x/-X` and `git stash --all` are blocked
Shell commands get a best-effort check (rm, mv, sed -i, redirects, obsidian delete… naming protected
files). Git history and the pre-commit hook remain the backstop.
Exit 2 blocks the call and stderr is the reason: Claude Code, Codex and Hermes all accept this.
"""
import glob
import json
import os
import re
import shlex
import sys

FOLD = sys.platform == "darwin" or os.name == "nt"  # case-insensitive filesystems by default


def fold(s):
    return s.lower() if FOLD else s


def configured_vault():
    v = os.environ.get("MEMEX_VAULT")
    if not v:
        try:
            with open(os.path.join(os.path.expanduser("~"), ".config", "memex", "config.json"), encoding="utf-8") as f:
                v = (json.load(f) or {}).get("vault")
        except Exception:
            v = None
    if not v:
        return None
    v = os.path.realpath(os.path.expanduser(str(v)))
    return v if os.path.isdir(v) else None


FRAMEWORK = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ROOT = configured_vault()
MINE = re.compile(r"(?m)^> \[!mine\][+-]?[^\n]*\n(?:>[^\n]*(?:\n|$))*")
BRIEF = re.compile(r"(?m)^> \[!brief\][+-]?[^\n]*\n(?:>[^\n]*(?:\n|$))*")
PLACEHOLDERS = {"", "(none yet)", "(none yet.)", "-"}
SHRINK_EXEMPT = {"wiki/hot.md"}
IMMUTABLE = {"raw", "notes", "journal"}            # never deleted, moved or overwritten by an agent
CONTENT = IMMUTABLE | {"wiki", "inbox", "outputs"}  # deletions here go through `memex rm`
OWNER_ONLY = {".memex/vault.json", ".memex/state.json"}
DELETE_VERBS = {"rm", "rmdir", "unlink", "shred", "trash", "srm"}
WRITE_VERBS = {"truncate", "tee"}
COPY_VERBS = {"cp", "install", "rsync", "ln"}
SHELLS = {"sh", "bash", "zsh", "dash"}
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "\n", ";;", "|&"}
_managed = None


class Deny(Exception):
    pass


def where(path, cwd):
    """Return (vault-relative posix path, top folder) for a path inside the vault, else None."""
    if not path or not ROOT:
        return None
    path = os.path.expandvars(os.path.expanduser(str(path)))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    real = os.path.realpath(path)
    if fold(real) == fold(ROOT):
        return ".", "."
    if not fold(real).startswith(fold(ROOT) + os.sep):
        return None
    r = real[len(ROOT) + 1:].replace(os.sep, "/")
    return r, r.split("/", 1)[0].lower()


def holds_vault(path):
    """True if path is the vault or a folder that contains it."""
    if not ROOT:
        return False
    real = fold(os.path.realpath(path).rstrip(os.sep))
    return fold(ROOT) == real or fold(ROOT).startswith(real + os.sep)


def managed():
    """Files and folders `memex sync` renders into the vault (from .memex/state.json)."""
    global _managed
    if _managed is None:
        try:
            with open(os.path.join(ROOT, ".memex", "state.json"), encoding="utf-8") as f:
                state = json.load(f)
            _managed = ({fold(k) for k in state.get("files", {})}, [fold(d).rstrip("/") + "/" for d in state.get("dirs", [])])
        except Exception:
            _managed = (set(), [])
    return _managed


def is_managed(r):
    files, dirs = managed()
    fr = fold(r)
    return fr in files or any(fr.startswith(d) for d in dirs)


def check_owned(r):
    if fold(r) in OWNER_ONLY:
        raise Deny(f"{r} holds the vault's settings or sync state; only the owner (or memex setup/sync) changes it.")
    if is_managed(r):
        raise Deny(f"{r} is rendered from the Memex framework by `memex sync`, so a change here would be overwritten. "
                   f"Change it in the framework ({FRAMEWORK}) with the owner's approval, or propose a vault-only rule "
                   "for .memex/local.md.")


def read(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def apply_edits(old, edits):
    """edits: list of (old_string, new_string, replace_all). Returns (new_text, all_found)."""
    new, found = old, True
    for o, n, all_ in edits:
        if not o:
            continue
        if o not in new:
            found = False
            continue
        new = new.replace(o, n) if all_ else new.replace(o, n, 1)
    return new, found


def block_body(block):
    lines = block.rstrip("\n").split("\n")[1:]
    return "\n".join(l.lstrip(">").strip() for l in lines).strip()


def real_mine_blocks(text):
    return [b for b in MINE.findall(text) if block_body(b).lower() not in PLACEHOLDERS]


# ---------- tool payloads -> operations ----------

def parse_patch(text):
    """Codex apply_patch / Hermes patch(mode=patch) format -> operations."""
    ops, cur = [], None
    for line in text.splitlines():
        for tag, kind in (("*** Add File: ", "add"), ("*** Update File: ", "update"), ("*** Delete File: ", "delete")):
            if line.startswith(tag):
                cur = {"kind": kind, "path": line[len(tag):].strip(), "lines": [], "hunks": [[]], "move": None}
                ops.append(cur)
                break
        else:
            if line.startswith("*** Move to: ") and cur and cur["kind"] == "update":
                cur["move"] = line[len("*** Move to: "):].strip()
            elif line.startswith("***") or cur is None:
                continue
            elif cur["kind"] == "add":
                cur["lines"].append(line[1:] if line.startswith("+") else line)
            elif cur["kind"] == "update":
                if line.startswith("@@"):
                    cur["hunks"].append([])
                else:
                    cur["hunks"][-1].append(line)
    out = []
    for op in ops:
        if op["kind"] == "add":
            out.append(("write", op["path"], "\n".join(op["lines"]) + "\n"))
        elif op["kind"] == "delete":
            out.append(("delete", op["path"], None))
        else:
            edits = []
            for h in op["hunks"]:
                if not any(l[:1] in "+-" for l in h if l):
                    continue
                old = "\n".join(l[1:] if l[:1] in " -" else l for l in h if l[:1] != "+")
                new = "\n".join(l[1:] if l[:1] in " +" else l for l in h if l[:1] != "-")
                edits.append((old, new, False))
            out.append(("edit", op["path"], edits))
            if op["move"]:
                out.append(("move", op["path"], op["move"]))
    return out


def shell_text(cmd):
    if isinstance(cmd, list):  # Codex may send argv, e.g. ["bash", "-lc", "…"]
        if len(cmd) >= 3 and os.path.basename(str(cmd[0])) in SHELLS and str(cmd[1]).startswith("-") and "c" in str(cmd[1]):
            return str(cmd[2])
        return " ".join(shlex.quote(str(c)) for c in cmd)
    return str(cmd or "")


def operations(d):
    tool = d.get("tool_name") or ""
    ti = d.get("tool_input") or d.get("args") or {}
    if not isinstance(ti, dict):
        ti = {"command": ti}
    cmd = ti.get("command")
    patch = ti.get("patch") or ti.get("input") or (cmd if isinstance(cmd, str) and "*** Begin Patch" in cmd else None)
    if tool == "apply_patch" or (patch and "*** Begin Patch" in str(patch)) or (tool == "patch" and ti.get("mode") == "patch"):
        return parse_patch(str(patch or cmd or ""))
    if tool in ("Bash", "terminal", "shell", "exec_command", "local_shell"):
        return [("shell", None, shell_text(cmd if cmd is not None else ti.get("cmd")))]
    if tool in ("Write", "write_file"):
        return [("write", ti.get("file_path") or ti.get("path"), ti.get("content", ""))]
    if tool in ("Edit", "patch"):
        return [("edit", ti.get("file_path") or ti.get("path"),
                 [(ti.get("old_string", ""), ti.get("new_string", ""), bool(ti.get("replace_all")))])]
    if tool == "MultiEdit":
        return [("edit", ti.get("file_path"),
                 [(e.get("old_string", ""), e.get("new_string", ""), bool(e.get("replace_all"))) for e in ti.get("edits") or []])]
    if tool == "NotebookEdit":
        return [("edit", ti.get("notebook_path"), None)]
    return []


# ---------- rules ----------

def check_write(r, top, path, content):
    exists = os.path.exists(path)
    if top == "notes":
        raise Deny(f"{r} is the owner's own writing (notes/). Suggest the text in chat instead.")
    if top == "raw" and exists:
        raise Deny(f"{r} is an immutable raw source. Put annotations on the wiki source page instead.")
    if top == "journal" and exists:
        raise Deny(f"{r} is the owner's journal. You may only create today's note or edit its > [!brief] block.")
    if top == "wiki" and exists and path.endswith(".md") and content is not None:
        old = read(path)
        check_mine(r, old, content)
        if r not in SHRINK_EXEMPT and len(old) > 400 and len(content) < 0.7 * len(old):
            raise Deny(f"overwriting {r} would shrink it by more than 30%. Use surgical edits (or split the page) instead of rewriting it.")


def check_mine(r, old, new):
    # An old block must reappear as an exact block, so appending lines inside it (writing in the owner's voice) is blocked too.
    new_blocks = {b.rstrip("\n") for b in MINE.findall(new)}
    for block in real_mine_blocks(old):
        if block.rstrip("\n") not in new_blocks:
            raise Deny(f"this edit would change, extend or remove one of the owner's > [!mine] blocks in {r}. Keep it verbatim and write outside it (leave a blank line after it).")


def check_edit(r, top, path, edits):
    if top == "notes":
        raise Deny(f"{r} is the owner's own writing (notes/). Suggest the text in chat instead.")
    if not os.path.exists(path):
        return
    if top == "raw":
        raise Deny(f"{r} is an immutable raw source. Put annotations on the wiki source page instead.")
    if top not in ("journal", "wiki") or not path.endswith(".md"):
        return
    old = read(path)
    if edits is None:
        raise Deny(f"{r}: this kind of edit can't be checked; use a plain text edit.")
    new, found = apply_edits(old, edits)
    if top == "journal":
        if not found:
            raise Deny(f"{r}: couldn't match this edit to the file's current text. Re-read it and edit exact lines of the > [!brief] block.")
        if BRIEF.sub("", old) != BRIEF.sub("", new):
            raise Deny(f"{r}: only the > [!brief] callout may be edited (keep every line of it starting with '>').")
    elif top == "wiki":
        if not found and real_mine_blocks(old):
            raise Deny(f"{r}: couldn't match this edit to the file's current text, so the owner's > [!mine] blocks can't be verified. Re-read the file and edit exact lines.")
        check_mine(r, old, new)


def check_delete(r, top):
    if r == ".":
        raise Deny(f"this would delete or move the Memex vault ({ROOT}) or a folder that contains it.")
    if top in IMMUTABLE:
        raise Deny(f"{r} can't be deleted: raw/ is immutable, and notes/ and journal/ belong to the owner.")
    check_owned(r)
    if top in CONTENT:
        raise Deny(f"delete {r} with `memex rm \"{r}\"`, which checks backlinks and keeps a copy in .trash/.")


def check_move(r, top, dst):
    if r == ".":
        raise Deny(f"this would move the Memex vault ({ROOT}) or a folder that contains it; the vault's path is recorded in ~/.config/memex/config.json.")
    if top in IMMUTABLE:
        raise Deny(f"{r} can't be moved or renamed: raw/ is immutable, and notes/ and journal/ belong to the owner.")
    check_owned(r)
    if dst:
        if dst[1] == "notes":
            raise Deny("agents don't write into notes/ (the owner's own writing).")
        check_owned(dst[0])


def tokens(cmd):
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return cmd.split()


def protected(tok, cwd, tops, whole=False, owned=False):
    """(rel, top) if tok names an existing path in the vault whose top folder is in tops (or, with owned=True,
    a managed or owner-only file). With whole=True, the vault itself or a folder containing it counts too
    (reported as ".")."""
    if not tok or tok.startswith("-") or not ROOT:
        return None
    if any(c in tok for c in "*?["):  # a glob: check what it would match
        pattern = os.path.join(cwd, os.path.expandvars(os.path.expanduser(tok)))
        for match in glob.glob(pattern):
            w = protected(match, cwd, tops, whole, owned)
            if w:
                return w
        return None
    w = where(tok, cwd)
    if not w:
        if whole and holds_vault(os.path.join(cwd, os.path.expandvars(os.path.expanduser(tok)))):
            return ".", "."
        return None
    r, top = w
    if r == ".":
        return w if whole else None
    if not os.path.exists(os.path.join(ROOT, r)):
        return None
    if top in tops or (owned and (fold(r) in OWNER_ONLY or is_managed(r))):
        return w
    return None


def deny_overwrite(w, how="overwrite"):
    if w[1] in IMMUTABLE:
        raise Deny(f"this command would {how} {w[0]}; raw/ is immutable and notes/ and journal/ belong to the owner.")
    check_owned(w[0])


def check_git(args, cwd):
    while args and args[0].startswith("-"):  # global options: git -C <dir> …, git -c k=v …
        if args[0] == "-C" and len(args) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(args[1]))
            args = args[2:]
        elif args[0] == "-c" and len(args) > 1:
            args = args[2:]
        else:
            args = args[1:]
    if not args:
        return
    sub, rest = args[0], args[1:]
    flags = [a for a in rest if a.startswith("-")]
    short = "".join(a[1:] for a in flags if not a.startswith("--"))
    if sub == "clean":
        if where(cwd, cwd):
            raise Deny("git clean inside the vault would delete uncommitted captures and notes.")
        forces = short.count("f") + flags.count("--force")
        if holds_vault(cwd) and (forces >= 2 or "x" in short or "X" in short):
            raise Deny(f"this git clean could delete the Memex vault ({ROOT}), which sits inside this folder and "
                       "has no remote. Clean specific paths instead.")
    if sub == "stash" and holds_vault(cwd) and ("--all" in flags or "a" in short):
        raise Deny(f"git stash --all would sweep the Memex vault ({ROOT}), which sits inside this folder, into a stash.")
    if sub in ("rm", "mv", "checkout", "restore"):
        paths = [a for a in rest if not a.startswith("-")]
        for a in paths:
            w = protected(a, cwd, CONTENT if sub in ("rm", "mv") else IMMUTABLE, whole=True, owned=sub in ("rm", "mv"))
            if w:
                raise Deny(f"git {sub} on {w[0]} isn't allowed: raw/ is immutable, notes/ and journal/ belong "
                           "to the owner, and other files are removed with `memex rm` or moved with `obsidian move`.")


def check_shell(cmd, cwd, depth=0):
    toks = tokens(cmd)
    seg, segs = [], []
    for t in toks:
        if t in SEPARATORS:
            segs.append(seg)
            seg = []
        else:
            seg.append(t)
    segs.append(seg)
    for seg in segs:
        # redirections: > file, >> file, &> file
        for i, t in enumerate(seg):
            if set(t) <= set("<>&") and ">" in t and i + 1 < len(seg):
                w = protected(seg[i + 1], cwd, IMMUTABLE, owned=True)
                if w and w[0] != ".":
                    deny_overwrite(w)
        words = [t for t in seg if not (set(t) <= set("<>&0123456789") and (">" in t or "<" in t))]
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("sudo", "command", "env", "nice", "nohup", "time", "xargs", "exec")):
            words = words[1:]
        if not words:
            continue
        verb, args = os.path.basename(words[0]), words[1:]
        if verb == "cd":
            target = os.path.expanduser(args[0]) if args else os.path.expanduser("~")
            cwd = target if os.path.isabs(target) else os.path.join(cwd, target)
            continue
        if verb in SHELLS and depth < 3:
            for i, a in enumerate(args):
                if a.startswith("-") and "c" in a and i + 1 < len(args):
                    check_shell(args[i + 1], cwd, depth + 1)
                    break
            continue
        paths = [a for a in args if not a.startswith("-")]
        if verb in DELETE_VERBS:
            for a in paths:
                w = protected(a, cwd, CONTENT, whole=True, owned=True)
                if w:
                    check_delete(*w)
        elif verb == "mv":
            for a in paths[:-1]:
                w = protected(a, cwd, CONTENT, whole=True, owned=True)
                if w:
                    check_move(*w, None)
                    raise Deny("move vault files with `obsidian move path=\"…\" to=\"…\"` (it keeps links intact), not mv.")
            if paths:
                w = protected(paths[-1], cwd, IMMUTABLE, owned=True)
                if w and w[0] != ".":
                    deny_overwrite(w)
        elif verb in WRITE_VERBS or verb in COPY_VERBS:
            targets = paths if verb in WRITE_VERBS else paths[-1:]
            for a in targets:
                w = protected(a, cwd, IMMUTABLE, owned=True)
                if w and w[0] != ".":
                    deny_overwrite(w)
        elif verb in ("sed", "gsed", "perl") and any(a.startswith("-i") or a == "--in-place" or (verb == "perl" and re.match(r"^-[a-z]*i", a)) for a in args):
            for a in paths:
                w = protected(a, cwd, IMMUTABLE, owned=True)
                if w and w[0] != ".":
                    deny_overwrite(w, "edit in place")
        elif verb == "find" and "-delete" in args:
            for a in paths:
                if protected(a, cwd, CONTENT, whole=True):
                    raise Deny("find -delete inside the vault isn't allowed; remove files with `memex rm`.")
        elif verb == "git" and args:
            check_git(args, cwd)
        elif verb == "obsidian" and args and args[0] in ("delete", "move", "rename"):
            for a in args[1:]:
                k, _, v = a.partition("=")
                if not v:
                    continue
                if k == "path":
                    w = where(v, ROOT)
                elif k == "file":
                    w = next((x for x in (where(p, ROOT) for p in find_named(v)) if x), None)
                elif k == "to":
                    dst = where(v, ROOT)
                    if (dst or ("", ""))[1] == "notes":
                        raise Deny("agents don't write into notes/ (the owner's own writing).")
                    if dst:
                        check_owned(dst[0])
                    continue
                else:
                    continue
                if not w:
                    continue
                if args[0] == "delete":
                    check_delete(*w)
                elif w[1] in IMMUTABLE:
                    check_move(*w, None)
                else:
                    check_owned(w[0])


def find_named(name):
    target = name.lower().removesuffix(".md")
    for top in sorted(CONTENT):
        for dirpath, dirnames, files in os.walk(os.path.join(ROOT, top)):
            for f in files:
                if f.lower().removesuffix(".md") == target:
                    yield os.path.join(dirpath, f)


def evaluate(d):
    if not ROOT:
        return
    cwd = d.get("cwd") or os.getcwd()
    for kind, target, data in operations(d):
        if kind == "shell":
            check_shell(data or "", cwd)
            continue
        w = where(target, cwd)
        if not w:
            continue
        r, top = w
        path = os.path.join(ROOT, r)
        if kind == "write":
            check_owned(r)
            check_write(r, top, path, data)
        elif kind == "edit":
            check_owned(r)
            check_edit(r, top, path, data)
        elif kind == "delete":
            check_delete(r, top)
        elif kind == "move":
            check_move(r, top, where(data, cwd))


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        evaluate(d)
    except Deny as e:
        print(f"Memex guard: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # a guard bug must not block unrelated work outside the vault
        if ROOT and fold(ROOT) in fold(json.dumps(d, ensure_ascii=False)):
            print(f"Memex guard: couldn't check this call ({type(e).__name__}: {e}); blocked to be safe.", file=sys.stderr)
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
