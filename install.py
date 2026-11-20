"""
Install the lean workspace into a folder (Windows, Python 3.9+, git, Node, Claude Code)

    python install.py <workspace-root> [--language English] [--no-caveman] [--tmp-in-root] [--dry-run] [--yes] [--choose KEY=VALUE ...]

    --tmp-in-root   set CLAUDE_CODE_TMPDIR to <root>/.lean/tmp: Claude Code's temp files, session scratchpad included, stay in the
                    workspace. It is a user setting, so sessions outside the workspace put their temp files there too

First a preflight looks for what an existing setup would clash with, and nothing is changed until every clash has a decision:

    other_caveman    another caveman plugin is enabled          disable | abort
    caveman_scope    caveman is already on outside the root     everywhere | root
    memory           auto-memories exist for projects here      migrate | keep | drop
    claude_md        the root already has its own CLAUDE.md     append | keep
    outside_readers  projects already read outside the root     unguard | guard

Decisions come from --choose KEY=VALUE, from the prompt when run in a terminal, or --yes takes every recommended one.
They are saved in <root>/.lean/config.json, which bootstrap.py and doctor.py read.

Everything the framework adds lives in <root>/.lean (hidden on Windows), except CLAUDE.md and .caveman.json, which must sit
in the root to be found. A flat layout from an older install (tools, .lean.json, .plugins, .tmp in the root) is migrated.

Then, in order (it is safe to re-run):
  1. copies bootstrap.py, forget.py, doctor.py into <root>/.lean; writes or extends CLAUDE.md as decided
  2. migrates auto-memories into each project's CLAUDE.local.md, if decided
  3. fetches a trimmed caveman into <root>/.lean/plugins/caveman (fetch_caveman.py)
  4. ~/.claude/settings.json (backed up first): SessionStart hook running bootstrap.py, the local marketplace + plugin,
     the caveman status line (only if you have no status line already), other caveman plugins disabled if decided
  5. registers and installs the plugin with the `claude` CLI
  6. runs bootstrap.py once

Then: python <root>/.lean/doctor.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("LEAN_HOME") or Path.home())          # LEAN_HOME / LEAN_APPDATA: tests only
APPDATA = Path(os.environ.get("LEAN_APPDATA") or os.environ.get("APPDATA") or HOME / "AppData" / "Roaming")
CLAUDE = HOME / ".claude"
TOOLS = ["bootstrap.py", "forget.py", "doctor.py"]
PLUGIN = "caveman@caveman-lean"
MARKETPLACE = "caveman-lean"
MARK_START = "<!-- lean-workspace:start -->"
MARK_END = "<!-- lean-workspace:end -->"
SKIP = {".git", ".claude", ".lean", ".plugins", "node_modules", "venv", ".venv", "__pycache__"}
LEAN = ".lean"                     # <root>/.lean holds every file of the framework
LEGACY_TOOL_MARKS = ("Bootstrap the workspace guardrails", "Bootstrap NG10 agent guardrails",
                     "Forget a folder", "Check that the lean workspace")


def lean(root: Path) -> Path:
    return root / LEAN


def config_path(root: Path) -> Path:
    return lean(root) / "config.json"


def saved_config(root: Path) -> dict:
    """
    Decisions of a previous install: <root>/.lean/config.json, or <root>/.lean.json from the flat layout
    """
    return read_json(config_path(root)) or read_json(root / ".lean.json")


def hook_command(root: Path) -> str:
    return f'python "{(lean(root) / "bootstrap.py").as_posix()}" --hook'


def legacy_hook_command(root: Path) -> str:
    return f'python "{(root / "bootstrap.py").as_posix()}" --hook'


def say(msg: str) -> None:
    print(f"- {msg}", flush=True)


def read_json(path: Path) -> dict:
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def write_json(path: Path, data: dict, dry: bool) -> None:
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def norm(p: str) -> str:
    """
    Comparable Windows path: lower case, forward slashes, no trailing slash, //c/ and /c/ turned into c:/
    """
    s = p.replace("\\\\", "/").replace("\\", "/").strip().rstrip("/").lower()
    s = re.sub(r"^//([a-z])/", r"\1:/", s)
    return re.sub(r"^/([a-z])/", r"\1:/", s)


def under(path, root: Path) -> bool:
    if not isinstance(path, str):
        return False
    p, r = norm(path), norm(str(root))
    return p == r or p.startswith(r + "/")


def check_root(root: Path) -> None:
    bad = {HOME, HOME / "Desktop", HOME / ".claude", Path(root.anchor)}
    if not root.is_dir():
        raise SystemExit(f"{root} is not a folder")
    if root in bad or len(root.parts) < 3:
        raise SystemExit(f"refused: {root} is too high up for a workspace root")


def project_folders(root: Path) -> list[Path]:
    """
    Same folders bootstrap.py guards: root, non-empty direct subfolders, git repos one level deeper
    """
    out = [root]
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in SKIP or child.name.startswith("."):
            continue
        out.append(child)
        for grand in sorted(child.iterdir()):
            if grand.is_dir() and grand.name not in SKIP and (grand / ".git").exists():
                out.append(grand)
    return out


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


############################################################ PREFLIGHT ############################################################

def find_conflicts(root: Path, with_caveman: bool, tmp_in_root: bool) -> tuple[list[dict], list[str], dict]:
    """
    Returns (conflicts needing a decision, notes needing none, facts used later)
    """
    conflicts, notes, facts = [], [], {}
    settings = read_json(CLAUDE / "settings.json")
    saved = saved_config(root)   # decisions of a previous install are not asked again

    if tmp_in_root:
        current = settings.get("env", {}).get("CLAUDE_CODE_TMPDIR")
        if current and norm(current) not in (norm(str(lean(root) / "tmp")), norm(str(root / ".tmp"))):
            other_ws = Path(current).name == "tmp" and Path(current).parent.name == LEAN
            conflicts.append({
                "key": "tmpdir", "recommended": "keep" if other_ws else "replace",
                "title": f"CLAUDE_CODE_TMPDIR is already set to {current}" + (" (another lean workspace)" if other_ws else ""),
                "why": ("it is one user setting, so only one workspace per machine can host Claude Code's temp files; "
                        "replacing it moves them out of the other workspace") if other_ws else
                       "--tmp-in-root moves Claude Code's temp files (session scratchpad included) into <root>/.lean/tmp",
                "options": {"replace": f"set it to {lean(root) / 'tmp'}",
                            "keep": "keep your value: temp files stay outside the root"}})
        env_tmp = os.environ.get("CLAUDE_CODE_TMPDIR")
        if env_tmp and norm(env_tmp) not in (norm(str(lean(root) / "tmp")), norm(str(root / ".tmp"))):
            notes.append("CLAUDE_CODE_TMPDIR is also set in your environment: remove it there if temp files still land outside the root")

    if with_caveman:
        others = [k for k, v in settings.get("enabledPlugins", {}).items() if k.startswith("caveman@") and k != PLUGIN and v is True]
        facts["other_caveman"] = others
        if others:
            conflicts.append({
                "key": "other_caveman", "recommended": "disable",
                "title": f"another caveman plugin is enabled: {', '.join(others)}",
                "why": "two caveman plugins run every hook twice and inject the rules twice",
                "options": {"disable": "disable it in ~/.claude/settings.json (it stays installed; re-enable any time)", 
                            "abort": "stop here and change nothing"}})

        mode = read_json(APPDATA / "caveman" / "config.json").get("defaultMode")
        if mode not in (None, "off") and "caveman_scope" not in saved:
            conflicts.append({
                "key": "caveman_scope", "recommended": "everywhere",
                "title": f"caveman is already on outside the root (defaultMode={mode!r})",
                "why": "the default setup turns caveman off outside the workspace",
                "options": {"everywhere": "keep caveman on everywhere, as today", 
                            "root": "caveman only inside the root (sets your caveman defaultMode to 'off')"}})

        if (root / ".caveman.json").exists() and read_json(root / ".caveman.json").get("defaultMode") != "caveman":
            notes.append(".caveman.json in the root will be set to defaultMode 'caveman' (old file backed up)")
        plugin_dir = lean(root) / "plugins" / "caveman"
        mods = plugin_dir / "MODIFICATIONS.md"
        if plugin_dir.exists() and not (mods.exists() and MARKETPLACE in mods.read_text(encoding="utf-8", errors="replace")):
            notes.append(f"{plugin_dir} is not ours: it will be moved to {plugin_dir.name}.bak-<time>")
        if "statusLine" in settings and "caveman-statusline" not in json.dumps(settings["statusLine"]):
            notes.append("you already have a status line: kept, so no [CAVEMAN] badge")

    memories = []
    done = set(saved.get("migrated_memory", []))
    for d in sorted((CLAUDE / "projects").glob("*")):
        files = sorted(p for p in (d / "memory").glob("*.md") if p.name != "MEMORY.md" and str(p) not in done) if (d / "memory").is_dir() else []
        cwd = project_cwd(d) if files else None
        if files and cwd and under(cwd, root):
            memories.append((cwd, files))
    facts["memories"] = memories
    if memories:
        listing = "; ".join(f"{Path(c).name or c} ({len(f)})" for c, f in memories)
        conflicts.append({
            "key": "memory", "recommended": "migrate",
            "title": f"auto-memories exist for projects in the root: {listing}",
            "why": "the setup turns auto-memory off, so they would silently stop loading",
            "options": {"migrate": "copy them into each project's CLAUDE.local.md (git-excluded), then auto-memory off",
                        "keep": "leave auto-memory on in this workspace",
                        "drop": "auto-memory off; the memory files stay on disk, unused"}})

    root_md = root / "CLAUDE.md"
    if root_md.exists() and MARK_START not in root_md.read_text(encoding="utf-8", errors="replace") and saved.get("claude_md") != "keep":
        conflicts.append({
            "key": "claude_md", "recommended": "append",
            "title": "the root already has its own CLAUDE.md",
            "why": "the workspace rules (perimeter, git, caveman style) would be missing",
            "options": {"append": "append the rules block at the end of your file (marked, so re-runs replace only it)",
                        "keep": "leave your CLAUDE.md as it is"}})

    readers = []
    for folder in project_folders(root):
        for name in ("settings.local.json", "settings.json"):
            perms = read_json(folder / ".claude" / name).get("permissions", {})
            dirs = [d for d in perms.get("additionalDirectories", []) if isinstance(d, str) and not under(d, root)]
            # only file-tool rules matter: the perimeter blocks file tools, not programs run from the shell
            def rule_path(r: str) -> str:   # Read(//c/Users/..) -> read(c:/users/..)
                return re.sub(r"//([a-z])/", r"\1:/", norm(r))
            rules = [r for r in perms.get("allow", []) if isinstance(r, str)
                     and re.match(r"(Read|Edit|Write|Glob|Grep|NotebookEdit)\(", r)
                     and re.search(r"(^|[^a-z])[a-z]:/", rule_path(r)) and norm(str(root)) not in rule_path(r)
                     and "appdata/local/temp" not in rule_path(r)]
            rel = folder.relative_to(root).as_posix() or "."
            if (dirs or rules) and rel not in saved.get("readers_decided", []):
                readers.append(rel)
                break
    facts["readers"] = readers
    if readers:
        conflicts.append({
            "key": "outside_readers", "recommended": "unguard",
            "title": f"projects already read outside the root: {', '.join(readers)}",
            "why": "the perimeter would block those reads and break their workflow",
            "options": {"unguard": "no perimeter in those folders (listed in .lean/config.json, editable)",
                        "guard": "apply the perimeter anyway"}})

    for e in settings.get("hooks", {}).get("SessionStart", []):
        for h in e.get("hooks", []):
            c = h.get("command", "")
            if "bootstrap.py" in c and "--hook" in c and norm(str(root)) not in norm(c):
                notes.append(f"another workspace is installed ({c}): fine, each acts only inside its own root")
    return conflicts, notes, facts


def decide(conflicts: list[dict], choices: dict, yes: bool, dry: bool) -> dict:
    decisions = {}
    pending = []
    for c in conflicts:
        key = c["key"]
        if key in choices:
            if choices[key] not in c["options"]:
                raise SystemExit(f"--choose {key}={choices[key]}: expected one of {', '.join(c['options'])}")
            decisions[key] = choices[key]
        elif yes or dry:
            decisions[key] = c["recommended"]
        else:
            pending.append(c)
    keys = " ".join(f"--choose {c['key']}={c['recommended']}" for c in pending)
    undecided = f"\nUndecided: {', '.join(c['key'] for c in pending)}. Nothing changed. Re-run with --yes (recommended choices) or: {keys}"
    if pending and not sys.stdin.isatty():
        raise SystemExit(undecided)
    for c in pending:
        names = list(c["options"])
        print(f"\n{c['title']}\n  ({c['why']})")
        for i, (name, text) in enumerate(c["options"].items(), 1):
            print(f"  {i}. {name}{' (recommended)' if name == c['recommended'] else ''}: {text}")
        while True:
            try:
                ans = input(f"  choice [1-{len(names)}, enter = {c['recommended']}]: ").strip()
            except EOFError:   # no real terminal
                raise SystemExit(undecided)
            if not ans:
                decisions[c["key"]] = c["recommended"]
                break
            if ans.isdigit() and 1 <= int(ans) <= len(names):
                decisions[c["key"]] = names[int(ans) - 1]
                break
            if ans in names:
                decisions[c["key"]] = ans
                break
    return decisions


def preflight(root: Path, with_caveman: bool, tmp_in_root: bool, choices: dict, yes: bool, dry: bool) -> tuple[dict, dict]:
    conflicts, notes, facts = find_conflicts(root, with_caveman, tmp_in_root)
    print("\nPreflight:")
    if not conflicts and not notes:
        print("  nothing clashes with the existing setup")
    for n in notes:
        print(f"  note: {n}")
    for c in conflicts:
        print(f"  clash [{c['key']}]: {c['title']}")
    decisions = decide(conflicts, choices, yes, dry)
    for k, v in decisions.items():
        print(f"  decision {k} = {v}")
    if decisions.get("other_caveman") == "abort":
        raise SystemExit("aborted: nothing changed")
    print()
    return decisions, facts


############################################################ STEPS ############################################################

def rules_block(root: Path, language: str) -> str:
    text = (HERE / "templates" / "CLAUDE.md").read_text(encoding="utf-8")
    text = text.replace("{{ROOT}}", str(root)).replace("{{LANGUAGE}}", language).strip()
    return f"{MARK_START}\n{text}\n{MARK_END}\n"


def copy_tools(root: Path, language: str, decisions: dict, dry: bool) -> None:
    for name in TOOLS:
        say(f"copy {name} -> {LEAN}/{name}")
        if not dry:
            lean(root).mkdir(exist_ok=True)
            shutil.copy2(HERE / "workspace" / name, lean(root) / name)
    target = root / "CLAUDE.md"
    block = rules_block(root, language)
    if not target.exists():
        say("write CLAUDE.md from template")
        if not dry:
            target.write_text(block, encoding="utf-8")
        return
    text = target.read_text(encoding="utf-8")
    if MARK_START in text and MARK_END in text:
        new = re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", lambda m: block, text, flags=re.S)
        say("CLAUDE.md: workspace rules block refreshed, the rest untouched")
    elif decisions.get("claude_md") == "append":
        new = text.rstrip() + "\n\n" + block
        say("CLAUDE.md: workspace rules block appended")
    else:
        say("CLAUDE.md: left untouched (your choice)")
        return
    if not dry:
        target.write_text(new, encoding="utf-8")


def git_exclude(folder: Path, root: Path, name: str, dry: bool) -> None:
    """
    Add `name` to .git/info/exclude of the repo holding `folder` (searched up to the root)
    """
    for d in [folder, *folder.parents]:
        if (d / ".git").is_dir():
            exclude = d / ".git" / "info" / "exclude"
            rel = (folder / name).relative_to(d).as_posix()
            current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if rel not in current.splitlines() and not dry:
                exclude.parent.mkdir(parents=True, exist_ok=True)
                exclude.write_text(current.rstrip("\n") + ("\n" if current.strip() else "") + rel + "\n", encoding="utf-8")
            return
        if d == root:
            return


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()


def migrate_memories(root: Path, memories: list, dry: bool) -> None:
    for cwd, files in memories:
        folder = Path(cwd)
        if not folder.is_dir():
            say(f"memory of {cwd}: folder gone, skipped (python forget.py removes it)")
            continue
        target = folder / "CLAUDE.local.md"
        parts = [f"### {p.stem}\n\n{strip_frontmatter(p.read_text(encoding='utf-8', errors='replace'))}" for p in files]
        section = f"\n## Migrated auto-memory ({time.strftime('%Y-%m-%d')})\n\n" + "\n\n".join(parts) + "\n"
        say(f"memory of {folder.name or cwd}: {len(files)} file(s) -> {target} (originals left in ~/.claude/projects)")
        if not dry:
            head = target.read_text(encoding="utf-8") if target.exists() else f"# {folder.name} - local notes (private, git-excluded)\n"
            target.write_text(head.rstrip("\n") + "\n" + section, encoding="utf-8")
        git_exclude(folder, root, "CLAUDE.local.md", dry)


def backup(path: Path, dry: bool) -> None:
    if path.exists() and not dry:
        bak = path.with_name(f"{path.name}.bak-{int(time.time())}")
        path.rename(bak)
        say(f"backup {bak}")


def is_our_caveman(path: Path) -> bool:
    mods = path / "MODIFICATIONS.md"
    return mods.exists() and MARKETPLACE in mods.read_text(encoding="utf-8", errors="replace")


def shared_caveman(root: Path):
    """
    Another workspace's caveman copy, if the machine already uses one: one plugin per machine, or every session would
    run two cavemans. Returns its folder, or None
    """
    registered = read_json(CLAUDE / "settings.json").get("extraKnownMarketplaces", {}).get(MARKETPLACE, {}).get("source", {}).get("path")
    if registered and not under(registered, root) and is_our_caveman(Path(registered)):
        return Path(registered)
    return None


def caveman_files(root: Path, decisions: dict, dry: bool) -> Path:
    shared = shared_caveman(root)
    if shared:
        say(f"caveman shared with another workspace: {shared} (one copy per machine)")
        dest = shared
    else:
        dest = lean(root) / "plugins" / "caveman"
        if dest.exists() and not is_our_caveman(dest):
            backup(dest, dry)
        say(f"fetch trimmed caveman -> {dest}")
        if not dry:
            sys.path.insert(0, str(HERE))
            import fetch_caveman
            fetch_caveman.fetch(dest)
    if read_json(root / ".caveman.json").get("defaultMode") != "caveman":
        backup(root / ".caveman.json", dry)
        write_json(root / ".caveman.json", {"defaultMode": "caveman"}, dry)
    say("caveman ON inside the root (.caveman.json)")
    cfg_path = APPDATA / "caveman" / "config.json"
    if decisions.get("caveman_scope") == "everywhere":
        say(f"caveman left as you had it outside the root ({cfg_path})")
        return dest
    cfg = read_json(cfg_path)
    cfg["defaultMode"] = "off"
    write_json(cfg_path, cfg, dry)
    say(f"caveman OFF elsewhere ({cfg_path})")
    return dest


def user_settings(root: Path, plugin_dir: Path | None, decisions: dict, facts: dict, tmp_in_root: bool, dry: bool) -> None:
    path = CLAUDE / "settings.json"
    if path.exists() and not dry:
        bak = CLAUDE / "backups" / f"settings.json.pre-lean.{int(time.time())}"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bak)
        say(f"backup {bak}")
    d = read_json(path)

    cmd = hook_command(root)
    ss = d.setdefault("hooks", {}).setdefault("SessionStart", [])
    old = legacy_hook_command(root)
    for e in ss:
        kept = [h for h in e.get("hooks", []) if h.get("command") != old]
        if len(kept) != len(e.get("hooks", [])):
            e["hooks"] = kept
            say("old SessionStart hook (flat layout) removed")
    ss[:] = [e for e in ss if e.get("hooks")]
    if not any(h.get("command") == cmd for e in ss for h in e.get("hooks", [])):
        ss.append({"hooks": [{"type": "command", "command": cmd, "timeout": 30, "statusMessage": "workspace bootstrap..."}]})
        say("SessionStart hook added")

    if tmp_in_root and decisions.get("tmpdir") != "keep":
        tmp = lean(root) / "tmp"
        if not dry:
            tmp.mkdir(exist_ok=True)
        d.setdefault("env", {})["CLAUDE_CODE_TMPDIR"] = str(tmp)
        say(f"CLAUDE_CODE_TMPDIR = {tmp} (temp files and session scratchpads now stay in the root, from the next session)")

    if plugin_dir:
        if decisions.get("other_caveman") == "disable":
            for k in facts.get("other_caveman", []):
                d.setdefault("enabledPlugins", {})[k] = False
                say(f"{k} disabled (still installed: `claude plugin enable {k}` restores it)")
        facts["old_marketplace_path"] = d.get("extraKnownMarketplaces", {}).get(MARKETPLACE, {}).get("source", {}).get("path")
        d.setdefault("extraKnownMarketplaces", {})[MARKETPLACE] = {"source": {"source": "directory", "path": str(plugin_dir)}}
        d.setdefault("enabledPlugins", {})[PLUGIN] = True
        say(f"plugin {PLUGIN} enabled")
        script = plugin_dir / "src" / "hooks" / "caveman-statusline.ps1"
        stable = CLAUDE / "hooks" / "caveman-statusline.ps1"
        if "statusLine" in d:
            say("you already have a statusLine: left untouched")
        elif script.exists() or dry:
            if not dry:
                stable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script, stable)
            d["statusLine"] = {"type": "command", "command": f'powershell -NoProfile -ExecutionPolicy Bypass -File "{stable.as_posix()}"'}
            say("caveman status line set")
    write_json(path, d, dry)


def claude_cli(plugin_dir: Path, facts: dict, dry: bool) -> None:
    if dry:
        say("would run: claude plugin marketplace add / plugin install")
        return
    if not shutil.which("claude"):
        say("claude CLI not found: install the plugin later with "
            f'`claude plugin marketplace add "{plugin_dir}"` and `claude plugin install {PLUGIN}`')
        return
    steps = [["plugin", "marketplace", "add", str(plugin_dir)], ["plugin", "install", PLUGIN, "--scope", "user"]]
    old = facts.get("old_marketplace_path")
    if old and norm(old) != norm(str(plugin_dir)):   # moved (flat layout): register the new folder
        steps.insert(0, ["plugin", "marketplace", "remove", MARKETPLACE])
    for args in steps:
        r = subprocess.run(["claude", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", shell=os.name == "nt")
        say(f"claude {' '.join(args[:3])}: {'ok' if r.returncode == 0 else 'FAILED ' + (r.stderr or r.stdout).strip()[:200]}")


def save_config(root: Path, decisions: dict, facts: dict, with_caveman: bool, tmp_in_root: bool, dry: bool) -> None:
    path = config_path(root)
    cfg = saved_config(root)
    if tmp_in_root:
        cfg["tmp_in_root"] = decisions.get("tmpdir") != "keep"
    if with_caveman:
        cfg["caveman_scope"] = decisions.get("caveman_scope", cfg.get("caveman_scope", "root"))
    cfg["auto_memory"] = decisions.get("memory") == "keep" if "memory" in decisions else cfg.get("auto_memory", False)
    shared = facts.get("plugin_dir")
    if shared and not under(shared, root):
        cfg["plugin_dir"] = shared
    else:
        cfg.pop("plugin_dir", None)
    unguarded = set(cfg.get("unguarded", []))
    if decisions.get("outside_readers") == "unguard":
        unguarded |= set(facts.get("readers", []))
    cfg["unguarded"] = sorted(unguarded)
    if "outside_readers" in decisions:
        cfg["readers_decided"] = sorted(set(cfg.get("readers_decided", [])) | set(facts.get("readers", [])))
    if "claude_md" in decisions:
        cfg["claude_md"] = decisions["claude_md"]
    if decisions.get("memory") == "migrate":
        migrated = set(cfg.get("migrated_memory", []))
        migrated |= {str(p) for cwd, files in facts.get("memories", []) if Path(cwd).is_dir() for p in files}
        cfg["migrated_memory"] = sorted(migrated)
    write_json(path, cfg, dry)
    say(f"{LEAN}/config.json: {json.dumps(cfg)}")


def migrate_flat_layout(root: Path, plugin_dir) -> None:
    """
    Clean what an older install left in the root: tools, .lean.json, .plugins/caveman, .tmp
    """
    for name in TOOLS:
        old = root / name
        if old.exists() and any(m in old.read_text(encoding="utf-8", errors="replace")[:400] for m in LEGACY_TOOL_MARKS):
            old.unlink()
            say(f"flat layout: removed {name} from the root (now in {LEAN}/)")
    if (root / ".lean.json").exists():
        (root / ".lean.json").unlink()
        say(f"flat layout: .lean.json merged into {LEAN}/config.json")
    old_plugin = root / ".plugins" / "caveman"
    mods = old_plugin / "MODIFICATIONS.md"
    if plugin_dir and old_plugin.exists() and mods.exists() and MARKETPLACE in mods.read_text(encoding="utf-8", errors="replace"):
        shutil.rmtree(old_plugin, ignore_errors=True)
        say("flat layout: old .plugins/caveman removed")
    if (root / ".plugins").is_dir() and not any((root / ".plugins").iterdir()):
        (root / ".plugins").rmdir()
    old_tmp, new_tmp = root / ".tmp", lean(root) / "tmp"
    if old_tmp.is_dir():
        new_tmp.mkdir(parents=True, exist_ok=True)
        left = 0
        for item in list(old_tmp.iterdir()):
            try:
                shutil.move(str(item), str(new_tmp / item.name))
            except Exception:
                left += 1   # in use by a running session
        try:
            old_tmp.rmdir()
            say(f"flat layout: .tmp moved to {LEAN}/tmp")
        except OSError:
            say(f"flat layout: .tmp moved to {LEAN}/tmp except {left} item(s) in use: delete {old_tmp} when no session runs")


def hide(root: Path) -> None:
    """
    Windows: hide the framework's own entries so the root shows only projects (and CLAUDE.md)
    """
    if os.name != "nt":
        return
    for name in (LEAN, ".caveman.json", ".claude"):
        target = root / name
        if target.exists():
            subprocess.run(["attrib", "+h", str(target)], capture_output=True)
    say(f"hidden: {LEAN}, .caveman.json, .claude")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = sys.argv[1:]
    language = "English"
    choices = {}
    while "--language" in argv or "--choose" in argv:
        flag = "--language" if "--language" in argv else "--choose"
        i = argv.index(flag)
        value = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
        if flag == "--language":
            language = value
        elif "=" in value:
            k, v = value.split("=", 1)
            choices[k.strip()] = v.strip()
        else:
            raise SystemExit(f"--choose expects KEY=VALUE, got {value!r}")
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    dry = "--dry-run" in flags
    with_caveman = "--no-caveman" not in flags
    check_root(root)
    # once chosen, --tmp-in-root sticks: re-runs without the flag keep it
    tmp_in_root = "--tmp-in-root" in flags or saved_config(root).get("tmp_in_root") is True
    print(f"Workspace root: {root}{'  (dry run: nothing is changed)' if dry else ''}")

    decisions, facts = preflight(root, with_caveman, tmp_in_root, choices, "--yes" in flags, dry)

    copy_tools(root, language, decisions, dry)
    if decisions.get("memory") == "migrate":
        migrate_memories(root, facts["memories"], dry)
    plugin_dir = caveman_files(root, decisions, dry) if with_caveman else None
    facts["plugin_dir"] = str(plugin_dir) if plugin_dir else None
    user_settings(root, plugin_dir, decisions, facts, tmp_in_root, dry)
    shared = plugin_dir is not None and not under(str(plugin_dir), root)
    if plugin_dir and "--no-cli" not in flags and not shared:   # a shared copy is already installed
        claude_cli(plugin_dir, facts, dry)
    save_config(root, decisions, facts, with_caveman, tmp_in_root, dry)
    if not dry:
        migrate_flat_layout(root, plugin_dir)
        hide(root)
        subprocess.run([sys.executable, str(lean(root) / "bootstrap.py")], check=False)
    print(f"\nDone. Check it: python \"{lean(root) / 'doctor.py'}\"   (add --live for a real session test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
