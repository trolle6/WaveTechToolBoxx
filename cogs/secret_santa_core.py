"""Secret Santa core: state, helpers, lifecycle (no slash commands). See SECRET_SANTA_COMMANDS.md."""

from __future__ import annotations

import aiohttp
import asyncio
import hashlib
import datetime as dt
import re
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import disnake
from disnake.ext import commands

from .utils import (
    RateLimiter,
    autocomplete_safety_wrapper,
    get_openai_headers,
    safe_edit_response,
    safe_followup_send,
)

# Import from modular components
from .secret_santa_storage import (
    ARCHIVE_DIR, BACKUPS_DIR, STATE_FILE,
    load_state_with_fallback, save_state, load_all_archives, archive_event,
    load_json, save_json, get_default_state
)
from .secret_santa_assignments import (
    load_history_from_archives, validate_assignment_possibility, make_assignments
)
from .secret_santa_views import (
    SecretSantaReplyView, SecretSantaReplyModal, YearHistoryPaginator,
    CommunicationsPaginator, YearTimelinePaginator, BackupListPaginator
)
from .secret_santa_checks import (
    GIFT_NO_SUBMISSION_ROW,
    format_gift_description_for_display,
    mod_check,
    safe_display_name,
)

# Constants
BACKUP_INTERVAL_SECONDS = 3600  # 1 hour - how often to backup state
# How often to re-check for scheduled shuffle/stop: when idle we sleep this long; when we have a
# schedule we sleep until that time but at most this long. No per-tick logging.
SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS = 300  # 5 minutes
# DM rate limiting: Discord throttles DMs. Space them out to avoid 429.
DM_DELAY_SECONDS = 1.2  # Delay between each DM to stay under rate limits
DM_MAX_RETRIES = 4  # Retry on 429, 5xx, connection errors (1 initial + 3 retries)
DM_FETCH_TIMEOUT = 10  # Timeout for fetch_user (seconds)
DM_SEND_TIMEOUT = 15  # Timeout for user.send (seconds)

# Anonymization API
ANONYMIZE_RETRY_MAX = 3  # Retries for OpenAI API (429, 5xx, connection)
ANONYMIZE_RETRY_BASE_DELAY = 1.0  # Exponential backoff base
ANONYMIZE_TIMEOUT = 20  # Request timeout (seconds)

# Participant spam guards — sliding window (NOT a cooldown between each action).
# Example: SS_ASK 10 / 600s = up to 10 questions in any 10-minute period, back-to-back OK.
SS_ASK_RATE_LIMIT = 10
SS_ASK_RATE_WINDOW = 600
SS_REPLY_RATE_LIMIT = 15
SS_REPLY_RATE_WINDOW = 600
SS_WISHLIST_RATE_LIMIT = 30
SS_WISHLIST_RATE_WINDOW = 60
SS_JOIN_DM_RATE_LIMIT = 5  # only blocks react spam re-join DMs, not /ss start batch
SS_JOIN_DM_RATE_WINDOW = 600
SS_MAX_COMMS_PER_PAIR = 40  # messages per Santa↔giftee thread per event

_SCRUB_MENTION_RE = re.compile(r"<@!?\d+>")
_SCRUB_ROLE_MENTION_RE = re.compile(r"<@&\d+>")
_SCRUB_DISCORD_USER_URL_RE = re.compile(r"https?://(?:www\.)?discord(?:app)?\.com/users/\d+", re.I)
_SCRUB_EVERYONE_RE = re.compile(r"@(?:everyone|here)\b", re.I)

# Discord locale (language) -> IANA timezone for parsing /ss start shuffle & end times.
# Discord doesn't provide timezone, so this is a best-effort guess from their app language.
DISCORD_LOCALE_TO_IANA: Dict[str, str] = {
    "id": "Asia/Jakarta",
    "da": "Europe/Copenhagen",
    "de": "Europe/Berlin",
    "en-GB": "Europe/London",
    "en-US": "America/New_York",
    "es-ES": "Europe/Madrid",
    "es-419": "America/Mexico_City",
    "fr": "Europe/Paris",
    "hr": "Europe/Zagreb",
    "it": "Europe/Rome",
    "lt": "Europe/Vilnius",
    "hu": "Europe/Budapest",
    "nl": "Europe/Amsterdam",
    "no": "Europe/Oslo",
    "pl": "Europe/Warsaw",
    "pt-BR": "America/Sao_Paulo",
    "ro": "Europe/Bucharest",
    "fi": "Europe/Helsinki",
    "sv-SE": "Europe/Stockholm",
    "vi": "Asia/Ho_Chi_Minh",
    "tr": "Europe/Istanbul",
    "cs": "Europe/Prague",
    "el": "Europe/Athens",
    "bg": "Europe/Sofia",
    "ru": "Europe/Moscow",
    "uk": "Europe/Kyiv",
    "hi": "Asia/Kolkata",
    "th": "Asia/Bangkok",
    "zh-CN": "Asia/Shanghai",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
}


class SecretSantaCore(commands.Cog):
    """Secret Santa event management"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("santa")

        # Placeholder until cog_load loads real state (avoids blocking event loop at startup)
        self.state = get_default_state()
        self._lock = asyncio.Lock()
        self._backup_task: Optional[asyncio.Task] = None
        self._scheduled_shuffle_task: Optional[asyncio.Task] = None
        self._unloaded = False  # Track if already unloaded
        self._executor = bot.executor  # Shared executor from main.py (bot is self.bot)

        self._limit_ask = RateLimiter(limit=SS_ASK_RATE_LIMIT, window=SS_ASK_RATE_WINDOW)
        self._limit_reply = RateLimiter(limit=SS_REPLY_RATE_LIMIT, window=SS_REPLY_RATE_WINDOW)
        self._limit_wishlist = RateLimiter(limit=SS_WISHLIST_RATE_LIMIT, window=SS_WISHLIST_RATE_WINDOW)
        self._limit_join_dm = RateLimiter(limit=SS_JOIN_DM_RATE_LIMIT, window=SS_JOIN_DM_RATE_WINDOW)
        
        self.logger.info("Secret Santa cog initialized with persistent reply buttons")
    
    async def _safe_defer(self, inter: disnake.ApplicationCommandInteraction, ephemeral: bool = True) -> bool:
        """
        Safely defer an interaction, handling expired interactions gracefully.
        
        Args:
            inter: The interaction to defer
            ephemeral: Whether the response should be ephemeral (default: True)
        
        Returns:
            True if defer was successful, False if interaction expired
        """
        try:
            await inter.response.defer(ephemeral=ephemeral)
            return True
        except disnake.errors.NotFound:
            # Interaction expired (404 Not Found - Unknown interaction)
            # This can happen if there's network latency or the bot is slow
            self.logger.warning(f"Interaction expired before defer: {inter.id} (command: {inter.application_command.name})")
            return False
        except disnake.errors.InteractionResponded:
            # Already responded to - this is fine, just return True
            return True
        except Exception as e:
            # Other errors - log but don't crash
            self.logger.error(f"Error deferring interaction: {e}", exc_info=True)
            return False
    
    async def _safe_edit_response(
        self,
        inter: disnake.ApplicationCommandInteraction,
        content: Optional[str] = None,
        embed: Optional[disnake.Embed] = None,
        view: Optional[disnake.ui.View] = None,
        file: Optional[disnake.File] = None,
        max_retries: int = 3
    ) -> bool:
        """Safely edit interaction response with shared retry logic."""
        return await safe_edit_response(
            self.logger, inter,
            content=content, embed=embed, view=view, file=file,
            max_retries=max_retries,
        )
    
    async def _safe_followup_send(
        self,
        inter: disnake.ApplicationCommandInteraction,
        content: Optional[str] = None,
        embed: Optional[disnake.Embed] = None,
        view: Optional[disnake.ui.View] = None,
        file: Optional[disnake.File] = None,
        ephemeral: bool = False,
        max_retries: int = 3
    ) -> Optional[disnake.WebhookMessage]:
        """Safely send followup message with shared retry logic."""
        return await safe_followup_send(
            self.logger, inter,
            content=content, embed=embed, view=view, file=file,
            ephemeral=ephemeral, max_retries=max_retries,
        )
    
    def _create_embed(self, title: str, description: str, color: disnake.Color, **fields) -> disnake.Embed:
        """
        Helper to create embeds with consistent formatting.
        Reduces duplication in command responses.
        
        Args:
            title: Embed title
            description: Embed description
            color: Embed color
            **fields: Optional named fields to add (name=value pairs), special 'footer' key sets footer
        
        Returns:
            Configured embed
        """
        embed = disnake.Embed(title=title, description=description, color=color)
        footer = fields.pop('footer', None)
        if footer:
            embed.set_footer(text=footer)
        for field_name, field_value in fields.items():
            if isinstance(field_value, tuple):
                # Support (value, inline) tuples
                embed.add_field(name=field_name, value=field_value[0], inline=field_value[1])
            else:
                embed.add_field(name=field_name, value=field_value, inline=False)
        return embed
    
    def _get_current_event(self) -> Optional[dict]:
        """Get active event with validation. Returns event dict if active, None otherwise"""
        event = self.state.get("current_event")
        return event if isinstance(event, dict) and event.get("active") else None
    
    def _get_available_years(self) -> List[int]:
        """Get list of available years from archive directory - excludes backup files (synchronous)"""
        years = []
        for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
            # Skip files in backups subdirectory
            if "backups" in archive_file.parts:
                continue
            # Skip backup files (files with "_backup_" in the name)
            if "_backup_" in archive_file.name.lower():
                continue
            year_str = archive_file.stem
            # Only include files that are exactly 4 digits (year format)
            if year_str.isdigit() and len(year_str) == 4:
                try:
                    years.append(int(year_str))
                except ValueError:
                    continue
        return sorted(years, reverse=True)  # Most recent first
    
    async def _get_available_years_async(self) -> List[int]:
        """Get list of available years asynchronously (non-blocking)"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._get_available_years)
    
    def _ensure_list_result(self, result: Any, function_name: str) -> List[str]:
        """Universal safety wrapper - ensures autocomplete always returns a list"""
        if isinstance(result, list):
            return [str(item) for item in result]  # Ensure all items are strings
        elif result is None:
            return []
        elif isinstance(result, str):
            self.logger.error(f"{function_name} returned string instead of list: '{result}'")
            return []
        else:
            try:
                return list(result) if result else []
            except Exception as e:
                self.logger.error(f"{function_name} returned invalid type: {type(result)}, error={e}")
                return []
    
    async def _autocomplete_year(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete function for year selection - shows available years (non-blocking)"""
        try:
            # Use async version to avoid blocking event loop on file system operations
            available_years = await self._get_available_years_async()
            if not available_years:
                return []  # Return empty list instead of error message for autocomplete
            
            # Filter years that match the input string
            string_lower = string.lower() if string else ""
            matching_years = [
                str(year) for year in available_years 
                if string_lower in str(year) or not string
            ]
            
            # Return up to 25 options (Discord limit)
            result = matching_years[:25]
            return self._ensure_list_result(result, "_autocomplete_year")
        except Exception as e:
            self.logger.error(f"Error in year autocomplete: {e}", exc_info=True)
            return []  # Always return a list, even on error
    
    # Autocomplete methods - registered via string references in Param()
    async def autocomplete_year_edit_gift(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for edit_gift year parameter"""
        try:
            result = await self._autocomplete_year(inter, string)
            return self._ensure_list_result(result, "autocomplete_year_edit_gift")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_year_edit_gift: {e}", exc_info=True)
            return []
    
    async def autocomplete_year_history(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for history year parameter"""
        try:
            result = await self._autocomplete_year(inter, string)
            return self._ensure_list_result(result, "autocomplete_year_history")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_year_history: {e}", exc_info=True)
            return []
    
    async def autocomplete_year_delete(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for delete_year year parameter"""
        try:
            result = await self._autocomplete_year(inter, string)
            return self._ensure_list_result(result, "autocomplete_year_delete")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_year_delete: {e}", exc_info=True)
            return []
    
    async def autocomplete_year_restore(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for restore_year year parameter"""
        try:
            result = await self._autocomplete_year(inter, string)
            return self._ensure_list_result(result, "autocomplete_year_restore")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_year_restore: {e}", exc_info=True)
            return []
    
    async def autocomplete_wishlist_item_number(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for wishlist remove item_number - shows valid item numbers from user's wishlist"""
        try:
            event = self._get_current_event()
            if not event or not event.get("active"):
                return []
            
            user_id = str(inter.author.id)
            participants = event.get("participants") or {}
            if not isinstance(participants, dict) or user_id not in participants:
                return []
            wishlists = event.get("wishlists") or {}
            if not isinstance(wishlists, dict):
                return []
            user_wishlist = wishlists.get(user_id)
            if not isinstance(user_wishlist, list):
                user_wishlist = []
            if not user_wishlist:
                return []
            
            valid_numbers = [str(i + 1) for i in range(len(user_wishlist))]
            
            if string:
                valid_numbers = [num for num in valid_numbers if string in num]
            
            result = valid_numbers[:25]
            return self._ensure_list_result(result, "autocomplete_wishlist_item_number")
        except Exception as e:
            self.logger.error(f"Error in wishlist autocomplete: {e}", exc_info=True)
            return []
    
    async def _validate_participant(self, inter: disnake.ApplicationCommandInteraction) -> Optional[tuple]:
        """
        Validate user is participant in active event.
        Returns (event, user_id) if valid, None otherwise (sends error response).
        COMBINED: Gets event + checks participants in one pass.
        """
        event = self._get_current_event()
        if not event:
            await self._safe_edit_response(inter, content="There's no Secret Santa event running right now. Maybe soon!")
            return None
        
        user_id = str(inter.author.id)
        participants = event.get("participants") or {}
        if not isinstance(participants, dict) or user_id not in participants:
            await self._safe_edit_response(inter, content="Hmm, it doesn't look like you're signed up for Secret Santa this year!")
            return None
        return (event, user_id)
    
    async def _validate_participant_with_assignment(self, inter: disnake.ApplicationCommandInteraction) -> Optional[tuple]:
        """
        COMBINED VALIDATION: Validate participant AND check assignment in one pass.
        Returns (event, user_id, receiver_id, participants, assignments) if valid, None otherwise.
        This combines 3 separate checks into 1 for efficiency.
        """
        event = self._get_current_event()
        if not event:
            await self._safe_edit_response(inter, content="There's no Secret Santa event running right now. Maybe soon!")
            return None
        
        user_id = str(inter.author.id)
        participants = event.get("participants") or {}
        assignments = event.get("assignments") or {}
        if not isinstance(participants, dict):
            await self._safe_edit_response(inter, content="Hmm, it doesn't look like you're signed up for Secret Santa this year!")
            return None
        if user_id not in participants:
            await self._safe_edit_response(inter, content="Hmm, it doesn't look like you're signed up for Secret Santa this year!")
            return None
        receiver_id = assignments.get(user_id)
        if not receiver_id:
            embed = self._error_embed(
                title="⏳ No giftee yet",
                description=(
                    "The organizer hasn't run `/ss shuffle` yet.\n\n"
                    "After shuffle you'll get a DM with your match. "
                    "Until then: `/ss wishlist add` for your own list."
                ),
            )
            await self._safe_edit_response(inter, embed=embed)
            return None
        
        return (event, user_id, receiver_id, participants, assignments)
    
    def _error_embed(self, title: str, description: str, footer: Optional[str] = None) -> disnake.Embed:
        """Create a standard error embed"""
        return self._create_embed(title, description, disnake.Color.red(), **({"footer": footer} if footer else {}))
    
    def _success_embed(self, title: str, description: str, footer: Optional[str] = None) -> disnake.Embed:
        """Create a standard success embed"""
        return self._create_embed(title, description, disnake.Color.green(), **({"footer": footer} if footer else {}))
    
    def _truncate_text(self, text: Optional[str], max_length: int = 100) -> str:
        """Truncate text with ellipsis if needed. Handles None and non-string safely."""
        if text is None or not isinstance(text, str):
            return ""
        return f"{text[:max_length]}..." if len(text) > max_length else text
    
    async def _require_event(self, inter: disnake.ApplicationCommandInteraction, custom_message: Optional[str] = None) -> Optional[dict]:
        """Require active event. Returns event if active, None otherwise (sends error response)"""
        event = self._get_current_event()
        if not event:
            msg = custom_message or "❌ No active event"
            await self._safe_edit_response(inter, content=msg)
            return None
        return event
    
    async def _check_assignment(self, inter: disnake.ApplicationCommandInteraction, event: dict, user_id: str) -> Optional[str]:
        """Check if user has assignment. Returns receiver_id if valid, None otherwise (sends error response)"""
        assignments = event.get("assignments") or {}
        if not isinstance(assignments, dict) or user_id not in assignments:
            embed = self._error_embed(
                title="⏳ No giftee yet",
                description=(
                    "Wait for `/ss shuffle` — you'll get a DM with who your match is."
                ),
            )
            await self._safe_edit_response(inter, embed=embed)
            return None
        return assignments.get(user_id)

    def _find_santa_for_giftee(self, event: dict, giftee_id: str) -> Optional[int]:
        """Find the Santa (giver) for a given giftee (receiver). Returns santa_id as int, or None"""
        assignments = event.get("assignments") or {}
        if not isinstance(assignments, dict):
            return None
        giftee_str = str(giftee_id)
        for giver, receiver in assignments.items():
            if str(receiver) == giftee_str:
                try:
                    return int(giver)
                except (TypeError, ValueError):
                    continue
        return None
    
    # Comms flow: ask_giftee / reply_santa / _process_reply → _save_communication → event["communications"].
    # structure: { santa_id: { "giftee_id": giftee_id, "thread": [ { type, message, rewritten, timestamp } ] } }.
    # On stop, full event (including communications) is archived to archive/YYYY.json. view_comms reads active event only.
    def _scrub_user_text(self, text: str) -> str:
        """Remove Discord pings/URLs that could deanonymize participants."""
        if not text or not isinstance(text, str):
            return ""
        cleaned = _SCRUB_MENTION_RE.sub("", text)
        cleaned = _SCRUB_ROLE_MENTION_RE.sub("", cleaned)
        cleaned = _SCRUB_DISCORD_USER_URL_RE.sub("", cleaned)
        cleaned = _SCRUB_EVERYONE_RE.sub("", cleaned)
        return cleaned.strip()

    async def _rate_limit_user(
        self,
        inter: disnake.Interaction,
        limiter: RateLimiter,
        action_label: str,
    ) -> bool:
        """Return True if allowed; otherwise send ephemeral rate-limit message."""
        if await limiter.check(str(inter.author.id)):
            return True
        window_min = max(1, limiter.window // 60)
        msg = (
            f"⏳ **{action_label.title()} limit reached** — "
            f"you can send up to **{limiter.limit}** per **{window_min} minutes** "
            f"(you can use them back-to-back; this is not a wait between each message). "
            f"Try again in a minute or two once older ones roll off."
        )
        try:
            if inter.response.is_done():
                await inter.followup.send(msg, ephemeral=True)
            else:
                await inter.response.send_message(msg, ephemeral=True)
        except disnake.HTTPException:
            pass
        return False

    def _comms_thread_length(self, event: dict, santa_id: str) -> int:
        comms = event.get("communications") or {}
        entry = comms.get(str(santa_id)) if isinstance(comms, dict) else None
        if not isinstance(entry, dict):
            return 0
        thread = entry.get("thread")
        return len(thread) if isinstance(thread, list) else 0

    async def _check_comms_cap(
        self,
        inter: disnake.Interaction,
        event: dict,
        santa_id: str,
    ) -> bool:
        if self._comms_thread_length(event, santa_id) < SS_MAX_COMMS_PER_PAIR:
            return True
        msg = (
            f"⏳ This conversation hit the cap (**{SS_MAX_COMMS_PER_PAIR}** questions+replies total for the event). "
            "Use `/ss giftee` or ask the organizer if you need more."
        )
        try:
            if inter.response.is_done():
                await inter.followup.send(msg, ephemeral=True)
            else:
                await inter.response.send_message(msg, ephemeral=True)
        except disnake.HTTPException:
            pass
        return False

    async def _save_communication(self, event: dict, santa_id: str, giftee_id: str, msg_type: str,
                                  message: str, rewritten: str):
        """Save communication thread entry. comms keyed by santa_id; each has giftee_id and thread list."""
        async with self._lock:
            comms = event.get("communications")
            if not isinstance(comms, dict):
                comms = {}
                event["communications"] = comms
            entry = comms.get(santa_id)
            if not isinstance(entry, dict):
                entry = {"giftee_id": giftee_id, "thread": []}
                comms[santa_id] = entry
            thread_list = entry.get("thread")
            if not isinstance(thread_list, list):
                thread_list = []
                entry["thread"] = thread_list
            thread_list.append({
                "type": str(msg_type) if msg_type else "message",
                "message": str(message) if message is not None else "",
                "rewritten": str(rewritten) if rewritten is not None else "",
                "timestamp": time.time()
            })
            await self._save_async()
    
    def _format_dm_question(self, rewritten_question: str, year: int) -> str:
        """DM to giftee — speak to *you*; never @mention the reader (no self-ping)."""
        return (
            f"❓ **Secret Santa {year}** — question from your Santa\n\n"
            f"*\"{rewritten_question}\"*\n\n"
            f"Answer with the **Reply to Santa** button on this message."
        )

    def _format_dm_reply(self, rewritten_reply: str, year: int, giftee_id: Optional[int] = None) -> str:
        """DM to Santa — @mention the giftee once; Santa is always *you*, never @mentioned."""
        match_line = (
            f"<@{giftee_id}> replied:\n\n" if giftee_id else "Your match replied:\n\n"
        )
        return (
            f"🎅 **Secret Santa {year}**\n\n"
            f"{match_line}"
            f"*\"{rewritten_reply}\"*\n\n"
            f"Ask another question: `/ss ask_giftee`"
        )

    def _get_join_message(self, year: int) -> str:
        """DM when someone joins — *you* only; no @mentions until shuffle."""
        return (
            f"🎄 **Secret Santa {year}** — you're in\n\n"
            f"You're signed up. After the shuffle, you'll get **another DM here** "
            f"with who your match is.\n\n"
            f"**Useful now:**\n"
            f"• `/ss wishlist add` — ideas for your Santa\n"
            f"• `/ss wishlist view` — see your list\n\n"
            f"Keep your match secret once you know who it is."
        )

    def _get_assignment_message(self, year: int, receiver_id: int, receiver_name: str) -> str:
        """DM to Santa (giver): one @mention of giftee (receiver); never @mention the Santa."""
        match = f"<@{receiver_id}>"
        return (
            f"🎁 **Secret Santa {year}** — your match\n\n"
            f"You're Secret Santa for **{match}**.\n\n"
            f"**Commands:**\n"
            f"• `/ss giftee` — your match's wishlist\n"
            f"• `/ss ask_giftee` — ask your match (anonymous)\n"
            f"• `/ss wishlist add` / `view` — your wishlist\n"
            f"• `/ss submit_gift` — log what you sent (optional)\n\n"
            f"Your match can answer with **Reply to Santa** on question DMs.\n"
            f"Don't reveal who your match is in the server."
        )
    
    def _get_event_end_message(self, year: int) -> str:
        """DM when the event stops — brief wrap-up."""
        return (
            f"🎄 **Secret Santa {year}** — event ended\n\n"
            f"Thanks for taking part! This year's event is archived.\n\n"
            f"• `/ss history` — browse past years\n"
            f"• `/ss edit_gift` — fix your gift note for a past year"
        )

    def _get_leave_message(self, year: int) -> str:
        """DM when someone removes their react / leaves before shuffle."""
        return (
            f"👋 **Secret Santa {year}** — you left the event\n\n"
            f"You're no longer on the participant list.\n\n"
            f"Changed your mind? React on the signup message again **before** the shuffle."
        )

    async def _resolve_guild_member(
        self, guild: disnake.Guild, user_id: int
    ) -> Optional[disnake.Member]:
        """Return guild member, fetching from API if not in cache."""
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (disnake.NotFound, disnake.HTTPException) as e:
            self.logger.warning(f"Could not fetch member {user_id} in guild {guild.id}: {e}")
            return None

    async def _apply_participant_role(
        self,
        guild: Optional[disnake.Guild],
        user_id: int,
        *,
        add: bool,
        reason: str,
    ) -> bool:
        """Add or remove the event participant role. Returns True if Discord roles changed."""
        if not guild:
            return False
        event = self._get_current_event()
        if not event:
            return False
        role_id = event.get("role_id")
        if not role_id:
            return False
        role = guild.get_role(int(role_id))
        if not role:
            self.logger.warning(f"Secret Santa role {role_id} not found in guild {guild.id}")
            return False
        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            self.logger.warning(f"Bot lacks Manage Roles in guild {guild.id}")
            return False
        if role >= me.top_role:
            self.logger.warning(
                f"Cannot assign role '{role.name}': it must be **below** the bot's highest role "
                f"('{me.top_role.name}') in Server Settings → Roles."
            )
            return False
        member = await self._resolve_guild_member(guild, user_id)
        if not member:
            return False
        try:
            if add:
                if role in member.roles:
                    return False
                await member.add_roles(role, reason=reason)
                self.logger.info(f"Added SS role '{role.name}' to {member.display_name} ({user_id})")
                return True
            if role not in member.roles:
                return False
            await member.remove_roles(role, reason=reason)
            self.logger.info(f"Removed SS role '{role.name}' from {member.display_name} ({user_id})")
            return True
        except disnake.Forbidden:
            self.logger.warning(
                f"Forbidden managing role for user {user_id} — check bot permissions and role order"
            )
        except disnake.HTTPException as e:
            self.logger.error(f"HTTP error managing role for user {user_id}: {e}")
        return False
    
    # State loading now uses load_state_with_fallback from secret_santa_storage module

    async def cog_load(self):
        """Initialize cog - load state from disk (non-blocking), then start tasks"""
        # Load state in executor to avoid blocking event loop during startup (file I/O)
        loop = asyncio.get_event_loop()
        loaded_state = await loop.run_in_executor(
            self._executor,
            lambda: load_state_with_fallback(logger=self.logger)
        )
        if isinstance(loaded_state, dict):
            self.state.clear()
            self.state.update(loaded_state)
        else:
            self.logger.warning("Loaded state was not a dict, keeping default")

        self._backup_task = asyncio.create_task(self._backup_loop())
        self._scheduled_shuffle_task = asyncio.create_task(self._scheduled_shuffle_checker())
        self.logger.info("Secret Santa cog loaded")
        
        # Notify Discord about cog loading
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("🎄 Secret Santa cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog (synchronous wrapper to prevent RuntimeWarning)"""
        if self._unloaded:
            return
        
        self._unloaded = True
        self.logger.info("Unloading Secret Santa cog...")
        
        # Do sync operations immediately
        self._save()  # Final save is sync, safe to call
        
        # Schedule async cleanup for backup task
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running() and self._backup_task:
                # Create task for async cleanup
                loop.create_task(self._async_unload())
            else:
                # No loop or no task, we're done
                self.logger.info("Secret Santa cog unloaded (sync)")
        except RuntimeError:
            # No event loop available
            self.logger.info("Secret Santa cog unloaded (no loop)")
    
    async def _async_unload(self):
        """Async cleanup operations"""
        try:
            if self._backup_task:
                self._backup_task.cancel()
                try:
                    await self._backup_task
                except asyncio.CancelledError:
                    pass
            
            if self._scheduled_shuffle_task:
                self._scheduled_shuffle_task.cancel()
                try:
                    await self._scheduled_shuffle_task
                except asyncio.CancelledError:
                    pass
            
            # Executor is shared (bot.executor) - shutdown in main.py graceful_shutdown
            
            self.logger.info("Secret Santa cog unloaded")
        except Exception as e:
            self.logger.error(f"Async unload error: {e}")

    def _save(self):
        """Save state to disk with error handling and backup (synchronous - call from executor)"""
        return save_state(self.state, logger=self.logger)
    
    async def _save_async(self):
        """Save state to disk asynchronously (non-blocking)"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._save)

    async def _backup_loop(self):
        """Periodic backup - runs file I/O in executor to avoid blocking"""
        try:
            while True:
                await asyncio.sleep(BACKUP_INTERVAL_SECONDS)
                async with self._lock:
                    # Run file I/O in executor to avoid blocking event loop
                    await self._save_async()
        except asyncio.CancelledError:
            pass

    async def _scheduled_shuffle_checker(self):
        """Background task that checks for scheduled shuffles and stops, and executes them"""
        while True:
            try:
                # Check first, then sleep – so we don't miss a schedule that's already due
                current_time = time.time()
                event = self._get_current_event()

                # Check if there's a scheduled shuffle
                scheduled_shuffle_time = event.get("scheduled_shuffle_time") if event else None
                if event and scheduled_shuffle_time is not None and current_time >= scheduled_shuffle_time:
                    # Time to shuffle! Get scheduler ID before clearing
                    scheduler_id = event.get("scheduled_by_user_id")
                    self.logger.info(
                        "Running scheduled shuffle (scheduled_time=%s, scheduler_id=%s)",
                        int(scheduled_shuffle_time),
                        scheduler_id,
                    )
                    if hasattr(self.bot, 'send_to_discord_log'):
                        await self.bot.send_to_discord_log(
                            f"🎲 **Scheduled shuffle running now** – assignments will be sent via DM shortly.",
                            "INFO",
                        )

                    # Clear the schedule first to prevent double execution
                    async with self._lock:
                        event.pop("scheduled_shuffle_time", None)
                        event.pop("scheduled_by_user_id", None)
                        await self._save_async()
                    
                    # Execute the shuffle (without interaction, so we pass None for inter)
                    try:
                        await self._execute_shuffle_internal(scheduler_id=scheduler_id)
                    except Exception as e:
                        self.logger.error(f"Error executing scheduled shuffle: {e}", exc_info=True)
                        # Try to notify scheduler about the error
                        if scheduler_id:
                            error_msg = (
                                f"❌ **Oops – the scheduled shuffle hit a snag!**\n\n"
                                f"Something went wrong while trying to make the assignments:\n"
                                f"`{str(e)}`\n\n"
                                f"You'll need to run `/ss shuffle` manually to get everyone paired up."
                            )
                            await self._send_dm(scheduler_id, error_msg)
                
                # Check if there's a scheduled stop
                scheduled_stop_time = event.get("scheduled_stop_time") if event else None
                if event and scheduled_stop_time is not None and current_time >= scheduled_stop_time:
                    # Time to stop! Get stopper ID before clearing
                    stopper_id = event.get("scheduled_stop_by_user_id")
                    self.logger.info(
                        "Running scheduled stop (scheduled_time=%s, stopper_id=%s)",
                        int(scheduled_stop_time),
                        stopper_id,
                    )

                    # Execute the stop (this will clear the scheduled_stop_time internally)
                    try:
                        success, saved_filename = await self._execute_stop_internal(stopper_id=stopper_id)
                        if success:
                            # Notify the stopper
                            if stopper_id:
                                success_msg = (
                                    f"🛑 **Auto-stop complete!** Your Secret Santa event is now officially wrapped up.\n\n"
                                    f"Everything's been saved to: `{saved_filename}`\n\n"
                                    f"All your participants have gotten their \"event's over\" DM."
                                )
                                success = await self._send_dm(stopper_id, success_msg)
                                if not success:
                                    self.logger.debug(f"Could not send stop notification to stopper {stopper_id} (DMs may be disabled)")
                        else:
                            self.logger.error(f"Scheduled stop returned error: {saved_filename}")
                            if stopper_id:
                                error_msg = (
                                    f"❌ **Couldn't auto-stop the event.**\n\n"
                                    f"Ran into an issue while trying to wrap things up:\n"
                                    f"`{saved_filename}`\n\n"
                                    f"Please run `/ss stop` manually to end the event."
                                )
                                await self._send_dm(stopper_id, error_msg)
                    except Exception as e:
                        self.logger.error(f"Error executing scheduled stop: {e}", exc_info=True)
                        if stopper_id:
                            error_msg = (
                                f"❌ **Couldn't auto-stop the event.**\n\n"
                                f"Ran into an issue while trying to wrap things up:\n"
                                f"`{str(e)}`\n\n"
                                f"Please run `/ss stop` manually to end the event."
                            )
                            await self._send_dm(stopper_id, error_msg)

                # Next run: soonest future scheduled time (shuffle or stop). If we have one, sleep until then
                # (capped at CHECK_INTERVAL so we re-check hourly in case the event was edited).
                next_run = None
                if event and scheduled_shuffle_time is not None and scheduled_shuffle_time > current_time:
                    next_run = scheduled_shuffle_time
                if event and scheduled_stop_time is not None and scheduled_stop_time > current_time:
                    if next_run is None or scheduled_stop_time < next_run:
                        next_run = scheduled_stop_time
                if next_run is not None:
                    sleep_seconds = min(
                        next_run - current_time,
                        SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS,
                    )
                else:
                    sleep_seconds = SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS
                sleep_seconds = max(0.0, sleep_seconds)
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"Scheduled events checker error: {e}", exc_info=True)
                await asyncio.sleep(SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS)

    async def _send_dm(self, user_id: int, message: str, view: disnake.ui.View = None) -> bool:
        """
        Send DM to user with optional view. Retries on 429, 5xx, and connection errors.
        Uses timeouts to prevent hanging; respects Retry-After for rate limits.
        """
        if not message or not str(message).strip():
            self.logger.debug(f"Empty DM message for user {user_id}, skipping")
            return True  # Nothing to send, consider success

        for attempt in range(DM_MAX_RETRIES):
            try:
                user = await asyncio.wait_for(
                    self.bot.fetch_user(user_id),
                    timeout=DM_FETCH_TIMEOUT
                )
                await asyncio.wait_for(
                    user.send(message, view=view),
                    timeout=DM_SEND_TIMEOUT
                )
                return True
            except disnake.Forbidden as e:
                error_code = getattr(e, 'code', None)
                if error_code == 50007:
                    self.logger.warning(f"User {user_id} has DMs disabled (50007) - DM not delivered")
                else:
                    self.logger.warning(f"User {user_id} blocked DM (Forbidden: {error_code})")
                return False
            except disnake.HTTPException as e:
                status = getattr(e, 'status', None)
                retry_after = getattr(e, 'retry_after', 2.0)
                if status == 429 and attempt < DM_MAX_RETRIES - 1:
                    self.logger.warning(
                        f"Rate limited sending DM to {user_id}, waiting {retry_after}s "
                        f"(attempt {attempt + 1}/{DM_MAX_RETRIES})"
                    )
                    await asyncio.sleep(min(retry_after, 60.0))
                    continue
                if status and status >= 500 and attempt < DM_MAX_RETRIES - 1:
                    wait = min(2 ** attempt, 10.0)
                    self.logger.warning(
                        f"Discord server error {status} sending DM to {user_id}, retrying in {wait}s "
                        f"(attempt {attempt + 1}/{DM_MAX_RETRIES})"
                    )
                    await asyncio.sleep(wait)
                    continue
                self.logger.warning(f"HTTP error sending DM to {user_id}: {e}")
                return False
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                if attempt < DM_MAX_RETRIES - 1:
                    wait = min(2 ** attempt, 10.0)
                    self.logger.warning(
                        f"Connection/timeout sending DM to {user_id}, retrying in {wait}s "
                        f"(attempt {attempt + 1}/{DM_MAX_RETRIES}): {e}"
                    )
                    await asyncio.sleep(wait)
                    continue
                self.logger.warning(f"Connection error sending DM to {user_id} after retries: {e}")
                return False
            except disnake.NotFound:
                self.logger.warning(f"User {user_id} not found - DM not delivered")
                return False
            except Exception as e:
                self.logger.warning(f"Unexpected error sending DM to {user_id}: {e}")
                return False
        return False

    async def _send_dms_to_participants(
        self,
        items: list[tuple[int, str]],
        view: disnake.ui.View = None,
    ) -> list[int]:
        """
        Send DMs to participants with rate limiting. Returns list of user_ids who failed to receive.
        Spacing avoids Discord 429; each failure is logged.
        """
        failed: list[int] = []
        for user_id, message in items:
            success = await self._send_dm(user_id, message, view=view)
            if not success:
                failed.append(user_id)
            await asyncio.sleep(DM_DELAY_SECONDS)
        return failed

    def _get_fallback_channel(self, guild_id: Optional[int] = None) -> Optional[disnake.TextChannel]:
        """Get channel for fallback messages (when DM fails). Prefers DISCORD_CHANNEL_ID."""
        channel_id = getattr(self.bot.config, "DISCORD_CHANNEL_ID", None)
        if channel_id:
            try:
                ch = self.bot.get_channel(int(channel_id))
                if ch and isinstance(ch, disnake.TextChannel):
                    if guild_id is None or ch.guild.id == guild_id:
                        return ch
            except (ValueError, TypeError):
                pass
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        return ch
        return None

    async def _post_fallback_for_failed_dms(
        self,
        guild_id: Optional[int],
        failed_user_ids: list[int],
        message_type: str,
        year: int,
    ) -> bool:
        """Post in channel when DMs fail so users still get notified. Returns True if posted."""
        if not failed_user_ids:
            return False
        channel = self._get_fallback_channel(guild_id)
        if not channel:
            self.logger.warning(f"Cannot post fallback: no channel for guild {guild_id}")
            return False
        mentions = " ".join(f"<@{uid}>" for uid in failed_user_ids)
        if message_type == "assignment":
            text = (
                f"🎄 **Secret Santa {year} – Assignment Fallback** 🎄\n\n"
                f"We couldn't send your assignment via DM. {mentions}\n\n"
                f"**Your match is saved.** Use `/ss giftee` in this server to see your match's wishlist."
            )
        else:
            text = (
                f"🛑 **Secret Santa {year} – Event Ended** 🛑\n\n"
                f"We couldn't send you the wrap-up message via DM. {mentions}\n\n"
                f"Thanks for participating! The event has ended."
            )
        for attempt in range(3):
            try:
                await asyncio.wait_for(channel.send(text[:2000]), timeout=10.0)
                self.logger.info(f"Posted fallback for {len(failed_user_ids)} users in #{channel.name}")
                return True
            except (disnake.HTTPException, ConnectionError, asyncio.TimeoutError) as e:
                if attempt < 2:
                    wait = min(2 ** attempt, 5.0)
                    self.logger.warning(f"Fallback post failed, retrying in {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    self.logger.error(f"Failed to post fallback in channel after retries: {e}")
                    return False
            except Exception as e:
                self.logger.error(f"Failed to post fallback in channel: {e}")
                return False
        return False

    async def _process_reply(self, inter: disnake.ModalInteraction, reply: str, santa_id: int, giftee_id: int):
        """Process a reply from giftee to santa (called from Reply button modal or could be reused elsewhere)."""
        year = self.state.get("current_year", dt.date.today().year)
        try:
            if not await self._rate_limit_user(inter, self._limit_reply, "reply"):
                return
            event = self._get_current_event()
            if not event:
                await self._safe_followup_send(inter, content="❌ No active Secret Santa event", ephemeral=True)
                return
            if not await self._check_comms_cap(inter, event, str(santa_id)):
                return

            cleaned = self._scrub_user_text(reply)
            if not cleaned:
                await self._safe_followup_send(inter, content="❌ Reply was empty after removing @mentions.", ephemeral=True)
                return
            anonymized = await self._anonymize_text(cleaned, "reply")
            reply_msg = self._format_dm_reply(anonymized, year, giftee_id=giftee_id)
            success = await self._send_dm(santa_id, reply_msg)

            if success:
                if event:
                    await self._save_communication(
                        event, str(santa_id), str(giftee_id), "reply", cleaned, anonymized
                    )
                embed = self._success_embed(
                    title=f"✅ Reply sent — Secret Santa {year}",
                    description="Your Santa got your message in DM.",
                    footer="",
                )
                embed.add_field(name="What you sent", value=f"*{self._truncate_text(reply)}*", inline=False)
                await self._safe_followup_send(inter, embed=embed, ephemeral=True)
            else:
                embed = self._error_embed(
                    title="❌ Message couldn't be delivered",
                    description="Couldn't DM your Santa — they may have DMs closed."
                )
                await self._safe_followup_send(inter, embed=embed, ephemeral=True)
                
        except Exception as e:
            self.logger.error(f"Error processing reply: {e}")
            await self._safe_followup_send(inter, content="Yikes – something went wrong while sending your message. Could you try again?", ephemeral=True)

    def _get_year_emoji_mapping(self, participants: Dict[str, str]) -> Dict[str, str]:
        """
        Create consistent emoji mapping for all participants.
        Each user gets the same emoji across ALL years based on their user ID hash.
        """
        if not isinstance(participants, dict):
            participants = {}
        emoji_pattern = ["🎁", "🎄", "🎅", "⭐", "❄️", "☃️", "🦌", "🔔", "🍪", "🥛", "🕯️", "✨", "🌟", "🎈", "🧸", "🍭", "🎂", "🎪", "🎨", "🎯"]
        emoji_mapping = {}
        for participant_id in participants.keys():
            pid_str = str(participant_id)
            digest = hashlib.sha256(pid_str.encode("utf-8")).hexdigest()
            emoji_index = int(digest[:8], 16) % len(emoji_pattern)
            emoji_mapping[pid_str] = emoji_pattern[emoji_index]
        
        return emoji_mapping

    def _get_openai_headers(self) -> Dict[str, str]:
        return get_openai_headers(getattr(self.bot.config, "OPENAI_API_KEY", None))

    def _normalize_anonymize_input(self, text: str) -> str:
        """Ensure text is safe for API: non-empty, UTF-8, no null bytes."""
        if not text or not isinstance(text, str):
            return ""
        text = text.replace("\x00", " ").strip()
        try:
            text.encode("utf-8").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        return text[:4000]  # Cap length to avoid token limits

    async def _anonymize_text(self, text: str, message_type: str = "question") -> str:
        """
        Use OpenAI to rewrite text for anonymity. Retries on 429, 5xx, connection errors.
        Falls back to original text on any failure - never breaks the user flow.
        """
        text = self._normalize_anonymize_input(text)
        if not text:
            return ""
        headers = self._get_openai_headers()
        if not headers:
            return text

        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{
                "role": "user",
                "content": (
                    "Rewrite this Secret Santa {type} for anonymity. "
                    "Remove ALL names, nicknames, @mentions, Discord tags, and anything that identifies the writer. "
                    "Keep the same meaning and tone but use neutral wording. "
                    "Do not add new facts. Output only the rewritten message.\n\n"
                    "Original: {text}\n\nRewritten:"
                ).format(type=message_type, text=text)
            }],
            "max_tokens": 150,
            "temperature": 0.2
        }
        last_error = None
        for attempt in range(ANONYMIZE_RETRY_MAX):
            try:
                session = await self.bot.http_mgr.get_session(timeout=ANONYMIZE_TIMEOUT)
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=ANONYMIZE_TIMEOUT)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        try:
                            rewritten = result["choices"][0]["message"]["content"].strip()
                        except (KeyError, TypeError, IndexError):
                            return text
                        rewritten = rewritten.replace("Rewritten:", "").strip()
                        return rewritten if rewritten else text
                    if resp.status in (429, 500, 502, 503) and attempt < ANONYMIZE_RETRY_MAX - 1:
                        delay = ANONYMIZE_RETRY_BASE_DELAY * (2 ** attempt)
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = min(float(retry_after), 30.0)
                            except (ValueError, TypeError):
                                pass
                        self.logger.debug(
                            f"Anonymization API {resp.status}, retrying in {delay:.1f}s "
                            f"(attempt {attempt + 1}/{ANONYMIZE_RETRY_MAX})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    self.logger.debug(f"Anonymization failed: {resp.status}")
                    return text
            except RuntimeError as e:
                if "Event loop is closed" in str(e) and attempt < ANONYMIZE_RETRY_MAX - 1:
                    self.logger.debug(
                        "Anonymization: session tied to closed loop, invalidating and retrying"
                    )
                    try:
                        await self.bot.http_mgr.invalidate_session()
                    except Exception:
                        pass
                    await asyncio.sleep(ANONYMIZE_RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                last_error = e
                self.logger.debug(f"Anonymization error: {e}")
                break
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                last_error = e
                if attempt < ANONYMIZE_RETRY_MAX - 1:
                    delay = ANONYMIZE_RETRY_BASE_DELAY * (2 ** attempt)
                    self.logger.debug(
                        f"Anonymization connection error, retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    continue
            except Exception as e:
                last_error = e
                self.logger.debug(f"Anonymization error: {e}")
                break
        return text

    def _archive_event(self, event: Dict[str, Any], year: int) -> str:
        """Archive event using the storage module"""
        filename = archive_event(event, year, logger=self.logger)
        
        # Also notify via Discord if backup was created
        if "backup" in filename and hasattr(self.bot, 'send_to_discord_log'):
            asyncio.create_task(
                self.bot.send_to_discord_log(
                    f"⚠️ Archive protection: {year}.json already exists! Saved to {filename} to prevent data loss. Review manually!",
                    "WARNING"
                )
            )
        
        return filename


