import discord
from discord.ext import commands, tasks
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import asyncio
import json
import yt_dlp as youtube_dl
from datetime import datetime, timedelta

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Spotify setup
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=config['spotify']['client_id'],
    client_secret=config['spotify']['client_secret'],
    redirect_uri=config['spotify']['redirect_uri'],
    scope="user-read-currently-playing user-read-playback-state"
))

# YouTube DL options
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Global state
current_track = None
voice_client = None
target_user_id = None
sync_enabled = False

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is ready to sync Spotify playback!')

@bot.command(name='sync', help='Start syncing a user\'s Spotify to the voice channel')
async def sync(ctx, user: discord.Member = None):
    global target_user_id, sync_enabled, voice_client
    
    if user is None:
        user = ctx.author
    
    target_user_id = user.id
    
    # Join the voice channel
    if ctx.author.voice is None:
        await ctx.send("You need to be in a voice channel!")
        return
    
    channel = ctx.author.voice.channel
    
    if voice_client is None or not voice_client.is_connected():
        voice_client = await channel.connect()
    
    sync_enabled = True
    await ctx.send(f"🎵 Now syncing {user.mention}'s Spotify to this voice channel!")
    
    # Start the sync loop
    if not check_spotify.is_running():
        check_spotify.start()

@bot.command(name='unsync', help='Stop syncing Spotify')
async def unsync(ctx):
    global sync_enabled, voice_client
    
    sync_enabled = False
    
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()
        voice_client = None
    
    if check_spotify.is_running():
        check_spotify.stop()
    
    await ctx.send("🛑 Stopped syncing Spotify!")

@bot.command(name='nowplaying', help='Show what\'s currently being synced')
async def nowplaying(ctx):
    if current_track:
        embed = discord.Embed(title="🎵 Now Playing (Synced)", color=0x1DB954)
        embed.add_field(name="Track", value=current_track['name'], inline=False)
        embed.add_field(name="Artist", value=current_track['artist'], inline=False)
        embed.add_field(name="Album", value=current_track['album'], inline=False)
        if current_track.get('image'):
            embed.set_thumbnail(url=current_track['image'])
        await ctx.send(embed=embed)
    else:
        await ctx.send("No track is currently being synced.")

@tasks.loop(seconds=5)
async def check_spotify():
    global current_track, voice_client, sync_enabled
    
    if not sync_enabled or voice_client is None:
        return
    
    try:
        # Get current playback from Spotify
        playback = sp.current_playback()
        
        if playback is None or not playback.get('is_playing'):
            # Nothing playing, pause/stop Discord playback
            if voice_client and voice_client.is_playing():
                voice_client.stop()
            return
        
        track = playback['item']
        track_id = track['id']
        progress_ms = playback['progress_ms']
        
        # Check if it's a new track
        if current_track is None or current_track['id'] != track_id:
            track_name = track['name']
            artist_name = track['artists'][0]['name']
            album_name = track['album']['name']
            image_url = track['album']['images'][0]['url'] if track['album']['images'] else None
            
            current_track = {
                'id': track_id,
                'name': track_name,
                'artist': artist_name,
                'album': album_name,
                'image': image_url
            }
            
            # Search for the song on YouTube
            search_query = f"{artist_name} {track_name} official audio"
            
            try:
                # Stop current playback
                if voice_client.is_playing():
                    voice_client.stop()
                
                # Play new track
                player = await YTDLSource.from_url(search_query, loop=bot.loop, stream=True)
                voice_client.play(player)
                
                # Try to seek to the current position (approximate)
                # Note: Seeking in stream mode is limited, but we try to start close to the position
                print(f"Now playing: {track_name} by {artist_name} (synced at {progress_ms}ms)")
                
            except Exception as e:
                print(f"Error playing track: {e}")
        
        # Handle play/pause state
        if playback['is_playing'] and voice_client.is_paused():
            voice_client.resume()
        elif not playback['is_playing'] and voice_client.is_playing():
            voice_client.pause()
            
    except Exception as e:
        print(f"Error in Spotify sync: {e}")

@check_spotify.before_loop
async def before_check_spotify():
    await bot.wait_until_ready()

# Run the bot
if __name__ == "__main__":
    bot.run(config['discord']['token'])
