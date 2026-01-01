"""
Owner Utilities - Centralized Reference for Bot Owner

This module provides a single source of truth for owner-only commands.
Use this to restrict commands to only the bot owner (trolle6).

USAGE:
    from .owner_utils import owner_check, OWNER_USERNAME
    
    @commands.check(owner_check())
    async def my_command(self, inter):
        # Only trolle6 can use this
        pass
"""

from __future__ import annotations

import disnake
from disnake.ext import commands
from typing import Callable

# Centralized owner username - change this to update all owner restrictions
OWNER_USERNAME = "trolle6"


def owner_check():
    """
    Check if user is the bot owner (trolle6).
    
    Returns a check predicate that can be used with @commands.check()
    
    Example:
        @commands.check(owner_check())
        async def my_command(self, inter):
            # Only trolle6 can use this
            pass
    """
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        user_username = inter.author.name.lower()
        is_owner = user_username == OWNER_USERNAME.lower()
        
        if not is_owner:
            # Log unauthorized attempt
            if hasattr(inter.bot, 'logger'):
                inter.bot.logger.warning(
                    f"User {inter.author.name} ({inter.author.id}) attempted to use owner-only command"
                )
        
        return is_owner
    
    return commands.check(predicate)


def is_owner(inter: "disnake.ApplicationCommandInteraction") -> bool:
    """
    Check if the interaction author is the bot owner.
    
    Use this for inline checks (not as a decorator).
    
    Example:
        if not is_owner(inter):
            await inter.response.send_message("Only the bot owner can use this!")
            return
    """
    user_username = inter.author.name.lower()
    return user_username == OWNER_USERNAME.lower()


def get_owner_mention() -> str:
    """
    Get a formatted mention of the owner username.
    
    Returns:
        String like "**trolle6**" for use in error messages
    """
    return f"**{OWNER_USERNAME}**"

