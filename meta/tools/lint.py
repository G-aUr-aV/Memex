#!/usr/bin/env python3
"""Deterministic Memex health check (no LLM).

  python3 meta/tools/lint.py            # errors + warnings + backlog info
  python3 meta/tools/lint.py --quick    # errors only (run after every write operation)
  python3 meta/tools/lint.py --report   # also write meta/lint/YYYY-MM-DD.md

Exit code 1 if there are errors.
"""
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memexlib import (ROOT, DOMAINS, GENERATED, SPECIAL, TYPE_FOLDERS, REQUIRED, FACTUAL_TYPES,  # noqa: E402
                      LINK, build_resolver, frontmatter, headings, iter_files, norm_heading, norm_text, rel)

TODAY = datetime.date.today()
STALE_DAYS, SYSTEM_STALE_DAYS, MAX_LINES, HOT_MAX, INBOX_DAYS = 180, 90, 250, 40, 7
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
QUOTE = re.compile(r'^>\s*["“](.+?)["”]\s*[—–-]+\s*\[\[([^\]\|#]+)', re.M)
DATE_START = re.compile(r"^\d{4}-\d{2}-\d{2}")
BAD_CHARS = re.compile(r"[:\\#^\[\]|]")
SECRETS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Atlassian API token", re.compile(r"\bATATT[A-Za-z0-9_=-]{20,}")),
    ("quoted password", re.compile(r"(?i)\b(?:password|passwd|pwd|passphrase|passcode)\b[^\n]{0,20}?\b(?:is|was|=|:)\s*[\"'\u201c\u2018][^\s\"'\u201d\u2019]{6,}")),
]
SOFT_SECRETS = [
    ("password/secret assignment", re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"<>]{8,}")),
    ("Aadhaar-like number", re.compile(r"\b\d{4} \d{4} \d{4}\b")),
]
CARD = re.compile(r"(?<![\d.,])(?:\d[ -]?){14}\d(?:[ -]?\d)?(?![\d.,]?\d)")


def luhn(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if len(digits) not in (15, 16):
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def parse_date(v):
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def main():
    quick = "--quick" in sys.argv
    report = "--report" in sys.argv
    errors, warnings, info = [], [], []
    resolve, by_name = build_resolver()
    texts, fms, bodies = {}, {}, {}
    md_files = [p for p in iter_files(exts=(".md",))]
    for p in md_files:
        t = p.read_text(encoding="utf-8", errors="ignore")
        texts[p] = t
        fms[p], bodies[p] = frontmatter(t)

    # --- duplicate basenames (ambiguous wikilinks) ---
    for name, paths in by_name.items():
        if name.endswith(".md"):
            continue
        uniq = sorted({rel(p) for p in paths if p.suffix == ".md"})
        if len(uniq) > 1:
            errors.append(f"duplicate note name '{name}' → ambiguous links: {', '.join(uniq)}")

    # --- secrets (all markdown/text in the vault) ---
    for p in list(md_files) + list(iter_files(exts=(".txt", ".canvas", ".base"))):
        t = texts.get(p) or p.read_text(encoding="utf-8", errors="ignore")
        r = rel(p)
        if r.startswith("meta/tools/"):
            continue
        for label, rx in SECRETS:
            if rx.search(t):
                errors.append(f"{r}: possible {label} — remove it and store a pointer instead")
        for m in CARD.finditer(t):
            if luhn(m.group(0)):
                errors.append(f"{r}: possible payment card number — keep last 4 digits only")
                break
        if not quick:
            for label, rx in SOFT_SECRETS:
                if rx.search(CODE.sub("", t)):
                    warnings.append(f"{r}: possible {label} — check and redact if real")

    inbound = defaultdict(set)
    cited_from_wiki = defaultdict(set)
    for p in md_files:
        r = rel(p)
        body = CODE.sub("", bodies[p])
        in_wiki = r.startswith("wiki/")
        check_links = in_wiki and r not in GENERATED
        doc_links = r in ("Home.md", "Memex Manual.md") or r.startswith("outputs/")
        for m in LINK.finditer(body):
            # links inside tables escape the alias pipe as \\| — strip the stray backslash
            target, anchor = m.group(2).rstrip("\\"), (m.group(3) or "").rstrip("\\")
            if not target.strip():
                continue
            dest = resolve(target)
            if dest is None:
                if check_links:
                    errors.append(f"{r}: broken link [[{target}]]")
                elif doc_links and not quick:
                    warnings.append(f"{r}: broken link [[{target}]]")
                continue
            if check_links and rel(dest).startswith("meta/templates/"):
                errors.append(f"{r}: [[{target}]] points at a template placeholder — replace it with a real page or source")
                continue
            if r not in GENERATED and r not in ("wiki/log.md", "wiki/hot.md"):
                inbound[dest].add(p)
            if in_wiki and r not in SPECIAL:
                cited_from_wiki[dest].add(p)
            if check_links and anchor.startswith("#") and not anchor.startswith("#^") and dest.suffix == ".md" and not quick:
                h = norm_heading(anchor[1:])
                if dest in texts and h not in headings(texts[dest]):
                    warnings.append(f"{r}: heading '{anchor[1:]}' not found in [[{target}]]")

    # --- wiki page checks ---
    wiki_pages = [p for p in md_files if rel(p).startswith("wiki/") and rel(p) not in SPECIAL]
    unreviewed, low_conf, conflicts = [], [], []
    for p in wiki_pages:
        r, fm, body = rel(p), fms[p], bodies[p]
        parts = r.split("/")
        if len(parts) < 3 or parts[1] not in DOMAINS:
            errors.append(f"{r}: wiki pages must live under wiki/<work|learning|personal>/<folder>/")
            continue
        missing = [k for k in REQUIRED if not str(fm.get(k, "")).strip()]
        if missing:
            errors.append(f"{r}: missing frontmatter {', '.join(missing)}")
        ptype = str(fm.get("type", "")).strip()
        folder = parts[2] if len(parts) > 3 else ""
        if ptype and ptype in TYPE_FOLDERS and folder not in TYPE_FOLDERS[ptype]:
            errors.append(f"{r}: type '{ptype}' belongs in {'/'.join(sorted(TYPE_FOLDERS[ptype]))}/, not '{folder or '(domain root)'}'")
        elif ptype and ptype not in TYPE_FOLDERS:
            errors.append(f"{r}: unknown type '{ptype}'")
        dom = str(fm.get("domain", "")).strip()
        if dom and dom != parts[1]:
            errors.append(f"{r}: domain '{dom}' doesn't match folder wiki/{parts[1]}/")
        for q, tgt in QUOTE.findall(body):
            src = resolve(tgt)
            if src is None:
                continue  # already reported as broken link
            if norm_text(q) not in norm_text(texts.get(src) or src.read_text(encoding="utf-8", errors="ignore")):
                errors.append(f'{r}: evidence quote not found verbatim in [[{tgt}]]: "{q[:70]}"')
        if quick:
            continue
        if DATE_START.match(p.stem):
            warnings.append(f"{r}: wiki filenames must not start with a date (put it last)")
        if BAD_CHARS.search(p.stem):
            warnings.append(f"{r}: filename has characters that break links")
        summ = str(fm.get("summary", ""))
        if len(summ) > 160:
            warnings.append(f"{r}: summary is {len(summ)} chars (max 160)")
        for k in ("created", "updated", "reviewed", "verified_on", "last_contact"):
            v = str(fm.get(k, "")).strip()
            if v and parse_date(v) is None:
                warnings.append(f"{r}: '{k}: {v}' is not an ISO date")
        if ptype in FACTUAL_TYPES:
            has_src = any((d := resolve(m.group(2))) is not None and rel(d).startswith(("raw/", "journal/"))
                          for m in LINK.finditer(CODE.sub("", body)))
            if not has_src and not str(fm.get("raw", "")).strip() and str(fm.get("uncited_ok", "")).lower() != "true":
                warnings.append(f"{r}: no citation to raw/ or journal/ (integrity rule 1)")
        lines = texts[p].count("\n")
        if lines > MAX_LINES:
            warnings.append(f"{r}: {lines} lines — split it")
        status = str(fm.get("status", "")).strip()
        live = status not in ("archived", "superseded", "done", "dropped", "deprecated")
        upd = parse_date(fm.get("updated", ""))
        if live and upd and (TODAY - upd).days > STALE_DAYS:
            warnings.append(f"{r}: not updated for {(TODAY - upd).days} days — still true?")
        if ptype == "system":
            vo = parse_date(fm.get("verified_on", ""))
            if vo is None:
                warnings.append(f"{r}: system facts never verified against code (verified_on empty)")
            elif (TODAY - vo).days > SYSTEM_STALE_DAYS:
                warnings.append(f"{r}: system facts not re-verified in {SYSTEM_STALE_DAYS} days (verified_on {vo})")
        if ptype != "source" and not inbound.get(p):
            warnings.append(f"{r}: orphan — nothing links here")
        if not str(fm.get("reviewed", "")).strip():
            unreviewed.append(r)
        if str(fm.get("confidence", "")).strip() in ("low", "uncertain"):
            low_conf.append(r)
        if "> [!conflict]" in body:
            conflicts.append(r)

    hot = ROOT / "wiki" / "hot.md"
    if hot.exists() and hot.read_text(encoding="utf-8").count("\n") > HOT_MAX + 6 and not quick:
        warnings.append(f"wiki/hot.md exceeds {HOT_MAX} lines — rewrite it tighter")

    if not quick:
        raw_md = [p for p in md_files if rel(p).startswith("raw/")]
        pending = [rel(p) for p in raw_md if not cited_from_wiki.get(p)]
        inbox = [p for p in iter_files(ROOT / "inbox")]
        old_inbox = []
        for p in inbox:
            m = DATE_START.match(p.name)
            d = parse_date(m.group(0)) if m else datetime.date.fromtimestamp(p.stat().st_mtime)
            if d and (TODAY - d).days > INBOX_DAYS:
                old_inbox.append(rel(p))
        info.append(f"inbox: {len(inbox)} item(s); older than {INBOX_DAYS} days: {len(old_inbox)}" + (f" → {', '.join(old_inbox[:10])}" if old_inbox else ""))
        info.append(f"raw sources not yet compiled into the wiki: {len(pending)}" + (f" → {', '.join(pending[:10])}" if pending else ""))
        info.append(f"wiki pages: {len(wiki_pages)} · unreviewed: {len(unreviewed)} · low confidence: {len(low_conf)}")
        info.append(f"open conflicts: {len(conflicts)}" + (f" → {', '.join(conflicts)}" if conflicts else ""))

    out = [f"# Memex lint {TODAY.isoformat()}", ""]
    out.append(f"## Errors ({len(errors)})")
    out += [f"- {e}" for e in errors] or ["- none"]
    if not quick:
        out += ["", f"## Warnings ({len(warnings)})"]
        out += [f"- {w}" for w in warnings] or ["- none"]
        out += ["", "## Backlog & review"]
        out += [f"- {i}" for i in info]
    text = "\n".join(out) + "\n"
    print(text)
    if report:
        dest = ROOT / "meta" / "lint" / f"{TODAY.isoformat()}.md"
        dest.write_text("---\ntype: lint-report\n---\n" + text, encoding="utf-8")
        print(f"report written: {rel(dest)}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
