"""
Production Configuration for Secret Santa Bot
Ensures consistent behavior across environments
"""

import os
import logging
from pathlib import Path

class ProductionConfig:
    """Production configuration with all necessary settings"""
    
    def __init__(self):
        self.setup_logging()
        self.validate_environment()
        self.create_directories()
    
    def setup_logging(self):
        """Setup comprehensive logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('bot.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('bot')
        self.logger.info("Production configuration loaded")
    
    def validate_environment(self):
        """Validate all required environment variables"""
        required_vars = {
            'DISCORD_TOKEN': 'Discord bot token',
            'DISCORD_MODERATOR_ROLE_ID': 'Moderator role ID (optional)',
            'OPENAI_API_KEY': 'OpenAI API key (optional)'
        }
        
        missing_vars = []
        for var, description in required_vars.items():
            if not os.getenv(var):
                if var == 'DISCORD_TOKEN':
                    missing_vars.append(f"{var} ({description})")
                else:
                    self.logger.warning(f"Optional variable {var} not set: {description}")
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")
        
        self.logger.info("Environment variables validated")
    
    def create_directories(self):
        """Create all required directories"""
        directories = [
            'cogs/archive',
            'cogs/archive/backups',
            'logs'
        ]
        
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Created directory: {directory}")
    
    def get_bot_config(self):
        """Get bot configuration"""
        return {
            'token': os.getenv('DISCORD_TOKEN'),
            'moderator_role_id': os.getenv('DISCORD_MODERATOR_ROLE_ID'),
            'openai_api_key': os.getenv('OPENAI_API_KEY'),
            'log_level': 'INFO',
            'backup_interval': 3600,  # 1 hour
            'max_participants': 100,
            'max_wishlist_items': 10
        }

# Global config instance
config = ProductionConfig()
