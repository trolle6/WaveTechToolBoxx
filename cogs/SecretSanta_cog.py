from __future__ import annotations
import aiohttp
import asyncio
import datetime as dt
import json
import pathlib
import random
import time
import os
from typing import Any, Dict, List, Optional
import disnake
from disnake.ext import commands

# Path configuration
ROOT_DIR = pathlib.Path(__file__).parent
STATE_PATH = ROOT_DIR / "secret_santa_state.json"
BACKUP_PATH = ROOT_DIR / "secret_santa_state.bak"
ARCHIVE_DIR = ROOT_DIR / "archive"
ARCHIVE_DIR.mkdir(exist_ok=True)
QUESTION_ARCHIVE_PATH = ROOT_DIR / "santa_questions.json"


# Utility functions optimized for efficiency
def _get_enhanced_entropy() -> float:
    """Simplified yet effective entropy generation"""
    return (hash((os.urandom(8), time.time_ns(), os.getpid(), random.random())) % 1000000) / 1000000.0


def _load_json_file(path: pathlib.Path, default: Any) -> Any:
    """Generic JSON file loader with error handling"""
    if path.exists():
        try:
            content = path.read_text().strip()
            return json.loads(content) if content else default
        except json.JSONDecodeError:
            pass
    return default


def _save_json_file(path: pathlib.Path, data: Any) -> None:
    """Safe JSON file saving with atomic write"""
    temp_path = path.with_suffix('.tmp')
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    temp_path.replace(path)


def _load_state() -> Dict[str, Any]:
    return _load_json_file(STATE_PATH, _load_json_file(BACKUP_PATH,
                                                       {"pair_history": {}, "current_year": dt.date.today().year,
                                                        "current_event": None}))


def _save_state(state: Dict[str, Any]) -> None:
    _save_json_file(STATE_PATH, state)


def _load_questions() -> Dict[str, Any]:
    return _load_json_file(QUESTION_ARCHIVE_PATH, {"questions": {}})


def _save_questions(questions: Dict[str, Any]) -> None:
    _save_json_file(QUESTION_ARCHIVE_PATH, questions)


def _load_archived_events() -> List[Dict[str, Any]]:
    """Load all archived events efficiently"""
    events = []
    for archive_file in ARCHIVE_DIR.glob("event_*.json"):
        try:
            data = _load_json_file(archive_file, {})
            if data and (year := archive_file.stem.replace("event_", "")).isdigit():
                data["filename_year"] = int(year)
                events.append(data)
        except Exception:
            continue
    return sorted(events, key=lambda x: x.get("year", x.get("filename_year", 0)), reverse=True)


def _load_archived_history() -> Dict[str, List[int]]:
    """Load historical assignments efficiently"""
    history = {}
    for event_data in _load_archived_events():
        assignments = event_data.get("event", event_data).get("assignments", [])
        if isinstance(assignments, dict):
            for giver_str, receiver_id in assignments.items():
                history.setdefault(giver_str, []).append(int(receiver_id))
        elif isinstance(assignments, list):
            for assignment in assignments:
                if "giver_id" in assignment and "receiver_id" in assignment and "giver_ids" not in assignment:
                    history.setdefault(str(assignment["giver_id"]), []).append(assignment["receiver_id"])
    return history


def _make_assignments(participants: List[int], pair_history: Dict[str, List[int]]) -> Dict[int, int]:
    """Optimized assignment algorithm"""
    if len(participants) < 2:
        raise ValueError("Need at least two participants for Secret Santa.")

    random.seed(time.time() + _get_enhanced_entropy() + random.random())
    givers, receivers = participants.copy(), participants.copy()
    random.shuffle(givers), random.shuffle(receivers)

    for attempt in range(1000):
        givers_attempt, receivers_attempt, assigns_attempt = givers.copy(), receivers.copy(), {}
        valid = True

        for giver in givers_attempt:
            valid_receivers = [r for r in receivers_attempt if r != giver and r not in pair_history.get(str(giver), [])]
            if not valid_receivers:
                valid = False
                break
            receiver = random.choice(valid_receivers)
            assigns_attempt[giver] = receiver
            receivers_attempt.remove(receiver)  # Fixed line 112

        if valid:
            for giver, receiver in assigns_attempt.items():
                pair_history.setdefault(str(giver), []).append(receiver)
            return assigns_attempt

        random.seed(time.time() + random.random() + attempt + _get_enhanced_entropy())

    # Fallback: simple rotation
    random.shuffle(givers), random.shuffle(receivers)
    return {giver: receivers[(i + 1) % len(receivers)] for i, giver in enumerate(givers)}


def _archive_current_year(event_data: Dict[str, Any], year: int) -> None:
    """Archive current event data"""
    archive_data = {
        "year": year, "event": event_data.copy(), "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat(),
        "shuffle_entropy": {"hardware_entropy": _get_enhanced_entropy(), "timestamp": time.time()}
    }
    _save_json_file(ARCHIVE_DIR / f"event_{year}.json", archive_data)


def _save_historical_gift(year: int, giver_id: str, giver_name: str, receiver_id: str,
                          receiver_name: str, gift_description: str) -> None:
    """Save gift to historical records"""
    year_file = ARCHIVE_DIR / f"{year}.json"
    data = _load_json_file(year_file, {"year": year, "assignments": {}})

    assignment_key = f"{giver_id}_{receiver_id}"
    data["assignments"][assignment_key] = {
        "giver_id": giver_id, "giver_name": giver_name, "receiver_id": receiver_id,
        "receiver_name": receiver_name, "gift": gift_description,
        "submitted_at": time.time(), "timestamp": dt.datetime.now().isoformat()
    }
    _save_json_file(year_file, data)


# Command decorators optimized
def mod_only():
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'discord'):
                mod_role_id = inter.bot.config.discord.moderator_role_id
                if mod_role_id and any(r.id == mod_role_id for r in inter.author.roles):
                    return True
            return inter.author.guild_permissions.administrator
        except Exception:
            return inter.author.guild_permissions.administrator

    return commands.check(predicate)


def participant_only():
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            cog = inter.bot.get_cog("SecretSantaCog")
            event = cog._event() if cog else None
            return bool(event and event.get("active") and str(inter.author.id) in event["participants"])
        except Exception:
            return False

    return commands.check(predicate)


# Main Secret Santa Cog - Optimized and streamlined
class SecretSantaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = _load_state()
        self.questions = _load_questions()
        self._lock, self._backup_task = asyncio.Lock(), None
        self.http_session, self.session_lock = None, asyncio.Lock()

    def _event(self) -> Optional[Dict[str, Any]]:
        return self.state.get("current_event")

    async def _save(self):
        _save_state(self.state)

    async def _send_dm(self, user_id: int, message: str, max_attempts: int = 3) -> bool:
        for attempt in range(max_attempts):
            try:
                user = await self.bot.fetch_user(user_id)
                await user.send(message)
                return True
            except Exception:
                if attempt == max_attempts - 1:
                    break
                await asyncio.sleep(1)
        return False

    async def _dm_assignment(self, giver_id: int, receiver_id: int, message: str = None):
        if not message:
            receiver_name = self._event()["participants"].get(str(receiver_id), f"User {receiver_id}")
            message = (f"🎅 You are Secret Santa for <@{receiver_id}> ({receiver_name}) this year!\n\n"
                       f"💬 **Ask questions with `/ss ask_giftee`!**\n📨 **Your giftee can reply with `/ss reply_santa`!**\n\n"
                       f"📝 **After gifting, use `/ss submit_gift` to record it!**")
        await self._send_dm(giver_id, message)

    async def _assign_role_to_participants(self, guild: disnake.Guild, role_id: int, participant_ids: List[int]):
        role = guild.get_role(role_id)
        if not role or not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position:
            return False

        success, failed = 0, 0
        for user_id in participant_ids:
            try:
                member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                if role not in member.roles:
                    await member.add_roles(role, reason="Secret Santa participant")
                    success += 1
            except Exception:
                failed += 1
        return {"success": success, "failed": failed}

    async def cog_load(self):
        self._backup_task = asyncio.create_task(self._start_backup_task())

    async def cog_unload(self):
        if self._backup_task:
            self._backup_task.cancel()
            try:
                await self._backup_task
            except asyncio.CancelledError:
                pass
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    async def _start_backup_task(self):
        try:
            while True:
                await asyncio.sleep(3600)
                async with self._lock:
                    _save_state(self.state)
                    _save_questions(self.questions)
                    BACKUP_PATH.write_text(json.dumps(self.state, indent=2))
        except (asyncio.CancelledError, Exception):
            pass

    async def _get_session(self):
        async with self.session_lock:
            if self.http_session is None or self.http_session.closed:
                self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
            return self.http_session

    # Command structure - All commands preserved exactly
    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.AppCmdInter):
        pass

    @ss_root.sub_command(name="participants", description="View current participants")
    @mod_only()
    async def ss_participants(self, inter: disnake.AppCmdInter):
        event = self._event()
        if not event or not event.get("active"):
            return await inter.send("❌ No active Secret Santa event", ephemeral=True)

        participants = event["participants"]
        if not participants:
            return await inter.send("❌ No participants yet", ephemeral=True)

        participant_list = "\n".join(f"{name} (ID: {uid})" for uid, name in participants.items())
        await inter.send(f"**Current Participants ({len(participants)}):**\n{participant_list}", ephemeral=True)

    @ss_root.sub_command(name="start", description="Start a new Secret Santa event")
    @mod_only()
    async def ss_start(self, inter: disnake.AppCmdInter, announcement_message_id: str, role_id: str):
        await inter.response.defer(ephemeral=True)

        try:
            message_id, role_id_int = int(announcement_message_id), int(role_id)
        except ValueError:
            return await inter.edit_original_response("❌ Invalid message ID or role ID.")

        if self._event() and self._event().get("active"):
            return await inter.edit_original_response("❌ There's already an active Secret Santa event")

        current_year = dt.date.today().year
        if current_year != self.state.get("current_year", current_year):
            self.state["current_year"] = current_year

        participants, message_channel_id = {}, "UNKNOWN"
        for channel in inter.guild.text_channels:
            try:
                message = await channel.fetch_message(message_id)
                message_channel_id = channel.id
                for reaction in message.reactions:
                    async for user in reaction.users():
                        if user.id != self.bot.user.id and str(user.id) not in participants:
                            member = inter.guild.get_member(user.id)
                            participants[str(user.id)] = member.display_name if member else user.name
                break
            except (disnake.NotFound, disnake.Forbidden):
                continue

        new_event = {
            "active": True, "join_closed": False, "announcement_message_id": message_id,
            "role_id": role_id_int, "participants": participants, "assignments": {},
            "guild_id": inter.guild.id, "gift_submissions": {}, "communications": {}
        }

        async with self._lock:
            self.state["current_event"] = new_event
            await self._save()

        confirmation_tasks = [self._send_dm(int(uid),
                                            f"✅ You've joined Secret Santa {current_year}! 🎁\n\nReact to the announcement to join/leave.")
                              for uid in participants]
        await asyncio.sleep(0.5)

        results = await asyncio.gather(*confirmation_tasks, return_exceptions=True)
        successful_dms = sum(1 for r in results if r is True)

        response_msg = (f"✅ Secret Santa {current_year} started!\nRole ID: {role_id_int}\n"
                        f"Participants can react to [this message](https://discord.com/channels/{inter.guild.id}/{message_channel_id}/{message_id}) to join.\n"
                        f"Found {len(participants)} participants. Sent DMs to {successful_dms}.")

        if len(results) - successful_dms > 0:
            response_msg += f"\n⚠️ Failed to send DMs to {len(results) - successful_dms} participants."

        await inter.edit_original_response(response_msg)

    @ss_root.sub_command(name="stop", description="Stop the current Secret Santa event without assigning")
    @mod_only()
    async def ss_stop(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active"):
            return await inter.edit_original_response("❌ No active Secret Santa event")

        _archive_current_year(event, self.state["current_year"])

        async with self._lock:
            for participant in map(int, event["participants"].keys()):
                self.state["pair_history"].setdefault(str(participant), [])
            self.state["current_event"] = None
            await self._save()

        await inter.edit_original_response("✅ Secret Santa event stopped without assignments. Event data archived.")

    @ss_root.sub_command(name="shuffle", description="Manually trigger assignment (mod only)")
    @mod_only()
    async def ss_shuffle(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active"):
            return await inter.edit_original_response("❌ No active Secret Santa event")

        participants = list(map(int, event["participants"].keys()))
        if len(participants) < 2:
            return await inter.edit_original_response("❌ Not enough participants to make assignments")

        hardware_entropy = _get_enhanced_entropy()
        await inter.edit_original_response(
            f"🎲 Using enhanced entropy ({hardware_entropy:.6f}) for optimal randomness...")

        combined_history = self.state.get("pair_history", {}).copy()
        archived_history = _load_archived_history()

        for giver, receivers in archived_history.items():
            combined_history.setdefault(giver, []).extend(receivers)

        try:
            assigns = _make_assignments(participants, combined_history)
        except ValueError as e:
            return await inter.edit_original_response(f"❌ {e}")

        role_result = await self._assign_role_to_participants(inter.guild, event["role_id"], participants)

        festive_messages = [
            "🎅 Ho ho ho! You've been assigned to gift {receiver_mention} this year!\n\n💬 **Ask questions with `/ss ask_giftee`!**\n📨 **Your giftee can reply with `/ss reply_santa`!**\n\n📝 **After gifting, use `/ss submit_gift`!**",
            "🎄 The elves have spoken! You're gifting {receiver_mention} this Christmas!\n\n💬 **Ask questions with `/ss ask_giftee`!**\n📨 **Your giftee can reply with `/ss reply_santa`!**\n\n📝 **After gifting, use `/ss submit_gift`!**",
            "✨ The magic of Christmas pairs you with {receiver_mention}!\n\n💬 **Ask questions with `/ss ask_giftee`!**\n📨 **Your giftee can reply with `/ss reply_santa`!**\n\n📝 **After gifting, use `/ss submit_gift`!**",
            "🦌 Rudolph's nose glows for {receiver_mention}! You're their Secret Santa!\n\n💬 **Ask questions with `/ss ask_giftee`!**\n📨 **Your giftee can reply with `/ss reply_santa`!**\n\n📝 **After gifting, use `/ss submit_gift`!**"
        ]

        dm_tasks = [self._dm_assignment(giver, receiver,
                                        random.choice(festive_messages).format(receiver_mention=f"<@{receiver}>"))
                    for giver, receiver in assigns.items()]

        await asyncio.gather(*dm_tasks)

        async with self._lock:
            event["assignments"] = {str(k): v for k, v in assigns.items()}
            event["join_closed"] = True
            for giver, receiver in assigns.items():
                self.state["pair_history"].setdefault(str(giver), []).append(receiver)
            await self._save()

        response_message = f"✅ Assignments shuffled and sent! 🎲 Entropy: {hardware_entropy:.6f}"
        if role_result:
            response_message += f"\n✅ Role assigned to {role_result['success']} participants."
            if role_result['failed'] > 0:
                response_message += f"\n⚠️ Failed to assign role to {role_result['failed']} participants."
        else:
            response_message += "\n⚠️ Could not assign role to participants."

        await inter.edit_original_response(response_message)

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee an anonymous question")
    @participant_only()
    async def ss_ask_giftee(self, inter: disnake.AppCmdInter, question: str):
        await inter.response.defer(ephemeral=True)

        event, user_id = self._event(), str(inter.author.id)
        if not event or user_id not in event.get("assignments", {}):
            return await inter.edit_original_response("❌ You don't have a Secret Santa assignment yet!")

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")

        try:
            receiver_user = await self.bot.fetch_user(receiver_id)
            await receiver_user.send(
                f"🎅 **Anonymous question from your Secret Santa:**\n\n{question}\n\n💬 *Reply with `/ss reply_santa`*")

            async with self._lock:
                event.setdefault("communications", {}).setdefault(user_id, {"giftee_id": receiver_id, "thread": []})
                event["communications"][user_id]["thread"].append({
                    "type": "question", "message": question, "timestamp": time.time()
                })
                await self._save()

            await inter.edit_original_response("✅ Your anonymous question has been sent to your giftee!")
        except Exception:
            await inter.edit_original_response("❌ Failed to send question. Your giftee might have DMs disabled.")

    @ss_root.sub_command(name="reply_santa", description="Reply to your Secret Santa's question")
    @participant_only()
    async def ss_reply_santa(self, inter: disnake.AppCmdInter, reply: str):
        await inter.response.defer(ephemeral=True)

        event, user_id = self._event(), str(inter.author.id)
        if not event:
            return await inter.edit_original_response("❌ No active Secret Santa event")

        santa_id = next((santa for santa, giftee in event.get("assignments", {}).items()
                         if giftee == int(user_id)), None)
        if not santa_id:
            return await inter.edit_original_response("❌ No Santa has asked you a question yet!")

        try:
            santa_user = await self.bot.fetch_user(int(santa_id))
            await santa_user.send(
                f"📨 **Anonymous reply from your giftee:**\n\n{reply}\n\n💬 *Ask more with `/ss ask_giftee`*")

            async with self._lock:
                event.setdefault("communications", {}).setdefault(santa_id, {"giftee_id": user_id, "thread": []})
                event["communications"][santa_id]["thread"].append({
                    "type": "reply", "message": reply, "timestamp": time.time()
                })
                await self._save()

            await inter.edit_original_response("✅ Your anonymous reply has been sent to your Secret Santa!")
        except Exception:
            await inter.edit_original_response("❌ Failed to send reply. Your Santa might have DMs disabled.")

    @ss_root.sub_command(name="submit_gift", description="Submit your gift description for historical records")
    @participant_only()
    async def ss_submit_gift(self, inter: disnake.AppCmdInter, gift_description: str):
        await inter.response.defer(ephemeral=True)

        event, user_id = self._event(), str(inter.author.id)
        if not event or user_id not in event.get("assignments", {}):
            return await inter.edit_original_response("❌ You don't have a Secret Santa assignment yet!")

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")
        giver_name = event["participants"].get(user_id, f"User {user_id}")

        _save_historical_gift(self.state["current_year"], user_id, giver_name, str(receiver_id), receiver_name,
                              gift_description)

        async with self._lock:
            event.setdefault("gift_submissions", {})[user_id] = {
                "gift": gift_description, "submitted_at": time.time(),
                "timestamp": dt.datetime.now().isoformat(), "receiver_id": receiver_id, "receiver_name": receiver_name
            }
            await self._save()

        await inter.edit_original_response(
            f"✅ Gift submitted successfully!\n\n**Your Gift for {receiver_name}:**\n{gift_description}\n\n"
            f"This has been recorded in the Secret Santa {self.state['current_year']} archives!")

    @ss_root.sub_command(name="view_gifts", description="View submitted gifts for this year")
    @mod_only()
    async def ss_view_gifts(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active"):
            return await inter.edit_original_response("❌ No active Secret Santa event")

        submissions = event.get("gift_submissions", {})
        if not submissions:
            return await inter.edit_original_response("❌ No gifts have been submitted yet.")

        embed = disnake.Embed(title=f"Secret Santa {self.state['current_year']} - Gift Submissions",
                              color=disnake.Color.green())
        for submitter_id, submission in submissions.items():
            submitter_name = event["participants"].get(submitter_id, f"User {submitter_id}")
            receiver_name = submission.get("receiver_name", "Unknown")
            gift_text = submission["gift"][:497] + "..." if len(submission["gift"]) > 500 else submission["gift"]
            embed.add_field(name=f"🎁 {submitter_name} → {receiver_name}", value=gift_text, inline=False)

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_communications", description="View communication threads (mod only)")
    @mod_only()
    async def ss_view_communications(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self._event()
        if not event or not event.get("active"):
            return await inter.edit_original_response("❌ No active Secret Santa event")

        communications = event.get("communications", {})
        if not communications:
            return await inter.edit_original_response("❌ No communications have occurred yet.")

        embed = disnake.Embed(title=f"Secret Santa {self.state['current_year']} - Communications",
                              color=disnake.Color.blue())
        for santa_id, comm_data in communications.items():
            santa_name = event["participants"].get(santa_id, f"User {santa_id}")
            giftee_id, giftee_name = comm_data.get("giftee_id"), event["participants"].get(
                str(comm_data.get("giftee_id")), "Unknown")

            thread_text = "".join(f"{'🎅 Santa' if msg['type'] == 'question' else '📨 Giftee'} "
                                  f"({dt.datetime.fromtimestamp(msg['timestamp']).strftime('%m/%d %H:%M')}): "
                                  f"{msg['message'][:100]}...\n" for msg in comm_data.get("thread", [])[:5])

            embed.add_field(name=f"💬 {santa_name} → {giftee_name}",
                            value=thread_text or "No messages yet", inline=False)

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="history", description="Show previous Secret Santa events")
    async def ss_history(self, inter: disnake.AppCmdInter, year: int = None):
        await inter.response.defer(ephemeral=True)

        events, historical_data = _load_archived_events(), _load_archived_history()
        if not events and not historical_data:
            return await inter.edit_original_response("No archived events found.")

        if year:
            event_data = next((e for e in events if e.get("year") == year or e.get("filename_year") == year), None)
            historical_year_data = historical_data.get(str(year))

            if not event_data and not historical_year_data:
                return await inter.edit_original_response(f"No event found for year {year}")

            embed = disnake.Embed(title=f"Secret Santa {year}", color=disnake.Color.gold())

            # Participant count
            participant_count = 0
            if event_data:
                event_obj = event_data.get("event", event_data)
                if "participants" in event_obj:
                    participant_count = len(event_obj["participants"])
                elif "assignments" in event_data and isinstance(event_data["assignments"], list):
                    participant_count = len({a.get("giver_id") for a in event_data["assignments"] if "giver_id" in a} |
                                            {a.get("receiver_id") for a in event_data["assignments"] if
                                             "receiver_id" in a})

            if participant_count > 0:
                embed.add_field(name="Participants", value=str(participant_count), inline=True)

            # Gift display
            gifts_text = self._format_gifts_for_year(year, event_data, historical_year_data)
            embed.add_field(name="Gift Assignments",
                            value=gifts_text[:1020] + "..." if len(gifts_text) > 1024 else gifts_text, inline=False)

            await inter.edit_original_response(embed=embed)
        else:
            embed = disnake.Embed(title="Secret Santa History", color=disnake.Color.blue())
            all_years = {e.get("year") or e.get("filename_year") for e in events if
                         e.get("year") or e.get("filename_year")} | \
                        {int(y) for y in historical_data.keys() if y.isdigit()}

            for year_val in sorted(all_years, reverse=True):
                participant_count, gift_count = self._get_year_stats(year_val, events, historical_data)
                embed.add_field(name=f"Year {year_val}",
                                value=f"👥 {participant_count} participants | 🎁 {gift_count} gifts recorded",
                                inline=True)

            await inter.edit_original_response(embed=embed)

    def _format_gifts_for_year(self, year: int, event_data: Optional[Dict],
                               historical_year_data: Optional[Dict]) -> str:
        """Helper to format gifts for history command"""
        gifts_text = ""
        assignments = []

        if historical_year_data and "assignments" in historical_year_data:
            assignments = [v for v in historical_year_data["assignments"].values() if isinstance(v, dict)] \
                if isinstance(historical_year_data["assignments"], dict) else historical_year_data["assignments"]
        elif event_data and "assignments" in event_data:
            assignments = event_data["assignments"]

        if assignments:
            server_gifts = [
                f"🎊 **{' & '.join(f'<@{gid}>' for gid in a['giver_ids'])}** → 🏰 **{a.get('receiver_name', 'Entire Server')}**: {a['gift']}"
                for a in assignments if "giver_ids" in a]
            normal_gifts = [f"🎁 <@{a['giver_id']}> → <@{a['receiver_id']}>: {a['gift']}"
                            for a in assignments if "giver_id" in a and "receiver_id" in a and "gift" in a]

            if server_gifts:
                gifts_text += "**🎊 Server-Wide Gifts**\n" + "\n".join(server_gifts) + "\n\n"
            if normal_gifts:
                gifts_text += "**🎁 Individual Gifts**\n" + "\n".join(normal_gifts)

        return gifts_text or "No gifts recorded for this year."

    def _get_year_stats(self, year_val: int, events: List[Dict], historical_data: Dict) -> tuple[int, int]:
        """Helper to get participant and gift counts for a year"""
        year_str = str(year_val)
        participant_count, gift_count = 0, 0

        event_for_year = next((e for e in events if e.get("year") == year_val or e.get("filename_year") == year_val),
                              None)
        if event_for_year:
            event_obj = event_for_year.get("event", event_for_year)
            if "participants" in event_obj:
                participant_count = len(event_obj["participants"])
            elif "assignments" in event_for_year and isinstance(event_for_year["assignments"], list):
                participant_count = len(
                    {a.get(k) for a in event_for_year["assignments"] for k in ("giver_id", "receiver_id") if a.get(k)})

        if year_str in historical_data and "assignments" in historical_data[year_str]:
            gift_count = len(historical_data[year_str]["assignments"])
        elif event_for_year and "assignments" in event_for_year:
            gift_count = len([a for a in event_for_year["assignments"] if "gift" in a]) if isinstance(
                event_for_year["assignments"], list) else 0

        return participant_count, gift_count

    @ss_root.sub_command(name="entropy", description="Check current entropy and system info")
    @mod_only()
    async def ss_entropy(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        embed = disnake.Embed(title="System Entropy Information", color=disnake.Color.blue())
        embed.add_field(name="Enhanced Entropy", value=f"{_get_enhanced_entropy():.6f}", inline=True)
        embed.add_field(name="Current Time", value=dt.datetime.now().isoformat(), inline=False)
        embed.add_field(name="Current Year", value=str(self.state["current_year"]), inline=True)

        await inter.edit_original_response(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        event = self._event()
        if not event or not event.get("active") or event.get("join_closed") or payload.message_id != event.get(
                "announcement_message_id"):
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
        if not event or not event.get("active") or event.get("join_closed") or payload.message_id != event.get(
                "announcement_message_id"):
            return

        user_id_str = str(payload.user_id)
        if user_id_str not in event["participants"]:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)
            user_has_other_reactions = any(
                user.id == payload.user_id async for reaction in message.reactions async for user in reaction.users())

            if not user_has_other_reactions:
                async with self._lock:
                    if user_id_str in event["participants"]:
                        event["participants"].pop(user_id_str)
                        await self._save()
                        await self._send_dm(payload.user_id,
                                            "❌ You've been removed from Secret Santa because you removed your reaction. React again to rejoin!")
        except Exception:
            pass


def setup(bot: commands.Bot):
    bot.add_cog(SecretSantaCog(bot))