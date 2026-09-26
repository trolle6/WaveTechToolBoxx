# Pterodactyl startup

## `startup.sh` is FROZEN

The file **`startup.sh` in this repo is the official startup command.**
**Do not change it.** Future work must leave `startup.sh` exactly as committed.

Panel Startup field (or console):

```bash
bash /home/container/startup.sh
```

(or `source startup.sh` — same file)

## Required panel variables

| Variable | Value |
|---|---|
| `AUTO_UPDATE` | `1` |
| `BRANCH` | `master` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| Docker image | Python 3.12 or 3.13 |

Secrets stay in Environment / `config.env`.

## One-time unblock (only if soft pull aborts)

If logs show `Please commit your changes or stash them before you merge`,
the working tree is dirty. Run **once** in the console (this is not a change to
`startup.sh`):

```bash
cd /home/container
git fetch origin master --prune
git reset --hard origin/master
git checkout -f -B master origin/master
git reset --hard origin/master
bash startup.sh
```

After that, with a clean tree and `AUTO_UPDATE=1`, `startup.sh`’s `git pull` can
update normally.

## TTS

Voice on typical Pterodactyl hosts still fails (Discord UDP). Use NAS +
`network_mode: host` for TTS.
