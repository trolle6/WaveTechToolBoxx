"""Secret Santa slash commands — participant commands (mixin)."""
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


class SecretSantaParticipantMixin:
    @SecretSantaRootMixin.ss_root.sub_command(name="ask_giftee", description="Ask your giftee a question (sent anonymously)")
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

    @SecretSantaRootMixin.ss_root.sub_command(name="submit_gift", description="Submit your gift for records (works for active events and current year archives)")
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

    @SecretSantaRootMixin.ss_root.sub_command(name="edit_gift", description="Edit your gift submission from a past year")
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

    @SecretSantaRootMixin.ss_root.sub_command_group(name="wishlist", description="Manage your Secret Santa wishlist")
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

    @SecretSantaRootMixin.ss_root.sub_command(name="giftee", description="View your giftee's wishlist")
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
