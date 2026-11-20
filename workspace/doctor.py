"""
Check that the lean workspace is really in force.

    python doctor.py          static checks (free)
    python doctor.py --live   also opens two real Claude sessions (Haiku, a few cents): one inside the root, one outside, and
                              checks what their hooks actually did

Exit 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("LEAN_HOME") or Path.home())
APPDATA = Path(os.environ.get("LEAN_APPDATA") or os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
CLAUDE = HOME / ".claude"
PLUGIN = "caveman@caveman-lean"
MARKER = "<!-- lean-workspace:start -->"
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str, fix: str = "") -> bool:
    RESULTS.append((ok, what))
    print(f"[{'ok' if ok else 'FAIL'}] {what}" + ("" if ok or not fix else f"\n       fix: {fix}"))
    return ok


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def static() -> None:
    reinstall = f'python install.py "{ROOT}"'
    cfg = read_json(ROOT / ".lean.json")
    s = read_json(CLAUDE / "settings.json")
    cmd = f'python "{(ROOT / "bootstrap.py").as_posix()}" --hook'
    hooks = [h.get("command") for e in s.get("hooks", {}).get("SessionStart", []) for h in e.get("hooks", [])]
    check(cmd in hooks, "SessionStart hook runs this root's bootstrap.py", reinstall)

    r = subprocess.run([sys.executable, str(ROOT / "bootstrap.py"), "--check"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    check(r.returncode == 0, "every project folder has the guard settings and .plans (bootstrap --check)", f'python "{ROOT / "bootstrap.py"}"')

    tmpdir = s.get("env", {}).get("CLAUDE_CODE_TMPDIR")
    if cfg.get("tmp_in_root"):
        check(isinstance(tmpdir, str) and Path(tmpdir).resolve() == (ROOT / ".tmp").resolve(),
              "CLAUDE_CODE_TMPDIR points to <root>/.tmp (scratchpads stay in the workspace)", f"{reinstall} --tmp-in-root")
    if tmpdir:
        check(Path(tmpdir).is_dir(), f"CLAUDE_CODE_TMPDIR folder exists ({tmpdir})",
              "the workspace was moved or renamed? re-run install.py from its new place")

    root_cfg = ROOT / "CLAUDE.md"
    check(root_cfg.exists(), "CLAUDE.md at the root", "copy templates/CLAUDE.md")
    if root_cfg.exists():
        n = len(root_cfg.read_text(encoding="utf-8").encode())
        check(n <= 2048, f"CLAUDE.md is lean ({n} bytes, budget 2048): it loads in every session", "move project-specific rules into that project's CLAUDE.md")
        if cfg.get("claude_md") != "keep":
            check(MARKER in root_cfg.read_text(encoding="utf-8"), "CLAUDE.md contains the workspace rules block", f"{reinstall} --choose claude_md=append")

    plugin_dir = ROOT / ".plugins" / "caveman"
    if not plugin_dir.exists():
        print("[--] caveman not installed (--no-caveman): plugin checks skipped")
        return
    check((plugin_dir / ".claude-plugin" / "plugin.json").exists(), "trimmed caveman present", reinstall)
    check(s.get("enabledPlugins", {}).get(PLUGIN) is True, f"{PLUGIN} enabled in user settings", reinstall)
    others = [k for k, v in s.get("enabledPlugins", {}).items() if k.startswith("caveman@") and k != PLUGIN and v is True]
    check(not others, "no second caveman plugin enabled (hooks would run twice)", f"{reinstall} --choose other_caveman=disable   (found: {', '.join(others)})")
    check(read_json(ROOT / ".caveman.json").get("defaultMode") == "caveman", "caveman ON in the root", reinstall)
    if cfg.get("caveman_scope", "root") == "root":
        check(read_json(APPDATA / "caveman" / "config.json").get("defaultMode") == "off", "caveman OFF outside the root", reinstall)
    else:
        print("[--] caveman scope = everywhere (your choice): outside-root check skipped")
    if shutil.which("claude") and "LEAN_HOME" not in os.environ:
        r = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=os.name == "nt")
        listed = PLUGIN in r.stdout and str(plugin_dir).lower() in r.stdout.lower()
        check(listed, f"{PLUGIN} installed from {plugin_dir}", f'claude plugin marketplace add "{plugin_dir}" && claude plugin install {PLUGIN}')


def run_session(cwd: Path) -> None:
    subprocess.run(["claude", "-p", "--model", "haiku", "Reply only: ok"], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, shell=os.name == "nt")


def new_modes(since: float) -> list[str]:
    d = CLAUDE / ".caveman-sessions"
    if not d.exists():
        return []
    return [p.read_text(encoding="utf-8").strip() for p in d.glob("*.mode") if p.stat().st_mtime >= since]


def live() -> None:
    if not shutil.which("claude"):
        check(False, "live test needs the claude CLI on PATH")
        return
    inside = ROOT / f"_doctor_{int(time.time())}"
    # Not tempfile's default dir: with CLAUDE_CODE_TMPDIR set, Claude Code hands TMPDIR=<root>/.tmp to shell commands,
    # and tempfile would put the "outside" folder inside the root
    system_tmp = next((Path(p) for p in (os.environ.get("TEMP"), os.environ.get("TMP"), HOME / "AppData" / "Local" / "Temp")
                       if p and ROOT not in Path(p).resolve().parents and Path(p).resolve() != ROOT and Path(p).is_dir()), HOME)
    outside = Path(tempfile.mkdtemp(prefix="lean_doctor_", dir=system_tmp))
    if ROOT in outside.resolve().parents:
        check(False, f"live: could not find a folder outside the root for the outside test (got {outside})")
        shutil.rmtree(outside, ignore_errors=True)
        return
    try:
        inside.mkdir()
        (inside / "probe.txt").write_text("probe\n", encoding="utf-8")
        start = time.time() - 1
        run_session(inside)
        check((inside / ".claude" / "settings.local.json").exists(),
              "live: a NEW folder gets the guard settings at its first session (hook ran)")
        if read_json(ROOT / ".lean.json").get("tmp_in_root"):
            fresh = [p for p in (ROOT / ".tmp").rglob("*") if p.stat().st_mtime >= start] if (ROOT / ".tmp").exists() else []
            check(bool(fresh), "live: the session wrote its temp files under <root>/.tmp, not the system temp")
        scope_root = read_json(ROOT / ".lean.json").get("caveman_scope", "root") == "root"
        if (ROOT / ".plugins" / "caveman").exists():
            check("caveman" in new_modes(start), "live: caveman activates in a session inside the root")
        if (ROOT / ".plugins" / "caveman").exists() and scope_root:
            start = time.time() - 1
            (outside / "probe.txt").write_text("probe\n", encoding="utf-8")
            run_session(outside)
            check("caveman" not in new_modes(start), "live: caveman stays off in a session outside the root")
    finally:
        shutil.rmtree(inside, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
        for folder in (inside, outside):   # leave no trace of the test sessions
            subprocess.run([sys.executable, str(ROOT / "forget.py"), str(folder), "--yes"], capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"Workspace root: {ROOT}")
    static()
    if "--live" in sys.argv:
        live()
    failed = sum(1 for ok, _ in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
