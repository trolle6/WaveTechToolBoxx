# Deployment

## TrueNAS (volume at `/app`)

Use `docker-compose.truenas.example.yml` as the template.

- **TTS / voice requires `network_mode: host`.** Without it, slash commands work
  but Discord voice UDP always times out. In the TrueNAS UI, enable Host Network
  if the compose field is ignored.
- Set `GIT_BRANCH` to the branch you deploy (e.g. `master` or your feature branch).
- Put secrets in `/mnt/.../discord-bot/config.env` and reference with `env_file`.
- First start installs `ffmpeg` + `git` once (`.truenas-deps-ready` marker).
- Startup runs `truenas-start.sh` → `docker-entrypoint.sh` → `main.py`.
- Prefer `VOICE_TIMEOUT=30` (or higher). `10` is too short for the voice handshake.

Confirm in logs: `Deployed: branch=... commit=...`, then `Starting ...@...` and `Loaded 4/4 cogs`.

## Pterodactyl

See `startup.md`. TTS needs NAS + host networking.

## Docker image

```bash
docker build -t wave-bot .
docker run --network host --env-file config.env -e GIT_BRANCH=master wave-bot
```

(`--network host` is required for TTS, same reason as TrueNAS.)

## Bare metal

```bash
./deploy.sh    # pip install + python deploy.py
python main.py
```

## Runtime files (not in git)

Created automatically on first bot start (no template copy needed):

- `cogs/secret_santa_state.json`
- `cogs/distributed_files_metadata.json`
- `cogs/archive/` (past Secret Santa years)
