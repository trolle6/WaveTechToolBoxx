# WaveTechToolBox

Discord bot: TTS voice, DALL·E images, Secret Santa events, and file distribution.

## Quick start

1. Copy `config.env.example` to `config.env` and fill in tokens/IDs.
2. Copy runtime templates if missing:
   - `cogs/secret_santa_state.json.example` → `cogs/secret_santa_state.json`
   - `cogs/distributed_files_metadata.json.example` → `cogs/distributed_files_metadata.json`
3. Install: `pip install -r requirements.txt` (or `./install_dependencies.sh`)
4. Check: `python deploy.py`
5. Run: `python main.py`

## Deploy

| Method | Files |
|--------|--------|
| Docker | `Dockerfile`, `docker-entrypoint.sh` |
| TrueNAS volume | `docker-compose.truenas.example.yml`, `truenas-start.sh` |
| Bare metal | `deploy.sh` → `deploy.py` |

See `DEPLOYMENT.md` and `SECRET_SANTA_COMMANDS.md`.

## Layout

- `main.py` — entry point, config, cog loader
- `cogs/` — voice, DALL·E, Secret Santa (split modules), DistributeZip
- `cogs/archive/` — completed Secret Santa years (JSON)
