# Changelog

## 2026-10 — Secret Santa message redesign

- Participant DMs are now color-coded embeds: cranberry = Santa mode, pine = giftee mode, gold = event, frost = notices
- DM shortcut buttons (add/view wishlist, view giftee wishlist, ask, log gift) — persistent across restarts
- Shuffle sends the "Your giftee has been chosen!" DM, then a separate 📖 commands card; other DMs stay short and point back to it
- Removed the word "match" from all participant-facing text (now "giftee")
- Every button and form is guarded: failures get a slate private reply saying why (bug, Discord trouble, rate limit, DMs closed), which command to type instead, what did NOT happen, and a copy of anything typed
- Expired button presses (Discord's "This interaction failed") get a follow-up DM, max once per 5 minutes per user
- `/distribute` files are per year: list/browse/get show only the current year; after `/ss stop`, that year's role holders keep access for 90 days, then files are deleted (older untracked years get a fresh 90 days on first run)
- Commands in messages are clickable `</ss giftee:ID>` mentions (ids cached at startup; plain text fallback)
- Leave DM links back to the signup message; giftee DM shows the real end date instead of a hardcoded Dec 25
- Giftee reply DM shows the question it answers; wrap-up DM shows event stats and your own gift
- New one-time reminders: empty-wishlist nudge 2 days after shuffle; one-week-left nudge for Santas with no logged gift
- DM-failure fallbacks in the server channel are embeds and actually ping the affected users

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
- Pterodactyl: **`startup.sh` FROZEN** — official panel command (`git pull` + pip `.local` + python); do not modify that file

## 2026-05 — Secret Santa simplify & cleanup

- Simplified `/ss start`; `/ss status`, `/ss oversight`; split Secret Santa modules
- TrueNAS/Docker deploy: `GIT_BRANCH`, `docker-entrypoint.sh`, `truenas-start.sh`

## Earlier history

See git history before `69fb076`.
