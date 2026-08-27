# AGENTS.md

## Cursor Cloud specific instructions

WaveTechToolBox is a single Python Discord bot (disnake). There is no web UI, no
test suite, and no separate lint config — validation is done by `deploy.py`.

### Services / commands

- Run the bot (dev): `python3 main.py` (entry point; loads cogs from `cogs/`).
- Validate ("lint"/preflight): `python3 deploy.py` — checks Python/dep versions,
  required env vars, file structure, and that all slash-command descriptions are
  within Discord's 1–100 char limit. Use this as the lint/build gate.
- There are no automated unit/integration tests in this repo.

### Runtime config (required before running `deploy.py` or `main.py`)

- `config.env` (gitignored) must exist with the 5 required keys:
  `DISCORD_TOKEN`, `DISCORD_CHANNEL_ID`, `DISCORD_LOG_CHANNEL_ID`,
  `DISCORD_MODERATOR_ROLE_ID`, `OPENAI_API_KEY`. Template: `config.env.example`.
  The three `*_ID` values must be valid integer snowflakes or `Config` init fails.
- Runtime state files (gitignored) are needed and are seeded from templates:
  `cogs/secret_santa_state.json` (from `.example`) and
  `cogs/distributed_files_metadata.json` (from `.example`).
- `SKIP_API_VALIDATION=true` in `config.env` lets the bot boot without calling
  the OpenAI key-validation endpoint (useful without a real `OPENAI_API_KEY`).

### Non-obvious gotchas

- Without a real `DISCORD_TOKEN`, `main.py` runs the full startup (deps check,
  cog loading, `cog_load` hooks) and then fails at Discord login with
  "Improper token has been passed" / 401 — this is expected. To fully connect
  end-to-end you need a real `DISCORD_TOKEN` and a real `OPENAI_API_KEY`.
- `main.py` retries `bot.run()` forever with backoff on any crash (24/7 design),
  so a bad token produces a repeating login-failure loop rather than exiting.
  Stop it with SIGINT/SIGTERM (or a `timeout` wrapper when testing).
- Voice/TTS requires `ffmpeg` on PATH (already present in this environment).
- Dependencies install to the user site (`~/.local`); `~/.local/bin` is not on
  PATH by default, but the bot only needs the importable packages, not scripts.
- Python 3.10+ is required (disnake 2.12+ / DAVE voice); this VM has 3.12.
