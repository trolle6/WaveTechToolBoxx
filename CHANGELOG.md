# Changelog

## 2026-09 — Quiet console + TTS fix

- Console: removed no-op daily maintenance / “Bot Online” / cog-loaded Discord spam; lifecycle noise → DEBUG
- Kept real signals: login, deploy identity, voice connect, UDP hang warnings, scheduled SS, errors
- TTS: `network_mode: host` on TrueNAS; optional `TTS_CHANNEL_ID`; fail-fast voice UDP timeouts
- Optional `DISCORD_MODERATOR_ROLE_ID`; slash error handler; soft git-fetch; member-leave alerts
- Removed AI fluff docs (NETWORK_*, TTS_FIX_GUIDE, QUICK_START, etc.) and unused `dns_fix.py`
- Pterodactyl: hard-reset startup + `PYTHONPATH` for `.local` (stock egg `git pull` is not enough); `pterodactyl-egg.wavetech.json`
- Pterodactyl: FORCE RESET startup (no AUTO_UPDATE gate) + `ptero-start.sh`; stale-deploy critical if mixed old cogs
- Pterodactyl: discard dirty local tracked files (`checkout -f` / `reset --hard`) so “commit or stash before merge” cannot block `origin/master`
- Pterodactyl: repo `startup.sh` force-resets to `origin/master` (old local soft-`git pull` startup.sh was the abort)
- Pterodactyl: panel/bootstrap **`rm -f startup.sh`** before fetch — old untracked soft-pull script was blocking its own replacement

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start`; `/ss status`, `/ss oversight`; split Secret Santa modules
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`

## Earlier history

See git history before `69fb076`.
