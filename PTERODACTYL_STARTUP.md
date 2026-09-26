# Pterodactyl

Startup command: `source startup.sh` (or `bash /home/container/startup.sh`)

| Variable | Value |
|---|---|
| `AUTO_UPDATE` | ON |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `BRANCH` | `master` (used when nuke is ON) |
| `GIT_HARD_RESET_NUKE` | OFF normally; ON only to force `reset --hard` to `origin/BRANCH` |

- Nuke **OFF** → `git pull` (default)
- Nuke **ON** → fetch + hard reset (use when pull aborts on dirty files; backup first)
