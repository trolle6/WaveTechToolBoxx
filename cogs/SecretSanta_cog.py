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
ROOT = Path(__file__).parent
STATE_FILE = ROOT / "secret_santa_state.json"
ARCHIVE_DIR = ROOT / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)


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
        archive = {
            "year": year,
            "event": event.copy(),
            "archived_at": time.time(),
            "timestamp": dt.datetime.now().isoformat()
        }
        save_json(ARCHIVE_DIR / f"event_{year}.json", archive)

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

        # Load archived history
        for archive_file in ARCHIVE_DIR.glob("event_*.json"):
            try:
                archive_data = load_json(archive_file)
                event_data = archive_data.get("event", {})
                assignments = event_data.get("assignments", {})

                if isinstance(assignments, dict):
                    for giver, receiver in assignments.items():
                        history.setdefault(giver, []).append(int(receiver))
            except Exception:
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

        # Load archived events
        archives = []
        for archive_file in ARCHIVE_DIR.glob("event_*.json"):
            try:
                data = load_json(archive_file)
                if data:
                    archives.append(data)
            except Exception:
                continue

        if not archives:
            await inter.edit_original_response(content="❌ No archived events found")
            return

        archives.sort(key=lambda x: x.get("year", 0), reverse=True)

        if year:
            # Show specific year
            archive = next((a for a in archives if a.get("year") == year), None)

            if not archive:
                await inter.edit_original_response(content=f"❌ No event found for {year}")
                return

            event_data = archive.get("event", {})
            participants = event_data.get("participants", {})
            gifts = event_data.get("gift_submissions", {})

            embed = disnake.Embed(
                title=f"🎄 Secret Santa {year}",
                color=disnake.Color.gold()
            )

            embed.add_field(name="Participants", value=str(len(participants)), inline=True)
            embed.add_field(name="Gifts Submitted", value=str(len(gifts)), inline=True)

            if gifts:
                gift_list = []
                for giver_id, submission in list(gifts.items())[:5]:
                    giver = participants.get(giver_id, f"User {giver_id}")
                    receiver = submission.get("receiver_name", "Unknown")
                    gift_list.append(f"🎁 {giver} → {receiver}")

                embed.add_field(
                    name="Gifts",
                    value="\n".join(gift_list),
                    inline=False
                )

            await inter.edit_original_response(embed=embed)

        else:
            # Show all years
            embed = disnake.Embed(
                title="🎄 Secret Santa History",
                color=disnake.Color.blue()
            )

            for archive in archives[:10]:
                year_val = archive.get("year", "Unknown")
                event_data = archive.get("event", {})
                participants = event_data.get("participants", {})
                gifts = event_data.get("gift_submissions", {})

                embed.add_field(
                    name=f"Year {year_val}",
                    value=f"👥 {len(participants)} participants | 🎁 {len(gifts)} gifts",
                    inline=True
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