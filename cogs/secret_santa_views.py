"""
Secret Santa Views Module - Discord UI Components

RESPONSIBILITIES:
- Reply button view (persistent across bot restarts)
- Reply modal for giftee responses
- History paginator for year viewing
- Various list paginators for different commands

ISOLATION:
- Discord UI components only
- Minimal coupling (uses cog lookup for functionality)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Dict, List, Optional, Tuple

import aiohttp
import disnake

from .secret_santa_checks import format_gift_description_for_display, GIFT_NO_SUBMISSION_ROW
from . import secret_santa_messages as ss_messages

# action -> (button name shown in errors, typed command to use instead, what did NOT happen)
ACTION_INFO: Dict[str, Tuple[str, Optional[str], str]] = {
    "wishlist_add": ("Add to wishlist", "ss wishlist add", "Your wishlist wasn't changed"),
    "wishlist_view": ("My wishlist", "ss wishlist view", "Nothing was changed"),
    "giftee": ("View their wishlist", "ss giftee", "Nothing was changed"),
    "ask": ("Ask them something", "ss ask_giftee", "Your question wasn't sent yet"),
    "submit_gift": ("Log my gift", "ss submit_gift", "Your gift wasn't logged yet"),
    "reply": ("Reply to Santa", None, "Your reply wasn't sent yet"),
}

TIMEOUT_DM_COOLDOWN_SECONDS = 300
_timeout_dm_sent_at: Dict[int, float] = {}


def _is_expired_interaction(exc: BaseException) -> bool:
    return isinstance(exc, disnake.NotFound) and getattr(exc, "code", None) == 10062


def _error_embed_for(exc: BaseException, action: str, year: int, typed: Optional[str]) -> disnake.Embed:
    button_name, command, not_done = ACTION_INFO[action]
    if isinstance(exc, disnake.HTTPException) and exc.status == 429:
        return ss_messages.error_rate_limited(year, command, not_done, getattr(exc, "retry_after", None), typed)
    if (isinstance(exc, disnake.HTTPException) and exc.status >= 500) or isinstance(
        exc, (asyncio.TimeoutError, ConnectionError, aiohttp.ClientError)
    ):
        return ss_messages.error_discord(year, command, not_done, typed)
    return ss_messages.error_bug(year, button_name, command, not_done, typed)


async def _dm_timed_out(inter: disnake.Interaction, action: str, year: int) -> None:
    """Discord refuses late replies to an expired click, so tell the user by DM (rate-limited)."""
    user_id = inter.author.id
    now = time.monotonic()
    if now - _timeout_dm_sent_at.get(user_id, -TIMEOUT_DM_COOLDOWN_SECONDS) < TIMEOUT_DM_COOLDOWN_SECONDS:
        return
    _timeout_dm_sent_at[user_id] = now
    button_name, command, not_done = ACTION_INFO[action]
    try:
        await inter.author.send(embed=ss_messages.error_timed_out_dm(year, button_name, command, not_done))
    except disnake.HTTPException:
        pass


async def run_guarded(inter: disnake.Interaction, action: str, handler, typed: Optional[str] = None) -> None:
    """Run a button/form handler; on any failure tell the user why and which command to type instead."""
    cog = inter.bot.get_cog("SecretSantaCog")
    year = cog.state.get("current_year", dt.date.today().year) if cog else dt.date.today().year
    try:
        await handler()
    except Exception as exc:
        logger = getattr(inter.bot, "logger", None)
        if logger:
            logger.error(f"SS button '{action}' failed: {exc!r}", exc_info=not isinstance(exc, disnake.HTTPException))
        if _is_expired_interaction(exc):
            await _dm_timed_out(inter, action, year)
            return
        embed = _error_embed_for(exc, action, year, typed)
        try:
            if inter.response.is_done():
                await inter.followup.send(embed=embed, ephemeral=True)
            else:
                await inter.response.send_message(embed=embed, ephemeral=True)
        except disnake.HTTPException:
            await _dm_timed_out(inter, action, year)
        return
    # The /ss handlers swallow an expired defer and return quietly; nothing reached the user.
    if not inter.response.is_done():
        await _dm_timed_out(inter, action, year)


class SecretSantaReplyView(disnake.ui.View):
    """View with reply button for Secret Santa messages - persists across bot restarts"""
    def __init__(self):
        super().__init__(timeout=None)  # Never expires - button stays active forever
    
    @disnake.ui.button(
        label="💬 Reply to Santa", 
        style=disnake.ButtonStyle.primary, 
        emoji="🎅",
        custom_id="ss_reply:persist"  # Persistent ID so Discord remembers it after restart
    )
    async def reply_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Handle reply button click - works even after bot restart"""
        async def handler():
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                await inter.response.send_message(content="❌ Secret Santa system not available", ephemeral=True)
                return
            event = cog._get_current_event()
            if not event:
                await inter.response.send_message(content="❌ No active Secret Santa event", ephemeral=True)
                return
            santa_id = cog._find_santa_for_giftee(event, str(inter.author.id))
            if not santa_id:
                await inter.response.send_message(content="❌ You don't have a Secret Santa assigned yet", ephemeral=True)
                return
            await inter.response.send_modal(SecretSantaReplyModal(santa_id, inter.author.id))
        await run_guarded(inter, "reply", handler)


class SecretSantaReplyModal(disnake.ui.Modal):
    """Modal for Secret Santa replies"""
    def __init__(self, santa_id: int, giftee_id: int):
        # Create the text input component
        text_input = disnake.ui.TextInput(
            label="Your Reply",
            custom_id="reply_text",
            placeholder="Type your reply here...",
            style=disnake.TextInputStyle.paragraph,
            max_length=2000,
            required=True
        )
        
        # Initialize modal with components
        super().__init__(
            title="💬 Reply to Your Secret Santa",
            components=[text_input]
        )
        self.santa_id = santa_id
        self.giftee_id = giftee_id
    
    async def callback(self, inter: disnake.ModalInteraction):
        """Handle modal submission"""
        text_values = getattr(inter, "text_values", None) or {}
        reply = (text_values.get("reply_text") or "").strip()

        async def handler():
            await inter.response.defer(ephemeral=True)
            if not reply:
                await inter.followup.send(content="❌ Please type a reply before sending.", ephemeral=True)
                return
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                await inter.followup.send(content="❌ Secret Santa system not available", ephemeral=True)
                return
            await cog._process_reply(inter, reply, self.santa_id, self.giftee_id)
        await run_guarded(inter, "reply", handler, typed=reply or None)


class SecretSantaTextModal(disnake.ui.Modal):
    """One-field form that forwards the typed text to an existing /ss command handler."""
    def __init__(self, action: str, title: str, label: str, placeholder: str, max_length: int):
        super().__init__(
            title=title,
            custom_id=f"ss_modal:{action}",
            components=[
                disnake.ui.TextInput(
                    label=label,
                    custom_id="text",
                    placeholder=placeholder,
                    style=disnake.TextInputStyle.paragraph,
                    max_length=max_length,
                    required=True,
                )
            ],
        )
        self.action = action

    async def callback(self, inter: disnake.ModalInteraction):
        text = ((getattr(inter, "text_values", None) or {}).get("text") or "").strip()
        cog = inter.bot.get_cog("SecretSantaCog")
        if not cog or not text:
            await inter.response.send_message(
                content="❌ Secret Santa system not available" if not cog else "❌ Please type something first.",
                ephemeral=True,
            )
            return

        async def handler():
            if self.action == "wishlist_add":
                await cog.wishlist_add.callback(cog, inter, item=text)
            elif self.action == "ask":
                await cog.ss_ask.callback(cog, inter, question=text)
            elif self.action == "submit_gift":
                await cog.ss_submit.callback(cog, inter, gift_description=text)
        await run_guarded(inter, self.action, handler, typed=text)


# action -> (label, emoji, style, modal spec or None). Modal spec: (title, label, placeholder, max_length).
SANTA_ACTIONS: Dict[str, tuple] = {
    "wishlist_add": ("Add to wishlist", "📝", disnake.ButtonStyle.success,
                     ("📝 Add to your wishlist", "Gift idea", "Favorite snacks, hobbies, colors…", 500)),
    "wishlist_view": ("My wishlist", "👀", disnake.ButtonStyle.secondary, None),
    "giftee": ("View their wishlist", "🎁", disnake.ButtonStyle.primary, None),
    "ask": ("Ask them something", "❓", disnake.ButtonStyle.secondary,
            ("❓ Ask your giftee (anonymous)", "Your question", "What's your favorite…", 2000)),
    "submit_gift": ("Log my gift", "📦", disnake.ButtonStyle.success,
                    ("📦 Log your gift", "What did you give?", "A cozy scarf and cocoa…", 2000)),
}


class SantaActionsView(disnake.ui.View):
    """Persistent DM shortcut buttons. Register `SantaActionsView(*SANTA_ACTIONS)` once at startup;
    messages can then carry any subset and keep working after restarts (matched by custom_id)."""
    def __init__(self, *actions: str, labels: Optional[Dict[str, str]] = None):
        super().__init__(timeout=None)
        labels = labels or {}
        for action in actions:
            label, emoji, style, _ = SANTA_ACTIONS[action]
            button = disnake.ui.Button(
                label=labels.get(action, label), emoji=emoji, style=style, custom_id=f"ss_act:{action}"
            )
            button.callback = self._make_callback(action)
            self.add_item(button)

    @staticmethod
    def _make_callback(action: str):
        async def callback(inter: disnake.MessageInteraction):
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                await inter.response.send_message(content="❌ Secret Santa system not available", ephemeral=True)
                return
            modal_spec = SANTA_ACTIONS[action][3]

            async def handler():
                if modal_spec:
                    await inter.response.send_modal(SecretSantaTextModal(action, *modal_spec))
                elif action == "wishlist_view":
                    await cog.wishlist_view.callback(cog, inter)
                elif action == "giftee":
                    await cog.ss_view_giftee_wishlist.callback(cog, inter)
            await run_guarded(inter, action, handler)
        return callback


def link_view(label: str, url: Optional[str], emoji: str = "🔗") -> Optional[disnake.ui.View]:
    if not url:
        return None
    view = disnake.ui.View(timeout=None)
    view.add_item(disnake.ui.Button(label=label, emoji=emoji, url=url, style=disnake.ButtonStyle.link))
    return view


class YearHistoryPaginator(disnake.ui.View):
    """
    Paginated view for year history with assignments.
    Allows users to flip through pages if there are many assignments.
    """
    def __init__(self, year: int, archive: dict, participants: dict, emoji_mapping: dict, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.year = year
        self.archive = archive if isinstance(archive, dict) else {}
        self.participants = participants if isinstance(participants, dict) else {}
        self.emoji_mapping = emoji_mapping if isinstance(emoji_mapping, dict) else {}
        self.current_page = 0
        
        # Build all assignment lines
        event_data = self.archive.get("event", {}) or {}
        if not isinstance(event_data, dict):
            event_data = {}
        assignments = event_data.get("assignments", {}) or {}
        gifts = event_data.get("gift_submissions", {}) or {}
        if not isinstance(assignments, dict):
            assignments = {}
        if not isinstance(gifts, dict):
            gifts = {}
        
        self.all_lines = []
        for giver_id, receiver_id in assignments.items():
            giver_name = self.participants.get(str(giver_id), f"User {giver_id}")
            receiver_name = self.participants.get(str(receiver_id), f"User {receiver_id}")
            
            giver_mention = f"<@{giver_id}>" if str(giver_id).isdigit() else giver_name
            receiver_mention = f"<@{receiver_id}>" if str(receiver_id).isdigit() else receiver_name
            
            giver_emoji = self.emoji_mapping.get(str(giver_id), "🎁")
            receiver_emoji = self.emoji_mapping.get(str(receiver_id), "🎄")
            
            # Check for gift (handle null/empty consistently)
            submission = gifts.get(str(giver_id))
            if submission and isinstance(submission, dict):
                raw = submission.get("gift")
                gift_desc = format_gift_description_for_display(
                    raw if isinstance(raw, str) else None,
                    max_length=60,
                )
                self.all_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention}")
                self.all_lines.append(f"    ⤷ {gift_desc}")
            else:
                self.all_lines.append(
                    f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention} *{GIFT_NO_SUBMISSION_ROW}*"
                )
        
        # Calculate pages (10 assignments per page = ~20 lines with gifts)
        self.items_per_page = 10
        self.total_assignments = len(assignments)
        self.total_pages = (self.total_assignments + self.items_per_page - 1) // self.items_per_page
        
        # Update button states
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button enabled/disabled state"""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
    
    def get_embed(self) -> disnake.Embed:
        """Generate embed for current page"""
        event_data = self.archive.get("event", {})
        assignments = event_data.get("assignments", {})
        gifts = event_data.get("gift_submissions", {})
        gifts_count = sum(1 for gid in assignments if (gifts.get(str(gid)) or {}).get("gift"))
        
        has_assignments = bool(assignments)
        has_gifts = gifts_count > 0
        
        if has_gifts:
            description = f"**{len(self.participants)}** participants, **{gifts_count}** gifts exchanged"
        elif has_assignments:
            description = f"**{len(self.participants)}** participants, assignments made but no gifts recorded"
        else:
            description = f"**{len(self.participants)}** participants signed up, event incomplete"
        
        embed = disnake.Embed(
            title=f"🎄 Secret Santa {self.year}",
            description=description,
            color=disnake.Color.gold(),
            timestamp=dt.datetime.now()
        )
        
        if has_assignments:
            # Calculate line range for this page
            # Each assignment can be 1-2 lines (with or without gift)
            # We need to count actual assignments, not lines
            start_idx = self.current_page * self.items_per_page
            end_idx = min(start_idx + self.items_per_page, self.total_assignments)
            
            # Build lines for this page's assignments
            page_lines = []
            assignment_idx = 0
            line_idx = 0
            
            while line_idx < len(self.all_lines) and assignment_idx < end_idx:
                if assignment_idx >= start_idx:
                    page_lines.append(self.all_lines[line_idx])
                    # Check if next line is a gift description (starts with spaces)
                    if line_idx + 1 < len(self.all_lines) and self.all_lines[line_idx + 1].startswith("    "):
                        page_lines.append(self.all_lines[line_idx + 1])
                        line_idx += 2
                    else:
                        line_idx += 1
                else:
                    # Skip this assignment
                    if line_idx + 1 < len(self.all_lines) and self.all_lines[line_idx + 1].startswith("    "):
                        line_idx += 2
                    else:
                        line_idx += 1
                
                assignment_idx += 1
            
            gifts_count = sum(1 for gid in assignments if (gifts.get(str(gid)) or {}).get("gift"))
            field_name = f"🎄 Assignments & Gifts ({gifts_count}/{len(assignments)} gifts submitted)"
            
            if self.total_pages > 1:
                field_name += f" - Page {self.current_page + 1}/{self.total_pages}"
            
            embed.add_field(
                name=field_name,
                value="\n".join(page_lines) if page_lines else "No assignments on this page",
                inline=False
            )
        else:
            status_text = f"⏸️ Signup completed ({len(self.participants)} joined)\n❌ No assignments made\n❌ No gifts recorded"
            embed.add_field(name="📝 Event Status", value=status_text, inline=False)
        
        # Statistics
        completion_rate = (gifts_count / len(self.participants) * 100) if self.participants else 0
        embed.add_field(
            name="📊 Statistics",
            value=f"**Completion:** {completion_rate:.0f}%\n**Total Gifts:** {gifts_count}",
            inline=True
        )
        
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use buttons to navigate")
        
        return embed
    
    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out"""
        for item in self.children:
            item.disabled = True


class FileListPaginator(disnake.ui.View):
    """Paginated view for file listings"""
    def __init__(self, files: List[Tuple[str, dict]], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.files = files
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = (len(files) + self.items_per_page - 1) // self.items_per_page
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button enabled/disabled state"""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
    
    def get_embed(self) -> disnake.Embed:
        """Generate embed for current page"""
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.files))
        page_files = self.files[start_idx:end_idx]
        
        embed = disnake.Embed(
            title="📦 Uploaded Files",
            color=disnake.Color.blue(),
            timestamp=dt.datetime.now()
        )
        
        for file_id, file_data in page_files:
            if not isinstance(file_data, dict):
                continue
            file_name = file_data.get("name") or "Unknown"
            uploaded_at = file_data.get("uploaded_at")
            size = file_data.get("size")
            size_mb = (size / 1024 / 1024) if isinstance(size, (int, float)) and size is not None else 0.0
            download_count = file_data.get("download_count", 0)
            uploaded_ts = int(uploaded_at) if isinstance(uploaded_at, (int, float)) and uploaded_at else 0
            embed.add_field(
                name=f"📦 {file_name}",
                value=(
                    f"Required by: 🎅 A Secret Santa\n"
                    f"Size: {size_mb:.2f} MB\n"
                    f"Sent to: {download_count} members\n"
                    f"Uploaded: <t:{uploaded_ts}:R>"
                ),
                inline=False
            )
        
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Showing {len(page_files)} of {len(self.files)} files")
        else:
            embed.set_footer(text=f"Total: {len(self.files)} file(s)")
        
        return embed
    
    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out"""
        for item in self.children:
            item.disabled = True


class CommunicationsPaginator(disnake.ui.View):
    """Paginated view for communication threads"""
    def __init__(self, comms: Dict[str, dict], participants: dict, emoji_mapping: dict, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.comms = list((comms or {}).items()) if isinstance(comms, dict) else []
        self.participants = participants or {}
        self.emoji_mapping = emoji_mapping or {}
        self.current_page = 0
        self.items_per_page = 5
        self.total_pages = max(1, (len(self.comms) + self.items_per_page - 1) // self.items_per_page)
        self._update_buttons()

    def _update_buttons(self):
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)

    def _thread_preview_lines(self, data: dict, santa_emoji: str, giftee_emoji: str) -> list:
        """Safe preview lines from thread list (max 3)."""
        thread = data.get("thread") if isinstance(data, dict) else None
        if not isinstance(thread, list):
            return []
        lines = []
        for msg in thread[:3]:
            if not isinstance(msg, dict):
                continue
            txt = msg.get("message") or ""
            if isinstance(txt, str) and len(txt) > 50:
                txt = txt[:50] + "..."
            msg_type = msg.get("type") or ""
            emoji = santa_emoji if msg_type == "question" else giftee_emoji
            lines.append(f"{emoji} {txt}")
        return lines

    def get_embed(self) -> disnake.Embed:
        """Generate embed for current page"""
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.comms))
        page_comms = self.comms[start_idx:end_idx]
        embed = disnake.Embed(
            title=f"💬 Communications ({len(self.comms)} threads)",
            color=disnake.Color.blue(),
            timestamp=dt.datetime.now()
        )
        for santa_id, data in page_comms:
            if not isinstance(data, dict):
                continue
            santa_name = self.participants.get(str(santa_id), f"User {santa_id}")
            giftee_id = data.get("giftee_id")
            giftee_name = self.participants.get(str(giftee_id), "Unknown")
            santa_emoji = self.emoji_mapping.get(str(santa_id), "🎅")
            giftee_emoji = self.emoji_mapping.get(str(giftee_id), "🎄")
            thread = data.get("thread") or []
            if not isinstance(thread, list):
                thread = []
            lines = self._thread_preview_lines(data, santa_emoji, giftee_emoji)
            thread_text = "\n".join(lines) if lines else "No messages"
            embed.add_field(
                name=f"💬 {santa_name} → {giftee_name} ({len(thread)} messages)",
                value=thread_text,
                inline=False
            )
        
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Showing {len(page_comms)} of {len(self.comms)} threads")
        else:
            embed.set_footer(text=f"Total: {len(self.comms)} thread(s)")
        
        return embed
    
    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out"""
        for item in self.children:
            item.disabled = True


class YearTimelinePaginator(disnake.ui.View):
    """Paginated view for year timeline overview"""
    def __init__(self, archives: Dict[int, dict], sorted_years: List[int], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.archives = archives
        self.sorted_years = sorted_years
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = (len(sorted_years) + self.items_per_page - 1) // self.items_per_page
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button enabled/disabled state"""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
    
    def get_embed(self) -> disnake.Embed:
        """Generate embed for current page"""
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.sorted_years))
        page_years = self.sorted_years[start_idx:end_idx]
        
        embed = disnake.Embed(
            title="🎄 Secret Santa Archive",
            description="Complete history of all Secret Santa events",
            color=disnake.Color.blue(),
            timestamp=dt.datetime.now()
        )
        
        # Build timeline for this page (count only submissions with non-empty gift)
        timeline_text = []
        for year_val in page_years:
            archive = self.archives[year_val]
            event_data = archive.get("event", {})
            participants = event_data.get("participants", {})
            gifts = event_data.get("gift_submissions", {})
            assignments_y = event_data.get("assignments", {})
            gifts_count_y = sum(1 for gid in assignments_y if (gifts.get(str(gid)) or {}).get("gift"))
            completion_rate = (gifts_count_y / len(participants) * 100) if participants else 0
            
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
                f"**{year_val}** {status} — {len(participants)} participants, {gifts_count_y} gifts ({completion_rate:.0f}%)"
            )
        
        embed.add_field(
            name="📅 Event Timeline",
            value="\n".join(timeline_text),
            inline=False
        )
        
        # Calculate all-time statistics (real gift count per year)
        total_participants = total_gifts = 0
        for y in self.sorted_years:
            event_data = self.archives[y].get("event", {})
            participants_y = event_data.get("participants", {})
            gifts_y = event_data.get("gift_submissions", {})
            assignments_y = event_data.get("assignments", {})
            total_participants += len(participants_y)
            total_gifts += sum(1 for gid in assignments_y if (gifts_y.get(str(gid)) or {}).get("gift"))
        avg_participants = total_participants / len(self.sorted_years) if self.sorted_years else 0
        avg_completion = (total_gifts / total_participants * 100) if total_participants else 0
        
        stats_text = [
            f"**Total Events:** {len(self.sorted_years)}",
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
        
        embed.add_field(
            name="📖 Status Legend",
            value="✅ 90%+ complete | 🟨 70-89% | 🟧 Under 70% | ⏳ No gifts recorded",
            inline=False
        )
        
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Use buttons to navigate • Use /ss history [year] for detailed view")
        else:
            embed.set_footer(text=f"Use /ss history [year] for detailed view")
        
        return embed
    
    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out"""
        for item in self.children:
            item.disabled = True


class BackupListPaginator(disnake.ui.View):
    """Paginated view for backup listings"""
    def __init__(self, backup_list: List[str], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.backup_list = backup_list
        self.current_page = 0
        self.items_per_page = 15
        self.total_pages = (len(backup_list) + self.items_per_page - 1) // self.items_per_page
        self._update_buttons()
    
    def _update_buttons(self):
        """Update button enabled/disabled state"""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page >= self.total_pages - 1)
    
    def get_embed(self) -> disnake.Embed:
        """Generate embed for current page"""
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.backup_list))
        page_backups = self.backup_list[start_idx:end_idx]
        
        embed = disnake.Embed(
            title="📋 Backed-Up Years",
            description=f"Found **{len(self.backup_list)}** year(s) in backups folder.",
            color=disnake.Color.blue(),
            timestamp=dt.datetime.now()
        )
        
        field_name = "Years" if self.current_page == 0 else f"Years (Page {self.current_page + 1})"
        embed.add_field(
            name=field_name,
            value="\n".join(page_backups),
            inline=False
        )
        
        embed.add_field(
            name="🔧 Actions",
            value=f"• Restore a year: `/ss restore_year [year]`\n"
                  f"• View all active years: `/ss history`\n"
                  f"• Bot ignores backups folder automatically",
            inline=False
        )
        
        if self.total_pages > 1:
            embed.set_footer(text=f"Page {self.current_page + 1}/{self.total_pages} • Location: archive/backups/")
        else:
            embed.set_footer(text=f"Location: archive/backups/")
        
        return embed
    
    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to previous page"""
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        """Go to next page"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out"""
        for item in self.children:
            item.disabled = True

