"""
Secret Santa Cog - Complete Rewrite
Manages Secret Santa events with gift tracking and anonymous communication
"""

import asyncio
import datetime as dt
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import disnake
from disnake.ext import commands

# Paths
# The cog file is at: PROJECT_ROOT/cogs/SecretSanta_cog.py
# We want archive at: PROJECT_ROOT/cogs/archive/
ROOT = Path(__file__).parent  # This is the 'cogs' directory
STATE_FILE = ROOT / "secret_santa_state.json"
ARCHIVE_DIR = ROOT / "archive"

# Ensure archive directory exists
ARCHIVE_DIR.mkdir(exist_ok=True)

# Log the paths for debugging
import logging
_init_logger = logging.getLogger("bot.santa.init")
_init_logger.info(f"Secret Santa cog file: {__file__}")
_init_logger.info(f"ROOT (cogs dir): {ROOT}")
_init_logger.info(f"Archive directory: {ARCHIVE_DIR}")
_init_logger.info(f"Archive exists: {ARCHIVE_DIR.exists()}")
if ARCHIVE_DIR.exists():
    files = list(ARCHIVE_DIR.glob("*.json"))
    _init_logger.info(f"Archive files found: {[f.name for f in files]}")


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON with error handling"""
    if path.exists():
        try:
            text = path.read_text().strip()
            return json.loads(text) if text else (default or {})
        except (json.JSONDecodeError, OSError):
            pass
    return default or {}


def save_json(path: Path, data: Any):
    """Save JSON atomically"""
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    temp.replace(path)


def make_assignments(participants: List[int], history: Dict[str, List[int]]) -> Dict[int, int]:
    """Create Secret Santa assignments avoiding repeats"""
    if len(participants) < 2:
        raise ValueError("Need at least 2 participants")

    random.seed(time.time() + random.random())

    # Try 100 times to find valid assignment
    for attempt in range(100):
        givers = participants.copy()
        receivers = participants.copy()
        random.shuffle(givers)
        random.shuffle(receivers)

        assignments = {}
        valid = True

        for giver in givers:
            # Filter out invalid receivers
            valid_receivers = [
                r for r in receivers
                if r != giver and r not in history.get(str(giver), [])
            ]

            if not valid_receivers:
                valid = False
                break

            receiver = random.choice(valid_receivers)
            assignments[giver] = receiver
            receivers.remove(receiver)

        if valid:
            # Update history
            for giver, receiver in assignments.items():
                history.setdefault(str(giver), []).append(receiver)
            return assignments

    # Fallback: simple rotation
    random.shuffle(givers)
    random.shuffle(receivers)
    return {g: receivers[(i + 1) % len(receivers)] for i, g in enumerate(givers)}


def mod_check():
    """Check if user is mod or admin"""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        if inter.author.guild_permissions.administrator:
            return True

        # Check config for mod role
        if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'DISCORD_MODERATOR_ROLE_ID'):
            mod_role_id = inter.bot.config.DISCORD_MODERATOR_ROLE_ID
            if mod_role_id and any(r.id == mod_role_id for r in inter.author.roles):
                return True

        return False

    return commands.check(predicate)


def participant_check():
    """Check if user is a participant"""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        cog = inter.bot.get_cog("SecretSantaCog")
        if not cog:
            return False

        event = cog.state.get("current_event")
        if not event or not event.get("active"):
            return False

        return str(inter.author.id) in event.get("participants", {})

    return commands.check(predicate)


class SecretSantaCog(commands.Cog):
    """Secret Santa event management"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("santa")

        # Load state
        self.state = load_json(STATE_FILE, {
            "current_year": dt.date.today().year,
            "pair_history": {},
            "current_event": None
        })

        self._lock = asyncio.Lock()
        self._backup_task: Optional[asyncio.Task] = None

        self.logger.info("Secret Santa cog initialized")

    async def cog_load(self):
        """Initialize cog"""
        self._backup_task = asyncio.create_task(self._backup_loop())
        self.logger.info("Secret Santa cog loaded")

    async def cog_unload(self):
        """Cleanup cog"""
        self.logger.info("Unloading Secret Santa cog...")

        if self._backup_task:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass

        # Final save
        self._save()

        self.logger.info("Secret Santa cog unloaded")

    def _save(self):
        """Save state to disk"""
        save_json(STATE_FILE, self.state)

    async def _backup_loop(self):
        """Periodic backup"""
        try:
            while True:
                await asyncio.sleep(3600)  # Every hour
                async with self._lock:
                    self._save()
        except asyncio.CancelledError:
            pass

    async def _send_dm(self, user_id: int, message: str) -> bool:
        """Send DM to user"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to DM {user_id}: {e}")
            return False

    def _archive_event(self, event: Dict[str, Any], year: int):
        """Archive event data"""
        # Convert event structure to match historical format
        archived_assignments = []

        # Process gift submissions
        for giver_id, submission in event.get("gift_submissions", {}).items():
            archived_assignments.append({
                "giver_id": giver_id,
                "giver_name": event["participants"].get(giver_id, f"User {giver_id}"),
                "receiver_id": str(submission.get("receiver_id", "")),
                "receiver_name": submission.get("receiver_name", "Unknown"),
                "gift": submission.get("gift", "No description")
            })

        # Archive in new format (event_YEAR.json)
        archive_new = {
            "year": year,
            "event": event.copy(),
            "archived_at": time.time(),
            "timestamp": dt.datetime.now().isoformat()
        }
        save_json(ARCHIVE_DIR / f"event_{year}.json", archive_new)

        # Also save in old format (YEAR.json) for compatibility
        archive_old = {
            "year": year,
            "assignments": archived_assignments
        }
        save_json(ARCHIVE_DIR / f"{year}.json", archive_old)

        self.logger.info(f"Archived Secret Santa {year} in both formats")

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.ApplicationCommandInteraction):
        """Secret Santa commands"""
        pass

    @ss_root.sub_command(name="start", description="Start a Secret Santa event")
    @mod_check()
    async def ss_start(
        self,
        inter: disnake.ApplicationCommandInteraction,
        announcement_message_id: str = commands.Param(description="Message ID for reactions"),
        role_id: str = commands.Param(description="Role ID to assign participants")
    ):
        """Start new Secret Santa event"""
        await inter.response.defer(ephemeral=True)

        # Validate IDs
        try:
            msg_id = int(announcement_message_id)
            role_id_int = int(role_id)
        except ValueError:
            await inter.edit_original_response(content="❌ Invalid message or role ID")
            return

        # Check if event already active
        event = self.state.get("current_event")
        if event and event.get("active"):
            await inter.edit_original_response(content="❌ Event already active")
            return

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

        # Create event
        current_year = dt.date.today().year

        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": msg_id,
            "role_id": role_id_int,
            "participants": participants,
            "assignments": {},
            "guild_id": inter.guild.id,
            "gift_submissions": {},
            "communications": {}
        }

        async with self._lock:
            self.state["current_year"] = current_year
            self.state["current_event"] = new_event
            self._save()

        # Send confirmation DMs
        dm_tasks = [
            self._send_dm(
                int(uid),
                f"✅ You've joined Secret Santa {current_year}! 🎄\n\n"
                f"React to the announcement to join/leave."
            )
            for uid in participants
        ]

        results = await asyncio.gather(*dm_tasks, return_exceptions=True)
        successful = sum(1 for r in results if r is True)

        await inter.edit_original_response(
            f"✅ Secret Santa {current_year} started!\n"
            f"• Participants: {len(participants)}\n"
            f"• DMs sent: {successful}/{len(participants)}\n"
            f"• Role ID: {role_id_int}"
        )

    @ss_root.sub_command(name="shuffle", description="Assign Secret Santas")
    @mod_check()
    async def ss_shuffle(self, inter: disnake.ApplicationCommandInteraction):
        """Make assignments"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        participants = list(map(int, event["participants"].keys()))

        if len(participants) < 2:
            await inter.edit_original_response(content="❌ Need at least 2 participants")
            return

        # Load all history (current + archived)
        history = self.state.get("pair_history", {}).copy()

        # Load archived history from YYYY.json files
        for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
            try:
                year_str = archive_file.stem
                if not year_str.isdigit() or len(year_str) != 4:
                    continue

                archive_data = load_json(archive_file)

                # Handle old format (direct assignments list)
                assignments_data = archive_data.get("assignments", [])

                if isinstance(assignments_data, list):
                    # Process old format where assignments is a list
                    for assignment in assignments_data:
                        giver_id = assignment.get("giver_id")
                        receiver_id = assignment.get("receiver_id")

                        if giver_id and receiver_id:
                            # Ensure receiver_id is int
                            try:
                                receiver_int = int(receiver_id)
                                history.setdefault(str(giver_id), []).append(receiver_int)
                            except (ValueError, TypeError):
                                continue

                # Also check for new format (event_YYYY.json)
                elif archive_data.get("event"):
                    event_data = archive_data["event"]
                    event_assignments = event_data.get("assignments", {})

                    if isinstance(event_assignments, dict):
                        for giver, receiver in event_assignments.items():
                            try:
                                receiver_int = int(receiver)
                                history.setdefault(str(giver), []).append(receiver_int)
                            except (ValueError, TypeError):
                                continue

            except Exception as e:
                self.logger.warning(f"Error loading archive {archive_file}: {e}")
                continue

        # Make assignments
        try:
            assignments = make_assignments(participants, history)
        except ValueError as e:
            await inter.edit_original_response(content=f"❌ {e}")
            return

        # Assign role to participants
        role = inter.guild.get_role(event["role_id"])
        if role and inter.guild.me.guild_permissions.manage_roles:
            for user_id in participants:
                try:
                    member = inter.guild.get_member(user_id)
                    if member and role not in member.roles:
                        await member.add_roles(role, reason="Secret Santa participant")
                except Exception:
                    pass

        # Send assignment DMs
        messages = [
            "🎅 Ho ho ho! You're Secret Santa for {receiver}!",
            "🎄 You've been assigned to gift {receiver}!",
            "✨ The magic of Christmas pairs you with {receiver}!",
            "🦌 Rudolph guides you to {receiver}!"
        ]

        dm_tasks = []
        for giver, receiver in assignments.items():
            msg = random.choice(messages).format(receiver=f"<@{receiver}>")
            msg += "\n\n💬 **Ask questions**: `/ss ask_giftee`"
            msg += "\n📨 **Reply to Santa**: `/ss reply_santa`"
            msg += "\n📝 **Submit gift**: `/ss submit_gift`"
            dm_tasks.append(self._send_dm(giver, msg))

        await asyncio.gather(*dm_tasks)

        # Save assignments
        async with self._lock:
            event["assignments"] = {str(k): v for k, v in assignments.items()}
            event["join_closed"] = True

            # Update history
            for giver, receiver in assignments.items():
                self.state["pair_history"].setdefault(str(giver), []).append(receiver)

            self._save()

        await inter.edit_original_response(
            f"✅ Assignments complete!\n"
            f"• {len(assignments)} pairs created\n"
            f"• DMs sent to all participants"
        )

    @ss_root.sub_command(name="stop", description="Stop the Secret Santa event")
    @mod_check()
    async def ss_stop(self, inter: disnake.ApplicationCommandInteraction):
        """Stop event"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        # Archive event
        self._archive_event(event, self.state["current_year"])

        async with self._lock:
            self.state["current_event"] = None
            self._save()

        await inter.edit_original_response("✅ Event stopped and archived")

    @ss_root.sub_command(name="participants", description="View participants")
    @mod_check()
    async def ss_participants(self, inter: disnake.ApplicationCommandInteraction):
        """Show participants"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
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

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee a question")
    @participant_check()
    async def ss_ask(
        self,
        inter: disnake.ApplicationCommandInteraction,
        question: str = commands.Param(description="Your anonymous question")
    ):
        """Ask giftee anonymously"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Check if user has assignment
        if user_id not in event.get("assignments", {}):
            await inter.edit_original_response(content="❌ You don't have an assignment yet")
            return

        receiver_id = event["assignments"][user_id]

        # Send question
        success = await self._send_dm(
            receiver_id,
            f"🎅 **Anonymous question from your Secret Santa:**\n\n{question}\n\n"
            f"💬 Reply with `/ss reply_santa`"
        )

        if success:
            # Save communication
            async with self._lock:
                comms = event.setdefault("communications", {})
                thread = comms.setdefault(user_id, {"giftee_id": receiver_id, "thread": []})
                thread["thread"].append({
                    "type": "question",
                    "message": question,
                    "timestamp": time.time()
                })
                self._save()

            await inter.edit_original_response(content="✅ Question sent!")
        else:
            await inter.edit_original_response(content="❌ Failed to send. They may have DMs disabled.")

    @ss_root.sub_command(name="reply_santa", description="Reply to your Secret Santa")
    @participant_check()
    async def ss_reply(
        self,
        inter: disnake.ApplicationCommandInteraction,
        reply: str = commands.Param(description="Your anonymous reply")
    ):
        """Reply to Santa anonymously"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Find who is the user's Santa
        santa_id = None
        for giver, receiver in event.get("assignments", {}).items():
            if receiver == int(user_id):
                santa_id = int(giver)
                break

        if not santa_id:
            await inter.edit_original_response(content="❌ No one has asked you a question yet")
            return

        # Send reply
        success = await self._send_dm(
            santa_id,
            f"📨 **Anonymous reply from your giftee:**\n\n{reply}\n\n"
            f"💬 Ask more with `/ss ask_giftee`"
        )

        if success:
            # Save communication
            async with self._lock:
                comms = event.setdefault("communications", {})
                thread = comms.setdefault(str(santa_id), {"giftee_id": user_id, "thread": []})
                thread["thread"].append({
                    "type": "reply",
                    "message": reply,
                    "timestamp": time.time()
                })
                self._save()

            await inter.edit_original_response(content="✅ Reply sent!")
        else:
            await inter.edit_original_response(content="❌ Failed to send. They may have DMs disabled.")

    @ss_root.sub_command(name="submit_gift", description="Submit your gift for records")
    @participant_check()
    async def ss_submit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        gift_description: str = commands.Param(description="Describe what you gave")
    ):
        """Submit gift description"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Check if user has assignment
        if user_id not in event.get("assignments", {}):
            await inter.edit_original_response(content="❌ You don't have an assignment yet")
            return

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")

        # Save gift
        async with self._lock:
            event.setdefault("gift_submissions", {})[user_id] = {
                "gift": gift_description,
                "receiver_id": receiver_id,
                "receiver_name": receiver_name,
                "submitted_at": time.time(),
                "timestamp": dt.datetime.now().isoformat()
            }
            self._save()

        await inter.edit_original_response(
            f"✅ Gift submitted!\n\n"
            f"**For:** {receiver_name}\n"
            f"**Gift:** {gift_description}\n\n"
            f"Recorded in Secret Santa {self.state['current_year']} archives!"
        )

    @ss_root.sub_command(name="view_gifts", description="View submitted gifts")
    @mod_check()
    async def ss_view_gifts(self, inter: disnake.ApplicationCommandInteraction):
        """Show gift submissions"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        submissions = event.get("gift_submissions", {})

        if not submissions:
            await inter.edit_original_response(content="❌ No gifts submitted yet")
            return

        embed = disnake.Embed(
            title=f"🎁 Gift Submissions ({len(submissions)})",
            color=disnake.Color.green()
        )

        for giver_id, submission in list(submissions.items())[:10]:
            giver_name = event["participants"].get(giver_id, f"User {giver_id}")
            receiver_name = submission.get("receiver_name", "Unknown")
            gift = submission["gift"][:200] + "..." if len(submission["gift"]) > 200 else submission["gift"]

            embed.add_field(
                name=f"🎁 {giver_name} → {receiver_name}",
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
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        comms = event.get("communications", {})

        if not comms:
            await inter.edit_original_response(content="❌ No communications yet")
            return

        embed = disnake.Embed(
            title=f"💬 Communications ({len(comms)})",
            color=disnake.Color.blue()
        )

        for santa_id, data in list(comms.items())[:5]:
            santa_name = event["participants"].get(santa_id, f"User {santa_id}")
            giftee_id = data.get("giftee_id")
            giftee_name = event["participants"].get(str(giftee_id), "Unknown")

            thread = data.get("thread", [])
            thread_text = "\n".join([
                f"{'🎅' if msg['type'] == 'question' else '📨'} {msg['message'][:50]}..."
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
            year: int = commands.Param(default=None, description="Specific year to view")
    ):
        """Show event history"""
        await inter.response.defer(ephemeral=True)

        # Load archived events - both YYYY.json and event_YYYY.json formats
        archives = {}

        # Load old format (YYYY.json)
        for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
            year_str = archive_file.stem

            # Skip non-4-digit year files
            if not year_str.isdigit() or len(year_str) != 4:
                continue

            try:
                year_int = int(year_str)
                data = load_json(archive_file)

                if data:
                    # Convert old format to consistent structure
                    if "assignments" in data and isinstance(data["assignments"], list):
                        # Old format - create a pseudo-event structure
                        participants = {}
                        gifts = {}
                        assignments_map = {}

                        for assignment in data["assignments"]:
                            giver_id = assignment.get("giver_id", "")
                            giver_name = assignment.get("giver_name", "Unknown")
                            receiver_id = assignment.get("receiver_id", "")
                            receiver_name = assignment.get("receiver_name", "Unknown")
                            gift = assignment.get("gift", "No description")

                            participants[giver_id] = giver_name
                            if receiver_id:
                                participants[receiver_id] = receiver_name

                            gifts[giver_id] = {
                                "gift": gift,
                                "receiver_name": receiver_name,
                                "receiver_id": receiver_id
                            }

                            if giver_id and receiver_id:
                                assignments_map[giver_id] = receiver_id

                        archives[year_int] = {
                            "year": year_int,
                            "event": {
                                "participants": participants,
                                "gift_submissions": gifts,
                                "assignments": assignments_map
                            }
                        }
                    elif "event" in data:
                        # New format
                        archives[year_int] = data

            except Exception as e:
                self.logger.warning(f"Error loading archive {archive_file}: {e}")
                continue

        # Load new format (event_YYYY.json) - these take precedence
        for archive_file in ARCHIVE_DIR.glob("event_*.json"):
            try:
                year_str = archive_file.stem.replace("event_", "")
                if year_str.isdigit():
                    year_int = int(year_str)
                    data = load_json(archive_file)
                    if data:
                        archives[year_int] = data
            except Exception:
                continue

        if not archives:
            await inter.edit_original_response(content="❌ No archived events found")
            return

        # Sort by year
        sorted_years = sorted(archives.keys(), reverse=True)

        if year:
            # Show specific year with improved layout
            if year not in archives:
                available = ", ".join(str(y) for y in sorted_years)
                await inter.edit_original_response(
                    content=f"❌ No event found for {year}\n**Available years:** {available}"
                )
                return

            archive = archives[year]
            event_data = archive.get("event", {})
            participants = event_data.get("participants", {})
            gifts = event_data.get("gift_submissions", {})

            embed = disnake.Embed(
                title=f"🎄 Secret Santa {year}",
                description=f"**{len(participants)}** participants exchanged gifts",
                color=disnake.Color.gold(),
                timestamp=dt.datetime.now()
            )

            # Create gift exchange list with mentions
            if gifts:
                exchange_lines = []

                for giver_id, submission in gifts.items():
                    receiver_id = submission.get("receiver_id", "")
                    gift_desc = submission.get("gift", "No description")

                    # Use mentions for clickable names
                    giver_mention = f"<@{giver_id}>" if giver_id.isdigit() else participants.get(giver_id, "Unknown")
                    receiver_mention = f"<@{receiver_id}>" if receiver_id and receiver_id.isdigit() else submission.get(
                        "receiver_name", "Unknown")

                    # Format gift description (truncate if needed)
                    if len(gift_desc) > 60:
                        gift_desc = gift_desc[:57] + "..."

                    exchange_lines.append(f"🎁 {giver_mention} → {receiver_mention}")
                    exchange_lines.append(f"    ⤷ *{gift_desc}*")

                # Split into multiple fields if needed
                chunks = []
                current_chunk = []
                current_length = 0

                for line in exchange_lines:
                    line_length = len(line)
                    if current_length + line_length > 900:  # Leave buffer for field limits
                        chunks.append("\n".join(current_chunk))
                        current_chunk = [line]
                        current_length = line_length
                    else:
                        current_chunk.append(line)
                        current_length += line_length + 1

                if current_chunk:
                    chunks.append("\n".join(current_chunk))

                # Add fields
                for i, chunk in enumerate(chunks[:3]):  # Max 3 fields
                    field_name = "🎁 Gift Exchanges" if i == 0 else "​"  # Zero width space for continuation
                    embed.add_field(name=field_name, value=chunk, inline=False)

                if len(gifts) > 10:
                    embed.add_field(
                        name="​",
                        value=f"*... and {len(gifts) - 10} more exchanges*",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="📝 Status",
                    value="No gifts recorded for this year",
                    inline=False
                )

            # Add statistics
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
            total_participants = sum(
                len(archives[y].get("event", {}).get("participants", {}))
                for y in sorted_years
            )
            total_gifts = sum(
                len(archives[y].get("event", {}).get("gift_submissions", {}))
                for y in sorted_years
            )
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

            # Add summary statistics
            total_participants = sum(
                len(archives[y].get("event", {}).get("participants", {}))
                for y in sorted_years
            )
            total_gifts = sum(
                len(archives[y].get("event", {}).get("gift_submissions", {}))
                for y in sorted_years
            )

            embed.add_field(
                name="📊 All-Time Stats",
                value=f"Total Events: {len(sorted_years)}\nTotal Participants: {total_participants}\nTotal Gifts: {total_gifts}",
                inline=False
            )

            await inter.edit_original_response(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction adds for joining"""
        if payload.user_id == self.bot.user.id:
            return

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            return

        if event.get("join_closed"):
            return

        if payload.message_id != event.get("announcement_message_id"):
            return

        user_id = str(payload.user_id)

        # Already joined
        if user_id in event.get("participants", {}):
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

        # Add participant
        async with self._lock:
            event["participants"][user_id] = name
            self._save()

        # Send confirmation
        await self._send_dm(
            payload.user_id,
            f"✅ You've joined Secret Santa {self.state['current_year']}! 🎄\n\n"
            f"You'll receive your assignment when the event starts!"
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction removes for leaving"""
        if payload.user_id == self.bot.user.id:
            return

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            return

        if event.get("join_closed"):
            return

        if payload.message_id != event.get("announcement_message_id"):
            return

        user_id = str(payload.user_id)

        # Not a participant
        if user_id not in event.get("participants", {}):
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

            # Remove if no reactions
            if not has_reaction:
                async with self._lock:
                    event["participants"].pop(user_id, None)
                    self._save()

                await self._send_dm(
                    payload.user_id,
                    "❌ You've left Secret Santa. React again to rejoin!"
                )

        except Exception as e:
            self.logger.error(f"Error handling reaction remove: {e}")


def setup(bot):
    """Setup the cog"""
    bot.add_cog(SecretSantaCog(bot))