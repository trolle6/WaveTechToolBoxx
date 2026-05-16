# Deployment

## TrueNAS (volume at `/app`)

Use `docker-compose.truenas.example.yml` as the template.

- Set `GIT_BRANCH` to the branch you deploy (e.g. `master` or your feature branch).
- Put secrets in `/mnt/.../discord-bot/config.env` and reference with `env_file`.
- First start installs `ffmpeg` + `git` once (`.truenas-deps-ready` marker).
- Startup runs `truenas-start.sh` → `docker-entrypoint.sh` → `main.py`.

Confirm in logs: `Deploy identity: ... ss_layout=split` and `Deployed: branch=... commit=...`.

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
