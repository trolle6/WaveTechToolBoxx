# Pterodactyl — READ THIS

## Why you are stuck on `7a72b8b`

Your panel Startup is effectively:

```text
source startup.sh   ← OLD local file that only does soft `git pull`
```

That old file:

1. Runs `git pull`
2. Aborts on dirty `main.py` / `deploy.py`
3. Also aborts because untracked `startup.sh` blocks merge of the **new** `startup.sh`
4. Then starts the bot anyway on ancient code

**GitHub is fine. Your container never installs it.**

## STOP. Paste this in the console NOW

Do **not** type `source startup.sh`. Paste **all** of this:

```bash
cd /home/container
rm -f startup.sh
git fetch origin master --prune
git reset --hard HEAD
git checkout -f -B master origin/master
git reset --hard origin/master
export GIT_BRANCH_ACTUAL=master
echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"
bash startup.sh
```

Success looks like:

```text
FORCE RESET → origin/master (discard local tracked changes)
Deployed: branch=master commit=472d89a   # or newer — NOT 7a72b8b
Starting master@...
Loaded 4/4 cogs
```

## Then fix the panel Startup field

Replace whatever is there (`source startup.sh` or stock `git pull`) with:

```bash
cd /home/container; rm -f startup.sh; git fetch origin master --prune || exit 1; git reset --hard HEAD || true; git checkout -f -B master origin/master; git reset --hard origin/master; export GIT_BRANCH_ACTUAL=master; echo "Deployed: branch=master commit=$(git rev-parse --short HEAD)"; exec bash /home/container/startup.sh
```

Save → Restart.

## Log cheat sheet

| You see | Means |
|---|---|
| `Updating 7a72b8b..` + stash/merge abort | Still on **old** soft-pull `startup.sh` |
| `untracked ... startup.sh` would be overwritten | Old local `startup.sh` blocking the new one — `rm -f startup.sh` first |
| `Starting unknown@7a72b8b` | Update never applied |
| `FORCE RESET → origin/master` | Good — new path |

## TTS

Voice `TimeoutError` on Ptero is host UDP. Use NAS + `network_mode: host` for TTS.
