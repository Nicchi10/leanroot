"""
Fetch caveman (github.com/juliusbrussee/caveman) and keep only the lean part.

Why: every skill a plugin ships is listed to the model in every session, and plugin skills cannot be hidden one by one (`skillOverrides` does not apply to
plugins). The full plugin lists 21 skills and 3 agents, this keeps the mode hooks, the status line and a handful of commands

    python fetch_caveman.py <dest> [--ref v4.1.0]

<dest> is replaced. Upstream is Apache-2.0: its LICENSE and NOTICE are kept and
MODIFICATIONS.md states what was removed or changed (Apache-2.0 section 4).
Requires git.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "https://github.com/juliusbrussee/caveman"
DEFAULT_REF = "v4.1.0"
MARKETPLACE = "caveman-lean"

# What the hooks need: plugin manifest, src/ (hooks + statusline), commands/,  the three mode rule files, licenses, plus the skills worth keeping
KEEP_DIRS = [".claude-plugin", "src", "commands"]
KEEP_FILES = ["LICENSE", "LICENSE-MIT", "NOTICE"]
KEEP_SKILLS = [
    "caveman", "ultracave", "megacave",           # mode rules, read by the hooks
    "caveman-commit", "caveman-review", "caveman-compress", "caveman-stats", "caveman-help",
]


def fetch(dest: Path, ref: str = DEFAULT_REF) -> Path:
    dest = dest.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "caveman"
        r = subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", ref, UPSTREAM, str(src)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise SystemExit(f"git clone failed: {r.stderr.strip()}")
        commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        missing = [s for s in KEEP_SKILLS if not (src / "skills" / s).is_dir()]
        if missing:
            raise SystemExit(f"upstream {ref} lacks skills {missing}: update KEEP_SKILLS")

        if dest.exists():
            shutil.rmtree(dest)
        (dest / "skills").mkdir(parents=True)
        for d in KEEP_DIRS:
            shutil.copytree(src / d, dest / d)
        for f in KEEP_FILES:
            if (src / f).exists():
                shutil.copy2(src / f, dest / f)
        for s in KEEP_SKILLS:
            shutil.copytree(src / "skills" / s, dest / "skills" / s)
        all_skills = sorted(p.name for p in (src / "skills").iterdir() if p.is_dir())
        all_agents = sorted(p.stem for p in (src / "agents").glob("*.md")) if (src / "agents").is_dir() else []

    mp_path = dest / ".claude-plugin" / "marketplace.json"
    mp = json.loads(mp_path.read_text(encoding="utf-8"))
    mp["name"] = MARKETPLACE
    mp["description"] = f"Trimmed local copy of caveman {ref} (mode hooks + basic commands)."
    mp_path.write_text(json.dumps(mp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    dropped = [s for s in all_skills if s not in KEEP_SKILLS]
    (dest / "MODIFICATIONS.md").write_text(
        f"# Modifications\n\n"
        f"Derived from {UPSTREAM} at `{ref}` (commit `{commit}`), Apache-2.0.\n\n"
        f"- Kept only: {', '.join(KEEP_DIRS + KEEP_FILES)}, skills {', '.join(KEEP_SKILLS)}.\n"
        f"- Removed from `skills/`: {', '.join(dropped) or 'none'}.\n"
        f"- Removed agents: {', '.join(all_agents) or 'none'}.\n"
        f"- Removed everything else in the repository (installers, engine, proxy, docs, tests).\n"
        f"- `.claude-plugin/marketplace.json`: `name` changed to `{MARKETPLACE}`, `description` changed.\n"
        f"- No source file was edited.\n",
        encoding="utf-8")
    return dest


def main() -> int:
    argv = sys.argv[1:]
    ref = DEFAULT_REF
    if "--ref" in argv:
        i = argv.index("--ref")
        ref = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = fetch(Path(argv[0]), ref)
    print(f"caveman {ref} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
