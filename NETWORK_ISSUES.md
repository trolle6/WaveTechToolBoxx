# Network Connection Issues - Hetzner Server Troubleshooting

## Problem: Frequent Discord Disconnections

Your logs show **17 disconnects in a few hours with only 41% uptime**. This is a **network/infrastructure issue**, not a code problem.

### Your Current Situation

```
2026-09-19 11:48:46 - bot - INFO - ⚠️ Bot disconnected (#1 in 24h, uptime: 31.4m)
2026-09-19 11:49:02 - bot - INFO - ⚠️ Bot disconnected (#2 in 24h, 16.0s since last, uptime: 31.7m)
...
2026-09-19 16:48:48 - bot - WARNING - 🚨 HIGH DISCONNECTION RATE: 17 disconnects in 24h (uptime: 41.0%)
2026-09-19 18:47:34 - bot.voice - WARNING - Voice connection error: 
```

**Root Cause:** Network instability between your Hetzner server and Discord's servers.

**Result:** The bot can't maintain stable connections, so voice connections fail.

## Immediate Diagnostics

### 1. Check Current Connection Health

Run this command in Discord (when the bot is online):
```
/tts diagnostics
```

This will show:
- Discord gateway latency
- Disconnect count in last 24h
- Current/longest uptime
- Connection health status

### 2. Test Network from Server

SSH into your Hetzner server and run:

```bash
# Test basic connectivity to Discord
ping -c 10 discord.com

# Check if UDP ports are being blocked (voice uses UDP)
nc -vzu gateway.discord.gg 443

# Test DNS resolution
nslookup discord.com

# Check route to Discord
traceroute discord.com
```

### 3. Check for Network Issues

```bash
# Check server network statistics
netstat -s | grep -i error

# Check if packets are being dropped
ip -s link

# Monitor live connections
watch -n 1 'ss -s'
```

## Common Causes & Fixes

### 1. **Pterodactyl Network Configuration** 

Your logs show you're using Pterodactyl panel. Check:

**A. Network Mode:**
```bash
# Check Docker network mode
docker inspect <container_id> | grep NetworkMode
```

- **Bridge mode issues:** Can cause connection instability
- **Host mode:** Often more stable for bots
- **Fix:** Change network mode in Pterodactyl panel settings

**B. Container Resource Limits:**
```bash
# Check if container is being throttled
docker stats <container_id>
```

- Low memory/CPU limits can cause disconnects
- **Fix:** Increase memory limit to at least 512MB

### 2. **Hetzner Firewall Rules**

Check if Hetzner's firewall is blocking Discord:

**A. Hetzner Cloud Firewall:**
1. Go to Hetzner Cloud Console
2. Check Firewall rules for your server
3. Ensure these are allowed:
   - **Outbound HTTPS (443):** Discord Gateway
   - **Outbound UDP 50000-65535:** Discord Voice
   - **Outbound DNS (53):** Name resolution

**B. Server-Level Firewall (ufw/iptables):**
```bash
# Check firewall status
sudo ufw status
# or
sudo iptables -L -n

# Allow Discord voice ports (if blocked)
sudo ufw allow out 50000:65535/udp
sudo ufw allow out 443/tcp
```

### 3. **DNS Issues**

Discord disconnects often caused by DNS resolution failures:

**Fix A: Use Better DNS Servers**

Edit `/etc/resolv.conf` (or use netplan/systemd-resolved):
```bash
nameserver 1.1.1.1      # Cloudflare
nameserver 8.8.8.8      # Google
nameserver 8.8.4.4      # Google backup
```

**Fix B: For Docker/Pterodactyl:**

Edit Docker daemon config `/etc/docker/daemon.json`:
```json
{
  "dns": ["1.1.1.1", "8.8.8.8"]
}
```

Then restart Docker:
```bash
sudo systemctl restart docker
```

### 4. **Network MTU Issues**

Wrong MTU size can cause packet fragmentation and disconnects:

```bash
# Check current MTU
ip link show

# Test optimal MTU to Discord
ping -M do -s 1472 discord.com

# If that fails, try smaller:
ping -M do -s 1400 discord.com
```

**Fix:** Set MTU in container or adjust server MTU:
```bash
# For Docker
docker network inspect bridge | grep Mtu

# To change Docker default MTU
# Edit /etc/docker/daemon.json
{
  "mtu": 1400
}
```

### 5. **Hetzner DDoS Protection**

Hetzner's automatic DDoS protection can interfere with Discord:

**Symptoms:**
- Frequent short disconnects (10-30 seconds)
- Happens in bursts
- Gateway latency spikes

**Check:**
1. Log into Hetzner Cloud/Robot Console
2. Check "DDoS Protection" status
3. Look for recent events/triggers

**Fix:**
- Contact Hetzner support to whitelist Discord IPs
- Adjust DDoS sensitivity settings
- Consider moving to Hetzner with less aggressive DDoS filtering

### 6. **Bot Token / Discord API Issues**

Sometimes Discord revokes/resets tokens:

**Check:**
1. Go to Discord Developer Portal
2. Verify bot token hasn't been reset
3. Check bot permissions in server
4. Verify bot hasn't been rate limited

**Fix:** Regenerate token and update `config.env` if needed

### 7. **Python/Discord Library Issues**

Ensure you're using compatible versions:

```bash
# Check versions
python3 --version  # Should be 3.10+
pip list | grep disnake  # Should be 2.12+
pip list | grep dave-py  # Should be 0.1.2+
```

**Fix:** Update if needed:
```bash
pip install --upgrade disnake[voice]
```

## Recommended Fixes for Your Situation

Based on your logs, try these in order:

### Priority 1: Fix DNS Resolution

```bash
# Edit Docker DNS (for Pterodactyl)
sudo nano /etc/docker/daemon.json
```

Add:
```json
{
  "dns": ["1.1.1.1", "8.8.8.8"],
  "mtu": 1400
}
```

Restart:
```bash
sudo systemctl restart docker
# Restart your Pterodactyl container
```

### Priority 2: Check Firewall

```bash
# Allow Discord voice ports
sudo ufw allow out 50000:65535/udp comment 'Discord Voice'
sudo ufw allow out 443/tcp comment 'Discord Gateway'
```

### Priority 3: Increase Connection Timeout

Edit `config.env` and add:
```env
VOICE_TIMEOUT=30
```

This gives voice connections more time to establish on unstable networks.

### Priority 4: Monitor Network

Install monitoring:
```bash
# Install mtr (better than ping)
sudo apt install mtr

# Monitor Discord continuously
mtr -r -c 100 discord.com
```

Look for:
- Packet loss % (should be 0%)
- High latency (should be <100ms)
- Jitter (variance in latency)

## Testing After Changes

1. Restart the bot
2. Monitor logs for 1 hour
3. Check disconnect rate:
   ```bash
   grep "disconnected" /path/to/logs | wc -l
   ```
4. Run `/tts diagnostics` in Discord
5. Expected result: <5 disconnects per hour

## Long-Term Solution

If problems persist after all fixes:

### Option A: Change Hetzner Location

Some Hetzner datacenters have better Discord connectivity:
- **Good:** Falkenstein (Germany), Helsinki (Finland)
- **Issues reported:** Ashburn (US), Singapore

### Option B: Add Connection Proxy

Use a proxy service between your server and Discord:
- Cloudflare WARP
- VPN tunnel
- Dedicated proxy

### Option C: Switch Providers

If Hetzner doesn't work:
- **Good for Discord bots:** DigitalOcean, Vultr, OVH
- **Best:** Oracle Cloud (free tier, excellent Discord connectivity)
- **Enterprise:** AWS, GCP (more expensive but ultra-stable)

## Monitoring & Prevention

### Add Network Monitoring

Install monitoring to catch issues early:

```bash
# Install Netdata (system monitoring)
bash <(curl -Ss https://my-netdata.io/kickstart.sh)

# Install speedtest
sudo apt install speedtest-cli

# Regular speed tests
speedtest-cli
```

### Bot-Level Improvements

The bot already has:
- ✅ Auto-reconnect system
- ✅ Connection health tracking
- ✅ Disconnect rate warnings

You can improve by:
1. Setting up log monitoring/alerts
2. Using `/tts diagnostics` regularly
3. Monitoring Discord status (https://discordstatus.com)

## When to Contact Support

**Contact Hetzner Support if:**
- Packet loss >5% to Discord
- Latency consistently >200ms
- DDoS protection triggering frequently
- Firewall rules look correct but still disconnecting

**Contact Discord Support if:**
- Bot works fine on other servers/networks
- Other bots also disconnect frequently
- Discord status page shows no issues but you're disconnecting

## Quick Reference

**Check bot health:**
```
/tts diagnostics  (in Discord)
```

**Check network from server:**
```bash
ping -c 100 discord.com
mtr -r discord.com
traceroute discord.com
```

**Fix DNS:**
```bash
echo "nameserver 1.1.1.1" | sudo tee /etc/resolv.conf
```

**Fix Docker DNS:**
```bash
echo '{"dns": ["1.1.1.1", "8.8.8.8"]}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

**Check firewall:**
```bash
sudo ufw status verbose
```

**Monitor disconnects:**
```bash
tail -f /path/to/logs | grep disconnect
```

---

## Summary

Your issue is **network instability**, not code. The TTS feature works perfectly - it just can't connect because the bot keeps disconnecting from Discord.

**Most likely causes (in order):**
1. DNS resolution issues
2. Pterodactyl network configuration
3. Hetzner firewall/DDoS protection
4. MTU/packet fragmentation issues

Start with Priority 1-3 fixes above and monitor for 1-2 hours. The bot should stabilize to <5 disconnects per day.
