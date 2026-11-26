"""
Bootstrap the workspace guardrails.

The workspace root is the folder this file lives in. Claude Code does not inherit settings.json from parent folders, so the root, each direct subfolder 
and every git repo one level deeper get their own .claude/settings.local.json (git-ignored), merged with what is already there:

  - file tools may not read or write outside the workspace root (except folders listed as "unguarded" in .lean/config.json)
  - the root is a working directory everywhere, so projects can read siblings
  - Claude's auto-memory is off: notes live in each project's CLAUDE.md (unless .lean/config.json says "auto_memory": true)
  - plan-mode files go to <project>/.plans (git-excluded) instead of ~/.claude/plans
  - the plugins in PLUGINS are enabled
  - .lean/tmp is kept clean (at most once a day): what shell tools left there and orphan session scratchpads,
    untouched for tmp_max_age_days (default 7), are deleted

CLAUDE.md, instead, is inherited and lives once at the root.

Usage:
    python .lean/bootstrap.py           apply to every folder (python3 on macOS)
    python .lean/bootstrap.py --check   report only
    python .lean/bootstrap.py --clean-tmp   clean .lean/tmp now and report
    python .lean/bootstrap.py --hook    SessionStart hook: reads the hook JSON on stdin, does nothing outside the root, prints a notice only if a folder changed (valid from
                                  the next session: settings load at startup)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

LEAN = Path(__file__).resolve().parent   # <root>/.lean holds every file of the framework
ROOT = LEAN.parent
ROOT_STR = str(ROOT)
SKIP = {".git", ".claude", ".lean", ".plugins", "node_modules", "venv", ".venv", "__pycache__"}
WIN = os.name == "nt"

def load_config() -> dict:
    """
    Choices made by install.py's preflight (<root>/.lean/config.json):
      auto_memory: true keeps Claude's auto-memory on
      unguarded:   folders (relative to the root) that legitimately read outside it: no perimeter forced there
      plugin_dir:  the caveman copy in use; another workspace's when several share one (one plugin per machine)
    """
    try:
        cfg = json.loads((LEAN / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {"auto_memory": cfg.get("auto_memory") is True,
            "unguarded": {u.replace("\\", "/").strip("/").lower() for u in cfg.get("unguarded", [])},
            "plugin_dir": cfg.get("plugin_dir") or str(LEAN / "plugins" / "caveman")}


CONFIG = load_config()

MARKETPLACES = {
    "caveman-lean": {"source": {"source": "directory", "path": CONFIG["plugin_dir"]}},
}
PLUGINS = ["caveman@caveman-lean"]
# Entries from earlier setups, removed from every folder's settings
STALE_MARKETPLACES: list[str] = []
STALE_PLUGINS: list[str] = []


def targets() -> list[Path]:
    """
    Root, non-empty direct subfolders, and git repos one level deeper
    """
    out = [ROOT]
    for child in sorted(ROOT.iterdir()):
        if not child.is_dir() or child.name in SKIP or child.name.startswith("."):
            continue
        if not any(c.name != ".claude" for c in child.iterdir()):
            continue  # empty folder: nothing to guard
        out.append(child)
        for grand in sorted(child.iterdir()):
            if grand.is_dir() and grand.name not in SKIP and (grand / ".git").exists():
                out.append(grand)
    return out


def outside_rules(perms: dict) -> list[str]:
    """
    Allow rules naming an absolute user path outside the root (review by hand)
    """
    if not WIN:
        return [r for r in perms.get("allow", []) if isinstance(r, str) and outside_posix(r)]
    flagged = []
    needle = ROOT_STR.lower().replace("\\", "/")
    for rule in perms.get("allow", []):
        low = rule.lower().replace("\\\\", "/").replace("\\", "/")
        low = low.replace("//" + needle[0] + "/", needle[0] + ":/")
        if ":/users/" in low and needle not in low and "/appdata/local/temp/claude" not in low:
            flagged.append(rule)
    return flagged


def outside_posix(rule: str) -> bool:
    """
    macOS: a rule naming //abs/path or ~/path outside the root (/path is relative to the settings file).
    Claude Code temp folders do not count
    """
    m = re.search(r"\((//|~/)([^)]*)\)", rule)
    if not m:
        return False
    home = Path(os.environ.get("LEAN_HOME") or Path.home())
    path = ("/" + m.group(2)) if m.group(1) == "//" else str(home / m.group(2))
    low, root = path.lower().rstrip("/"), ROOT_STR.lower()
    if low == root or low.startswith(root + "/"):
        return False
    return not re.match(r"/(private/)?(tmp/claude|var/folders/)", low)


def claude_tmp_dirs(tmp: Path) -> list[Path]:
    """
    Claude Code's own folders inside a temp dir: claude (Windows) or claude-<uid> (macOS)
    """
    return [p for p in tmp.glob("claude*") if p.is_dir() and (p.name == "claude" or p.name.startswith("claude-"))]


PLANS_DIR = ".plans"


def git_exclude(folder: Path, entry: str, check: bool) -> bool:
    """
    Keep `entry` out of git in a folder that is a repo (local .git/info/exclude, never committed).
    True if it was missing
    """
    if not (folder / ".git").is_dir():
        return False
    exclude = folder / ".git" / "info" / "exclude"
    current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if entry in current.splitlines():
        return False
    if not check:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(current.rstrip("\n") + ("\n" if current.strip() else "") + entry + "\n", encoding="utf-8")
    return True


def apply(folder: Path, check: bool) -> tuple[bool, list[str]]:
    path = folder / ".claude" / "settings.local.json"
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    perms = data.setdefault("permissions", {})
    dirs = perms.setdefault("additionalDirectories", [])

    changed = False
    if not any(Path(d).resolve() == ROOT for d in dirs):
        dirs.append(ROOT_STR)
        changed = True
    rel = folder.relative_to(ROOT).as_posix().lower()
    if rel not in CONFIG["unguarded"]:
        for key in ("blockReadsOutsideWorkingDirectories", "blockWritesOutsideWorkingDirectories"):
            if perms.get(key) is not True:
                perms[key] = True
                changed = True
    if not CONFIG["auto_memory"] and data.get("autoMemoryEnabled") is not False:
        data["autoMemoryEnabled"] = False
        changed = True
    # plan-mode files stay in the project (default is ~/.claude/plans, outside the workspace)
    if data.get("plansDirectory") != PLANS_DIR:
        data["plansDirectory"] = PLANS_DIR
        changed = True
    if git_exclude(folder, PLANS_DIR + "/", check):
        changed = True

    markets = data.setdefault("extraKnownMarketplaces", {})
    enabled = data.setdefault("enabledPlugins", {})
    for name in STALE_MARKETPLACES:
        if markets.pop(name, None) is not None:
            changed = True
    for plugin in STALE_PLUGINS:
        if enabled.pop(plugin, None) is not None:
            changed = True
    for name, spec in MARKETPLACES.items():
        if markets.get(name) != spec:
            markets[name] = spec
            changed = True
    for plugin in PLUGINS:
        if enabled.get(plugin) is not True:
            enabled[plugin] = True
            changed = True

    if changed and not check:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed, outside_rules(perms)


def newest_mtime(path: Path) -> float:
    """
    Latest modification inside `path` (a folder counts as recent if anything in it is), links not followed
    """
    latest = path.lstat().st_mtime
    if path.is_dir() and not path.is_symlink():
        for dirpath, dirnames, filenames in os.walk(path):
            for name in dirnames + filenames:
                try:
                    latest = max(latest, os.lstat(os.path.join(dirpath, name)).st_mtime)
                except OSError:
                    pass
    return latest


def clean_tmp(force: bool = False) -> tuple[int, int]:
    """
    Delete what nobody touched for tmp_max_age_days in <root>/.lean/tmp: files shell tools left there (TEMP/TMPDIR point
    here when CLAUDE_CODE_TMPDIR does) and session scratchpads whose chat no longer exists. Claude Code's own
    retention keeps cleaning the scratchpads of chats that still exist. Never leaves .lean/tmp.
    Returns (items removed, bytes freed)
    """
    tmp = LEAN / "tmp"
    stamp = tmp / ".last-clean"
    if not tmp.is_dir() or (not force and stamp.exists() and time.time() - stamp.stat().st_mtime < 86400):
        return 0, 0
    try:
        days = float(json.loads((LEAN / "config.json").read_text(encoding="utf-8")).get("tmp_max_age_days", 7))
    except Exception:
        days = 7.0
    cutoff = time.time() - days * 86400
    home = Path(os.environ.get("LEAN_HOME") or Path.home())
    known = {f.stem for f in (home / ".claude" / "projects").glob("*/*.jsonl")}

    own = claude_tmp_dirs(tmp)
    candidates = [p for p in tmp.iterdir() if p not in own and p.name != ".last-clean"]
    for project in (q for d in own for q in d.glob("*")):
        if project.is_dir():
            candidates += [s for s in project.iterdir() if s.is_dir() and s.name not in known]
    removed, freed = 0, 0
    for item in candidates:
        try:
            if newest_mtime(item) >= cutoff:
                continue
            size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) if item.is_dir() else item.stat().st_size
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed, freed = removed + 1, freed + size
        except OSError:
            pass   # in use: next time
    stamp.touch()
    return removed, freed


def hook() -> int:
    """
    SessionStart hook: never fails the session, silent unless it changed something
    """
    try:
        cwd = Path(json.load(sys.stdin).get("cwd") or ".").resolve()
    except Exception:
        cwd = Path.cwd().resolve()
    if cwd != ROOT and ROOT not in cwd.parents:
        return 0
    try:
        changed = [f.relative_to(ROOT).as_posix() or "." for f in targets() if apply(f, False)[0]]
        clean_tmp()   # silent: at most once a day
    except Exception as exc:  # report, never block the session
        print(json.dumps({"systemMessage": f"workspace bootstrap failed: {exc}"}))
        return 0
    if changed:
        msg = "workspace bootstrap: updated " + ", ".join(changed) + " (active from the next session)"
        print(json.dumps({"systemMessage": msg}))
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--hook" in sys.argv:
        return hook()
    if "--clean-tmp" in sys.argv:
        n, b = clean_tmp(force=True)
        print(f"{LEAN / 'tmp'}: {n} item(s) removed, {b / 1048576:.1f} MB freed")
        return 0
    check = "--check" in sys.argv
    pending = 0
    for folder in targets():
        changed, flagged = apply(folder, check)
        rel = folder.relative_to(ROOT).as_posix() or "."
        if changed:
            pending += 1
        status = ("PENDING" if check else "updated") if changed else "ok"
        print(f"{status:8} {rel}")
        for rule in flagged:
            print(f"{'':8}   ! allow rule pointing outside the root: {rule}")
    if check and pending:
        print(f"\n{pending} folder(s) to update: run without --check.")
    return 1 if (check and pending) else 0


if __name__ == "__main__":
    sys.exit(main())
