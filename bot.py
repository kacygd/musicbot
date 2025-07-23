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

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Set up bot with intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Remove default help command
bot.remove_command('help')

# Sync slash commands with retry
async def sync_commands():
    for attempt in range(5):
        try:
            await bot.tree.sync()
            print("Slash commands synced successfully!")
            return
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"Rate limited during command sync (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                await asyncio.sleep(5)
            else:
                print(f"Failed to sync commands: {e}")
                raise e
    print("Failed to sync commands after 5 attempts")

# Queue to store songs
song_queue = deque()

# Global variables
saved_volumes = {}  # Dictionary to store volume per guild, default 50
current_playing_message = None
loop_count = {} 
loop_active = {}
loop_track = {} 
is_skipping = False  # Flag to prevent multiple skip triggers
auto_disconnect_task = {}  # Dictionary to track auto-disconnect tasks per guild
last_status_update = None
status_update_interval = 60  # Update status every 60 seconds

# HTML content for the server
HTML_CONTENT = """Bot is Alive"""

# HTTP server handler
class SimpleHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_CONTENT.encode('utf-8'))

# Function to start HTTP server
def start_http_server():
    PORT = 8000
    with socketserver.TCPServer(("", PORT), SimpleHTTPRequestHandler) as httpd:
        print(f"HTTP server running on port {PORT}")
        httpd.serve_forever()

# Convert duration from milliseconds to minutes:seconds
def format_duration(length):
    seconds = length // 1000  # Convert milliseconds to seconds
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"

# Update bot status
async def update_bot_status(guild_id=None, player=None):
    global last_status_update
    current_time = time.time()
    if last_status_update and (current_time - last_status_update < status_update_interval):
        return
    last_status_update = current_time
    guild_count = len(bot.guilds)
    try:
        if player and player.playing and player.current:
            await bot.change_presence(activity=discord.Activity(
                type=discord.ActivityType.playing, 
                name=f"music in {guild_count} servers"
            ))
        elif song_queue:
            await bot.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching, 
                name=f"queue: {len(song_queue)} tracks in {guild_count} servers"
            ))
        else:
            await bot.change_presence(activity=discord.Activity(
                type=discord.ActivityType.playing, 
                name="Use /help for commands"
            ))
        print(f"Updated bot status for guild {guild_id}")
    except Exception as e:
        print(f"Error updating bot status: {e}")

# Auto-disconnect from voice channel
async def auto_disconnect(guild_id, player):
    global song_queue, current_playing_message
    await asyncio.sleep(180)  # Wait 3 minutes
    if not player or not player.channel:
        print(f"No player or channel found for guild {guild_id}")
        return
    # Check if no users are in the voice channel (except bot)
    if len([member for member in player.channel.members if not member.bot]) == 0:
        print(f"No users in voice channel for guild {guild_id}, disconnecting...")
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
            await player.text_channel.send(embed=embed, delete_after=5)
    # Check if no tracks are playing and queue is empty
    elif not player.playing and not song_queue:
        print(f"No track playing or in queue for guild {guild_id}, disconnecting...")
        song_queue.clear()
        current_playing_message = None
        await player.disconnect()
        await update_bot_status(guild_id)
        embed = discord.Embed(
            title="Disconnected", 
            description="Left voice channel due to no tracks after 3 minutes.", 
            color=discord.Color.blue()
        )
        if player.text_channel:
            await player.text_channel.send(embed=embed, delete_after=5)
    if guild_id in auto_disconnect_task:
        del auto_disconnect_task[guild_id]

# Class for music control buttons
class MusicButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, song_queue, is_skipping
        if not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        if is_skipping:
            await interaction.response.defer()
            return
        is_skipping = True
        player = interaction.guild.voice_client
        player_id = id(player)
        await player.stop()
        current_playing_message = None
        # Clear loop settings for this player
        if player_id in loop_count:
            del loop_count[player_id]
        if player_id in loop_active:
            del loop_active[player_id]
        if player_id in loop_track:
            del loop_track[player_id]
        if song_queue:
            await play_next(interaction.channel)
        else:
            if interaction.guild.id not in auto_disconnect_task:
                auto_disconnect_task[interaction.guild.id] = asyncio.create_task(auto_disconnect(interaction.guild.id, player))
        await interaction.response.send_message("Skipped the current track.", ephemeral=True, delete_after=5)
        is_skipping = False

    @discord.ui.button(label="Volume Up", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, saved_volumes
        if not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        guild_id = interaction.guild.id
        current_volume = player.volume
        saved_volumes[guild_id] = min(current_volume + 10, 100)
        await player.set_volume(saved_volumes[guild_id])
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = message.embeds[0]
                embed.set_field_at(1, name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                await message.edit(embed=embed, view=self)
            except:
                embed = discord.Embed(
                    title="Now Playing", 
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
                message = await interaction.channel.send(embed=embed, view=self)
                current_playing_message = message.id
        await interaction.response.defer()

    @discord.ui.button(label="Volume Down", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, saved_volumes
        if not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        guild_id = interaction.guild.id
        current_volume = player.volume
        saved_volumes[guild_id] = max(current_volume - 10, 0)
        await player.set_volume(saved_volumes[guild_id])
        if current_playing_message:
            try:
                message = await interaction.channel.fetch_message(current_playing_message)
                embed = message.embeds[0]
                embed.set_field_at(1, name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
                await message.edit(embed=embed, view=self)
            except:
                embed = discord.Embed(
                    title="Now Playing", 
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
                message = await interaction.channel.send(embed=embed, view=self)
                current_playing_message = message.id
        await interaction.response.defer()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, song_queue, auto_disconnect_task
        if interaction.guild.voice_client:
            guild_id = interaction.guild.id
            player_id = id(interaction.guild.voice_client)
            if guild_id in auto_disconnect_task:
                auto_disconnect_task[guild_id].cancel()
                del auto_disconnect_task[guild_id]
            # Clear loop settings
            if player_id in loop_count:
                del loop_count[player_id]
            if player_id in loop_active:
                del loop_active[player_id]
            if player_id in loop_track:
                del loop_track[player_id]
            await interaction.guild.voice_client.stop()
            song_queue.clear()
            current_playing_message = None
            await update_bot_status(guild_id)
            embed = discord.Embed(
                title="Stopped", description="Stopped music and cleared the queue.", color=discord.Color.blue())
            await interaction.response.send_message(embed=embed, delete_after=5)
        else:
            await interaction.response.send_message(embed=discord.Embed(
                title="Error", description="No music is playing!", color=discord.Color.red()), ephemeral=True)

# Class for pagination view
class QueueView(discord.ui.View):
    def __init__(self, song_queue):
        super().__init__(timeout=60)
        self.song_queue = song_queue
        self.current_page = 1
        self.per_page = 10  # Display 10 tracks per page
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
    print(f'{bot.user} has connected to Discord!')
    try:
        await wavelink.Pool.connect(
            client=bot,
            nodes=[wavelink.Node(
                uri='wss://lava-v4.ajieblogs.eu.org:443',
                password='https://dsc.gg/ajidevserver'
            )]
        )
        print("Connected to Lavalink node")
        await sync_commands()
    except Exception as e:
        print(f"Failed to connect to Lavalink: {e}")
    await update_bot_status()
    threading.Thread(target=start_http_server, daemon=True).start()

@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    print(f"Lavalink node ready! Node: {payload.node.uri}")

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    global current_playing_message, is_skipping, auto_disconnect_task
    print(f"Track ended: {payload.reason}")
    player = payload.player
    channel = getattr(player, 'text_channel', None)
    guild_id = player.guild.id
    current_playing_message = None
    player_id = id(player)
    current_track = loop_track.get(player_id)

    # Check for track looping
    if current_track and player_id in loop_count and loop_count[player_id] > 0 and loop_active.get(player_id, False):
        loop_count[player_id] -= 1
        try:
            await player.play(current_track)
            print(f"Replaying track: {current_track.title} (Remaining loops: {loop_count[player_id]})")
            await update_bot_status(guild_id, player)
        except Exception as e:
            print(f"Error replaying track: {e}")
        return
    elif player_id in loop_count and loop_count[player_id] == 0:
        del loop_count[player_id]
        del loop_active[player_id]
        del loop_track[player_id]
        if channel:
            await channel.send("Loop ended", delete_after=5)
        return

    # Proceed with queue if no loop is active and not skipping
    if channel and song_queue and not loop_active.get(player_id, False) and not is_skipping:
        await play_next(channel)
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
    # Check if no users are in the voice channel
    if len([m for m in before.channel.members if not m.bot]) == 0:
        if guild_id not in auto_disconnect_task:
            auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, player))
    # Cancel task if users rejoin
    elif guild_id in auto_disconnect_task:
        auto_disconnect_task[guild_id].cancel()
        del auto_disconnect_task[guild_id]
        print(f"Cancelled auto-disconnect for guild {guild_id}, users rejoined")

async def play_next(channel):
    global current_playing_message, saved_volumes, is_skipping, auto_disconnect_task
    print(f"Attempting play_next, Is Skipping: {is_skipping}")
    if not channel.guild.voice_client:
        print("No voice client found in play_next")
        is_skipping = False
        return
    if is_skipping:
        print("Skipping play_next due to ongoing skip")
        is_skipping = False
        return
    is_skipping = True
    for attempt in range(5):
        try:
            if song_queue:
                track = song_queue.popleft()
                player = channel.guild.voice_client
                guild_id = channel.guild.id
                volume = saved_volumes.get(guild_id, 50)
                await player.set_volume(volume)
                await player.play(track)
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
                message = await channel.send(embed=embed, view=MusicButtons())
                current_playing_message = message.id
                print(f"Playing track: {track.title} from {track.source}")
                await update_bot_status(channel.guild.id, player)
                if channel.guild.id in auto_disconnect_task:
                    auto_disconnect_task[channel.guild.id].cancel()
                    del auto_disconnect_task[channel.guild.id]
            else:
                if channel.guild.id not in auto_disconnect_task:
                    auto_disconnect_task[channel.guild.id] = asyncio.create_task(auto_disconnect(channel.guild.id, channel.guild.voice_client))
                await update_bot_status(channel.guild.id)
            break
        except discord.HTTPException as e:
            if e.status == 429:
                print(f"Rate limited in play_next (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                await asyncio.sleep(5)
            else:
                embed = discord.Embed(title="Error", description=f"Error playing track: {e}", color=discord.Color.red())
                await channel.send(embed=embed)
                print(f"Error playing track: {e}")
                break
        except Exception as e:
            embed = discord.Embed(title="Error", description=f"Error playing track: {e}", color=discord.Color.red())
            await channel.send(embed=embed)
            print(f"Error playing track: {e}")
            break
    is_skipping = False

@bot.tree.command(name="play", description="Play a song or playlist from any source (YouTube, Spotify, SoundCloud, etc.)")
async def play_slash(interaction: discord.Interaction, query: str):
    global current_playing_message, saved_volumes, auto_disconnect_task
    print(f"Received /play command with query: {query}")
    
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        embed = discord.Embed(
            title="Error", description="You need to be in a voice channel to play music!", color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        print("User not in a voice channel")
        return

    channel = interaction.user.voice.channel
    player = interaction.guild.voice_client
    guild_id = interaction.guild.id

    if not player:
        for attempt in range(5):
            try:
                player = await channel.connect(cls=wavelink.Player)
                volume = saved_volumes.get(guild_id, 50)
                await player.set_volume(volume)
                player.text_channel = interaction.channel
                print(f"Connected to voice channel: {channel.name}")
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    print(f"Rate limited when connecting to voice channel (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                    await asyncio.sleep(5)
                else:
                    embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    print(f"Error connecting to voice channel: {e}")
                    return
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed, ephemeral=True)
                print(f"Error connecting to voice channel: {e}")
                return

    async with interaction.channel.typing():
        for attempt in range(5):
            try:
                tracks = await wavelink.Playable.search(query, source=None)
                if not tracks:
                    embed = discord.Embed(
                        title="Error", description="No song or playlist found!", color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    print("No song found")
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
                        message = await interaction.followup.send(embed=embed, view=MusicButtons())
                        current_playing_message = message.id
                        print(f"Playing track: {track.title} from {track.source}")
                        await update_bot_status(interaction.guild.id, player)
                        if interaction.guild.id in auto_disconnect_task:
                            auto_disconnect_task[interaction.guild.id].cancel()
                            del auto_disconnect_task[interaction.guild.id]
                    else:
                        await interaction.followup.send(embed=embed)
                        print(f"Added {len(tracks.tracks)} tracks from playlist to queue")
                        await update_bot_status(interaction.guild.id)
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
                        message = await interaction.followup.send(embed=embed, view=MusicButtons())
                        current_playing_message = message.id
                        print(f"Playing track: {track.title} from {track.source}")
                        await update_bot_status(interaction.guild.id, player)
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
                        print(f"Added to queue: {track.title} from {track.source}")
                        await update_bot_status(interaction.guild.id)
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    print(f"Rate limited in play command (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                    await asyncio.sleep(5)
                else:
                    embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed)
                    print(f"Error in play command: {e}")
                    break
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed)
                print(f"Error in play command: {e}")
                break

@bot.tree.command(name="volume", description="Set volume (0-100)")
async def volume_slash(interaction: discord.Interaction, volume: int):
    global current_playing_message, saved_volumes
    if not interaction.guild.voice_client:
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
    await player.set_volume(saved_volumes[guild_id])
    if player.playing and current_playing_message:
        try:
            message = await interaction.channel.fetch_message(current_playing_message)
            embed = message.embeds[0]
            embed.set_field_at(1, name="Volume", value=f"{saved_volumes[guild_id]}%", inline=True)
            await message.edit(embed=embed, view=MusicButtons())
        except:
            embed = discord.Embed(
                title="Now Playing", 
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
            message = await interaction.channel.send(embed=embed, view=MusicButtons())
            current_playing_message = message.id
    else:
        embed = discord.Embed(
            title="Volume", description=f"Volume set to {saved_volumes[guild_id]}%", color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
    message = await interaction.followup.send(f"Volume set to {saved_volumes[guild_id]}%", ephemeral=True)
    await asyncio.sleep(5)
    await message.delete()
    await update_bot_status(interaction.guild.id, player)

@bot.tree.command(name="skip", description="Skip the current song")
async def skip_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, is_skipping, auto_disconnect_task
    if not interaction.guild.voice_client:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="Bot is not in a voice channel!", color=discord.Color.red()), ephemeral=True)
        return
    if is_skipping:
        await interaction.response.send_message("Cannot skip right now.", ephemeral=True)
        return
    is_skipping = True
    player = interaction.guild.voice_client
    player_id = id(player)
    await player.stop()
    current_playing_message = None
    # Clear loop settings
    if player_id in loop_count:
        del loop_count[player_id]
    if player_id in loop_active:
        del loop_active[player_id]
    if player_id in loop_track:
        del loop_track[player_id]
    if song_queue:
        await play_next(interaction.channel)
    else:
        if interaction.guild.id not in auto_disconnect_task:
            auto_disconnect_task[interaction.guild.id] = asyncio.create_task(auto_disconnect(interaction.guild.id, player))
    message = await interaction.response.send_message("Skipped the current track.", ephemeral=True)
    await asyncio.sleep(5)
    await message.delete()
    await update_bot_status(interaction.guild.id)
    is_skipping = False

@bot.tree.command(name="stop", description="Stop music and clear the queue")
async def stop_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, auto_disconnect_task
    if interaction.guild.voice_client:
        guild_id = interaction.guild.id
        player_id = id(interaction.guild.voice_client)
        if guild_id in auto_disconnect_task:
            auto_disconnect_task[guild_id].cancel()
            del auto_disconnect_task[guild_id]
        # Clear loop settings
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
        await interaction.response.send_message(embed=embed, delete_after=5)
        if guild_id not in auto_disconnect_task:
            auto_disconnect_task[guild_id] = asyncio.create_task(auto_disconnect(guild_id, interaction.guild.voice_client))
        await update_bot_status(guild_id)
    else:
        await interaction.response.send_message(embed=discord.Embed(
            title="Error", description="No music is playing!", color=discord.Color.red()), ephemeral=True)

@bot.tree.command(name="leave", description="Leave the voice channel")
async def leave_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, auto_disconnect_task
    if interaction.guild.voice_client:
        guild_id = interaction.guild.id
        player_id = id(interaction.guild.voice_client)
        if guild_id in auto_disconnect_task:
            auto_disconnect_task[guild_id].cancel()
            del auto_disconnect_task[guild_id]
        # Clear loop settings
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
        await interaction.response.send_message(embed=embed, delete_after=5)
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
    if not interaction.guild.voice_client or not interaction.guild.voice_client.playing:
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

# Check for multiple instances
def is_already_running():
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.name() == 'python3' and proc.pid != current_pid:
            with open('bot.pid', 'w') as f:
                f.write(str(current_pid))
            return True
    return False

# Custom login with retry
async def login_with_retry(client, token, max_retries=5, delay=5):
    for attempt in range(max_retries):
        try:
            await client.login(token)
            return
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"Rate limited during login (attempt {attempt + 1}/{max_retries}). Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise e
    raise Exception("Failed to login after maximum retries")

if __name__ == "__main__":
    if is_already_running():
        print("Another instance of the bot is already running. Exiting...")
        exit(1)
    async def start_bot():
        await login_with_retry(bot, TOKEN)
        await bot.start(TOKEN)
    asyncio.run(start_bot())
