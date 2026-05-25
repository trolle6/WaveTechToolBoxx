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

## TTS dev lab (browser test, local only)

`ERR_CONNECTION_REFUSED` on http://127.0.0.1:8765/ means **the lab server is not running** (not a gitignore issue).

From repo root, **keep the terminal open**:

```bash
python start_tts_lab.py --open-browser
# or: python main.py --tts-lab
# Windows: scripts\start-tts-lab.bat
```

Requires `config.env` with `OPENAI_API_KEY`. See `dev/tts-lab/README.md`.

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
