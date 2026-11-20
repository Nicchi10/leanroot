"""
Bootstrap the workspace guardrails.

The workspace root is the folder this file lives in. Claude Code does not inherit settings.json from parent folders, so the root, each direct subfolder 
and every git repo one level deeper get their own .claude/settings.local.json (git-ignored), merged with what is already there:

  - file tools may not read or write outside the workspace root (except folders listed as "unguarded" in .lean.json)
  - the root is a working directory everywhere, so projects can read siblings
  - Claude's auto-memory is off: notes live in each project's CLAUDE.md (unless .lean.json says "auto_memory": true)
  - the plugins in PLUGINS are enabled

CLAUDE.md, instead, is inherited and lives once at the root.

Usage:
    python bootstrap.py           apply to every folder
    python bootstrap.py --check   report only
    python bootstrap.py --hook    SessionStart hook: reads the hook JSON on stdin, does nothing outside the root, prints a notice only if a folder changed (valid from
                                  the next session: settings load at startup)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROOT_STR = str(ROOT)
SKIP = {".git", ".claude", ".plugins", "node_modules", "venv", ".venv", "__pycache__"}

MARKETPLACES = {
    "caveman-lean": {"source": {"source": "directory", "path": str(ROOT / ".plugins" / "caveman")}},
}
PLUGINS = ["caveman@caveman-lean"]
# Entries from earlier setups, removed from every folder's settings
STALE_MARKETPLACES: list[str] = []
STALE_PLUGINS: list[str] = []


def load_config() -> dict:
    """
    Choices made by install.py's preflight (<root>/.lean.json):
      auto_memory: true keeps Claude's auto-memory on
      unguarded:   folders (relative to the root) that legitimately read outside it: no perimeter forced there
    """
    try:
        cfg = json.loads((ROOT / ".lean.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {"auto_memory": cfg.get("auto_memory") is True,
            "unguarded": {u.replace("\\", "/").strip("/").lower() for u in cfg.get("unguarded", [])}}


CONFIG = load_config()


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
    flagged = []
    needle = ROOT_STR.lower().replace("\\", "/")
    for rule in perms.get("allow", []):
        low = rule.lower().replace("\\\\", "/").replace("\\", "/")
        low = low.replace("//" + needle[0] + "/", needle[0] + ":/")
        if ":/users/" in low and needle not in low and "/appdata/local/temp/claude" not in low:
            flagged.append(rule)
    return flagged


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
