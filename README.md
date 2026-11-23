<p align="center">
  <img src="images/logo.png" alt="leanroot - a lean, fenced workspace for AI coding agents" width="200" />
</p>

<h1 align="center">leanroot</h1>

<p align="center">
  <em>A folder where every Claude Code session starts small, stays inside its walls and leaves no clutter behind.</em>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-Windows-5b21b6" alt="Platform: Windows" />
  <img src="https://img.shields.io/badge/python-3.9%2B-5b21b6" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/for-Claude_Code-5b21b6" alt="For Claude Code" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/CLAUDE.md-%E2%88%9262%25-22d3ee?labelColor=2e1065" alt="Root CLAUDE.md 62% smaller" />
  <img src="https://img.shields.io/badge/plugin_skills-24_%E2%86%92_7-22d3ee?labelColor=2e1065" alt="Plugin skills and agents from 24 to 7" />
  <img src="https://img.shields.io/badge/caveman_on_disk-26_MB_%E2%86%92_0.6_MB-22d3ee?labelColor=2e1065" alt="caveman plugin from 26 MB to 0.6 MB" />
  <img src="https://img.shields.io/badge/auto--memory-off-22d3ee?labelColor=2e1065" alt="Auto-memory off" />
</p>

Every session incurs a cost even before anything is launched, due to everything that gets loaded at startup instructions, memory, descriptions, plugins, and so on. The issue is that much of this material is never actually used.
The goal of this repository is to reduce that cost and keep it contained, every statement provided below is based on concrete, documented measurements.

## What you get

| | How |
|---|---|
| **A perimeter** | Agents cannot read or write files outside the workspace root (Claude Code's own `blockReadsOutsideWorkingDirectories` / `blockWritesOutsideWorkingDirectories`), while every project can still read its siblings |
| **Automatic setup of new projects** | A `SessionStart` hook runs `bootstrap.py`: a folder you create gets the guard settings by itself. Settings are not inherited by sub-folders in Claude Code, so each project needs its own copy |
| **One small `CLAUDE.md`** | General rules only, in terse "caveman" style, about 1 KB. Project-specific notes go in that project's own file and load only there |
| **No auto-memory** | The memory system prompt costs tokens in every session, notes live in files you control instead |
| **A trimmed caveman** | [caveman](https://github.com/juliusbrussee/caveman) makes answers shorter. It’s a fantastic repo, but it comes with things that many people don’t use, plugin skills cannot be hidden one by one, so `fetch_caveman.py` keeps only the mode and a few commands|

## Measured on test workspace

| | Before | After |
|---|---|---|
| Root `CLAUDE.md` | 2,751 bytes (rules + project map) | 1,044 bytes (rules only) |
| Skills listed by the caveman plugin | 21 skills + 3 agents | 7 skills, 0 agents |
| caveman plugin on disk | 26 MB | 0.6 MB |
| Auto-memory section in the system prompt | present | gone |
| A project runbook, rewritten in caveman style | 62,867 bytes | 38,675 bytes (-38%) |

Sizes are bytes, which do not depend on a tokenizer. The runbook rewrite was checked mechanically: every number, commit hash and issue reference of the original is still in it. To see what these changes cost in tokens on your own setup, run `/context` before and after.

## Install

Requirements: Windows, Python 3.9+, git, Node.js (for caveman's hooks), Claude Code.

```
git clone <this repo>
python install.py "C:\path\to\workspace" --language English
python "C:\path\to\workspace\.lean\doctor.py" --live
```

Everything the framework adds goes in one hidden folder, so the workspace root shows only your projects and `CLAUDE.md`:

```
workspace\
  CLAUDE.md          workspace rules (loaded by every session in the workspace)
  .caveman.json      hidden; must sit in the root, caveman looks for it walking up the folders
  .claude\           hidden; Claude Code's own settings for the root
  .lean\             hidden; bootstrap.py, forget.py, doctor.py, config.json, plugins\caveman, tmp\
  your-project\
```

An install from an older version with these files loose in the root is moved into `.lean\` automatically.

`install.py` is safe to re-run, and it backs up `~/.claude/settings.json` first. Before changing anything it runs a preflight against your existing setup and stops on every clash until you decide:

| Clash | Choices (recommended first) |
|---|---|
| another caveman plugin is enabled | disable it (it stays installed) / abort |
| caveman is already on outside the workspace | keep it everywhere / workspace only |
| auto-memories exist for projects in the workspace | migrate them into each project's `CLAUDE.local.md` / keep auto-memory / drop |
| the workspace already has its own `CLAUDE.md` | append the rules block (marked, refreshed on re-runs) / keep yours untouched |
| projects already read outside the workspace | leave them unguarded / guard them anyway |

Answer at the prompt, pass `--choose KEY=VALUE`, or `--yes` for every recommended choice. Decisions are saved in `<workspace>\.lean\config.json` and not asked again. An existing status line is never replaced.
Other options: `--no-caveman` (perimeter and setup only), `--dry-run` (print, change nothing).

**`--tmp-in-root` (recommended for a complete perimeter):** by default Claude Code keeps its temp files, the session scratchpad included, in the system temp folder (`%TEMP%\claude\...`), outside the workspace. This option sets `CLAUDE_CODE_TMPDIR` to `<workspace>\.lean\tmp`, so they stay inside. Shell commands run by Claude then get the same folder as `TEMP`/`TMPDIR`. It is a user setting: sessions outside the workspace use that folder too. The folder is cleaned with each session's transcript (30 days by default), not by Windows. If you move or rename the workspace, run `install.py` again from the new place, `doctor.py` reports the broken path.

Shell tools (pip, npm, Python's `tempfile`...) then write their temp files to `.lean\tmp` too, and Claude Code cleans only its own session folders. `bootstrap.py` therefore cleans `.lean\tmp` from the session-start hook, at most once a day: anything untouched for `tmp_max_age_days` (default 7, in `.lean\config.json`) is deleted, a folder counts as touched if anything inside it is, and session scratchpads are kept while their chat exists. `python .lean\bootstrap.py --clean-tmp` cleans now, `doctor.py` shows the folder size.

Several workspaces on one machine work: install each one. They share a single caveman copy (the first workspace's; one plugin per machine, or every session would run caveman twice), and each workspace's hook acts only inside its own root. `CLAUDE_CODE_TMPDIR` is one user setting, so only one workspace can host the temp files: the preflight recommends keeping it where it is, and `doctor.py` says which workspace has it.

Plan-mode files are also kept in each project: `bootstrap.py` sets `plansDirectory` to `<project>\.plans` (git-excluded) instead of `~/.claude/plans`.

After installing:

- Open a new session inside the workspace. A brand-new folder is set up by its first session and fully guarded from the second, because Claude Code reads settings at startup, before the hook runs
- Put project notes in `<project>/CLAUDE.local.md` (`templates/project-CLAUDE.local.md`) and exclude it in `<project>/.git/info/exclude`.
- After deleting a project: `python .lean\forget.py "<path>"`

## Further savings this doesn't automate (for now)

The largest fixed cost is often outside your files:

- **MCP connectors**: every server adds all of its tool descriptions to every session
- **Skills synced from claude.ai** and **bundled skills**: `syncClaudeAiSkills`, `disableBundledSkills` and `skillOverrides` (which works for those, not for plugins)
- **Long sessions**: every turn resends the conversation, `/clear` between tasks
- **Big instruction files**: keep a short "start here" that is always read and move the rest into files read on demand

## Limits

- Windows only for now (PowerShell status line, Windows paths)
- With `--tmp-in-root` and several workspaces, the session scratchpads of every workspace land in the one hosting `CLAUDE_CODE_TMPDIR` (each in its own subfolder). Claude Code always allows writes to the scratchpad, so a second workspace writes inside the first. Use `--tmp-in-root` only with a single workspace
- Shell commands are not hard-blocked by the perimeter: Claude Code asks before a command that names an outside path, and `CLAUDE.md` tells the agent not to try
- Claude Code's settings change fast: `doctor.py --live` is the way to know it still works on your version

## License

MIT, see `LICENSE`. Free for personal and commercial use.

## Credits

Caveman by Julius Brussee, Apache-2.0, fetched at install time, not redistributed. See `NOTICE`
