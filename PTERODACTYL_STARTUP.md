# Pterodactyl startup (generic Python egg)

Works with the stock **python generic** egg (`AUTO_UPDATE`, `BRANCH`, `PY_FILE`,
`REQUIREMENTS_FILE`, …).

## Panel variables (set these — not in the startup script)

| Variable | Value |
|---|---|
| `AUTO_UPDATE` | `1` |
| `BRANCH` | `master` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `GIT_ADDRESS` | your repo URL (install / reinstall) |

Bot secrets (`DISCORD_TOKEN`, `OPENAI_API_KEY`, channel IDs, `VOICE_TIMEOUT`, …)
stay in **Environment** and/or `config.env` — same as TrueNAS. Do **not** put them
in the startup command.

Back up Secret Santa archives / state yourself in the panel when you want — not here.

## Startup command (paste into the egg / server)

Replaces the egg default `git pull` (which does **not** force overwrite).

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
  echo "Deployed: branch=$(git rev-parse --abbrev-ref HEAD) commit=$(git rev-parse --short HEAD)"
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

## What this does / does not

| Action | Effect |
|---|---|
| `git reset --hard origin/master` | Overwrites **tracked** code to match GitHub. |
| `git clean -fd` | **Not used** — would delete untracked `config.env`, archives, state. |

`config.env`, `cogs/archive/`, `secret_santa_state.json`, and uploads are untracked /
gitignored, so a hard reset alone does **not** wipe them.

## After restart you should see

```
Updating origin/master (hard reset of tracked files only)...
Deployed: branch=master commit=<sha>
```

## TTS note

This startup fixes “stuck on old code”. It does **not** fix Pterodactyl hosts that
cannot complete Discord voice UDP — use the NAS + `network_mode: host` for TTS.
