# Pterodactyl

## Startup command (panel → Startup — paste this, do NOT use `source startup.sh`)

```
if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; if [[ ! -z "${PY_PACKAGES}" ]]; then pip install -U --prefix .local ${PY_PACKAGES}; fi; if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi; /usr/local/bin/python /home/container/${PY_FILE}
```

`startup.sh` in the repo is the same line (for eggs that run `bash /home/container/startup.sh`).

## Voice / TTS on Pterodactyl

Discord voice needs outbound UDP. Many Ptero nodes use Docker **bridge** NAT — slash commands work, voice handshake hangs. Same bot joins in ~1s on NAS with host networking.

1. In Discord: channel → Permissions → bot role → **Connect** + **Speak** allow on the VC.
2. Test: `/tts join` while you are in the VC.
3. If UDP still times out: run TTS on a host with `network_mode: host` (see `DEPLOYMENT.md`), or ask the node admin to allow outbound Discord voice UDP.

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
