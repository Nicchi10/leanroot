"""
Install the lean workspace into a folder (Windows, Python 3.9+, git, Node, Claude Code)

    python install.py <workspace-root> [--language English] [--no-caveman] [--dry-run]

What it does, in order (it is safe to re-run):
  1. copies bootstrap.py, forget.py, doctor.py into the root and CLAUDE.md only if the root has none (yours is never overwritten)
  2. fetches a trimmed caveman into <root>/.plugins/caveman (fetch_caveman.py)
  3. caveman on inside the root, off elsewhere: <root>/.caveman.json = caveman, %APPDATA%/caveman/config.json = off
  4. ~/.claude/settings.json (backed up first): SessionStart hook running bootstrap.py, the local marketplace + plugin, the caveman status line (only if you have no status line already)
  5. registers and installs the plugin with the `claude` CLI
  6. runs bootstrap.py once

Then: python <root>/doctor.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("LEAN_HOME") or Path.home())          # LEAN_HOME / LEAN_APPDATA: tests only
APPDATA = Path(os.environ.get("LEAN_APPDATA") or os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
TOOLS = ["bootstrap.py", "forget.py", "doctor.py"]
PLUGIN = "caveman@caveman-lean"
MARKETPLACE = "caveman-lean"


def say(msg: str) -> None:
    print(f"- {msg}", flush=True)


def read_json(path: Path) -> dict:
    if path.exists() and path.read_text(encoding="utf-8").strip():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_json(path: Path, data: dict, dry: bool) -> None:
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_root(root: Path) -> None:
    bad = {HOME, HOME / "Desktop", HOME / ".claude", Path(root.anchor)}
    if not root.is_dir():
        raise SystemExit(f"{root} is not a folder")
    if root in bad or len(root.parts) < 3:
        raise SystemExit(f"refused: {root} is too high up for a workspace root")


def copy_tools(root: Path, language: str, dry: bool) -> None:
    for name in TOOLS:
        say(f"copy {name}")
        if not dry:
            shutil.copy2(HERE / "workspace" / name, root / name)
    target = root / "CLAUDE.md"
    if target.exists():
        say("CLAUDE.md exists: left untouched (compare with templates/CLAUDE.md)")
        return
    text = (HERE / "templates" / "CLAUDE.md").read_text(encoding="utf-8")
    text = text.replace("{{ROOT}}", str(root)).replace("{{LANGUAGE}}", language)
    say("write CLAUDE.md from template")
    if not dry:
        target.write_text(text, encoding="utf-8")


def caveman_files(root: Path, dry: bool) -> Path:
    dest = root / ".plugins" / "caveman"
    say(f"fetch trimmed caveman -> {dest}")
    if not dry:
        sys.path.insert(0, str(HERE))
        import fetch_caveman
        fetch_caveman.fetch(dest)
    write_json(root / ".caveman.json", {"defaultMode": "caveman"}, dry)
    say("caveman ON inside the root (.caveman.json)")
    cfg_path = APPDATA / "caveman" / "config.json"
    cfg = read_json(cfg_path)
    if cfg.get("defaultMode") not in (None, "off"):
        say(f"caveman user config had defaultMode={cfg['defaultMode']!r}: set to 'off' (outside the root)")
    cfg["defaultMode"] = "off"
    write_json(cfg_path, cfg, dry)
    say(f"caveman OFF elsewhere ({cfg_path})")
    return dest


def user_settings(root: Path, plugin_dir: Path | None, dry: bool) -> None:
    path = HOME / ".claude" / "settings.json"
    if path.exists() and not dry:
        bak = HOME / ".claude" / "backups" / f"settings.json.pre-lean.{int(time.time())}"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bak)
        say(f"backup {bak}")
    d = read_json(path)

    cmd = f'python "{(root / "bootstrap.py").as_posix()}" --hook'
    ss = d.setdefault("hooks", {}).setdefault("SessionStart", [])
    if not any(h.get("command") == cmd for e in ss for h in e.get("hooks", [])):
        ss.append({"hooks": [{"type": "command", "command": cmd, "timeout": 30, "statusMessage": "workspace bootstrap..."}]})
        say("SessionStart hook added")

    if plugin_dir:
        d.setdefault("extraKnownMarketplaces", {})[MARKETPLACE] = {
            "source": {"source": "directory", "path": str(plugin_dir)}}
        d.setdefault("enabledPlugins", {})[PLUGIN] = True
        say(f"plugin {PLUGIN} enabled (inert outside the root: defaultMode off)")
        script = plugin_dir / "src" / "hooks" / "caveman-statusline.ps1"
        stable = HOME / ".claude" / "hooks" / "caveman-statusline.ps1"
        if "statusLine" in d:
            say("you already have a statusLine: left untouched")
        elif script.exists() or dry:
            if not dry:
                stable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script, stable)
            d["statusLine"] = {"type": "command", "command": f'powershell -NoProfile -ExecutionPolicy Bypass -File "{stable.as_posix()}"'}
            say("caveman status line set")
    write_json(path, d, dry)


def claude_cli(plugin_dir: Path, dry: bool) -> None:
    if dry:
        say("would run: claude plugin marketplace add / plugin install")
        return
    if not shutil.which("claude"):
        say("claude CLI not found: install the plugin later with "
            f'`claude plugin marketplace add "{plugin_dir}"` and `claude plugin install {PLUGIN}`')
        return
    for args in (["plugin", "marketplace", "add", str(plugin_dir)], ["plugin", "install", PLUGIN, "--scope", "user"]):
        r = subprocess.run(["claude", *args], capture_output=True, text=True, shell=os.name == "nt")
        say(f"claude {' '.join(args[:3])}: {'ok' if r.returncode == 0 else 'FAILED ' + (r.stderr or r.stdout).strip()[:200]}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = sys.argv[1:]
    language = "English"
    if "--language" in argv:
        i = argv.index("--language")
        language = argv[i + 1]
        del argv[i:i + 2]
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    dry = "--dry-run" in flags
    check_root(root)
    print(f"Workspace root: {root}{'  (dry run)' if dry else ''}")

    copy_tools(root, language, dry)
    plugin_dir = None if "--no-caveman" in flags else caveman_files(root, dry)
    user_settings(root, plugin_dir, dry)
    if plugin_dir and "--no-cli" not in flags:
        claude_cli(plugin_dir, dry)
    if not dry:
        subprocess.run([sys.executable, str(root / "bootstrap.py")], check=False)
    print(f"\nDone. Check it: python \"{root / 'doctor.py'}\"   (add --live for a real session test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
