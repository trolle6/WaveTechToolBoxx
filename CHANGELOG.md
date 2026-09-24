# Changelog

## 2026-09 — Hardening (Odysseus review, adapted)

- Optional `DISCORD_MODERATOR_ROLE_ID` (warn, don't crash); guild owner always passes mod checks
- Global slash-command error handler (ephemeral replies); cog load failures log full tracebacks
- Corrupt JSON backed up to `*.corrupt`; atomic saves create parent dirs
- `on_member_remove` alerts mods (removes pre-shuffle roster entries; never auto-reshuffles)
- Docker entrypoint: soft-fail on git fetch; Dockerfile installs `git`; `chunk_text` for long fallback posts

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start` params; added `/ss status`, `/ss oversight`, archive subgroup
- Top-level `/distribute` (participants upload; mods remove)
- Split Secret Santa into `secret_santa_core` + `secret_santa_commands`
- Role on reaction; mod checks via `DISCORD_MODERATOR_ROLE_ID` (removed bot-owner list)
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`
- Removed legacy deploy scripts, empty JSON stubs, runtime state from git

## Earlier history

See git history before `69fb076` for voice/TTS, DALL·E, archive format, and wishlist timeout fixes.
