"""Forget a folder: remove every reference Claude Code keeps about it.

Run it after deleting a folder (inside the workspace or anywhere else):

    python forget.py <folder>            list what would go, then ask
    python forget.py <folder> --yes      no question
    python forget.py <folder> --force    also if the folder still exists
                                         (keeps the folder, forgets the rest)

Sub-folders count too: forgetting a/ also forgets a/b.

Removed, for that folder and everything under it:
  ~/.claude/projects/<folder>/       chat transcripts, titles, memory
  ~/.claude/file-history/<session>   edit checkpoints of those chats
  ~/.claude/session-env/<session>    their saved environments
  ~/.claude/.caveman-sessions + caveman logs   lines of those sessions
  ~/.claude/history.jsonl            prompt history (up-arrow) lines
  ~/.claude.json                     "projects" entries, githubRepoPaths
  permission rules naming the folder in ~/.claude/settings.json and in every
  .claude/settings.local.json under the workspace root

~/.claude.json is backed up to ~/.claude/backups first. Everything else is
deleted for good (no Recycle Bin).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("LEAN_HOME") or Path.home())   # LEAN_HOME: tests only
CLAUDE = HOME / ".claude"
ROOT = Path(__file__).resolve().parent
BS = chr(92)


def norm(p: str) -> str:
    """Comparable form of a Windows path: lower case, forward slashes, no trailing slash."""
    s = p.replace(BS + BS, "/").replace(BS, "/").strip().rstrip("/").lower()
    s = re.sub(r"^//([a-z])/", r"\1:/", s)   # //c/Users -> c:/users (permission rules)
    s = re.sub(r"^/([a-z])/", r"\1:/", s)    # /c/Users  -> c:/users (git bash)
    return s


def under(path, target: str) -> bool:
    if not isinstance(path, str):
        return False
    p = norm(path)
    return p == target or p.startswith(target + "/")


def mentions(rule: str, target: str) -> bool:
    """
    Does a permission rule name the target folder or something under it?
    """
    t = norm(rule).replace("//" + target[0] + "/", target[0] + ":/").replace("/" + target[0] + "/", target[0] + ":/")
    start = t.find(target)
    while start != -1:
        end = start + len(target)
        if end == len(t) or t[end] in "/*\")' ":
            return True
        start = t.find(target, start + 1)
    return False


def project_cwd(d: Path):
    """
    The folder a ~/.claude/projects entry belongs to, read from its transcripts
    """
    for f in d.glob("*.jsonl"):
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except Exception:
                    continue
                if isinstance(cwd, str):
                    return cwd
    return None


def encoded(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path)).lower()


def jsonl_filter(path: Path, drop) -> tuple[int, callable]:
    """
    Count lines `drop` matches and return an action that removes them (re-reads first)
    """
    def keep(lines):
        out = []
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                out.append(line)
                continue
            if not (isinstance(o, dict) and drop(o)):
                out.append(line)
        return out

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    n = len(lines) - len(keep(lines))

    def go():
        cur = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        path.write_text("".join(keep(cur)), encoding="utf-8")
    return n, go


def plan(target: str, raw: Path):
    acts = []          # (description, action)
    sessions = set()

    for d in sorted((CLAUDE / "projects").glob("*")):
        if not d.is_dir():
            continue
        cwd = project_cwd(d)
        hit = under(cwd, target) if cwd else d.name.lower() == encoded(raw)
        if hit:
            sessions |= {f.stem for f in d.glob("*.jsonl")}
            acts.append((f"chats/memory    {d.name}", lambda d=d: shutil.rmtree(d)))

    for sub in ("file-history", "session-env"):
        for sid in sorted(sessions):
            p = CLAUDE / sub / sid
            if p.exists():
                acts.append((f"{sub:15} {sid}", lambda p=p: shutil.rmtree(p)))
    for sid in sorted(sessions):
        p = CLAUDE / ".caveman-sessions" / f"{sid}.mode"
        if p.exists():
            acts.append((f"caveman         {p.name}", lambda p=p: p.unlink()))

    logs = [(CLAUDE / "history.jsonl", lambda o: under(o.get("project"), target))]
    if sessions:
        for name in (".caveman-history.jsonl", ".caveman-mode-log.jsonl"):
            logs.append((CLAUDE / name, lambda o: o.get("session_id") in sessions))
    for path, drop in logs:
        if path.exists():
            n, go = jsonl_filter(path, drop)
            if n:
                acts.append((f"{path.name:15} {n} lines", go))

    cj = HOME / ".claude.json"
    if cj.exists():
        data = json.loads(cj.read_text(encoding="utf-8"))
        keys = [k for k in data.get("projects", {}) if under(k, target)]
        n_repo = sum(1 for v in data.get("githubRepoPaths", {}).values() for x in v if under(x, target))
        if keys or n_repo:
            def go(cj=cj):
                d = json.loads(cj.read_text(encoding="utf-8"))   # re-read: Claude may have written meanwhile
                for k in [k for k in d.get("projects", {}) if under(k, target)]:
                    d["projects"].pop(k)
                g = d.get("githubRepoPaths", {})
                for r in list(g):
                    g[r] = [x for x in g[r] if not under(x, target)]
                    if not g[r]:
                        g.pop(r)
                bak = CLAUDE / "backups" / f".claude.json.pre-forget.{int(time.time())}"
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cj, bak)
                tmp = cj.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, cj)
            acts.append((f".claude.json    {len(keys)} projects, {n_repo} githubRepoPaths", go))

    files = [CLAUDE / "settings.json"]
    for p in ROOT.rglob("settings.local.json"):
        if p.parent.name == ".claude" and not ({"node_modules", ".git"} & set(p.parts)):
            files.append(p)
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        perms = d.get("permissions", {})
        bad = {k: [r for r in perms.get(k, []) if isinstance(r, str) and mentions(r, target)]
               for k in ("allow", "deny", "ask")}
        n = sum(map(len, bad.values()))
        if n:
            def go(f=f, bad=bad):
                d = json.loads(f.read_text(encoding="utf-8"))
                for k, rules in bad.items():
                    if k in d.get("permissions", {}):
                        d["permissions"][k] = [r for r in d["permissions"][k] if r not in rules]
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            acts.append((f"permissions     {n} rules in {f}", go))
    return acts


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    raw = Path(args[0])
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    raw = Path(os.path.normpath(raw))
    target = norm(str(raw))
    forbidden = {norm(str(x)) for x in (ROOT, HOME, CLAUDE, HOME / "Desktop", Path(raw.anchor))}
    if target in forbidden or len(raw.parts) < 4:
        print(f"Refused: {raw} is too high up (workspace root, home, Desktop, ~/.claude or drive root).")
        return 2
    if raw.exists() and "--force" not in sys.argv:
        print(f"{raw} still exists. Delete it first, or use --force to forget only its references.")
        return 1

    acts = plan(target, raw)
    if not acts:
        print(f"No references to {raw}.")
        return 0
    print(f"References to {raw}:")
    for desc, _ in acts:
        print("  -", desc)
    if "--yes" not in sys.argv:
        answer = input("Delete all? Cannot be undone (except .claude.json, which is backed up). [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    for _, go in acts:
        go()
    print(f"Done: {len(acts)} item(s) removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
