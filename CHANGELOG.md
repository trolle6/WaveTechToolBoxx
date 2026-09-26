# Changelog

## 2026-09 — Quiet console + TTS fix

- Console: removed no-op daily maintenance / “Bot Online” / cog-loaded Discord spam; lifecycle noise → DEBUG
- Kept real signals: login, deploy identity, voice connect, UDP hang warnings, scheduled SS, errors
- TTS: `network_mode: host` on TrueNAS; optional `TTS_CHANNEL_ID`; fail-fast voice UDP timeouts
- Optional `DISCORD_MODERATOR_ROLE_ID`; slash error handler; soft git-fetch; member-leave alerts
- Removed AI fluff docs (NETWORK_*, TTS_FIX_GUIDE, QUICK_START, etc.) and unused `dns_fix.py`
- Pterodactyl: hard-reset startup + `PYTHONPATH` for `.local` (stock egg `git pull` is not enough); `pterodactyl-egg.wavetech.json`

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start`; `/ss status`, `/ss oversight`; split Secret Santa modules
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`

## Earlier history

See git history before `69fb076`.
