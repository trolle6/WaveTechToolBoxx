# Pterodactyl startup (python generic egg)

## Read this if logs still look “old”

Your console must show **`Deployed: branch=master commit=…`** and a **recent**
commit (currently `9ca62fb` or newer), then `Starting master@…`.

If you see something like `Starting unknown@7a72b8b…` plus INFO spam
(`TTS enabled`, `FFmpeg found`, `Allowed channel configured`) — **the panel is
still on the stock egg startup** (soft `git pull`). GitHub `master` never made it
onto the server. Paste the command below again, save, **restart**.

Voice `TimeoutError` on Pterodactyl is **not** fixed by startup. Discord voice UDP
does not work on typical Ptero hosts — use NAS + `network_mode: host` for TTS.

## Panel variables

| Variable | Value |
|---|---|
| Docker image | **Python 3.12** or **3.13** (not 3.8/3.9) |
| `BRANCH` | `master` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `AUTO_UPDATE` | `1` (unused by our startup; set anyway) |
| `GIT_ADDRESS` | `https://github.com/trolle6/WaveTechToolBoxx` (Reinstall) |

Secrets → Environment / `config.env`. Never put tokens in the startup command.

## Startup command (replace the egg default entirely)

**Always** hard-resets tracked files (does not depend on `AUTO_UPDATE`), then runs
`ptero-start.sh` from the repo for pip + `PYTHONPATH`.

One line (paste into Startup):

```bash
cd /home/container; BRANCH_NAME="{{BRANCH}}"; if [[ -z "${BRANCH_NAME}" || "${BRANCH_NAME}" == "{{BRANCH}}" ]]; then BRANCH_NAME=master; fi; if [[ ! -d .git ]]; then echo "FATAL: no .git — Reinstall server with GIT_ADDRESS set"; exit 1; fi; echo "FORCE RESET → origin/${BRANCH_NAME}"; git fetch origin "${BRANCH_NAME}" --prune || { echo "FATAL: git fetch failed"; exit 1; }; git checkout -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"; git reset --hard "origin/${BRANCH_NAME}"; export GIT_BRANCH_ACTUAL="$(git rev-parse --abbrev-ref HEAD)"; echo "Deployed: branch=${GIT_BRANCH_ACTUAL} commit=$(git rev-parse --short HEAD)"; test -f /home/container/ptero-start.sh || { echo "FATAL: ptero-start.sh missing after reset — wrong repo/branch?"; exit 1; }; exec bash /home/container/ptero-start.sh
```

Readable:

```bash
cd /home/container

BRANCH_NAME="{{BRANCH}}"
if [[ -z "${BRANCH_NAME}" || "${BRANCH_NAME}" == "{{BRANCH}}" ]]; then
  BRANCH_NAME=master
fi

if [[ ! -d .git ]]; then
  echo "FATAL: no .git — Reinstall server with GIT_ADDRESS set"
  exit 1
fi

echo "FORCE RESET → origin/${BRANCH_NAME}"
git fetch origin "${BRANCH_NAME}" --prune || { echo "FATAL: git fetch failed"; exit 1; }
git checkout -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"
git reset --hard "origin/${BRANCH_NAME}"
export GIT_BRANCH_ACTUAL="$(git rev-parse --abbrev-ref HEAD)"
echo "Deployed: branch=${GIT_BRANCH_ACTUAL} commit=$(git rev-parse --short HEAD)"

test -f /home/container/ptero-start.sh || {
  echo "FATAL: ptero-start.sh missing after reset — wrong repo/branch?"
  exit 1
}
exec bash /home/container/ptero-start.sh
```

Optional: import `pterodactyl-egg.wavetech.json` (same startup baked in).

## After restart you MUST see

```text
FORCE RESET → origin/master
Deployed: branch=master commit=<recent sha>
PYTHONPATH=/home/container/.local/lib/python3.xx/site-packages
Starting main.py (python=Python 3.xx.x)
Starting master@<same sha>
Loaded 4/4 cogs
Logged in as ...
```

No `FORCE RESET` / `Deployed:` lines ⇒ panel still has the **old** startup. Paste again.

## What hard-reset does not wipe

`config.env`, `cogs/archive/`, `secret_santa_state.json`, uploads (gitignored).
Does **not** run `git clean -fd`.

## TTS

| Host | Slash commands | TTS / voice |
|---|---|---|
| Pterodactyl | OK | **Fails** (UDP / IP discovery) |
| TrueNAS + `network_mode: host` | OK | OK |
