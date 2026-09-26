# WaveTechToolBox

Discord bot: TTS voice, DALL·E images, Secret Santa events, and file distribution.

## Quick start

1. `pip install -r requirements.txt`
2. `cp config.env.example config.env` — fill in tokens/IDs
3. `python3 check_config.py`
4. `python main.py`

Runtime JSON under `cogs/` (Secret Santa state, file metadata) is created automatically on first run.

## Requirements

- Python 3.10+ (disnake 2.12+ / DAVE voice)
- FFmpeg
- Discord bot token and OpenAI API key

## Deploy

| Method | Files |
|--------|--------|
| Docker | `Dockerfile`, `docker-entrypoint.sh` |
| TrueNAS | `docker-compose.truenas.example.yml`, `truenas-start.sh` — TTS needs `network_mode: host` |
| Pterodactyl | Frozen `startup.sh` + `PTERODACTYL_STARTUP.md` (+ optional egg JSON) |
| Bare metal | `deploy.sh` → `deploy.py` |

See `DEPLOYMENT.md` and `SECRET_SANTA_COMMANDS.md`. Privacy: `PRIVACY.md`.

## Layout

- `main.py` — entry point, config, cog loader
- `cogs/` — voice, DALL·E, Secret Santa, DistributeZip
- `cogs/archive/` — completed Secret Santa years (JSON)
