from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import random
import time
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Optional, Callable, Set

import disnake
from disnake.ext import commands, tasks

# Constants
ROOT_DIR = pathlib.Path(__file__).parent
STATE_PATH = ROOT_DIR / "secret_santa_state.json"
BACKUP_PATH = ROOT_DIR / "secret_santa_state.bak"
ARCHIVE_DIR = ROOT_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
QUESTION_ARCHIVE_PATH = ROOT_DIR / "santa_questions.json"


# State helpers
def _load_state() -> Dict[str, Any]:
    for path in [STATE_PATH, BACKUP_PATH]:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
    return {"pair_history": {}, "current_year": dt.date.today().year, "current_event": None}


def _save_state(state: Dict[str, Any]) -> None:
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


def _make_assignments(participants: List[int], pair_history: Dict[str, List[int]]) -> Dict[int, int]:
    if len(participants) < 2:
        raise ValueError("Need at least two participants for Secret Santa.")

    # Add atmospheric randomness by shuffling with a time-based seed
    random.seed(time.time() + random.random())

    givers = participants.copy()
    random.shuffle(givers)
    receivers = participants.copy()
    random.shuffle(receivers)

    assigns: Dict[int, int] = {}
    attempts = 0
    max_attempts = 100  # Prevent infinite loops

    while givers and attempts < max_attempts:
        giver = givers[0]
        valid_receivers = [
            r for r in receivers
            if r != giver and r not in pair_history.get(str(giver), [])
        ]

        if not valid_receivers:
            # If no valid receivers, reset and try again with fresh randomness
            random.seed(time.time() + random.random())
            random.shuffle(givers)
            random.shuffle(receivers)
            attempts += 1
            continue

        # Weight random selection towards less frequent pairings
        receiver = random.choice(valid_receivers)
        assigns[giver] = receiver

        # Update history
        if str(giver) not in pair_history:
            pair_history[str(giver)] = []
        pair_history[str(giver)].append(receiver)

        givers.remove(giver)
        receivers.remove(receiver)
        attempts = 0

    if attempts >= max_attempts:
        raise ValueError("Could not create valid assignments after multiple attempts")

    return assigns


def _load_archived_history() -> Dict[str, List[int]]:
    """Load pairing history from all archived events"""
    history = {}

    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Handle both old and new archive formats
                if "event" in data and "assignments" in data["event"]:
                    # New format: {"event": {"assignments": {...}}}
                    assignments = data["event"]["assignments"]
                elif "assignments" in data:
                    # Old format: {"assignments": {...}}
                    assignments = data["assignments"]
                else:
                    continue

                # Add to history
                for giver_str, receiver_id in assignments.items():
                    if giver_str not in history:
                        history[giver_str] = []
                    history[giver_str].append(int(receiver_id))

        except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
            print(f"Error loading archive {archive_file}: {e}")
            continue

    return history


def _archive_current_year(event_data: Dict[str, Any], year: int):
    """Archive the current year to a separate file"""
    archive_data = {
        "year": year,
        "event": event_data.copy(),
        "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat()
    }

    archive_path = ARCHIVE_DIR / f"event_{year}.json"

    try:
        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to archive event {year}: {e}")


def mod_only():
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            mod_role_id = inter.bot.config.discord.moderator_role_id
            return any(r.id == mod_role_id for r in inter.author.roles)
        except Exception as e:
            inter.bot.logger.error(f"Error in mod_only check: {e}")
            return False

    return commands.check(predicate)


def participant_only():
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                return False
            event = cog._event()
            if not event or not event.get("active", False):
                return False
            return str(inter.author.id) in event["participants"]
        except Exception as e:
            inter.bot.logger.error(f"Error in participant_only check: {e}")
            return False

    return commands.check(predicate)


class SecretSantaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()
        self.questions = _load_questions()
        self._lock = asyncio.Lock()
        self._backup_task = None

    def _event(self) -> Optional[Dict[str, Any]]:
        return self.state.get("current_event")

    async def _save(self):
        _save_state(self.state)

    async def _dm_assignment(self, giver_id: int, receiver_id: int, message: str = None):
        if message is None:
            receiver_name = self._event()["participants"].get(str(receiver_id), f"User {receiver_id}")
            message = f"🎅 You are Secret Santa for {receiver_name} this year!"

        for attempt in range(3):
            try:
                user = await self.bot.fetch_user(giver_id)
                receiver_name = self._event()["participants"].get(str(receiver_id), f"User {receiver_id}")
                await user.send(message)
                return
            except disnake.HTTPException:
                if attempt == 2:
                    self.bot.logger.warning(f"Failed to DM {giver_id}")
                await asyncio.sleep(1)
            except Exception as e:
                if attempt == 2:
                    self.bot.logger.error(f"Unexpected error DMing {giver_id}: {e}")
                await asyncio.sleep(1)

    async def cog_load(self):
        self._backup_task = asyncio.create_task(self._start_backup_task())

    async def _start_backup_task(self):
        try:
            while True:
                await asyncio.sleep(3600)
                try:
                    async with self._lock:
                        _save_state(self.state)
                        _save_questions(self.questions)
                        BACKUP_PATH.write_text(json.dumps(self.state, indent=2))
                except Exception as e:
                    self.bot.logger.error(f"Backup failed: {e}")
        except asyncio.CancelledError:
            self.bot.logger.info("Backup task cancelled")
        except Exception as e:
            self.bot.logger.error(f"Backup task error: {e}")

    @commands.slash_command(name="santa")
    async def santa_root(self, _: disnake.AppCmdInter):
        pass

    @santa_root.sub_command(name="start", description="Start a new Secret Santa event")
    @mod_only()
    async def santa_start(self, inter: disnake.AppCmdInter, announcement_message_id: str):
        """Start a new Secret Santa event with the given message ID"""
        await inter.response.defer(ephemeral=True)

        try:
            message_id = int(announcement_message_id)
        except ValueError:
            await inter.edit_original_response("❌ Invalid message ID. Please provide a valid numeric message ID.")
            return

        current_event = self._event()
        if current_event and current_event.get("active", False):
            await inter.edit_original_response("❌ There's already an active Secret Santa event")
            return

        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": message_id,
            "participants": {},
            "assignments": {},
            "guild_id": inter.guild.id
        }

        async with self._lock:
            self.state["current_event"] = new_event
            self.state["current_year"] = dt.date.today().year
            await self._save()

        await inter.edit_original_response(
            f"✅ Secret Santa {self.state['current_year']} started!\n"
            f"Participants can react to [this message](https://discord.com/channels/{inter.guild.id}/{inter.channel.id}/{message_id}) to join."
        )

    @santa_root.sub_command(name="stop", description="Stop the current Secret Santa event without assigning")
    @mod_only()
    async def santa_stop(self, inter: disnake.AppCmdInter):
        """Stop the current Secret Santa event without making assignments"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        # Archive to separate file
        _archive_current_year(event, self.state["current_year"])

        async with self._lock:
            # Update pair history with current participants for algorithm
            participants = list(map(int, event["participants"].keys()))
            for participant in participants:
                if str(participant) not in self.state["pair_history"]:
                    self.state["pair_history"][str(participant)] = []

            # Clear current event
            self.state["current_event"] = None
            await self._save()

        await inter.edit_original_response("✅ Secret Santa event stopped without assignments. Event data archived.")

    @santa_root.sub_command(name="shuffle", description="Manually trigger assignment (mod only)")
    @mod_only()
    async def santa_shuffle(self, inter: disnake.AppCmdInter):
        event = self._event()
        if not event or not event.get("active", False):
            await inter.send("❌ No active Secret Santa event", ephemeral=True)
            return

        participants = list(map(int, event["participants"].keys()))
        if len(participants) < 2:
            await inter.send("❌ Not enough participants to make assignments", ephemeral=True)
            return

        # Load both current and archived history
        combined_history = self.state.get("pair_history", {}).copy()
        archived_history = _load_archived_history()

        # Merge histories
        for giver, receivers in archived_history.items():
            if giver not in combined_history:
                combined_history[giver] = []
            combined_history[giver].extend(receivers)

        try:
            assigns = _make_assignments(participants, combined_history)
        except ValueError as e:
            await inter.send(f"❌ {e}", ephemeral=True)
            return

        # Send DMs with some festive randomness
        festive_messages = [
            "🎅 Ho ho ho! You've been assigned to gift {receiver} this year!",
            "🎄 The elves have spoken! You're gifting {receiver} this Christmas!",
            "✨ The magic of Christmas pairs you with {receiver}!",
            "🦌 Rudolph's nose glows for {receiver}! You're their Secret Santa!"
        ]

        for giver, receiver in assigns.items():
            message = random.choice(festive_messages).format(
                receiver=event["participants"].get(str(receiver), f"User {receiver}")
            )
            await self._dm_assignment(giver, receiver, message)

        async with self._lock:
            event["assignments"] = {str(k): v for k, v in assigns.items()}
            event["join_closed"] = True
            # Update the actual history with new assignments
            for giver, receiver in assigns.items():
                if str(giver) not in self.state["pair_history"]:
                    self.state["pair_history"][str(giver)] = []
                self.state["pair_history"][str(giver)].append(receiver)
            await self._save()

        await inter.send("✅ Assignments shuffled and sent to participants!")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        event = self._event()
        if (not event or not event.get("active", False) or
                event.get("join_closed", False) or
                payload.message_id != event.get("announcement_message_id")):
            return

        if str(payload.user_id) in event["participants"]:
            return

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

        try:
            user = await self.bot.fetch_user(payload.user_id)
            await user.send("✅ You've joined Secret Santa! 🎁")
        except disnake.HTTPException:
            pass

    def cog_unload(self):
        if self._backup_task:
            self._backup_task.cancel()
        self.bot.logger.info("SecretSantaCog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(SecretSantaCog(bot))