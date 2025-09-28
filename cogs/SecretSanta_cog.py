from __future__ import annotations
import aiohttp
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
# Use only ARCHIVE_DIR, remove HISTORY_DIR completely
HISTORY_DIR = ARCHIVE_DIR

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
                content = path.read_text().strip()
                if not content:  # Handle empty files
                    print(f"Warning: {path} is empty, using default state")
                    continue
                return json.loads(content)
            except json.JSONDecodeError as e:
                print(f"Warning: {path} contains invalid JSON: {e}, trying backup")
                continue
    return {"pair_history": {}, "current_year": dt.date.today().year, "current_event": None}


def _save_state(state: Dict[str, Any]) -> None:
    temp_path = STATE_PATH.with_suffix('.tmp')
    temp_path.write_text(json.dumps(state, indent=2))
    temp_path.replace(STATE_PATH)


def _load_questions() -> Dict[str, Any]:
    if QUESTION_ARCHIVE_PATH.exists():
        try:
            content = QUESTION_ARCHIVE_PATH.read_text().strip()
            if not content:  # Handle empty files
                return {"questions": {}}
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"Warning: {QUESTION_ARCHIVE_PATH} contains invalid JSON: {e}, using default")
    return {"questions": {}}


def _save_questions(questions: Dict[str, Any]) -> None:
    temp_path = QUESTION_ARCHIVE_PATH.with_suffix('.tmp')
    temp_path.write_text(json.dumps(questions, indent=2))
    temp_path.replace(QUESTION_ARCHIVE_PATH)


def _load_historical_assignments() -> Dict[str, Dict[str, Any]]:
    """Load all historical assignments from archive directory"""
    historical_data = {}

    for history_file in ARCHIVE_DIR.glob("*.json"):
        try:
            year = history_file.stem
            # Only load year files (not event files)
            if not year.isdigit() or history_file.name.startswith('event_'):
                continue

            content = history_file.read_text().strip()
            if not content:  # Skip empty files
                continue

            data = json.loads(content)
            historical_data[year] = data
        except (json.JSONDecodeError, FileNotFoundError):
            continue

    return historical_data


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
    """Load historical assignments from archived events - FIXED VERSION"""
    history = {}

    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Handle different archive formats
                assignments = None

                # Format 1: New format with event wrapper
                if "event" in data and "assignments" in data["event"]:
                    assignments = data["event"]["assignments"]
                # Format 2: Direct assignments in event file
                elif "assignments" in data:
                    assignments = data["assignments"]
                else:
                    continue

                # Handle both dictionary and list formats
                if isinstance(assignments, dict):
                    # Dictionary format: {giver_id: receiver_id}
                    for giver_str, receiver_id in assignments.items():
                        if giver_str not in history:
                            history[giver_str] = []
                        history[giver_str].append(int(receiver_id))
                elif isinstance(assignments, list):
                    # List format: [{giver_id: ..., receiver_id: ...}]
                    for assignment in assignments:
                        # Skip special assignments with multiple givers
                        if "giver_ids" in assignment:
                            continue

                        if "giver_id" in assignment and "receiver_id" in assignment:
                            giver_str = str(assignment["giver_id"])
                            receiver_id = assignment["receiver_id"]

                            if giver_str not in history:
                                history[giver_str] = []
                            history[giver_str].append(int(receiver_id))

        except (json.JSONDecodeError, FileNotFoundError, KeyError, TypeError, ValueError) as e:
            continue

    return history


def _get_archived_events() -> List[Dict[str, Any]]:
    """Get all archived events with detailed information - FIXED VERSION"""
    events = []

    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            with open(archive_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Extract year from filename as fallback
                filename_year = archive_file.stem.replace("event_", "")
                if filename_year.isdigit():
                    data["filename_year"] = int(filename_year)

                events.append(data)
        except (json.JSONDecodeError, FileNotFoundError):
            continue

    return sorted(events, key=lambda x: x.get("year", x.get("filename_year", 0)), reverse=True)


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


def _save_historical_gift(year: int, giver_id: str, giver_name: str, receiver_id: str, receiver_name: str,
                          gift_description: str):
    """Save gift submission to historical records"""
    year_file = ARCHIVE_DIR / f"{year}.json"

    try:
        if year_file.exists():
            with open(year_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"year": year, "assignments": {}}

        assignment_key = f"{giver_id}_{receiver_id}"
        data["assignments"][assignment_key] = {
            "giver_id": giver_id,
            "giver_name": giver_name,
            "receiver_id": receiver_id,
            "receiver_name": receiver_name,
            "gift": gift_description,
            "submitted_at": time.time(),
            "timestamp": dt.datetime.now().isoformat()
        }

        with open(year_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    except Exception as e:
        print(f"Error saving historical gift: {e}")


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


# Add to SecretSantaCog __init__ method:
class SecretSantaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()
        self.questions = _load_questions()
        self.historical_data = _load_historical_assignments()
        self._lock = asyncio.Lock()
        self._backup_task = None

        # Add HTTP session for API calls if needed
        self.http_session = None
        self.session_lock = asyncio.Lock()

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
            # Use Discord mention format which shows as a blue link with user popup
            receiver_mention = f"<@{receiver_id}>"
            message = f"🎅 You are Secret Santa for {receiver_mention} ({receiver_name}) this year!\n\n💬 **You can ask anonymous questions to your giftee using `/ss ask_giftee`!**\n📨 **Your giftee can reply using `/ss reply_santa`!**\n\n📝 **After you've gifted**, use `/ss submit_gift` to record what you gave for the historical archives!"
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

    async def cog_unload(self):
        """Enhanced cleanup"""
        if self._backup_task:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass

        # Close HTTP session if it exists
        if hasattr(self, 'http_session') and self.http_session and not self.http_session.closed:
            try:
                await self.http_session.close()
            except Exception as e:
                print(f"Error closing HTTP session: {e}")

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

        # Check if we should start a new year
        current_year = dt.date.today().year
        if current_year != self.state.get("current_year", current_year):
            self.state["current_year"] = current_year

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
            "guild_id": inter.guild.id,
            "gift_submissions": {},  # New field for gift submissions
            "communications": {}  # Track ongoing communications
        }

        async with self._lock:
            self.state["current_event"] = new_event
            await self._save()

        confirmation_tasks = []
        for user_id_str in participants:
            user_id = int(user_id_str)
            confirmation_tasks.append(
                self._send_dm(user_id,
                              f"✅ You've joined Secret Santa {current_year}! 🎁\n\nReact to the announcement message to join or leave the event.")
            )
            await asyncio.sleep(0.5)

        results = await asyncio.gather(*confirmation_tasks, return_exceptions=True)

        successful_dms = sum(1 for result in results if result is True)
        failed_dms = len(results) - successful_dms

        response_msg = (
            f"✅ Secret Santa {current_year} started!\n"
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

    # Add session management methods
    async def _get_session(self):
        """Get or create HTTP session"""
        async with self.session_lock:
            if self.http_session is None or self.http_session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                self.http_session = aiohttp.ClientSession(timeout=timeout)
            return self.http_session


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
            "🎅 Ho ho ho! You've been assigned to gift {receiver_mention} this year!\n\n💬 **You can ask anonymous questions to your giftee using `/ss ask_giftee`!**\n📨 **Your giftee can reply using `/ss reply_santa`!**\n\n📝 **After you've gifted**, use `/ss submit_gift` to record what you gave for the historical archives!",
            "🎄 The elves have spoken! You're gifting {receiver_mention} this Christmas!\n\n💬 **You can ask anonymous questions to your giftee using `/ss ask_giftee`!**\n📨 **Your giftee can reply using `/ss reply_santa`!**\n\n📝 **After you've gifted**, use `/ss submit_gift` to record what you gave for the historical archives!",
            "✨ The magic of Christmas pairs you with {receiver_mention}!\n\n💬 **You can ask anonymous questions to your giftee using `/ss ask_giftee`!**\n📨 **Your giftee can reply using `/ss reply_santa`!**\n\n📝 **After you've gifted**, use `/ss submit_gift` to record what you gave for the historical archives!",
            "🦌 Rudolph's nose glows for {receiver_mention}! You're their Secret Santa!\n\n💬 **You can ask anonymous questions to your giftee using `/ss ask_giftee`!**\n📨 **Your giftee can reply using `/ss reply_santa`!**\n\n📝 **After you've gifted**, use `/ss submit_gift` to record what you gave for the historical archives!"
        ]

        dm_tasks = []
        for giver, receiver in assigns.items():
            receiver_name = event["participants"].get(str(receiver), f"User {receiver}")
            receiver_mention = f"<@{receiver}>"
            message = random.choice(festive_messages).format(receiver_mention=receiver_mention)
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

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee an anonymous question")
    @participant_only()
    async def ss_ask_giftee(self, inter: disnake.AppCmdInter, question: str):
        """Ask your giftee a question anonymously"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        user_id = str(inter.author.id)

        # Check if user has an assignment (is a Santa)
        if user_id not in event.get("assignments", {}):
            await inter.edit_original_response("❌ You don't have a Secret Santa assignment yet!")
            return

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")

        # Send the question to the giftee
        try:
            receiver_user = await self.bot.fetch_user(receiver_id)
            await receiver_user.send(
                f"🎅 **Anonymous question from your Secret Santa:**\n\n{question}\n\n"
                f"💬 *You can reply using `/ss reply_santa`*"
            )

            # Store the communication thread
            async with self._lock:
                if "communications" not in event:
                    event["communications"] = {}
                if user_id not in event["communications"]:
                    event["communications"][user_id] = {"giftee_id": receiver_id, "thread": []}
                event["communications"][user_id]["thread"].append({
                    "type": "question",
                    "message": question,
                    "timestamp": time.time()
                })
                await self._save()

            await inter.edit_original_response(f"✅ Your anonymous question has been sent to your giftee!")
        except Exception as e:
            await inter.edit_original_response("❌ Failed to send question. Your giftee might have DMs disabled.")

    @ss_root.sub_command(name="reply_santa", description="Reply to your Secret Santa's question")
    @participant_only()
    async def ss_reply_santa(self, inter: disnake.AppCmdInter, reply: str):
        """Reply to your Santa's question anonymously"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        user_id = str(inter.author.id)

        # Find who is the Santa for this user (reverse lookup)
        santa_id = None
        for santa, giftee in event.get("assignments", {}).items():
            if giftee == int(user_id):
                santa_id = santa
                break

        if not santa_id:
            await inter.edit_original_response("❌ No Santa has asked you a question yet!")
            return

        # Send the reply to the Santa
        try:
            santa_user = await self.bot.fetch_user(int(santa_id))
            await santa_user.send(
                f"📨 **Anonymous reply from your giftee:**\n\n{reply}\n\n"
                f"💬 *You can ask more questions using `/ss ask_giftee`*"
            )

            # Store the communication thread
            async with self._lock:
                if "communications" not in event:
                    event["communications"] = {}
                if santa_id not in event["communications"]:
                    event["communications"][santa_id] = {"giftee_id": user_id, "thread": []}
                event["communications"][santa_id]["thread"].append({
                    "type": "reply",
                    "message": reply,
                    "timestamp": time.time()
                })
                await self._save()

            await inter.edit_original_response(f"✅ Your anonymous reply has been sent to your Secret Santa!")
        except Exception as e:
            await inter.edit_original_response("❌ Failed to send reply. Your Santa might have DMs disabled.")

    @ss_root.sub_command(name="submit_gift", description="Submit your gift description for historical records")
    @participant_only()
    async def ss_submit_gift(self, inter: disnake.AppCmdInter, gift_description: str):
        """Submit your gift description to be recorded in the historical archives"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        user_id = str(inter.author.id)

        # Check if user has an assignment
        if user_id not in event.get("assignments", {}):
            await inter.edit_original_response("❌ You don't have a Secret Santa assignment yet!")
            return

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")
        giver_name = event["participants"].get(user_id, f"User {user_id}")

        # Save to historical records
        _save_historical_gift(
            self.state["current_year"],
            user_id,
            giver_name,
            str(receiver_id),
            receiver_name,
            gift_description
        )

        # Also save to current event for moderation purposes
        async with self._lock:
            if "gift_submissions" not in event:
                event["gift_submissions"] = {}
            event["gift_submissions"][user_id] = {
                "gift": gift_description,
                "submitted_at": time.time(),
                "timestamp": dt.datetime.now().isoformat(),
                "receiver_id": receiver_id,
                "receiver_name": receiver_name
            }
            await self._save()

        await inter.edit_original_response(
            f"✅ Gift submitted successfully!\n\n"
            f"**Your Gift for {receiver_name}:**\n"
            f"{gift_description}\n\n"
            f"This has been recorded in the Secret Santa {self.state['current_year']} archives!"
        )

    @ss_root.sub_command(name="view_gifts", description="View submitted gifts for this year")
    @mod_only()
    async def ss_view_gifts(self, inter: disnake.AppCmdInter):
        """View all gift submissions for the current year"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        submissions = event.get("gift_submissions", {})
        if not submissions:
            await inter.edit_original_response("❌ No gifts have been submitted yet.")
            return

        embed = disnake.Embed(
            title=f"Secret Santa {self.state['current_year']} - Gift Submissions",
            color=disnake.Color.green()
        )

        for submitter_id, submission in submissions.items():
            submitter_name = event["participants"].get(submitter_id, f"User {submitter_id}")
            receiver_name = submission.get("receiver_name", "Unknown")

            gift_text = submission["gift"]
            if len(gift_text) > 500:
                gift_text = gift_text[:497] + "..."

            embed.add_field(
                name=f"🎁 {submitter_name} → {receiver_name}",
                value=gift_text,
                inline=False
            )

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_communications", description="View communication threads (mod only)")
    @mod_only()
    async def ss_view_communications(self, inter: disnake.AppCmdInter):
        """View all communication threads between Santas and giftees"""
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active", False):
            await inter.edit_original_response("❌ No active Secret Santa event")
            return

        communications = event.get("communications", {})
        if not communications:
            await inter.edit_original_response("❌ No communications have occurred yet.")
            return

        embed = disnake.Embed(
            title=f"Secret Santa {self.state['current_year']} - Communications",
            color=disnake.Color.blue()
        )

        for santa_id, comm_data in communications.items():
            santa_name = event["participants"].get(santa_id, f"User {santa_id}")
            giftee_id = comm_data.get("giftee_id")
            giftee_name = event["participants"].get(str(giftee_id), f"User {giftee_id}")

            thread_text = ""
            for msg in comm_data.get("thread", [])[:5]:  # Show last 5 messages
                msg_type = "🎅 Santa" if msg["type"] == "question" else "📨 Giftee"
                timestamp = dt.datetime.fromtimestamp(msg["timestamp"]).strftime("%m/%d %H:%M")
                thread_text += f"{msg_type} ({timestamp}): {msg['message'][:100]}...\n"

            if not thread_text:
                thread_text = "No messages yet"

            embed.add_field(
                name=f"💬 {santa_name} → {giftee_name}",
                value=thread_text,
                inline=False
            )

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="history", description="Show previous Secret Santa events")
    async def ss_history(self, inter: disnake.AppCmdInter, year: int = None):
        await inter.response.defer(ephemeral=True)

        # Load both archived events and historical gift data
        events = _get_archived_events()
        historical_data = _load_historical_assignments()

        if not events and not historical_data:
            await inter.edit_original_response("No archived events found.")
            return

        if year:
            # Try to find event data
            event_data = next((e for e in events if e.get("year") == year or e.get("filename_year") == year), None)
            historical_year_data = historical_data.get(str(year))

            if not event_data and not historical_year_data:
                await inter.edit_original_response(f"No event found for year {year}")
                return

            embed = disnake.Embed(title=f"Secret Santa {year}", color=disnake.Color.gold())

            # Add event details if available
            participant_count = 0
            if event_data:
                # Handle different event formats
                if "event" in event_data and "participants" in event_data["event"]:
                    participant_count = len(event_data["event"]["participants"])
                elif "assignments" in event_data and isinstance(event_data["assignments"], list):
                    # Count unique participants from assignments list
                    participants_set = set()
                    for assignment in event_data["assignments"]:
                        if "giver_id" in assignment:
                            participants_set.add(assignment["giver_id"])
                        if "receiver_id" in assignment:
                            participants_set.add(assignment["receiver_id"])
                    participant_count = len(participants_set)

                if participant_count > 0:
                    embed.add_field(name="Participants", value=str(participant_count), inline=True)

            # Enhanced gift display with special formatting
            gifts_text = ""

            # First try historical data
            if historical_year_data and "assignments" in historical_year_data:
                assignments = historical_year_data["assignments"]
                if isinstance(assignments, dict):
                    # Convert dictionary format to list for consistent processing
                    assignment_list = []
                    for key, data in assignments.items():
                        if isinstance(data, dict) and "giver_id" in data and "receiver_id" in data:
                            assignment_list.append(data)
                    assignments = assignment_list

                if assignments:
                    normal_gifts = []
                    server_gifts = []

                    for assignment in assignments:
                        if "giver_ids" in assignment:
                            # Server-wide gift (multiple givers)
                            giver_mentions = " & ".join([f"<@{gid}>" for gid in assignment["giver_ids"]])
                            receiver_name = assignment.get("receiver_name", "Entire Server")
                            server_gifts.append(f"🎊 **{giver_mentions}** → 🏰 **{receiver_name}**: {assignment['gift']}")
                        elif "giver_id" in assignment and "receiver_id" in assignment and "gift" in assignment:
                            # Normal one-to-one gift
                            giver_mention = f"<@{assignment['giver_id']}>"
                            receiver_mention = f"<@{assignment['receiver_id']}>"
                            normal_gifts.append(f"🎁 {giver_mention} → {receiver_mention}: {assignment['gift']}")

                    # Combine gifts with server gifts first
                    if server_gifts:
                        gifts_text += "**🎊 Server-Wide Gifts**\n" + "\n".join(server_gifts) + "\n\n"
                    if normal_gifts:
                        gifts_text += "**🎁 Individual Gifts**\n" + "\n".join(normal_gifts)

            # If no gifts from historical data, try event data
            elif event_data and "assignments" in event_data:
                assignments = event_data["assignments"]
                if isinstance(assignments, list):
                    normal_gifts = []
                    server_gifts = []

                    for assignment in assignments:
                        if "giver_ids" in assignment:
                            # Server-wide gift
                            giver_mentions = " & ".join([f"<@{gid}>" for gid in assignment["giver_ids"]])
                            receiver_name = assignment.get("receiver_name", "Entire Server")
                            server_gifts.append(f"🎊 **{giver_mentions}** → 🏰 **{receiver_name}**: {assignment['gift']}")
                        elif "giver_id" in assignment and "receiver_id" in assignment and "gift" in assignment:
                            # Normal gift
                            giver_mention = f"<@{assignment['giver_id']}>"
                            receiver_mention = f"<@{assignment['receiver_id']}>"
                            normal_gifts.append(f"🎁 {giver_mention} → {receiver_mention}: {assignment['gift']}")

                    # Combine gifts with server gifts first
                    if server_gifts:
                        gifts_text += "**🎊 Server-Wide Gifts**\n" + "\n".join(server_gifts) + "\n\n"
                    if normal_gifts:
                        gifts_text += "**🎁 Individual Gifts**\n" + "\n".join(normal_gifts)

            # Add gifts to embed if we have any
            if gifts_text:
                if len(gifts_text) > 1024:
                    gifts_text = gifts_text[:1020] + "..."
                embed.add_field(name="Gift Assignments", value=gifts_text, inline=False)
            else:
                embed.add_field(name="Gift Assignments", value="No gifts recorded for this year.", inline=False)

            await inter.edit_original_response(embed=embed)

        else:
            # List all years
            embed = disnake.Embed(title="Secret Santa History", color=disnake.Color.blue())

            # Combine events from both sources
            all_years = set()
            if events:
                for event in events:
                    year_val = event.get("year") or event.get("filename_year")
                    if year_val:
                        all_years.add(year_val)
            if historical_data:
                all_years.update([int(y) for y in historical_data.keys() if y.isdigit()])

            for year_val in sorted(all_years, reverse=True):
                year_str = str(year_val)

                # Get participant count from events
                participant_count = 0
                event_for_year = next(
                    (e for e in events if e.get("year") == year_val or e.get("filename_year") == year_val), None)
                if event_for_year:
                    if "event" in event_for_year and "participants" in event_for_year["event"]:
                        participant_count = len(event_for_year["event"].get("participants", {}))
                    elif "assignments" in event_for_year and isinstance(event_for_year["assignments"], list):
                        participants_set = set()
                        for assignment in event_for_year["assignments"]:
                            if "giver_id" in assignment:
                                participants_set.add(assignment["giver_id"])
                            if "receiver_id" in assignment:
                                participants_set.add(assignment["receiver_id"])
                        participant_count = len(participants_set)

                # Get gift count from historical data
                gift_count = 0
                if year_str in historical_data and "assignments" in historical_data[year_str]:
                    gift_count = len(historical_data[year_str]["assignments"])
                elif event_for_year and "assignments" in event_for_year:
                    if isinstance(event_for_year["assignments"], list):
                        gift_count = len([a for a in event_for_year["assignments"] if "gift" in a])

                embed.add_field(
                    name=f"Year {year_val}",
                    value=f"👥 {participant_count} participants | 🎁 {gift_count} gifts recorded",
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
        embed.add_field(name="Current Year", value=str(self.state["current_year"]), inline=True)

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

        await self._send_dm(payload.user_id,
                            f"✅ You've joined Secret Santa {self.state['current_year']}! 🎁\n\nYou'll receive your assignment when the event starts!")

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