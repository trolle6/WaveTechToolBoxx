# Pterodactyl startup (python generic egg)

## The error: “Please commit your changes or stash them before you merge”

That means the container has **dirty local edits** on tracked files. Stock
`git pull` (and a plain `git checkout`) **aborts**, so you stay forever on an
ancient commit like `7a72b8b`.

The startup below **throws away local tracked changes**, force-checks out
`origin/master`, then starts the bot. Secrets/state are not wiped.

## Panel variables

| Variable | Value |
|---|---|
| Docker image | **Python 3.12** or **3.13** |
| `BRANCH` | `master` (startup always deploys **master**) |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `GIT_ADDRESS` | `https://github.com/trolle6/WaveTechToolBoxx` |

Secrets → Environment / `config.env`. Never in the startup command.

## Startup command (replace egg default entirely)

One line (paste into Startup):

```bash
cd /home/container; if [[ ! -d .git ]]; then echo "FATAL: no .git — Reinstall with GIT_ADDRESS set"; exit 1; fi; echo "FORCE RESET → origin/master (discard local tracked changes)"; git fetch origin master --prune || { echo "FATAL: git fetch failed"; exit 1; }; git reset --hard HEAD || true; git checkout -f -B master origin/master; git reset --hard origin/master; git clean -fd -e config.env -e .local -e cogs/archive -e cogs/secret_santa_state.json -e cogs/distributed_files -e cogs/distributed_files_metadata.json -e __pycache__ || true; export GIT_BRANCH_ACTUAL=master; echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"; test -f /home/container/ptero-start.sh || { echo "FATAL: ptero-start.sh missing after reset"; exit 1; }; exec bash /home/container/ptero-start.sh
```

Readable:

```bash
cd /home/container

if [[ ! -d .git ]]; then
  echo "FATAL: no .git — Reinstall with GIT_ADDRESS set"
  exit 1
fi

echo "FORCE RESET → origin/master (discard local tracked changes)"
git fetch origin master --prune || { echo "FATAL: git fetch failed"; exit 1; }

# Fixes: "Please commit your changes or stash them before you merge"
git reset --hard HEAD || true
git checkout -f -B master origin/master
git reset --hard origin/master

# Drop untracked junk only (keep secrets, state, .local deps)
git clean -fd \
  -e config.env \
  -e .local \
  -e cogs/archive \
  -e cogs/secret_santa_state.json \
  -e cogs/distributed_files \
  -e cogs/distributed_files_metadata.json \
  -e __pycache__ || true

export GIT_BRANCH_ACTUAL=master
echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"

test -f /home/container/ptero-start.sh || {
  echo "FATAL: ptero-start.sh missing after reset"
  exit 1
}
exec bash /home/container/ptero-start.sh
```

Optional egg import: `pterodactyl-egg.wavetech.json` (same startup).

## After restart you MUST see

```text
FORCE RESET → origin/master (discard local tracked changes)
Deployed: branch=master commit=<recent sha>
PYTHONPATH=...
Starting master@<same sha>
Loaded 4/4 cogs
```

Still seeing `7a72b8b` / `Allowed channel configured` / no `FORCE RESET` line ⇒
the Startup field was **not** replaced. Paste again, Save, Restart.

## Kept vs discarded

| Kept | Discarded |
|---|---|
| `config.env` | Dirty edits to tracked code |
| `cogs/archive/`, SS state, uploads | Untracked junk (via selective `git clean`) |
| `.local/` pip packages | |

## TTS

Pterodactyl voice UDP usually fails. Use NAS + `network_mode: host` for TTS.
Slash commands on Ptero are fine once master is actually deployed.
