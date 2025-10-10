import datetime as dt
import functools
import json
import random
from pathlib import Path

import disnake
from disnake.ext import commands


ARCHIVE_DIR = Path("ss_archive")
ARCHIVE_DIR.mkdir(exist_ok=True)


def mod_only(func):
    @functools.wraps(func)
    async def wrapper(self, inter, *args, **kwargs):
        if inter.author.guild_permissions.administrator or any(
            r.id == self.bot.config.DISCORD_MODERATOR_ROLE_ID for r in inter.author.roles
        ):
            return await func(self, inter, *args, **kwargs)
        await inter.send("Mod only", ephemeral=True)

    return wrapper


class SecretSantaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state = {}
        self.load_state_sync()

    def load_state_sync(self):
        """Load state synchronously at init"""
        try:
            content = Path("ss_state.json").read_text()
            self.state = json.loads(content) if content else {"event": None}
        except FileNotFoundError:
            self.state = {"event": None}

    @commands.slash_command(name="ss")
    async def ss(self, inter: disnake.AppCmdInter):
        pass

    @ss.sub_command(name="start")
    @mod_only
    async def ss_start(
        self, inter: disnake.AppCmdInter, message_id: str
    ):
        await inter.response.defer(ephemeral=True)

        try:
            msg_id = int(message_id)
        except ValueError:
            return await inter.edit_original_response("Invalid message ID")

        if self.state.get("event") and self.state["event"].get("active"):
            return await inter.edit_original_response(
                "Event already active"
            )

        participants = {}
        for channel in inter.guild.text_channels:
            try:
                msg = await channel.fetch_message(msg_id)
                for reaction in msg.reactions:
                    async for user in reaction.users():
                        if user.id != self.bot.user.id:
                            member = inter.guild.get_member(user.id)
                            participants[str(user.id)] = (
                                member.display_name if member else user.name
                            )
                break
            except Exception:
                continue

        self.state["event"] = {
            "active": True,
            "msg_id": msg_id,
            "participants": participants,
            "assignments": {},
            "guild_id": inter.guild.id,
            "year": dt.date.today().year,
        }
        await self._save_state()
        await inter.edit_original_response(
            f"Started! Found {len(participants)} participants"
        )

    @ss.sub_command(name="shuffle")
    @mod_only
    async def ss_shuffle(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        event = self.state.get("event")
        if not event:
            return await inter.edit_original_response("No active event")

        participants = list(map(int, event["participants"].keys()))
        if len(participants) < 2:
            return await inter.edit_original_response("Need 2+ participants")

        random.shuffle(participants)
        assigns = {
            participants[i]: participants[(i + 1) % len(participants)]
            for i in range(len(participants))
        }

        for giver, receiver in assigns.items():
            try:
                user = await self.bot.fetch_user(giver)
                await user.send(
                    f"You're Secret Santa for <@{receiver}>!"
                )
            except Exception:
                pass

        self.state["event"]["assignments"] = {
            str(k): v for k, v in assigns.items()
        }
        await self._save_state()
        await inter.edit_original_response(f"Assigned {len(assigns)} pairs!")

    @ss.sub_command(name="stop")
    @mod_only
    async def ss_stop(self, inter: disnake.AppCmdInter):
        await inter.response.defer(ephemeral=True)

        if not self.state.get("event"):
            return await inter.edit_original_response("No active event")

        year = self.state["event"]["year"]
        (ARCHIVE_DIR / f"event_{year}.json").write_text(
            json.dumps(self.state["event"], indent=2)
        )
        self.state["event"] = None
        await self._save_state()
        await inter.edit_original_response("Event stopped and archived")

    async def _save_state(self):
        try:
            Path("ss_state.json").write_text(
                json.dumps(self.state, indent=2, ensure_ascii=False)
            )
        except Exception as e:
            self.bot.logger.error(f"Failed to save state: {e}")


def setup(bot):
    bot.add_cog(SecretSantaCog(bot))