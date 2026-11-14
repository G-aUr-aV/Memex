"""Local embeddings for hybrid search (stdlib only).

`memex search` can fuse its keyword ranking with a ranking by meaning. The vectors come from an embeddings
server on this machine (Ollama's POST /api/embed), never from a remote one: the URL must be a loopback
address, proxies are ignored and redirects are refused, so no page text or query leaves the machine.

  .memex/vectors.json   machine-local, git-excluded: {"model", "pages": {path: {"h": text hash, "v": float32}}}

One vector per wiki page (title, aliases, summary and the start of the body). A page is re-embedded when
that text or the model changes. Every failure raises Unavailable, and the caller falls back to keywords.
"""
import array
import base64
import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import memexlib as ml

STORE = Path(".memex") / "vectors.json"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
BODY_CHARS = 1500      # how much of a page's body is embedded after its title, aliases and summary
BATCH = 16             # pages per request when (re)building
BUILD_TIMEOUT = 120    # seconds per batch for `memex index`; a search uses the short search.timeout instead
# What each model expects in front of a query and a document. Unknown models get no prefix.
PREFIXES = {"embeddinggemma": ("task: search result | query: ", "title: {title} | text: "),
            "nomic-embed-text": ("search_query: ", "search_document: ")}


class Unavailable(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def endpoint(cfg: dict) -> str:
    url = str(cfg.get("url") or "")
    try:
        u = urllib.parse.urlsplit(url)
        host = u.hostname or ""
    except ValueError:
        host, u = "", None
    if not u or u.scheme not in ("http", "https") or host not in LOOPBACK:
        raise Unavailable("search.url must be on this machine (localhost), so no page text leaves it")
    return url.rstrip("/") + "/api/embed"


def prefixes(model: str):
    return next((p for name, p in PREFIXES.items() if str(model).startswith(name)), ("", ""))


def embed(cfg: dict, texts, timeout) -> list:
    """Unit-length vectors for texts, in order."""
    req = urllib.request.Request(endpoint(cfg), data=json.dumps({"model": cfg["model"], "input": list(texts)}).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(req, timeout=float(timeout)) as r:
            vecs = json.load(r).get("embeddings")
    except (OSError, ValueError, urllib.error.URLError) as e:
        reason = getattr(e, "reason", None) or e
        raise Unavailable(f"no answer from the local embeddings server at {cfg['url']} ({reason})")
    if not isinstance(vecs, list) or len(vecs) != len(texts) or not all(vecs):
        raise Unavailable(f"the local embeddings server returned no vectors for model {cfg['model']!r}")
    out = []
    for x in vecs:
        norm = math.sqrt(sum(a * a for a in x)) or 1.0
        out.append(array.array("f", [a / norm for a in x]))
    return out


def page_text(d, model: str) -> str:
    """What is embedded for a page (a doc from memex.docs()): its name, aliases, summary and opening."""
    fm = d["fm"]
    aliases = fm.get("aliases")
    aliases = ", ".join(str(a) for a in aliases) if isinstance(aliases, list) else str(aliases or "")
    parts = [d["title"], aliases, str(fm.get("summary") or fm.get("why") or ""), d["body"][:BODY_CHARS]]
    return prefixes(model)[1].format(title=d["title"]) + "\n".join(p for p in parts if p.strip())


def load(v: Path, model: str) -> dict:
    try:
        data = json.loads((v / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("pages") or {} if data.get("model") == model else {}


def refresh(v: Path, cfg: dict, docs, inline=None):
    """({path: vector}, pages embedded now) for the wiki pages in docs, embedding the ones that are new or changed.
    With inline=N (a search), more than N stale pages, or no index at all, raises Unavailable instead: building
    is `memex index`'s job."""
    model = str(cfg["model"])
    pages = [d for d in docs if d["path"].startswith("wiki/")]
    if inline is not None and not (v / STORE).exists():
        raise Unavailable("no vector index yet: run `memex index --vectors`")
    stored = load(v, model)
    texts = {d["path"]: page_text(d, model) for d in pages}
    hashes = {p: hashlib.sha1(t.encode("utf-8")).hexdigest() for p, t in texts.items()}
    stale = [p for p in texts if (stored.get(p) or {}).get("h") != hashes[p]]
    if inline is not None and len(stale) > inline:
        raise Unavailable(f"the vector index is out of date ({len(stale)} pages): run `memex index --vectors`")
    timeout = cfg["timeout"] if inline is not None else BUILD_TIMEOUT
    for i in range(0, len(stale), BATCH):
        chunk = stale[i:i + BATCH]
        for p, vec in zip(chunk, embed(cfg, [texts[p] for p in chunk], timeout)):
            stored[p] = {"h": hashes[p], "v": base64.b64encode(vec.tobytes()).decode("ascii")}
    kept = {p: stored[p] for p in texts}
    if stale or len(kept) != len(stored):
        (v / STORE).parent.mkdir(parents=True, exist_ok=True)
        ml.write_atomic(v / STORE, json.dumps({"model": model, "pages": kept}) + "\n")
    out = {}
    for p, e in kept.items():
        vec = array.array("f")
        vec.frombytes(base64.b64decode(e["v"]))
        out[p] = vec
    return out, len(stale)


def nearest(cfg: dict, query: str, vecs: dict) -> list:
    """[(cosine, path)] for every stored page, most similar to the query first."""
    q = embed(cfg, [prefixes(cfg["model"])[0] + query], cfg["timeout"])[0]
    sims = [(sum(a * b for a, b in zip(q, vec)), p) for p, vec in vecs.items() if len(vec) == len(q)]
    if not sims:
        raise Unavailable("the vector index doesn't match the model: run `memex index --vectors`")
    return sorted(sims, key=lambda x: (-x[0], x[1]))
