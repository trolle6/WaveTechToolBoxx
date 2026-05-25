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


def is_moderator(inter: "disnake.ApplicationCommandInteraction") -> bool:
    """Return True if the user can run mod-gated commands (admin or mod role)."""
    member = _get_member_from_inter(inter)
    return _has_mod_access(member, inter.bot) if member else False


def mod_check():
    """Check if user is server admin or has the configured moderator role."""
    async def predicate(inter: "disnake.ApplicationCommandInteraction"):
        member = _get_member_from_inter(inter)
        if member and _has_mod_access(member, inter.bot):
            return True
        if hasattr(inter.bot, "logger"):
            inter.bot.logger.warning(
                f"User {inter.author.name} ({inter.author.id}) attempted to use mod-only command"
            )
        return False

    return commands.check(predicate)


def admin_check():
    """Check if user is administrator (guild-only, fails in DMs). Unused — prefer mod_check()."""
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


# Wording for history/embeds: avoid plain "nothing" / ambiguous empty states
GIFT_EMPTY_DESCRIPTION = "*(no description saved yet)*"
GIFT_NO_SUBMISSION_ROW = "*(no submission on file)*"


def format_gift_description_for_display(
    raw: Optional[str],
    *,
    max_length: int = 200,
    empty_label: str = GIFT_EMPTY_DESCRIPTION,
) -> str:
    """
    Format gift text for Discord embeds.

    Non-empty text is wrapped in inline `` `...` `` so a literal joke like "nothing"
    is clearly the participant's wording, not a missing gift. Empty strings use a
    clear meta label instead of looking like a real gift name.
    """
    if not isinstance(raw, str) or not raw.strip():
        return empty_label
    single_line = " ".join(raw.split())
    if len(single_line) > max_length:
        single_line = single_line[: max_length - 1] + "…"
    if "`" in single_line:
        single_line = single_line.replace("`", "′")
    return f"`{single_line}`"
