"""Secret Santa slash commands — moderation, archive, event listeners (mixin)."""
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


class SecretSantaAdminMixin:
    @SecretSantaRootMixin.ss_root.sub_command(name="oversight", description="View gift submissions and/or Q&A (mod, spoilers)")
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

    @SecretSantaRootMixin.ss_root.sub_command(name="history", description="View past Secret Santa events")
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

    @SecretSantaRootMixin.ss_root.sub_command(name="user_history", description="View a specific user's Secret Santa history across all years")
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

    @SecretSantaRootMixin.ss_root.sub_command_group(name="archive", description="Archive maintenance (mod)")
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
