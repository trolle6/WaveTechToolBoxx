"""
Secret Santa Cog - Complete Event Management System

FEATURES:
- 🎄 Event creation with reaction-based signup
- 🎲 Smart assignment algorithm with history tracking (avoids repeats)
- 💬 Anonymous communication between Santas and giftees (AI-rewritten)
- 🎁 Gift submission tracking with beautiful embeds
- 📊 Multi-year history viewing (by year or by user)
- 🔒 Archive protection (prevents accidental data loss)

COMMANDS (Moderator):
- /ss start [message_id] [role_id] [shuffle_date] [shuffle_time] [end_date] [end_time] - Start new event (optional auto-shuffle and auto-stop)
- /ss shuffle - Make Secret Santa assignments
- /ss stop - Stop event and archive data (manual stop, cancels scheduled stop if set)
- /ss participants - View current participants
- /ss view_gifts - View submitted gifts
- /ss view_comms - View communication threads

COMMANDS (Participant):
- /ss ask_giftee [question] - Ask your giftee anonymously (includes instant reply button)
- /ss reply_santa [reply] - Reply to your Secret Santa
- /ss submit_gift [description] - Record your gift
- /ss wishlist add [item] - Add item to your wishlist
- /ss wishlist remove [number] - Remove item from wishlist
- /ss wishlist view - View your wishlist
- /ss wishlist clear - Clear your wishlist
- /ss giftee - See your giftee's wishlist

COMMANDS (Anyone):
- /ss history - View all years overview
- /ss history [year] - View specific year details
- /ss user_history @user - View one user's complete history
- /ss test_emoji_consistency @user - Test emoji consistency across years
- /ss edit_gift [year] [description] - Edit your gift submission from any past year

SAFETY FEATURES:
- ✅ Cryptographic randomness (secrets.SystemRandom)
- ✅ Archive overwrite protection (saves to backup if year exists)
- ✅ Progressive fallback (excludes old years if needed)
- ✅ State persistence (survives bot restarts)
- ✅ Automatic hourly backups
- ✅ Atomic file writes (prevents corruption)
- ✅ Validation on state load
- ✅ Health monitoring (disk space, permissions, early failure detection)

DATA STORAGE:
- secret_santa_state.json - Active event state
- secret_santa_state.backup - Backup if main fails
- archive/YYYY.json - Completed events by year
- archive/YYYY_backup_TIMESTAMP.json - Protected overwrites

ALGORITHM:
1. Collect participants via reactions
2. Load history from all archive files
3. Make assignments avoiding past pairings
4. Fall back to older years if needed
5. Send DMs with assignments
6. Track communications and gifts
7. Archive on event stop
"""

from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import time
from typing import Any, Dict, List, Optional

import disnake
from disnake.ext import commands

from .owner_utils import owner_check, get_owner_mention

# Import from modular components
from .secret_santa_storage import (
    ARCHIVE_DIR, BACKUPS_DIR, STATE_FILE,
    load_state_with_fallback, save_state, load_all_archives, archive_event,
    load_json, save_json
)
from .secret_santa_assignments import (
    load_history_from_archives, validate_assignment_possibility, make_assignments
)
from .secret_santa_views import (
    SecretSantaReplyView, SecretSantaReplyModal, YearHistoryPaginator
)
from .secret_santa_checks import mod_check, participant_check

# Constants
BACKUP_INTERVAL_SECONDS = 3600  # 1 hour - how often to backup state
SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS = 60  # 1 minute - how often to check for scheduled events

# Log the paths for debugging
import logging
_init_logger = logging.getLogger("bot.santa.init")
_init_logger.info(f"Secret Santa cog file: {__file__}")
_init_logger.info(f"Archive directory: {ARCHIVE_DIR}")
_init_logger.info(f"Archive exists: {ARCHIVE_DIR.exists()}")
if ARCHIVE_DIR.exists():
    files = list(ARCHIVE_DIR.glob("*.json"))
    _init_logger.info(f"Archive files found: {[f.name for f in files]}")



class SecretSantaCog(commands.Cog):
    """Secret Santa event management"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("santa")

        # Load state with multi-layer fallback and validation
        # 1. Try main state file → 2. Try backup → 3. Use defaults
        self.state = load_state_with_fallback(logger=self.logger)

        self._lock = asyncio.Lock()
        self._backup_task: Optional[asyncio.Task] = None
        self._scheduled_shuffle_task: Optional[asyncio.Task] = None
        self._unloaded = False  # Track if already unloaded
        
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
        return event if event and event.get("active") else None
    
    def _get_available_years(self) -> List[int]:
        """Get list of available years from archive directory"""
        years = []
        for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
            # Skip files in backups subdirectory
            if "backups" in archive_file.parts:
                continue
            year_str = archive_file.stem
            if year_str.isdigit() and len(year_str) == 4:
                try:
                    years.append(int(year_str))
                except ValueError:
                    continue
        return sorted(years, reverse=True)  # Most recent first
    
    async def _autocomplete_year(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete function for year selection - shows available years"""
        try:
            available_years = self._get_available_years()
            if not available_years:
                return []  # Return empty list instead of error message for autocomplete
            
            # Filter years that match the input string
            string_lower = string.lower() if string else ""
            matching_years = [
                str(year) for year in available_years 
                if string_lower in str(year) or not string
            ]
            
            # Return up to 25 options (Discord limit)
            return matching_years[:25]
        except Exception as e:
            self.logger.error(f"Error in year autocomplete: {e}", exc_info=True)
            return []  # Always return a list, even on error
    
    # Autocomplete methods need to be referenced correctly - create wrapper methods
    async def autocomplete_year_edit_gift(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for edit_gift year parameter"""
        return await self._autocomplete_year(inter, string)
    
    async def autocomplete_year_history(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for history year parameter"""
        return await self._autocomplete_year(inter, string)
    
    async def autocomplete_year_delete(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for delete_year year parameter"""
        return await self._autocomplete_year(inter, string)
    
    async def autocomplete_year_restore(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for restore_year year parameter"""
        return await self._autocomplete_year(inter, string)
    
    async def autocomplete_wishlist_item_number(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for wishlist remove item_number - shows valid item numbers from user's wishlist"""
        try:
            # NOTE: Cannot use _validate_participant here as it sends responses, which breaks autocomplete
            # Instead, silently check if user is participant without sending any response
            event = self._get_current_event()
            if not event or not event.get("active"):
                return []
            
            user_id = str(inter.author.id)
            if user_id not in event.get("participants", {}):
                return []
            
            wishlists = event.get("wishlists", {})
            user_wishlist = wishlists.get(user_id, [])
            
            if not user_wishlist:
                return []
            
            # Return item numbers (1-indexed) as strings
            valid_numbers = [str(i + 1) for i in range(len(user_wishlist))]
            
            # Filter by input string
            if string:
                valid_numbers = [num for num in valid_numbers if string in num]
            
            return valid_numbers[:25]
        except Exception as e:
            self.logger.error(f"Error in wishlist autocomplete: {e}", exc_info=True)
            return []  # Always return a list, even on error
    
    async def _validate_participant(self, inter: disnake.ApplicationCommandInteraction) -> Optional[tuple]:
        """
        Validate user is participant in active event.
        Returns (event, user_id) if valid, None otherwise (sends error response).
        COMBINED: Gets event + checks participants in one pass.
        """
        event = self._get_current_event()
        if not event:
            await inter.edit_original_response(content="There's no Secret Santa event running right now. Maybe soon!")
            return None
        
        user_id = str(inter.author.id)
        # COMBINED CHECK: Get participants once and check membership
        if user_id not in event.get("participants", {}):
            await inter.edit_original_response(content="Hmm, it doesn't look like you're signed up for Secret Santa this year!")
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
            await inter.edit_original_response(content="There's no Secret Santa event running right now. Maybe soon!")
            return None
        
        user_id = str(inter.author.id)
        participants = event.get("participants", {})
        assignments = event.get("assignments", {})
        
        # Check participant status
        if user_id not in participants:
            await inter.edit_original_response(content="Hmm, it doesn't look like you're signed up for Secret Santa this year!")
            return None
        
        # Check assignment status
        receiver_id = assignments.get(user_id)
        if not receiver_id:
            embed = self._error_embed(
                title="🎅 Hold your reindeer!",
                description="You don't have a giftee yet! The organizer still needs to run the shuffle. Good things come to those who wait!"
            )
            await inter.edit_original_response(embed=embed)
            return None
        
        return (event, user_id, receiver_id, participants, assignments)
    
    def _error_embed(self, title: str, description: str, footer: Optional[str] = None) -> disnake.Embed:
        """Create a standard error embed"""
        return self._create_embed(title, description, disnake.Color.red(), **({"footer": footer} if footer else {}))
    
    def _success_embed(self, title: str, description: str, footer: Optional[str] = None) -> disnake.Embed:
        """Create a standard success embed"""
        return self._create_embed(title, description, disnake.Color.green(), **({"footer": footer} if footer else {}))
    
    def _truncate_text(self, text: Optional[str], max_length: int = 100) -> str:
        """Truncate text with ellipsis if needed. Handles None values."""
        if not text:
            return ""
        return f"{text[:max_length]}..." if len(text) > max_length else text
    
    async def _require_event(self, inter: disnake.ApplicationCommandInteraction, custom_message: Optional[str] = None) -> Optional[dict]:
        """Require active event. Returns event if active, None otherwise (sends error response)"""
        event = self._get_current_event()
        if not event:
            msg = custom_message or "❌ No active event"
            await inter.edit_original_response(content=msg)
            return None
        return event
    
    async def _check_assignment(self, inter: disnake.ApplicationCommandInteraction, event: dict, user_id: str) -> Optional[str]:
        """Check if user has assignment. Returns receiver_id if valid, None otherwise (sends error response)"""
        if user_id not in event.get("assignments", {}):
            embed = self._error_embed(
                title="🎅 Hold your reindeer!",
                description="You don't have a giftee yet! The organizer still needs to run the shuffle. Good things come to those who wait!"
            )
            await inter.edit_original_response(embed=embed)
            return None
        return event["assignments"][user_id]
    
    def _find_santa_for_giftee(self, event: dict, giftee_id: str) -> Optional[int]:
        """Find the Santa (giver) for a given giftee (receiver). Returns santa_id as int, or None"""
        return next((int(giver) for giver, receiver in event.get("assignments", {}).items() if receiver == giftee_id), None)
    
    async def _save_communication(self, event: dict, santa_id: str, giftee_id: str, msg_type: str, 
                                  message: str, rewritten: str):
        """Save communication thread entry"""
        async with self._lock:
            comms = event.setdefault("communications", {})
            thread = comms.setdefault(santa_id, {"giftee_id": giftee_id, "thread": []})
            thread["thread"].append({
                "type": msg_type,
                "message": message,
                "rewritten": rewritten,
                "timestamp": time.time()
            })
            self._save()
    
    def _format_dm_question(self, rewritten_question: str, year: int) -> str:
        """Format a question for DM"""
        templates = [
            # Variation A: Curious
            lambda q: (
                f"❓ **Secret Santa {year} - YOUR SANTA IS CURIOUS!** ❓\n\n"
                f"Ooh, your Santa has a question for you! They're wondering:\n\n"
                f"*\"{q}\"*\n\n"
                f"---\n\n"
                f"**Want to help them solve the puzzle?**\n"
                f"Reply below or use `/ss reply_santa [your answer]`\n\n"
                f"Every hint helps them find that perfect gift! 🔍"
            ),
            # Variation B: Clue Request
            lambda q: (
                f"🔍 **Secret Santa {year} - CLUE REQUEST!** 🔍\n\n"
                f"Your Santa's on a treasure hunt for the ideal gift! They need a little direction:\n\n"
                f"*\"{q}\"*\n\n"
                f"---\n\n"
                f"**Care to drop a hint?**\n"
                f"Reply here or type `/ss reply_santa [your thoughts]`\n\n"
                f"You're their best guide to gift-giving success! 🗺️"
            ),
            # Variation C: Thinking of you
            lambda q: (
                f"💭 **Secret Santa {year} - YOUR SANTA IS THINKING OF YOU!** 💭\n\n"
                f"Your Santa's brainstorming gift ideas and would love your input:\n\n"
                f"*\"{q}\"*\n\n"
                f"---\n\n"
                f"**Want to share your thoughts?**\n"
                f"Hit reply below or use `/ss reply_santa`\n\n"
                f"They're putting so much care into finding you something special! ❤️"
            )
        ]
        return secrets.choice(templates)(rewritten_question)
    
    def _format_dm_reply(self, rewritten_reply: str, year: int) -> str:
        """Format a reply for DM"""
        templates = [
            # Variation A: Wrote back
            lambda r: (
                f"🎅 **Secret Santa {year} - YOUR GIFTEE WROTE BACK!** 🎅\n\n"
                f"Great news! Your giftee responded:\n\n"
                f"*\"{r}\"*\n\n"
                f"---\n\n"
                f"**Need more info?**\n"
                f"Ask another question with `/ss ask_giftee`\n\n"
                f"You're getting closer to that \"perfect gift\" moment! ✨"
            ),
            # Variation B: Message incoming
            lambda r: (
                f"💌 **Secret Santa {year} - MESSAGE INCOMING!** 💌\n\n"
                f"Your giftee sent a reply! Here's what they said:\n\n"
                f"*\"{r}\"*\n\n"
                f"---\n\n"
                f"**Ready for another question?**\n"
                f"Use `/ss ask_giftee` to keep the conversation going!\n\n"
                f"The clues are adding up! 🧩"
            ),
            # Variation C: Plot thickens
            lambda r: (
                f"✨ **Secret Santa {year} - THE PLOT THICKENS!** ✨\n\n"
                f"Interesting! Your giftee just shared this:\n\n"
                f"*\"{r}\"*\n\n"
                f"---\n\n"
                f"**Want to dig deeper?**\n"
                f"Ask follow-up questions with `/ss ask_giftee`\n\n"
                f"You're like a gift detective on a holiday case! 🕵️‍♂️🎁"
            )
        ]
        return secrets.choice(templates)(rewritten_reply)
    
    def _get_join_message(self, year: int) -> str:
        """Get the join message for participants"""
        templates = [
            # Variation A: Welcome aboard
            lambda y: (
                f"🎉 **Secret Santa {y} - WELCOME ABOARD!** 🎉\n\n"
                f"You're officially on the nice list! 🎅\n\n"
                f"Get ready for some holiday magic! We'll message you here once you've been matched with your giftee.\n\n"
                f"In the meantime, why not add some wishlist ideas? It helps your own Santa out! 🎄"
            ),
            # Variation B: So glad you're here
            lambda y: (
                f"✨ **Secret Santa {y} - SO GLAD YOU'RE HERE!** ✨\n\n"
                f"Welcome to this year's Secret Santa adventure!\n\n"
                f"We'll DM you with your special assignment once the shuffle happens. The magic begins soon! ❄️\n\n"
                f"Pro tip: Add a few wishlist items now to give your Santa a head start! 🎁"
            ),
            # Variation C: You're in
            lambda y: (
                f"❤️ **Secret Santa {y} - YOU'RE IN!** ❤️\n\n"
                f"Yay! You've joined the holiday fun!\n\n"
                f"Keep an eye on your DMs - we'll send your giftee assignment here when everything's ready.\n\n"
                f"Why not sprinkle some hints on your wishlist? Your Santa will thank you! 🤫"
            )
        ]
        return secrets.choice(templates)(year)
    
    def _get_assignment_message(self, year: int, receiver_id: int, receiver_name: str) -> str:
        """Get the assignment message for a Santa"""
        opening_messages = [
            "🎅 **The elves have spoken!** You're the Secret Santa for **{receiver}**!",
            "🎄 **The festive stars have aligned!** You'll be gifting **{receiver}**!",
            "✨ **You've been matched!** Get ready to spread some joy to **{receiver}**!",
            "🦌 **Rudolph's nose lit up for you!** You're gifting **{receiver}** this year!",
            "🎁 **Your mission, should you choose to accept it:** Make **{receiver}**'s holiday sparkle!",
            "❄️ **A little winter magic just paired you with** **{receiver}**!",
            "✨ **A sprinkle of holiday magic just paired you with** **{receiver}**!",
            "🔮 **The festive crystal ball reveals...** your giftee is **{receiver}**!",
            "🎇 **By the power of tinsel and cheer, you shall gift** **{receiver}**!",
            "🕯️ **The candlelight of Yule shines upon...** **{receiver}**!",
            "🌟 **A shooting star carried your name straight to** **{receiver}**!",
            "🧙‍♂️ **The Great Holiday Wizard has decreed:** You shall gift **{receiver}**!"
        ]
        
        # Three different message templates for variety
        templates = [
            # Template 1: Mission-focused
            lambda opening, name: (
                f"🎯 **Secret Santa {year} - YOUR SPECIAL MISSION!** 🎯\n\n"
                f"{opening}\n\n"
                f"---\n\n"
                f"**Your Giftee:** {name}\n\n"
                f"Let the gift planning begin! Check their wishlist with `/ss giftee` and remember... shhh, it's a secret! 🤫"
            ),
            # Template 2: Adventure-focused
            lambda opening, name: (
                f"🎁 **Secret Santa {year} - YOUR GIFTING ADVENTURE!** 🎁\n\n"
                f"{opening}\n\n"
                f"---\n\n"
                f"**Time to spoil:** {name}\n\n"
                f"Ready to make their holiday magical? Start by checking `/ss giftee` to see what they're hoping for! The journey begins now! ✨"
            ),
            # Template 3: Magic-focused
            lambda opening, name: (
                f"✨ **Secret Santa {year} - THE MAGIC BEGINS!** ✨\n\n"
                f"{opening}\n\n"
                f"---\n\n"
                f"**Your lucky giftee:** {name}\n\n"
                f"Time to work your Santa magic! Peek at their wishlist with `/ss giftee` and start planning something amazing. Keep it secret, keep it safe! 🎄"
            )
        ]
        
        opening = secrets.choice(opening_messages).format(receiver=f'<@{receiver_id}> ({receiver_name})')
        template = secrets.choice(templates)
        return template(opening, receiver_name)
    
    def _get_event_end_message(self, year: int) -> str:
        """Get the event end message for participants"""
        templates = [
            # Variation A: And that's a wrap
            lambda y: (
                f"✨ **Secret Santa {y} - AND THAT'S A WRAP!** ✨\n\n"
                f"A huge, heartfelt thank you to everyone who participated! 🎁\n\n"
                f"Because of all of you, this holiday season just got a whole lot warmer and brighter. The joy you've shared is the real gift.\n\n"
                f"Until next year! Stay merry and bright! 🎄❤️"
            ),
            # Variation B: Mission complete
            lambda y: (
                f"🎄 **Secret Santa {y} - MISSION COMPLETE!** 🎄\n\n"
                f"And just like that, another wonderful Secret Santa comes to a close.\n\n"
                f"Thank you for spreading so much joy and holiday magic. You've made someone's season truly special.\n\n"
                f"Wishing you all the warmth and happiness this holiday brings! ❤️"
            ),
            # Variation C: Thanks for the magic
            lambda y: (
                f"🌟 **Secret Santa {y} - THANKS FOR THE MAGIC!** 🌟\n\n"
                f"The final sleigh bell has rung! Secret Santa {y} is complete.\n\n"
                f"What an amazing gift-giving journey it's been! Thank you for your kindness, creativity, and holiday spirit.\n\n"
                f"May your holidays be as bright as the smiles you've created! ✨🎅"
            )
        ]
        return secrets.choice(templates)(year)
    
    def _get_leave_message(self, year: int) -> str:
        """Get the leave message for participants"""
        return (
            f"👋 **Secret Santa {year} - WE'LL MISS YOU!** 👋\n\n"
            f"You've left this year's Secret Santa.\n\n"
            f"Your spot has been cleared and you won't be matched with anyone.\n\n"
            f"Changed your mind? You can always rejoin before the shuffle happens! ❤️"
        )
    
    # State loading now uses load_state_with_fallback from secret_santa_storage module

    async def cog_load(self):
        """Initialize cog"""
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
            
            self.logger.info("Secret Santa cog unloaded")
        except Exception as e:
            self.logger.error(f"Async unload error: {e}")

    def _save(self):
        """Save state to disk with error handling and backup"""
        return save_state(self.state, logger=self.logger)

    async def _backup_loop(self):
        """Periodic backup"""
        try:
            while True:
                await asyncio.sleep(BACKUP_INTERVAL_SECONDS)
                async with self._lock:
                    self._save()
        except asyncio.CancelledError:
            pass

    async def _scheduled_shuffle_checker(self):
        """Background task that checks for scheduled shuffles and stops, and executes them"""
        try:
            while True:
                await asyncio.sleep(SCHEDULED_EVENT_CHECK_INTERVAL_SECONDS)
                
                event = self._get_current_event()
                if not event:
                    continue
                
                current_time = time.time()
                
                # Check if there's a scheduled shuffle
                scheduled_shuffle_time = event.get("scheduled_shuffle_time")
                if scheduled_shuffle_time and current_time >= scheduled_shuffle_time:
                    # Time to shuffle! Get scheduler ID before clearing
                    scheduler_id = event.get("scheduled_by_user_id")
                    
                    # Clear the schedule first to prevent double execution
                    async with self._lock:
                        event.pop("scheduled_shuffle_time", None)
                        event.pop("scheduled_by_user_id", None)
                        self._save()
                    
                    # Execute the shuffle (without interaction, so we pass None for inter)
                    try:
                        await self._execute_shuffle_internal(scheduler_id=scheduler_id)
                        
                        # Notify the scheduler
                        if scheduler_id:
                            try:
                                user = await self.bot.fetch_user(scheduler_id)
                                year = self.state.get('current_year', dt.date.today().year)
                                await user.send(
                                    f"🎲 **Secret Santa {year} - THE CARDS HAVE BEEN DEALT!** 🎲\n\n"
                                    f"The shuffle is complete! Everyone has been matched with their giftee!\n\n"
                                    f"The magic is officially in motion... ✨\n\n"
                                    f"All Santas should now have a DM with their special assignment. The gift-giving adventure begins!"
                                )
                            except Exception as e:
                                self.logger.warning(f"Failed to notify scheduler {scheduler_id}: {e}")
                    except Exception as e:
                        self.logger.error(f"Error executing scheduled shuffle: {e}", exc_info=True)
                        # Try to notify scheduler about the error
                        if scheduler_id:
                            try:
                                user = await self.bot.fetch_user(scheduler_id)
                                await user.send(
                                    f"❌ **Oops – the scheduled shuffle hit a snag!**\n\n"
                                    f"Something went wrong while trying to make the assignments:\n"
                                    f"`{str(e)}`\n\n"
                                    f"You'll need to run `/ss shuffle` manually to get everyone paired up."
                                )
                            except Exception:
                                pass
                
                # Check if there's a scheduled stop
                scheduled_stop_time = event.get("scheduled_stop_time")
                if scheduled_stop_time and current_time >= scheduled_stop_time:
                    # Time to stop! Get stopper ID before clearing
                    stopper_id = event.get("scheduled_stop_by_user_id")
                    
                    # Execute the stop (this will clear the scheduled_stop_time internally)
                    try:
                        success, saved_filename = await self._execute_stop_internal(stopper_id=stopper_id)
                        if success:
                            # Notify the stopper
                            if stopper_id:
                                try:
                                    user = await self.bot.fetch_user(stopper_id)
                                    await user.send(
                                        f"🛑 **Auto-stop complete!** Your Secret Santa event is now officially wrapped up.\n\n"
                                        f"Everything's been saved to: `{saved_filename}`\n\n"
                                        f"All your participants have gotten their \"event's over\" DM."
                                    )
                                except Exception as e:
                                    self.logger.warning(f"Failed to notify stopper {stopper_id}: {e}")
                        else:
                            self.logger.error(f"Scheduled stop returned error: {saved_filename}")
                            if stopper_id:
                                try:
                                    user = await self.bot.fetch_user(stopper_id)
                                    await user.send(
                                        f"❌ **Couldn't auto-stop the event.**\n\n"
                                        f"Ran into an issue while trying to wrap things up:\n"
                                        f"`{saved_filename}`\n\n"
                                        f"Please run `/ss stop` manually to end the event."
                                    )
                                except Exception:
                                    pass
                    except Exception as e:
                        self.logger.error(f"Error executing scheduled stop: {e}", exc_info=True)
                        if stopper_id:
                            try:
                                user = await self.bot.fetch_user(stopper_id)
                                await user.send(
                                    f"❌ **Couldn't auto-stop the event.**\n\n"
                                    f"Ran into an issue while trying to wrap things up:\n"
                                    f"`{str(e)}`\n\n"
                                    f"Please run `/ss stop` manually to end the event."
                                )
                            except Exception:
                                pass
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Scheduled events checker error: {e}", exc_info=True)

    async def _send_dm(self, user_id: int, message: str, view: disnake.ui.View = None) -> bool:
        """Send DM to user with optional view"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message, view=view)
            return True
        except disnake.Forbidden as e:
            # User has DMs disabled or blocked the bot (error code 50007)
            # This is expected and common - only log at debug level
            error_code = getattr(e, 'code', None)
            if error_code == 50007:
                self.logger.debug(f"User {user_id} has DMs disabled (50007) - skipping DM")
            else:
                self.logger.debug(f"User {user_id} blocked DM (Forbidden: {error_code})")
            return False
        except disnake.HTTPException as e:
            # Other HTTP errors (rate limits, etc.) - log as warning
            self.logger.warning(f"HTTP error sending DM to {user_id}: {e}")
            return False
        except Exception as e:
            # Unexpected errors - log as warning
            self.logger.warning(f"Unexpected error sending DM to {user_id}: {e}")
            return False

    async def _process_reply(self, inter: disnake.ModalInteraction, reply: str, santa_id: int, giftee_id: int):
        """Process a reply from giftee to santa"""
        try:
            # Send reply to santa
            reply_msg = self._format_dm_reply(reply, self.state['current_year'])
            success = await self._send_dm(santa_id, reply_msg)

            if success:
                # Save communication
                event = self._get_current_event()
                if event:
                    await self._save_communication(event, str(santa_id), str(giftee_id), "reply", reply, reply)

                # Success embed for giftee
                embed = self._success_embed(
                    title=f"💌 Secret Santa {self.state['current_year']} - REPLY DELIVERED! 💌",
                    description="Your message is now in your Santa's hands! ✨\n\nThey'll be thrilled to get your response. Good hints make for great gifts! 🎁",
                    footer=""
                )
                embed.add_field(name="What you sent", value=f"*{self._truncate_text(reply)}*", inline=False)
                await inter.followup.send(embed=embed, ephemeral=True)
            else:
                embed = self._error_embed(
                    title="❌ Message couldn't be delivered",
                    description="Looks like we couldn't send your reply. Your Secret Santa might have their DMs closed."
                )
                await inter.followup.send(embed=embed, ephemeral=True)
                
        except Exception as e:
            self.logger.error(f"Error processing reply: {e}")
            await inter.followup.send(content="Yikes – something went wrong while sending your message. Could you try again?", ephemeral=True)

    def _get_year_emoji_mapping(self, participants: Dict[str, str]) -> Dict[str, str]:
        """
        Create consistent emoji mapping for all participants.
        Each user gets the same emoji across ALL years based on their user ID hash.
        This makes it easy to track a specific user's participation across history.
        """
        # Christmas emoji pool for participants
        emoji_pattern = ["🎁", "🎄", "🎅", "⭐", "❄️", "☃️", "🦌", "🔔", "🍪", "🥛", "🕯️", "✨", "🌟", "🎈", "🧸", "🍭", "🎂", "🎪", "🎨", "🎯"]
        
        emoji_mapping = {}
        for participant_id in participants.keys():
            # Use hash of user ID to get consistent emoji across all years
            # Same user = same emoji, always!
            user_hash = hash(int(participant_id) if participant_id.isdigit() else participant_id)
            emoji_index = user_hash % len(emoji_pattern)
            emoji_mapping[participant_id] = emoji_pattern[emoji_index]
        
        return emoji_mapping

    def _get_openai_headers(self) -> Dict[str, str]:
        """Get common OpenAI API headers"""
        if not hasattr(self.bot.config, 'OPENAI_API_KEY') or not self.bot.config.OPENAI_API_KEY:
            return {}
        return {
            "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

    async def _anonymize_text(self, text: str, message_type: str = "question") -> str:
        """Use OpenAI to rewrite text for anonymity"""
        headers = self._get_openai_headers()
        if not headers:
            return text
        
        try:
            # Single prompt template (question/reply use same logic)
            base_prompt = "Rewrite this Secret Santa {type} with MINIMAL changes - just enough to obscure writing style. "
            base_prompt += "Keep 80-90% of the original words and phrasing. Only change a few words here and there. "
            base_prompt += "Preserve the exact same meaning, tone, personality, slang, and emotion. "
            base_prompt += "If they're casual, stay casual. If they use emojis, keep them. If they misspell, that's fine.\n\n"
            base_prompt += f"Original: {text}\n\nRewritten:"
            
            prompt = base_prompt.format(type=message_type)
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,  # Allow longer responses to preserve original length
                "temperature": 0.2  # Very low temperature for minimal changes
            }
            
            # Use reasonable timeout for anonymization
            session = await self.bot.http_mgr.get_session(timeout=20)
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    rewritten = result["choices"][0]["message"]["content"].strip()
                    # Remove common AI response prefixes
                    rewritten = rewritten.replace("Rewritten:", "").strip()
                    return rewritten if rewritten else text
                else:
                    self.logger.debug(f"Anonymization failed: {resp.status}")
                    return text
                    
        except Exception as e:
            self.logger.debug(f"Anonymization error: {e}")
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

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.ApplicationCommandInteraction):
        """Secret Santa commands"""
        pass

    @ss_root.sub_command(name="start", description="Start a Secret Santa event")
    @owner_check()
    async def ss_start(
        self,
        inter: disnake.ApplicationCommandInteraction,
        announcement_message_id: str = commands.Param(description="Message ID for reactions"),
        role_id: Optional[str] = commands.Param(default=None, description="Optional: Role ID to assign participants"),
        shuffle_date: Optional[str] = commands.Param(default=None, description="Optional: Date to auto-shuffle (REQUIRES shuffle_time if set)"),
        shuffle_time: Optional[str] = commands.Param(default=None, description="⚠️ REQUIRED if shuffle_date is set! Time to auto-shuffle (e.g., '14:30' or '2:30 PM')"),
        end_date_preset: Optional[str] = commands.Param(
            default=None,
            choices=["December 24", "December 25", "December 31", "January 1", "Custom"],
            description="Optional: Quick preset for end date (REQUIRES end_time below if set)"
        ),
        end_date: Optional[str] = commands.Param(default=None, description="Optional: Custom date to auto-stop (only if end_date_preset is 'Custom')"),
        end_time: Optional[str] = commands.Param(default=None, description="⚠️ REQUIRED if end_date_preset is set! Time to auto-stop (e.g., '23:59' or '11:59 PM')")
    ):
        """Start new Secret Santa event (optionally schedule automatic shuffle)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate message ID
        try:
            msg_id = int(announcement_message_id)
        except ValueError:
            await inter.edit_original_response(content="❌ Invalid message ID")
            return

        # Validate role ID if provided
        role_id_int = None
        if role_id:
            try:
                role_id_int = int(role_id)
            except ValueError:
                await inter.edit_original_response(content="❌ Invalid role ID")
                return

        # Check if event already active
        event = self.state.get("current_event")
        if event and event.get("active"):
            await inter.edit_original_response(content="❌ Event already active")
            return

        # SAFETY WARNING: Check if current year is already archived
        # Prevents accidental data loss if you test on wrong server or run twice
        current_year = dt.date.today().year
        existing_archive = ARCHIVE_DIR / f"{current_year}.json"
        if existing_archive.exists():
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
            await inter.edit_original_response(embed=embed)
            
            # Log this warning
            self.logger.warning(f"Starting new event for {current_year} but archive already exists!")
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"⚠️ {inter.author.display_name} is starting a new Secret Santa {current_year} event, but {current_year}.json archive already exists!",
                    "WARNING"
                )
            
            # Wait 5 seconds so user can read the warning, then continue
            await asyncio.sleep(5)

        # Find message and collect participants
        participants = {}
        found = False

        for channel in inter.guild.text_channels:
            try:
                message = await channel.fetch_message(msg_id)
                found = True

                # Collect users who reacted
                for reaction in message.reactions:
                    async for user in reaction.users():
                        if user.bot:
                            continue

                        if str(user.id) not in participants:
                            member = inter.guild.get_member(user.id)
                            name = member.display_name if member else user.name
                            participants[str(user.id)] = name

                break
            except (disnake.NotFound, disnake.Forbidden):
                continue

        if not found:
            await inter.edit_original_response(content="❌ Message not found")
            return

        # Parse and validate shuffle schedule if provided
        scheduled_timestamp = None
        if shuffle_date and shuffle_time:
            scheduled_timestamp = self._parse_datetime(shuffle_date, shuffle_time)
            if not scheduled_timestamp:
                await inter.edit_original_response(
                    content="❌ Invalid date or time format!\n\n"
                           "**Date formats:**\n"
                           "• `2025-12-25` (YYYY-MM-DD)\n"
                           "• `12/25/2025` (MM/DD/YYYY)\n"
                           "• `December 25, 2025`\n\n"
                           "**Time formats:**\n"
                           "• `14:30` (24-hour)\n"
                           "• `2:30 PM` (12-hour)\n\n"
                           "**Example:** `/ss start ... shuffle_date:2025-12-25 shuffle_time:2:30 PM`"
                )
                return
            
            # Check if scheduled time is in the past
            current_time = time.time()
            if scheduled_timestamp <= current_time:
                await inter.edit_original_response(
                    content="❌ Scheduled shuffle time must be in the future!\n\n"
                           f"Current time: <t:{int(current_time)}:F>\n"
                           f"Your time: <t:{int(scheduled_timestamp)}:F>"
                )
                return
        elif shuffle_date or shuffle_time:
            # One provided but not the other
            await inter.edit_original_response(
                content="❌ Both `shuffle_date` and `shuffle_time` must be provided together, or leave both empty for manual shuffle."
            )
            return

        # Parse and validate stop schedule if provided
        scheduled_stop_timestamp = None
        
        # Handle end date preset or custom
        actual_end_date = None
        if end_date_preset:
            if end_date_preset == "Custom":
                # Use custom date if provided
                if end_date:
                    actual_end_date = end_date
                else:
                    await inter.edit_original_response(
                        content="❌ You selected 'Custom' for end_date_preset but didn't provide `end_date`.\n\n"
                               "Either:\n"
                               "• Choose a preset date (December 24, 25, 31, or January 1)\n"
                               "• Or provide both `end_date` and `end_time` when using 'Custom'"
                    )
                    return
            else:
                # Use preset date - map to current year (reuse current_year from above)
                preset_map = {
                    "December 24": f"December 24, {current_year}",
                    "December 25": f"December 25, {current_year}",
                    "December 31": f"December 31, {current_year}",
                    "January 1": f"January 1, {current_year + 1}"  # Next year for Jan 1
                }
                actual_end_date = preset_map.get(end_date_preset)
        
        # If no preset but custom date provided, use it
        if not actual_end_date and end_date:
            actual_end_date = end_date
        
        # Validate stop schedule
        if actual_end_date and end_time:
            scheduled_stop_timestamp = self._parse_datetime(actual_end_date, end_time)
            if not scheduled_stop_timestamp:
                await inter.edit_original_response(
                    content="❌ Invalid end date or time format!\n\n"
                           "**Date formats:**\n"
                           "• `2025-12-31` (YYYY-MM-DD)\n"
                           "• `12/31/2025` (MM/DD/YYYY)\n"
                           "• `December 31, 2025`\n\n"
                           "**Time formats:**\n"
                           "• `23:59` (24-hour)\n"
                           "• `11:59 PM` (12-hour)\n\n"
                           "**Example:** `/ss start ... end_date_preset:December 31 end_time:11:59 PM`\n"
                           "**Or:** `/ss start ... end_date_preset:Custom end_date:2025-12-31 end_time:11:59 PM`"
                )
                return
            
            # Check if scheduled stop time is in the future
            current_time = time.time()
            if scheduled_stop_timestamp <= current_time:
                await inter.edit_original_response(
                    content="❌ Scheduled stop time must be in the future!\n\n"
                           f"Current time: <t:{int(current_time)}:F>\n"
                           f"Your time: <t:{int(scheduled_stop_timestamp)}:F>"
                )
                return
            
            # Check if stop is after shuffle (if shuffle is scheduled)
            if scheduled_timestamp and scheduled_stop_timestamp <= scheduled_timestamp:
                await inter.edit_original_response(
                    content="❌ Scheduled stop time must be after shuffle time!\n\n"
                           f"Shuffle: <t:{int(scheduled_timestamp)}:F>\n"
                           f"Stop: <t:{int(scheduled_stop_timestamp)}:F>"
                )
                return
        elif (actual_end_date and not end_time) or (end_time and not actual_end_date):
            # One provided but not the other
            await inter.edit_original_response(
                content="❌ Both end date (preset or custom) and `end_time` must be provided together, or leave both empty for manual stop.\n\n"
                       "**Quick preset:** Use `end_date_preset` (e.g., 'December 25') + `end_time`\n"
                       "**Custom date:** Use `end_date_preset:Custom` + `end_date` + `end_time`"
            )
            return
        elif end_date_preset == "Custom" and not end_date:
            # Custom selected but no custom date provided
            await inter.edit_original_response(
                content="❌ You selected 'Custom' for end_date_preset but didn't provide `end_date`.\n\n"
                       "Either:\n"
                       "• Choose a preset date (December 24, 25, 31, or January 1)\n"
                       "• Or provide both `end_date` and `end_time` when using 'Custom'"
            )
            return

        # Create event (current_year already set above during safety check)
        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": msg_id,
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

        async with self._lock:
            self.state["current_year"] = current_year
            self.state["current_event"] = new_event
            self._save()

        # Send confirmation DMs
        join_msg = self._get_join_message(current_year)
        dm_tasks = [self._send_dm(int(uid), join_msg) for uid in participants]

        results = await asyncio.gather(*dm_tasks, return_exceptions=True)
        successful = sum(1 for r in results if r is True)

        # Build response message
        response_msg = (
            f"✅ Secret Santa {current_year} started!\n"
            f"• Participants: {len(participants)}\n"
            f"• DMs sent: {successful}/{len(participants)}"
        )
        if role_id_int:
            response_msg += f"\n• Role ID: {role_id_int}"
        
        if scheduled_timestamp:
            response_msg += f"\n\n📅 **Shuffle scheduled for:** <t:{int(scheduled_timestamp)}:F>\n"
            response_msg += f"🎉 You'll be notified when it happens!"
        
        if scheduled_stop_timestamp:
            preset_info = ""
            if end_date_preset and end_date_preset != "Custom":
                preset_info = f" (preset: {end_date_preset})"
            response_msg += f"\n\n🛑 **Event will auto-stop on:** <t:{int(scheduled_stop_timestamp)}:F>{preset_info}\n"
            response_msg += f"✨ Event will archive automatically!"
        
        await inter.edit_original_response(response_msg)
        
        # Notify Discord log channel
        log_msg = f"Secret Santa {current_year} event started by {inter.author.display_name} - {len(participants)} participants joined"
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
        event = self._get_current_event()
        if not event:
            error_msg = "❌ No active event - use `/ss start` to create one first"
            if inter:
                await inter.edit_original_response(content=error_msg)
            return False, error_msg

        # Convert participant IDs to integers
        participants = [int(uid) for uid in event["participants"]]

        if len(participants) < 2:
            error_msg = "❌ Need at least 2 participants"
            if inter:
                await inter.edit_original_response(content=error_msg)
            return False, error_msg

        # Get guild for role assignment (if inter provided, use it; otherwise try to get from event)
        guild = None
        if inter:
            guild = inter.guild
        elif event.get("guild_id"):
            guild = self.bot.get_guild(event["guild_id"])

        # HISTORY LOADING: Load all past Secret Santa events from archive files
        history, available_years = load_history_from_archives(ARCHIVE_DIR, exclude_years=[], logger=self.logger)
        
        self.logger.info(f"Attempting Secret Santa assignment with {len(participants)} participants")
        self.logger.info(f"Available history years: {available_years}")
        
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
                history, _ = load_history_from_archives(ARCHIVE_DIR, exclude_years=exclude_years, logger=self.logger)
                
                if inter:
                    years_str = ", ".join(map(str, exclude_years))
                    await inter.edit_original_response(
                        content=f"⚠️ Initial assignment difficult... trying fallback (excluding {years_str})..."
                    )
            
            validation_error = validate_assignment_possibility(participants, history)
            if validation_error:
                if attempt == len(available_years):
                    error_msg = f"❌ {validation_error}"
                    if inter:
                        await inter.edit_original_response(content=error_msg)
                    if hasattr(self.bot, 'send_to_discord_log'):
                        await self.bot.send_to_discord_log(
                            f"Secret Santa assignment failed even with all fallbacks - {validation_error}",
                            "ERROR"
                        )
                    return False, error_msg
                continue
            
            try:
                assignments = make_assignments(participants, history)
                break
            except ValueError as e:
                if attempt == len(available_years):
                    error_msg = f"❌ Assignment failed: {e}"
                    if inter:
                        await inter.edit_original_response(content=error_msg)
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
                await inter.edit_original_response(content=error_msg)
            return False, error_msg

        # Assign role to participants (if role_id was provided)
        if guild and event.get("role_id"):
            role = guild.get_role(event["role_id"])
            if role and guild.me.guild_permissions.manage_roles:
                for user_id in participants:
                    try:
                        member = guild.get_member(user_id)
                        if member and role not in member.roles:
                            await member.add_roles(role, reason="Secret Santa participant")
                    except Exception:
                        pass

        # Send assignment DMs
        dm_tasks = []
        for giver, receiver in assignments.items():
            receiver_name = event["participants"].get(str(receiver), f"User {receiver}")
            msg = self._get_assignment_message(self.state['current_year'], receiver, receiver_name)
            dm_tasks.append(self._send_dm(giver, msg))

        await asyncio.gather(*dm_tasks)

        # Save assignments
        async with self._lock:
            event["assignments"] = {str(k): str(v) for k, v in assignments.items()}
            event["join_closed"] = True
            # Clear any scheduled shuffle since we just executed
            event.pop("scheduled_shuffle_time", None)
            event.pop("scheduled_by_user_id", None)
            self._save()

        # Build success message
        response_msg = f"✅ Assignments complete!\n"
        response_msg += f"• {len(assignments)} pairs created\n"
        response_msg += f"• DMs sent to all participants\n"
        response_msg += f"• History respected (no repeated pairings!)\n"
        
        if fallback_used:
            years_str = ", ".join(map(str, exclude_years))
            response_msg += f"\n⚠️ **Fallback used:** Excluded history from {years_str} to make assignments possible\n"
            response_msg += f"💡 Consider having Secret Santa more frequently to avoid this!"
        
        if inter:
            await inter.edit_original_response(content=response_msg)
        
        # Notify Discord log channel
        executor_name = inter.author.display_name if inter else (f"User {scheduler_id}" if scheduler_id else "Scheduled task")
        if hasattr(self.bot, 'send_to_discord_log'):
            log_msg = f"Secret Santa assignments completed by {executor_name} - {len(assignments)} pairs created"
            if fallback_used:
                log_msg += f" (fallback: excluded years {', '.join(map(str, exclude_years))})"
            await self.bot.send_to_discord_log(log_msg, "SUCCESS" if not fallback_used else "WARNING")
        
        return True, None

    async def _execute_stop_internal(self, stopper_id: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """
        Internal method to execute stop logic. Can be called from manual command or scheduled task.
        
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        event = self._get_current_event()
        if not event:
            return False, "❌ No active event"
        
        year = self.state["current_year"]
        
        # Send thank you message to all participants
        participants = event.get("participants", {})
        if participants:
            end_msg = self._get_event_end_message(year)
            dm_tasks = [self._send_dm(int(uid), end_msg) for uid in participants]
            await asyncio.gather(*dm_tasks, return_exceptions=True)
        
        # Archive event (with automatic backup protection)
        saved_filename = self._archive_event(event, year)
        
        async with self._lock:
            # Clear scheduled stop before clearing event
            event.pop("scheduled_stop_time", None)
            event.pop("scheduled_stop_by_user_id", None)
            self.state["current_event"] = None
            self._save()
        
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

    def _parse_datetime(self, date_str: str, time_str: str) -> Optional[float]:
        """
        Parse date and time strings into a Unix timestamp.
        
        Supports intuitive formats:
        - Date: "YYYY-MM-DD", "MM/DD/YYYY", "December 25, 2025"
        - Time: "HH:MM" (24-hour), "HH:MM AM/PM" (12-hour)
        
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
            
            # Combine date and time
            datetime_obj = dt.datetime.combine(date_obj, time_obj)
            
            # Convert to Unix timestamp
            return datetime_obj.timestamp()
            
        except Exception as e:
            self.logger.debug(f"Date/time parsing error: {e}")
            return None

    @ss_root.sub_command(name="shuffle", description="🔧 Manually assign Secret Santas (emergency/fallback)")
    @owner_check()
    async def ss_shuffle(self, inter: disnake.ApplicationCommandInteraction):
        """Make assignments manually (use /ss start with shuffle_date/shuffle_time for automatic execution)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        # COMBINED: Get event and check/cancel scheduled shuffle in one pass
        event = self._get_current_event()
        scheduled_time = event.get("scheduled_shuffle_time") if event else None
        if scheduled_time:
            async with self._lock:
                event.pop("scheduled_shuffle_time", None)
                event.pop("scheduled_by_user_id", None)
                self._save()
            self.logger.info(f"Manual shuffle cancelled scheduled shuffle (was scheduled for <t:{int(scheduled_time)}:F>)")
        
        success, error = await self._execute_shuffle_internal(inter=inter)
        if not success and error:
            # Error already sent to inter
            pass

    @ss_root.sub_command(name="stop", description="Stop the Secret Santa event")
    @mod_check()
    async def ss_stop(self, inter: disnake.ApplicationCommandInteraction):
        """Stop event"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # COMBINED: Get event and check/cancel scheduled stop in one pass
        event = self._get_current_event()
        if not event:
            await inter.edit_original_response(content="❌ No active event")
            return

        # Check if there's a scheduled stop and cancel it
        cancelled_scheduled = False
        scheduled_time = event.get("scheduled_stop_time")
        if scheduled_time:
            async with self._lock:
                event.pop("scheduled_stop_time", None)
                event.pop("scheduled_stop_by_user_id", None)
                self._save()
            cancelled_scheduled = True
            self.logger.info(f"Manual stop cancelled scheduled stop (was scheduled for <t:{int(scheduled_time)}:F>)")

        # Execute stop using the helper function
        success, saved_filename = await self._execute_stop_internal(stopper_id=inter.author.id)
        
        if not success:
            await inter.edit_original_response(content=saved_filename or "❌ Failed to stop event")
            return

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
                description=(response_msg if response_msg else "") + f"Secret Santa {self.state['current_year']} has been archived with data protection!",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="🔒 Archive Protection",
                value=f"**Original:** `{self.state['current_year']}.json` (preserved)\n"
                      f"**This event:** `{saved_filename}`\n\n"
                      f"⚠️ You ran multiple {self.state['current_year']} events! Review archives folder manually.",
                inline=False
            )
            embed.set_footer(text="Your original archive was NOT overwritten!")
            await inter.edit_original_response(embed=embed)
        else:
            # Normal archive
            if response_msg:
                await inter.edit_original_response(content=response_msg + f"✅ Event stopped and archived → `{saved_filename}`")
            else:
                await inter.edit_original_response(content=f"✅ Event stopped and archived → `{saved_filename}`")

    @ss_root.sub_command(name="participants", description="View participants")
    @mod_check()
    async def ss_participants(self, inter: disnake.ApplicationCommandInteraction):
        """Show participants"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        event = await self._require_event(inter)
        if not event:
            return

        participants = event.get("participants", {})
        if not participants:
            await inter.edit_original_response(content="❌ No participants yet")
            return

        embed = disnake.Embed(
            title=f"🎄 Participants ({len(participants)})",
            color=disnake.Color.green()
        )

        # Group participants for display
        lines = [f"• {name} (<@{uid}>)" for uid, name in list(participants.items())[:20]]

        if len(participants) > 20:
            lines.append(f"... and {len(participants) - 20} more")

        embed.description = "\n".join(lines)

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee a question (sent anonymously)")
    async def ss_ask(
        self,
        inter: disnake.ApplicationCommandInteraction,
        question: str = commands.Param(description="Your question (sent as-is for anonymity)", max_length=2000),
        use_ai_rewrite: bool = commands.Param(default=False, description="Use AI to rewrite for extra anonymity")
    ):
        """Ask giftee anonymously with AI rewriting"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # COMBINED VALIDATION: Participant + assignment check in one pass
        result = await self._validate_participant_with_assignment(inter)
        if not result:
            return
        event, user_id, receiver_id, _, _ = result

        # Rewrite question for anonymity (only if requested)
        if use_ai_rewrite:
            await inter.edit_original_response(content="🤖 Rewriting your question for extra anonymity...")
            rewritten_question = await self._anonymize_text(question, "question")
        else:
            rewritten_question = question

        # Send question with reply button
        question_msg = self._format_dm_question(rewritten_question, self.state['current_year'])
        reply_view = SecretSantaReplyView()
        success = await self._send_dm(int(receiver_id), question_msg, reply_view)

        if success:
            # Save communication
            await self._save_communication(event, user_id, receiver_id, "question", question, rewritten_question)

            # Success embed
            embed = self._success_embed(
                title="✅ Question Sent!",
                description="Your question has been delivered anonymously!",
                footer="💡 Tip: Keep asking questions to find the perfect gift!"
            )
            embed.add_field(name="📝 Original", value=f"*{self._truncate_text(question)}*", inline=False)
            if use_ai_rewrite and rewritten_question != question:
                embed.add_field(name="🤖 Rewritten", value=f"*{self._truncate_text(rewritten_question)}*", inline=False)
            await inter.edit_original_response(embed=embed)
        else:
            embed = self._error_embed(
                title="❌ Delivery Failed",
                description="Couldn't send your question. Your giftee may have DMs disabled."
            )
            await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="reply_santa", description="Reply to your Secret Santa (sent anonymously)")
    async def ss_reply(
        self,
        inter: disnake.ApplicationCommandInteraction,
        reply: str = commands.Param(description="Your reply (sent anonymously)", max_length=2000)
    ):
        """Reply to Santa anonymously"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        # Find who is the user's Santa
        santa_id = self._find_santa_for_giftee(event, user_id)
        if not santa_id:
            embed = self._error_embed(
                title="❌ No Secret Santa Found",
                description="No one has asked you a question yet, or you haven't been assigned a Secret Santa!",
                footer="💡 Wait for your Secret Santa to ask you something first!"
            )
            await inter.edit_original_response(embed=embed)
            return

        # Send reply (no AI rewriting needed - anonymity already protected)
        reply_msg = self._format_dm_reply(reply, self.state['current_year'])
        success = await self._send_dm(santa_id, reply_msg)

        if success:
            # Save communication
            await self._save_communication(event, str(santa_id), user_id, "reply", reply, reply)

            # Success embed
            embed = self._success_embed(
                title="✅ Reply Sent!",
                description="Your reply has been delivered to your Secret Santa!",
                footer="🎄 Your Secret Santa will be so happy to hear from you!"
            )
            embed.add_field(name="📝 Original", value=f"*{self._truncate_text(reply)}*", inline=False)
            await inter.edit_original_response(embed=embed)
        else:
            embed = self._error_embed(
                title="❌ Delivery Failed",
                description="Couldn't send your reply. Your Secret Santa may have DMs disabled."
            )
            await inter.edit_original_response(embed=embed)


    @ss_root.sub_command(name="submit_gift", description="Submit your gift for records")
    async def ss_submit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        gift_description: str = commands.Param(description="Describe what you gave", max_length=2000)
    ):
        """Submit gift description"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # COMBINED VALIDATION: Participant + assignment check in one pass
        result = await self._validate_participant_with_assignment(inter)
        if not result:
            return
        event, user_id, receiver_id, participants, _ = result
        receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")

        # Check if this is updating an existing submission
        gift_submissions = event.get("gift_submissions", {})
        existing_submission = gift_submissions.get(user_id)
        is_update = existing_submission is not None

        # Save gift (overwrites if already exists - simple approach!)
        async with self._lock:
            event.setdefault("gift_submissions", {})[user_id] = {
                "gift": gift_description,
                "receiver_id": receiver_id,
                "receiver_name": receiver_name,
                "submitted_at": time.time(),
                "timestamp": dt.datetime.now().isoformat()
            }
            self._save()

        # Create beautiful success embed with variations
        year = self.state['current_year']
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
        title, description = secrets.choice(gift_templates)
        
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
            value=f"**{self.state['current_year']}**",
            inline=True
        )
        embed.add_field(
            name="⏰ Submitted",
            value=f"<t:{int(time.time())}:R>",
            inline=True
        )
        embed.add_field(
            name="🎁 Gift Description",
            value=f"*{gift_description}*",
            inline=False
        )
        embed.set_footer(text="🎄 Thank you for participating in Secret Santa! Your kindness makes the season brighter.")
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/852616843715395605.png")  # Gift emoji

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="edit_gift", description="Edit your gift submission from a past year")
    async def ss_edit_gift(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year of the Secret Santa event", autocomplete="autocomplete_year_edit_gift"),
        gift_description: str = commands.Param(description="Updated gift description", max_length=2000)
    ):
        """Edit your own gift submission from an archived year"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        user_id = str(inter.author.id)
        
        # Load archive file for the year
        archive_path = ARCHIVE_DIR / f"{year}.json"
        if not archive_path.exists():
            await inter.edit_original_response(
                content=f"❌ No archive found for year {year}. Make sure the year is correct!"
            )
            return
        
        try:
            # Load archive
            archive_data = load_json(archive_path)
            if not archive_data:
                await inter.edit_original_response(content=f"❌ Failed to load archive for {year}")
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
                await inter.edit_original_response(content=f"❌ No assignments found in archive for {year}")
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
                await inter.edit_original_response(
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
            
            # Save updated archive
            async with self._lock:
                save_json(archive_path, archive_data, self.logger)
            
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
                    value=f"*{self._truncate_text(old_gift, 200)}*",
                    inline=False
                )
            embed.add_field(
                name="🎁 New Gift",
                value=f"*{gift_description}*",
                inline=False
            )
            
            await inter.edit_original_response(embed=embed)
            
            self.logger.info(f"User {inter.author.display_name} ({user_id}) updated their gift for {year}")
            
        except Exception as e:
            self.logger.error(f"Error editing gift for {year}: {e}", exc_info=True)
            await inter.edit_original_response(
                content=f"❌ An error occurred while updating your gift: {e}"
            )

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

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        # Get or create user's wishlist
        async with self._lock:
            wishlists = event.setdefault("wishlists", {})
            user_wishlist = wishlists.setdefault(user_id, [])
            
            # Check if already have this item
            if item.lower() in [w.lower() for w in user_wishlist]:
                await inter.edit_original_response(content="❌ This item is already on your wishlist!")
                return
            
            # Limit wishlist size
            if len(user_wishlist) >= 10:
                await inter.edit_original_response(content="❌ Wishlist full! (max 10 items). Remove some items first.")
                return
            
            # Add item
            user_wishlist.append(item)
            self._save()

        year = self.state['current_year']
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
        title, description, field_name = secrets.choice(wishlist_templates)
        
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
        await inter.edit_original_response(embed=embed)

    @ss_wishlist.sub_command(name="remove", description="Remove item from your wishlist")
    async def wishlist_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        item_number: int = commands.Param(description="Item number to remove (1-10)", ge=1, le=10, autocomplete="autocomplete_wishlist_item_number")
    ):
        """Remove item from wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        wishlists = event.get("wishlists", {})
        user_wishlist = wishlists.get(user_id, [])

        if not user_wishlist:
            await inter.edit_original_response(content="❌ Your wishlist is empty!")
            return

        if item_number > len(user_wishlist):
            await inter.edit_original_response(content=f"❌ Invalid item number! You only have {len(user_wishlist)} items.")
            return

        # Remove item
        removed_item = user_wishlist.pop(item_number - 1)

        async with self._lock:
            self._save()

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
        await inter.edit_original_response(embed=embed)

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

        wishlists = event.get("wishlists", {})
        user_wishlist = wishlists.get(user_id, [])

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
        
        await inter.edit_original_response(embed=embed)

    @ss_wishlist.sub_command(name="clear", description="Clear your entire wishlist")
    async def wishlist_clear(self, inter: disnake.ApplicationCommandInteraction):
        """Clear wishlist"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Validate participant
        result = await self._validate_participant(inter)
        if not result:
            return
        event, user_id = result

        wishlists = event.get("wishlists", {})
        
        if user_id not in wishlists or not wishlists[user_id]:
            await inter.edit_original_response(content="❌ Your wishlist is already empty!")
            return

        # Clear wishlist
        async with self._lock:
            wishlists[user_id] = []
            self._save()

        await inter.edit_original_response(content="✅ Wishlist cleared!")

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
        receiver_name = event["participants"].get(receiver_id, f"User {receiver_id}")

        wishlists = event.get("wishlists", {})
        giftee_wishlist = wishlists.get(receiver_id, [])

        if not giftee_wishlist:
            embed = disnake.Embed(
                title=f"📋 {receiver_name}'s Wishlist",
                description=f"{receiver_name} hasn't added anything to their wishlist yet.\n\nYou can ask them questions with `/ss ask_giftee` to learn what they'd like!",
                color=disnake.Color.blue()
            )
            embed.set_footer(text="💡 Check back later - they might add items soon!")
        else:
            embed = disnake.Embed(
                title=f"📋 {receiver_name}'s Wishlist",
                description=f"Your giftee has **{len(giftee_wishlist)}** item{'s' if len(giftee_wishlist) != 1 else ''} on their list",
                color=disnake.Color.gold()
            )
            wishlist_text = "\n".join(f"{i+1}. {item}" for i, item in enumerate(giftee_wishlist))
            embed.add_field(name="🎁 Their Wishes", value=wishlist_text, inline=False)
            embed.set_footer(text="💡 Use these as inspiration for the perfect gift!")
        
        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_gifts", description="View submitted gifts")
    @mod_check()
    async def ss_view_gifts(self, inter: disnake.ApplicationCommandInteraction):
        """Show gift submissions"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        event = await self._require_event(inter)
        if not event:
            return

        submissions = event.get("gift_submissions", {})
        if not submissions:
            await inter.edit_original_response(content="❌ No gifts submitted yet")
            return

        embed = disnake.Embed(
            title=f"🎁 Gift Submissions ({len(submissions)})",
            color=disnake.Color.green()
        )

        # Create consistent emoji mapping for all participants this year
        emoji_mapping = self._get_year_emoji_mapping(event["participants"])
        
        for giver_id, submission in list(submissions.items())[:10]:
            giver_name = event["participants"].get(giver_id, f"User {giver_id}")
            receiver_name = submission.get("receiver_name", "Unknown")
            gift = submission["gift"][:200] + "..." if len(submission["gift"]) > 200 else submission["gift"]

            # Get consistent emojis for each person this year
            giver_emoji = emoji_mapping.get(giver_id, "🎁")
            
            # Try to get receiver emoji from their ID if available
            receiver_id = submission.get("receiver_id")
            if receiver_id:
                receiver_emoji = emoji_mapping.get(str(receiver_id), "🎄")
            else:
                receiver_emoji = "🎄"

            embed.add_field(
                name=f"{giver_emoji} {giver_name} → {receiver_emoji} {receiver_name}",
                value=gift,
                inline=False
            )

        if len(submissions) > 10:
            embed.set_footer(text=f"Showing 10 of {len(submissions)} submissions")

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_comms", description="View communications")
    @mod_check()
    async def ss_view_comms(self, inter: disnake.ApplicationCommandInteraction):
        """Show communication threads"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        event = await self._require_event(inter)
        if not event:
            return

        comms = event.get("communications", {})
        if not comms:
            await inter.edit_original_response(content="❌ No communications yet")
            return

        embed = disnake.Embed(
            title=f"💬 Communications ({len(comms)})",
            color=disnake.Color.blue()
        )

        # Create consistent emoji mapping for all participants this year
        emoji_mapping = self._get_year_emoji_mapping(event["participants"])
        
        for santa_id, data in list(comms.items())[:5]:
            santa_name = event["participants"].get(santa_id, f"User {santa_id}")
            giftee_id = data.get("giftee_id")
            giftee_name = event["participants"].get(str(giftee_id), "Unknown")

            # Get consistent emojis for each person this year
            santa_emoji = emoji_mapping.get(santa_id, "🎅")
            giftee_emoji = emoji_mapping.get(str(giftee_id), "🎄")

            thread = data.get("thread", [])
            thread_text = "\n".join([
                f"{santa_emoji if msg['type'] == 'question' else giftee_emoji} {msg['message'][:50]}..."
                for msg in thread[:3]
            ])

            embed.add_field(
                name=f"💬 {santa_name} → {giftee_name} ({len(thread)} messages)",
                value=thread_text or "No messages",
                inline=False
            )

        if len(comms) > 5:
            embed.set_footer(text=f"Showing 5 of {len(comms)} threads")

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="history", description="View past Secret Santa events")
    async def ss_history(
            self,
            inter: disnake.ApplicationCommandInteraction,
            year: int = commands.Param(default=None, description="Specific year to view", autocomplete="autocomplete_year_history")
    ):
        """Show event history"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed

        # Load all archives (NOTE: Current active event is NOT shown - would reveal secrets!)
        archives = load_all_archives(logger=self.logger)

        if not archives:
            await inter.edit_original_response(content="❌ No archived events found")
            return

        # Sort by year
        sorted_years = sorted(archives.keys(), reverse=True)

        if year:
            # Show specific year with pagination
            if year not in archives:
                available = ", ".join(str(y) for y in sorted_years)
                await inter.edit_original_response(
                    content=f"❌ No event found for {year}\n**Available years:** {available}"
                )
                return

            archive = archives[year]
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
                await inter.edit_original_response(embed=embed, view=paginator)
            else:
                # Few assignments - show all on one page (no buttons needed)
                gifts = event_data.get("gift_submissions", {})
                has_assignments = bool(assignments)
                has_gifts = bool(gifts)
                
                if has_gifts:
                    description = f"**{len(participants)}** participants, **{len(gifts)}** gifts exchanged"
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
                            gift_desc = submission.get("gift", "No description provided")
                            if isinstance(gift_desc, str) and len(gift_desc) > 60:
                                gift_desc = gift_desc[:57] + "..."
                            elif not isinstance(gift_desc, str):
                                gift_desc = "Invalid gift description"
                            
                            exchange_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention}")
                            exchange_lines.append(f"    ⤷ *{gift_desc}*")
                        else:
                            exchange_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention} *(no gift recorded)*")
                    
                    gifts_count = len([g for g in gifts.keys() if g in [str(a) for a in assignments.keys()]])
                    embed.add_field(
                        name=f"🎄 Assignments & Gifts ({gifts_count}/{len(assignments)} gifts submitted)",
                        value="\n".join(exchange_lines),
                        inline=False
                    )
                else:
                    status_text = f"⏸️ Signup completed ({len(participants)} joined)\n❌ No assignments made\n❌ No gifts recorded"
                    embed.add_field(name="📝 Event Status", value=status_text, inline=False)

                # Statistics
                completion_rate = (len(gifts) / len(participants) * 100) if participants else 0
                embed.add_field(
                    name="📊 Statistics",
                    value=f"**Completion:** {completion_rate:.0f}%\n**Total Gifts:** {len(gifts)}",
                    inline=True
                )

                embed.set_footer(text=f"Requested by {inter.author.display_name}")
                await inter.edit_original_response(embed=embed)

        else:
            # Show all years overview with better layout
            embed = disnake.Embed(
                title="🎄 Secret Santa Archive",
                description="Complete history of all Secret Santa events",
                color=disnake.Color.blue(),
                timestamp=dt.datetime.now()
            )

            # Create year timeline
            timeline_text = []
            for year_val in sorted_years:
                archive = archives[year_val]
                event_data = archive.get("event", {})
                participants = event_data.get("participants", {})
                gifts = event_data.get("gift_submissions", {})

                completion_rate = (len(gifts) / len(participants) * 100) if participants else 0

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
                    f"**{year_val}** {status} — {len(participants)} participants, {len(gifts)} gifts ({completion_rate:.0f}%)"
                )

            # Split timeline into chunks if needed
            if len(timeline_text) <= 10:
                embed.add_field(
                    name="📅 Event Timeline",
                    value="\n".join(timeline_text),
                    inline=False
                )
            else:
                embed.add_field(
                    name="📅 Recent Events",
                    value="\n".join(timeline_text[:5]),
                    inline=False
                )
                embed.add_field(
                    name="📅 Earlier Events",
                    value="\n".join(timeline_text[5:10]),
                    inline=False
                )
                if len(timeline_text) > 10:
                    embed.add_field(
                        name="​",
                        value=f"*... and {len(timeline_text) - 10} more years*",
                        inline=False
                    )

            # Calculate all-time statistics
            # Cache event lookups to avoid repeated .get() chains
            total_participants = total_gifts = 0
            for y in sorted_years:
                event_data = archives[y].get("event", {})
                total_participants += len(event_data.get("participants", {}))
                total_gifts += len(event_data.get("gift_submissions", {}))
            avg_participants = total_participants / len(sorted_years) if sorted_years else 0
            avg_completion = (total_gifts / total_participants * 100) if total_participants else 0

            # Add statistics with better formatting
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

            # Add legend
            embed.add_field(
                name="📖 Status Legend",
                value="✅ 90%+ complete | 🟨 70-89% | 🟧 Under 70% | ⏳ No gifts recorded",
                inline=False
            )

            embed.set_footer(
                text=f"Use /ss history [year] for detailed view • Requested by {inter.author.display_name}")
            await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="user_history", description="View a specific user's Secret Santa history across all years")
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
            await inter.edit_original_response(content="❌ No archived events found")
            return
        
        # Find user's participation across all years
        participations = []
        
        for year in sorted(archives.keys()):
            event_data = archives[year].get("event", {})
            participants = event_data.get("participants", {})
            assignments = event_data.get("assignments", {})
            gifts = event_data.get("gift_submissions", {})
            
            # Check if user participated this year
            if user_id not in participants:
                continue
            
            user_name = participants[user_id]
            
            # Find who they gave to
            gave_to_id = assignments.get(user_id)
            gave_to_name = participants.get(str(gave_to_id), "Unknown") if gave_to_id else None
            
            # Find what gift they gave
            gift_data = gifts.get(user_id)
            gift_desc = None
            if gift_data and isinstance(gift_data, dict):
                gift_desc = gift_data.get("gift", "No description")
            
            # Find who gave to them
            received_from_id = None
            received_from_name = None
            received_gift = None
            
            for giver_id, receiver_id in assignments.items():
                if str(receiver_id) == user_id:
                    received_from_id = giver_id
                    received_from_name = participants.get(giver_id, "Unknown")
                    
                    # Find gift they received
                    giver_gift = gifts.get(giver_id)
                    if giver_gift and isinstance(giver_gift, dict):
                        received_gift = giver_gift.get("gift", "No description")
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
            await inter.edit_original_response(embed=embed)
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
                if participation["gift_given"]:
                    gift_short = participation["gift_given"][:80] + "..." if len(participation["gift_given"]) > 80 else participation["gift_given"]
                    year_lines.append(f"   └─ *{gift_short}*")
                else:
                    year_lines.append(f"   └─ *(no gift recorded)*")
            else:
                year_lines.append(f"🎁 **Gave to:** *(assignment not found)*")
            
            # What they received
            if participation["received_from_name"]:
                received_from_mention = f"<@{participation['received_from_id']}>" if participation['received_from_id'] else participation['received_from_name']
                year_lines.append(f"🎅 **Received from:** {received_from_mention}")
                if participation["gift_received"]:
                    gift_short = participation["gift_received"][:80] + "..." if len(participation["gift_received"]) > 80 else participation["gift_received"]
                    year_lines.append(f"   └─ *{gift_short}*")
                else:
                    year_lines.append(f"   └─ *(no gift recorded)*")
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
        embed.set_footer(text=f"Requested by {inter.author.display_name}")
        
        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="test_emoji_consistency", description="🎨 Test emoji consistency across years for a user")
    async def ss_test_emoji_consistency(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="User to check emoji consistency for")
    ):
        """Test that a user gets the same emoji across all years"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        user_id = str(user.id)
        
        # Load all archives
        archives = load_all_archives(logger=self.logger)
        
        if not archives:
            await inter.edit_original_response(content="❌ No archived events found")
            return
        
        # Check emoji for this user across all years
        emoji_results = []
        
        for year in sorted(archives.keys()):
            event_data = archives[year].get("event", {})
            participants = event_data.get("participants", {})
            
            # Check if user participated this year
            if user_id in participants:
                # Generate emoji mapping for this year
                emoji_mapping = self._get_year_emoji_mapping(participants)
                user_emoji = emoji_mapping.get(user_id, "❓")
                user_name = participants[user_id]
                
                emoji_results.append(f"**{year}**: {user_emoji} {user_name}")
        
        if not emoji_results:
            await inter.edit_original_response(
                content=f"❌ {user.mention} has never participated in Secret Santa"
            )
            return
        
        # Build response
        embed = disnake.Embed(
            title=f"🎨 Emoji Consistency Test",
            description=f"Testing emoji assignment for {user.mention} across all years",
            color=disnake.Color.blue()
        )
        
        embed.add_field(
            name="📅 Participation History",
            value="\n".join(emoji_results),
            inline=False
        )
        
        # Check if all emojis are the same (they should be!)
        emojis = [line.split()[1] for line in emoji_results]
        all_same = len(set(emojis)) == 1
        
        if all_same:
            embed.add_field(
                name="✅ Consistency Check",
                value=f"**PASS**: {user.display_name} has the same emoji ({emojis[0]}) across all {len(emoji_results)} years!",
                inline=False
            )
            embed.color = disnake.Color.green()
        else:
            embed.add_field(
                name="⚠️ Consistency Check",
                value=f"**INCONSISTENT**: Found different emojis: {', '.join(set(emojis))}",
                inline=False
            )
            embed.color = disnake.Color.red()
        
        embed.set_footer(text="Each user should have the same emoji across all years based on their user ID")
        
        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="delete_year", description="🗑️ Delete an archive year (CAREFUL!)")
    @commands.has_permissions(administrator=True)
    async def ss_delete_year(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year to delete", autocomplete="autocomplete_year_delete")
    ):
        """Delete archive file for a specific year (admin only)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        # Safety check - don't allow deleting very old years accidentally
        current_year = dt.date.today().year
        if year < 2020 or year > current_year + 1:
            await inter.edit_original_response(content=f"❌ Invalid year {year} (must be 2020-{current_year + 1})")
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
            await inter.edit_original_response(embed=embed)
            return
        
        archive_path = ARCHIVE_DIR / f"{year}.json"
        
        if not archive_path.exists():
            await inter.edit_original_response(content=f"❌ No archive found for {year}")
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
            await inter.edit_original_response(embed=embed)
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
                      f"• Restore anytime with `/ss restore_year {year}`\n\n"
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
            embed.set_footer(text="💡 Use /ss list_backups to view all backed-up years")
            
            await inter.edit_original_response(embed=embed)
            
            # Log to Discord
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"🛡️ {inter.author.display_name} moved Secret Santa {year} to backups (safely archived)",
                    "INFO"
                )
            
        except Exception as e:
            self.logger.error(f"Failed to move archive to backups: {e}")
            await inter.edit_original_response(content=f"❌ Failed to move archive: {e}")

    @ss_root.sub_command(name="restore_year", description="♻️ Restore a year from backups")
    @commands.has_permissions(administrator=True)
    async def ss_restore_year(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year: int = commands.Param(description="Year to restore", autocomplete="autocomplete_year_restore")
    ):
        """Restore archive file from backups folder (admin only)"""
        if not await self._safe_defer(inter, ephemeral=True):
            return  # Interaction expired, can't proceed
        
        backup_path = BACKUPS_DIR / f"{year}.json"
        archive_path = ARCHIVE_DIR / f"{year}.json"
        
        # Check if backup exists
        if not backup_path.exists():
            # List available backups to help user
            available_backups = sorted([int(f.stem) for f in BACKUPS_DIR.glob("[0-9][0-9][0-9][0-9].json")])
            
            if available_backups:
                backups_str = ", ".join(str(y) for y in available_backups)
                await inter.edit_original_response(
                    content=f"❌ No backup found for {year}\n\n**Available backups:** {backups_str}"
                )
            else:
                await inter.edit_original_response(
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
                      f"1. Delete the current archive with `/ss delete_year {year}`\n"
                      f"2. Manually move/rename the current archive\n"
                      f"3. Keep the current archive (backup remains safe)",
                inline=False
            )
            embed.set_footer(text="Protection: Prevents accidental overwrites!")
            await inter.edit_original_response(embed=embed)
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
            
            await inter.edit_original_response(embed=embed)
            
            # Log to Discord
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"♻️ {inter.author.display_name} restored Secret Santa {year} from backups",
                    "INFO"
                )
            
        except Exception as e:
            self.logger.error(f"Failed to restore archive from backups: {e}")
            await inter.edit_original_response(content=f"❌ Failed to restore archive: {e}")

    @ss_root.sub_command(name="list_backups", description="📋 View all backed-up years")
    @commands.has_permissions(administrator=True)
    async def ss_list_backups(self, inter: disnake.ApplicationCommandInteraction):
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
            embed.set_footer(text="Use /ss delete_year to move archives to backups")
            await inter.edit_original_response(embed=embed)
            return
        
        # Build list of backed-up years with file sizes
        backup_list = []
        for backup_file in backup_files:
            year = backup_file.stem
            size_kb = backup_file.stat().st_size / 1024
            backup_list.append(f"**{year}** - {size_kb:.1f} KB")
        
        embed = disnake.Embed(
            title="📋 Backed-Up Years",
            description=f"Found **{len(backup_files)}** year(s) in backups folder:",
            color=disnake.Color.blue()
        )
        
        # Split into chunks if too many
        chunk_size = 15
        for i in range(0, len(backup_list), chunk_size):
            chunk = backup_list[i:i+chunk_size]
            field_name = "Years" if i == 0 else f"Years (continued)"
            embed.add_field(
                name=field_name,
                value="\n".join(chunk),
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
        await inter.edit_original_response(embed=embed)

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
        participants = event.get("participants", {})
        
        # Already joined
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

        # Add participant (ensure participants dict exists in event, then modify it)
        async with self._lock:
            if "participants" not in event:
                event["participants"] = {}
            event["participants"][user_id] = name
            self._save()

        # Send confirmation (same message as /ss start)
        join_msg = self._get_join_message(self.state['current_year'])
        await self._send_dm(payload.user_id, join_msg)

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
        participants = event.get("participants", {})
        
        # Not a participant
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

            # Remove if no reactions (modify event's participants dict)
            if not has_reaction:
                async with self._lock:
                    if "participants" in event:
                        event["participants"].pop(user_id, None)
                    self._save()

                leave_msg = self._get_leave_message(self.state['current_year'])
                await self._send_dm(payload.user_id, leave_msg)

        except Exception as e:
            self.logger.error(f"Error handling reaction remove: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        # Register persistent reply button view - works even after bot restarts
        # The view dynamically looks up santa/giftee relationships from event data
        self.bot.add_view(SecretSantaReplyView())  # Button uses dynamic lookup


def setup(bot):
    """Setup the cog"""
    bot.add_cog(SecretSantaCog(bot))