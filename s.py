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
import logging
import sys

# Thiết lập logging
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s')

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
            logging.info("Slash commands synced successfully!")
            return
        except discord.errors.HTTPException as e:
            if e.status == 429:
                print(f"Rate limited during command sync (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                logging.warning(f"Rate limited during command sync (attempt {attempt + 1}/5).")
                await asyncio.sleep(5)
            else:
                print(f"Failed to sync commands: {e}")
                logging.error(f"Failed to sync commands: {e}")
                raise e
    print("Failed to sync commands after 5 attempts")
    logging.error("Failed to sync commands after 5 attempts")

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
bot_start_time = time.time()  # Thời gian bot bắt đầu chạy

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
        logging.info(f"HTTP server running on port {PORT}")
        httpd.serve_forever()

# Convert duration from milliseconds to minutes:seconds
def format_duration(length):
    seconds = length // 1000  # Convert milliseconds to seconds
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"

# Update bot status
async def update_bot_status(guild_id=None, player=None):
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.playing,
        name="Use /help for commands"
    ))

# Hàm kiểm tra và khởi động lại bot sau 4 tiếng
async def restart_bot_after_timeout():
    global bot_start_time
    RESTART_INTERVAL = 4 * 3600  # 4 tiếng tính bằng giây
    while True:
        elapsed_time = time.time() - bot_start_time
        if elapsed_time >= RESTART_INTERVAL:
            logging.info("Bot has been running for 4 hours. Initiating restart...")
            print("Bot has been running for 4 hours. Initiating restart...")
            try:
                for guild in bot.guilds:
                    if guild.voice_client:
                        await guild.voice_client.disconnect()
                await bot.close()
                logging.info("Bot connections closed successfully.")
                print("Bot connections closed successfully.")
            except Exception as e:
                logging.error(f"Error closing bot connections: {e}")
                print(f"Error closing bot connections: {e}")
            os.execv(sys.executable, ['python'] + sys.argv)
        await asyncio.sleep(60)  # Kiểm tra mỗi 60 giây

# Auto-disconnect from voice channel
async def auto_disconnect(guild_id, player):
    global song_queue, current_playing_message
    await asyncio.sleep(180)  # Wait 3 minutes
    if not player or not player.channel:
        print(f"No player or channel found for guild {guild_id}")
        logging.info(f"No player or channel found for guild {guild_id}")
        return
    if len([member for member in player.channel.members if not member.bot]) == 0:
        print(f"No users in voice channel for guild {guild_id}, disconnecting...")
        logging.info(f"No users in voice channel for guild {guild_id}, disconnecting...")
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
    elif not player.playing and not song_queue:
        print(f"No track playing or in queue for guild {guild_id}, disconnecting...")
        logging.info(f"No track playing or in queue for guild {guild_id}, disconnecting...")
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
                title="Error", description interna: discord.Interaction
async def play_slash(interaction: discord.Interaction, query: str):
    global current_playing_message, saved_volumes, auto_disconnect_task
    print(f"Received /play command with query: {query}")
    logging.info(f"Received /play command with query: {query}")
    
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        embed = discord.Embed(
            title="Error", description="You need to be in a voice channel to play music!", color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        print("User not in a voice channel")
        logging.info("User not in a voice channel")
        return

    channel = interaction.user.voice.channel
    player = interaction.guild.voice_client
    guild_id = interaction.guild.id

    print(f"Attempting to connect to channel: {channel.name}, ID: {channel.id}, Permissions: {channel.permissions_for(interaction.guild.me)}")
    logging.info(f"Attempting to connect to channel: {channel.name}, ID: {channel.id}, Permissions: {channel.permissions_for(interaction.guild.me)}")

    if not player:
        for attempt in range(5):
            try:
                print(f"Attempt {attempt + 1}: Connecting to voice channel {channel.name}...")
                logging.info(f"Attempt {attempt + 1}: Connecting to voice channel {channel.name}...")
                player = await channel.connect(cls=wavelink.Player, timeout=60.0, reconnect=True)
                volume = saved_volumes.get(guild_id, 50)
                await player.set_volume(volume)
                player.text_channel = interaction.channel
                print(f"Successfully connected to voice channel: {channel.name}")
                logging.info(f"Successfully connected to voice channel: {channel.name}")
                break
            except discord.HTTPException as e:
                print(f"HTTPException during voice channel connection (attempt {attempt + 1}/5): Status {e.status}, Code {e.code}, Text: {e.text}")
                logging.error(f"HTTPException during voice channel connection (attempt {attempt + 1}/5): Status {e.status}, Code {e.code}, Text: {e.text}")
                if e.status == 429:
                    print(f"Rate limited, retrying in {5 * (attempt + 1)} seconds...")
                    logging.warning(f"Rate limited, retrying in {5 * (attempt + 1)} seconds...")
                    await asyncio.sleep(5 * (attempt + 1))
                else:
                    embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    print(f"Failed to connect to voice channel: {e}")
                    logging.error(f"Failed to connect to voice channel: {e}")
                    return
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error connecting to voice channel: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed, ephemeral=True)
                print(f"Unexpected error during voice channel connection: {e}")
                logging.error(f"Unexpected error during voice channel connection: {e}")
                return

    async with interaction.channel.typing():
        for attempt in range(5):
            try:
                # Kiểm tra nếu query là URL
                if query.startswith(('http://', 'https://')):
                    tracks = await wavelink.Player.search(query)
                    if not tracks:
                        embed = discord.Embed(
                            title="Error", description="No song or playlist found from the link!", color=discord.Color.red()
                        )
                        await interaction.followup.send(embed=embed)
                        print("No song found from link")
                        logging.info("No song found from link")
                        return

                    if isinstance(tracks, wavelink.Playlist):
                        for track in tracks.tracks:
                            song_queue.append(track)
                        embed = discord.Embed(
                            title="Added Playlist",
                            description=f"Added {len(tracks.tracks)} tracks from playlist to the queue",
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
                            logging.info(f"Playing track: {track.title} from {track.source}")
                            await update_bot_status()
                            if interaction.guild.id in auto_disconnect_task:
                                auto_disconnect_task[interaction.guild.id].cancel()
                                del auto_disconnect_task[interaction.guild.id]
                        else:
                            await interaction.followup.send(embed=embed)
                            print(f"Added {len(tracks.tracks)} tracks from playlist to queue")
                            logging.info(f"Added {len(tracks.tracks)} tracks from playlist to queue")
                            await update_bot_status()
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
                            logging.info(f"Playing track: {track.title} from {track.source}")
                            await update_bot_status()
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
                            logging.info(f"Added to queue: {track.title} from {track.source}")
                            await update_bot_status()
                else:
                    # Tìm kiếm trên các nền tảng (YouTube, Spotify, SoundCloud)
                    all_tracks = []
                    try:
                        # Tìm kiếm trên YouTube
                        youtube_tracks = await wavelink.YouTubeTrack.search(query=query, return_first_result=False)
                        if youtube_tracks:
                            all_tracks.extend([(track, "youtube") for track in youtube_tracks[:10]])  # Giới hạn 10 bài
                        # Tìm kiếm trên Spotify (nếu được hỗ trợ bởi nút Lavalink)
                        spotify_tracks = await wavelink.SpotifyTrack.search(query=query, return_first_result=False)
                        if spotify_tracks:
                            all_tracks.extend([(track, "spotify") for track in spotify_tracks[:10]])
                        # Tìm kiếm trên SoundCloud
                        soundcloud_tracks = await wavelink.SoundCloudTrack.search(query=query, return_first_result=False)
                        if soundcloud_tracks:
                            all_tracks.extend([(track, "soundcloud") for track in soundcloud_tracks[:10]])
                    except Exception as e:
                        print(f"Error searching: {e}")
                        logging.error(f"Error searching: {e}")

                    if not all_tracks:
                        embed = discord.Embed(
                            title="Error", description=f"No results found for '{query}' on any platform!", color=discord.Color.red()
                        )
                        await interaction.followup.send(embed=embed)
                        print(f"No results found for '{query}'")
                        logging.info(f"No results found for '{query}'")
                        return

                    # Lấy tối đa 10 bài từ tất cả nền tảng
                    all_tracks = all_tracks[:10]
                    # Tạo view để chọn bài hát
                    class TrackSelection(discord.ui.View):
                        def __init__(self, tracks, interaction):
                            super().__init__(timeout=60)
                            self.tracks = tracks
                            self.interaction = interaction
                            self.add_items()

                        def add_items(self):
                            for i, (track, source) in enumerate(self.tracks):
                                button = discord.ui.Button(label=f"{i + 1}. {track.title[:50]}... ({source.capitalize()})", style=discord.ButtonStyle.primary)
                                async def callback(interaction):
                                    selected_track = self.tracks[i][0]
                                    song_queue.append(selected_track)
                                    if not player.playing:
                                        selected_track = song_queue.popleft()
                                        await player.play(selected_track)
                                        embed = discord.Embed(
                                            title="Now Playing", 
                                            description=f"**[{selected_track.title}]({selected_track.uri})**", 
                                            color=discord.Color.green()
                                        )
                                        embed.add_field(name="Source", value=selected_track.source, inline=True)
                                        embed.add_field(name="Volume", value=f"{saved_volumes.get(guild_id, 50)}%", inline=True)
                                        embed.add_field(name="Duration", value=format_duration(selected_track.length), inline=True)
                                        if hasattr(selected_track, 'author'):
                                            embed.add_field(name="Artist", value=selected_track.author, inline=True)
                                        if hasattr(selected_track, 'thumbnail'):
                                            embed.set_thumbnail(url=selected_track.thumbnail)
                                        message = await self.interaction.followup.send(embed=embed, view=MusicButtons())
                                        current_playing_message = message.id
                                        print(f"Playing selected track: {selected_track.title} from {selected_track.source}")
                                        logging.info(f"Playing selected track: {selected_track.title} from {selected_track.source}")
                                        await update_bot_status()
                                        if interaction.guild.id in auto_disconnect_task:
                                            auto_disconnect_task[interaction.guild.id].cancel()
                                            del auto_disconnect_task[interaction.guild.id]
                                    else:
                                        embed = discord.Embed(
                                            title="Added to Queue",
                                            description=f"**[{selected_track.title}]({selected_track.uri})**",
                                            color=discord.Color.blue()
                                        )
                                        embed.add_field(name="Source", value=selected_track.source, inline=True)
                                        embed.add_field(name="Duration", value=format_duration(selected_track.length), inline=True)
                                        if hasattr(selected_track, 'author'):
                                            embed.add_field(name="Artist", value=selected_track.author, inline=True)
                                        await self.interaction.followup.send(embed=embed)
                                        print(f"Added to queue: {selected_track.title} from {selected_track.source}")
                                        logging.info(f"Added to queue: {selected_track.title} from {selected_track.source}")
                                        await update_bot_status()
                                    self.stop()
                                button.callback = callback
                                self.add_item(button)

                    # Hiển thị kết quả tìm kiếm
                    embed = discord.Embed(
                        title="Search Results",
                        description=f"Found tracks for '{query}'. Select a track by clicking a button below:",
                        color=discord.Color.blue()
                    )
                    for i, (track, source) in enumerate(all_tracks, 1):
                        embed.add_field(
                            name=f"{i}. {track.title}",
                            value=f"Source: {source.capitalize()} | Duration: {format_duration(track.length)}",
                            inline=False
                        )
                    view = TrackSelection(all_tracks, interaction)
                    await interaction.followup.send(embed=embed, view=view)
                    print(f"Displayed {len(all_tracks)} search results for '{query}'")
                    logging.info(f"Displayed {len(all_tracks)} search results for '{query}'")

                break
            except discord.HTTPException as e:
                if e.status == 429:
                    print(f"Rate limited in play command (attempt {attempt + 1}/5). Retrying in 5 seconds...")
                    logging.warning(f"Rate limited in play command (attempt {attempt + 1}/5).")
                    await asyncio.sleep(5)
                else:
                    embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                    await interaction.followup.send(embed=embed)
                    print(f"Error in play command: {e}")
                    logging.error(f"Error in play command: {e}")
                    break
            except Exception as e:
                embed = discord.Embed(title="Error", description=f"Error: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed)
                print(f"Error in play command: {e}")
                logging.error(f"Error in play command: {e}")
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
                logging.warning(f"Rate limited during login (attempt {attempt + 1}/{max_retries}).")
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise e
    raise Exception("Failed to login after maximum retries")

if __name__ == "__main__":
    if is_already_running():
        print("Another instance of the bot is already running. Exiting...")
        logging.info("Another instance of the bot is already running. Exiting...")
        exit(1)
    async def start_bot():
        await login_with_retry(bot, TOKEN)
        await bot.start(TOKEN)
    asyncio.run(start_bot())
