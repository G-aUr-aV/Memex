#!/usr/bin/env python3
"""The vault's optional private remote: attach and detach (owner only), pull, push, status. Stdlib only.

The vault is local-first. With no remote attached nothing here does anything, and the pre-push hook refuses
every push. When the owner attaches ONE private remote (`memex remote set <url>`, or `setup.sh --remote <url>`
on another machine):
  - its URL is recorded in .memex/remote.json: machine-local (git-excluded, so each machine may use its own
    URL form) and owner-only (the guard blocks agents from editing it or running `memex remote set`);
  - the pre-push hook allows pushes only to that URL, only through `memex push` (MEMEX_PUSH=1), and only when
    the outgoing changes pass the secret scan;
  - `memex commit` pushes after every commit, and every vault session pulls when it starts;
  - a repository anyone can read without logging in is refused, and re-checked at most once a day.
A pull that conflicts never leaves the vault half-merged: it's rolled back and reported, and
`memex pull --merge` starts a merge that an agent resolves and `memex commit` finishes.
"""
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memexlib as ml  # noqa: E402
from memexlib import VaultError  # noqa: E402

STATUS = Path(".memex") / "remote.json"
FETCH_TIMEOUT, PUSH_TIMEOUT, CHECK_TIMEOUT = 15, 60, 15
OFFLINE = re.compile(r"could not resolve|failed to connect|timed out|network is unreachable|connection refused|"
                     r"no route to host|ssl|operation not permitted", re.I)
SCP = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$")  # git@github.com:owner/repo.git


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


KEY_LINE = re.compile(r"denied|not found|could not resolve|timed out|rejected|does not appear|refused|"
                      r"authentication|unreachable", re.I)


def short(text: str) -> str:
    """The line of git's error output worth showing: a Memex hook's reason, else the line naming the cause."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and not l.startswith("hint:")]
    for pick in ([l for l in lines if l.startswith("Memex ")], [l for l in lines if KEY_LINE.search(l)],
                 [l for l in lines if l.lower().startswith(("fatal:", "error:"))], lines[-1:]):
        if pick:
            return pick[0][:200]
    return "unknown error"


# ---------- state (.memex/remote.json) ----------

def load(v: Path) -> dict:
    try:
        return json.loads((v / STATUS).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save(v: Path, **changes):
    data = {**load(v), **changes}
    (v / STATUS).parent.mkdir(parents=True, exist_ok=True)
    (v / STATUS).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def settings(v: Path):
    """{"url", "branch", "auto"} for the attached remote, or None for a local-only vault."""
    s = load(v)
    if not s.get("url"):
        return None
    return {"url": str(s["url"]), "branch": str(s.get("branch") or "main"), "auto": s.get("auto", True) is not False}


# ---------- URLs ----------

def is_local(url: str) -> bool:
    return url.startswith(("file://", "/", "~", "./", "../")) or not (SCP.match(url) or "://" in url)


def check_url(url: str) -> str:
    """The URL to record, or VaultError for one that embeds a credential."""
    u = (url or "").strip()
    if not u:
        raise VaultError("give the remote's URL, e.g. git@github.com:you/memex-vault.git")
    m = re.match(r"^([a-z+]+)://([^/@]*)@", u, re.I)
    if m and (m.group(1).lower().startswith("http") or ":" in m.group(2)):
        raise VaultError("that URL carries a username or token. Use an SSH URL (git@host:you/repo.git), or an "
                         "https URL without credentials and let git's credential helper sign in.")
    if is_local(u) and not u.startswith("file://"):
        u = str(Path(u).expanduser().resolve())
    return u


def https_form(url: str):
    """The anonymous https URL of a hosted repository, or None for a local path."""
    if is_local(url):
        return None
    m = re.match(r"^(?:ssh|git|https?)://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", url, re.I) or SCP.match(url)
    if not m:
        return None
    host, path = m.group(1), m.group(2).strip("/")
    return f"https://{host.lower()}/{path}"


def same_repo(a: str, b: str) -> bool:
    """True if two URLs name the same repository (ssh and https forms of one repo count as the same)."""
    def key(u):
        u = (u or "").strip()
        h = https_form(u)
        if h is None:
            return os.path.realpath(os.path.expanduser(u[7:] if u.startswith("file://") else u)).rstrip("/")
        return re.sub(r"\.git$", "", h.rstrip("/")).lower()
    return bool(a and b) and key(a) == key(b)


def visibility(url: str) -> str:
    """'public' if anyone can read the repository without credentials, 'private' if not, 'local' for a path on
    this machine, 'unknown' if it couldn't be checked (offline, or a host without https)."""
    h = https_form(url)
    if h is None:
        return "local"
    env = ml.git_env({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true", "SSH_ASKPASS": "true"})
    r = ml.git(tempfile.gettempdir(), "-c", "credential.helper=", "-c", "core.askPass=true",
               "ls-remote", "--heads", h, env=env, timeout=CHECK_TIMEOUT)
    if r.returncode == 0:
        return "public"
    return "unknown" if OFFLINE.search(r.stderr) or r.returncode == 124 else "private"


# ---------- git plumbing ----------

def net_env(v: Path, push=False) -> dict:
    ident = ml.identity(v)
    extra = {"GIT_TERMINAL_PROMPT": "0",
             "GIT_AUTHOR_NAME": ident["name"], "GIT_AUTHOR_EMAIL": ident["email"],
             "GIT_COMMITTER_NAME": ident["name"], "GIT_COMMITTER_EMAIL": ident["email"]}
    if not os.environ.get("GIT_SSH_COMMAND") and not ml.git(v, "config", "core.sshCommand").stdout.strip():
        extra["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=10"
    if push:
        extra["MEMEX_PUSH"] = "1"
    return ml.git_env(extra)


def in_progress(v: Path):
    """'merge' or 'rebase' if one is unfinished in the vault, else None."""
    g = v / ".git"
    if (g / "MERGE_HEAD").exists():
        return "merge"
    if (g / "rebase-merge").exists() or (g / "rebase-apply").exists():
        return "rebase"
    return None


def count(v: Path, rng: str) -> int:
    r = ml.git(v, "rev-list", "--count", rng)
    return int(r.stdout.strip() or 0) if r.returncode == 0 else 0


def conflicted(v: Path):
    return ml.git(v, "diff", "--name-only", "--diff-filter=U").stdout.split("\n")[:-1] or []


def check_remotes(v: Path, cfg=None):
    """Raise unless the vault's git remotes are exactly what the owner attached (or none, when local-only)."""
    cfg = cfg if cfg is not None else settings(v)
    names = ml.git(v, "remote").stdout.split()
    if not cfg:
        if names:
            raise VaultError(f"the vault has git remote(s) {', '.join(names)} that weren't attached with "
                             "`memex remote set`. Remove them (git remote remove <name>), or ask the owner to attach "
                             "a private remote with `memex remote set <url>`.")
        return
    extra = [n for n in names if n != "origin"]
    if extra:
        raise VaultError(f"unexpected git remote(s) {', '.join(extra)}; the vault syncs only with origin "
                         f"({cfg['url']}). Remove them with git remote remove <name>.")
    if "origin" not in names:  # e.g. a vault copied from a backup: restore the recorded remote
        ml.git(v, "remote", "add", "origin", cfg["url"])
    for url in (ml.git(v, "remote", "get-url", "origin").stdout.strip(),
                ml.git(v, "remote", "get-url", "--push", "origin").stdout.strip()):
        if not same_repo(url, cfg["url"]):
            raise VaultError(f"origin points at {url}, but the remote the owner attached is {cfg['url']}. "
                             f"Run: git -C \"{v}\" remote set-url origin \"{cfg['url']}\"")


# ---------- pull / push ----------

def pull(v: Path, merge=False) -> str:
    """Bring in the remote's commits. Returns a one-line summary (starting with CONFLICT on a conflict)."""
    import vault as vaultmod
    cfg = settings(v)
    if not cfg:
        return "no remote: the vault is local-only"
    check_remotes(v, cfg)
    with vaultmod.locked(v):
        state = in_progress(v)
        if state:
            files = conflicted(v)
            return (f"CONFLICT: a {state} is in progress" + (f" in {', '.join(files[:5])}" if files else "") +
                    ". Resolve the files (keep both sides' facts), then run memex commit \"merge: sync\".")
        branch, upstream = cfg["branch"], f"origin/{cfg['branch']}"
        f = ml.git(v, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
                   env=net_env(v), timeout=FETCH_TIMEOUT)
        if f.returncode:
            if "couldn't find remote ref" in f.stderr:
                return f"origin has no {branch} branch yet; the next push creates it"
            save(v, last_error=short(f.stderr), last_error_at=now())
            return f"offline: couldn't reach origin ({short(f.stderr)}); commits stay local and push later"
        behind, ahead = count(v, f"HEAD..{upstream}"), count(v, f"{upstream}..HEAD")
        result = "up to date"
        if behind:
            env = net_env(v)
            if not ahead:
                r = ml.git(v, "merge", "--ff-only", "--autostash", "--quiet", upstream, env=env)
            elif merge:
                r = ml.git(v, "merge", "--no-edit", "--autostash", "--quiet", upstream, env=env)
            else:
                r = ml.git(v, "rebase", "--autostash", "--quiet", upstream, env=env)
            if r.returncode:
                files = conflicted(v)
                if merge and in_progress(v) == "merge":
                    save(v, conflict=files, last_error="merge in progress", last_error_at=now())
                    return (f"CONFLICT: merging {upstream} left conflict markers in {', '.join(files[:8]) or '(see git status)'}. "
                            "Edit each file to keep both sides' facts, then run memex commit \"merge: sync\".")
                if in_progress(v) == "rebase":
                    ml.git(v, "rebase", "--abort")
                save(v, conflict=files, last_error=short(r.stderr or r.stdout), last_error_at=now())
                if files:
                    return (f"CONFLICT: {ahead} local and {behind} remote commit(s) both changed "
                            f"{', '.join(files[:5])}. Nothing was changed here. Run memex pull --merge, resolve the "
                            "files (keep both sides' facts), then memex commit \"merge: sync\".")
                return f"couldn't apply the remote's commits ({short(r.stderr or r.stdout)}); nothing was changed here"
            result = f"pulled {behind} commit(s) from {upstream}"
            vaultmod.after_pull(v)
        save(v, last_pull=now(), conflict=[], last_error="")
        if ahead and not merge:
            result += f"; {ahead} local commit(s) to push"
        return result


def push(v: Path, retry=True):
    """Push the vault's commits to the attached remote. Returns a one-line summary, or None when there was
    nothing to push (or no remote). Never raises for network trouble: commits simply stay local."""
    cfg = settings(v)
    if not cfg:
        return None
    check_remotes(v, cfg)
    if in_progress(v):
        return "push skipped: finish the merge first (resolve the files, then memex commit)"
    st, branch = load(v), cfg["branch"]
    checked = st.get("visibility_checked", "")
    if not checked or checked[:10] < (datetime.date.today() - datetime.timedelta(days=1)).isoformat():
        vis = visibility(cfg["url"])
        if vis == "public":
            save(v, visibility=vis, visibility_checked=now(), last_error="the remote is public")
            raise VaultError(f"{cfg['url']} can now be read by anyone without logging in, so nothing was pushed. "
                             "Make the repository private again; commits stay local until then.")
        if vis != "unknown":
            save(v, visibility=vis, visibility_checked=now())
    if ml.git(v, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}").returncode == 0 and \
            count(v, f"origin/{branch}..HEAD") == 0:
        return None
    p = ml.git(v, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", env=net_env(v, push=True),
               timeout=PUSH_TIMEOUT)
    if p.returncode == 0:  # git also moves origin/<branch>, so the next check knows what's pushed
        ml.git(v, "branch", "--quiet", f"--set-upstream-to=origin/{branch}")
        save(v, last_push=now(), last_error="")
        return f"pushed to origin/{branch}"
    err = p.stderr or p.stdout
    if retry and re.search(r"rejected|fetch first|non-fast-forward", err):
        msg = pull(v)
        if msg.startswith(("CONFLICT", "offline", "couldn't")):
            return msg
        return push(v, retry=False)
    save(v, last_error=short(err), last_error_at=now())
    return f"push failed ({short(err)}); commits stay local and push on the next commit"


def session_line(v: Path):
    """The Sync line for a vault session's context (pulls first), or None for a local-only vault."""
    if not settings(v):
        return None
    try:
        msg = pull(v)
    except VaultError as e:
        return f"Sync WARNING: {e}"
    cfg = settings(v)
    ahead = count(v, f"origin/{cfg['branch']}..HEAD") if ml.git(
        v, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{cfg['branch']}").returncode == 0 else 0
    if ahead and "to push" not in msg and not msg.startswith("CONFLICT"):
        msg += f"; {ahead} local commit(s) not pushed yet (the next memex commit pushes them)"
    return f"Sync: {msg}"


def status(v: Path) -> str:
    cfg = settings(v)
    if not cfg:
        names = ml.git(v, "remote").stdout.split()
        return "No remote: the vault is local-only (pushes are refused)." + (
            f" WARNING: unattached git remote(s): {', '.join(names)}." if names else "")
    st = load(v)
    lines = [f"Remote: origin → {cfg['url']} (branch {cfg['branch']}, auto-sync {'on' if cfg['auto'] else 'off'})",
             f"Visibility: {st.get('visibility', 'not checked')}" +
             (f" (checked {st['visibility_checked']})" if st.get("visibility_checked") else "")]
    if ml.git(v, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{cfg['branch']}").returncode == 0:
        lines.append(f"Ahead {count(v, 'origin/' + cfg['branch'] + '..HEAD')}, behind "
                     f"{count(v, 'HEAD..origin/' + cfg['branch'])} (as of the last fetch)")
    lines.append(f"Last pull: {st.get('last_pull', 'never')} · last push: {st.get('last_push', 'never')}")
    if st.get("conflict"):
        lines.append(f"CONFLICT in: {', '.join(st['conflict'])} (memex pull --merge, resolve, memex commit)")
    if st.get("last_error"):
        lines.append(f"Last error ({st.get('last_error_at', '?')}): {st['last_error']}")
    return "\n".join(lines)


# ---------- attach / detach (owner only) ----------

def pristine(v: Path) -> bool:
    """A vault nobody has written to yet: only the setup commit, and nothing uncommitted."""
    return count(v, "HEAD") <= 1 and not ml.git(v, "status", "--porcelain").stdout.strip()


def attach(v: Path, url: str, branch="main", yes=False, unverified=False) -> str:
    """Attach the private remote at url (owner only). On a new, empty vault whose remote already holds a vault,
    adopt the remote's history: that's how a second machine joins."""
    import vault as vaultmod
    v = Path(v).resolve()
    url = check_url(url)
    old = settings(v)
    names = [n for n in ml.git(v, "remote").stdout.split() if n != "origin"]
    if names:
        raise VaultError(f"the vault has other git remote(s): {', '.join(names)}. Remove them first.")
    vis = visibility(url)
    if vis == "public":
        raise VaultError(f"{url} can be read by anyone without logging in. Make the repository private, then run "
                         "this again. (A Memex vault must never be public.)")
    if vis == "unknown" and not unverified:
        raise VaultError(f"couldn't check that {url} is private (offline, or the host has no https). Run this again "
                         "when online, or add --unverified if it's your own server.")
    if not yes:
        if not sys.stdin.isatty():
            raise VaultError("attaching a remote sends the whole vault there: confirm with --yes")
        ans = input(f"  Sync this vault ({v}) with {url} [{vis}]? Every commit will be pushed there. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            raise VaultError("cancelled")
    if in_progress(v):
        raise VaultError("finish the merge or rebase in progress first")
    had_origin = ml.git(v, "remote", "get-url", "origin")
    if had_origin.returncode == 0:
        ml.git(v, "remote", "set-url", "origin", url)
    else:
        ml.git(v, "remote", "add", "origin", url)

    def undo():
        if had_origin.returncode == 0:
            ml.git(v, "remote", "set-url", "origin", had_origin.stdout.strip())
        else:
            ml.git(v, "remote", "remove", "origin")

    f = ml.git(v, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
               env=net_env(v), timeout=FETCH_TIMEOUT * 4)
    if f.returncode and "couldn't find remote ref" not in f.stderr:
        undo()
        raise VaultError(f"couldn't fetch {url}: {short(f.stderr)}. Check that git on this machine can reach it "
                         "without a prompt (for GitHub: ssh -T git@github.com).")
    upstream, adopted = f"origin/{branch}", False
    if f.returncode == 0:
        if ml.git(v, "cat-file", "-e", f"{upstream}:{ml.MARKER.as_posix()}").returncode:
            undo()
            raise VaultError(f"{url} already has commits but isn't a Memex vault; use an empty private repository")
        if ml.git(v, "merge-base", "HEAD", upstream).returncode:
            if not pristine(v):
                undo()
                raise VaultError(f"{url} holds a different vault, and this one already has its own knowledge. Use an "
                                 "empty repository, or set this machine up from scratch with "
                                 "`bash setup.sh --vault <new folder> --remote <url>` to join that vault.")
            with vaultmod.locked(v):  # a pristine vault holds nothing to lose, so no backup is needed
                r = ml.git(v, "reset", "--hard", "--quiet", upstream)
            if r.returncode:
                undo()
                raise VaultError(f"couldn't adopt {url}: {short(r.stderr)}")
            adopted = True
    save(v, url=url, branch=branch, auto=True if old is None else old["auto"], attached=now(),
         visibility=vis, visibility_checked=now(), conflict=[], last_error="")
    if adopted:
        ml._vault = None
        vaultmod.harden_git(v)
        vaultmod.sync(v)
        msg = f"joined the vault at {url} ({count(v, 'HEAD')} commits)"
        ml.git(v, "branch", "--quiet", f"--set-upstream-to={upstream}")
        save(v, last_pull=now())
        return msg
    if f.returncode == 0:
        pulled = pull(v)
        if pulled.startswith("CONFLICT"):
            return f"attached {url}, but {pulled}"
    pushed = push(v) or "nothing to push"
    return f"attached {url} [{vis}]: {pushed}"


def detach(v: Path) -> str:
    cfg = settings(v)
    if ml.git(v, "remote", "get-url", "origin").returncode == 0:
        ml.git(v, "remote", "remove", "origin")
    (v / STATUS).unlink(missing_ok=True)
    return ("detached " + cfg["url"] if cfg else "no remote was attached") + \
        ": the vault is local-only again (pushes refused). The remote repository itself is untouched."
