from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import random
import time
import traceback
import os
import psutil
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

try:
    import psutil
except ImportError:
    psutil = None


def _get_enhanced_entropy() -> float:
    """Get high-quality entropy from multiple reliable sources"""
    entropy_sources = []

    # 1. Cryptographic randomness (most important)
    try:
        entropy_sources.append(int.from_bytes(os.urandom(8), byteorder='big'))
    except:
        entropy_sources.append(random.getrandbits(64))

    # 2. High-precision time
    precise_time = time.time_ns() if hasattr(time, 'time_ns') else time.time() * 1_000_000_000
    entropy_sources.append(precise_time)

    # 3. Process and system identifiers
    entropy_sources.append(os.getpid())
    entropy_sources.append(hash(os.urandom(4)) if hasattr(os, 'urandom') else random.random())

    # 4. System performance metrics (more reliable than temperature)
    if psutil:
        try:
            entropy_sources.append(psutil.cpu_percent())
            entropy_sources.append(psutil.virtual_memory().used % 1000)
            entropy_sources.append(int(time.time() * 1000) % 1000)
        except:
            pass

    # Add some additional time-based entropy
    entropy_sources.append(dt.datetime.now().microsecond)
    entropy_sources.append(time.monotonic_ns() if hasattr(time, 'monotonic_ns') else time.monotonic() * 1_000_000_000)

    # Combine all sources
    cosmic_hash = hash(tuple(entropy_sources))
    final_entropy = (cosmic_hash % 1000000) / 1000000.0

    return final_entropy


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

    # Use enhanced entropy for better randomness
    hardware_entropy = _get_enhanced_entropy()

    # Combine multiple entropy sources
    seed_value = time.time() + hardware_entropy + random.random()

    random.seed(seed_value)

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
        # Refresh entropy for each attempt
        new_entropy = _get_enhanced_entropy()
        new_seed = time.time() + random.random() + attempts + new_entropy
        random.seed(new_seed)

    if attempts >= max_attempts:
        # Fallback to simple rotation if complex matching fails
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
            continue

    return history


def _get_archived_events() -> List[Dict[str, Any]]:
    """Get all archived events with detailed information"""
    events = []

    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                events.append(data)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

    return sorted(events, key=lambda x: x.get("year", 0), reverse=True)


def _archive_current_year(event_data: Dict[str, Any], year: int):
    archive_data = {
        "year": year,
        "event": event_data.copy(),
        "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat(),
        "shuffle_entropy": {
            "hardware_entropy": _get_enhanced_entropy(),
            "timestamp": time.time()
        }
    }

    archive_path = ARCHIVE_DIR / f"event_{year}.json"

    try:
        with open(archive_path, 'w', encoding='utf-8') as f:
            json.dump(archive_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        pass


def mod_only():
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            # Try to get moderator role from config with proper error handling
            if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'discord'):
                mod_role_id = inter.bot.config.discord.moderator_role_id
                if mod_role_id and any(r.id == mod_role_id for r in inter.author.roles):
                    return True
            # Fallback: check for administrator permission
            return inter.author.guild_permissions.administrator
        except Exception as e:
            # If anything fails, fall back to administrator check
            return inter.author.guild_permissions.administrator

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
                    pass
                await asyncio.sleep(1)
            except Exception as e:
                if attempt == max_attempts - 1:
                    pass
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
            return False

        if not guild.me.guild_permissions.manage_roles:
            return False

        if role.position >= guild.me.top_role.position:
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
                        failed_count += 1
                        continue

                if role not in member.roles:
                    await member.add_roles(role, reason="Secret Santa participant")
                    success_count += 1
            except disnake.Forbidden:
                failed_count += 1
            except disnake.HTTPException as e:
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
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            pass

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.AppCmdInter):
        pass

    @ss_root.sub_command(name="participants", description="View current participants")
    @mod_only()
    async def ss_participants(self, inter: disnake.AppCmdInter):
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

    @ss_root.sub_command(name="start", description="Start a new Secret Santa event")
    @mod_only()
    async def ss_start(self, inter: disnake.AppCmdInter, announcement_message_id: str, role_id: str):
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
            pass

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

    @ss_root.sub_command(name="stop", description="Stop the current Secret Santa event without assigning")
    @mod_only()
    async def ss_stop(self, inter: disnake.AppCmdInter):
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

    @ss_root.sub_command(name="shuffle", description="Manually trigger assignment (mod only)")
    @mod_only()
    async def ss_shuffle(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        participants = list(map(int, event["participants"].keys()))
        if len(participants) < 2:
            await inter.edit_original_response("❌ Not enough participants to make assignments")
            return

        # Get enhanced entropy for better randomness
        hardware_entropy = _get_enhanced_entropy()
        await inter.edit_original_response(
            f"🎲 Using enhanced entropy ({hardware_entropy:.6f}) for optimal randomness...")

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
        response_message += f"\n🎲 Shuffled using enhanced entropy: {hardware_entropy:.6f}"

        if role_assignment_result is not False:
            response_message += f"\n✅ Role assigned to {role_assignment_result['success']} participants."
            if role_assignment_result['failed'] > 0:
                response_message += f"\n⚠️ Failed to assign role to {role_assignment_result['failed']} participants (permission issues)."
        else:
            response_message += "\n⚠️ Could not assign role to participants (role not found)."

        await inter.edit_original_response(response_message)

    @ss_root.sub_command(name="history", description="Show previous Secret Santa events")
    @mod_only()
    async def ss_history(self, inter: disnake.AppCmdInter, year: int = None):
        await inter.response.defer(ephemeral=True)

        events = _get_archived_events()
        if not events:
            await inter.edit_original_response("No archived events found.")
            return

        if year:
            event_data = next((e for e in events if e.get("year") == year), None)
            if not event_data:
                await inter.edit_original_response(f"No event found for year {year}")
                return

            embed = disnake.Embed(title=f"Secret Santa {year}", color=disnake.Color.gold())

            # Add event details
            if "event" in event_data:
                event_info = event_data["event"]
                if "participants" in event_info:
                    participant_count = len(event_info["participants"])
                    embed.add_field(name="Participants", value=str(participant_count), inline=True)

                if "assignments" in event_info:
                    assignments_text = "\n".join(
                        [f"<@{giver}> → <@{receiver}>" for giver, receiver in event_info["assignments"].items()]
                    )
                    if len(assignments_text) > 1024:
                        assignments_text = assignments_text[:1020] + "..."
                    embed.add_field(name="Assignments", value=assignments_text, inline=False)

            # Add shuffle entropy info if available
            if "shuffle_entropy" in event_data:
                entropy = event_data["shuffle_entropy"]
                entropy_info = []
                if "hardware_entropy" in entropy:
                    entropy_info.append(f"Entropy: {entropy['hardware_entropy']:.6f}")

                if entropy_info:
                    embed.add_field(name="Shuffle Details", value=" | ".join(entropy_info), inline=False)

            await inter.edit_original_response(embed=embed)
        else:
            embed = disnake.Embed(title="Secret Santa History", color=disnake.Color.blue())

            for event in events:
                year_val = event.get("year", "Unknown")
                participant_count = len(event.get("event", {}).get("participants", {}))

                # Add entropy info if available
                extra_info = ""
                if "shuffle_entropy" in event and "hardware_entropy" in event["shuffle_entropy"]:
                    entropy_val = event["shuffle_entropy"]["hardware_entropy"]
                    extra_info = f" (Entropy: {entropy_val:.6f})"

                embed.add_field(
                    name=f"Year {year_val}",
                    value=f"{participant_count} participants{extra_info}",
                    inline=True
                )

            await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="entropy", description="Check current entropy and system info")
    @mod_only()
    async def ss_entropy(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        hardware_entropy = _get_enhanced_entropy()

        embed = disnake.Embed(title="System Entropy Information", color=disnake.Color.blue())

        embed.add_field(name="Enhanced Entropy", value=f"{hardware_entropy:.6f}", inline=True)
        embed.add_field(name="Current Time", value=dt.datetime.now().isoformat(), inline=False)

        await inter.edit_original_response(embed=embed)

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
                        event["participants"].pop(user_id_str)
                        await self._save()

                        await self._send_dm(
                            payload.user_id,
                            "❌ You've been removed from Secret Santa because you removed your reaction. "
                            "If this was a mistake, please react to the announcement message again to rejoin!"
                        )

        except Exception as e:
            pass

    def cog_unload(self):
        if self._backup_task:
            self._backup_task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(SecretSantaCog(bot))