# Contributing

Thanks for helping. Keep changes small and focused: one topic per pull request.

## Branches

Name the branch after the area you change plus a short description:

```
<area>/<short-description>
```

Areas: `install`, `bootstrap`, `doctor`, `forget`, `os` (`lean_os.py`), `caveman` (`fetch_caveman.py`), `templates`, `docs`.
Examples: `forget/scratchpads-on-linux`, `docs/install-steps`.

## Pull requests opened with AI agents

Welcome, provided that a human has reviewed every change before opening the pull request. Please specify in the description which agent was used and confirm that you have reviewed the result. Automatically generated pull requests that have not been reviewed will be closed immediately!

## Testing

A pull request is merged only if it was tested with a positive outcome and passes every test in the repository at the time it is opened, including any added after this file was written.

At minimum:

- install into a throwaway workspace with a fake home, never your real `~/.claude`:
  set `LEAN_HOME` and `LEAN_APPDATA` to empty test folders (`LEAN_OS=windows|mac|linux` forces a system), then run
  `install.py <test-root> --yes --tmp-in-root --no-cli` twice (the second run must change nothing)
- `python <test-root>/.lean/doctor.py` passes every check
- for changes to hooks, settings or caveman: `doctor.py --live` on a real install
- say in the pull request which OS you tested on and paste the doctor output

## Code

- Python 3.9+. External libraries are fine if listed in `requirements.txt` and documented in the README (what they are for, which script needs them). The hook runs `bootstrap.py` with the system interpreter, so its dependencies must be installed there: prefer the standard library for `workspace/` scripts: let’s not add unnecessary complexity
- Every Windows / macOS / Linux difference goes in `workspace/lean_os.py`, not in the other scripts
- Never write outside the workspace root or `~/.claude`, and back up user files before changing them
- New measurements in the README must be reproducible: say what was measured and how
- caveman is fetched at install time, never committed to this repository
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`...)

## Writing style

README and other files for people: plain prose. Files read by agents (`templates/CLAUDE.md`, `CLAUDE*.md`): terse caveman style, facts only.

## License

By contributing you agree that your work is released under the MIT license of this repository.
