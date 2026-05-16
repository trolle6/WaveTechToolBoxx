# Changelog

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start` params; added `/ss status`, `/ss oversight`, archive subgroup
- Top-level `/distribute` (participants upload; mods remove)
- Split Secret Santa into `secret_santa_core` + `secret_santa_commands`
- Role on reaction; mod checks via `DISCORD_MODERATOR_ROLE_ID` (removed bot-owner list)
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`
- Removed legacy deploy scripts, empty JSON stubs, runtime state from git

## Earlier history

See git history before `69fb076` for voice/TTS, DALL·E, archive format, and wishlist timeout fixes.
