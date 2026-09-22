"""Secret Santa slash commands — lifecycle (start, shuffle, stop) (mixin)."""
from __future__ import annotations

import asyncio
import random
import time
import datetime as dt
from typing import List, Optional
from zoneinfo import ZoneInfo

import disnake
from disnake.ext import commands

from .secret_santa_assignments import (
    load_history_from_archives,
    make_assignments,
    validate_assignment_possibility,
)
from .secret_santa_checks import (
    GIFT_NO_SUBMISSION_ROW,
    format_gift_description_for_display,
    mod_check,
    safe_display_name,
)
from .secret_santa_storage import (
    ARCHIVE_DIR,
    BACKUPS_DIR,
    archive_event,
    load_all_archives,
    load_json,
    save_json,
)
from .secret_santa_views import (
    BackupListPaginator,
    CommunicationsPaginator,
    SecretSantaReplyView,
    YearHistoryPaginator,
    YearTimelinePaginator,
)
from .secret_santa_core import DISCORD_LOCALE_TO_IANA
from .secret_santa_cmd_root import SecretSantaRootMixin


class SecretSantaLifecycleMixin:
    @SecretSantaRootMixin.ss_root.sub_command(name="start", description="Start Secret Santa (react on message to join)")
    @mod_check()
    async def ss_start(
        self,
        inter: disnake.ApplicationCommandInteraction,
        message: disnake.Message = commands.Param(description="Signup message — members react to join"),
        shuffle: Optional[str] = commands.Param(
            default=None,
            name="shuffle",
            description="Optional shuffle time (e.g. 2025-12-24 18:00). Discord timezone, else UTC",
        ),
        end: Optional[str] = commands.Param(
            default=None,
            name="end",
            description="Optional auto-stop (e.g. 2025-12-26 02:00). Default: Dec 25 23:59 if omitted",
        ),
        role: Optional[disnake.Role] = commands.Param(
            default=None,
            description="Optional: Discord role added when someone joins (react) and removed if they leave",
        ),
    ):
        """Start event; optional auto-shuffle and auto-stop times"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        if not inter.guild:
            await self._safe_edit_response(inter, content="❌ Use this command in a server.")
            return
        if not message:
            await self._safe_edit_response(inter, content="❌ No message provided.")
            return
        msg_id = message.id
        msg_guild = getattr(message, "guild", None)
        if not msg_guild:
            await self._safe_edit_response(inter, content="❌ Message must be from a server channel (not DMs).")
            return
        if msg_guild.id != inter.guild.id:
            await self._safe_edit_response(inter, content="❌ Message must be from this server.")
            return

        # Get role ID if provided
        role_id_int = role.id if role else None

        debug_start = bool(getattr(getattr(self.bot, "config", None), "SS_DEBUG_START", False))

        # SAFETY WARNING: Check if current year is already archived
        # Prevents accidental data loss if you test on wrong server or run twice
        # SS_DEBUG_START in config.env skips this warning for algorithm testing
        current_year = dt.date.today().year
        existing_archive = ARCHIVE_DIR / f"{current_year}.json"
        if existing_archive.exists() and not debug_start:
            embed = disnake.Embed(
                title="⚠️ Year Already Archived",
                description=f"An archive already exists for {current_year}!\n\n"
                            f"**This might mean:**\n"
                            f"• You already ran Secret Santa this year\n"
                            f"• You're testing on the wrong server\n"
                            f"• This is intentional (test event)\n\n"
                            f"**If you continue, the old archive will be preserved** and any new archive will be saved to a backup file.",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="🔒 Protection Active",
                value=f"Existing archive: `{current_year}.json`\n"
                      f"New archives will save to: `{current_year}_backup_TIMESTAMP.json`",
                inline=False
            )
            embed.set_footer(text="✅ Your existing archive is safe and won't be overwritten!")
            await self._safe_edit_response(inter, embed=embed)
            
            self.logger.warning(f"Starting new event for {current_year} but archive already exists!")
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"⚠️ {safe_display_name(inter.author)} is starting a new Secret Santa {current_year} event, but {current_year}.json archive already exists!",
                    "WARNING"
                )

        if existing_archive.exists() and debug_start:
            self.logger.info(f"SS_DEBUG_START: skipping archive warning, will use history from other years (excluding {current_year})")

        # Collect participants from the message reactions (may be empty if no one reacted yet)
        participants = {}
        try:
            reactions = getattr(message, "reactions", None) or []
            for reaction in reactions:
                async for user in reaction.users():
                    if getattr(user, "bot", True):
                        continue
                    user_id_str = str(user.id)
                    if user_id_str not in participants:
                        member = inter.guild.get_member(user.id)
                        name = member.display_name if member else getattr(user, "name", f"User {user.id}")
                        participants[user_id_str] = name
        except Exception as e:
            self.logger.warning(f"Could not load reactions from message: {e}")

        # Timezone for parsing shuffle/end: guess from command author's Discord language, else UTC
        tz_info = None
        used_locale_tz: Optional[str] = None
        locale_raw = getattr(inter, "locale", None)
        if locale_raw is not None:
            locale_str = getattr(locale_raw, "value", str(locale_raw)).replace("_", "-")
            tz_name = DISCORD_LOCALE_TO_IANA.get(locale_str)
            if tz_name:
                try:
                    tz_info = ZoneInfo(tz_name)
                    used_locale_tz = tz_name
                except Exception:
                    pass

        # Parse and validate shuffle schedule if provided
        scheduled_timestamp = None
        if shuffle:
            scheduled_timestamp = self._parse_datetime_combined(shuffle, tz_info=tz_info)
            if not scheduled_timestamp:
                await self._safe_edit_response(
                    inter,
                    content="❌ Invalid shuffle date/time. Use one string with both date and time.\n\n"
                           "**Examples:**\n"
                           "• `2025-12-25 14:30`\n"
                           "• `December 25, 2025 2:30 PM`\n"
                           "• `12/25/2025 11:00 AM`"
                )
                return
            current_time = time.time()
            if scheduled_timestamp <= current_time:
                await self._safe_edit_response(
                    inter,
                    content="❌ Scheduled shuffle time must be in the future!\n\n"
                           f"Current time: <t:{int(current_time)}:F>\n"
                           f"Your time: <t:{int(scheduled_timestamp)}:F>"
                )
                return

        # Parse and validate stop schedule if provided
        scheduled_stop_timestamp = None
        if end:
            scheduled_stop_timestamp = self._parse_datetime_combined(end, tz_info=tz_info)
            if not scheduled_stop_timestamp:
                await self._safe_edit_response(
                    inter,
                    content="❌ Invalid end date/time. Use one string with both date and time.\n\n"
                           "**Examples:**\n"
                           "• `2025-12-31 23:59`\n"
                           "• `December 31, 2025 11:59 PM`\n"
                           "• `12/31/2025 11:59 PM`"
                )
                return
            current_time = time.time()
            if scheduled_stop_timestamp <= current_time:
                await self._safe_edit_response(
                    inter,
                    content="❌ Scheduled stop time must be in the future!\n\n"
                           f"Current time: <t:{int(current_time)}:F>\n"
                           f"Your time: <t:{int(scheduled_stop_timestamp)}:F>"
                )
                return
            if scheduled_timestamp and scheduled_stop_timestamp <= scheduled_timestamp:
                await self._safe_edit_response(
                    inter,
                    content="❌ Scheduled stop time must be after shuffle time!\n\n"
                           f"Shuffle: <t:{int(scheduled_timestamp)}:F>\n"
                           f"Stop: <t:{int(scheduled_stop_timestamp)}:F>"
                )
                return

        used_default_stop = False
        if scheduled_stop_timestamp is None:
            scheduled_stop_timestamp = self._default_scheduled_stop_timestamp(
                current_year, tz_info, scheduled_timestamp
            )
            used_default_stop = scheduled_stop_timestamp is not None

        # Create event (current_year already set above during safety check)
        channel_id = getattr(getattr(message, "channel", None), "id", None)
        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": msg_id,
            "announcement_channel_id": channel_id,
            "role_id": role_id_int,
            "participants": participants,
            "assignments": {},
            "guild_id": inter.guild.id,
            "gift_submissions": {},
            "communications": {},
            "wishlists": {}  # User wishlists
        }
        
        # Add scheduled shuffle if provided
        if scheduled_timestamp:
            new_event["scheduled_shuffle_time"] = scheduled_timestamp
            new_event["scheduled_by_user_id"] = inter.author.id
        
        # Add scheduled stop if provided
        if scheduled_stop_timestamp:
            new_event["scheduled_stop_time"] = scheduled_stop_timestamp
            new_event["scheduled_stop_by_user_id"] = inter.author.id

        # Use lock to prevent concurrent event creation
        async with self._lock:
            # CRITICAL: Check if event already active INSIDE lock (prevents race conditions)
            event = self.state.get("current_event")
            if event and event.get("active"):
                await self._safe_edit_response(inter, content="❌ Event already active")
                return
            
            self.state["current_year"] = current_year
            self.state["current_event"] = new_event
            await self._save_async()

        # Participant role for everyone already on the signup message
        if role_id_int and inter.guild:
            for uid in participants:
                try:
                    await self._apply_participant_role(
                        inter.guild, int(uid), add=True, reason="Secret Santa signup"
                    )
                except Exception as e:
                    self.logger.error(f"Role assign at start for {uid}: {e}", exc_info=True)

        # Send confirmation DMs (rate-limited to avoid Discord 429)
        join_msg = self._get_join_message(current_year)
        dm_items = [(int(uid), join_msg) for uid in participants]
        failed = await self._send_dms_to_participants(dm_items)
        successful = len(participants) - len(failed)

        # Build response message
        response_msg = (
            f"✅ Secret Santa {current_year} started!\n"
            f"• Participants: {len(participants)}\n"
            f"• DMs sent: {successful}/{len(participants)}"
        )
        if debug_start:
            response_msg += "\n• 🔧 SS_DEBUG_START: archive warning skipped"
        if role:
            response_msg += f"\n• Join role: {role.mention} _(added on react; bot role must be above this role)_"
        
        if scheduled_timestamp:
            response_msg += f"\n\n📅 **Auto-shuffle:** <t:{int(scheduled_timestamp)}:F>"
            if used_locale_tz:
                response_msg += f" _(parsed in {used_locale_tz} from your Discord language)_"
            else:
                response_msg += " _(parsed in UTC — change Discord app language for local parsing)_"
            response_msg += "\n💡 Or run `/ss shuffle` anytime before then."
        
        if scheduled_stop_timestamp:
            response_msg += f"\n\n🛑 **Auto-stop & archive:** <t:{int(scheduled_stop_timestamp)}:F>"
            if used_default_stop:
                response_msg += "\n_(Default: Christmas Day safety net — set `end` on start to override)_"
            response_msg += "\n💡 Or run `/ss stop` manually anytime before then."
        
        await self._safe_edit_response(inter, content=response_msg)
        
        # Notify Discord log channel
        log_msg = f"Secret Santa {current_year} event started by {safe_display_name(inter.author)} - {len(participants)} participants joined"
        if scheduled_timestamp:
            log_msg += f" (shuffle scheduled for <t:{int(scheduled_timestamp)}:F>)"
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log(log_msg, "SUCCESS")

    async def _execute_shuffle_internal(self, inter: Optional[disnake.ApplicationCommandInteraction] = None, scheduler_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """
        Internal method to execute shuffle logic. Can be called from manual command or scheduled task.
        
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        # Use lock to prevent concurrent shuffle executions
        async with self._lock:
            event = self._get_current_event()
            if not event:
                error_msg = "❌ No active event - use `/ss start` to create one first"
                if inter:
                    await self._safe_edit_response(inter, content=error_msg)
                return False, error_msg

            # CRITICAL: Check if assignments already exist - prevent overwriting
            existing_assignments = event.get("assignments", {})
            if existing_assignments:
                error_msg = (
                    "❌ **Assignments already exist!**\n\n"
                    f"• {len(existing_assignments)} pairs have already been created\n"
                    "• Participants have already received their assignments\n\n"
                    "💡 **To reshuffle:** Use `/ss stop` to end the current event, then `/ss start` to create a new one.\n"
                    "⚠️ **Warning:** Reshuffling will overwrite existing assignments and send new DMs to all participants!"
                )
                if inter:
                    await self._safe_edit_response(inter, content=error_msg)
                return False, error_msg

            # Convert participant IDs to integers (safe: skip malformed keys)
            participants_dict = event.get("participants", {}) or {}
            participants = []
            for uid in participants_dict:
                try:
                    participants.append(int(uid))
                except (ValueError, TypeError):
                    continue

            if len(participants) < 2:
                error_msg = "❌ Need at least 2 participants"
                if inter:
                    await self._safe_edit_response(inter, content=error_msg)
                return False, error_msg

            # Get guild for role assignment (if inter provided, use it; otherwise try to get from event)
            guild = None
            if inter:
                guild = inter.guild
            elif event.get("guild_id"):
                guild = self.bot.get_guild(event["guild_id"])

            # HISTORY LOADING: Load all past Secret Santa events from archive files (run in executor - sync file I/O)
            # CRITICAL: Exclude current year from history - we're creating a NEW event for this year
            current_year = self.state.get('current_year', dt.date.today().year)
            loop = asyncio.get_running_loop()
            history, available_years = await loop.run_in_executor(
                self._executor,
                lambda: load_history_from_archives(ARCHIVE_DIR, exclude_years=[current_year], logger=self.logger)
            )
            
            self.logger.info(f"Attempting Secret Santa assignment with {len(participants)} participants")
            self.logger.info(f"Available history years: {available_years}")
            self.logger.info(f"Excluding current year {current_year} from history (creating new event for this year)")
            
            # PROGRESSIVE FALLBACK SYSTEM
            exclude_years = []
            assignments = None
            fallback_used = False
            
            # Try with all years first, then progressively exclude oldest years
            for attempt in range(len(available_years) + 1):
                if attempt:
                    exclude_years = available_years[:attempt]
                    fallback_used = True
                    self.logger.info(f"Fallback attempt {attempt}: Excluding years {exclude_years}")
                    # Use default arg to capture exclude_years at definition time (closure safety)
                    exclude_copy = list(exclude_years)
                    history, _ = await loop.run_in_executor(
                        self._executor,
                        lambda ex=exclude_copy: load_history_from_archives(ARCHIVE_DIR, exclude_years=ex, logger=self.logger)
                    )
                    
                    if inter:
                        years_str = ", ".join(map(str, exclude_years))
                        await self._safe_edit_response(
                            inter,
                            content=f"⚠️ Initial assignment difficult... trying fallback (excluding {years_str})..."
                        )
                
                validation_error = validate_assignment_possibility(participants, history)
                if validation_error:
                    if attempt == len(available_years):
                        error_msg = f"❌ {validation_error}"
                        if inter:
                            await self._safe_edit_response(inter, content=error_msg)
                        if hasattr(self.bot, 'send_to_discord_log'):
                            await self.bot.send_to_discord_log(
                                f"Secret Santa assignment failed even with all fallbacks - {validation_error}",
                                "ERROR"
                            )
                        return False, error_msg
                    continue
                
                try:
                    assignments = await loop.run_in_executor(
                        self._executor,
                        lambda: make_assignments(participants, history, logger=self.logger)
                    )
                    self.logger.info("Assignment algorithm succeeded")
                    break
                except ValueError as e:
                    if attempt == len(available_years):
                        error_msg = f"❌ Assignment failed: {e}"
                        if inter:
                            await self._safe_edit_response(inter, content=error_msg)
                        if hasattr(self.bot, 'send_to_discord_log'):
                            await self.bot.send_to_discord_log(
                                f"Secret Santa assignment failed even with all fallbacks - {e}",
                                "ERROR"
                            )
                        return False, error_msg
                    continue
            
            if not assignments:
                error_msg = "❌ Assignment failed unexpectedly"
                if inter:
                    await self._safe_edit_response(inter, content=error_msg)
                return False, error_msg

            # Save assignments BEFORE sending DMs (prevents race conditions)
            event["assignments"] = {str(k): str(v) for k, v in assignments.items()}
            event["join_closed"] = True
            # Clear any scheduled shuffle since we just executed
            event.pop("scheduled_shuffle_time", None)
            event.pop("scheduled_by_user_id", None)
            await self._save_async()
            self.logger.info("Assignments saved, releasing lock before DMs")
        
        # Release lock before sending DMs (they can take time, don't block other operations)
        # Assignments are already saved, so concurrent shuffle attempts will see them and fail
        
        # Ensure participant role on everyone (covers anyone who joined before role was set or missed on react)
        if guild:
            for user_id in participants:
                await self._apply_participant_role(
                    guild, user_id, add=True, reason="Secret Santa participant (shuffle)"
                )

        # Send assignment DMs with rate limiting (avoids 429) and track failures
        participants_dict = event.get("participants", {})
        current_year = self.state.get("current_year", dt.date.today().year)
        dm_items = []
        for giver, receiver in assignments.items():
            receiver_name = participants_dict.get(str(receiver), f"User {receiver}")
            msg = self._get_assignment_message(current_year, int(receiver) if isinstance(receiver, str) else receiver, receiver_name)
            dm_items.append((int(giver) if isinstance(giver, str) else giver, msg))
        self.logger.info(f"Sending assignment DMs to {len(dm_items)} participants")
        failed = await self._send_dms_to_participants(dm_items)
        self.logger.info(f"DM send complete: {len(failed)} failed of {len(dm_items)}")
        guild_id = event.get("guild_id")
        if failed:
            await self._post_fallback_for_failed_dms(guild_id, failed, "assignment", current_year)
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"Secret Santa: {len(failed)} participant(s) did not receive assignment DM – fallback posted in channel",
                    "WARNING",
                )

        # Build success message
        response_msg = f"✅ Assignments complete!\n"
        response_msg += f"• {len(assignments)} pairs created\n"
        response_msg += f"• DMs sent to all participants" + (f" ({len(failed)} got fallback in channel)" if failed else "") + "\n"
        response_msg += f"• History respected (no repeated pairings!)\n"
        
        if fallback_used:
            years_str = ", ".join(map(str, exclude_years))
            response_msg += f"\n⚠️ **Fallback used:** Excluded history from {years_str} to make assignments possible\n"
            response_msg += f"💡 Consider having Secret Santa more frequently to avoid this!"
        
        if inter:
            await self._safe_edit_response(inter, content=response_msg)
        
        # Notify Discord log channel (DM stats: e.g. "5/5 DMs sent" or "4/5 DMs sent (1 fallback)")
        total_dms = len(dm_items)
        success_dms = total_dms - len(failed)
        dm_stats = f"{success_dms}/{total_dms} DMs sent"
        if failed:
            dm_stats += f" ({len(failed)} fallback in channel)"
        self.logger.info(f"Shuffle complete: {dm_stats}")
        executor_name = safe_display_name(inter.author) if inter else (f"User {scheduler_id}" if scheduler_id else "Scheduled task")
        if hasattr(self.bot, 'send_to_discord_log'):
            log_msg = f"Secret Santa assignments completed by {executor_name} - {len(assignments)} pairs, {dm_stats}"
            if fallback_used:
                log_msg += f" (history fallback: excluded {', '.join(map(str, exclude_years))})"
            await self.bot.send_to_discord_log(log_msg, "SUCCESS" if not fallback_used else "WARNING")
        
        return True, None

    async def _execute_stop_internal(self, stopper_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """
        Internal method to execute stop logic. Can be called from manual command or scheduled task.
        
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        # Use lock to prevent concurrent stop executions
        async with self._lock:
            event = self._get_current_event()
            if not event:
                return False, "❌ No active event"
            
            # Archive under the year the event was started (not calendar "today")
            year = self.state.get("current_year") or dt.date.today().year
            
            # Archive event (with automatic backup protection)
            saved_filename = self._archive_event(event, year)
            
            # Clear scheduled stop before clearing event
            event.pop("scheduled_stop_time", None)
            event.pop("scheduled_stop_by_user_id", None)
            self.state["current_event"] = None
            await self._save_async()
        
        # Release lock before sending DMs (they can take time)
        # Event is already cleared, so concurrent stops will see no event and fail
        
        # Send thank you message to all participants with rate limiting and fallback
        participants = event.get("participants", {}) or {}
        if participants:
            end_msg = self._get_event_end_message(year)
            dm_items = []
            for uid in participants:
                try:
                    dm_items.append((int(uid), end_msg))
                except (ValueError, TypeError):
                    continue
            if dm_items:
                failed = await self._send_dms_to_participants(dm_items)
                guild_id = event.get("guild_id")
                if failed:
                    await self._post_fallback_for_failed_dms(guild_id, failed, "stop", year)
                    if hasattr(self.bot, 'send_to_discord_log'):
                        await self.bot.send_to_discord_log(
                            f"Secret Santa stop: {len(failed)} participant(s) did not receive DM – fallback posted in channel",
                            "WARNING",
                        )

        # Notify Discord log channel
        if hasattr(self.bot, 'send_to_discord_log'):
            participants_count = len(event.get("participants", {}))
            gifts_count = len(event.get("gift_submissions", {}))
            executor_name = f"User {stopper_id}" if stopper_id else "Scheduled task"
            await self.bot.send_to_discord_log(
                f"Secret Santa {year} event stopped by {executor_name} - {participants_count} participants, {gifts_count} gifts submitted",
                "INFO"
            )
        
        return True, saved_filename

    def _parse_datetime(self, date_str: str, time_str: str, tz_info: Optional[ZoneInfo] = None) -> Optional[float]:
        """
        Parse date and time strings into a Unix timestamp.
        
        Supports intuitive formats:
        - Date: "YYYY-MM-DD", "MM/DD/YYYY", "December 25, 2025"
        - Time: "HH:MM" (24-hour), "HH:MM AM/PM" (12-hour)
        
        If tz_info is set, the time is interpreted in that timezone (e.g. user local).
        Otherwise the time is interpreted in server local time (often UTC).
        
        Returns:
            Unix timestamp (float) or None if parsing fails
        """
        try:
            # Try common date formats
            date_obj = None
            date_formats = [
                "%Y-%m-%d",      # 2025-12-25
                "%m/%d/%Y",      # 12/25/2025
                "%B %d, %Y",     # December 25, 2025
                "%b %d, %Y",     # Dec 25, 2025
                "%d %B %Y",      # 25 December 2025
                "%d %b %Y",      # 25 Dec 2025
            ]
            
            for fmt in date_formats:
                try:
                    date_obj = dt.datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
            
            if not date_obj:
                return None
            
            # Try common time formats
            time_obj = None
            time_formats = [
                "%H:%M",         # 14:30 (24-hour)
                "%I:%M %p",      # 02:30 PM (12-hour)
                "%I:%M%p",       # 02:30PM (12-hour, no space)
                "%H:%M:%S",      # 14:30:00 (24-hour with seconds)
            ]
            
            for fmt in time_formats:
                try:
                    time_obj = dt.datetime.strptime(time_str, fmt).time()
                    break
                except ValueError:
                    continue
            
            if not time_obj:
                return None
            
            # Combine date and time; if timezone given, interpret in that zone
            datetime_obj = dt.datetime.combine(date_obj, time_obj, tzinfo=tz_info)
            return datetime_obj.timestamp()
            
        except Exception as e:
            self.logger.debug(f"Date/time parsing error: {e}")
            return None

    def _parse_datetime_combined(self, date_time_str: str, tz_info: Optional[ZoneInfo] = None) -> Optional[float]:
        """
        Parse a single string containing both date and time into a Unix timestamp.
        E.g. "2025-12-25 14:30", "December 25, 2025 2:30 PM", "12/25/2025 11:59 PM".
        """
        if not date_time_str or not isinstance(date_time_str, str):
            return None
        s = date_time_str.strip()
        if not s:
            return None
        combined_formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M %p",
            "%Y-%m-%d %I:%M%p",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %I:%M%p",
            "%B %d, %Y %H:%M",
            "%B %d, %Y %I:%M %p",
            "%B %d, %Y %I:%M%p",
            "%b %d, %Y %H:%M",
            "%b %d, %Y %I:%M %p",
            "%b %d, %Y %I:%M%p",
            "%d %B %Y %H:%M",
            "%d %B %Y %I:%M %p",
            "%d %b %Y %H:%M",
            "%d %b %Y %I:%M %p",
        ]
        try:
            for fmt in combined_formats:
                try:
                    dt_obj = dt.datetime.strptime(s, fmt)
                    if tz_info:
                        dt_obj = dt_obj.replace(tzinfo=tz_info)
                    return dt_obj.timestamp()
                except ValueError:
                    continue
            return None
        except Exception as e:
            self.logger.debug(f"Combined date/time parsing error: {e}")
            return None

    @SecretSantaRootMixin.ss_root.sub_command(name="status", description="Event dashboard: participants, schedule, assignments (mod)")
    @mod_check()
    async def ss_status(self, inter: disnake.ApplicationCommandInteraction):
        """Show current event state for the organizer"""
        if not await self._safe_defer(inter, ephemeral=True):
            return

        event = self._get_current_event()
        year = self.state.get("current_year", dt.date.today().year)
        if not event or not event.get("active"):
            await self._safe_edit_response(
                inter,
                content=f"📭 No active Secret Santa event for **{year}**.\n\nRun `/ss start` with your signup message.",
            )
            return

        participants = event.get("participants") or {}
        n_participants = len(participants) if isinstance(participants, dict) else 0
        assignments = event.get("assignments") or {}
        n_assigned = len(assignments) if isinstance(assignments, dict) else 0
        shuffled = n_assigned > 0

        embed = disnake.Embed(
            title=f"🎄 Secret Santa {year} — Status",
            color=disnake.Color.green() if shuffled else disnake.Color.gold(),
        )
        embed.add_field(name="Participants", value=str(n_participants), inline=True)
        embed.add_field(
            name="Assignments",
            value="✅ Done" if shuffled else "⏳ Not yet — run `/ss shuffle`",
            inline=True,
        )
        embed.add_field(
            name="Join via reactions",
            value="🔒 Closed" if event.get("join_closed") else "✅ Open",
            inline=True,
        )

        msg_id = event.get("announcement_message_id")
        chan_id = event.get("announcement_channel_id")
        if msg_id and inter.guild and chan_id:
            link = f"https://discord.com/channels/{inter.guild.id}/{chan_id}/{msg_id}"
            embed.add_field(name="Signup message", value=f"[Open signup post]({link})", inline=False)
        elif msg_id:
            embed.add_field(name="Signup message ID", value=f"`{msg_id}`", inline=False)

        role_id = event.get("role_id")
        if role_id and inter.guild:
            role = inter.guild.get_role(role_id)
            embed.add_field(
                name="Join role",
                value=role.mention if role else f"`{role_id}` (role missing)",
                inline=False,
            )

        sched_shuffle = event.get("scheduled_shuffle_time")
        if sched_shuffle:
            embed.add_field(
                name="Auto-shuffle",
                value=f"<t:{int(sched_shuffle)}:F> (<t:{int(sched_shuffle)}:R>)",
                inline=False,
            )
        sched_stop = event.get("scheduled_stop_time")
        if sched_stop:
            embed.add_field(
                name="Auto-stop",
                value=f"<t:{int(sched_stop)}:F> (<t:{int(sched_stop)}:R>)",
                inline=False,
            )

        if not sched_shuffle and not shuffled:
            embed.set_footer(text="Tip: set shuffle on /ss start, or run /ss shuffle when everyone has joined")
        elif shuffled:
            embed.set_footer(text="Participants use /ss ask_giftee, /ss giftee, /ss submit_gift")

        if participants:
            lines = [f"• {name} (<@{uid}>)" for uid, name in list(participants.items())[:25]]
            if len(participants) > 25:
                lines.append(f"_…and {len(participants) - 25} more_")
            embed.add_field(name="Roster", value="\n".join(lines), inline=False)

        await self._safe_edit_response(inter, embed=embed)

    @SecretSantaRootMixin.ss_root.sub_command(name="shuffle", description="Pair participants and DM assignments (mod)")
    @mod_check()
    async def ss_shuffle(self, inter: disnake.ApplicationCommandInteraction):
        """Make assignments now (cancels a pending auto-shuffle from /ss start)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        # COMBINED: Get event and check/cancel scheduled shuffle in one pass
        event = self._get_current_event()
        scheduled_time = event.get("scheduled_shuffle_time") if event else None
        if scheduled_time:
            async with self._lock:
                event.pop("scheduled_shuffle_time", None)
                event.pop("scheduled_by_user_id", None)
                await self._save_async()
            self.logger.info(f"Manual shuffle cancelled scheduled shuffle (was scheduled for <t:{int(scheduled_time)}:F>)")
        
        success, error = await self._execute_shuffle_internal(inter=inter)
        if not success and error:
            # Error already sent to inter
            pass

    @SecretSantaRootMixin.ss_root.sub_command(name="stop", description="End event and archive this year (mod)")
    @mod_check()
    async def ss_stop(self, inter: disnake.ApplicationCommandInteraction):
        """Stop event"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # COMBINED: Get event and check/cancel scheduled stop in one pass
        event = self._get_current_event()
        if not event:
            await self._safe_edit_response(inter, content="❌ No active event")
            return

        # Check if there's a scheduled stop and cancel it
        cancelled_scheduled = False
        scheduled_time = event.get("scheduled_stop_time")
        if scheduled_time:
            async with self._lock:
                event.pop("scheduled_stop_time", None)
                event.pop("scheduled_stop_by_user_id", None)
                await self._save_async()
            cancelled_scheduled = True
            self.logger.info(f"Manual stop cancelled scheduled stop (was scheduled for <t:{int(scheduled_time)}:F>)")

        # Execute stop using the helper function
        success, saved_filename = await self._execute_stop_internal(stopper_id=inter.author.id)
        
        if not success:
            await self._safe_edit_response(inter, content=saved_filename or "❌ Failed to stop event")
            return

        # Get current year (state was updated by _execute_stop_internal)
        current_year = self.state.get('current_year', dt.date.today().year)

        # Build response message
        if cancelled_scheduled:
            response_msg = f"⚠️ Scheduled stop cancelled and executed manually.\n"
            response_msg += f"(Was scheduled for: <t:{int(scheduled_time)}:F>)\n\n"
        else:
            response_msg = ""

        # Show appropriate message based on what file was saved
        if "backup" in saved_filename:
            # Archive protection was triggered
            embed = disnake.Embed(
                title="✅ Event Stopped & Protected",
                description=(response_msg if response_msg else "") + f"Secret Santa {current_year} has been archived with data protection!",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="🔒 Archive Protection",
                value=f"**Original:** `{current_year}.json` (preserved)\n"
                      f"**This event:** `{saved_filename}`\n\n"
                      f"⚠️ You ran multiple {current_year} events! Review archives folder manually.",
                inline=False
            )
            embed.set_footer(text="Your original archive was NOT overwritten!")
            await self._safe_edit_response(inter, embed=embed)
        else:
            # Normal archive
            if response_msg:
                await self._safe_edit_response(inter, content=response_msg + f"✅ Event stopped and archived → `{saved_filename}`")
            else:
                await self._safe_edit_response(inter, content=f"✅ Event stopped and archived → `{saved_filename}`")
