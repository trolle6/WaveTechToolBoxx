# Pterodactyl startup (always overwrite → origin/master)

Same idea as TrueNAS: fetch master, **hard reset** so local edits/old code cannot stick,
install deps, then `exec` Python as PID 1.

**Secrets stay out of this command** — put `DISCORD_TOKEN`, `OPENAI_API_KEY`, channel IDs,
etc. in the panel **Environment** tab and/or `config.env` in `/home/container` (same as NAS).

## Startup command (paste into Pterodactyl)

```bash
set -euo pipefail
cd /home/container

GIT_BRANCH="${GIT_BRANCH:-master}"
GIT_REMOTE="${GIT_REMOTE:-origin}"

if [ -d .git ]; then
  echo "Updating ${GIT_REMOTE}/${GIT_BRANCH} (hard reset — local changes discarded)..."
  git fetch "${GIT_REMOTE}" "${GIT_BRANCH}" --prune
  git checkout -B "${GIT_BRANCH}" "${GIT_REMOTE}/${GIT_BRANCH}"
  git reset --hard "${GIT_REMOTE}/${GIT_BRANCH}"
  git clean -fd
  echo "Deployed: branch=$(git rev-parse --abbrev-ref HEAD) commit=$(git rev-parse --short HEAD)"
else
  echo "WARN: no .git directory — using files already on disk"
fi

if [ -f requirements.txt ]; then
  pip install -U --prefix .local -r requirements.txt
fi

export PATH="/home/container/.local/bin:${PATH:-}"
for d in /home/container/.local/lib/python*/site-packages; do
  [ -d "$d" ] && export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"
done

# Optional: override in panel env; 30 is enough. Do not use 120 on broken UDP hosts.
export VOICE_TIMEOUT="${VOICE_TIMEOUT:-30}"

exec python /home/container/main.py
```

If your egg’s Python is not on `PATH` as `python`, use the full path instead, e.g.:

```bash
exec /usr/local/bin/python /home/container/main.py
```

## Panel environment (not in the startup script)

Keep these in **Environment** / `config.env` like TrueNAS:

- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`
- `DISCORD_LOG_CHANNEL_ID`
- `OPENAI_API_KEY`
- `DISCORD_MODERATOR_ROLE_ID` (optional)
- `GIT_BRANCH=master` (optional; default above is already master)
- `VOICE_TIMEOUT=30` (optional)

## After each restart you should see

```
Updating origin/master (hard reset — local changes discarded)...
Deployed: branch=master commit=<short sha>
```

If that line never appears, the startup command was not saved/applied.

## TTS reality check

Hard-reset to master guarantees you run **whatever is on GitHub master**.
Voice UDP still has to work on the host. NAS + `network_mode: host` already works;
Pterodactyl often cannot complete Discord voice UDP even with correct code.
