import discord
from discord.ext import commands
from discord import app_commands
import wavelink
import asyncio
import re
from typing import Optional, cast
import logging
from utils.queue import MusicQueue
from utils.views import MusicControlView, QueuePaginationView

logger = logging.getLogger(__name__)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}  # Guild ID -> MusicQueue
        
    def get_queue(self, guild_id: int) -> MusicQueue:
        """Get or create queue for guild"""
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]
        
    async def ensure_voice(self, interaction: discord.Interaction) -> bool:
        """Ensure user is in voice channel and bot can connect"""
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to join a voice channel first!", ephemeral=True)
            return False
            
        voice_client = cast(wavelink.Player, interaction.guild.voice_client)
        if voice_client and interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message("❌ Bot is being used in another voice channel!", ephemeral=True)
            return False
            
        return True
        
    async def connect_to_voice(self, interaction: discord.Interaction) -> Optional[wavelink.Player]:
        """Connect to voice channel and return player"""
        try:
            if not interaction.guild.voice_client:
                player: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            else:
                player = cast(wavelink.Player, interaction.guild.voice_client)
                
            return player
        except Exception as e:
            logger.error(f"Voice connection error: {e}")
            return None
            
    async def search_track(self, query: str) -> Optional[wavelink.Playable]:
        """Search for track on YouTube"""
        try:
            # Check if it's a URL
            url_pattern = re.compile(
                r'^https?://'  # http:// or https://
                r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
                r'localhost|'  # localhost...
                r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
                r'(?::\d+)?'  # optional port
                r'(?:/?|[/?]\S+)$', re.IGNORECASE)
            
            if url_pattern.match(query):
                tracks = await wavelink.Playable.search(query)
            else:
                tracks = await wavelink.Playable.search(f"ytsearch:{query}")
                
            return tracks[0] if tracks else None
        except Exception as e:
            logger.error(f"Search error: {e}")
            return None
            
    async def create_now_playing_embed(self, track: wavelink.Playable, player: wavelink.Player) -> discord.Embed:
        """Create now playing embed"""
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**[{track.title}]({track.uri})**",
            color=0x00ff00
        )
        
        embed.add_field(name="Artist", value=track.author or "Unknown", inline=True)
        
        # Convert length from milliseconds to mm:ss
        duration_ms = track.length or 0
        duration_seconds = duration_ms // 1000
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        embed.add_field(name="Volume", value=f"{player.volume}%", inline=True)
        
        # Add thumbnail if available
        if hasattr(track, 'artwork') and track.artwork:
            embed.set_thumbnail(url=track.artwork)
            
        return embed
        
    async def play_next(self, player: wavelink.Player):
        """Play next track in queue"""
        try:
            if not player or not player.guild:
                logger.error("Invalid player or guild in play_next")
                return
                
            queue = self.get_queue(player.guild.id)
            next_track = queue.get_next()
            
            if next_track:
                await player.play(next_track)
                # Update now playing message if it exists
                if hasattr(player, 'now_playing_message') and player.now_playing_message:
                    try:
                        embed = await self.create_now_playing_embed(next_track, player)
                        view = MusicControlView(self, player)
                        await player.now_playing_message.edit(embed=embed, view=view)
                    except discord.NotFound:
                        logger.debug("Now playing message not found (possibly deleted)")
                    except discord.HTTPException as e:
                        if e.code == 50027:  # Invalid Webhook Token
                            logger.debug("Webhook token expired, clearing message reference")
                            delattr(player, 'now_playing_message')
                        else:
                            logger.error(f"HTTP error updating message: {e}")
                    except Exception as e:
                        logger.error(f"Error updating message: {e}")
        except Exception as e:
            logger.error(f"Error playing next track: {e}")
            
    @app_commands.command(name="play", description="Play a song from a URL")
    @app_commands.describe(query="Song name, YouTube URL, or playlist URL")
    async def play(self, interaction: discord.Interaction, query: str):
        """Play music from YouTube (supports playlists)"""
        if not await self.ensure_voice(interaction):
            return
            
        await interaction.response.defer()
        
        # Connect to voice and set default volume to 50%
        player = await self.connect_to_voice(interaction)
        if not player:
            await interaction.followup.send("❌ Failed to connect to voice channel!")
            return
            
        # Set default volume to 50% if not set
        if player.volume == 100:  # Default wavelink volume
            await player.set_volume(50)
        
        # Check if it's a playlist URL
        is_playlist = ("playlist?" in query or "list=" in query) and ("youtube.com" in query or "youtu.be" in query)
        
        if is_playlist:
            # Handle playlist
            await self.handle_playlist_load(interaction, player, query)
        else:
            # Handle single track
            track = await self.search_track(query)
            if not track:
                await interaction.followup.send("❌ No songs found!")
                return
                
            queue = self.get_queue(interaction.guild.id)
            
            if player.playing:
                # Add to queue
                queue.add_track(track)
                embed = discord.Embed(
                    title="Added to Queue",
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x0099ff
                )
                embed.add_field(name="Position", value=f"#{queue.size()}", inline=False)
                await interaction.followup.send(embed=embed)
            else:
                # Play immediately
                queue.set_current(track)
                await player.play(track)
                
                embed = await self.create_now_playing_embed(track, player)
                view = MusicControlView(self, player)
                message = await interaction.followup.send(embed=embed, view=view)
                player.now_playing_message = message
                
    async def handle_playlist_load(self, interaction: discord.Interaction, player: wavelink.Player, url: str):
        """Handle playlist loading for both /play and /playlist commands"""
        try:
            # Multiple search methods to ensure playlist loading
            tracks = []
            search_methods = [
                url,  # Direct URL
                f"ytpl:{url}",  # YouTube playlist prefix
                f"ytplaylist:{url}",  # Alternative playlist prefix
            ]
            
            for method in search_methods:
                try:
                    result = await wavelink.Playable.search(method)
                    if result and len(result) > 1:
                        tracks = result
                        logger.info(f"Playlist loaded with method '{method}': {len(tracks)} tracks")
                        break
                    elif result and len(result) == 1 and not tracks:
                        tracks = result  # Keep single track as fallback
                except Exception as e:
                    logger.error(f"Search method '{method}' failed: {e}")
                    continue
            
            if not tracks:
                await interaction.followup.send("❌ No tracks found in playlist! Please check the URL.")
                return
                
            # Limit playlist size to prevent spam (max 50 songs)
            original_count = len(tracks)
            if len(tracks) > 50:
                tracks = tracks[:50]
                
            queue = self.get_queue(interaction.guild.id)
            
            # Add all tracks to queue
            first_track = None
            
            for track in tracks:
                if first_track is None and not player.playing:
                    first_track = track
                    queue.set_current(track)
                else:
                    queue.add_track(track)
            
            # Show success message with detailed info
            embed = discord.Embed(
                title="📋 Playlist Loaded",
                description=f"Successfully loaded **{len(tracks)}** songs from playlist",
                color=0x00ff00
            )
            
            if original_count > 50:
                embed.add_field(
                    name="⚠️ Limited",
                    value=f"Showing first 50 of {original_count} total songs",
                    inline=False
                )
                
            # Show first few tracks
            if len(tracks) > 0:
                track_list = "\n".join([f"`{i+1}.` **{track.title}**" for i, track in enumerate(tracks[:5])])
                if len(tracks) > 5:
                    track_list += f"\n... and **{len(tracks)-5}** more songs"
                embed.add_field(name="🎵 Tracks", value=track_list, inline=False)
            
            # Start playing first track if nothing is playing
            if first_track and not player.playing:
                await player.play(first_track)
                now_playing_embed = await self.create_now_playing_embed(first_track, player)
                view = MusicControlView(self, player)
                    
                message = await interaction.followup.send(
                    embed=embed
                )
                # Send now playing as separate message
                now_playing_msg = await interaction.followup.send(embed=now_playing_embed, view=view)
                player.now_playing_message = now_playing_msg
            else:
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Playlist error: {e}")
            await interaction.followup.send("❌ Failed to load playlist! Please check the URL and try again.")
            

            
    @app_commands.command(name="pause", description="Pause music")
    async def pause(self, interaction: discord.Interaction):
        """Pause music"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or not player.playing:
            await interaction.response.send_message("❌ No music is currently playing!", ephemeral=True)
            return
            
        await player.pause(True)
        await interaction.response.send_message("⏸️ Music paused!")
        
    @app_commands.command(name="resume", description="Resume music")
    async def resume(self, interaction: discord.Interaction):
        """Resume music"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or not player.paused:
            await interaction.response.send_message("❌ Music is not paused!", ephemeral=True)
            return
            
        await player.pause(False)
        await interaction.response.send_message("▶️ Music resumed!")
        
    @app_commands.command(name="stop", description="Stop music and clear queue")
    async def stop(self, interaction: discord.Interaction):
        """Stop music and clear queue"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            await interaction.response.send_message("❌ Bot is not in a voice channel!", ephemeral=True)
            return
            
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        await player.stop()
        await interaction.response.send_message("⏹️ Music stopped and queue cleared!")
        
    @app_commands.command(name="skip", description="Skip songs in queue")
    @app_commands.describe(count="Number of songs to skip (default: 1)")
    async def skip(self, interaction: discord.Interaction, count: int = 1):
        """Skip songs in queue"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or not (player.playing or player.paused):
            await interaction.response.send_message("❌ No music is currently playing!", ephemeral=True)
            return
            
        # Validate skip count
        if count < 1:
            await interaction.response.send_message("❌ Skip count must be at least 1!", ephemeral=True)
            return
            
        queue = self.get_queue(interaction.guild.id)
        
        # If skipping more than 1, remove additional tracks from queue
        if count > 1:
            skipped_count = 1  # Current track
            for _ in range(count - 1):
                if not queue.is_empty():
                    queue.get_next()  # Remove from queue
                    skipped_count += 1
                else:
                    break
            
            if skipped_count < count:
                await interaction.response.send_message(f"⏭️ Skipped {skipped_count} songs (only {skipped_count} available)")
            else:
                await interaction.response.send_message(f"⏭️ Skipped {count} songs!")
        else:
            await interaction.response.send_message("⏭️ Skipped to next track!")
        
        # Stop current track to trigger next song
        await player.stop()
        
    @app_commands.command(name="volume", description="Set volume")
    @app_commands.describe(volume="Volume from 0 to 100")
    async def volume(self, interaction: discord.Interaction, volume: int):
        """Set volume"""
        if not 0 <= volume <= 100:
            await interaction.response.send_message("❌ Volume must be between 0 and 100!", ephemeral=True)
            return
            
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            await interaction.response.send_message("❌ Bot is not in a voice channel!", ephemeral=True)
            return
            
        await player.set_volume(volume)
        await interaction.response.send_message(f"🔊 Volume set to {volume}%")
        
    @app_commands.command(name="queue", description="Show music queue")
    async def queue(self, interaction: discord.Interaction):
        """Show music queue"""
        queue = self.get_queue(interaction.guild.id)
        
        if queue.is_empty():
            await interaction.response.send_message("Queue is empty!")
            return
            
        embed = discord.Embed(title="Music Queue", color=0x0099ff)
        
        current = queue.get_current()
        if current:
            embed.add_field(
                name="🎵 Now Playing",
                value=f"**[{current.title}]({current.uri})**",
                inline=False
            )
            
        tracks = queue.get_upcoming(10)  # Show next 10 tracks
        if tracks:
            queue_text = ""
            for i, track in enumerate(tracks, 1):
                queue_text += f"`{i}.` **[{track.title}]({track.uri})**\n"
            embed.add_field(name="⏭️ Up Next", value=queue_text, inline=False)
            
        embed.set_footer(text=f"Total: {queue.size()} songs")
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="nowplaying", description="Show current song info")
    async def nowplaying(self, interaction: discord.Interaction):
        """Show now playing info"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player or not (player.playing or player.paused):
            await interaction.response.send_message("❌ No music is currently playing!", ephemeral=True)
            return
            
        track = player.current
        if not track:
            await interaction.response.send_message("❌ No current track!", ephemeral=True)
            return
            
        embed = await self.create_now_playing_embed(track, player)
        view = MusicControlView(self, player)
        await interaction.response.send_message(embed=embed, view=view)
        
    @app_commands.command(name="disconnect", description="Disconnect bot from voice channel")
    async def disconnect(self, interaction: discord.Interaction):
        """Disconnect from voice channel"""
        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)
        if not player:
            await interaction.response.send_message("❌ Bot is not in a voice channel!", ephemeral=True)
            return
            
        queue = self.get_queue(interaction.guild.id)
        queue.clear()
        await player.disconnect()
        await interaction.response.send_message("Disconnected!")
        
    @app_commands.command(name="search", description="Search for songs without playing")
    @app_commands.describe(query="Song name or YouTube URL to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        """Search for songs without playing"""
        await interaction.response.defer()
        
        try:
            # Search for multiple tracks
            if query.startswith("http"):
                tracks = await wavelink.Playable.search(query)
            else:
                tracks = await wavelink.Playable.search(f"ytsearch:{query}")
                
            if not tracks:
                await interaction.followup.send("❌ No songs found!")
                return
                
            embed = discord.Embed(title="🔍 Search Results", color=0x0099ff)
            
            # Show first 10 results
            results = tracks[:10] if len(tracks) > 10 else tracks
            search_text = ""
            for i, track in enumerate(results, 1):
                duration_ms = track.length or 0
                duration_seconds = duration_ms // 1000
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                search_text += f"`{i}.` **[{track.title}]({track.uri})** - `{minutes}:{seconds:02d}`\n"
                
            embed.add_field(name="Results", value=search_text, inline=False)
            embed.set_footer(text=f"Found {len(tracks)} results • Use /play to add songs to queue")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            await interaction.followup.send("❌ Search failed!")
            
    @app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        """Join voice channel"""
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to join a voice channel first!", ephemeral=True)
            return
            
        if interaction.guild.voice_client:
            await interaction.response.send_message("❌ Bot is already connected to a voice channel!", ephemeral=True)
            return
            
        try:
            player: wavelink.Player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
            await player.set_volume(50)  # Set default volume to 50%
            await interaction.response.send_message(f"✅ Joined **{interaction.user.voice.channel.name}**!")
        except Exception as e:
            logger.error(f"Join error: {e}")
            await interaction.response.send_message("❌ Failed to join voice channel!")
            
    @app_commands.command(name="help", description="Show bot commands and usage")
    async def help(self, interaction: discord.Interaction):
        """Show help information"""
        embed = discord.Embed(
            title="🎵 Music Bot Help",
            description="A comprehensive music bot with YouTube playbook and interactive controls.",
            color=0x0099ff
        )
        
        # Music Commands
        embed.add_field(
            name="🎵 Music Commands",
            value=(
                "`/play <query>` - Play music or playlists from YouTube\n"
                "`/pause` - Pause current music\n"
                "`/resume` - Resume paused music\n" 
                "`/skip [count]` - Skip songs (default: 1)\n"
                "`/stop` - Stop music and clear queue\n"
                "`/volume <0-100>` - Set volume level\n"
                "`/nowplaying` - Show current song info"
            ),
            inline=False
        )
        
        # Queue Commands
        embed.add_field(
            name="📃 Queue Commands",
            value=(
                "`/queue` - Show music queue\n"
                "`/search <query>` - Search for songs\n"
            ),
            inline=False
        )
        
        # Control Commands  
        embed.add_field(
            name="🎛️ Control Commands",
            value=(
                "`/join` - Join your voice channel\n"
                "`/disconnect` - Leave voice channel\n"
                "`/ping` - Show bot and Lavalink latency\n"
                "`/help` - Show this help message"
            ),
            inline=False
        )
        
        # Interactive Controls
        embed.add_field(
            name="🎮 Interactive Controls",
            value=(
                "⏯️ Play/Pause • ⏭️ Skip • ⏹️ Stop\n"
                "📃 Queue • 🔊 Volume +/- • 🔀 Shuffle"
            ),
            inline=False
        )
        
        embed.set_footer(text="Bot powered by Wavelink & Lavalink • Default volume: 50%")
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="queue", description="Show the current music queue")
    async def queue(self, interaction: discord.Interaction):
        """Show the current music queue with pagination"""
        if not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Bot is not connected to voice!", ephemeral=True)
            return
            
        player = cast(wavelink.Player, interaction.guild.voice_client)
        queue = self.get_queue(interaction.guild.id)
        
        # Get current track and queue
        current_track = player.current
        queue_tracks = list(queue.queue)
        
        if not current_track and not queue_tracks:
            await interaction.response.send_message("❌ No music in queue!", ephemeral=True)
            return
            
        # Create paginated view
        view = QueuePaginationView(current_track, queue_tracks)
        embed = view.create_queue_embed(0)
        
        await interaction.response.send_message(embed=embed, view=view)
        
    @app_commands.command(name="ping", description="Show bot latency")
    async def ping(self, interaction: discord.Interaction):
        """Show bot and Lavalink latency"""
        try:
            bot_latency = round(self.bot.latency * 1000)
            
            # Get active Lavalink nodes
            embed = discord.Embed(title="Ping", color=0x0099ff)
            embed.add_field(name="Bot Latency", value=f"{bot_latency}ms", inline=True)
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logger.error(f"Ping command error: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Error getting ping information!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Music(bot))