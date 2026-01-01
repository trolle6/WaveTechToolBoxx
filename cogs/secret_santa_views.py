"""
Secret Santa Views Module - Discord UI Components

RESPONSIBILITIES:
- Reply button view (persistent across bot restarts)
- Reply modal for giftee responses
- History paginator for year viewing

ISOLATION:
- Discord UI components only
- Minimal coupling (uses cog lookup for functionality)
"""

from __future__ import annotations

import datetime as dt
from typing import Dict

import disnake


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
        try:
            # Get the cog instance
            cog = inter.bot.get_cog("SecretSantaCog")
            if not cog:
                await inter.response.send_message(content="❌ Secret Santa system not available", ephemeral=True)
                return
            
            # Check if there's an active event
            event = cog._get_current_event()
            if not event:
                await inter.response.send_message(content="❌ No active Secret Santa event", ephemeral=True)
                return
            
            # Find who is the user's Santa (dynamic lookup from event data)
            user_id = str(inter.author.id)  # Convert to string to match dict keys
            santa_id = None
            for giver, receiver in event.get("assignments", {}).items():
                if receiver == user_id:
                    santa_id = int(giver)
                    break
            
            if not santa_id:
                await inter.response.send_message(content="❌ You don't have a Secret Santa assigned yet", ephemeral=True)
                return
            
            # Create a modal for the reply (modal needs int IDs for DM sending)
            modal = SecretSantaReplyModal(santa_id, int(user_id))
            await inter.response.send_modal(modal)
            
        except Exception as e:
            # Log the error for debugging
            if hasattr(inter.bot, 'logger'):
                inter.bot.logger.error(f"Reply button error: {e}")
            await inter.response.send_message(content="❌ An error occurred while opening the reply form", ephemeral=True)


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
        await inter.response.defer(ephemeral=True)
        
        reply = inter.text_values["reply_text"]
        
        # Get the cog instance
        cog = inter.bot.get_cog("SecretSantaCog")
        if not cog:
            await inter.followup.send(content="❌ Secret Santa system not available", ephemeral=True)
            return
        
        # Process the reply using the existing logic
        await cog._process_reply(inter, reply, self.santa_id, self.giftee_id)


class YearHistoryPaginator(disnake.ui.View):
    """
    Paginated view for year history with assignments.
    Allows users to flip through pages if there are many assignments.
    """
    def __init__(self, year: int, archive: dict, participants: dict, emoji_mapping: dict, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.year = year
        self.archive = archive
        self.participants = participants
        self.emoji_mapping = emoji_mapping
        self.current_page = 0
        
        # Build all assignment lines
        event_data = archive.get("event", {})
        assignments = event_data.get("assignments", {})
        gifts = event_data.get("gift_submissions", {})
        
        self.all_lines = []
        for giver_id, receiver_id in assignments.items():
            giver_name = participants.get(str(giver_id), f"User {giver_id}")
            receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")
            
            giver_mention = f"<@{giver_id}>" if str(giver_id).isdigit() else giver_name
            receiver_mention = f"<@{receiver_id}>" if str(receiver_id).isdigit() else receiver_name
            
            giver_emoji = emoji_mapping.get(str(giver_id), "🎁")
            receiver_emoji = emoji_mapping.get(str(receiver_id), "🎄")
            
            # Check for gift
            submission = gifts.get(str(giver_id))
            if submission and isinstance(submission, dict):
                gift_desc = submission.get("gift", "No description provided")
                if isinstance(gift_desc, str) and len(gift_desc) > 60:
                    gift_desc = gift_desc[:57] + "..."
                elif not isinstance(gift_desc, str):
                    gift_desc = "Invalid gift description"
                
                self.all_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention}")
                self.all_lines.append(f"    ⤷ *{gift_desc}*")
            else:
                self.all_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention} *(no gift recorded)*")
        
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
        
        has_assignments = bool(assignments)
        has_gifts = bool(gifts)
        
        if has_gifts:
            description = f"**{len(self.participants)}** participants, **{len(gifts)}** gifts exchanged"
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
            
            gifts_count = len([g for g in gifts.keys() if g in [str(a) for a in assignments.keys()]])
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
        completion_rate = (len(gifts) / len(self.participants) * 100) if self.participants else 0
        embed.add_field(
            name="📊 Statistics",
            value=f"**Completion:** {completion_rate:.0f}%\n**Total Gifts:** {len(gifts)}",
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




