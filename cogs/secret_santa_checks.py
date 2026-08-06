"""
Secret Santa display helpers and re-exports of shared permission checks.

Permission decorators live in bot_checks.py so other cogs do not import
Secret Santa for mod/admin gating.
"""

from __future__ import annotations

from typing import Optional

from .bot_checks import (
    _has_mod_access,
    is_moderator,
    manage_guild_check,
    mod_check,
    safe_display_name,
)

__all__ = [
    "GIFT_EMPTY_DESCRIPTION",
    "GIFT_NO_SUBMISSION_ROW",
    "format_gift_description_for_display",
    "is_moderator",
    "manage_guild_check",
    "mod_check",
    "safe_display_name",
]

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

    Non-empty text is wrapped in inline backticks so a literal joke like "nothing"
    is clearly the participant's wording, not a missing gift.
    """
    if not isinstance(raw, str) or not raw.strip():
        return empty_label
    single_line = " ".join(raw.split())
    if len(single_line) > max_length:
        single_line = single_line[: max_length - 1] + "…"
    if "`" in single_line:
        single_line = single_line.replace("`", "′")
    return f"`{single_line}`"
