# Pterodactyl startup (python generic egg)

The stock egg startup is **not enough** for this bot. It only runs `git pull`
(soft, can leave you on old / dirty code) and installs into `.local` **without**
putting that on `PYTHONPATH` — so imports fail or you keep running stale files.

Use the startup below. Paste it into the server **Startup** command (replaces the
egg default entirely). Secrets stay in Environment / `config.env` — never in this
command.

## Panel variables

| Variable | Value |
|---|---|
| Docker image | **Python 3.12** (or 3.11/3.13). Not 3.8/3.9. |
| `AUTO_UPDATE` | `1` |
| `BRANCH` | `master` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `GIT_ADDRESS` | your repo URL (used on **Reinstall**) |
| `USER_UPLOAD` | `0` |

Bot secrets (`DISCORD_TOKEN`, `OPENAI_API_KEY`, channel IDs, optional
`VOICE_TIMEOUT=30`, …) → **Environment** and/or `config.env` in `/home/container`.

## Startup command (paste this)

One line (panel-friendly):

```bash
cd /home/container; if [[ -d .git ]] && [[ "{{AUTO_UPDATE}}" == "1" ]]; then BRANCH_NAME="{{BRANCH}}"; if [[ -z "${BRANCH_NAME}" || "${BRANCH_NAME}" == "{{BRANCH}}" ]]; then BRANCH_NAME=master; fi; echo "Updating origin/${BRANCH_NAME} (hard reset of tracked files only)..."; git fetch origin "${BRANCH_NAME}" --prune; git checkout -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"; git reset --hard "origin/${BRANCH_NAME}"; export GIT_BRANCH_ACTUAL="$(git rev-parse --abbrev-ref HEAD)"; echo "Deployed: branch=${GIT_BRANCH_ACTUAL} commit=$(git rev-parse --short HEAD)"; fi; if [[ ! -z "{{PY_PACKAGES}}" ]]; then pip install -U --prefix .local {{PY_PACKAGES}}; fi; if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi; export PATH="/home/container/.local/bin:${PATH:-}"; for d in /home/container/.local/lib/python*/site-packages; do [ -d "$d" ] && export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"; done; exec /usr/local/bin/python /home/container/{{PY_FILE}}
```

Readable form (same logic):

```bash
cd /home/container

if [[ -d .git ]] && [[ "{{AUTO_UPDATE}}" == "1" ]]; then
  BRANCH_NAME="{{BRANCH}}"
  if [[ -z "${BRANCH_NAME}" || "${BRANCH_NAME}" == "{{BRANCH}}" ]]; then
    BRANCH_NAME=master
  fi

  echo "Updating origin/${BRANCH_NAME} (hard reset of tracked files only)..."
  git fetch origin "${BRANCH_NAME}" --prune
  git checkout -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"
  git reset --hard "origin/${BRANCH_NAME}"
  export GIT_BRANCH_ACTUAL="$(git rev-parse --abbrev-ref HEAD)"
  echo "Deployed: branch=${GIT_BRANCH_ACTUAL} commit=$(git rev-parse --short HEAD)"
fi

if [[ ! -z "{{PY_PACKAGES}}" ]]; then
  pip install -U --prefix .local {{PY_PACKAGES}}
fi

if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then
  pip install -U --prefix .local -r ${REQUIREMENTS_FILE}
fi

export PATH="/home/container/.local/bin:${PATH:-}"
for d in /home/container/.local/lib/python*/site-packages; do
  [ -d "$d" ] && export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"
done

exec /usr/local/bin/python /home/container/{{PY_FILE}}
```

Optional: import `pterodactyl-egg.wavetech.json` as a custom egg (same startup baked in).

## Why the stock egg fails here

Stock startup from the python generic egg:

```text
git pull  →  pip --prefix .local  →  python {{PY_FILE}}
```

| Problem | Effect |
|---|---|
| `git pull` only | Dirty / divergent tree → **old code keeps running** |
| No `PYTHONPATH=.local/...` | `disnake` / deps installed but **not importable** |
| No branch checkout | `BRANCH=master` ignored on start |

This startup: `fetch` + `checkout -B` + `reset --hard` + `PYTHONPATH` + `exec python`.

## What is / is not wiped

| Action | Effect |
|---|---|
| `git reset --hard origin/master` | Overwrites **tracked** code to match GitHub |
| `git clean -fd` | **Not used** — would delete `config.env`, archives, state |

`config.env`, `cogs/archive/`, `secret_santa_state.json`, uploads are gitignored —
hard reset does **not** wipe them. Back those up in the panel yourself if you want.

## After restart you should see

```text
Updating origin/master (hard reset of tracked files only)...
Deployed: branch=master commit=<sha>
Starting master@<sha>
Loaded 4/4 cogs
Logged in as <bot>
```

If you still see soft `Already up to date` from `git pull` and no `Deployed:` line,
the panel is still on the **old** startup — paste again and restart.

## TTS note

This fixes “stuck on old code / missing deps”. It does **not** fix Pterodactyl hosts
that cannot complete Discord voice UDP. For TTS use NAS + `network_mode: host`
(see `DEPLOYMENT.md`).
