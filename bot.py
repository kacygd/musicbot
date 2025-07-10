import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
from collections import deque
from aiohttp import web

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = '!'

# Set up bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Remove default help command
bot.remove_command('help')

# YTDL options for audio streaming
ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': False,  # Enable playlist support
    'nocheckcertificate': True,  # Bypass SSL verification as a fallback
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# Queue to store songs
song_queue = deque()

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            return [cls(discord.FFmpegPCMAudio(entry['url'], **ffmpeg_options), data=entry) for entry in data['entries']]
        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data)

def play_next(ctx):
    if song_queue:
        player = song_queue.popleft()
        ctx.voice_client.play(player, after=lambda e: play_next(ctx) if not e else print(f'Player error: {e}'))
        asyncio.run_coroutine_threadsafe(ctx.send(f'Now playing: {player.title}'), bot.loop)
    else:
        asyncio.run_coroutine_threadsafe(ctx.send("Queue is empty!"), bot.loop)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

@bot.command(name='play', help='Plays a song or playlist from YouTube')
async def play(ctx, *, url):
    if not ctx.message.author.voice:
        await ctx.send("You need to be in a voice channel to play music!")
        return

    channel = ctx.message.author.voice.channel
    if not ctx.voice_client:
        await channel.connect()

    async with ctx.typing():
        try:
            players = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            if isinstance(players, list):  # Playlist
                for player in players:
                    song_queue.append(player)
                await ctx.send(f'Added {len(players)} songs from playlist to the queue.')
            else:  # Single song
                song_queue.append(players)
                await ctx.send(f'Added {players.title} to the queue.')

            if not ctx.voice_client.is_playing():
                play_next(ctx)
        except Exception as e:
            await ctx.send(f'Error: {str(e)}')

@bot.command(name='volume', help='Sets the volume (0-100)')
async def volume(ctx, volume: int):
    if not ctx.voice_client:
        await ctx.send("I'm not in a voice channel!")
        return
    if not 0 <= volume <= 100:
        await ctx.send("Volume must be between 0 and 100!")
        return
    ctx.voice_client.source.volume = volume / 100
    await ctx.send(f"Set volume to {volume}%")

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused the music.")
    else:
        await ctx.send("No music is playing!")

@bot.command(name='resume', help='Resumes the paused song')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed the music.")
    else:
        await ctx.send("Music is not paused!")

@bot.command(name='stop', help='Stops the music and clears the queue')
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        song_queue.clear()
        await ctx.send("Stopped the music and cleared the queue.")
    else:
        await ctx.send("No music is playing!")

@bot.command(name='leave', help='Leaves the voice channel')
async def leave(ctx):
    if ctx.voice_client:
        song_queue.clear()
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected from the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel!")

@bot.command(name='help', help='Shows this help message')
async def help_command(ctx):
    embed = discord.Embed(title="Music Bot Commands", color=discord.Color.blue())
    for command in bot.commands:
        embed.add_field(name=f"{PREFIX}{command.name}", value=command.help, inline=False)
    await ctx.send(embed=embed)

# Web server for UptimeRobot
async def handle_request(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Web server started on port 8080")

# Start both bot and web server
async def main():
    await asyncio.gather(bot.start(TOKEN), start_web_server())

if __name__ == "__main__":
    asyncio.run(main())
