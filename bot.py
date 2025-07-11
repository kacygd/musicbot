import discord
from discord.ext import commands
import wavelink
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

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    # Kết nối với node Lavalink công khai
    await wavelink.Pool.connect(
        client=bot,
        nodes=[
            wavelink.Node(
                uri='wss://lava-v3.ajieblogs.eu.org:443',
                password='https://dsc.gg/ajidevserver',
                secure=True
            )
        ]
    )

@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    print(f"Lavalink node {node.identifier} ready!")

async def play_next(ctx):
    if song_queue:
        track = song_queue.popleft()
        player = ctx.voice_client
        await player.play(track)
        await ctx.send(f'Now playing: {track.title}')
    else:
        await ctx.send("Queue is empty!")

@bot.command(name='play', help='Phát nhạc từ YouTube')
async def play(ctx, *, query):
    if not ctx.message.author.voice:
        await ctx.send("Bạn cần vào voice channel để phát nhạc!")
        return

    channel = ctx.message.author.voice.channel
    if not ctx.voice_client:
        player = await channel.connect(cls=wavelink.Player)
    else:
        player = ctx.voice_client

    async with ctx.typing():
        try:
            tracks = await wavelink.YouTubeTrack.search(query, return_first=True)
            if not tracks:
                await ctx.send("Không tìm thấy bài hát!")
                return
            song_queue.append(tracks)
            await ctx.send(f'Added {tracks.title} to the queue.')
            if not player.playing:
                await play_next(ctx)
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
    player = ctx.voice_client
    await player.set_volume(volume)
    await ctx.send(f"Volume đặt thành {volume}%")

@bot.command(name='pause', help='Tạm dừng nhạc')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.playing:
        await ctx.voice_client.pause()
        await ctx.send("Đã tạm dừng nhạc.")
    else:
        await ctx.send("Không có nhạc đang phát!")

@bot.command(name='resume', help='Tiếp tục nhạc')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.paused:
        await ctx.voice_client.resume()
        await ctx.send("Đã tiếp tục nhạc.")
    else:
        await ctx.send("Nhạc không bị tạm dừng!")

@bot.command(name='stop', help='Dừng nhạc và xóa queue')
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.stop()
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

# Chạy bot và web server
async def main():
    await asyncio.gather(bot.start(TOKEN), start_web_server())

if __name__ == "__main__":
    asyncio.run(main())
