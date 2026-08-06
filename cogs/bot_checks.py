"""
Shared Discord permission checks used across cogs.

Kept separate from Secret Santa so DistributeZip, Voice, and SS do not
cross-import feature modules for basic mod/admin gating.
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


def _has_mod_access(member: disnake.Member, bot: disnake.Client) -> bool:
    """True if member is guild admin or has DISCORD_MODERATOR_ROLE_ID from config."""
    if member.guild_permissions.administrator:
        return True
    config = getattr(bot, "config", None)
    if not config:
        return False
    mod_role_id = getattr(config, "DISCORD_MODERATOR_ROLE_ID", None)
    if mod_role_id is None:
        return False
    if not isinstance(mod_role_id, int):
        try:
            mod_role_id = int(mod_role_id)
        except (TypeError, ValueError):
            return False
    return any(r.id == mod_role_id for r in member.roles)


def is_moderator(inter: disnake.ApplicationCommandInteraction) -> bool:
    """Return True if the user can run mod-gated commands (admin or mod role)."""
    member = _get_member_from_inter(inter)
    return _has_mod_access(member, inter.bot) if member else False


def mod_check():
    """Check if user is server admin or has the configured moderator role."""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        member = _get_member_from_inter(inter)
        if member and _has_mod_access(member, inter.bot):
            return True
        if hasattr(inter.bot, "logger"):
            inter.bot.logger.warning(
                "User %s (%s) attempted to use mod-only command",
                inter.author.name,
                inter.author.id,
            )
        return False

    return commands.check(predicate)


def manage_guild_check():
    """Check if user has manage_guild permission (guild-only, fails in DMs)."""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        member = _get_member_from_inter(inter)
        return member.guild_permissions.manage_guild if member else False

    return commands.check(predicate)


def safe_display_name(author: disnake.User | disnake.Member | None) -> str:
    """Display name for Member, username for User, or fallback."""
    if author is None:
        return "Unknown"
    if isinstance(author, disnake.Member):
        return author.display_name or author.name or "Unknown"
    return getattr(author, "name", None) or "Unknown"
