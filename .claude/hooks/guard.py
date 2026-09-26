#!/usr/bin/env python3
"""Memex PreToolUse guard (matcher: Edit|Write|MultiEdit|NotebookEdit).

Enforces the ownership table in CLAUDE.md deterministically:
  - notes/**            human-only: never written by the agent
  - raw/**              create-only: existing sources are immutable
  - journal/**          create-only, except edits confined to the `> [!brief]` callout
  - wiki/**             `> [!mine]` blocks must survive verbatim (placeholders excepted);
                        full-file overwrites may not shrink a page by more than 30%
Exit 2 blocks the call; stderr is shown to Claude as the reason.
"""
import json
import os
import re
import sys

MINE = re.compile(r"(?m)^> \[!mine\][+-]?[^\n]*\n(?:>[^\n]*(?:\n|$))*")
BRIEF = re.compile(r"(?m)^> \[!brief\][+-]?[^\n]*\n(?:>[^\n]*(?:\n|$))*")
PLACEHOLDERS = {"", "(none yet)", "(none yet.)", "-"}
SHRINK_EXEMPT = {"wiki/hot.md"}


def deny(msg):
    print(f"Memex guard: {msg}", file=sys.stderr)
    sys.exit(2)


def block_body(block):
    lines = block.rstrip("\n").split("\n")[1:]
    return "\n".join(l.lstrip(">").strip() for l in lines).strip()


def apply_edits(old, tool, ti):
    if tool == "Write":
        return ti.get("content", "")
    new = old
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    for e in edits or []:
        o, n = e.get("old_string", ""), e.get("new_string", "")
        if not o:
            continue
        new = new.replace(o, n) if e.get("replace_all") else new.replace(o, n, 1)
    return new


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not target:
        sys.exit(0)
    root = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or d.get("cwd") or ".")
    if not os.path.isabs(target):
        target = os.path.join(d.get("cwd") or root, target)
    path = os.path.realpath(target)
    if not (path == root or path.startswith(root + os.sep)):
        sys.exit(0)
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    top = parts[0].lower()
    rel_posix = "/".join(parts)
    exists = os.path.exists(path)

    if top == "notes":
        deny(f"{rel} is the owner's own writing (notes/). Suggest the text in chat instead.")

    if top == "raw" and exists:
        deny(f"{rel} is an immutable raw source. Put annotations on the wiki source page instead.")

    if top == "journal" and exists:
        if tool != "Edit" and tool != "MultiEdit":
            deny(f"{rel} is the owner's journal. You may only create today's note or edit its > [!brief] block.")
        old = open(path, encoding="utf-8", errors="ignore").read()
        new = apply_edits(old, tool, ti)
        if BRIEF.sub("", old) != BRIEF.sub("", new):
            deny(f"{rel}: only the > [!brief] callout may be edited (keep every line of it starting with '>').")
        sys.exit(0)

    if top == "wiki" and exists and path.endswith(".md"):
        old = open(path, encoding="utf-8", errors="ignore").read()
        new = apply_edits(old, tool, ti)
        # Compare whole blocks: an old block must reappear as an exact block in the new text,
        # so appending lines inside it (writing in the owner's voice) is blocked too.
        new_blocks = {b.rstrip("\n") for b in MINE.findall(new)}
        for block in MINE.findall(old):
            if block_body(block).lower() in PLACEHOLDERS:
                continue
            if block.rstrip("\n") not in new_blocks:
                deny(f"this edit would change, extend or remove one of the owner's > [!mine] blocks in {rel}. Keep it verbatim and write outside it (leave a blank line after it).")
        if tool == "Write" and rel_posix not in SHRINK_EXEMPT and len(old) > 400 and len(new) < 0.7 * len(old):
            deny(f"overwriting {rel} would shrink it by more than 30%. Use surgical Edit calls (or split the page) instead of rewriting it.")

    sys.exit(0)


if __name__ == "__main__":
    main()
