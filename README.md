# Spotify Discord Sync Bot

A Discord bot that reads what a specific user is listening to on Spotify and plays it in sync in a Discord voice channel!

## Features

- 🎵 Syncs Spotify playback to Discord voice channels
- 🔄 Automatically detects track changes
- ⏯️ Handles play/pause states
- 🎨 Beautiful now-playing embeds
- 👥 Can sync any user's Spotify (with their permission)

## How It Works

1. The bot connects to Spotify's API to monitor what you're currently playing
2. When a new track is detected, it searches for the song on YouTube
3. The bot plays the YouTube audio in the Discord voice channel
4. It continuously syncs play/pause states every 5 seconds

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- FFmpeg installed on your system
- A Discord Bot Token
- Spotify Developer App credentials

### Step 1: Install FFmpeg

**Linux:**
```bash
sudo apt-get install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Create Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to the "Bot" section and click "Add Bot"
4. Copy the bot token
5. Enable "Message Content Intent" and "Server Members Intent" under Privileged Gateway Intents
6. Go to OAuth2 > URL Generator
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Connect`, `Speak`, `Use Voice Activity`, `Send Messages`, `Embed Links`
9. Copy the generated URL and invite the bot to your server

### Step 4: Create Spotify App

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click "Create App"
3. Fill in the details:
   - App name: "Discord Sync Bot"
   - Redirect URI: `http://localhost:8888/callback`
4. Copy the Client ID and Client Secret

### Step 5: Configure the Bot

1. Copy `config.json.example` to `config.json`:
   ```bash
   cp config.json.example config.json
   ```

2. Edit `config.json` with your credentials:
   ```json
   {
     "discord": {
       "token": "YOUR_DISCORD_BOT_TOKEN"
     },
     "spotify": {
       "client_id": "YOUR_SPOTIFY_CLIENT_ID",
       "client_secret": "YOUR_SPOTIFY_CLIENT_SECRET",
       "redirect_uri": "http://localhost:8888/callback"
     }
   }
   ```

### Step 6: Authenticate with Spotify

When you first run the bot, you'll need to authenticate with Spotify:

1. Run the bot:
   ```bash
   python spotify_discord_bot.py
   ```

2. A browser window will open asking you to log in to Spotify
3. After logging in, you'll be redirected to localhost (this is normal)
4. The bot will cache your credentials for future use

## Usage

### Commands

- `!sync [@user]` - Start syncing Spotify playback to the voice channel
  - If no user is mentioned, syncs your own Spotify
  - You must be in a voice channel to use this command

- `!unsync` - Stop syncing and disconnect the bot

- `!nowplaying` - Show what's currently being synced

### Example Usage

1. Join a Discord voice channel
2. Type `!sync` to sync your Spotify
3. Start playing music on Spotify (any device)
4. The bot will automatically play the same song in Discord!

## Limitations & Notes

### Technical Limitations

- **Sync Accuracy**: The bot checks Spotify every 5 seconds, so there may be a slight delay
- **Seeking**: Starting position sync is approximate due to YouTube streaming limitations
- **Audio Source**: Uses YouTube as the audio source, so song availability depends on YouTube
- **Latency**: There's inherent latency between Spotify and Discord playback (~5-10 seconds)

### Important Considerations

- **Spotify Premium**: The person being synced needs Spotify Premium for the API to work properly
- **Multiple Devices**: The bot syncs whatever device the user is actively playing on
- **Privacy**: The bot can only read Spotify data from users who have authenticated with your Spotify app

## Troubleshooting

### Bot doesn't play audio
- Make sure FFmpeg is installed and in your PATH
- Check that the bot has proper voice permissions in Discord

### Spotify sync not working
- Ensure you're playing music on Spotify (Premium account)
- Check that you've authenticated with Spotify
- Verify your Spotify app credentials in config.json

### Bot can't find songs
- The bot searches YouTube, so availability depends on YouTube's library
- Try playing more popular/official releases for better results

## Advanced: Multi-User Sync

To sync another user's Spotify:
1. They need to authenticate with your Spotify app (modify the code to support multiple users)
2. Use `!sync @username` to sync their playback

## License

This is a demonstration/educational project. Be aware of:
- Discord's Terms of Service
- Spotify's Developer Terms
- YouTube's Terms of Service
- Music licensing and copyright laws

## Contributing

Feel free to fork and improve! Some ideas:
- Better seeking/sync accuracy
- Queue management
- Playlist syncing
- Multiple user support
- Web dashboard

---

**Note**: This bot is for educational purposes. Respect copyright laws and platform terms of service.
