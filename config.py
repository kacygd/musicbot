import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Configuration class for the Discord music bot"""
    
    # Discord Bot Configuration
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    PREFIX = os.getenv('PREFIX', '!')
    
    # Lavalink Configuration - Multiple nodes for redundancy
    LAVALINK_NODES = [
        {
            'uri': os.getenv('LAVALINK_URI_1', 'ws://lavalink.jirayu.net:13592'),
            'password': os.getenv('LAVALINK_PASSWORD_1', 'youshallnotpass'),
            'identifier': 'Node-1'
        },
        {
            'uri': os.getenv('LAVALINK_URI_2', 'ws://lava-all.ajieblogs.eu.org:80'),
            'password': os.getenv('LAVALINK_PASSWORD_2', 'https://dsc.gg/ajidevserver'),
            'identifier': 'Node-2'
        }
    ]
    
    # Bot Settings
    MAX_QUEUE_SIZE = int(os.getenv('MAX_QUEUE_SIZE', '100'))
    DEFAULT_VOLUME = int(os.getenv('DEFAULT_VOLUME', '50'))
    COMMAND_COOLDOWN = int(os.getenv('COMMAND_COOLDOWN', '3'))
    
    # Feature Flags
    ENABLE_SPOTIFY = os.getenv('ENABLE_SPOTIFY', 'false').lower() == 'true'
    ENABLE_SOUNDCLOUD = os.getenv('ENABLE_SOUNDCLOUD', 'false').lower() == 'true'
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        required_vars = ['BOT_TOKEN']
        missing_vars = [var for var in required_vars if not getattr(cls, var)]
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
            
    @classmethod
    def get_all_settings(cls):
        """Get all configuration settings as dict"""
        return {
            'BOT_TOKEN': '***' if cls.BOT_TOKEN else None,
            'PREFIX': cls.PREFIX,
            'LAVALINK_NODES': f"{len(cls.LAVALINK_NODES)} nodes configured",
            'MAX_QUEUE_SIZE': cls.MAX_QUEUE_SIZE,
            'DEFAULT_VOLUME': cls.DEFAULT_VOLUME,
            'COMMAND_COOLDOWN': cls.COMMAND_COOLDOWN,
            'ENABLE_SPOTIFY': cls.ENABLE_SPOTIFY,
            'ENABLE_SOUNDCLOUD': cls.ENABLE_SOUNDCLOUD,
            'LOG_LEVEL': cls.LOG_LEVEL,
        }
