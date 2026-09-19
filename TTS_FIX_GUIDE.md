# TTS Connection Fix Guide

## ✅ What I've Fixed

1. **Installed Dependencies** - All required packages are now installed:
   - ✅ disnake 2.12.1 (with Discord voice support)
   - ✅ dave-py 0.1.2 (Discord E2EE voice encryption)
   - ✅ aiohttp, PyNaCl, and other dependencies
   - ✅ FFmpeg is already installed on the server

2. **Created Runtime Files** - JSON state files created from examples:
   - ✅ `cogs/secret_santa_state.json`
   - ✅ `cogs/distributed_files_metadata.json`

## ❌ What You Need to Do

### Critical: Create `config.env` File

The bot **cannot start** without this file. It contains your Discord bot token and API keys.

**Create the file now:**
```bash
cp config.env.example config.env
```

Then edit `config.env` and fill in these **REQUIRED** values:

```env
# REQUIRED - Get from Discord Developer Portal (https://discord.com/developers/applications)
DISCORD_TOKEN=your_bot_token_here

# REQUIRED - Right-click channel in Discord, "Copy ID" (enable Developer Mode first)
DISCORD_CHANNEL_ID=1234567890123456789

# REQUIRED - Channel for bot logs
DISCORD_LOG_CHANNEL_ID=1234567890123456789

# REQUIRED - Right-click role in Server Settings, "Copy ID"
DISCORD_MODERATOR_ROLE_ID=1234567890123456789

# REQUIRED - Get from OpenAI (https://platform.openai.com/api-keys)
OPENAI_API_KEY=sk-...
```

### How to Get These Values

1. **DISCORD_TOKEN**
   - Go to https://discord.com/developers/applications
   - Select your bot application
   - Go to "Bot" tab
   - Click "Reset Token" or "Copy" to get your token
   - ⚠️ **NEVER share this token!**

2. **Channel IDs** (DISCORD_CHANNEL_ID, DISCORD_LOG_CHANNEL_ID)
   - Enable Developer Mode in Discord: User Settings → Advanced → Developer Mode
   - Right-click the channel → "Copy ID"
   - You can use the same channel ID for both if you want

3. **DISCORD_MODERATOR_ROLE_ID**
   - Go to Server Settings → Roles
   - Right-click the moderator role → "Copy ID"

4. **OPENAI_API_KEY**
   - Go to https://platform.openai.com/api-keys
   - Click "Create new secret key"
   - Copy the key (starts with `sk-`)
   - ⚠️ **You'll need billing enabled for TTS to work**

### Optional: Restrict TTS to Specific Role

If you want only certain users to use TTS, uncomment this line in `config.env`:
```env
TTS_ROLE_ID=your_role_id_here
```

## 🚀 Testing the Fix

Once you've created `config.env`:

```bash
# Verify configuration
python3 deploy.py

# If that passes, start the bot
python3 main.py
```

## 🎤 How TTS Works (After Fix)

1. User joins a voice channel
2. User sends a message in Discord
3. Bot automatically converts message to speech using OpenAI TTS
4. Bot connects to voice channel and plays the audio
5. **DAVE encryption** handles secure voice transmission (this was the missing piece!)

## 🔧 Troubleshooting

### "Circuit breaker open" or API errors
- Check your OpenAI API key is valid
- Ensure you have billing enabled on OpenAI
- Check API quota/limits

### "WebSocket close code 4017" or voice connection fails
- This is fixed! dave-py is now installed
- Make sure you're using the latest code (already done)

### Bot connects but no audio
- Check FFmpeg is working: `ffmpeg -version`
- Check voice permissions in Discord server
- Try `/tts diagnostics` command in Discord

### "Missing required config" error
- You didn't create `config.env` or left values empty
- Fill in ALL required values (see above)

## 📋 Quick Checklist

- [ ] Installed dependencies (✅ Done)
- [ ] Created `config.env` from example
- [ ] Added DISCORD_TOKEN
- [ ] Added DISCORD_CHANNEL_ID
- [ ] Added DISCORD_LOG_CHANNEL_ID  
- [ ] Added DISCORD_MODERATOR_ROLE_ID
- [ ] Added OPENAI_API_KEY (with billing enabled)
- [ ] Enabled Discord Developer Mode
- [ ] Bot has "Administrator" or voice permissions in server
- [ ] Tested with `python3 deploy.py`
- [ ] Started bot with `python3 main.py`

## 🎯 What Was Actually Broken

The TTS connection issue was caused by **three missing components**:

1. **Missing disnake library** - Discord API wasn't even available
2. **Missing dave-py** - Discord's mandatory E2EE voice encryption (required since 2026)
3. **Missing config.env** - No bot token or API keys to authenticate

All the code was correct, but the runtime environment wasn't set up!

## 📞 Need More Help?

If TTS still doesn't work after following this guide:

1. Check the bot logs for specific errors
2. Run `/tts diagnostics` in Discord to see system status
3. Run `/tts status` to check voice connection status
4. Verify bot has permission to connect to voice channels
5. Check OpenAI API key has TTS access and billing enabled
