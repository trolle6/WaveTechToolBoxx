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

ROOT_DIR = pathlib.Path(__file__).parent
STATE_PATH = ROOT_DIR / "secret_santa_state.json"
BACKUP_PATH = ROOT_DIR / "secret_santa_state.bak"
ARCHIVE_DIR = ROOT_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
QUESTION_ARCHIVE_PATH = ROOT_DIR / "santa_questions.json"


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

    random.seed(time.time() + random.random())

    givers = participants.copy()
    receivers = participants.copy()
    random.shuffle(givers)
    random.shuffle(receivers)

    assigns: Dict[int, int] = {}
    attempts = 0
    max_attempts = 1000

    while givers and attempts < max_attempts:
        givers_attempt = givers.copy()
        receivers_attempt = receivers.copy()
        assigns_attempt = {}
        valid = True

        for giver in givers_attempt:
            valid_receivers = [
                r for r in receivers_attempt
                if r != giver and r not in pair_history.get(str(giver), [])
            ]

            if not valid_receivers:
                valid = False
                break

            receiver = random.choice(valid_receivers)
            assigns_attempt[giver] = receiver
            receivers_attempt.remove(receiver)

        if valid:
            assigns = assigns_attempt
            break

        attempts += 1
        random.seed(time.time() + random.random() + attempts)

    if attempts >= max_attempts:
        random.shuffle(givers)
        random.shuffle(receivers)
        assigns = {}

        for i, giver in enumerate(givers):
            receiver_idx = (i + 1) % len(receivers)
            assigns[giver] = receivers[receiver_idx]

    for giver, receiver in assigns.items():
        if str(giver) not in pair_history:
            pair_history[str(giver)] = []
        pair_history[str(giver)].append(receiver)

    return assigns


def _load_archived_history() -> Dict[str, List[int]]:
    history = {}

    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                if "event" in data and "assignments" in data["event"]:
                    assignments = data["event"]["assignments"]
                elif "assignments" in data:
                    assignments = data["assignments"]
                else:
                    continue

                for giver_str, receiver_id in assignments.items():
                    if giver_str not in history:
                        history[giver_str] = []
                    history[giver_str].append(int(receiver_id))

        except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
            print(f"Error loading archive {archive_file}: {e}")
            continue

    return history


def _archive_current_year(event_data: Dict[str, Any], year: int):
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

    async def _send_dm(self, user_id: int, message: str, max_attempts: int = 3):
        for attempt in range(max_attempts):
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(message)
                return True
            except disnake.HTTPException:
                if attempt == max_attempts - 1:
                    self.bot.logger.warning(f"Failed to DM {user_id}")
                await asyncio.sleep(1)
            except Exception as e:
                if attempt == max_attempts - 1:
                    self.bot.logger.error(f"Unexpected error DMing {user_id}: {e}")
                await asyncio.sleep(1)
        return False

    async def _dm_assignment(self, giver_id: int, receiver_id: int, message: str = None):
        if message is None:
            receiver_name = self._event()["participants"].get(str(receiver_id), f"User {receiver_id}")
            message = f"🎅 You are Secret Santa for {receiver_name} this year!"
        await self._send_dm(giver_id, message)

    async def _assign_role_to_participants(self, guild: disnake.Guild, role_id: int, participant_ids: List[int]):
        role = guild.get_role(role_id)
        if not role:
            self.bot.logger.error(f"Role with ID {role_id} not found in guild {guild.id}")
            return False

        if not guild.me.guild_permissions.manage_roles:
            self.bot.logger.error(f"Bot lacks 'Manage Roles' permission in guild {guild.id}")
            return False

        if role.position >= guild.me.top_role.position:
            self.bot.logger.error(f"Role {role.name} is above bot's highest role in hierarchy")
            return False

        success_count = 0
        failed_count = 0

        for user_id in participant_ids:
            try:
                member = guild.get_member(user_id)
                if not member:
                    try:
                        member = await guild.fetch_member(user_id)
                    except disnake.NotFound:
                        self.bot.logger.warning(f"Member {user_id} not found in guild {guild.id}")
                        failed_count += 1
                        continue

                if role not in member.roles:
                    await member.add_roles(role, reason="Secret Santa participant")
                    success_count += 1
                    self.bot.logger.info(f"Successfully assigned role to {member.display_name}")
            except disnake.Forbidden:
                self.bot.logger.error(f"Missing permissions to assign role to {user_id}")
                failed_count += 1
            except disnake.HTTPException as e:
                self.bot.logger.error(f"Error assigning role to {user_id}: {e}")
                failed_count += 1

        return {"success": success_count, "failed": failed_count}

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

    @santa_root.sub_command(name="participants", description="View current participants")
    @mod_only()
    async def santa_participants(self, inter: disnake.AppCmdInter):
        event = self._event()
        if not event or not event.get("active", False):
            await inter.send("❌ No active Secret Santa event", ephemeral=True)
            return

        participants = event["participants"]
        if not participants:
            await inter.send("❌ No participants yet", ephemeral=True)
            return

        participant_list = "\n".join([f"{name} (ID: {uid})" for uid, name in participants.items()])
        await inter.send(f"**Current Participants ({len(participants)}):**\n{participant_list}", ephemeral=True)

    @santa_root.sub_command(name="start", description="Start a new Secret Santa event")
    @mod_only()
    async def santa_start(self, inter: disnake.AppCmdInter, announcement_message_id: str, role_id: str):
        await inter.response.defer(ephemeral=True)

        try:
            message_id = int(announcement_message_id)
            role_id_int = int(role_id)
        except ValueError:
            await inter.edit_original_response("❌ Invalid message ID or role ID. Please provide valid numeric IDs.")
            return

        current_event = self._event()
        if current_event and current_event.get("active", False):
            await inter.edit_original_response("❌ There's already an active Secret Santa event")
            return

        participants = {}
        message_channel_id = "UNKNOWN"
        try:
            for channel in inter.guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    message_channel_id = channel.id
                    for reaction in message.reactions:
                        async for user in reaction.users():
                            if user.id == self.bot.user.id:
                                continue
                            if str(user.id) not in participants:
                                member = inter.guild.get_member(user.id)
                                name = member.display_name if member else user.name
                                participants[str(user.id)] = name
                    break
                except disnake.NotFound:
                    continue
                except disnake.Forbidden:
                    continue
        except Exception as e:
            self.bot.logger.error(f"Error fetching message or reactions: {e}")

        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": message_id,
            "role_id": role_id_int,
            "participants": participants,
            "assignments": {},
            "guild_id": inter.guild.id
        }

        async with self._lock:
            self.state["current_event"] = new_event
            self.state["current_year"] = dt.date.today().year
            await self._save()

        confirmation_tasks = []
        for user_id_str in participants:
            user_id = int(user_id_str)
            confirmation_tasks.append(
                self._send_dm(user_id, "✅ You've joined Secret Santa! 🎁")
            )
            await asyncio.sleep(0.5)

        results = await asyncio.gather(*confirmation_tasks, return_exceptions=True)

        successful_dms = sum(1 for result in results if result is True)
        failed_dms = len(results) - successful_dms

        response_msg = (
            f"✅ Secret Santa {self.state['current_year']} started!\n"
            f"Role ID: {role_id_int}\n"
            f"Participants can react to [this message](https://discord.com/channels/{inter.guild.id}/{message_channel_id}/{message_id}) to join.\n"
            f"Found {len(participants)} existing participants from reactions.\n"
            f"Sent confirmation DMs to {successful_dms} participants."
        )

        if failed_dms > 0:
            response_msg += f"\n⚠️ Failed to send DMs to {failed_dms} participants."

        await inter.edit_original_response(response_msg)

    @santa_root.sub_command(name="stop", description="Stop the current Secret Santa event without assigning")
    @mod_only()
    async def santa_stop(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        _archive_current_year(event, self.state["current_year"])

        async with self._lock:
            participants = list(map(int, event["participants"].keys()))
            for participant in participants:
                if str(participant) not in self.state["pair_history"]:
                    self.state["pair_history"][str(participant)] = []

            self.state["current_event"] = None
            await self._save()

        await inter.edit_original_response("✅ Secret Santa event stopped without assignments. Event data archived.")

    @santa_root.sub_command(name="shuffle", description="Manually trigger assignment (mod only)")
    @mod_only()
    async def santa_shuffle(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        participants = list(map(int, event["participants"].keys()))
        if len(participants) < 2:
            await inter.edit_original_response("❌ Not enough participants to make assignments")
            return

        combined_history = self.state.get("pair_history", {}).copy()
        archived_history = _load_archived_history()

        for giver, receivers in archived_history.items():
            if giver not in combined_history:
                combined_history[giver] = []
            combined_history[giver].extend(receivers)

        try:
            assigns = _make_assignments(participants, combined_history)
        except ValueError as e:
            await inter.edit_original_response(f"❌ {e}")
            return

        role_assignment_result = await self._assign_role_to_participants(
            inter.guild, event["role_id"], participants
        )

        festive_messages = [
            "🎅 Ho ho ho! You've been assigned to gift {receiver} this year!",
            "🎄 The elves have spoken! You're gifting {receiver} this Christmas!",
            "✨ The magic of Christmas pairs you with {receiver}!",
            "🦌 Rudolph's nose glows for {receiver}! You're their Secret Santa!"
        ]

        dm_tasks = []
        for giver, receiver in assigns.items():
            message = random.choice(festive_messages).format(
                receiver=event["participants"].get(str(receiver), f"User {receiver}")
            )
            dm_tasks.append(self._dm_assignment(giver, receiver, message))

        await asyncio.gather(*dm_tasks)

        async with self._lock:
            event["assignments"] = {str(k): v for k, v in assigns.items()}
            event["join_closed"] = True
            for giver, receiver in assigns.items():
                if str(giver) not in self.state["pair_history"]:
                    self.state["pair_history"][str(giver)] = []
                self.state["pair_history"][str(giver)].append(receiver)
            await self._save()

        response_message = "✅ Assignments shuffled and sent to participants!"
        if role_assignment_result is not False:
            response_message += f"\n✅ Role assigned to {role_assignment_result['success']} participants."
            if role_assignment_result['failed'] > 0:
                response_message += f"\n⚠️ Failed to assign role to {role_assignment_result['failed']} participants (permission issues)."
        else:
            response_message += "\n⚠️ Could not assign role to participants (role not found)."

        await inter.edit_original_response(response_message)

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

        await self._send_dm(payload.user_id, "✅ You've joined Secret Santa! 🎁")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        event = self._event()
        if (not event or not event.get("active", False) or
                payload.message_id != event.get("announcement_message_id")):
            return

        if event.get("join_closed", False):
            self.bot.logger.info(f"Ignoring reaction removal - assignments already made for user {payload.user_id}")
            return

        user_id_str = str(payload.user_id)
        if user_id_str not in event["participants"]:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)

            user_has_other_reactions = False
            for reaction in message.reactions:
                async for user in reaction.users():
                    if user.id == payload.user_id:
                        user_has_other_reactions = True
                        break
                if user_has_other_reactions:
                    break

            if not user_has_other_reactions:
                async with self._lock:
                    if user_id_str in event["participants"]:
                        removed_name = event["participants"].pop(user_id_str)
                        await self._save()
                        self.bot.logger.info(
                            f"Removed {removed_name} ({user_id_str}) from participants due to reaction removal"
                        )

                        await self._send_dm(
                            payload.user_id,
                            "❌ You've been removed from Secret Santa because you removed your reaction. "
                            "If this was a mistake, please react to the announcement message again to rejoin!"
                        )

        except Exception as e:
            self.bot.logger.error(f"Error checking reaction removal for user {user_id_str}: {e}")

    def cog_unload(self):
        if self._backup_task:
            self._backup_task.cancel()
        self.bot.logger.info("SecretSantaCog unloaded")


def setup(bot: commands.Bot):
    bot.add_cog(SecretSantaCog(bot))