"""
Everything that differs between Windows, macOS and Linux, in one place.

install.py copies this file into <root>/.lean next to bootstrap.py, doctor.py and forget.py, which import it (a script's
own folder is on Python's import path, hook included). Each difference is one function with its branches side by side.

LEAN_OS=windows|mac|linux forces a system and LEAN_HOME / LEAN_APPDATA replace the home folders: tests only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

OS = os.environ.get("LEAN_OS") or ("windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux")
WIN, MAC, LINUX = OS == "windows", OS == "mac", OS == "linux"
CASE_INSENSITIVE = WIN or MAC   # NTFS and default APFS ignore case, Linux file systems do not
HOME = Path(os.environ.get("LEAN_HOME") or Path.home())
CLAUDE = HOME / ".claude"
PY = "python" if WIN else "python3"   # macOS and most Linux distributions ship python3 only
FILE_TOOLS = r"(Read|Edit|Write|Glob|Grep|NotebookEdit)\("


def norm(p: str) -> str:
    """
    Comparable path: forward slashes, no trailing slash, lower case where the file system ignores case.
    Windows: //c/ and /c/ (permission rules, git bash) turned into c:/. macOS / Linux: //abs (permission rules) into /abs
    """
    s = p.strip()
    if WIN:
        s = s.replace("\\\\", "/").replace("\\", "/")
    s = s.rstrip("/")
    if CASE_INSENSITIVE:
        s = s.lower()
    if not WIN:
        return re.sub(r"^//+", "/", s)
    s = re.sub(r"^//([a-z])/", r"\1:/", s)
    return re.sub(r"^/([a-z])/", r"\1:/", s)


def under(path, root) -> bool:
    """
    `path` is `root` or inside it
    """
    if not isinstance(path, str):
        return False
    p, r = norm(path), norm(str(root))
    return p == r or p.startswith(r + "/")


def claude_temp(path: str) -> bool:
    """
    A path in Claude Code's own temp folders (session scratchpads), which may be allowed outside the root
    """
    p = norm(path)
    if WIN:
        return "appdata/local/temp" in p
    return bool(re.match(r"/(private/)?(tmp/claude|var/folders/)", p))


def outside_rule(rule, root, file_tools_only: bool = True) -> bool:
    """
    A permission rule naming an absolute path outside the root.
    file_tools_only: only Read/Edit/... rules count (the perimeter blocks file tools, not programs run from the shell)
    otherwise any rule naming a user folder (bootstrap.py lists them for review)
    """
    if not isinstance(rule, str) or (file_tools_only and not re.match(FILE_TOOLS, rule)):
        return False
    if WIN:
        if file_tools_only:   # Read(//c/Users/..) -> read(c:/users/..)
            p = re.sub(r"//([a-z])/", r"\1:/", norm(rule))
            return bool(re.search(r"(^|[^a-z])[a-z]:/", p)) and norm(str(root)) not in p and "appdata/local/temp" not in p
        low = rule.lower().replace("\\\\", "/").replace("\\", "/")
        needle = str(root).lower().replace("\\", "/")
        low = low.replace("//" + needle[0] + "/", needle[0] + ":/")
        return ":/users/" in low and needle not in low and "/appdata/local/temp/claude" not in low
    # macOS / Linux: Read(//abs/path) and Read(~/path) are absolute; Read(/path) is relative to the settings file
    m = re.match(r"[A-Za-z]+\((//|~/)(.*)\)$", rule, flags=re.S)
    if not m:
        return False
    path = "/" + m.group(2) if m.group(1) == "//" else str(HOME / m.group(2))
    return not under(path, root) and not claude_temp(path)


def caveman_config() -> Path:
    """
    caveman's user config, where its hooks read defaultMode: $XDG_CONFIG_HOME, else %APPDATA% on Windows, else ~/.config
    """
    if os.environ.get("LEAN_APPDATA"):
        return Path(os.environ["LEAN_APPDATA"]) / "caveman" / "config.json"
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "caveman" / "config.json"
    if WIN:
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming") / "caveman" / "config.json"
    return HOME / ".config" / "caveman" / "config.json"


def statusline_script() -> str:
    """
    caveman's status line script for this system (in <plugin>/src/hooks)
    """
    return "caveman-statusline.ps1" if WIN else "caveman-statusline.sh"


def statusline_command(script: Path) -> str:
    if WIN:
        return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script.as_posix()}"'
    return f'bash "{script.as_posix()}"'


def system_temps() -> list[Path]:
    """
    The system temp folders, most specific first (Claude Code's default home for its temp files)
    """
    if WIN:
        cands = [os.environ.get("TEMP"), os.environ.get("TMP"), HOME / "AppData" / "Local" / "Temp"]
    else:
        cands = [os.environ.get("TMPDIR"), "/tmp"]
    out = []
    for c in cands:
        if c and Path(c) not in out:
            out.append(Path(c))
    return out


def claude_tmp_dirs(tmp: Path) -> list[Path]:
    """
    Claude Code's own folders inside a temp folder: claude (Windows) or claude-<uid> (macOS, Linux)
    """
    if not tmp.is_dir():
        return []
    return [p for p in tmp.glob("claude*") if p.is_dir() and (p.name == "claude" or p.name.startswith("claude-"))]


def hide(paths: list[Path]) -> bool:
    """
    Hide files from the file browser. Windows: hidden attribute. macOS / Linux: dot names are already hidden.
    True if something was done
    """
    if not WIN:
        return False
    for p in paths:
        if p.exists():
            subprocess.run(["attrib", "+h", str(p)], capture_output=True)
    return True
