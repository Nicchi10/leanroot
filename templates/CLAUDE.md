# Workspace rules

- Work only in `{{ROOT}}`. No reads, searches or writes outside it, except the session scratchpad. If a task needs outside, stop and ask; never route around the block via shell
- Project instructions (`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`) win over these: read them first
- Reply in {{LANGUAGE}}
- Python in the project's `venv`, Node in its `node_modules`. No global installs without asking
- Git: check `git status` + branch before touching a repo, work on a dedicated branch, never push/merge/rebase/force without OK. Never commit `venv/`, `node_modules/`, `__pycache__/`, `build/`, `dist/` or secrets
- Before deletions, migrations or wide refactors: propose plan, wait for OK
- Agent files (`CLAUDE*.md`, runbooks, `.claude/`): terse caveman style, facts only. README and git-tracked docs: normal prose
- Notes worth keeping go in the project's `CLAUDE.md` / `CLAUDE.local.md` (auto-memory is off)
- Temp files in the scratchpad, never in repos
- Report results honestly: failing test = say so, show output
