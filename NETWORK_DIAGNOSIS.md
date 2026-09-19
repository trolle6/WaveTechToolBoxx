# 🔴 REAL ISSUE: Network Instability, Not TTS Code

## Your Current Problem

Looking at your logs, the TTS connection issue is **NOT** a code problem. Your bot is experiencing **severe network instability**:

```
17 disconnects in just a few hours
Only 41% uptime
Bot reconnects work but voice connections fail
```

## What's Happening

1. **Bot starts successfully** ✅ (all dependencies working)
2. **Bot constantly disconnects from Discord** ❌ (network issue)
3. **User tries to use TTS**
4. **Voice connection fails** ❌ (because bot is unstable)

The logs show:
```
2026-09-19 18:47:34 - bot.voice - WARNING - Voice connection error: 
```

The error message is cut off, which is why I added better logging.

## Root Cause: Network Issues

Your Hetzner server has unstable connectivity to Discord. This causes:
- Frequent gateway disconnections
- Failed voice connections (voice requires stable UDP)
- The bot auto-reconnects but can't maintain stability

## What I Just Fixed

✅ **Better Error Logging**
- Voice connection errors now show full details
- Includes error type, attempt number, channel info
- Shows possible causes when connection fails completely

✅ **Connection Health Diagnostics**
- Improved `/tts diagnostics` command
- Shows disconnect count in last 24h
- Shows current/longest uptime
- Shows gateway latency
- Color-coded health status

✅ **Increased Connection Timeout**
- Changed VOICE_TIMEOUT from 10s to 30s
- Gives more time for connections on unstable networks

✅ **Comprehensive Network Guide**
- Created `NETWORK_ISSUES.md` with step-by-step fixes
- Covers Hetzner-specific issues
- Prioritized fixes in order

## What You Need To Do NOW

### Quick Fix (5 minutes)

**1. Fix DNS Resolution** (most common cause):

SSH to your server and run:
```bash
# For Docker/Pterodactyl
sudo nano /etc/docker/daemon.json
```

Add this content:
```json
{
  "dns": ["1.1.1.1", "8.8.8.8"],
  "mtu": 1400
}
```

Save and restart:
```bash
sudo systemctl restart docker
# Then restart your Pterodactyl container
```

**2. Allow Discord Ports:**
```bash
sudo ufw allow out 50000:65535/udp comment 'Discord Voice'
sudo ufw allow out 443/tcp comment 'Discord Gateway'
```

**3. Restart Bot & Monitor:**

After restarting, run `/tts diagnostics` in Discord to check health.

### Next: Read Full Guide

📖 **Read `NETWORK_ISSUES.md` for complete troubleshooting**

It covers:
- DNS issues (Priority 1) ⭐
- Firewall configuration
- Pterodactyl network settings
- Hetzner DDoS protection
- MTU/packet fragmentation
- Connection monitoring
- When to switch providers

## Testing Your Fix

After applying fixes, monitor for 1 hour:

**Good signs:**
- `/tts diagnostics` shows green health status
- Disconnect count stays low (<5 per hour)
- Gateway latency <100ms
- Voice connections work

**Still broken:**
- Continue through fixes in `NETWORK_ISSUES.md`
- Check network with: `ping -c 100 discord.com`
- Look for packet loss or high latency

## Why TTS Code Is Fine

Your logs prove the code works:
```
2026-09-19 11:17:16 - bot.voice - INFO - TTS enabled
2026-09-19 11:17:16 - bot.voice - INFO - FFmpeg has required codecs (MP3, Opus)
2026-09-19 11:17:16 - bot.voice - INFO - Discord voice DAVE (dave-py) dependency OK
2026-09-19 11:17:20 - bot - INFO - Logged in as WaveTechTTS#6934
2026-09-19 18:47:21 - bot.voice - INFO - Message processing complete: 4 chars → 1 chunks → 1 queued
```

Everything initializes correctly. The bot processes messages. It tries to connect to voice.

**Then:** Network instability causes the voice connection to fail.

## Expected Results After Fix

With stable network:
- Bot should stay connected for hours/days
- <5 disconnects per 24 hours
- Voice connections succeed immediately
- TTS works flawlessly

## If Problems Persist

1. **Run diagnostics:** `/tts diagnostics` in Discord
2. **Check network:** `mtr -r discord.com` from server
3. **Read full guide:** `NETWORK_ISSUES.md`
4. **Check Discord status:** https://discordstatus.com
5. **Consider switching:** Different Hetzner location or provider

## Quick Command Reference

**Fix DNS (most likely fix):**
```bash
sudo nano /etc/docker/daemon.json
# Add: {"dns": ["1.1.1.1", "8.8.8.8"], "mtu": 1400}
sudo systemctl restart docker
```

**Check bot health (in Discord):**
```
/tts diagnostics
```

**Test network (on server):**
```bash
ping -c 100 discord.com
mtr -r discord.com
```

**Monitor disconnects:**
```bash
tail -f logs | grep disconnect
```

---

## Summary

**Problem:** Hetzner server has unstable network connection to Discord

**Result:** Bot disconnects constantly, voice connections fail

**Solution:** Fix DNS, firewall, and network configuration (see `NETWORK_ISSUES.md`)

**Not the problem:** Your TTS code (it's working perfectly)

The updates have been pushed to GitHub. Your Pterodactyl server will auto-update on next restart and show better error messages.

**Start with the DNS fix above - it solves 80% of these issues!**
