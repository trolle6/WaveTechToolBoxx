# Pterodactyl

## Startup command (panel → Startup — paste this, do NOT use `source startup.sh`)

```
if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; if [[ ! -z "${PY_PACKAGES}" ]]; then pip install -U --prefix .local ${PY_PACKAGES}; fi; if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi; /usr/local/bin/python /home/container/${PY_FILE}
```

`startup.sh` in the repo is the same line (for eggs that run `bash /home/container/startup.sh`).

## Voice / TTS on Pterodactyl

Bot joins **whatever VC the speaking author is in**.

This host already proved Discord voice UDP works (`Connected to Public`). If **WaveTech A** (or another VC) hangs/times out and the log says Connect denied, fix that channel’s permissions — it is not a generic “Ptero can’t do UDP” failure.

1. Channel → Permissions → add the bot (or its role) → Allow **Connect** + **Speak**.
2. Or give the bot **Manage Roles** so it can self-grant a member overwrite on join.
3. Test: `/tts join` while you are in that VC.

## Variables

| Variable | Value |
|---|---|
| `AUTO_UPDATE` | `1` |
| `PY_FILE` | `main.py` |
| `REQUIREMENTS_FILE` | `requirements.txt` |
| `BRANCH` | `master` (install / clone only) |

Do **not** use `source startup.sh` with a custom script — use the Startup command above (or `bash /home/container/startup.sh` after pull has the stock file).

### Dirty tree / pull abort

In the panel File Manager console once:

```
git fetch origin master && git reset --hard origin/master
```

Then restart. (`GIT_HARD_RESET_NUKE` is unused with the stock Startup command.)
