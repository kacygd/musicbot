import discord
from discord.ext import commands
import wavelink
import asyncio
import os
from dotenv import load_dotenv
from collections import deque

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

# Sync slash commands
async def sync_commands():
    try:
        await bot.tree.sync()
        print("Slash commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# Queue to store songs
song_queue = deque()

# Global variables
saved_volume = 50  # Default volume
current_playing_message = None
loop_count = {} 
loop_active = {}
loop_track = {} 
is_skipping = False  # Flag to prevent multiple skip triggers

# Hàm chuyển đổi thời lượng từ mili-giây sang phút:giây
def format_duration(length):
    seconds = length // 1000  # Chuyển mili-giây sang giây
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"

# Class for music control buttons
class MusicButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="⏭️")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, song_queue, is_skipping
        if not interaction.guild.voice_client or is_skipping:
            await interaction.response.defer()
            return
        is_skipping = True
        player = interaction.guild.voice_client
        await player.stop()
        current_playing_message = None
        if song_queue:
            await play_next(interaction.channel)
        is_skipping = False
        await interaction.response.send_message("Đã bỏ qua bài hát.", ephemeral=True, delete_after=5)

    @discord.ui.button(label="Volume Up", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global saved_volume, current_playing_message
        if not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Lỗi", description="Bot không ở trong voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        current_volume = player.volume
        saved_volume = min(current_volume + 10, 100)
        await player.set_volume(saved_volume)
        embed = discord.Embed(
            title="Đang phát", description=f"{player.current.title}", color=discord.Color.green()
        )
        embed.add_field(name="Nguồn", value=player.current.source, inline=True)
        embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
        embed.add_field(name="Thời lượng", value=format_duration(player.current.length), inline=True)
        message = await interaction.channel.fetch_message(current_playing_message)
        await message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"Âm lượng đã tăng lên {saved_volume}%", ephemeral=True, delete_after=5)

    @discord.ui.button(label="Volume Down", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global saved_volume, current_playing_message
        if not interaction.guild.voice_client:
            await interaction.response.send_message(embed=discord.Embed(
                title="Lỗi", description="Bot không ở trong voice channel!", color=discord.Color.red()), ephemeral=True)
            return
        player = interaction.guild.voice_client
        current_volume = player.volume
        saved_volume = max(current_volume - 10, 0)
        await player.set_volume(saved_volume)
        embed = discord.Embed(
            title="Đang phát", description=f"{player.current.title}", color=discord.Color.green()
        )
        embed.add_field(name="Nguồn", value=player.current.source, inline=True)
        embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
        embed.add_field(name="Thời lượng", value=format_duration(player.current.length), inline=True)
        message = await interaction.channel.fetch_message(current_playing_message)
        await message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"Âm lượng đã giảm xuống {saved_volume}%", ephemeral=True, delete_after=5)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global current_playing_message, song_queue
        if interaction.guild.voice_client:
            song_queue.clear()
            await interaction.guild.voice_client.disconnect()
            current_playing_message = None
            await interaction.response.send_message(embed=discord.Embed(
                title="Rời", description="Đã rời voice channel.", color=discord.Color.blue()), delete_after=5)
        else:
            await interaction.response.send_message(embed=discord.Embed(
                title="Lỗi", description="Bot không ở trong voice channel!", color=discord.Color.red()), ephemeral=True)

# Class cho View với nút phân trang
class QueueView(discord.ui.View):
    def __init__(self, song_queue):
        super().__init__(timeout=60)
        self.song_queue = song_queue
        self.current_page = 1
        self.per_page = 10  # Hiển thị 10 bài mỗi trang
        self.total_pages = max(1, (len(song_queue) + self.per_page - 1) // self.per_page)

    async def update_embed(self, interaction: discord.Interaction):
        start_idx = (self.current_page - 1) * self.per_page
        end_idx = min(start_idx + self.per_page, len(self.song_queue))
        embed = discord.Embed(title="Hàng đợi", color=discord.Color.blue())
        if not self.song_queue:
            embed.description = "Hàng đợi hiện đang trống!"
        else:
            for i, track in enumerate(list(self.song_queue)[start_idx:end_idx], start_idx + 1):
                embed.add_field(
                    name=f"Bài {i}: {track.title}",
                    value=f"Thời lượng: {format_duration(track.length)}",
                    inline=False
                )
        embed.set_footer(text=f"Trang {self.current_page}/{self.total_pages}")
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
            nodes=[
                wavelink.Node(
                    uri='wss://lava-v4.ajieblogs.eu.org:443',
                    password='https://dsc.gg/ajidevserver'
                )
            ]
        )
        print("Connected to Lavalink node")
        await sync_commands()  # Sync slash commands on startup
    except Exception as e:
        print(f"Failed to connect to Lavalink: {e}")

@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    print(f"Lavalink node ready! Node: {payload.node.uri}")

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    global current_playing_message, is_skipping
    print(f"Track ended: {payload.reason}")
    player = payload.player
    channel = getattr(player, 'text_channel', None)
    current_playing_message = None
    player_id = id(player)
    current_track = loop_track.get(player_id)

    # Debug log
    print(f"Player ID: {player_id}, Loop Count: {loop_count.get(player_id)}, Loop Active: {loop_active.get(player_id)}, Loop Track: {current_track}, Is Skipping: {is_skipping}")

    # Kiểm tra loop cho bài hát hiện tại
    if current_track and player_id in loop_count and loop_count[player_id] > 0 and loop_active.get(player_id, False):
        loop_count[player_id] -= 1
        try:
            await player.play(current_track)
            print(f"Replaying track: {current_track.title} (Loop count left: {loop_count[player_id]})")
        except Exception as e:
            print(f"Error replaying track: {e}")
        return
    elif player_id in loop_count and loop_count[player_id] == 0:
        del loop_count[player_id]
        del loop_active[player_id]
        del loop_track[player_id]
        if channel:
            await channel.send("Kết thúc loop", delete_after=5)
        return

    # Tiếp tục với queue nếu không có loop active và không đang trong quá trình skip
    if channel and song_queue and not loop_active.get(player_id, False) and not is_skipping:
        await play_next(channel)
    elif channel and not song_queue:
        embed = discord.Embed(title="Hàng đợi rỗng", description="Queue is empty!", color=discord.Color.blue())
        await channel.send(embed=embed)
        current_playing_message = None

async def play_next(channel):
    global current_playing_message, saved_volume, is_skipping
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
    try:
        if song_queue:
            track = song_queue.popleft()
            player = channel.guild.voice_client
            await player.play(track)
            embed = discord.Embed(
                title="Đang phát", description=f"{track.title}", color=discord.Color.green()
            )
            embed.add_field(name="Nguồn", value=track.source, inline=True)
            embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
            embed.add_field(name="Thời lượng", value=format_duration(track.length), inline=True)
            message = await channel.send(embed=embed, view=MusicButtons())
            current_playing_message = message.id
            print(f"Playing track: {track.title}")
        else:
            embed = discord.Embed(title="Hàng đợi rỗng", description="Queue is empty!", color=discord.Color.blue())
            await channel.send(embed=embed)
            current_playing_message = None
    except discord.HTTPException as e:
        if e.status == 429:
            print(f"Rate limited (429). Waiting 5 seconds before retrying...")
            await asyncio.sleep(5)
            await play_next(channel)
        else:
            embed = discord.Embed(title="Lỗi", description=f"Error playing track: {e}", color=discord.Color.red())
            await channel.send(embed=embed)
            print(f"Error playing track: {e}")
    except Exception as e:
        embed = discord.Embed(title="Lỗi", description=f"Error playing track: {e}", color=discord.Color.red())
        await channel.send(embed=embed)
        print(f"Error playing track: {e}")
    finally:
        is_skipping = False

@bot.tree.command(name="play", description="Phát nhạc hoặc playlist từ YouTube")
async def play_slash(interaction: discord.Interaction, query: str):
    global current_playing_message, saved_volume
    print(f"Received /play command with query: {query}")
    
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        embed = discord.Embed(
            title="Lỗi", description="Bạn cần vào voice channel để phát nhạc!", color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        print("User not in voice channel")
        return

    channel = interaction.user.voice.channel
    player = interaction.guild.voice_client

    if not player:
        try:
            player = await channel.connect(cls=wavelink.Player)
            await player.set_volume(saved_volume)
            player.text_channel = interaction.channel
            print(f"Connected to voice channel: {channel.name}")
        except Exception as e:
            embed = discord.Embed(
                title="Lỗi", description=f"Failed to join voice channel: {e}", color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            print(f"Failed to join voice channel: {e}")
            return

    async with interaction.channel.typing():
        try:
            tracks = await wavelink.Playable.search(query)
            if not tracks:
                embed = discord.Embed(
                    title="Lỗi", description="Không tìm thấy bài hát hoặc playlist!", color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                print("No tracks found")
                return

            if isinstance(tracks, wavelink.Playlist):
                for track in tracks.tracks:
                    song_queue.append(track)
                embed = discord.Embed(
                    title="Đã thêm playlist",
                    description=f"Đã thêm {len(tracks.tracks)} bài từ playlist '{tracks.name}' vào hàng đợi",
                    color=discord.Color.blue()
                )
                if not player.playing and song_queue:
                    track = song_queue.popleft()
                    await player.play(track)
                    embed = discord.Embed(
                        title="Đang phát", description=f"{track.title}", color=discord.Color.green()
                    )
                    embed.add_field(name="Nguồn", value=track.source, inline=True)
                    embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
                    embed.add_field(name="Thời lượng", value=format_duration(track.length), inline=True)
                    message = await interaction.followup.send(embed=embed, view=MusicButtons())
                    current_playing_message = message.id
                    print(f"Playing track: {track.title}")
                else:
                    await interaction.followup.send(embed=embed)
                    print(f"Added {len(tracks.tracks)} tracks from playlist to queue")
            else:
                track = tracks[0]
                song_queue.append(track)
                if not player.playing:
                    track = song_queue.popleft()
                    await player.play(track)
                    embed = discord.Embed(
                        title="Đang phát", description=f"{track.title}", color=discord.Color.green()
                    )
                    embed.add_field(name="Nguồn", value=track.source, inline=True)
                    embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
                    embed.add_field(name="Thời lượng", value=format_duration(track.length), inline=True)
                    message = await interaction.followup.send(embed=embed, view=MusicButtons())
                    current_playing_message = message.id
                    print(f"Playing track: {track.title}")
                else:
                    embed = discord.Embed(
                        title="Đã thêm vào hàng đợi",
                        description=f"{track.title}",
                        color=discord.Color.blue()
                    )
                    embed.add_field(name="Nguồn", value=track.source, inline=True)
                    embed.add_field(name="Thời lượng", value=format_duration(track.length), inline=True)
                    await interaction.followup.send(embed=embed)
                    print(f"Added to queue: {track.title}")

        except discord.HTTPException as e:
            if e.status == 429:
                embed = discord.Embed(
                    title="Lỗi", description="Đã bị giới hạn (429). Vui lòng thử lại sau vài giây.", color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                print(f"Rate limited (429) on play command. Waiting 5 seconds...")
                await asyncio.sleep(5)
            else:
                embed = discord.Embed(title="Lỗi", description=f"Error: {e}", color=discord.Color.red())
                await interaction.followup.send(embed=embed)
                print(f"Error in play command: {e}")
        except Exception as e:
            embed = discord.Embed(title="Lỗi", description=f"Error: {e}", color=discord.Color.red())
            await interaction.followup.send(embed=embed)
            print(f"Error in play command: {e}")

@bot.tree.command(name="volume", description="Đặt volume (0-100)")
async def volume_slash(interaction: discord.Interaction, volume: int):
    global saved_volume, current_playing_message
    if not interaction.guild.voice_client:
        embed = discord.Embed(
            title="Lỗi", description="Bot không ở trong voice channel!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if not 0 <= volume <= 100:
        embed = discord.Embed(
            title="Lỗi", description="Volume phải từ 0 đến 100!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    player = interaction.guild.voice_client
    saved_volume = volume
    await player.set_volume(saved_volume)
    if player.playing and current_playing_message:
        try:
            message = await interaction.channel.fetch_message(current_playing_message)
            embed = message.embeds[0]
            embed.set_field_at(1, name="Âm lượng", value=f"{saved_volume}%", inline=True)
            await message.edit(embed=embed, view=MusicButtons())
        except:
            embed = discord.Embed(
                title="Đang phát", description=f"{player.current.title}", color=discord.Color.green()
            )
            embed.add_field(name="Nguồn", value=player.current.source, inline=True)
            embed.add_field(name="Âm lượng", value=f"{saved_volume}%", inline=True)
            embed.add_field(name="Thời lượng", value=format_duration(player.current.length), inline=True)
            message = await interaction.channel.send(embed=embed, view=MusicButtons())
            current_playing_message = message.id
    else:
        embed = discord.Embed(
            title="Volume", description=f"Volume đặt thành {saved_volume}%", color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
    await interaction.followup.send(f"Volume đã được đặt thành {saved_volume}%", ephemeral=True, delete_after=5)

@bot.tree.command(name="skip", description="Bỏ qua bài hát hiện tại")
async def skip_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue, is_skipping
    if not interaction.guild.voice_client or is_skipping:
        await interaction.response.send_message("Không thể bỏ qua ngay bây giờ.", ephemeral=True)
        return
    is_skipping = True
    player = interaction.guild.voice_client
    await player.stop()
    current_playing_message = None
    is_skipping = False
    if song_queue:
        await play_next(interaction.channel)
    await interaction.response.send_message("Đã bỏ qua bài hát.", ephemeral=True, delete_after=5)

@bot.tree.command(name="stop", description="Dừng nhạc và xóa queue")
async def stop_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.stop()
        song_queue.clear()
        current_playing_message = None
        embed = discord.Embed(
            title="Dừng", description="Đã dừng nhạc và xóa queue.", color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(
            title="Lỗi", description="Không có nhạc đang phát!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="leave", description="Rời voice channel")
async def leave_slash(interaction: discord.Interaction):
    global current_playing_message, song_queue
    if interaction.guild.voice_client:
        song_queue.clear()
        await interaction.guild.voice_client.disconnect()
        current_playing_message = None
        embed = discord.Embed(title="Rời", description="Đã rời voice channel.", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed)
    else:
        embed = discord.Embed(
            title="Lỗi", description="Bot không ở trong voice channel!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="queue", description="Hiển thị danh sách bài hát trong hàng đợi")
async def queue_slash(interaction: discord.Interaction):
    global song_queue
    await interaction.response.defer(thinking=True)
    view = QueueView(song_queue)
    start_idx = (view.current_page - 1) * view.per_page
    end_idx = min(start_idx + view.per_page, len(song_queue))
    embed = discord.Embed(title="Hàng đợi", color=discord.Color.blue())
    if not song_queue:
        embed.description = "Hàng đợi hiện đang trống!"
    else:
        for i, track in enumerate(list(song_queue)[start_idx:end_idx], start_idx + 1):
            embed.add_field(
                name=f"Bài {i}: {track.title}",
                value=f"Thời lượng: {format_duration(track.length)}",
                inline=False
            )
        embed.set_footer(text=f"Trang {view.current_page}/{view.total_pages}")
    await interaction.followup.send(embed=embed, view=view)
    await interaction.followup.send("Đã hiển thị hàng đợi.", ephemeral=True, delete_after=5)

@bot.tree.command(name="help", description="Hiển thị danh sách lệnh")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(title="Lệnh Music Bot", color=discord.Color.blue())
    for command in bot.tree.get_commands():
        embed.add_field(name=f"/{command.name}", value=command.description, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="loop", description="Loop bài hát hiện tại (không có số: vô hạn, có số: số lần lặp)")
async def loop_slash(interaction: discord.Interaction, times: str = None):
    if not interaction.guild.voice_client or not interaction.guild.voice_client.playing:
        embed = discord.Embed(
            title="Lỗi", description="Không có bài hát đang phát!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    player = interaction.guild.voice_client
    player_id = id(player)
    current_track = player.current
    if not current_track:
        embed = discord.Embed(
            title="Lỗi", description="Không thể xác định bài hát hiện tại!", color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if times is None:
        loop_count[player_id] = float('inf')
        loop_active[player_id] = True
        loop_track[player_id] = current_track
        embed = discord.Embed(
            title="Loop", description=f"Đã bật loop vô hạn cho '{current_track.title}'.", color=discord.Color.blue()
        )
    else:
        try:
            times = int(times)
            if times < 0:
                embed = discord.Embed(
                    title="Lỗi", description="Số lần lặp phải lớn hơn hoặc bằng 0!", color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            loop_count[player_id] = times
            loop_active[player_id] = True
            loop_track[player_id] = current_track
            embed = discord.Embed(
                title="Loop", description=f"Đã bật loop {times} lần cho '{current_track.title}'.", color=discord.Color.blue()
            )
        except ValueError:
            embed = discord.Embed(
                title="Lỗi", description="Vui lòng nhập số nguyên hợp lệ!", color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    bot.run(TOKEN)
