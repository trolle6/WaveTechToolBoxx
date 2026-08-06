# Deployment

## TrueNAS (volume at `/app`)

Use `docker-compose.truenas.example.yml` as the template.

- Set `GIT_BRANCH` to the branch you deploy (e.g. `master` or your feature branch).
- Put secrets in `/mnt/.../discord-bot/config.env` and reference with `env_file`.
- First start installs `ffmpeg` + `git` once (`.truenas-deps-ready` marker).
- Startup runs `truenas-start.sh` → `docker-entrypoint.sh` → `main.py`.

Confirm in logs: `Deploy identity: ... ss_layout=split` and `Deployed: branch=... commit=...`.

### TTS dev lab (optional)

The Discord container does **not** start the TTS browser UI unless you enable it. Logs that only show `Starting bot...` and loaded cogs mean port **8765 is closed** — `ERR_CONNECTION_REFUSED` on your PC is expected.

1. Deploy a revision that includes `start_tts_lab.py` and `dev/tts-lab/` (e.g. merge [PR #15](https://github.com/trolle6/WaveTechToolBoxx/pull/15) or set `GIT_BRANCH` to `cursor/tts-dev-lab-6c6a` and restart).
2. In `config.env` or compose `environment`:
   - `TTS_LAB_ENABLED=true`
   - `TTS_LAB_HOST=0.0.0.0` (default in compose example)
   - `TTS_LAB_PORT=8765`
3. Publish port `8765:8765` in compose (see `docker-compose.truenas.example.yml`).
4. Open `http://<TrueNAS-IP>:8765/` from your PC — not `http://127.0.0.1:8765/` unless the lab runs on that same machine.

On startup you should see `TTS dev lab: starting at http://0.0.0.0:8765/`. If you see `start_tts_lab.py missing`, pull a newer commit.

For local-only testing (no NAS): `python start_tts_lab.py --open-browser` from the repo root.

## Docker image

```bash
docker build -t wave-bot .
docker run --env-file config.env -e GIT_BRANCH=master wave-bot
```

## Bare metal

```bash
./deploy.sh    # pip install + python deploy.py
python main.py
```

## Runtime files (not in git)

Copy examples on a fresh install:

```bash
cp cogs/secret_santa_state.json.example cogs/secret_santa_state.json
cp cogs/distributed_files_metadata.json.example cogs/distributed_files_metadata.json
```
