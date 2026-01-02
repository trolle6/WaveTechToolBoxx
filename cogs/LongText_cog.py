"""
Long Text Handler Cog - Handle Large Text Inputs

FEATURES:
- 📄 Upload text files (.txt) and extract content
- 📝 Multi-part message builder (append chunks, finalize)
- 💬 Output full text to channel or send as file
- 🔄 Clear buffer and start over

COMMANDS:
- /longtext upload [file] - Upload a text file and get the content
- /longtext start - Start a new multi-part message
- /longtext append [text] - Append text to current message
- /longtext show - Show current message content
- /longtext finalize - Finalize and output the complete message
- /longtext clear - Clear current message buffer
- /longtext send [channel] - Send the message to a channel
"""

import asyncio
import time
from io import BytesIO
from typing import Any, Dict, Optional

import disnake
from disnake.ext import commands


class LongTextCog(commands.Cog):
    """Handle large text inputs through files or multi-part messages"""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("longtext")
        self._buffers: Dict[int, Dict[str, Any]] = {}  # {user_id: {"text": str, "started_at": float}}
        self._lock = asyncio.Lock()
        self.logger.info("LongText cog initialized")
    
    # ============ BUFFER MANAGEMENT ============
    def _get_buffer(self, user_id: int) -> Optional[Dict]:
        """Get user's buffer"""
        return self._buffers.get(user_id)
    
    def _create_buffer(self, user_id: int, text: str = ""):
        """Create or update buffer"""
        self._buffers[user_id] = {
            "text": text,
            "started_at": time.time()
        }
    
    def _get_text_stats(self, text: str) -> str:
        """Get text statistics"""
        lines = text.count('\n') + 1
        words = len(text.split())
        return f"• Characters: {len(text):,}\n• Lines: {lines}\n• Words: ~{words}"
    
    async def _cleanup_old_buffers(self):
        """Clean up buffers older than 1 hour"""
        current_time = time.time()
        async with self._lock:
            expired = [
                user_id for user_id, data in self._buffers.items()
                if current_time - data["started_at"] > 3600
            ]
            for user_id in expired:
                del self._buffers[user_id]
                self.logger.debug(f"Cleaned up expired buffer for user {user_id}")

    # ============ COMMANDS ============
    @commands.slash_command(name="longtext", description="Handle large text inputs")
    async def longtext(self, inter: disnake.ApplicationCommandInteraction):
        """Main longtext command group"""
        pass

    @longtext.sub_command(name="upload", description="Upload a text file (.txt) and extract the content")
    async def upload_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file: disnake.Attachment = commands.Param(description="Text file to upload (.txt)")
    ):
        """Upload and extract text from a file"""
        await inter.response.defer(ephemeral=True)
        
        if not file.filename.lower().endswith('.txt'):
            await inter.edit_original_response(content="❌ Error: Please upload a `.txt` file")
            return
        
        if file.size > 1_000_000:  # 1MB
            await inter.edit_original_response(
                content=f"❌ Error: File too large ({file.size / 1024:.1f}KB). Maximum size is 1MB for text files."
            )
            return
        
        try:
            file_data = await file.read()
            text_content = file_data.decode('utf-8')
            
            async with self._lock:
                self._create_buffer(inter.author.id, text_content)
            
            preview_length = min(500, len(text_content))
            preview = text_content[:preview_length]
            
            embed = disnake.Embed(
                title="✅ File Uploaded Successfully!",
                description=f"**File:** `{file.filename}`\n**Size:** {len(text_content):,} characters",
                color=disnake.Color.green()
            )
            embed.add_field(
                name="📄 Preview (first 500 chars)",
                value=f"```\n{preview}\n```" if preview else "*Empty file*",
                inline=False
            )
            embed.add_field(name="📊 Stats", value=self._get_text_stats(text_content), inline=True)
            embed.set_footer(text="Use /longtext show to see full content, or /longtext send to output it")
            
            await inter.edit_original_response(embed=embed)
            
        except UnicodeDecodeError:
            await inter.edit_original_response(
                content="❌ Error: File encoding issue. Please ensure the file is UTF-8 encoded."
            )
        except Exception as e:
            self.logger.error(f"Error uploading file: {e}", exc_info=True)
            await inter.edit_original_response(content=f"❌ Error reading file: {str(e)}")

    @longtext.sub_command(name="start", description="Start building a new multi-part message")
    async def start_message(self, inter: disnake.ApplicationCommandInteraction):
        """Start a new multi-part message"""
        await inter.response.defer(ephemeral=True)
        
        async with self._lock:
            self._create_buffer(inter.author.id)
        
        embed = disnake.Embed(
            title="📝 Message Builder Started",
            description="You can now append text using `/longtext append [text]`\n\n"
                       "**Available commands:**\n"
                       "• `/longtext append [text]` - Add text to the message\n"
                       "• `/longtext show` - View current content\n"
                       "• `/longtext finalize` - Get the complete message\n"
                       "• `/longtext send [channel]` - Send to a channel\n"
                       "• `/longtext clear` - Start over",
            color=disnake.Color.blue()
        )
        embed.set_footer(text="Your buffer will expire after 1 hour of inactivity")
        
        await inter.edit_original_response(embed=embed)

    @longtext.sub_command(name="append", description="Append text to your current message buffer")
    async def append_text(
        self,
        inter: disnake.ApplicationCommandInteraction,
        text: str = commands.Param(description="Text to append to your message", max_length=2000)
    ):
        """Append text to the current buffer"""
        await inter.response.defer(ephemeral=True)
        
        async with self._lock:
            buffer = self._get_buffer(inter.author.id)
            if not buffer:
                await inter.edit_original_response(
                    content="❌ No active message buffer! Use `/longtext start` to create one first."
                )
                return
            
            current_text = buffer["text"]
            if current_text and not current_text.endswith('\n'):
                buffer["text"] += "\n"
            buffer["text"] += text
            buffer["started_at"] = time.time()
            updated_text = buffer["text"]
        
        embed = disnake.Embed(
            title="✅ Text Appended",
            description=f"**Current length:** {len(updated_text):,} characters",
            color=disnake.Color.green()
        )
        embed.add_field(
            name="📝 Last Added",
            value=f"```\n{text[:200]}{'...' if len(text) > 200 else ''}\n```",
            inline=False
        )
        embed.set_footer(text="Use /longtext show to view full content, or /longtext finalize when done")
        
        await inter.edit_original_response(embed=embed)

    @longtext.sub_command(name="show", description="Show the current message content")
    async def show_message(self, inter: disnake.ApplicationCommandInteraction):
        """Show current buffer content"""
        await inter.response.defer(ephemeral=True)
        
        async with self._lock:
            buffer = self._get_buffer(inter.author.id)
            if not buffer:
                await inter.edit_original_response(
                    content="❌ No active message buffer! Use `/longtext start` to create one first."
                )
                return
            
            text = buffer["text"]
        
        if not text:
            await inter.edit_original_response(
                content="📝 Your message buffer is empty. Use `/longtext append [text]` to add content."
            )
            return
        
        if len(text) <= 2000:
            embed = disnake.Embed(
                title="📄 Current Message Content",
                description=f"**Length:** {len(text):,} characters",
                color=disnake.Color.blue()
            )
            embed.add_field(name="Content", value=f"```\n{text}\n```", inline=False)
            await inter.edit_original_response(embed=embed)
        else:
            file = disnake.File(
                BytesIO(text.encode('utf-8')),
                filename=f"message_{inter.author.id}.txt"
            )
            
            embed = disnake.Embed(
                title="📄 Current Message Content",
                description=f"**Length:** {len(text):,} characters\n\nContent is too long to display inline. Here's the file:",
                color=disnake.Color.blue()
            )
            embed.add_field(name="📊 Stats", value=self._get_text_stats(text), inline=True)
            
            await inter.edit_original_response(embed=embed, file=file)

    @longtext.sub_command(name="finalize", description="Get the final complete message (as file if too long)")
    async def finalize_message(self, inter: disnake.ApplicationCommandInteraction):
        """Finalize and get the complete message"""
        await inter.response.defer(ephemeral=True)
        
        async with self._lock:
            buffer = self._get_buffer(inter.author.id)
            if not buffer:
                await inter.edit_original_response(
                    content="❌ No active message buffer! Use `/longtext start` to create one first."
                )
                return
            
            text = buffer["text"]
        
        if not text:
            await inter.edit_original_response(content="📝 Your message buffer is empty. Nothing to finalize.")
            return
        
        file = disnake.File(
            BytesIO(text.encode('utf-8')),
            filename=f"message_{inter.author.display_name}_{inter.author.id}.txt"
        )
        
        embed = disnake.Embed(
            title="✅ Message Finalized",
            description=f"**Final length:** {len(text):,} characters",
            color=disnake.Color.green()
        )
        embed.add_field(name="📊 Final Stats", value=self._get_text_stats(text), inline=True)
        embed.set_footer(text="You can copy this text or use it wherever you need!")
        
        await inter.edit_original_response(embed=embed, file=file)

    @longtext.sub_command(name="send", description="Send the message to a channel (as file if too long)")
    async def send_message(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: Optional[disnake.TextChannel] = commands.Param(
            default=None,
            description="Channel to send to (defaults to current channel)"
        )
    ):
        """Send the message to a channel"""
        await inter.response.defer(ephemeral=True)
        
        target_channel = channel or inter.channel
        if not isinstance(target_channel, disnake.TextChannel):
            await inter.edit_original_response(content="❌ Error: Can only send to text channels")
            return
        
        if not target_channel.permissions_for(inter.author).send_messages:
            await inter.edit_original_response(
                content=f"❌ Error: You don't have permission to send messages in {target_channel.mention}"
            )
            return
        
        async with self._lock:
            buffer = self._get_buffer(inter.author.id)
            if not buffer:
                await inter.edit_original_response(
                    content="❌ No active message buffer! Use `/longtext start` to create one first."
                )
                return
            
            text = buffer["text"]
        
        if not text:
            await inter.edit_original_response(content="📝 Your message buffer is empty. Nothing to send.")
            return
        
        try:
            if len(text) <= 2000:
                await target_channel.send(text)
                await inter.edit_original_response(content=f"✅ Message sent to {target_channel.mention}!")
            else:
                file = disnake.File(
                    BytesIO(text.encode('utf-8')),
                    filename=f"message_{inter.author.display_name}.txt"
                )
                await target_channel.send(
                    f"📄 **Message from {inter.author.mention}** ({len(text):,} characters):",
                    file=file
                )
                await inter.edit_original_response(content=f"✅ Message sent to {target_channel.mention} as file!")
        except disnake.Forbidden:
            await inter.edit_original_response(
                content=f"❌ Error: Bot doesn't have permission to send messages in {target_channel.mention}"
            )
        except Exception as e:
            self.logger.error(f"Error sending message: {e}", exc_info=True)
            await inter.edit_original_response(content=f"❌ Error sending message: {str(e)}")

    @longtext.sub_command(name="clear", description="Clear your current message buffer")
    async def clear_message(self, inter: disnake.ApplicationCommandInteraction):
        """Clear the current buffer"""
        await inter.response.defer(ephemeral=True)
        
        async with self._lock:
            buffer = self._get_buffer(inter.author.id)
            if not buffer:
                await inter.edit_original_response(content="📝 No active message buffer to clear.")
                return
            
            length = len(buffer["text"])
            del self._buffers[inter.author.id]
        
        embed = disnake.Embed(
            title="🗑️ Buffer Cleared",
            description=f"Cleared {length:,} characters from your buffer.",
            color=disnake.Color.orange()
        )
        embed.set_footer(text="Use /longtext start to begin a new message")
        
        await inter.edit_original_response(embed=embed)

    # ============ COG LIFECYCLE ============
    async def cog_load(self):
        """Initialize cog"""
        self.logger.info("LongText cog loaded")
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("📄 LongText cog loaded successfully", "SUCCESS")
    
    def cog_unload(self):
        """Cleanup cog"""
        self.logger.info("Unloading LongText cog...")
        self._buffers.clear()
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Start cleanup task"""
        async def cleanup_loop():
            try:
                while True:
                    await asyncio.sleep(600)  # 10 minutes
                    await self._cleanup_old_buffers()
            except asyncio.CancelledError:
                pass
        
        asyncio.create_task(cleanup_loop())


def setup(bot):
    """Setup the cog"""
    bot.add_cog(LongTextCog(bot))
