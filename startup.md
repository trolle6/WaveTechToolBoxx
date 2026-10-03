# Pterodactyl

## Startup command (panel → Startup)

```
bash /home/container/startup.sh
```

(`source startup.sh` also works.) With `AUTO_UPDATE=1`, every start:

1. Fetches `BRANCH` (default `master`) and forces the code to match GitHub exactly.
2. Stashes code edits made on the server first, so they never block an update. Recover with `git stash list` / `git stash show -p`. **Make code changes on GitHub, not on the server.**
3. Keeps `config.env`, `cogs/archive/` (Secret Santa years) and other bot data untouched.
4. Prints `[update] Updated master: old -> new` or `[update] Already on latest master (sha)`; the bot then logs `Starting unknown@<sha>`.

## Voice / TTS on Pterodactyl

Bot joins **whatever VC the speaking author is in**.

This host already proved Discord voice UDP works (`Connected to Public`). If **WaveTech A** (or another VC) hangs/times out and the log says Connect denied, fix that channel’s permissions — it is not a generic “Ptero can’t do UDP” failure.

1. Channel → Permissions → **WaveTechTTS** (or **BOT**) → Allow **View Channel** + **Connect** + **Speak**.
   If `@everyone` denies View, Connect-only allows do nothing.
2. Or give the bot **Manage Roles** so it can self-grant View/Connect on join.
3. Test: `/tts join` while you are in that VC.

## Variables

| Variable | Value |
|---|---|
| `AUTO_UPDATE` | `1` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `BRANCH` | `master` (branch auto-update follows) |
