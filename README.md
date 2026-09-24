# WaveTechToolBox

Discord bot: TTS voice, DALL·E images, Secret Santa events, and file distribution.

## Quick start

1. `pip install -r requirements.txt`
2. `cp config.env.example config.env` — fill in tokens/IDs
3. Copy runtime templates if missing:
   - `cogs/secret_santa_state.json.example` → `cogs/secret_santa_state.json`
   - `cogs/distributed_files_metadata.json.example` → `cogs/distributed_files_metadata.json`
4. `python3 check_config.py`
5. `python main.py`

## Requirements

- Python 3.10+ (disnake 2.12+ / DAVE voice)
- FFmpeg
- Discord bot token and OpenAI API key

## Deploy

| Method | Files |
|--------|--------|
| Docker | `Dockerfile`, `docker-entrypoint.sh` |
| TrueNAS | `docker-compose.truenas.example.yml`, `truenas-start.sh` — TTS needs `network_mode: host` |
| Pterodactyl | `PTERODACTYL_STARTUP.md` |
| Bare metal | `deploy.sh` → `deploy.py` |

See `DEPLOYMENT.md` and `SECRET_SANTA_COMMANDS.md`. Privacy: `PRIVACY.md`.

## Layout

- `main.py` — entry point, config, cog loader
- `cogs/` — voice, DALL·E, Secret Santa, DistributeZip
- `cogs/archive/` — completed Secret Santa years (JSON)
