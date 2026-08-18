"""Secret Santa slash commands (mixin — combined into SecretSantaCog)."""
from __future__ import annotations

import asyncio
import random
import time

import disnake
from disnake.ext import commands

import datetime as dt
from typing import List, Optional
from zoneinfo import ZoneInfo

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

class SecretSantaCommandsMixin:
    """Slash commands; mixed into SecretSantaCog."""

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.ApplicationCommandInteraction):
        """Secret Santa commands"""
        pass

    # START command – full logic path:
    # 1. Defer ephemeral → 2. Require guild + message → 3. Message must have guild and same guild as inter
    # 4. Optional: warn if current_year archive already exists (continue anyway) → 5. Collect participants from message.reactions (safe)
    # 6. Resolve timezone from Discord locale → 7. Parse shuffle / end if set; validate future and stop > shuffle
    # 8. Build new_event dict → 9. Under lock: if event already active return; else state.current_year + state.current_event = new_event; save
    # 10. Send join DMs to participants → 11. Edit response with success + schedule info → 12. Optional Discord log
    @ss_root.sub_command(name="start", description="Start Secret Santa (react on message to join)")
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

    @ss_root.sub_command(name="status", description="Event dashboard: participants, schedule, assignments (mod)")
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

    @ss_root.sub_command(name="shuffle", description="Pair participants and DM assignments (mod)")
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

    @ss_root.sub_command(name="stop", description="End event and archive this year (mod)")
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

    # -------------------------------------------------------------------------
    # ASK SANTA / ANSWER GIFTEE — LOGIC FLOW (step-by-step for debugging)
    # -------------------------------------------------------------------------
    # (A) Santa asks giftee: /ss ask_giftee [question]
    #     1. Defer ephemeral.
    #     2. _validate_participant_with_assignment → event, user_id (santa), receiver_id (giftee).
    #     3. Optionally AI-rewrite question; format DM text; create SecretSantaReplyView (no IDs stored).
    #     4. _send_dm(receiver_id, question_msg, view) → giftee gets DM with "Reply to Santa" button.
    #     5. _save_communication(event, santa_id, giftee_id, "question", raw, rewritten).
    # (B) Giftee answers via Reply button (on the DM):
    #     1. Button callback (secret_santa_views.SecretSantaReplyView.reply_button).
    #     2. Get cog; _get_current_event(); assignments = event.get("assignments") or {}.
    #     3. Find santa_id: giver where receiver == giftee_id (inter.author.id).
    #     4. Send SecretSantaReplyModal(santa_id, giftee_id); user types reply and submits.
    #     5. Modal callback: reply = text_values["reply_text"]; cog._process_reply(inter, reply, santa_id, giftee_id).
    #     6. _process_reply: _format_dm_reply → _send_dm(santa_id); _save_communication("reply"); followup embed to giftee.
    # (C) Giftee answers via Reply button on the DM (see secret_santa_views).
    # -------------------------------------------------------------------------

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee a question (sent anonymously)")
    async def ss_ask(
        self,
        inter: disnake.ApplicationCommandInteraction,
        question: str = commands.Param(
            description="Your question (scrubbed + anonymized before delivery)",
            max_length=2000,
        ),
    ):
        """Ask giftee anonymously; giftee replies via the DM button."""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        if not await self._rate_limit_user(inter, self._limit_ask, "question"):
            return

        # COMBINED VALIDATION: Participant + assignment check in one pass
        result = await self._validate_participant_with_assignment(inter)
        if not result:
            return
        event, user_id, receiver_id, _, _ = result
        if not await self._check_comms_cap(inter, event, user_id):
            return

        cleaned = self._scrub_user_text(question)
        if not cleaned:
            embed = self._error_embed(
                title="❌ Empty question",
                description="Your message was empty after removing @mentions. Rephrase without tagging anyone.",
            )
            await self._safe_edit_response(inter, embed=embed)
            return

        anonymized = await self._anonymize_text(cleaned, "question")

        # Send question with reply button (giftee only sees anonymized text)
        year = self.state.get("current_year", dt.date.today().year)
        question_msg = self._format_dm_question(anonymized, year)
        reply_view = SecretSantaReplyView()
        success = await self._send_dm(int(receiver_id), question_msg, reply_view)

        if success:
            # Save communication (raw + delivered text for mod oversight)
            await self._save_communication(
                event, user_id, receiver_id, "question", cleaned, anonymized
            )

            # Success embed
            embed = self._success_embed(
                title="✅ Question Sent!",
                description=(
                    "Your match received an **anonymized** version in DM "
                    "(names and @mentions are stripped; writing style is obscured)."
                ),
                footer="💡 Tip: Keep asking questions to find the perfect gift!",
            )
            embed.add_field(name="📝 You wrote", value=f"*{self._truncate_text(cleaned)}*", inline=False)
            if anonymized != cleaned:
                embed.add_field(
                    name="📨 They saw (anonymized)",
                    value=f"*{self._truncate_text(anonymized)}*",
                    inline=False,
                )
            await self._safe_edit_response(inter, embed=embed)
        else:
            embed = self._error_embed(
                title="❌ Delivery Failed",
                description="Couldn't send your question. Your match may have DMs disabled."
            )
            await self._safe_edit_response(inter, embed=embed)

    @ss_root.sub_command(name="submit_gift", description="Submit your gift for records (works for active events and current year archives)")
    async def ss_submit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        gift_description: str = commands.Param(description="Describe what you gave", max_length=2000)
    ):
        """Submit gift description - works for active events or archived current year"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        user_id = str(inter.author.id)
        current_year = self.state.get('current_year', dt.date.today().year)
        
        # OPTIMIZATION: Check if there's an active event first
        event = self._get_current_event()
        is_archived = False
        
        if event:
            # Active event - use normal flow
            result = await self._validate_participant_with_assignment(inter)
            if not result:
                return
            event, user_id, receiver_id, participants, _ = result
            receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")
        else:
            # No active event - check if archive exists for current year
            archive_path = ARCHIVE_DIR / f"{current_year}.json"
            if not archive_path.exists():
                await self._safe_edit_response(
                    inter,
                    content=f"❌ No active Secret Santa event, and no archive found for {current_year}.\n\n"
                           f"💡 If you want to edit a gift from a past year, use `/ss edit_gift [year]`"
                )
                return
            
            # Load archive and check if user participated
            archive_data = load_json(archive_path)
            if not archive_data:
                await self._safe_edit_response(inter, content=f"❌ Failed to load archive for {current_year}")
                return
            
            if "event" in archive_data:
                event_data = archive_data["event"]
                participants = event_data.get("participants") or {}
                assignments = event_data.get("assignments") or {}
                if not isinstance(participants, dict):
                    participants = {}
                if not isinstance(assignments, dict):
                    assignments = {}
            else:
                # Legacy format
                await self._safe_edit_response(
                    inter,
                    content=f"❌ Archive for {current_year} is in legacy format. Use `/ss edit_gift {current_year}` instead."
                )
                return
            
            # Check if user participated
            if user_id not in participants:
                await self._safe_edit_response(
                    inter,
                    content=f"❌ You didn't participate in Secret Santa {current_year}."
                )
                return
            
            # Check if user has assignment
            receiver_id = assignments.get(user_id)
            if not receiver_id:
                await self._safe_edit_response(
                    inter,
                    content=f"❌ No assignment found for you in {current_year} archive."
                )
                return
            
            receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")
            event = event_data  # Use event_data for consistency
            is_archived = True

        gift_submissions = event.get("gift_submissions") or {}
        if not isinstance(gift_submissions, dict):
            gift_submissions = {}
        existing_submission = gift_submissions.get(user_id)
        is_update = existing_submission is not None

        if is_archived:
            # Update archive file
            archive_path = ARCHIVE_DIR / f"{current_year}.json"
            archive_data = load_json(archive_path)
            
            if "event" in archive_data:
                event_data = archive_data["event"]
                if "gift_submissions" not in event_data:
                    event_data["gift_submissions"] = {}
                
                event_data["gift_submissions"][user_id] = {
                    "gift": gift_description,
                    "receiver_id": receiver_id,
                    "receiver_name": receiver_name,
                    "submitted_at": time.time(),
                    "timestamp": dt.datetime.now().isoformat()
                }
                
                assignments_map = event_data.get("assignments") or {}
                if not isinstance(assignments_map, dict):
                    assignments_map = {}
                total_participants = len(assignments_map)
                gs = event_data.get("gift_submissions") or {}
                gifts_exchanged = sum(1 for g in (gs.values() if isinstance(gs, dict) else []) if isinstance(g, dict) and (g.get("gift") or ""))
                completion_percentage = int((gifts_exchanged / total_participants) * 100) if total_participants > 0 else 0
                
                if "statistics" not in archive_data:
                    archive_data["statistics"] = {}
                archive_data["statistics"]["gifts_exchanged"] = gifts_exchanged
                archive_data["statistics"]["completion_percentage"] = completion_percentage
                
                # Save archive
                async with self._lock:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        self._executor,
                        save_json,
                        archive_path,
                        archive_data,
                        self.logger
                    )
        else:
            # Save to active event (normal flow)
            async with self._lock:
                event.setdefault("gift_submissions", {})[user_id] = {
                    "gift": gift_description,
                    "receiver_id": receiver_id,
                    "receiver_name": receiver_name,
                    "submitted_at": time.time(),
                    "timestamp": dt.datetime.now().isoformat()
                }
                await self._save_async()

        # Create beautiful success embed with variations
        year = current_year
        gift_templates = [
            # Variation A: Gift logged
            (f"🎁 Secret Santa {year} - GIFT LOGGED! 🎁",
             "You've marked your gift as ready!\n\nYour giftee is going to be so excited! The organizers have been notified that you're all set.\n\nOne less thing on your holiday list! ✅"),
            # Variation B: Mission accomplished
            (f"✅ Secret Santa {year} - MISSION ACCOMPLISHED! ✅",
             "Excellent! Your gift is marked as ready to go.\n\nYour giftee has no idea what's coming... but they're going to love it! 🎉\n\nOrganizers have been notified. Great work, Santa! 🎅"),
            # Variation C: Gift prepared
            (f"🌟 Secret Santa {year} - GIFT PREPARED! 🌟",
             "Perfect! You've logged your gift as complete.\n\nThe anticipation is building... your giftee is in for a wonderful surprise! ✨\n\nThe organizers are now updated. Well done! 🎄")
        ]
        title, description = random.choice(gift_templates)
        
        embed = disnake.Embed(
            title=title,
            description=description,
            color=disnake.Color.green()
        )
        embed.add_field(
            name="🎯 Recipient",
            value=f"**{receiver_name}**",
            inline=True
        )
        embed.add_field(
            name="📅 Year",
            value=f"**{self.state.get('current_year', dt.date.today().year)}**",
            inline=True
        )
        embed.add_field(
            name="⏰ Submitted",
            value=f"<t:{int(time.time())}:R>",
            inline=True
        )
        embed.add_field(
            name="🎁 Gift Description",
            value=format_gift_description_for_display(gift_description, max_length=900),
            inline=False
        )
        embed.set_footer(text="🎄 Thank you for participating in Secret Santa! Your kindness makes the season brighter.")
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/852616843715395605.png")  # Gift emoji

        await self._safe_edit_response(inter, embed=embed)

    @ss_root.sub_command(name="edit_gift", description="Edit your gift submission from a past year")
    async def ss_edit_gift(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year of the Secret Santa event"),
        gift_description: str = commands.Param(description="Updated gift description", max_length=2000)
    ):
        """Edit your own gift submission from an archived year"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        # Validate year range (same as delete_year) to avoid bad paths
        today_year = dt.date.today().year
        if year < 2020 or year > today_year + 1:
            await self._safe_edit_response(inter, content=f"❌ Invalid year {year} (must be 2020–{today_year + 1})")
            return
        user_id = str(inter.author.id)
        archive_path = ARCHIVE_DIR / f"{year}.json"
        if not archive_path.exists():
            await self._safe_edit_response(inter,
                content=f"❌ No archive found for year {year}. Make sure the year is correct!"
            )
            return
        
        try:
            # Load archive
            archive_data = load_json(archive_path)
            if not archive_data:
                await self._safe_edit_response(inter, content=f"❌ Failed to load archive for {year}")
                return
            
            # Handle both formats: list format (legacy) and unified format
            is_unified_format = False
            assignments = None
            if "assignments" in archive_data and isinstance(archive_data["assignments"], list):
                # Legacy list format
                assignments = archive_data["assignments"]
            elif "event" in archive_data and "assignments" in archive_data["event"]:
                # Unified format - convert to list for editing
                # COMBINED: Cache all event data in one pass to avoid repeated .get() calls
                is_unified_format = True
                event = archive_data["event"]
                participants = event.get("participants", {})
                assignments_map = event.get("assignments", {})
                gift_submissions = event.get("gift_submissions", {})
                
                assignments = []
                for giver_id, receiver_id in assignments_map.items():
                    gift_data = gift_submissions.get(giver_id, {})
                    gift = gift_data.get("gift") if gift_data else None
                    assignments.append({
                        "giver_id": giver_id,
                        "giver_name": participants.get(giver_id, f"User {giver_id}"),
                        "receiver_id": receiver_id,
                        "receiver_name": participants.get(receiver_id, f"User {receiver_id}"),
                        "gift": gift
                    })
            
            if not assignments:
                await self._safe_edit_response(inter, content=f"❌ No assignments found in archive for {year}")
                return
            
            # Find user's assignment
            user_assignment = None
            for assignment in assignments:
                # Check if this assignment belongs to the user
                giver_id = assignment.get("giver_id", "")
                # Match exact ID or check if it's a PLACEHOLDER that we can't match (skip those)
                if giver_id == user_id:
                    user_assignment = assignment
                    break
            
            if not user_assignment:
                await self._safe_edit_response(inter,
                    content=f"❌ You didn't participate in Secret Santa {year}, or your user ID isn't in the archive."
                )
                return
            
            # Update the gift
            old_gift = user_assignment.get("gift")
            user_assignment["gift"] = gift_description
            
            # Recalculate statistics
            total_participants = len(assignments)
            gifts_exchanged = sum(1 for a in assignments if a.get("gift"))
            completion_percentage = int((gifts_exchanged / total_participants) * 100) if total_participants > 0 else 0
            
            # If unified format, convert updated list back to unified format
            if is_unified_format:
                event = archive_data["event"]
                # Update gift_submissions in unified format
                if "gift_submissions" not in event:
                    event["gift_submissions"] = {}
                
                receiver_id = user_assignment.get("receiver_id")
                receiver_name = user_assignment.get("receiver_name", "Unknown")
                event["gift_submissions"][user_id] = {
                    "gift": gift_description,
                    "receiver_id": receiver_id,
                    "receiver_name": receiver_name
                }
                
                # Update statistics in unified format
                if "statistics" not in archive_data:
                    archive_data["statistics"] = {}
                archive_data["statistics"]["gifts_exchanged"] = gifts_exchanged
                archive_data["statistics"]["completion_percentage"] = completion_percentage
            else:
                # Legacy format - update statistics
                if "statistics" in archive_data:
                    archive_data["statistics"]["gifts_exchanged"] = gifts_exchanged
                    archive_data["statistics"]["completion_percentage"] = completion_percentage
            
            # Save updated archive (run in executor to avoid blocking event loop)
            async with self._lock:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._executor,
                    lambda: save_json(archive_path, archive_data, self.logger)
                )
            
            # Create success embed
            receiver_name = user_assignment.get("receiver_name", "Unknown")
            embed = self._success_embed(
                title="✅ Gift Updated!",
                description=f"Your gift submission for **Secret Santa {year}** has been updated!",
                footer="🎄 You can edit your gift anytime, even years later!"
            )
            embed.add_field(
                name="🎯 Recipient",
                value=f"**{receiver_name}**",
                inline=True
            )
            embed.add_field(
                name="📅 Year",
                value=f"**{year}**",
                inline=True
            )
            embed.add_field(
                name="📊 Completion",
                value=f"**{completion_percentage}%** ({gifts_exchanged}/{total_participants} gifts)",
                inline=True
            )
            if old_gift:
                embed.add_field(
                    name="📝 Old Gift",
                    value=format_gift_description_for_display(old_gift, max_length=900),
                    inline=False
                )
            embed.add_field(
                name="🎁 New Gift",
                value=format_gift_description_for_display(gift_description, max_length=900),
                inline=False
            )
            
            await self._safe_edit_response(inter, embed=embed)
            
            self.logger.info(f"User {safe_display_name(inter.author)} ({user_id}) updated their gift for {year}")
            
        except Exception as e:
            self.logger.error(f"Error editing gift for {year}: {e}", exc_info=True)
            await self._safe_edit_response(inter,
                content=f"❌ An error occurred while updating your gift: {e}"
            )
    
    @ss_edit_gift.autocomplete("year")
    async def autocomplete_year_edit_gift_decorator(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete decorator for edit_gift year parameter"""
        return await self.autocomplete_year_edit_gift(inter, string)

    @ss_root.sub_command_group(name="wishlist", description="Manage your Secret Santa wishlist")
    async def ss_wishlist(self, inter: disnake.ApplicationCommandInteraction):
        """Wishlist commands"""
        pass

    @ss_wishlist.sub_command(name="add", description="Add item to your wishlist")
    async def wishlist_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        item: str = commands.Param(description="Item to add to wishlist", max_length=500)
    ):
        """Add item to wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        if not await self._rate_limit_user(inter, self._limit_wishlist, "wishlist"):
            return

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        item = self._scrub_user_text(item) or item.strip()
        if not item:
            await self._safe_edit_response(inter, content="❌ Item text was empty after removing @mentions.")
            return

        async with self._lock:
            wishlists = event.get("wishlists")
            if not isinstance(wishlists, dict):
                wishlists = {}
                event["wishlists"] = wishlists
            user_wishlist = wishlists.get(user_id)
            if not isinstance(user_wishlist, list):
                user_wishlist = []
                wishlists[user_id] = user_wishlist
            if item.lower() in [str(w).lower() for w in user_wishlist]:
                await self._safe_edit_response(inter, content="❌ This item is already on your wishlist!")
                return
            
            # Limit wishlist size
            if len(user_wishlist) >= 10:
                await self._safe_edit_response(inter, content="❌ Wishlist full! (max 10 items). Remove some items first.")
                return
            
            # Add item
            user_wishlist.append(item)
            await self._save_async()

        year = self.state.get("current_year", dt.date.today().year)
        wishlist_templates = [
            # Variation A: Wishlist refreshed
            (f"📝 Secret Santa {year} - WISHLIST REFRESHED! 📝",
             "Your wishlist has been updated!\n\nYour Santa will appreciate the new ideas. The more they know, the more they can make your holiday sparkle! ✨",
             "Latest addition"),
            # Variation B: New ideas added
            (f"💡 Secret Santa {year} - NEW IDEAS ADDED! 💡",
             "Great thinking! Your wishlist just got an update.\n\nYour Santa is probably checking right now... these hints will help them nail the perfect gift! 🎯",
             "You added"),
            # Variation C: Hint dropped
            (f"🎯 Secret Santa {year} - HINT DROPPED! 🎯",
             "Nice! You've updated your wishlist with more clues.\n\nYour Santa's gift-spotting skills just got a major boost! They're on the case! 🔍",
             "New hint")
        ]
        title, description, field_name = random.choice(wishlist_templates)
        
        embed = self._success_embed(
            title=title,
            description=description,
            footer=f"Items: {len(user_wishlist)}/10"
        )
        embed.add_field(
            name=field_name,
            value=f"*\"{item}\"*",
            inline=False
        )
        embed.add_field(
            name="📋 Your Wishlist",
            value="\n".join(f"{i+1}. {w}" for i, w in enumerate(user_wishlist)),
            inline=False
        )
        await self._safe_edit_response(inter, embed=embed)

    @ss_wishlist.sub_command(name="remove", description="Remove item from your wishlist")
    async def wishlist_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        item_number: int = commands.Param(description="Item number to remove (1-10)", ge=1, le=10)
    ):
        """Remove item from wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        if not await self._rate_limit_user(inter, self._limit_wishlist, "wishlist"):
            return

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        wishlists = event.get("wishlists") or {}
        if not isinstance(wishlists, dict):
            wishlists = {}
        user_wishlist = wishlists.get(user_id)
        if not isinstance(user_wishlist, list):
            user_wishlist = []
        if not user_wishlist:
            await self._safe_edit_response(inter, content="❌ Your wishlist is empty!")
            return
        if item_number > len(user_wishlist):
            await self._safe_edit_response(inter, content=f"❌ Invalid item number! You only have {len(user_wishlist)} items.")
            return

        # Remove item (inside lock to prevent race with concurrent wishlist operations)
        async with self._lock:
            wishlists = event.get("wishlists") or {}
            if not isinstance(wishlists, dict):
                wishlists = {}
            user_wishlist = wishlists.get(user_id)
            if not isinstance(user_wishlist, list) or item_number > len(user_wishlist):
                await self._safe_edit_response(inter, content="❌ Wishlist changed, please try again.")
                return
            removed_item = user_wishlist.pop(item_number - 1)
            await self._save_async()

        embed = self._success_embed(
            title="✅ Item Removed!",
            description=f"Removed: **{removed_item}**",
            footer=f"Items remaining: {len(user_wishlist)}/10" if user_wishlist else "Your wishlist is now empty"
        )
        embed.color = disnake.Color.orange()
        if user_wishlist:
            embed.add_field(
                name="📋 Your Wishlist",
                value="\n".join(f"{i+1}. {w}" for i, w in enumerate(user_wishlist)),
                inline=False
            )
        await self._safe_edit_response(inter, embed=embed)
    
    @wishlist_remove.autocomplete("item_number")
    async def autocomplete_wishlist_item_number_decorator(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete decorator for wishlist remove item_number"""
        return await self.autocomplete_wishlist_item_number(inter, string)
    
    @ss_wishlist.sub_command(name="view", description="View your wishlist")
    async def wishlist_view(self, inter: disnake.ApplicationCommandInteraction):
        """View your wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result
        wishlists = event.get("wishlists") or {}
        if not isinstance(wishlists, dict):
            wishlists = {}
        user_wishlist = wishlists.get(user_id)
        if not isinstance(user_wishlist, list):
            user_wishlist = []
        if not user_wishlist:
            embed = disnake.Embed(
                title="📋 Your Wishlist",
                description="Your wishlist is empty! Add items with `/ss wishlist add`",
                color=disnake.Color.blue()
            )
            embed.set_footer(text="💡 Tip: Add gift ideas to help your Secret Santa!")
        else:
            embed = disnake.Embed(
                title="📋 Your Wishlist",
                description=f"You have **{len(user_wishlist)}** item{'s' if len(user_wishlist) != 1 else ''} on your list",
                color=disnake.Color.green()
            )
            wishlist_text = "\n".join(f"{i+1}. {item}" for i, item in enumerate(user_wishlist))
            embed.add_field(name="🎁 Items", value=wishlist_text, inline=False)
            embed.set_footer(text=f"{len(user_wishlist)}/10 items • Use /ss wishlist remove [number] to remove")
        
        await self._safe_edit_response(inter, embed=embed)

    @ss_wishlist.sub_command(name="clear", description="Clear your entire wishlist")
    async def wishlist_clear(self, inter: disnake.ApplicationCommandInteraction):
        """Clear wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        if not await self._rate_limit_user(inter, self._limit_wishlist, "wishlist"):
            return

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result
        wishlists = event.get("wishlists") or {}
        if not isinstance(wishlists, dict):
            wishlists = {}
            event["wishlists"] = wishlists
        if user_id not in wishlists:
            await self._safe_edit_response(inter, content="❌ Your wishlist is already empty!")
            return
        current = wishlists.get(user_id)
        if not isinstance(current, list) or not current:
            await self._safe_edit_response(inter, content="❌ Your wishlist is already empty!")
            return
        async with self._lock:
            wishlists[user_id] = []
            await self._save_async()

        await self._safe_edit_response(inter, content="✅ Wishlist cleared!")

    @ss_root.sub_command(name="giftee", description="View your giftee's wishlist")
    async def ss_view_giftee_wishlist(self, inter: disnake.ApplicationCommandInteraction):
        """View giftee's wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        # Check if user has assignment
        receiver_id = await self._check_assignment(inter, event, user_id)
        if not receiver_id:
            return
        receiver_id = str(receiver_id)
        participants = event.get("participants") or {}
        if not isinstance(participants, dict):
            participants = {}
        receiver_name = participants.get(receiver_id, f"User {receiver_id}")
        wishlists = event.get("wishlists") or {}
        if not isinstance(wishlists, dict):
            wishlists = {}
        giftee_wishlist = wishlists.get(receiver_id)
        if not isinstance(giftee_wishlist, list):
            giftee_wishlist = []
        match = f"<@{receiver_id}>"
        if not giftee_wishlist:
            embed = disnake.Embed(
                title=f"📋 Your match's wishlist",
                description=(
                    f"**{match}** hasn't added items yet.\n\n"
                    f"Ask with `/ss ask_giftee`."
                ),
                color=disnake.Color.blue(),
            )
            embed.set_footer(text="Check back later with `/ss giftee`.")
        else:
            embed = disnake.Embed(
                title="📋 Your match's wishlist",
                description=f"**{match}** — **{len(giftee_wishlist)}** item{'s' if len(giftee_wishlist) != 1 else ''}:",
                color=disnake.Color.gold(),
            )
            wishlist_text = "\n".join(f"{i+1}. {item}" for i, item in enumerate(giftee_wishlist))
            embed.add_field(name="🎁 Wishlist", value=wishlist_text, inline=False)
            embed.set_footer(text="Use `/ss ask_giftee` if you need more info.")
        
        await self._safe_edit_response(inter, embed=embed)

    @ss_root.sub_command(name="oversight", description="View gift submissions and/or Q&A (mod, spoilers)")
    @mod_check()
    async def ss_oversight(
        self,
        inter: disnake.ApplicationCommandInteraction,
        view: str = commands.Param(
            default="all",
            choices=["gifts", "comms", "all"],
            description="Show gifts, communications, or both (two messages if both)",
        ),
    ):
        """Mod oversight: spoilers for gifts and anonymous threads."""
        if not await self._safe_defer(inter, ephemeral=True):
            return

        event = await self._require_event(inter)
        if not event:
            return

        show_gifts = view in ("gifts", "all")
        show_comms = view in ("comms", "all")
        submissions = event.get("gift_submissions") or {}
        if not isinstance(submissions, dict):
            submissions = {}

        if show_gifts:
            if not submissions:
                await self._safe_edit_response(inter, content="❌ No gifts submitted yet")
                if not show_comms:
                    return
            else:
                participants = event.get("participants") or {}
                if not isinstance(participants, dict):
                    participants = {}
                emoji_mapping = self._get_year_emoji_mapping(participants)
                embed = disnake.Embed(
                    title=f"🎁 Gift Submissions ({len(submissions)})",
                    color=disnake.Color.green(),
                )
                for giver_id, submission in list(submissions.items())[:10]:
                    if not isinstance(submission, dict):
                        continue
                    giver_name = participants.get(giver_id, f"User {giver_id}")
                    receiver_name = submission.get("receiver_name", "Unknown")
                    raw_gift = submission.get("gift")
                    gift = format_gift_description_for_display(
                        raw_gift if isinstance(raw_gift, str) else None,
                        max_length=200,
                    )
                    giver_emoji = emoji_mapping.get(giver_id, "🎁")
                    receiver_id = submission.get("receiver_id")
                    receiver_emoji = (
                        emoji_mapping.get(str(receiver_id), "🎄") if receiver_id else "🎄"
                    )
                    embed.add_field(
                        name=f"{giver_emoji} {giver_name} → {receiver_emoji} {receiver_name}",
                        value=gift,
                        inline=False,
                    )
                if len(submissions) > 10:
                    embed.set_footer(text=f"Showing 10 of {len(submissions)} submissions")
                if show_comms:
                    await self._safe_edit_response(inter, embed=embed)
                else:
                    await self._safe_edit_response(inter, embed=embed)

        if show_comms:
            comms = event.get("communications") or {}
            if not isinstance(comms, dict):
                comms = {}
            if not comms:
                msg = "❌ No communications yet"
                if show_gifts and submissions:
                    await self._safe_followup_send(inter, content=msg, ephemeral=True)
                else:
                    await self._safe_edit_response(inter, content=msg)
                return

            participants = event.get("participants") or {}
            if not isinstance(participants, dict):
                participants = {}
            emoji_mapping = self._get_year_emoji_mapping(participants)

            if len(comms) > 5:
                paginator = CommunicationsPaginator(comms, participants, emoji_mapping, timeout=300)
                embed = paginator.get_embed()
                if show_gifts and submissions:
                    await self._safe_followup_send(inter, embed=embed, view=paginator, ephemeral=True)
                else:
                    await self._safe_edit_response(inter,embed=embed, view=paginator)
            else:
                embed = disnake.Embed(
                    title=f"💬 Communications ({len(comms)})",
                    color=disnake.Color.blue(),
                )
                for santa_id, data in comms.items():
                    if not isinstance(data, dict):
                        continue
                    santa_name = participants.get(str(santa_id), f"User {santa_id}")
                    giftee_id = data.get("giftee_id")
                    giftee_name = participants.get(str(giftee_id), "Unknown")
                    santa_emoji = emoji_mapping.get(str(santa_id), "🎅")
                    giftee_emoji = emoji_mapping.get(str(giftee_id), "🎄")
                    thread = data.get("thread") or []
                    if not isinstance(thread, list):
                        thread = []
                    lines = []
                    for msg in thread[:3]:
                        if not isinstance(msg, dict):
                            continue
                        txt = msg.get("message") or ""
                        if isinstance(txt, str) and len(txt) > 50:
                            txt = txt[:50] + "..."
                        msg_type = msg.get("type") or ""
                        emoji = santa_emoji if msg_type == "question" else giftee_emoji
                        lines.append(f"{emoji} {txt}")
                    thread_text = "\n".join(lines) if lines else "No messages"
                    embed.add_field(
                        name=f"💬 {santa_name} → {giftee_name} ({len(thread)} messages)",
                        value=thread_text,
                        inline=False,
                    )
                embed.set_footer(text=f"Total: {len(comms)} thread(s)")
                if show_gifts and submissions:
                    await self._safe_followup_send(inter, embed=embed, ephemeral=True)
                else:
                    await self._safe_edit_response(inter, embed=embed)

    @ss_root.sub_command(name="history", description="View past Secret Santa events")
    async def ss_history(
            self,
            inter: disnake.ApplicationCommandInteraction,
            year: int = commands.Param(default=None, description="Specific year to view")
    ):
        """Show event history"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        # Validate year range if provided (consistent with edit_gift / delete_year)
        today_year = dt.date.today().year
        if year is not None and (year < 2020 or year > today_year + 1):
            await self._safe_edit_response(inter, content=f"❌ Invalid year {year} (must be 2020–{today_year + 1})")
            return
        archives = load_all_archives(logger=self.logger)
        if not archives:
            await self._safe_edit_response(inter, content="❌ No archived events found")
            return
        sorted_years = sorted(archives.keys(), reverse=True)
        if year:
            if year not in archives:
                available = ", ".join(str(y) for y in sorted_years)
                await self._safe_edit_response(inter,
                    content=f"❌ No event found for {year}\n**Available years:** {available}"
                )
                return

            # Safe access - year is validated to exist in archives
            archive = archives.get(year)
            if not archive:
                await self._safe_edit_response(inter, content=f"❌ No archive found for year {year}")
                return
            event_data = archive.get("event", {})
            participants = event_data.get("participants", {})
            assignments = event_data.get("assignments", {})
            
            # Create consistent emoji mapping for all participants this year
            emoji_mapping = self._get_year_emoji_mapping(participants)
            
            # Use paginator for years with assignments
            if assignments and len(assignments) > 10:
                # Many assignments - use paginated view
                paginator = YearHistoryPaginator(year, archive, participants, emoji_mapping, timeout=300)
                embed = paginator.get_embed()
                await self._safe_edit_response(inter,embed=embed, view=paginator)
            else:
                # Few assignments - show all on one page (no buttons needed)
                gifts = event_data.get("gift_submissions", {})
                has_assignments = bool(assignments)
                gifts_count = sum(1 for gid in (assignments or {}) if ((gifts or {}).get(str(gid)) or {}).get("gift"))
                has_gifts = gifts_count > 0
                if has_gifts:
                    description = f"**{len(participants)}** participants, **{gifts_count}** gifts exchanged"
                elif has_assignments:
                    description = f"**{len(participants)}** participants, assignments made but no gifts recorded"
                else:
                    description = f"**{len(participants)}** participants signed up, event incomplete"

                embed = disnake.Embed(
                    title=f"🎄 Secret Santa {year}",
                    description=description,
                    color=disnake.Color.gold(),
                    timestamp=dt.datetime.now()
                )

                # Show all assignments (10 or fewer)
                if has_assignments:
                    exchange_lines = []
                    for giver_id, receiver_id in assignments.items():
                        giver_name = participants.get(str(giver_id), f"User {giver_id}")
                        receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")
                        
                        giver_mention = f"<@{giver_id}>" if str(giver_id).isdigit() else giver_name
                        receiver_mention = f"<@{receiver_id}>" if str(receiver_id).isdigit() else receiver_name
                        
                        giver_emoji = emoji_mapping.get(str(giver_id), "🎁")
                        receiver_emoji = emoji_mapping.get(str(receiver_id), "🎄")
                        
                        submission = gifts.get(str(giver_id))
                        if submission and isinstance(submission, dict):
                            raw = submission.get("gift")
                            gift_desc = format_gift_description_for_display(
                                raw if isinstance(raw, str) else None,
                                max_length=60,
                            )
                            exchange_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention}")
                            exchange_lines.append(f"    ⤷ {gift_desc}")
                        else:
                            exchange_lines.append(
                                f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention} *{GIFT_NO_SUBMISSION_ROW}*"
                            )
                    
                    embed.add_field(
                        name=f"🎄 Assignments & Gifts ({gifts_count}/{len(assignments)} gifts submitted)",
                        value="\n".join(exchange_lines),
                        inline=False
                    )
                else:
                    gifts_count = 0
                    status_text = f"⏸️ Signup completed ({len(participants)} joined)\n❌ No assignments made\n❌ No gifts recorded"
                    embed.add_field(name="📝 Event Status", value=status_text, inline=False)

                # Statistics (count only submissions with non-empty gift)
                completion_rate = (gifts_count / len(participants) * 100) if participants else 0
                embed.add_field(
                    name="📊 Statistics",
                    value=f"**Completion:** {completion_rate:.0f}%\n**Total Gifts:** {gifts_count}",
                    inline=True
                )

                embed.set_footer(text=f"Requested by {safe_display_name(inter.author)}")
                await self._safe_edit_response(inter, embed=embed)

        else:
            # Show all years overview with pagination
            # Use paginator if more than 10 years, otherwise show all
            if len(sorted_years) > 10:
                paginator = YearTimelinePaginator(archives, sorted_years, timeout=300)
                embed = paginator.get_embed()
                await self._safe_edit_response(inter,embed=embed, view=paginator)
            else:
                # Show all years on one page (no pagination needed)
                embed = disnake.Embed(
                    title="🎄 Secret Santa Archive",
                    description="Complete history of all Secret Santa events",
                    color=disnake.Color.blue(),
                    timestamp=dt.datetime.now()
                )

                # Create year timeline (count only submissions with non-empty gift)
                timeline_text = []
                for year_val in sorted_years:
                    archive = archives[year_val]
                    event_data = archive.get("event", {})
                    participants = event_data.get("participants", {})
                    gifts = event_data.get("gift_submissions", {})
                    assignments_y = event_data.get("assignments", {})
                    gifts_count_y = sum(1 for gid in assignments_y if (gifts.get(str(gid)) or {}).get("gift"))
                    completion_rate = (gifts_count_y / len(participants) * 100) if participants else 0

                    # Status indicator
                    if completion_rate >= 90:
                        status = "✅"
                    elif completion_rate >= 70:
                        status = "🟨"
                    elif completion_rate > 0:
                        status = "🟧"
                    else:
                        status = "⏳"

                    timeline_text.append(
                        f"**{year_val}** {status} — {len(participants)} participants, {gifts_count_y} gifts ({completion_rate:.0f}%)"
                    )

                embed.add_field(
                    name="📅 Event Timeline",
                    value="\n".join(timeline_text),
                    inline=False
                )

                # Calculate all-time statistics (real gift count per year)
                total_participants = total_gifts = 0
                for y in sorted_years:
                    event_data = archives[y].get("event", {})
                    participants_y = event_data.get("participants", {})
                    gifts_y = event_data.get("gift_submissions", {})
                    assignments_y = event_data.get("assignments", {})
                    total_participants += len(participants_y)
                    total_gifts += sum(1 for gid in assignments_y if (gifts_y.get(str(gid)) or {}).get("gift"))
                avg_participants = total_participants / len(sorted_years) if sorted_years else 0
                avg_completion = (total_gifts / total_participants * 100) if total_participants else 0

                stats_text = [
                    f"**Total Events:** {len(sorted_years)}",
                    f"**Total Participants:** {total_participants}",
                    f"**Total Gifts Given:** {total_gifts}",
                    f"**Average per Year:** {avg_participants:.0f} participants",
                    f"**Overall Completion:** {avg_completion:.0f}%"
                ]

                embed.add_field(
                    name="📊 All-Time Statistics",
                    value="\n".join(stats_text),
                    inline=False
                )

                embed.add_field(
                    name="📖 Status Legend",
                    value="✅ 90%+ complete | 🟨 70-89% | 🟧 Under 70% | ⏳ No gifts recorded",
                    inline=False
                )

                embed.set_footer(
                    text=f"Use /ss history [year] for detailed view • Requested by {safe_display_name(inter.author)}")
                await self._safe_edit_response(inter, embed=embed)
    
    @ss_history.autocomplete("year")
    async def autocomplete_year_history_decorator(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete decorator for history year parameter"""
        return await self.autocomplete_year_history(inter, string)

    @ss_root.sub_command(name="user_history", description="View a specific user's Secret Santa history across all years")
    @mod_check()
    async def ss_user_history(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="User to look up")
    ):
        """Show specific user's participation across all years"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        user_id = str(user.id)
        
        # Load all archives using shared helper (no duplication!)
        archives = load_all_archives(logger=self.logger)
        
        if not archives:
            await self._safe_edit_response(inter, content="❌ No archived events found")
            return
        
        # Find user's participation across all years
        participations = []
        
        for year in sorted(archives.keys()):
            archive_entry = archives.get(year)
            if not isinstance(archive_entry, dict):
                continue
            event_data = archive_entry.get("event") or {}
            if not isinstance(event_data, dict):
                continue
            participants = event_data.get("participants") or {}
            assignments = event_data.get("assignments") or {}
            gifts = event_data.get("gift_submissions") or {}
            if not isinstance(participants, dict):
                participants = {}
            if not isinstance(assignments, dict):
                assignments = {}
            if not isinstance(gifts, dict):
                gifts = {}
            if user_id not in participants:
                continue
            user_name = participants.get(user_id, f"User {user_id}")
            
            # Find who they gave to
            gave_to_id = assignments.get(user_id)
            gave_to_name = participants.get(str(gave_to_id), "Unknown") if gave_to_id else None
            
            # Find what gift they gave (normalize: only None or non-empty string for display safety)
            gift_data = gifts.get(user_id)
            gift_desc = None
            if gift_data and isinstance(gift_data, dict):
                raw = gift_data.get("gift")
                if isinstance(raw, str) and raw.strip():
                    gift_desc = raw
                # else leave None so display shows "(no gift recorded)"
            
            # Find who gave to them
            received_from_id = None
            received_from_name = None
            received_gift = None
            
            for giver_id, receiver_id in assignments.items():
                if str(receiver_id) == user_id:
                    received_from_id = giver_id
                    received_from_name = participants.get(giver_id, "Unknown")
                    giver_gift = gifts.get(giver_id)
                    if giver_gift and isinstance(giver_gift, dict):
                        raw = giver_gift.get("gift")
                        if isinstance(raw, str) and raw.strip():
                            received_gift = raw
                    break
            
            participations.append({
                "year": year,
                "gave_to_name": gave_to_name,
                "gave_to_id": gave_to_id,
                "gift_given": gift_desc,
                "received_from_name": received_from_name,
                "received_from_id": received_from_id,
                "gift_received": received_gift
            })
        
        if not participations:
            embed = disnake.Embed(
                title=f"🎄 Secret Santa History - {user.display_name}",
                description=f"{user.mention} has never participated in Secret Santa.",
                color=disnake.Color.red()
            )
            embed.set_footer(text="Maybe this year! 🎅")
            await self._safe_edit_response(inter, embed=embed)
            return
        
        # Build beautiful history embed
        embed = disnake.Embed(
            title=f"🎄 Secret Santa History - {user.display_name}",
            description=f"**{len(participations)} year{'s' if len(participations) != 1 else ''}** of participation",
            color=disnake.Color.gold(),
            timestamp=dt.datetime.now()
        )
        
        # Show each year's participation
        for participation in reversed(participations):  # Most recent first
            year = participation["year"]
            
            # Build year summary
            year_lines = []
            
            # What they gave
            if participation["gave_to_name"]:
                gave_to_mention = f"<@{participation['gave_to_id']}>" if participation['gave_to_id'] else participation['gave_to_name']
                year_lines.append(f"🎁 **Gave to:** {gave_to_mention}")
                if participation["gift_given"] and isinstance(participation["gift_given"], str):
                    year_lines.append(
                        f"   └─ {format_gift_description_for_display(participation['gift_given'], max_length=80)}"
                    )
                else:
                    year_lines.append(f"   └─ {format_gift_description_for_display(None)}")
            else:
                year_lines.append(f"🎁 **Gave to:** *(assignment not found)*")
            
            # What they received
            if participation["received_from_name"]:
                received_from_mention = f"<@{participation['received_from_id']}>" if participation['received_from_id'] else participation['received_from_name']
                year_lines.append(f"🎅 **Received from:** {received_from_mention}")
                if participation["gift_received"] and isinstance(participation["gift_received"], str):
                    year_lines.append(
                        f"   └─ {format_gift_description_for_display(participation['gift_received'], max_length=80)}"
                    )
                else:
                    year_lines.append(f"   └─ {format_gift_description_for_display(None)}")
            else:
                year_lines.append(f"🎅 **Received from:** *(unknown)*")
            
            embed.add_field(
                name=f"🎄 {year}",
                value="\n".join(year_lines),
                inline=False
            )
        
        # Add summary statistics
        total_gifts_given = sum(1 for p in participations if p["gift_given"])
        total_gifts_received = sum(1 for p in participations if p["gift_received"])
        
        stats_text = f"**Years Participated:** {len(participations)}\n"
        stats_text += f"**Gifts Given:** {total_gifts_given}/{len(participations)}\n"
        stats_text += f"**Gifts Received:** {total_gifts_received}/{len(participations)}"
        
        embed.add_field(
            name="📊 User Statistics",
            value=stats_text,
            inline=False
        )
        
        embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
        embed.set_footer(text=f"Requested by {safe_display_name(inter.author)}")
        
        await self._safe_edit_response(inter, embed=embed)

    @ss_root.sub_command_group(name="archive", description="Archive maintenance (mod)")
    async def ss_archive(self, inter: disnake.ApplicationCommandInteraction):
        """Archive backup and restore commands"""
        pass

    @ss_archive.sub_command(name="delete", description="Move an archive year to backups (careful)")
    @mod_check()
    async def ss_archive_delete(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year to delete")
    ):
        """Delete archive file for a specific year (admin only)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        # Safety check - don't allow deleting very old years accidentally
        current_year = dt.date.today().year
        if year < 2020 or year > current_year + 1:
            await self._safe_edit_response(inter, content=f"❌ Invalid year {year} (must be 2020-{current_year + 1})")
            return
        
        # CRITICAL SAFETY CHECK: Prevent deleting current active year
        # If there's an active event for this year, deletion could cause data loss
        active_event = self._get_current_event()
        if active_event and self.state.get("current_year") == year:
            embed = disnake.Embed(
                title="🛑 Cannot Delete Active Year",
                description=f"**Year {year} has an active Secret Santa event!**\n\n"
                            f"You must stop the event first with `/ss stop` before deleting the archive.\n\n"
                            f"This prevents accidental data loss from an ongoing event.",
                color=disnake.Color.red()
            )
            embed.add_field(
                name="🔒 Protection Active",
                value="**What to do:**\n"
                      "1. Run `/ss stop` to end and archive the current event\n"
                      "2. Then you can safely delete the archive if needed\n\n"
                      "**Or** wait until the event is complete!",
                inline=False
            )
            embed.set_footer(text="Safety first! Your active event data is protected.")
            await self._safe_edit_response(inter, embed=embed)
            return
        
        archive_path = ARCHIVE_DIR / f"{year}.json"
        
        if not archive_path.exists():
            await self._safe_edit_response(inter, content=f"❌ No archive found for {year}")
            return
        
        # INDESTRUCTIBLE BACKUP SYSTEM: Move to backups folder instead of deleting
        backup_path = BACKUPS_DIR / f"{year}.json"
        
        # Check if backup already exists
        if backup_path.exists():
            embed = disnake.Embed(
                title="⚠️ Backup Already Exists",
                description=f"A backup for **{year}** already exists in the backups folder!",
                color=disnake.Color.yellow()
            )
            embed.add_field(
                name="🤔 What happened?",
                value=f"You've already deleted {year} before. The backup is preserved.\n\n"
                      f"If you want to replace it:\n"
                      f"1. Manually delete `backups/{year}.json`\n"
                      f"2. Run this command again",
                inline=False
            )
            embed.set_footer(text="The current archive was NOT moved to prevent overwriting the existing backup.")
            await self._safe_edit_response(inter, embed=embed)
            return
        
        try:
            # MOVE to backups folder (not copy - this is the key!)
            import shutil
            shutil.move(str(archive_path), str(backup_path))
            
            embed = disnake.Embed(
                title="🛡️ Archive Moved to Backups",
                description=f"Archive for **{year}** has been safely moved to backups!",
                color=disnake.Color.green()
            )
            embed.add_field(
                name="✅ Indestructible Backup",
                value=f"**Location:** `archive/backups/{year}.json`\n\n"
                      f"• Not permanently deleted - just isolated\n"
                      f"• Bot commands ignore backups folder\n"
                      f"• Restore anytime with `/ss archive restore year:{year}`\n\n"
                      f"**This system makes data loss nearly impossible!**",
                inline=False
            )
            embed.add_field(
                name="⚠️ Important Note",
                value=f"**This command does NOT start a new Secret Santa event!**\n\n"
                      f"• It only moves the {year} archive to backups\n"
                      f"• No new event is created\n"
                      f"• To start a new event, use `/ss start`\n"
                      f"• To shuffle an existing event, use `/ss shuffle`",
                inline=False
            )
            embed.set_footer(text="💡 Use /ss archive backups to view all backed-up years")
            
            await self._safe_edit_response(inter, embed=embed)
            
            # Log to Discord
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"🛡️ {safe_display_name(inter.author)} moved Secret Santa {year} to backups (safely archived)",
                    "INFO"
                )
            
        except Exception as e:
            self.logger.error(f"Failed to move archive to backups: {e}")
            await self._safe_edit_response(inter, content=f"❌ Failed to move archive: {e}")
    
    @ss_archive_delete.autocomplete("year")
    async def autocomplete_year_delete_decorator(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete decorator for archive delete year parameter"""
        return await self.autocomplete_year_delete(inter, string)

    @ss_archive.sub_command(name="restore", description="Restore a year from backups")
    @mod_check()
    async def ss_archive_restore(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year to restore")
    ):
        """Restore archive file from backups folder (admin only)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        today_year = dt.date.today().year
        if year < 2020 or year > today_year + 1:
            await self._safe_edit_response(inter, content=f"❌ Invalid year {year} (must be 2020–{today_year + 1})")
            return
        backup_path = BACKUPS_DIR / f"{year}.json"
        archive_path = ARCHIVE_DIR / f"{year}.json"
        
        # Check if backup exists
        if not backup_path.exists():
            # List available backups to help user
            available_backups = sorted([int(f.stem) for f in BACKUPS_DIR.glob("[0-9][0-9][0-9][0-9].json")])
            
            if available_backups:
                backups_str = ", ".join(str(y) for y in available_backups)
                await self._safe_edit_response(inter,
                    content=f"❌ No backup found for {year}\n\n**Available backups:** {backups_str}"
                )
            else:
                await self._safe_edit_response(inter,
                    content=f"❌ No backup found for {year} (backups folder is empty)"
                )
            return
        
        # Check if archive already exists (don't overwrite!)
        if archive_path.exists():
            embed = disnake.Embed(
                title="⚠️ Archive Already Exists",
                description=f"An archive for **{year}** already exists in the active archives!",
                color=disnake.Color.yellow()
            )
            embed.add_field(
                name="🤔 What happened?",
                value=f"Cannot restore because `{year}.json` already exists.\n\n"
                      f"**Options:**\n"
                      f"1. Delete the current archive with `/ss archive delete year:{year}`\n"
                      f"2. Manually move/rename the current archive\n"
                      f"3. Keep the current archive (backup remains safe)",
                inline=False
            )
            embed.set_footer(text="Protection: Prevents accidental overwrites!")
            await self._safe_edit_response(inter, embed=embed)
            return
        
        try:
            # MOVE from backups to active archives
            import shutil
            shutil.move(str(backup_path), str(archive_path))
            
            embed = disnake.Embed(
                title="♻️ Archive Restored Successfully",
                description=f"Archive for **{year}** has been restored to active archives!",
                color=disnake.Color.green()
            )
            embed.add_field(
                name="✅ What Changed",
                value=f"**From:** `archive/backups/{year}.json`\n"
                      f"**To:** `archive/{year}.json`\n\n"
                      f"• Now visible in `/ss history`\n"
                      f"• Used by shuffle algorithm\n"
                      f"• Counts toward user history\n\n"
                      f"**The year is back in action!**",
                inline=False
            )
            embed.set_footer(text="💡 Restoration complete!")
            
            await self._safe_edit_response(inter, embed=embed)
            
            # Log to Discord
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"♻️ {safe_display_name(inter.author)} restored Secret Santa {year} from backups",
                    "INFO"
                )
            
        except Exception as e:
            self.logger.error(f"Failed to restore archive from backups: {e}")
            await self._safe_edit_response(inter, content=f"❌ Failed to restore archive: {e}")
    
    @ss_archive_restore.autocomplete("year")
    async def autocomplete_year_restore_decorator(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete decorator for archive restore year parameter"""
        return await self.autocomplete_year_restore(inter, string)

    @ss_archive.sub_command(name="backups", description="List years in the backups folder")
    @mod_check()
    async def ss_archive_backups(self, inter: disnake.ApplicationCommandInteraction):
        """List all years in the backups folder (admin only)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        # Scan backups folder for year files
        backup_files = sorted(BACKUPS_DIR.glob("[0-9][0-9][0-9][0-9].json"))
        
        if not backup_files:
            embed = disnake.Embed(
                title="📋 Backed-Up Years",
                description="✅ No years in backups (all archives are active!)",
                color=disnake.Color.green()
            )
            embed.set_footer(text="Use /ss archive delete to move archives to backups")
            await self._safe_edit_response(inter, embed=embed)
            return
        
        # Build list of backed-up years with file sizes
        backup_list = []
        for backup_file in backup_files:
            year = backup_file.stem
            size_kb = backup_file.stat().st_size / 1024
            backup_list.append(f"**{year}** - {size_kb:.1f} KB")
        
        # Use paginator if more than 15 backups, otherwise show all
        if len(backup_list) > 15:
            paginator = BackupListPaginator(backup_list, timeout=300)
            embed = paginator.get_embed()
            await self._safe_edit_response(inter,embed=embed, view=paginator)
        else:
            # Show all backups on one page (no pagination needed)
            embed = disnake.Embed(
                title="📋 Backed-Up Years",
                description=f"Found **{len(backup_files)}** year(s) in backups folder:",
                color=disnake.Color.blue()
            )
            
            embed.add_field(
                name="Years",
                value="\n".join(backup_list),
                inline=False
            )
            
            embed.add_field(
                name="🔧 Actions",
                value=f"• Restore a year: `/ss restore_year [year]`\n"
                      f"• View all active years: `/ss history`\n"
                      f"• Bot ignores backups folder automatically",
                inline=False
            )
            
            embed.set_footer(text=f"Location: archive/backups/")
            await self._safe_edit_response(inter, embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction adds for joining"""
        if payload.user_id == self.bot.user.id:
            return

        # COMBINED CHECK: Get event and validate all conditions in one pass
        event = self.state.get("current_event")
        if not event or not event.get("active") or event.get("join_closed") or payload.message_id != event.get("announcement_message_id"):
            return
        
        user_id = str(payload.user_id)
        participants = event.get("participants") or {}
        if not isinstance(participants, dict):
            return
        if user_id in participants:
            return

        # Get user name
        name = f"User {payload.user_id}"
        if payload.guild_id:
            try:
                guild = self.bot.get_guild(payload.guild_id)
                if guild:
                    member = guild.get_member(payload.user_id)
                    if member:
                        name = member.display_name
            except Exception:
                pass

        # Add participant only if event is still current (re-check inside lock)
        async with self._lock:
            current = self.state.get("current_event")
            if not current or not current.get("active") or current.get("join_closed"):
                return
            if current.get("guild_id") and payload.guild_id and current.get("guild_id") != payload.guild_id:
                return
            if current.get("announcement_message_id") != payload.message_id:
                return  # Event was stopped or is different
            if "participants" not in current:
                current["participants"] = {}
            if user_id in current["participants"]:
                return
            current["participants"][user_id] = name
            await self._save_async()

        # Participant role on join (if configured on /ss start)
        if payload.guild_id:
            guild = self.bot.get_guild(payload.guild_id)
            if guild:
                await self._apply_participant_role(
                    guild, payload.user_id, add=True, reason="Secret Santa signup (reaction)"
                )

        # Send confirmation (same message as /ss start); rate-limit react spam
        if await self._limit_join_dm.check(str(payload.user_id)):
            join_msg = self._get_join_message(self.state.get("current_year", dt.date.today().year))
            await self._send_dm(payload.user_id, join_msg)
        else:
            self.logger.debug("Join DM skipped (rate limit) for user %s", payload.user_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction removes for leaving"""
        if payload.user_id == self.bot.user.id:
            return

        # COMBINED CHECK: Get event and validate all conditions in one pass
        event = self.state.get("current_event")
        if not event or not event.get("active") or event.get("join_closed") or payload.message_id != event.get("announcement_message_id"):
            return
        
        user_id = str(payload.user_id)
        participants = event.get("participants") or {}
        if not isinstance(participants, dict):
            return
        if user_id not in participants:
            return

        # Check if user has other reactions on the message
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)

            # Check all reactions
            has_reaction = False
            for reaction in message.reactions:
                async for user in reaction.users():
                    if user.id == payload.user_id:
                        has_reaction = True
                        break
                if has_reaction:
                    break

            # Remove if no reactions (only if event is still current and active)
            if not has_reaction:
                async with self._lock:
                    current = self.state.get("current_event")
                    if not current or not current.get("active") or current.get("announcement_message_id") != payload.message_id:
                        return  # Event was stopped or is different, don't modify
                    participants = current.get("participants")
                    if isinstance(participants, dict):
                        participants.pop(user_id, None)
                    await self._save_async()

                if payload.guild_id:
                    guild = self.bot.get_guild(payload.guild_id)
                    if guild:
                        await self._apply_participant_role(
                            guild, payload.user_id, add=False, reason="Left Secret Santa signup"
                        )

                leave_msg = self._get_leave_message(self.state.get("current_year", dt.date.today().year))
                await self._send_dm(payload.user_id, leave_msg)

        except Exception as e:
            self.logger.error(f"Error handling reaction remove: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent reply button view once — works after bot restarts
        if getattr(self, "_reply_view_registered", False):
            return
        self.bot.add_view(SecretSantaReplyView())
        self._reply_view_registered = True