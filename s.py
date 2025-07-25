import discord
from discord.ext import commands
import wavelink
import asyncio
import os
from dotenv import load_dotenv
from collections import deque
import http.server
import socketserver
import threading
import time
import psutil
import sys
import json

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')

song_queue = deque()
saved_volumes = {}
current_playing_message = None
loop_count = {}
loop_active = {}
loop_track = {}
auto_disconnect_task = {}
bot_start_time = time.time()
skip_locks = {}  # Per-guild locks for skipping

HTML_CONTENT = """Bot is Alive"""

class SimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode('utf-8'))

def start_http_server():
    PORT = 8000
    with socketserver.TCPServer(("", PORT), SimpleHTTPRequestHandler) as httpd:
        httpd.serve_forever()

def format_duration(length):
    seconds = length // 1000
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"

async def update_bot_status(guild_id=None, player=None):
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.playing,
        name="Use /help for commands"
    ))

def save_volumes():
    try:
        with open('volumes.json', 'w') as f:
            json.dump({str(k): v for k, v in saved_volumes.items()}, f, indent=4)
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Saved volumes to volumes.json: {saved_volumes}\n")
    except Exception as e:
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Error saving volumes.json: {e}\n")

def load_volumes():
    global saved_volumes
    try:
        with open('volumes.json', 'r') as f:
            saved_volumes = {int(k): int(v) for k, v in json.load(f).items()}
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Loaded volumes from volumes.json: {saved_volumes}\n")
    except FileNotFoundError:
        saved_volumes = {}
    except Exception as e:
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Error loading volumes.json: {e}\n")
        saved_volumes = {}

async def restart_bot_after_timeout():
    global bot_start_time
    RESTART_INTERVAL = 4 * 3600
    while True:
        elapsed_time = time.time() - bot_start_time
        if elapsed_time >= RESTART_INTERVAL:
            save_volumes()
            try:
                for guild in bot.guilds:
                    if guild.voice_client:
                        await guild.voice_client.disconnect()
                await bot.close()
            except Exception:
                pass
            os.execv(sys.executable, ['python'] + sys.argv)
        await asyncio.sleep(60)

async def auto_disconnect(guild_id, player):
    global song_queue, current_playing_message
    await asyncio.sleep(180)
    if not player or not player.channel:
        return
    if len([member for member in player.channel.members if not member.bot]) == 0:
        song_queue.clear()
        current_playing_message = None
        await player.disconnect()
        await update_bot_status(guild_id)
        embed = discord.Embed(
            title="Disconnected",
            description="Left voice channel due to no users after 3 minutes.",
            color=discord.Color.blue()
        )
        if player.text_channel:
            message = await player.text_channel.send(embed=embed)
            await asyncio.sleep(5)
            await message.delete()
    elif not player.playing and not song_queue:
        song_queue.clear()
        current_playing_message = None
        await player.disconnect()
        await update_bot_status(guild_id)
        embed = discord.Embed(
            title="Disconnected",
            description="No tracks remaining.",
            color=discord.Color.blue()
        )
        if player.text_channel:
            message = await player.text_channel.send(embed=embed)
            await asyncio.sleep(5)
            await message.delete()
    if guild_id in auto_disconnect_task:
        del auto_disconnect_task[guild_id]

class MusicButtons(discord.ui.View):
    def __init__(self, player=None):
        super().__init__(timeout=None)
        self.player = player
        if player:
            self.children[0].disabled = player.paused
            self.children[1].disabled = not player.paused
        else:
            self.children[0].disabled = True
            self.children[1].disabled = True

    async def update_embed(self, interaction, player, status):
        guild_id = interaction.guild.id
        embed = discord.Embed(
            title=f"Now {status}",
            description=f"**[{player.current.title}]({player.current.uri})**",
            color=discord.Color.green()
        )
        embed.add_field(name="Source", value=player.current.source, inline=True)
        embed.add_field(name="Volume", value=f"{saved_volumes.get(guild_id, 50)}%", inline=True)
        embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
        if hasattr(player.current, 'author'):
            embed.add_field(name="Artist", value=player.current.author, inline=True)
        if hasattr(player.current, 'thumbnail'):
            embed.set_thumbnail(url=player.current.thumbnail)
        return embed

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        if not player.playing or player.paused:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="No track is playing or already paused!", color=discord.Color.red()), ephemeral=True)
            return
        await player.pause(True)
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = await self.update_embed(interaction, player, "Paused")
                view = MusicButtons(player)
                await message.edit(embed=embed, view=view)
            except:
                embed = await self.update_embed(interaction, player, "Paused")
                view = MusicButtons(player)
                message = await interaction.channel.send(embed=embed, view=view)
                current_playing_message = message.id
        await interaction.response.send_message("Paused the current track.", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.delete_original_response()
        await update_bot_status(interaction.guild.id)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.secondary, emoji="▶️")
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        if not player.paused:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Track is not paused!", color=discord.Color.red()), ephemeral=True)
            return
        await player.pause(False)
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = await self.update_embed(interaction, player, "Playing")
                view = MusicButtons(player)
                await message.edit(embed=embed, view=view)
            except:
                embed = await self.update_embed(interaction, player, "Playing")
                view = MusicButtons(player)
                message = await interaction.channel.send(embed=embed, view=view)
                current_playing_message = message.id
        await interaction.response.send_message("Resumed the current track.", ephemeral=True)
        await asyncio.sleep(5)
        await interaction.delete_original_response()
        await update_bot_status(interaction.guild.id)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        guild_id = interaction.guild.id
        if guild_id not in skip_locks:
            skip_locks[guild_id] = asyncio.Lock()
        async with skip_locks[guild_id]:
            player = interaction.guild.voice_client
            player_id = id(player)
            await player.stop()
            if player_id in loop_count:
                del loop_count[player_id]
            if player_id in loop_active:
                del loop_active[player_id]
            if player_id in loop_track:
                del loop_track[player_id]
            await play_next(interaction.channel, amount=1)
            message = await interaction.followup.send("Skipped the current track.", ephemeral=True)
            await asyncio.sleep(5)
            await message.delete()
        await update_bot_status(interaction.guild.id)

    @discord.ui.button(label="Volume Up", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, saved_volumes
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        guild_id = interaction.guild.id
        current_volume = saved_volumes.get(guild_id, 50)
        saved_volumes[guild_id] = min(current_volume + 10, 100)
        await player.set_volume(saved_volumes[guild_id])
        save_volumes()
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = message.embeds[0]
                embed.set_field_at(1, name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                await message.edit(embed=embed, view=MusicButtons(player))
            except:
                embed = discord.Embed(
                    title=f"Now {'Paused' if player.paused else 'Playing'}",
                    description=f"**[{player.current.title}]({player.current.uri})**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Source", value=player.current.source, inline=True)
                embed.add_field(name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
                if hasattr(player.current, 'author'):
                    embed.add_field(name="Artist", value=player.current.author, inline=True)
                if hasattr(player.current, 'thumbnail'):
                    embed.set_thumbnail(url=player.current.thumbnail)
                message = await interaction.channel.send(embed=embed, view=MusicButtons(player))
                current_playing_message = message.id
        await interaction.response.defer()

    @discord.ui.button(label="Volume Down", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, saved_volumes
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        guild_id = interaction.guild.id
        current_volume = saved_volumes.get(guild_id, 50)
        saved_volumes[guild_id] = max(current_volume - 10, 0)
        await player.set_volume(saved_volumes[guild_id])
        save_volumes()
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = message.embeds[0]
                embed.set_field_at(1, name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                await message.edit(embed=embed, view=MusicButtons(player))
            except:
                embed = discord.Embed(
                    title=f"Now {'Paused' if player.paused else 'Playing'}",
                    description=f"**[{player.current.title}]({player.current.uri})**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Source", value=player.current.source, inline=True)
                embed.add_field(name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
                if hasattr(player.current, 'author'):
                    embed.add_field(name="Artist", value=player.current.author, inline=True)
                if hasattr(player.current, 'thumbnail'):
                    embed.set_thumbnail(url=player.current.thumbnail)
                message = await interaction.channel.send(embed=embed, view=MusicButtons(player))
                current_playing_message = message.id
        await interaction.response.defer()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, song_queue, auto_disconnect_task
        if interaction.guild and interaction.guild.voice_client:
            guild_id = interaction.guild.id
            player_id = id(interaction.guild.voice_client)
            if guild_id in auto_disconnect_task:
                auto_disconnect_task[guild_id].cancel()
                del auto_disconnect_task[guild_id]
            if player_id in loop_count:
                del loop_count[player_id]
            if player_id in loop_active:
                del loop_active[player_id]
            if player_id in loop_track:
                del loop_track[player_id]
            await interaction.guild.voice_client.stop()
            song_queue.clear()
            current_playing_message = None
            embed = discord.Embed(
                title="Stopped", description="Stopped music and cleared the queue.", color=discord.Color.blue())
            message = await interaction.response.send_message(embed=embed)
            await asyncio.sleep(5)
            await message.delete()
            if guild_id not in auto_disconnect_task:
                auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, interaction.guild.voice_client))
            await update_bot_status(guild_id)
        else:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="No music is playing!", color=discord.Color.red()), ephemeral=True)

class QueueView(discord.ui.View):
    def __init__(self, song_queue):
        super().__init__(timeout=60)
        self.song_queue = song_queue
        self.current_page = 1
        self.per_page = 10
        self.total_pages = max(1, (len(song_queue) + self.per_page - 1) // self.per_page)

    async def update_embed(self, interaction: discord.Interaction):
        start_idx = (self.current_page - 1) * self.per_page
        end_idx = min(start_idx + self.per_page, len(self.song_queue))
        embed = discord.Embed(title="Queue", color=discord.Color.blue())
        if not self.song_queue:
            embed.description = "The queue is currently empty!"
        else:
            for i, track in enumerate(list(self.song_queue)[start_idx:end_idx], start_idx + 1):
                embed.add_field(
                    name=f"Track {i}: [{track.title}]({track.uri})",
                    value=f"Duration: {format_duration(track.length)}",
                    inline=False
                )
        embed.set_footer(text=f"Page {self.current_page}/{self.total_pages}")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="⬅️", disabled=True)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 1:
            self.current_page -= 1
            self.previous_button.disabled = (self.current_page == 1)
            self.next_button.disabled = False
            await self.update_embed(interaction)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.previous_button.disabled = False
            self.next_button.disabled = (self.current_page == self.total_pages)
            await self.update_embed(interaction)
        else:
            await interaction.response.defer()

@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = time.time()
    load_volumes()
    try:
        await wavelink.Pool.connect(
            client=bot,
            nodes=[wavelink.Node(
                uri='wss://lava-all.ajieblogs.eu.org:443',
                password='https://dsc.gg/ajidevserver'
            )]
        )
        await bot.tree.sync()
    except Exception as e:
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Failed to connect to Lavalink or sync commands: {e}\n")
    await update_bot_status()
    threading.Thread(target=start_http_server, daemon=True).start()
    asyncio.create_task(restart_bot_after_timeout())

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    global current_playing_message, auto_disconnect_task
    player = payload.player
    channel = getattr(player, 'text_channel', None)
    guild_id = player.guild.id
    current_playing_message = None
    player_id = id(player)
    current_track = loop_track.get(player_id)

    if guild_id in skip_locks and skip_locks[guild_id].locked():
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Skip lock active, skipping automatic playback for guild {guild_id}\n")
        return  # Skip automatic playback if a skip operation is in progress

    if current_track and player_id in loop_count and loop_count[player_id] > 0 and loop_active.get(player_id, False):
        loop_count[player_id] -= 1
        try:
            await player.play(current_track)
            await update_bot_status(guild_id, player)
        except Exception as e:
            with open('bot.log', 'a') as f:
                f.write(f"{time.ctime()}: Error replaying track: {e}\n")
        return
    elif player_id in loop_count and loop_count[player_id] == 0:
        del loop_count[player_id]
        del loop_active[player_id]
        del loop_track[player_id]
        if channel:
            message = await channel.send("Loop ended")
            await asyncio.sleep(5)
            await message.delete()
        return

    if channel and song_queue:
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Playing next track from on_wavelink_track_end for guild {guild_id}, queue length: {len(song_queue)}\n")
        await play_next(channel, amount=1)
    elif channel and not song_queue:
        if guild_id not in auto_disconnect_task:
            auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, player))
        await update_bot_status(guild_id)

@bot.event
async def on_voice_state_update(member, before, after):
    global auto_disconnect_task
    if member.bot or not before.channel:
        return
    guild_id = member.guild.id
    player = member.guild.voice_client
    if not player:
        return
    if len([m for m in before.channel.members if not m.bot]) == 0:
        if guild_id not in auto_disconnect_task:
            auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, player))
    elif guild_id in auto_disconnect_task:
        auto_disconnect_task[guild_id].cancel()
        del auto_disconnect_task[guild_id]

async def play_next(channel, amount=1):
    global current_playing_message, saved_volumes, auto_disconnect_task
    if not channel.guild or not channel.guild.voice_client:
        return
    guild_id = channel.guild.id
    if guild_id not in skip_locks:
        skip_locks[guild_id] = asyncio.Lock()
    async with skip_locks[guild_id]:
        with open('bot.log', 'a') as f:
            f.write(f"{time.ctime()}: Entering play_next for guild {guild_id}, requested amount: {amount}, queue length: {len(song_queue)}\n")
        for attempt in range(5):
            try:
                if len(song_queue) < amount:
                    embed = discord.Embed(
                        title="Error",
                        description=f"Not enough tracks in queue to skip {amount}. Only {len(song_queue)} track(s) available.",
                        color=discord.Color.red()
                    )
                    message = await channel.send(embed=embed)
                    await asyncio.sleep(5)
                    await message.delete()
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Not enough tracks to skip {amount} for guild {guild_id}\n")
                    return
                # Skip the specified number of tracks
                skipped_tracks = []
                for _ in range(amount):
                    if song_queue:
                        skipped_track = song_queue.popleft()
                        skipped_tracks.append(skipped_track.title)
                with open('bot.log', 'a') as f:
                    f.write(f"{time.ctime()}: Skipped tracks {skipped_tracks} for guild {guild_id}, remaining queue: {list(song_queue)}\n")

                if song_queue:
                    track = song_queue.popleft()
                    player = channel.guild.voice_client
                    volume = saved_volumes.get(guild_id, 50)
                    await player.set_volume(volume)
                    await player.play(track)
                    if player.paused:
                        await player.pause(True)
                    embed = discord.Embed(
                        title="Now Playing",
                        description=f"**[{track.title}]({track.uri})**",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Source", value=track.source, inline=True)
                    embed.add_field(name="Volume", value=f"{volume}%", inline=True)
                    embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                    if hasattr(track, 'author'):
                        embed.add_field(name="Artist", value=track.author, inline=True)
                    if hasattr(track, 'thumbnail'):
                        embed.set_thumbnail(url=track.thumbnail)
                    message = await channel.send(embed=embed, view=MusicButtons(player))
                    current_playing_message = message.id
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Now playing {track.title} for guild {guild_id}\n")
                    await update_bot_status(channel.guild.id, player)
                    if channel.guild.id in auto_disconnect_task:
                        auto_disconnect_task[channel.guild.id].cancel()
                        del auto_disconnect_task[channel.guild.id]
                else:
                    if channel.guild.id not in auto_disconnect_task:
                        auto_disconnect_task[channel.guild.id] = asyncio.create_task(auto_disconnect(channel.guild.id, channel.guild.voice_client))
                    await update_bot_status(channel.guild.id)
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Queue empty after skip for guild {guild_id}\n")
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                else:
                    embed = discord.Embed(title="Error", description=f"Error playing track: {e}", color=discord.Color.red())
                    message = await channel.send(embed=embed)
                    await asyncio.sleep(5)
                    await message.delete()
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Error playing track: {e} for guild {guild_id}\n")
                    break
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error playing track: {e}", color=discord.Color.red())
                message = await channel.send(embed=embed)
                await asyncio.sleep(5)
                await message.delete()
                with open('bot.log', 'a') as f:
                    f.write(f"{time.ctime()}: Error playing track: {e} for guild {guild_id}\n")
                break

@bot.tree.command(name="ping", description="Check bot's latency")
async def ping_slash(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="Pong!",
        description=f"**Latency**: {latency} ms",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="play", description="Play a song or playlist by URL")
async def play_slash(interaction: discord.Interaction, query: str):
    global current_playing_message, saved_volumes, auto_disconnect_task
    await interaction.response.defer(thinking=True)

    if not interaction.user or not interaction.user.voice:
        embed = discord.Embed(
            title="Error", description="You need to be in a voice channel to play music!", color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    channel = interaction.user.voice.channel
    player = interaction.guild.voice_client
    guild_id = interaction.guild.id

    if not player:
        for attempt in range(5):
            try:
                player = await channel.connect(cls=wavelink.Player, timeout=60.0, reconnect=True)
                volume = saved_volumes.get(guild_id, 50)
                await player.set_volume(volume)
                player.text_channel = interaction.channel
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(5 * (attempt + 1))
                else:
                    embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Failed to connect to voice channel: {e}\n")
                    return
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed, ephemeral=True)
                with open('bot.log', 'a') as f:
                    f.write(f"{time.ctime()}: Unexpected error during voice channel connection: {e}\n")
                return

    async with interaction.channel.typing():
        for attempt in range(5):
            try:
                if not query.startswith(('http://', 'https://')):
                    embed = discord.Embed(
                        title="Error", description="Please provide a valid URL (e.g., YouTube or Spotify link)!", color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    return

                tracks = await wavelink.Playable.search(query)
                if not tracks:
                    embed = discord.Embed(
                        title="Error", description="No song or playlist found from the link!", color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    return

                if isinstance(tracks, wavelink.Playlist):
                    for track in tracks.tracks:
                        song_queue.append(track)
                    embed = discord.Embed(
                        title="Added Playlist",
                        description=f"Added {len(tracks.tracks)} tracks from playlist '{tracks.name}' to the queue",
                        color=discord.Color.blue()
                    )
                    if not player.playing and song_queue:
                        track = song_queue.popleft()
                        await player.play(track)
                        embed = discord.Embed(
                            title="Now Playing",
                            description=f"**[{track.title}]({track.uri})**",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Source", value=track.source, inline=True)
                        embed.add_field(name="Volume", value=f"{saved_volumes.get(guild_id, 50)}%", inline=True)
                        embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                        if hasattr(track, 'author'):
                            embed.add_field(name="Artist", value=track.author, inline=True)
                        if hasattr(track, 'thumbnail'):
                            embed.set_thumbnail(url=track.thumbnail)
                        message = await interaction.followup.send(embed=embed, view=MusicButtons(player))
                        current_playing_message = message.id
                        await update_bot_status(interaction.guild.id)
                        if interaction.guild.id in auto_disconnect_task:
                            auto_disconnect_task[interaction.guild.id].cancel()
                            del auto_disconnect_task[interaction.guild.id]
                    else:
                        await interaction.followup.send(embed=embed)
                else:
                    track = tracks[0]
                    song_queue.append(track)
                    if not player.playing:
                        track = song_queue.popleft()
                        await player.play(track)
                        embed = discord.Embed(
                            title="Now Playing",
                            description=f"**[{track.title}]({track.uri})**",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Source", value=track.source, inline=True)
                        embed.add_field(name="Volume", value=f"{saved_volumes.get(guild_id, 50)}%", inline=True)
                        embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                        if hasattr(track, 'author'):
                            embed.add_field(name="Artist", value=track.author, inline=True)
                        if hasattr(track, 'thumbnail'):
                            embed.set_thumbnail(url=track.thumbnail)
                        message = await interaction.followup.send(embed=embed, view=MusicButtons(player))
                        current_playing_message = message.id
                        await update_bot_status(interaction.guild.id)
                        if interaction.guild.id in auto_disconnect_task:
                            auto_disconnect_task[interaction.guild.id].cancel()
                            del auto_disconnect_task[interaction.guild.id]
                    else:
                        embed = discord.Embed(
                            title="Added to Queue",
                            description=f"**[{track.title}]({track.uri})**",
                            color=discord.Color.blue()
                        )
                        embed.add_field(name="Source", value=track.source, inline=True)
                        embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                        if hasattr(track, 'author'):
                            embed.add_field(name="Artist", value=track.author, inline=True)
                        await interaction.followup.send(embed=embed)
                        await update_bot_status(interaction.guild.id)
                save_volumes()
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(5)
                else:
                    embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed)
                    with open('bot.log', 'a') as f:
                        f.write(f"{time.ctime()}: Error in play command: {e}\n")
                    return
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed)
                with open('bot.log', 'a') as f:
                    f.write(f"{time.ctime()}: Error in play command: {e}\n")
                return

@bot.tree.command(name="pause", description="Pause the current song")
async def pause_slash(interaction: discord.Interaction):
    global current_playing_message
    if not interaction.guild or not interaction.guild.voice_client:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
        return
    player = interaction.guild.voice_client
    if not player.playing or player.paused:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="No track is playing or already paused!", color=discord.Color.red()), ephemeral=True)
        return
    await player.pause(True)
    if current_playing_message:
        try:
            message = await interaction.channel.fetch_message(current_playing_message)
            embed = discord.Embed(
                title="Now Paused",
                description=f"**[{player.current.title}]({player.current.uri})**",
                color=discord.Color.green()
            )
            embed.add_field(name="Source", value=player.current.source, inline=True)
            embed.add_field(name="Volume", value=f"{saved_volumes.get(interaction.guild.id, 50)}%", inline=True)
            embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
            if hasattr(player.current, 'author'):
                embed.add_field(name="Artist", value=player.current.author, inline=True)
            if hasattr(player.current, 'thumbnail'):
                embed.set_thumbnail(url=player.current.thumbnail)
            await message.edit(embed=embed, view=MusicButtons(player))
        except:
            embed = discord.Embed(
                title="Now Paused",
                description=f"**[{player.current.title}]({player.current.uri})**",
                color=discord.Color.green()
            )
            embed.add_field(name="Source", value=player.current.source, inline=True)
            embed.add_field(name="Volume", value=f"{saved_volumes.get(interaction.guild.id, 50)}%", inline=True)
            embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
            if hasattr(player.current, 'author'):
                embed.add_field(name="Artist", value=player.current.author, inline=True)
            if hasattr(player.current, 'thumbnail'):
                embed.set_thumbnail(url=player.current.thumbnail)
            message = await interaction.channel.send(embed=embed, view=MusicButtons(player))
            current_playing_message = message.id
    await interaction.response.send_message("Paused the current track.", ephemeral=True)
    await asyncio.sleep(5)
    await interaction.delete_original_response()
    await update_bot_status(interaction.guild.id)

@bot.tree.command(name="resume", description="Resume the paused song")
async def resume_slash(interaction: discord.Interaction):
    global current_playing_message
    if not interaction.guild or not interaction.guild.voice_client:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
        return
    player = interaction.guild.voice_client
    if not player.paused:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Track is not paused!", color=discord.Color.red()), ephemeral=True)
        return
    await player.pause(False)
    if current_playing_message:
        try:
            message = await interaction.channel.fetch_message(current_playing_message)
            embed = discord.Embed(
                title="Now Playing",
                description=f"**[{player.current.title}]({player.current.uri})**",
                color=discord.Color.green()
            )
            embed.add_field(name="Source", value=player.current.source, inline=True)
            embed.add_field(name="Volume", value=f"{saved_volumes.get(interaction.guild.id, 50)}%", inline=True)
            embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
            if hasattr(player.current, 'author'):
                embed.add_field(name="Artist", value=player.current.author, inline=True)
            if hasattr(player.current, 'thumbnail'):
                embed.set_thumbnail(url=player.current.thumbnail)
            await message.edit(embed=embed, view=MusicButtons(player))
        except:
            embed = discord.Embed(
                title="Now Playing",
                description=f"**[{player.current.title}]({player.current.uri})**",
                color=discord.Color.green()
            )
            embed.add_field(name="Source", value=player.current.source, inline=True)
            embed.add_field(name="Volume", value=f"{saved_volumes.get(interaction.guild.id, 50)}%", inline=True)
            embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
            if hasattr(player.current, 'author'):
                embed.add_field(name="Artist", value=player.current.author, inline=True)
            if hasattr(player.current, 'thumbnail'):
                embed.set_thumbnail(url=player.current.thumbnail)
            message = await interaction.channel.send(embed=embed, view=MusicButtons(player))
            current_playing_message = message.id
    await interaction.response.send_message("Resumed the current track.", ephemeral=True)
    await asyncio.sleep(5)
    await interaction.delete_original_response()
    await update_bot_status(interaction.guild.id)

@bot.tree.command(name="volume", description="Set volume (0-100)")
async def volume_slash(interaction: discord.Interaction, volume: int):
    global current_playing_message, saved_volumes
    if not interaction.guild or not interaction.guild.voice_client:
        embed = discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if not 0 <= volume <= 100:
        embed = discord.Embed(
            title="Error", description="Volume must be between 0 and 100!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    player = interaction.guild.voice_client
    guild_id = interaction.guild.id
    saved_volumes[guild_id] = volume
    await player.set_volume(volume)
    if player.playing and current_playing_message:
        try:
            message = await interaction.channel.fetch_message(current_playing_message)
            embed = message.embeds[0]
            embed.set_field_at(1, name="Volume", value=f"{volume}%", inline=True)
            await message.edit(embed=embed, view=MusicButtons(player))
        except:
            embed = discord.Embed(
                title=f"Now {'Paused' if player.paused else 'Playing'}",
                description=f"**[{player.current.title}]({player.current.uri})**",
                color=discord.Color.green()
            )
            embed.add_field(name="Source", value=player.current.source, inline=True)
            embed.add_field(name="Volume", value=f"{volume}%", inline=True)
            embed.add_field(name="Duration", value=format_duration(player.current.length), inline=True)
            if hasattr(player.current, 'author'):
                embed.add_field(name="Artist", value=player.current.author, inline=True)
            if hasattr(player.current, 'thumbnail'):
                embed.set_thumbnail(url=player.current.thumbnail)
            message = await interaction.channel.send(embed=embed, view=MusicButtons(player))
            current_playing_message = message.id
    else:
        embed = discord.Embed(
            title="Volume", description=f"Volume set to {volume}%", color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
    message = await interaction.followup.send(f"Volume set to {volume}%", ephemeral=True)
    await asyncio.sleep(5)
    await message.delete()
    await update_bot_status(interaction.guild.id, player)
    save_volumes()

@bot.tree.command(name="skip", description="Skip the current song or a specified number of songs")
async def skip_slash(interaction: discord.Interaction, amount: int = 1):
    if not interaction.guild or not interaction.guild.voice_client:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
        return
    if amount < 1:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Skip amount must be at least 1!", color=discord.Color.red()), ephemeral=True)
        return
    guild_id = interaction.guild.id
    if guild_id not in skip_locks:
        skip_locks[guild_id] = asyncio.Lock()
    if skip_locks[guild_id].locked():
        await interaction.response.send_message("Cannot skip right now.", ephemeral=True)
        return
    await interaction.response.defer()
    async with skip_locks[guild_id]:
        try:
            player = interaction.guild.voice_client
            player_id = id(player)
            await player.stop()
            if player_id in loop_count:
                del loop_count[player_id]
            if player_id in loop_active:
                del loop_active[player_id]
            if player_id in loop_track:
                del loop_track[player_id]
            await play_next(interaction.channel, amount=amount)
            message = await interaction.followup.send(f"Skipped {amount} track(s).", ephemeral=True)
            await asyncio.sleep(5)
            await message.delete()
        except Exception as e:
            await interaction.followup.send(f"Error skipping track: {e}", ephemeral=True)
            with open('bot.log', 'a') as f:
                f.write(f"{time.ctime()}: Error in skip_slash: {e}\n")
    await update_bot_status(interaction.guild.id)

@bot.tree.command(name="stop", description="Stop music and clear the queue")
async def stop_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, auto_disconnect_task
    if interaction.guild and interaction.guild.voice_client:
        guild_id = interaction.guild.id
        player_id = id(interaction.guild.voice_client)
        if guild_id in auto_disconnect_task:
            auto_disconnect_task[guild_id].cancel()
            del auto_disconnect_task[guild_id]
        if player_id in loop_count:
            del loop_count[player_id]
        if player_id in loop_active:
            del loop_active[player_id]
        if player_id in loop_track:
            del loop_track[player_id]
        await interaction.guild.voice_client.stop()
        song_queue.clear()
        current_playing_message = None
        embed = discord.Embed(
            title="Stopped", description="Stopped music and cleared the queue.", color=discord.Color.blue()
        )
        message = await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        await message.delete()
        if guild_id not in auto_disconnect_task:
            auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, interaction.guild.voice_client))
        await update_bot_status(guild_id)
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="No music is playing!", color=discord.Color.red()), ephemeral=True)

@bot.tree.command(name="leave", description="Leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, auto_disconnect_task
    if interaction.guild and interaction.guild.voice_client:
        guild_id = interaction.guild.id
        player_id = id(interaction.guild.voice_client)
        if guild_id in auto_disconnect_task:
            auto_disconnect_task[guild_id].cancel()
            del auto_disconnect_task[guild_id]
        if player_id in loop_count:
            del loop_count[player_id]
        if player_id in loop_active:
            del loop_active[player_id]
        if player_id in loop_track:
            del loop_track[player_id]
        song_queue.clear()
        await interaction.guild.voice_client.disconnect()
        current_playing_message = None
        embed = discord.Embed(title="Disconnected", description="Left the voice channel.", color=discord.Color.blue())
        message = await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        await message.delete()
        await update_bot_status(guild_id)
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)

@bot.tree.command(name="queue", description="Display the list of tracks in the queue")
async def queue_slash(interaction: discord.Interaction):
    global song_queue
    await interaction.response.defer(thinking=True)
    view = QueueView(song_queue)
    start_idx = (view.current_page - 1) * view.per_page
    end_idx = min(start_idx + view.per_page, len(song_queue))
    embed = discord.Embed(title="Queue", color=discord.Color.blue())
    if not song_queue:
        embed.description = "The queue is currently empty!"
    else:
        for i, track in enumerate(list(song_queue)[start_idx:end_idx], start_idx + 1):
            embed.add_field(
                name=f"Track {i}: [{track.title}]({track.uri})",
                value=f"Duration: {format_duration(track.length)}",
                inline=False
            )
        embed.set_footer(text=f"Page {view.current_page}/{view.total_pages}")
    await interaction.followup.send(embed=embed, view=view)
    message = await interaction.followup.send("Displayed the queue.", ephemeral=True)
    await asyncio.sleep(5)
    await message.delete()
    await update_bot_status(interaction.guild.id)

@bot.tree.command(name="help", description="Display the list of commands")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(title="Music Bot Commands", color=discord.Color.blue())
    for command in bot.tree.get_commands():
        embed.add_field(name=f"/{command.name}", value=command.description, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="loop", description="Loop the current track (no number: infinite, number: loop count)")
async def loop_slash(interaction: discord.Interaction, times: str = None):
    if not interaction.guild or not interaction.guild.voice_client or not interaction.guild.voice_client.playing:
        embed = discord.Embed(
            title="Error", description="No track is currently playing!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    player = interaction.guild.voice_client
    player_id = id(player)
    current_track = player.current
    if not current_track:
        embed = discord.Embed(
            title="Error", description="Cannot identify the current track!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if times is None:
        loop_count[player_id] = float('inf')
        loop_active[player_id] = True
        loop_track[player_id] = current_track
        embed = discord.Embed(
            title="Loop", description=f"Enabled infinite loop for '[{current_track.title}]({current_track.uri})'.", color=discord.Color.blue()
        )
    else:
        try:
            times = int(times)
            if times < 0:
                embed = discord.Embed(
                    title="Error", description="Loop count must be 0 or greater!", color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            loop_count[player_id] = times
            loop_active[player_id] = True
            loop_track[player_id] = current_track
            embed = discord.Embed(
                title="Loop", description=f"Enabled loop for '[{current_track.title}]({current_track.uri})' {times} times.", color=discord.Color.blue()
            )
        except ValueError:
            embed = discord.Embed(
                title="Error", description="Please enter a valid integer!", color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    await interaction.response.send_message(embed=embed)
    await update_bot_status(interaction.guild.id, player)

def is_already_running():
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.name() == 'python3' and proc.pid != current_pid:
            with open('bot.pid', 'w') as f:
                f.write(str(current_pid))
            return True
    return False

async def login_with_retry(client, token, max_retries=5, delay=5):
    for attempt in range(max_retries):
        try:
            await client.login(token)
            return
        except discord.errors.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise e
    raise Exception("Failed to login after maximum retries")

if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv('DISCORD_TOKEN')
    if is_already_running():
        exit(1)
    async def start_bot():
        await login_with_retry(bot, TOKEN)
        await bot.start(TOKEN)
    asyncio.run(start_bot())
