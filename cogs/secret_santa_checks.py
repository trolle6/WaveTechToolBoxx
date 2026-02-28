"""
Secret Santa Checks Module - Permission and Validation Functions

RESPONSIBILITIES:
- Permission checks (mod/admin, participant)
- Validation decorators for commands
"""

from __future__ import annotations

from typing import Optional

import disnake
from disnake.ext import commands


def _get_member_from_inter(inter: disnake.ApplicationCommandInteraction) -> Optional[disnake.Member]:
    """Resolve Member from interaction (guild-only). Returns None in DMs or if member not found."""
    if not inter.guild:
        return None
    if isinstance(inter.author, disnake.Member):
        return inter.author
    return inter.guild.get_member(inter.author.id)


def mod_check():
    """Check if user is mod or admin."""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        member = _get_member_from_inter(inter)
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
    """Check if user is administrator (guild-only, fails in DMs)."""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        member = _get_member_from_inter(inter)
        return member.guild_permissions.administrator if member else False

    return commands.check(predicate)


def manage_guild_check():
    """Check if user has manage_guild permission (guild-only, fails in DMs)."""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        member = _get_member_from_inter(inter)
        return member.guild_permissions.manage_guild if member else False

    return commands.check(predicate)


def participant_check():
    """Check if user is a participant"""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        try:
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                return False

            event = cog.state.get("current_event")
            if not event or not isinstance(event, dict) or not event.get("active"):
                return False
            participants = event.get("participants") or {}
            if not isinstance(participants, dict):
                return False
            return str(inter.author.id) in participants
        except Exception:
            return False

    return commands.check(predicate)


def safe_display_name(author: disnake.User | disnake.Member | None) -> str:
    """
    Safely get display_name from User or Member object.
    Returns display_name for Member, name for User, or fallback for None.
    """
    if author is None:
        return "Unknown"
    if isinstance(author, disnake.Member):
        return author.display_name or author.name or "Unknown"
    return getattr(author, "name", None) or "Unknown"
