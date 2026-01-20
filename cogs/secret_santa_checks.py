"""
Secret Santa Checks Module - Permission and Validation Functions

RESPONSIBILITIES:
- Permission checks (mod/admin, participant)
- Validation decorators for commands
"""

from __future__ import annotations

import disnake
from disnake.ext import commands


def mod_check():
    """Check if user is mod or admin"""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        # Mod checks only work in guilds (not DMs)
        if not inter.guild:
            return False
        
        # In DMs, inter.author is a User, not a Member
        # We need to get the Member from the guild
        if isinstance(inter.author, disnake.Member):
            member = inter.author
        else:
            # Try to get member from guild
            member = inter.guild.get_member(inter.author.id)
            if not member:
                return False
        
        # Check administrator permission
        if member.guild_permissions.administrator:
            return True

        # Check config for mod role
        try:
            if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'DISCORD_MODERATOR_ROLE_ID'):
                mod_role_id = inter.bot.config.DISCORD_MODERATOR_ROLE_ID
                if mod_role_id and any(r.id == mod_role_id for r in member.roles):
                    return True
        except (AttributeError, TypeError):
            pass

        return False

    return commands.check(predicate)


def admin_check():
    """Check if user is administrator (guild-only, fails in DMs)"""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        # Admin checks only work in guilds (not DMs)
        if not inter.guild:
            return False
        
        # Get Member object
        if isinstance(inter.author, disnake.Member):
            member = inter.author
        else:
            member = inter.guild.get_member(inter.author.id)
            if not member:
                return False
        
        # Check administrator permission
        return member.guild_permissions.administrator

    return commands.check(predicate)


def manage_guild_check():
    """Check if user has manage_guild permission (guild-only, fails in DMs)"""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        # Manage guild checks only work in guilds (not DMs)
        if not inter.guild:
            return False
        
        # Get Member object
        if isinstance(inter.author, disnake.Member):
            member = inter.author
        else:
            member = inter.guild.get_member(inter.author.id)
            if not member:
                return False
        
        # Check manage_guild permission
        return member.guild_permissions.manage_guild

    return commands.check(predicate)


def participant_check():
    """Check if user is a participant"""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        try:
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                return False

            event = cog.state.get("current_event")
            if not event or not event.get("active"):
                return False

            return str(inter.author.id) in event.get("participants", {})
        except Exception:
            return False

    return commands.check(predicate)


def safe_display_name(author: disnake.User | disnake.Member) -> str:
    """
    Safely get display_name from User or Member object.
    Returns display_name for Member, name for User.
    """
    if isinstance(author, disnake.Member):
        return author.display_name
    return author.name
