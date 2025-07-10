import discord
from discord.ext import commands
import pytube
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

# Queue to store songs
song_queue = deque()

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.title
        self.url = data.watch_url

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        try:
            youtube = pytube.YouTube(url)
            stream = youtube.streams.filter(only_audio=True).first()
            if not stream:
                raise Exception("No audio stream available")
            return cls(discord.FFmpegPCMAudio(stream.url), data=youtube)
        except Exception as e:
            raise Exception(f"Failed to process video: {str(e)}")

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

@bot.command(name='play', help='Plays a song from YouTube')
async def play(ctx, *, url):
    if not ctx.message.author.voice:
        await ctx.send("You cần vào voice channel để phát nhạc!")
        return

    channel = ctx.message.author.voice.channel
    if not ctx.voice_client:
        await channel.connect()

    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
            song_queue.append(player)
            await ctx.send(f'Added {player.title} to the queue.')
            if not ctx.voice_client.is_playing():
                play_next(ctx)
        except Exception as e:
            await ctx.send(f'Error: {str(e)}')

@bot.command(name='volume', help='Đặt volume (0-100)')
async def volume(ctx, volume: int):
    if not ctx.voice_client:
        await ctx.send("Bot không ở trong voice channel!")
        return
    if not 0 <= volume <= 100:
        await ctx.send("Volume phải từ 0 đến 100!")
        return
    ctx.voice_client.source.volume = volume / 100
    await ctx.send(f"Volume đặt thành {volume}%")

@bot.command(name='pause', help='Tạm dừng nhạc')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Đã tạm dừng nhạc.")
    else:
        await ctx.send("Không có nhạc đang phát!")

@bot.command(name='resume', help='Tiếp tục nhạc')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Đã tiếp tục nhạc.")
    else:
        await ctx.send("Nhạc không bị tạm dừng!")

@bot.command(name='stop', help='Dừng nhạc và xóa queue')
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        song_queue.clear()
        await ctx.send("Đã dừng nhạc và xóa queue.")
    else:
        await ctx.send("Không có nhạc đang phát!")

@bot.command(name='leave', help='Rời voice channel')
async def leave(ctx):
    if ctx.voice_client:
        song_queue.clear()
        await ctx.voice_client.disconnect()
        await ctx.send("Đã rời voice channel.")
    else:
        await ctx.send("Bot không ở trong voice channel!")

@bot.command(name='help', help='Hiển thị danh sách lệnh')
async def help_command(ctx):
    embed = discord.Embed(title="Lệnh Music Bot", color=discord.Color.blue())
    for command in bot.commands:
        embed.add_field(name=f"{PREFIX}{command.name}", value=command.help, inline=False)
    await ctx.send(embed=embed)

# Web server cho UptimeRobot
async def handle_request(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_request)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Web server chạy trên port 8080")

# Chạy bot và web server cùng lúc
async def main():
    await asyncio.gather(bot.start(TOKEN), start_web_server())

if __name__ == "__main__":
    asyncio.run(main())
