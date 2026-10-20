"""Secret Santa signup card (mixin — combined into SecretSantaCog).

The bot posts its own signup card with Join/Leave buttons, a live headcount and the
latest joiners. The card follows the event: open (signup) → closed (shuffled) → ended.
Reactions on the card still work as a second way to join.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Optional

import disnake

from . import secret_santa_messages as ss_messages
from .secret_santa_views import join_card_view

JOIN_CARD_REFRESH_DELAY = 3
JOIN_MILESTONE_EVERY = 10


class SecretSantaJoinCardMixin:
    def _year(self) -> int:
        return self.state.get("current_year", dt.date.today().year)

    async def _signup_add(self, guild_id: Optional[int], user_id: int, name: str, source: str) -> tuple[str, int]:
        """Add a participant. Returns (joined | already | closed | no_event, participant count)."""
        async with self._lock:
            event = self._get_current_event()
            if not event:
                return "no_event", 0
            participants = event.setdefault("participants", {})
            if event.get("join_closed"):
                return "closed", len(participants)
            if str(user_id) in participants:
                return "already", len(participants)
            participants[str(user_id)] = name
            count = len(participants)
            await self._save_async()
        self.logger.info("SS join: %s (%s participants)", name, count)
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._apply_participant_role(guild, user_id, add=True, reason=f"Secret Santa signup ({source})")
        self._schedule_join_card_refresh()
        await self._maybe_post_milestone(count)
        return "joined", count

    async def _signup_remove(self, guild_id: Optional[int], user_id: int, source: str) -> tuple[str, int]:
        """Remove a participant. Returns (left | not_in | closed | no_event, participant count)."""
        async with self._lock:
            event = self._get_current_event()
            if not event:
                return "no_event", 0
            participants = event.setdefault("participants", {})
            if event.get("join_closed"):
                return "closed", len(participants)
            name = participants.pop(str(user_id), None)
            if name is None:
                return "not_in", len(participants)
            count = len(participants)
            await self._save_async()
        self.logger.info("SS leave: %s (%s participants)", name, count)
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._apply_participant_role(guild, user_id, add=False, reason=f"Left Secret Santa signup ({source})")
        self._schedule_join_card_refresh()
        return "left", count

    async def _join_card_join(self, inter: disnake.MessageInteraction) -> None:
        name = getattr(inter.author, "display_name", None) or inter.author.name
        status, count = await self._signup_add(getattr(inter.guild, "id", None), inter.author.id, name, "button")
        if status == "joined":
            join_embed, join_view = self._join_dm(self._year())
            join_embed.title = f"You're Santa #{count}! ✨"
            await inter.followup.send(embed=join_embed, view=join_view, ephemeral=True)
            if await self._limit_join_dm.check(str(inter.author.id)):
                await self._send_dm(inter.author.id, embed=join_embed, view=join_view)
        elif status == "already":
            await inter.followup.send(
                content="🎅 You're already on the list! Changed your mind? Press **Leave**.", ephemeral=True
            )
        elif status == "closed":
            await inter.followup.send(content="🔒 Signup is closed, the pairs are already out.", ephemeral=True)
        else:
            await inter.followup.send(content="❌ This Secret Santa has ended.", ephemeral=True)

    async def _join_card_leave(self, inter: disnake.MessageInteraction) -> None:
        status, _ = await self._signup_remove(getattr(inter.guild, "id", None), inter.author.id, "button")
        if status == "left":
            leave_embed, _ = self._leave_dm(self._year())
            await inter.followup.send(embed=leave_embed, ephemeral=True)
        elif status == "not_in":
            await inter.followup.send(content="You're not signed up. Press **Join** to hop in! 🎄", ephemeral=True)
        elif status == "closed":
            await inter.followup.send(
                content="🔒 The shuffle already happened, so you can't leave from here. Ask a mod if you need out.",
                ephemeral=True,
            )
        else:
            await inter.followup.send(content="❌ This Secret Santa has ended.", ephemeral=True)

    def _join_card_phase(self, event: dict) -> str:
        if not event.get("active"):
            return "ended"
        return "closed" if event.get("assignments") else "open"

    async def _post_join_card(self, channel, year: int, event: dict) -> disnake.Message:
        return await channel.send(
            embed=ss_messages.join_card_embed(year, event, "open"), view=join_card_view("open")
        )

    async def _update_join_card(self, event: Optional[dict], year: Optional[int] = None,
                                phase: Optional[str] = None) -> None:
        """Re-render the signup card for this event (no-op for reaction-only events)."""
        if not event or not event.get("join_card"):
            return
        channel_id = event.get("announcement_channel_id")
        message_id = event.get("announcement_message_id")
        if not channel_id or not message_id:
            return
        phase = phase or self._join_card_phase(event)
        year = year or self._year()
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await channel.get_partial_message(message_id).edit(
                embed=ss_messages.join_card_embed(year, event, phase), view=join_card_view(phase)
            )
        except disnake.NotFound:
            self.logger.warning("Secret Santa signup card %s was deleted", message_id)
        except disnake.HTTPException as e:
            self.logger.warning("Could not update Secret Santa signup card: %s", e)

    def _schedule_join_card_refresh(self) -> None:
        """Batch rapid joins/leaves into one card edit (avoids Discord rate limits)."""
        event = self._get_current_event()
        if not event or not event.get("join_card"):
            return
        pending = getattr(self, "_join_card_refresh_task", None)
        if pending and not pending.done():
            return

        async def refresh():
            await asyncio.sleep(JOIN_CARD_REFRESH_DELAY)
            await self._update_join_card(self._get_current_event())

        self._join_card_refresh_task = asyncio.create_task(refresh())

    async def _maybe_post_milestone(self, count: int) -> None:
        if count < JOIN_MILESTONE_EVERY or count % JOIN_MILESTONE_EVERY:
            return
        async with self._lock:
            event = self._get_current_event()
            if not event or not event.get("join_card") or count <= event.get("join_milestone", 0):
                return
            event["join_milestone"] = count
            await self._save_async()
        try:
            channel_id = event.get("announcement_channel_id")
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await channel.send(
                ss_messages.join_milestone_text(self._year(), count, self._signup_url(event)),
                allowed_mentions=disnake.AllowedMentions.none(),
            )
        except disnake.HTTPException as e:
            self.logger.warning("Could not post Secret Santa milestone: %s", e)
