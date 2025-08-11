import discord
from discord.ext import commands
import wavelink
from typing import TYPE_CHECKING, cast, List, Optional
import logging
import math

if TYPE_CHECKING:
    from cogs.music import Music

logger = logging.getLogger(__name__)

class MusicControlView(discord.ui.View):
    """Interactive music control buttons"""
    
    def __init__(self, music_cog: 'Music', player: wavelink.Player):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.music_cog = music_cog
        self.player = player
        
    async def on_timeout(self):
        """Called when view times out"""
        # Disable all buttons
        for item in self.children:
            if hasattr(item, 'disabled'):
                item.disabled = True
            
        try:
            if hasattr(self.player, 'now_playing_message'):
                await self.player.now_playing_message.edit(view=self)
        except Exception as e:
            logger.error(f"Error updating view timeout: {e}")
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if user can interact with buttons"""
        if not interaction.user.voice:
            await interaction.response.send_message("❌ You need to join a voice channel to control the music!", ephemeral=True)
            return False
            
        if interaction.user.voice.channel != self.player.channel:
            await interaction.response.send_message("❌ You must be in the same voice channel as the bot!", ephemeral=True)
            return False
            
        return True
        
    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, label="Play/Pause")
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Pause/Resume button"""
        try:
            if self.player.paused:
                await self.player.pause(False)
                await interaction.response.send_message("▶️ Music resumed!", ephemeral=True)
            elif self.player.playing:
                await self.player.pause(True)
                await interaction.response.send_message("⏸️ Music paused!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No music is playing!", ephemeral=True)
        except Exception as e:
            logger.error(f"Pause/resume error: {e}")
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            
    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, label="Skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Skip button"""
        try:
            if self.player.playing or self.player.paused:
                await self.player.stop()
                await interaction.response.send_message("⏭️ Skipped to next track!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ No music is playing!", ephemeral=True)
        except Exception as e:
            logger.error(f"Skip error: {e}")
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            
    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, label="Stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stop button"""
        try:
            queue = self.music_cog.get_queue(interaction.guild.id)
            queue.clear()
            await self.player.stop()
            await interaction.response.send_message("⏹️ Music stopped and queue cleared!", ephemeral=True)
        except Exception as e:
            logger.error(f"Stop error: {e}")
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
        
    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.secondary, label="View Queue")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Open paginated queue view"""
        try:
            # Get guild voice client and queue
            if not interaction.guild.voice_client:
                await interaction.response.send_message("❌ Bot is not connected to voice!", ephemeral=True)
                return
                
            player = cast(wavelink.Player, interaction.guild.voice_client)
            queue = self.music_cog.get_queue(interaction.guild.id)
            
            # Get current track and queue
            current_track = player.current
            queue_tracks = list(queue.queue)
            
            if not current_track and not queue_tracks:
                await interaction.response.send_message("❌ No music in queue!", ephemeral=True)
                return
                
            # Create paginated view
            queue_view = QueuePaginationView(current_track, queue_tracks)
            embed = queue_view.create_queue_embed(0)
            
            await interaction.response.send_message(embed=embed, view=queue_view, ephemeral=True)
        except Exception as e:
            logger.error(f"Error opening queue view: {e}")
            await interaction.response.send_message("❌ Error opening queue view!", ephemeral=True)
        
    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, label="Volume")
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Volume control button"""
        try:
            # Create volume +/- buttons
            view = VolumeControlView(self.player, self.music_cog, interaction)
            embed = discord.Embed(
                title="🔊 Volume Control", 
                description=f"Current volume: **{self.player.volume}%**\nUse +/- buttons to adjust:",
                color=0x0099ff
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            logger.error(f"Volume button error: {e}")
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, label="Shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Shuffle button"""
        try:
            queue = self.music_cog.get_queue(interaction.guild.id)
            
            if queue.size() < 2:
                await interaction.response.send_message("❌ Need at least 2 songs in queue to shuffle!", ephemeral=True)
                return
                
            queue.shuffle()
            await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)
        except Exception as e:
            logger.error(f"Shuffle error: {e}")
            await interaction.response.send_message("❌ An error occurred!", ephemeral=True)


class VolumeControlView(discord.ui.View):
    """Volume control buttons with +/- adjustment"""
    
    def __init__(self, player: wavelink.Player, music_cog: 'Music', original_interaction: discord.Interaction):
        super().__init__(timeout=60)
        self.player = player
        self.music_cog = music_cog
        self.original_interaction = original_interaction
        
    @discord.ui.button(label="Volume Down", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Decrease volume by 10%"""
        current_volume = self.player.volume
        new_volume = max(0, current_volume - 10)
        await self.set_volume(interaction, new_volume)
        
    @discord.ui.button(label="Volume Up", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Increase volume by 10%"""
        current_volume = self.player.volume
        new_volume = min(100, current_volume + 10)
        await self.set_volume(interaction, new_volume)
        
    async def set_volume(self, interaction: discord.Interaction, volume: int):
        """Set player volume and update embed"""
        try:
            await self.player.set_volume(volume)
            
            # Update the embed with new volume
            embed = discord.Embed(
                title="🔊 Volume Control", 
                description=f"Current volume: **{volume}%**\nUse +/- buttons to adjust:",
                color=0x0099ff
            )
            
            # Update now playing embed if it exists
            if hasattr(self.player, 'now_playing_message') and self.player.now_playing_message:
                try:
                    track = self.player.current
                    if track:
                        now_playing_embed = await self.music_cog.create_now_playing_embed(track, self.player)
                        await self.player.now_playing_message.edit(embed=now_playing_embed)
                except Exception as e:
                    logger.error(f"Failed to update now playing embed: {e}")
            
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Volume error: {e}")
            await interaction.response.send_message("❌ An error occurred setting volume!", ephemeral=True)


class QueueView(discord.ui.View):
    """Extended queue view with pagination"""
    
    def __init__(self, music_cog: 'Music', guild_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.music_cog = music_cog
        self.guild_id = guild_id
        self.page = page
        self.per_page = 10
        
    async def get_embed(self) -> discord.Embed:
        """Get queue embed for current page"""
        queue = self.music_cog.get_queue(self.guild_id)
        embed = discord.Embed(title="📃 Music Queue", color=0x0099ff)
        
        current = queue.get_current()
        if current:
            embed.add_field(
                name="🎵 Now Playing",
                value=f"**[{current.title}]({current.uri})**",
                inline=False
            )
            
        all_tracks = queue.get_upcoming()
        total_pages = (len(all_tracks) + self.per_page - 1) // self.per_page if all_tracks else 0
        
        if total_pages == 0:
            embed.add_field(name="📭", value="Queue is empty", inline=False)
        else:
            start_idx = self.page * self.per_page
            end_idx = min(start_idx + self.per_page, len(all_tracks))
            page_tracks = all_tracks[start_idx:end_idx]
            
            queue_text = ""
            for i, track in enumerate(page_tracks, start_idx + 1):
                queue_text += f"`{i}.` **{track.title}**\n"
                
            embed.add_field(name="⏭️ Up Next", value=queue_text, inline=False)
            embed.set_footer(text=f"Page {self.page + 1}/{total_pages} • Total: {len(all_tracks)} songs")
            
        return embed


class QueuePaginationView(discord.ui.View):
    """Paginated queue view showing 10 songs per page"""
    
    def __init__(self, current_track: Optional[wavelink.Playable], queue_tracks: List[wavelink.Playable]):
        super().__init__(timeout=300)  # 5 minutes timeout
        self.current_track = current_track
        self.queue_tracks = queue_tracks
        self.current_page = 0
        self.songs_per_page = 10
        self.total_pages = max(1, math.ceil(len(queue_tracks) / self.songs_per_page)) if queue_tracks else 1
        
        # Update button states
        self.update_button_states()
    
    def update_button_states(self):
        """Update button disabled states based on current page"""
        # Find buttons and update their states
        for item in self.children:
            if hasattr(item, 'custom_id'):
                if item.custom_id == 'queue_prev':
                    item.disabled = self.current_page <= 0
                elif item.custom_id == 'queue_next':
                    item.disabled = self.current_page >= self.total_pages - 1
    
    def create_queue_embed(self, page: int) -> discord.Embed:
        """Create embed for specific page"""
        embed = discord.Embed(
            title="🎵 Music Queue", 
            color=0x0099ff
        )
        
        # Current playing track
        if self.current_track:
            duration_ms = self.current_track.length or 0
            duration_seconds = duration_ms // 1000
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            
            embed.add_field(
                name="🎵 Now Playing",
                value=f"**[{self.current_track.title}]({self.current_track.uri})**\n"
                      f"👤 {self.current_track.author or 'Unknown'} | ⏱️ {minutes}:{seconds:02d}",
                inline=False
            )
        
        # Queue tracks
        if self.queue_tracks:
            start_idx = page * self.songs_per_page
            end_idx = min(start_idx + self.songs_per_page, len(self.queue_tracks))
            
            queue_text = ""
            for i in range(start_idx, end_idx):
                track = self.queue_tracks[i]
                duration_ms = track.length or 0
                duration_seconds = duration_ms // 1000
                minutes = duration_seconds // 60
                seconds = duration_seconds % 60
                
                queue_text += f"**{i + 1}.** [{track.title}]({track.uri})\n"
                queue_text += f"   👤 {track.author or 'Unknown'} | ⏱️ {minutes}:{seconds:02d}\n\n"
            
            embed.add_field(
                name=f"📋 Queue ({len(self.queue_tracks)} songs)",
                value=queue_text if queue_text else "Queue is empty",
                inline=False
            )
        else:
            embed.add_field(
                name="📋 Queue",
                value="No songs in queue",
                inline=False
            )
        
        # Footer with page info
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {page + 1}/{self.total_pages} • {len(self.queue_tracks)} songs total")
        else:
            embed.set_footer(text=f"{len(self.queue_tracks)} songs total")
        
        return embed
    
    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, label="Previous", custom_id="queue_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_button_states()
            embed = self.create_queue_embed(self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, label="Next", custom_id="queue_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_button_states()
            embed = self.create_queue_embed(self.current_page)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    

    
    async def on_timeout(self):
        """Called when view times out"""
        # Disable all buttons
        for item in self.children:
            if hasattr(item, 'disabled'):
                item.disabled = True