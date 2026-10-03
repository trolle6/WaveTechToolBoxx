# Pterodactyl

## Startup command (panel → Startup)

```
bash /home/container/startup.sh
```

(`source startup.sh` also works.)

**Normal start (`AUTO_UPDATE=1`, nuke OFF): safe.** The code is only fast-forwarded to GitHub, and nothing on the server is ever overwritten or deleted. Server edits in files the update doesn't touch stay as they are. If a server edit clashes with the update, the update is skipped and the log names the file:

```
[update] WARNING: update to origin/master SKIPPED; nothing on the server was changed.
[update]   blocked by: main.py
```

**`GIT_HARD_RESET_NUKE` ON (rare cases only):** the code is forced to match GitHub. Edited tracked files are backed up to `git stash list` first. `config.env`, bot data files and `cogs/archive/` are kept. Turn it OFF again after that boot.

Each boot logs `[update] Updated master: old -> new` or `[update] Running master at <sha>`, and the bot logs `Starting unknown@<sha>`.

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
| `GIT_HARD_RESET_NUKE` | OFF normally; ON for one boot to force-sync the code |
