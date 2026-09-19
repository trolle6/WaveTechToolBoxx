# WaveTechToolBox

Discord bot: TTS voice, DALL·E images, Secret Santa events, and file distribution.

## Quick start

1. **Install dependencies:** `pip install -r requirements.txt`
2. **Create config:** `cp config.env.example config.env` and fill in tokens/IDs
3. **Copy runtime templates** if missing:
   - `cogs/secret_santa_state.json.example` → `cogs/secret_santa_state.json`
   - `cogs/distributed_files_metadata.json.example` → `cogs/distributed_files_metadata.json`
4. **Validate setup:** `python3 check_config.py`
5. **Check deployment:** `python deploy.py`
6. **Run bot:** `python main.py`

> 🚨 **TTS not working?** See [QUICK_START.md](QUICK_START.md) for troubleshooting!

## Requirements

- Python 3.10+ (disnake 2.12+ requires 3.10 for DAVE voice)
- FFmpeg (for TTS audio processing)
- Discord bot token and OpenAI API key
- **DAVE voice encryption** (automatically installed with disnake[voice])

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
