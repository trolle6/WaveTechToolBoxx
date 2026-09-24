# Pterodactyl startup command (pulls TTS fix branch)

Paste this as the **Startup Command** in the Pterodactyl egg / server settings.
It checks out the fix branch (not a silent `git pull` of whatever was left on disk),
installs deps into `.local` with PYTHONPATH set, and starts the bot.

```bash
set -e
cd /home/container
if [ -d .git ]; then
  git fetch origin cursor/odysseus-hardening-adapt-715b
  git checkout -B cursor/odysseus-hardening-adapt-715b origin/cursor/odysseus-hardening-adapt-715b
  git reset --hard origin/cursor/odysseus-hardening-adapt-715b
  echo "Deployed: $(git rev-parse --short HEAD) $(git rev-parse --abbrev-ref HEAD)"
fi
pip install -U --prefix .local -r requirements.txt
export PATH="/home/container/.local/bin:${PATH}"
# Pick the site-packages dir that matches this egg's Python (3.11 / 3.12 / 3.13)
for d in /home/container/.local/lib/python*/site-packages; do
  [ -d "$d" ] && export PYTHONPATH="${d}${PYTHONPATH:+:$PYTHONPATH}"
done
# Do NOT use 120s — on a broken UDP path it only hangs longer. Prefer 30.
export VOICE_TIMEOUT="${VOICE_TIMEOUT:-30}"
exec /usr/local/bin/python /home/container/main.py
```

## How to know the new code actually loaded

Within ~5–10 seconds of `Attempting voice connection` you MUST see:

```
Still waiting for Discord voice UDP handshake to 'WaveTech A' (5s / 30s, attempt 1/4)...
```

Then after one timeout (~35s), a **CRITICAL** line ending retries.

If you only see `Attempting voice connection … timeout: 120s` and nothing else for minutes,
you are still on **old master** — the startup command did not check out this branch.

## Important

Even with this branch, **Pterodactyl will still fail to join voice** if the host blocks Discord UDP.
You will get a fast, loud failure instead of a 5–8 minute silent hang.
For working TTS, run on the NAS with `network_mode: host` (that path already works).

After PR #30 merges to master, change the branch name above to `master`.
