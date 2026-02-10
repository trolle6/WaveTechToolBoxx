"""
Owner Utilities - Centralized Reference for Bot Owner

This module provides a single source of truth for owner-only commands.
Only the bot owner can use commands decorated with owner_check().
Admins, moderators, and members are rejected.

Identification (in order of use):
  1. If config has BOT_OWNER_USER_ID (integer), use inter.author.id == that (cannot be impersonated).
  2. Else use BOT_OWNER_USERNAME from config or fallback "trolle6" (username can be changed by anyone).

USAGE:
    from .owner_utils import owner_check, get_owner_mention, is_owner
    @owner_check()  # or inline: if not is_owner(inter): return
"""

from __future__ import annotations

import disnake
from disnake.ext import commands

OWNER_USERNAME = "trolle6"  # Fallback if BOT_OWNER_USER_ID not set


def _resolve_owner_check(inter: "disnake.ApplicationCommandInteraction") -> bool:
    """True if inter.author is the configured bot owner (ID takes precedence over username)."""
    config = getattr(inter.bot, "config", None)
    owner_id = getattr(config, "BOT_OWNER_USER_ID", None) if config else None
    if owner_id is not None and isinstance(owner_id, int):
        return inter.author.id == owner_id
    username = getattr(config, "BOT_OWNER_USERNAME", OWNER_USERNAME) if config else OWNER_USERNAME
    if isinstance(username, str):
        return inter.author.name.lower() == username.lower()
    return False


def owner_check():
    """Decorator: only the bot owner can use this command. Admins/mods/members are rejected."""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        ok = _resolve_owner_check(inter)
        if not ok and hasattr(inter.bot, "logger"):
            inter.bot.logger.warning(
                f"User {inter.author.name} ({inter.author.id}) attempted to use owner-only command"
            )
        return ok
    return commands.check(predicate)


def is_owner(inter: "disnake.ApplicationCommandInteraction") -> bool:
    """Return True if the interaction author is the bot owner (for inline checks)."""
    return _resolve_owner_check(inter)


def get_owner_mention() -> str:
    """Get a formatted mention of the owner (username from config or fallback)."""
    return f"**{OWNER_USERNAME}**"  # Could be extended to accept bot and use config.BOT_OWNER_USERNAME
