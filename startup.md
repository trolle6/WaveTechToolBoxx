# Pterodactyl

## Startup command (paste into panel → Startup)

```
if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; if [[ ! -z "${PY_PACKAGES}" ]]; then pip install -U --prefix .local ${PY_PACKAGES}; fi; if [[ -f /home/container/${REQUIREMENTS_FILE} ]]; then pip install -U --prefix .local -r ${REQUIREMENTS_FILE}; fi; /usr/local/bin/python /home/container/${PY_FILE}
```

Same line lives in `startup.sh` if the egg uses `bash /home/container/startup.sh`.

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
