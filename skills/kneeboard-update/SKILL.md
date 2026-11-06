---
name: kneeboard-update
description: Update an existing source-installed Kneeboard application and its bundled AI skills from the official GitHub repository. Use when the user asks to update Kneeboard; not for reviewing code or upgrading unrelated software.
---

Update the existing installation using [scripts/update.py](scripts/update.py), resolved relative to this skill directory. Do not install from PyPI: Kneeboard currently uses source installs from https://github.com/JPPrado1657/kneeboard.git.

Locate the user's Kneeboard checkout and its installed Python environment from conversation context, the `kneeboard` executable, or pip's installed distribution metadata (`direct_url.json` can identify a source checkout). Prefer `<checkout>/.venv/bin/python` on macOS/Linux or `<checkout>/.venv/Scripts/python.exe` on Windows when it contains `ai-kneeboard`. Verify the environment rather than choosing an arbitrary system Python. Do not confuse the repository being reviewed with the Kneeboard application's repository.

Once paths are established, run:

```sh
python3 /absolute/path/to/kneeboard-update/scripts/update.py \
  --repo /absolute/path/to/kneeboard \
  --python /absolute/path/to/kneeboard/.venv/bin/python
```

The script checks the official origin and a clean `main` checkout, fetches main, permits only a fast-forward, reinstalls the application into the selected environment, verifies the CLI, then refreshes `kneeboard` and `kneeboard-update` in `${CODEX_HOME:-~/.codex}/skills`. Pass `--skills-dir /absolute/path` for another assistant or installation location. Updating replaces bundled files in those two installed skill folders; keep personal customizations elsewhere.

If discovery is ambiguous, identify the missing installation path or environment instead of guessing. Never discard local edits, switch branches, use sudo, force-reset history, change remotes, or stop review sessions to make an update succeed. Report local changes or divergence and stop. The user can resolve those without losing work. If a step fails, report the failed step and successful earlier steps; the script does not roll back a completed Git update or partial package installation. Retry only after resolving the specific cause.

A matching source commit still triggers reinstall and skill sync so retrying an interrupted update repairs the installation. This follows the latest `main` commit, not a numbered release channel. Report the resulting commit and remind the user to restart Kneeboard and start a new AI session for updated skills. Do not claim running sessions were upgraded. Do not write or preserve review data during an update.
