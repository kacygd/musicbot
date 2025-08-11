import discord
from discord.ext import commands
import wavelink
import asyncio
import logging
import os
from dotenv import load_dotenv
from config import Config

# Load environment variables
load_dotenv()

# Setup logging with Unicode support for Windows
import sys
import io

# Configure logging handlers with proper encoding
log_handlers = []

# Console handler with proper encoding for Windows
if sys.platform.startswith('win'):
    # Windows console with UTF-8 encoding and error replacement
    console_handler = logging.StreamHandler()
    try:
        console_handler.stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except:
        # Fallback to regular handler if wrapping fails
        console_handler = logging.StreamHandler()
else:
    console_handler = logging.StreamHandler()

log_handlers.append(console_handler)

# File handler with UTF-8 encoding
log_handlers.append(logging.FileHandler('bot.log', encoding='utf-8', errors='replace'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(
            command_prefix=Config.PREFIX,
            intents=intents,
            help_command=None
        )
        
    async def setup_hook(self):
        """Setup hook called when bot is starting up"""
        # Load cogs
        await self.load_extension('cogs.music')
        
        # Connect to multiple Lavalink nodes for redundancy
        nodes = []
        for node_config in Config.LAVALINK_NODES:
            try:
                node = wavelink.Node(
                    uri=node_config['uri'],
                    password=node_config['password'],
                    identifier=node_config['identifier']
                )
                nodes.append(node)
                logger.info(f"Added Lavalink node: {node_config['identifier']} ({node_config['uri']})")
            except Exception as e:
                logger.warning(f"Failed to configure node {node_config['identifier']}: {e}")
        
        await wavelink.Pool.connect(nodes=nodes, client=self)
        logger.info("Connected to Lavalink server")
        
    async def on_ready(self):
        """Called when bot is ready"""
        logger.info(f'{self.user} is ready!')
        logger.info(f'Bot is active on {len(self.guilds)} servers')
        
        # Set bot status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="Use /help | GBot Music"
            )
        )
        
        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f'Synced {len(synced)} slash command(s)')
        except Exception as e:
            logger.error(f'Error syncing commands: {e}')
            
        # Auto-deploy notification
        logger.info('Bot is ready for deployment!')
            
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        """Called when a Lavalink node is ready"""
        logger.info(f'Lavalink node {payload.node.identifier} is ready!')
        
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        """Called when a track ends"""
        if payload.reason in ["finished", "stopped"]:
            # Get the music cog and handle next track
            music_cog = self.get_cog('Music')
            if music_cog and hasattr(music_cog, 'play_next') and payload.player:
                await music_cog.play_next(payload.player)

# Create and run bot
async def main():
    bot = MusicBot()
    
    try:
        if Config.BOT_TOKEN:
            await bot.start(Config.BOT_TOKEN)
        else:
            logger.error("Please provide BOT_TOKEN in .env file")
    except discord.LoginFailure:
        logger.error("Invalid bot token!")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

if __name__ == "__main__":
    asyncio.run(main())