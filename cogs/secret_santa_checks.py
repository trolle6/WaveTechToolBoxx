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
        if inter.author.guild_permissions.administrator:
            return True

        # Check config for mod role
        try:
            if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'DISCORD_MODERATOR_ROLE_ID'):
                mod_role_id = inter.bot.config.DISCORD_MODERATOR_ROLE_ID
                if mod_role_id and any(r.id == mod_role_id for r in inter.author.roles):
                    return True
        except (AttributeError, TypeError):
            pass

        return False

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
