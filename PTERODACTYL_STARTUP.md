# Pterodactyl startup

## Errors in your log (decoded)

| Log line | Meaning |
|---|---|
| `Updating 7a72b8b..e6781a1` then **commit or stash before you merge** | Soft `git pull` hit dirty `main.py` / `deploy.py` and **aborted**. Code never updated. |
| `Starting unknown@7a72b8baf578` | Still on ancient commit (not current master). |
| `Allowed channel configured` / `TTS enabled` at INFO | Old voice cog (master has these at DEBUG). |
| `ExtensionNotFound: cogs.DALLE_cog` | Partial/stale tree after failed pull. |
| Voice `TimeoutError` | Ptero host UDP limit — use NAS + host network for TTS. |

If you ran `source startup.sh` and still see the merge abort: that was an **old local**
`startup.sh` that only did `git pull`. Repo now ships a force-reset `startup.sh`.

## Fix RIGHT NOW (paste in Ptero console)

Do **not** use the old `startup.sh` until this succeeds once:

```bash
cd /home/container
git fetch origin master --prune
git reset --hard HEAD
git checkout -f -B master origin/master
git reset --hard origin/master
bash startup.sh
```

You must see `FORCE RESET → origin/master` and `Deployed: branch=master commit=…`
with a sha that is **not** `7a72b8b`.

## Panel Startup field (replace egg default)

```bash
cd /home/container; if [[ ! -d .git ]]; then echo "FATAL: no .git"; exit 1; fi; git fetch origin master --prune || exit 1; git reset --hard HEAD || true; git checkout -f -B master origin/master; git reset --hard origin/master; export GIT_BRANCH_ACTUAL=master; echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"; exec bash /home/container/startup.sh
```

Or simply (after the one-time console fix above):

```bash
bash /home/container/startup.sh
```

Variables: Docker **Python 3.12/3.13**, `PY_FILE=main.py`, `REQUIREMENTS_FILE=requirements.txt`,
`BRANCH=master`. Secrets in Environment / `config.env` only.

## What startup.sh does

1. `git fetch` + `reset --hard` + `checkout -f -B master origin/master` (discards dirty tracked files)
2. Selective `git clean` (keeps `config.env`, `.local`, archives, state)
3. `pip --prefix .local` + `PYTHONPATH`
4. Run `main.py`

Never uses soft `git pull`.
