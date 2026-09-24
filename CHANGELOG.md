# Changelog

## 2026-09 — TTS always-fail fix + Odysseus hardening

- **TTS:** restore `network_mode: host` in TrueNAS compose (was never on master — Discord
  voice UDP fails 100% of the time on Docker bridge while slash commands still work)
- **TTS:** stop gating speech on `DISCORD_CHANNEL_ID`; optional `TTS_CHANNEL_ID` instead
- **TTS:** detect Docker bridge at startup and log CRITICAL; clearer connect-timeout errors
- **TTS:** stale-client cleanup after timeout; thread-safe playback done callback
- Optional `DISCORD_MODERATOR_ROLE_ID`; guild owner always passes mod checks
- Global slash error handler; corrupt JSON → `*.corrupt`; soft git-fetch; member-leave alerts

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start` params; added `/ss status`, `/ss oversight`, archive subgroup
- Top-level `/distribute` (participants upload; mods remove)
- Split Secret Santa into `secret_santa_core` + `secret_santa_commands`
- Role on reaction; mod checks via `DISCORD_MODERATOR_ROLE_ID` (removed bot-owner list)
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`
- Removed legacy deploy scripts, empty JSON stubs, runtime state from git

## Earlier history

See git history before `69fb076` for voice/TTS, DALL·E, archive format, and wishlist timeout fixes.
