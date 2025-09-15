from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import random
import time
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Optional, Callable, Set, Union

import disnake
from disnake.ext import commands, tasks

# Constants
JOIN_EMOJI = "🎁"
ROOT_DIR = pathlib.Path(__file__).parent
STATE_PATH = ROOT_DIR / "secret_santa_state.json"
BACKUP_PATH = ROOT_DIR / "secret_santa_state.bak"
ARCHIVE_DIR = ROOT_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
QUESTION_ARCHIVE_PATH = ROOT_DIR / "santa_questions.json"
GIFT_SUBMISSION_PATH = ROOT_DIR / "gift_submissions.json"


# State helpers
def _load_state() -> Dict[str, Any]:
    """Load state with fallback to backup if main file is corrupted"""
    for path in [STATE_PATH, BACKUP_PATH]:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
    return {
        "pair_history": {},
        "current_year": dt.date.today().year,
        "current_event": None,
    }


def _save_state(state: Dict[str, Any]) -> None:
    """Save state atomically with backup"""
    temp_path = STATE_PATH.with_suffix('.tmp')
    temp_path.write_text(json.dumps(state, indent=2))
    temp_path.replace(STATE_PATH)


def _load_questions() -> Dict[str, Any]:
    if QUESTION_ARCHIVE_PATH.exists():
        return json.loads(QUESTION_ARCHIVE_PATH.read_text())
    return {"questions": {}}


def _save_questions(questions: Dict[str, Any]) -> None:
    temp_path = QUESTION_ARCHIVE_PATH.with_suffix('.tmp')
    temp_path.write_text(json.dumps(questions, indent=2))
    temp_path.replace(QUESTION_ARCHIVE_PATH)


def _load_gift_submissions() -> Dict[str, Any]:
    if GIFT_SUBMISSION_PATH.exists():
        return json.loads(GIFT_SUBMISSION_PATH.read_text())
    return {"submissions": []}


def _save_gift_submissions(submissions: Dict[str, Any]) -> None:
    temp_path = GIFT_SUBMISSION_PATH.with_suffix('.tmp')
    temp_path.write_text(json.dumps(submissions, indent=2))
    temp_path.replace(GIFT_SUBMISSION_PATH)


# Pair assignment
def _make_assignments(participants: List[int], pair_history: Dict[str, List[int]]) -> Dict[int, int]:
    """Create assignments ensuring no repeats until full cycle"""
    if len(participants) < 2:
        raise ValueError("Need at least two participants for Secret Santa.")

    givers = participants.copy()
    random.shuffle(givers)
    receivers = givers.copy()
    assigns: Dict[int, int] = {}

    for giver in givers:
        for _ in range(len(receivers)):
            cand = receivers[0]
            if cand != giver and cand not in pair_history.get(str(giver), []):
                assigns[giver] = cand
                receivers.pop(0)
                break
            receivers.append(receivers.pop(0))
        else:  # Reset giver history if stuck
            pair_history[str(giver)] = []
            return _make_assignments(participants, pair_history)
    return assigns


def _maybe_reset_histories(pair_history: Dict[str, List[int]], universe: Set[int]):
    """Reset histories for users who have gifted everyone"""
    for giver_str, recs in list(pair_history.items()):
        giver = int(giver_str)
        if set(recs) >= (universe - {giver}):
            pair_history[giver_str] = []


# Archiving
def _archive_year(year: int, assignments: Dict[int, int], participants: Dict[int, str]) -> pathlib.Path:
    """Create archive file for the year"""
    lines = [f"Secret Santa {year}", ""]
    lines += [f"<@{g}> -> <@{r}>" for g, r in assignments.items()]
    lines += ["", "Participants:"]
    lines += [f"• <@{uid}> ({name})" for uid, name in participants.items()]

    path = ARCHIVE_DIR / f"wavesanta_{year}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# Decorators
def mod_only() -> Callable[[commands.InvokableSlashCommand], commands.InvokableSlashCommand]:
    """Restrict command to moderators"""

    async def predicate(inter: disnake.ApplicationCommandInteraction):
        mod_role_id = inter.bot.config.discord.moderator_role_id
        return any(r.id == mod_role_id for r in inter.author.roles)

    return commands.check(predicate)


def participant_only() -> Callable[[commands.InvokableSlashCommand], commands.InvokableSlashCommand]:
    """Restrict command to Secret Santa participants"""

    async def predicate(inter: disnake.ApplicationCommandInteraction):
        event = inter.bot.get_cog("SecretSantaCog")._event()
        if not event:
            await inter.send("No active Secret Santa event", ephemeral=True)
            return False
        return str(inter.author.id) in event["participants"]

    return commands.check(predicate)


class SecretSantaCog(commands.Cog):
    """Automated Secret Santa with Gift Tracking"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()
        self.questions = _load_questions()
        self.gift_submissions = _load_gift_submissions()
        self._lock = asyncio.Lock()
        self.deadline_check.start()
        self.active_events = defaultdict(dict)

        # Don't start backup task here, wait for cog_load
        self._backup_task = None

    async def cog_load(self):
        """Start the backup task when the cog is loaded"""
        self._backup_task = asyncio.create_task(self._start_backup_task())

    async def _start_backup_task(self):
        """Periodically backup state"""
        try:
            while True:
                await asyncio.sleep(3600)  # Hourly backups
                try:
                    async with self._lock:
                        _save_state(self.state)
                        _save_questions(self.questions)
                        _save_gift_submissions(self.gift_submissions)
                        BACKUP_PATH.write_text(json.dumps(self.state, indent=2))
                except Exception as e:
                    self.bot.logger.error(f"Backup failed: {e}")
        except asyncio.CancelledError:
            self.bot.logger.info("Backup task cancelled")
        except Exception as e:
            self.bot.logger.error(f"Backup task error: {e}")

    async def _save(self):
        """Thread-safe state saving"""
        async with self._lock:
            _save_state(self.state)

    def _event(self) -> Optional[Dict[str, Any]]:
        return self.state.get("current_event")

    async def _dm_assignment(self, giver_id: int, receiver_id: int):
        """DM assignment with retry logic"""
        for attempt in range(3):
            try:
                user = await self.bot.fetch_user(giver_id)
                receiver_name = self._event()["participants"].get(str(receiver_id), f"User {receiver_id}")
                await user.send(
                    f"🎅 You are Secret Santa for {receiver_name} (<@{receiver_id}>) this year! "
                    "Keep it secret, keep it safe.\n\n"
                    "You'll receive questions from them via DM, and you can submit your gift using "
                    "`/santa submit_gift` when you're ready!"
                )
                return
            except disnake.HTTPException as e:
                if attempt == 2:
                    self.bot.logger.warning(f"Failed to DM {giver_id}: {e}")
                await asyncio.sleep(1)

    async def _send_to_mods(self, content: str):
        """Send message to moderator channel"""
        try:
            chan_id = self.bot.config.discord.moderator_channel_id
            chan = self.bot.get_channel(chan_id) or await self.bot.fetch_channel(chan_id)
            await chan.send(content)
        except Exception as e:
            self.bot.logger.error(f"Failed to send to mods: {e}")

    async def _rephrase_question(self, question: str) -> str:
        """Rephrase question using AI (simplified version)"""
        return f"❓ Rephrased: {question}"

    # Command group
    @commands.slash_command(name="santa")
    async def santa_root(self, _: disnake.AppCmdInter):
        pass

    # NEW: Start command for moderators
    @santa_root.sub_command(name="start", description="Start a new Secret Santa event")
    @mod_only()
    async def santa_start(
            self,
            inter: disnake.AppCmdInter,
            announcement_message_id: int,
            channel: disnake.TextChannel,
            deadline_days: int = commands.Param(description="Days until signup closes", gt=0)
    ):
        """Start a new Secret Santa event"""
        if self._event() and self._event().get("active", False):
            await inter.send("❌ There's already an active Secret Santa event", ephemeral=True)
            return

        # Calculate deadline
        deadline = dt.datetime.now() + dt.timedelta(days=deadline_days)

        # Create new event
        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": announcement_message_id,
            "channel_id": channel.id,
            "deadline": deadline.isoformat(),
            "participants": {},
            "assignments": {},
            "guild_id": inter.guild.id
        }

        async with self._lock:
            self.state["current_event"] = new_event
            self.state["current_year"] = dt.date.today().year
            await self._save()

        # Store in active events
        self.active_events[inter.guild.id] = new_event

        await inter.send(
            f"✅ Secret Santa {self.state['current_year']} started!\n"
            f"Participants can react with 🎁 to [this message](https://discord.com/channels/{inter.guild.id}/{channel.id}/{announcement_message_id}) "
            f"in {channel.mention} to join.\n"
            f"Signups close in {deadline_days} days on {deadline.strftime('%B %d, %Y')}"
        )

    # Essential commands
    @santa_root.sub_command(name="ask", description="Ask your Santa a question")
    @participant_only()
    async def santa_ask(self, inter: disnake.AppCmdInter, question: str):
        """Ask your Santa an anonymous question"""
        event = self._event()
        if not event or not event.get("active", False):
            await inter.send("No active Secret Santa event", ephemeral=True)
            return

        # Find who is assigned to this user (their Santa)
        receiver_id = inter.author.id
        giver_id = None
        for giver, receiver in event["assignments"].items():
            if receiver == receiver_id:
                giver_id = giver
                break

        if not giver_id:
            await inter.send("❌ Couldn't find your Santa. Contact a moderator.", ephemeral=True)
            return

        # Rephrase question
        rephrased = await self._rephrase_question(question)

        # Save question
        year = self.state["current_year"]
        question_id = f"{year}-{time.time()}"
        async with self._lock:
            if str(year) not in self.questions["questions"]:
                self.questions["questions"][str(year)] = {}
            self.questions["questions"][str(year)][question_id] = {
                "receiver_id": receiver_id,
                "giver_id": giver_id,
                "original": question,
                "rephrased": rephrased
            }
            _save_questions(self.questions)

        # Send question to Santa
        try:
            santa_user = await self.bot.fetch_user(giver_id)
            await santa_user.send(
                f"❓ **Question from your giftee:**\n"
                f"{rephrased}\n\n"
                f"You can reply to them directly in this DM!"
            )
            await inter.send("✅ Your question has been sent to your Santa!", ephemeral=True)
        except Exception as e:
            self.bot.logger.error(f"Failed to send question: {e}")
            await inter.send("❌ Failed to send question. Contact a moderator.", ephemeral=True)

    @santa_root.sub_command(name="submit_gift", description="Submit your gift for your receiver")
    @participant_only()
    async def santa_submit_gift(
            self,
            inter: disnake.AppCmdInter,
            description: str,
            image: disnake.Attachment = None
    ):
        """Submit your gift to the moderators"""
        event = self._event()
        if not event or not event.get("active", False):
            await inter.send("No active Secret Santa event", ephemeral=True)
            return

        # Find who this user is assigned to
        giver_id = inter.author.id
        receiver_id = event["assignments"].get(giver_id)

        if not receiver_id:
            await inter.send("❌ Couldn't find your receiver. Contact a moderator.", ephemeral=True)
            return

        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")

        # Save submission
        submission = {
            "year": self.state["current_year"],
            "giver_id": giver_id,
            "receiver_id": receiver_id,
            "description": description,
            "timestamp": dt.datetime.now().isoformat(),
            "image_url": image.url if image else None
        }

        async with self._lock:
            self.gift_submissions["submissions"].append(submission)
            _save_gift_submissions(self.gift_submissions)

        # Send to mods
        image_text = f"\n📸 [Image Link]({image.url})" if image else ""
        await self._send_to_mods(
            f"🎁 **New Gift Submission**\n"
            f"**From:** <@{giver_id}>\n"
            f"**To:** {receiver_name} (<@{receiver_id}>)\n"
            f"**Description:** {description}{image_text}"
        )

        await inter.send("✅ Your gift submission has been sent to the moderators!", ephemeral=True)

    @santa_root.sub_command(name="questions", description="View questions from a specific year (Mods only)")
    @mod_only()
    async def santa_questions(self, inter: disnake.AppCmdInter, year: int):
        """View archived questions"""
        year_questions = self.questions["questions"].get(str(year), {})
        if not year_questions:
            await inter.send(f"No questions found for {year}", ephemeral=True)
            return

        response = [f"**Questions from {year}:**"]
        for qid, question in year_questions.items():
            response.append(
                f"\n❓ **Question {qid.split('-')[-1]}**\n"
                f"From: <@{question['receiver_id']}>\n"
                f"To: <@{question['giver_id']}>\n"
                f"Original: {question['original']}\n"
                f"Rephrased: {question['rephrased']}"
            )

        await inter.send("\n".join(response), ephemeral=True)

    @santa_root.sub_command(name="reveal", description="Reveal pairs")
    @mod_only()
    async def santa_reveal(self, inter: disnake.AppCmdInter):
        """Reveal all Santa-giftee pairs"""
        event = self._event()
        if not event or not event["assignments"]:
            await inter.send("No assignments to reveal.", ephemeral=True)
            return

        lines = [f"<@{g}> → <@{r}>" for g, r in event["assignments"].items()]
        await inter.send(
            f"🎉 **Secret Santa {self.state['current_year']} Reveal!**\n" +
            "\n".join(lines)
        )

    # NEW: Stop command for moderators
    @santa_root.sub_command(name="stop", description="Stop the current Secret Santa event")
    @mod_only()
    async def santa_stop(self, inter: disnake.AppCmdInter):
        """Stop the current Secret Santa event"""
        event = self._event()
        if not event or not event.get("active", False):
            await inter.send("❌ No active Secret Santa event", ephemeral=True)
            return

        # Archive the event
        participants = event["participants"]
        assignments = event["assignments"]
        year = self.state["current_year"]

        try:
            archive_path = _archive_year(year, assignments, participants)
            await inter.send(f"✅ Event archived: {archive_path.name}")
        except Exception as e:
            self.bot.logger.error(f"Archiving failed: {e}")
            await inter.send("❌ Archiving failed, but event stopped")

        async with self._lock:
            # Update history
            for giver_id in assignments:
                giver_key = str(giver_id)
                if giver_key not in self.state["pair_history"]:
                    self.state["pair_history"][giver_key] = []
                self.state["pair_history"][giver_key].append(assignments[giver_id])

            # Clear current event
            self.state["current_event"] = None
            await self._save()

        await inter.send("✅ Secret Santa event stopped")

    # Listeners
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """Handle join reactions"""
        if (payload.emoji.name != JOIN_EMOJI or
                payload.user_id == self.bot.user.id):
            return

        event = self._event()
        if (not event or not event.get("active", False) or
                event.get("join_closed", False) or
                payload.message_id != event.get("announcement_message_id")):
            return

        if str(payload.user_id) in event["participants"]:
            return

        # Get display name
        name = f"User {payload.user_id}"
        if payload.guild_id:
            try:
                guild = self.bot.get_guild(payload.guild_id)
                member = guild.get_member(payload.user_id) if guild else None
                name = member.display_name if member else name
            except Exception:
                pass

        async with self._lock:
            event["participants"][str(payload.user_id)] = name
            await self._save()

        # DM confirmation
        try:
            user = await self.bot.fetch_user(payload.user_id)
            await user.send("✅ You've joined Secret Santa! 🎁")
        except disnake.HTTPException:
            pass

    # Tasks
    @tasks.loop(minutes=5)
    async def deadline_check(self):
        """Check if join deadline has passed"""
        await self.bot.wait_until_ready()
        event = self._event()
        if not event or event.get("join_closed", False) or not event.get("deadline"):
            return

        try:
            deadline = dt.datetime.fromisoformat(event["deadline"])
            if dt.datetime.now() > deadline:
                guild_id = event.get("guild_id")
                if not guild_id:
                    self.bot.logger.error("Missing guild_id in event")
                    return

                guild = self.bot.get_guild(guild_id)
                if not guild:
                    self.bot.logger.error(f"Guild not found: {guild_id}")
                    return

                # Close joining automatically
                chan_id = event["channel_id"]
                chan = guild.get_channel(chan_id)

                if not chan:
                    self.bot.logger.error(f"Channel not found: {chan_id}")
                    return

                # Send notification
                await chan.send("⏰ Joining period has ended! Assigning Santas now...")

                # Close and assign
                participants = list(map(int, event["participants"].keys()))

                if len(participants) < 2:
                    await chan.send("❌ Not enough participants (need at least 2)")
                    async with self._lock:
                        event["active"] = False
                        await self._save()
                    return

                assigns = _make_assignments(participants, self.state["pair_history"])

                async with self._lock:
                    event["assignments"] = assigns
                    event["join_closed"] = True
                    await self._save()

                # DM assignments with error handling
                for giver, receiver in assigns.items():
                    try:
                        await self._dm_assignment(giver, receiver)
                    except Exception as e:
                        self.bot.logger.error(f"Failed to DM {giver}: {e}\n{traceback.format_exc()}")

                await chan.send("🎁 Assignments sent via DM! Participants can now use `/santa ask` to ask questions")
        except Exception as e:
            self.bot.logger.error(f"Auto-close failed: {e}\n{traceback.format_exc()}")

    @deadline_check.before_loop
    async def _before_deadline_loop(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        """Cleanup on cog unload"""
        if self._backup_task:
            self._backup_task.cancel()
        self.deadline_check.cancel()
        self.bot.logger.info("SecretSantaCog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(SecretSantaCog(bot))