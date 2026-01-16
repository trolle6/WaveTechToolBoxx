"""
Zip File Distribution Cog

FEATURES:
- 📦 Upload and distribute zip files (e.g., Minecraft texture packs)
- 👤 Track who required the file
- 📨 Automatically send files to Secret Santa participants (if active) or all server members via DM
- 🔒 Permission checks (only authorized users can upload)
- 💾 Persistent storage of file metadata
- 💻 Cross-platform compatible (Windows, Linux, macOS)

COMMANDS:
- /distributezip upload [attachment] [required_by] - Upload a zip file and distribute it
- /distributezip list - List all uploaded files
- /distributezip get [file_name] - Get a specific file
- /distributezip remove [file_name] - Remove a file (moderator only)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import disnake
from disnake.ext import commands

from .owner_utils import owner_check, get_owner_mention, is_owner
from .secret_santa_checks import mod_check
from .distributezip_file_browser import create_file_browser_view, FileBrowserSelectView

# Paths
ROOT = Path(__file__).parent
FILES_DIR = ROOT / "distributed_files"
METADATA_FILE = ROOT / "distributed_files_metadata.json"

# Ensure files directory exists
FILES_DIR.mkdir(exist_ok=True)

# File size limits and configuration
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB - Discord's limit for attachments
MEGABYTE = 1024 * 1024  # Bytes in one megabyte (for size formatting)


def load_metadata() -> Dict:
    """Load file metadata"""
    if METADATA_FILE.exists():
        try:
            return json.loads(METADATA_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {}
    return {}


def save_metadata(data: Dict):
    """Save file metadata"""
    try:
        METADATA_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
    except OSError as e:
        logging.getLogger("bot").error(f"Failed to save file metadata: {e}")


class DistributeZipCog(commands.Cog):
    """Zip file distribution system"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("distributezip")
        self.metadata = load_metadata()
        self._sending_lock = asyncio.Lock()
        
        # Ensure metadata structure
        self.metadata.setdefault("files", {})
        self.metadata.setdefault("history", [])
        
        self.logger.info("DistributeZip cog initialized")

    # ============ FILE UTILITIES ============
    def _find_file_by_name(self, file_name: str) -> Optional[Tuple[str, dict]]:
        """Find file by name (case-insensitive)"""
        files = self.metadata.get("files", {})
        file_name_lower = file_name.lower()
        for fid, data in files.items():
            if data.get("name", "").lower() == file_name_lower:
                return (fid, data)
        return None
    
    def _get_available_files(self) -> List[str]:
        """Get list of available file names"""
        files = self.metadata.get("files", {})
        return sorted([data.get("name", "") for data in files.values() if data.get("name")])
    
    async def _autocomplete_file_name(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete function for file_name selection"""
        try:
            available_files = self._get_available_files()
            if not available_files:
                return []
            
            # Filter files that match the input string
            string_lower = string.lower() if string else ""
            matching_files = [
                file_name for file_name in available_files
                if string_lower in file_name.lower() or not string
            ]
            
            # Return up to 25 options (Discord limit)
            return matching_files[:25]
        except Exception as e:
            self.logger.error(f"Error in file_name autocomplete: {e}", exc_info=True)
            return []  # Always return a list, even on error
    
    async def autocomplete_file_name_get(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for get file_name parameter"""
        return await self._autocomplete_file_name(inter, string)
    
    async def autocomplete_file_name_remove(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for remove file_name parameter"""
        return await self._autocomplete_file_name(inter, string)
    
    def _validate_file(self, attachment: disnake.Attachment) -> Optional[str]:
        """Validate file. Returns error message if invalid, None if valid"""
        if not attachment.filename.lower().endswith('.zip'):
            return "❌ Error: File must be a .zip file"
        
        if attachment.size > MAX_FILE_SIZE:
            size_mb = attachment.size / MEGABYTE
            return f"❌ Error: File size ({size_mb:.2f}MB) exceeds maximum allowed size ({MAX_FILE_SIZE / MEGABYTE:.0f}MB)"
        
        # Validate filename
        issues = []
        if len(attachment.filename) > 255:
            issues.append("Filename too long (max 255 characters)")
        
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*', '\\']
        found = [c for c in invalid_chars if c in attachment.filename]
        if found:
            issues.append(f"Contains invalid characters: {', '.join(found)}")
        
        if issues:
            warnings = "\n".join(f"• {issue}" for issue in issues)
            return f"⚠️ Warning: Filename may cause issues on some systems:\n{warnings}\n\nConsider renaming the file before uploading."
        
        return None
    
    def _create_file_embed(self, file_data: dict, color: disnake.Color = disnake.Color.green()) -> disnake.Embed:
        """Create a standard file embed"""
        embed = disnake.Embed(title=f"📦 {file_data.get('name')}", color=color)
        embed.add_field(name="Required By", value="🎅 A Secret Santa", inline=False)
        embed.add_field(
            name="Uploaded",
            value=f"<t:{int(file_data.get('uploaded_at', 0))}:F>",
            inline=False
        )
        return embed

    # ============ FILE BROWSER ============
    async def _handle_file_browser(self, inter: disnake.ApplicationCommandInteraction, action_type: str, handler_func):
        """Common file browser setup"""
        files = self.metadata.get("files", {})
        if not files:
            await inter.edit_original_response(content="📦 No files have been uploaded yet")
            return
        
        embed, browser_view = create_file_browser_view(FILES_DIR, self.metadata, action_type)
        if not browser_view:
            await inter.edit_original_response(embed=embed)
            return
        
        browser_view.selection_handler = handler_func
        await inter.edit_original_response(embed=embed, view=browser_view)

    # ============ DISTRIBUTION ============
    async def _get_distribution_targets(self, guild: disnake.Guild) -> Tuple[list, str]:
        """Get members to distribute to and distribution type"""
        # Check if Secret Santa event is active
        secret_santa_cog = self.bot.get_cog("SecretSantaCog")
        participant_ids = []
        
        if secret_santa_cog:
            try:
                state = secret_santa_cog.state
                event = state.get("current_event")
                if event and event.get("active"):
                    participants = event.get("participants", {})
                    if participants:
                        participant_ids = [int(uid) for uid in participants.keys() if uid.isdigit()]
                        self.logger.info(f"Using Secret Santa participants: {len(participant_ids)} participants")
            except Exception as e:
                self.logger.debug(f"Could not check Secret Santa state: {e}")
        
        # Get members to send to
        if participant_ids:
            members = []
            for user_id in participant_ids:
                try:
                    member = guild.get_member(user_id)
                    if member and not member.bot:
                        members.append(member)
                except Exception:
                    pass
            return members, "Secret Santa participants"
        else:
            members = [member for member in guild.members if not member.bot]
            return members, "all server members"

    async def _distribute_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_id: str,
        file_name: str,
        file_path: Path,
        required_by: disnake.Member
    ):
        """Distribute file to Secret Santa participants (if active) or all server members"""
        guild = inter.guild
        if not guild:
            await inter.followup.send("❌ Error: Command must be used in a server")
            return
        
        members, distribution_type = await self._get_distribution_targets(guild)
        
        if not members:
            await inter.followup.send("⚠️ No members found to send the file to")
            return
        
        # Create embed
        embed = disnake.Embed(
            title="📦 File Distribution",
            description=f"**{file_name}**",
            color=disnake.Color.green()
        )
        
        required_by_text = "🎅 A Secret Santa requires this file" if distribution_type == "Secret Santa participants" else "📋 A server member requires this file"
        embed.add_field(name="Required By", value=required_by_text, inline=False)
        embed.add_field(name="Uploaded At", value=f"<t:{int(time.time())}:F>", inline=False)
        embed.add_field(
            name="💻 Cross-Platform Compatible",
            value="✅ This ZIP file works on **Windows, Linux, and macOS**\n"
                  "The ZIP format is standardized and supported on all platforms.",
            inline=False
        )
        embed.set_footer(text=f"This file is required for {distribution_type}")
        
        # Send to all members
        successful = 0
        failed = 0
        
        async with self._sending_lock:
            for i, member in enumerate(members, 1):
                try:
                    # Skip uploader
                    if member.id == inter.author.id:
                        successful += 1
                        continue
                    
                    # Create file object for each member
                    file = disnake.File(file_path, filename=file_path.name)
                    
                    try:
                        await member.send(embed=embed, file=file)
                        successful += 1
                    except disnake.Forbidden:
                        failed += 1
                    except Exception as e:
                        failed += 1
                        self.logger.warning(f"Error sending to {member.display_name}: {e}")
                    
                    # Rate limiting
                    if i % 10 == 0:
                        await asyncio.sleep(1)
                    
                except Exception as e:
                    failed += 1
                    self.logger.error(f"Unexpected error sending to {member.display_name}: {e}")
        
        # Update download count
        if file_id in self.metadata["files"]:
            self.metadata["files"][file_id]["download_count"] = successful
            save_metadata(self.metadata)
        
        # Send summary
        summary_embed = disnake.Embed(
            title="📊 Distribution Complete",
            description=f"File '{file_name}' has been distributed to {distribution_type}",
            color=disnake.Color.blue()
        )
        summary_embed.add_field(name="✅ Successful", value=str(successful), inline=True)
        summary_embed.add_field(name="❌ Failed", value=str(failed), inline=True)
        summary_embed.add_field(name="📦 Total Recipients", value=str(len(members)), inline=True)
        if distribution_type == "Secret Santa participants":
            summary_embed.set_footer(text="Distributed to Secret Santa participants")
        
        await inter.followup.send(embed=summary_embed)

    # ============ COMMANDS ============
    @commands.slash_command(name="distributezip", description="Zip file distribution management")
    async def distributezip(self, inter: disnake.ApplicationCommandInteraction):
        """Main distributezip command group"""
        pass

    @distributezip.sub_command(name="upload", description="Upload a zip file and distribute it")
    async def upload_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        attachment: disnake.Attachment,
        required_by: disnake.Member = None
    ):
        """Upload a zip file and send it to Secret Santa participants (if active) or all server members"""
        await inter.response.defer()
        
        # Permission check
        if not is_owner(inter):
            owner_name = get_owner_mention()
            await inter.edit_original_response(
                content=f"❌ **Permission Denied**\n"
                       f"Only {owner_name} can upload files for distribution.\n"
                       f"\n"
                       f"💡 **Note:** This restriction only applies to file uploads.\n"
                       f"Secret Santa commands (`/ss ask_giftee`, `/ss reply_santa`, etc.) are **NOT affected** and work normally for all participants."
            )
            self.logger.warning(f"User {inter.author.name} ({inter.author.id}) attempted to upload file but is not authorized")
            return
        
        # Validate file
        validation_error = self._validate_file(attachment)
        if validation_error:
            await inter.edit_original_response(content=validation_error)
            return
        
        # Determine requester
        requester = required_by or inter.author
        file_name = Path(attachment.filename).stem
        
        try:
            # Download the file
            await inter.edit_original_response(content=f"📥 Downloading file '{file_name}'...")
            
            file_data = await attachment.read()
            file_path = FILES_DIR / attachment.filename
            
            # Save the file
            file_path.write_bytes(file_data)
            
            # Update metadata
            file_id = str(int(time.time()))
            self.metadata["files"][file_id] = {
                "name": file_name,
                "filename": attachment.filename,
                "uploaded_by": inter.author.id,
                "required_by": requester.id,
                "uploaded_at": time.time(),
                "size": attachment.size,
                "download_count": 0
            }
            
            self.metadata["history"].append({
                "file_id": file_id,
                "file_name": file_name,
                "uploaded_by": inter.author.id,
                "required_by": requester.id,
                "uploaded_at": time.time()
            })
            
            save_metadata(self.metadata)
            
            # Notify about upload success
            await inter.edit_original_response(
                content=f"✅ File '{file_name}' uploaded successfully!\n📤 Starting distribution..."
            )
            
            # Distribute to members
            await self._distribute_file(inter, file_id, file_name, file_path, requester)
            
        except Exception as e:
            self.logger.error(f"Error uploading file: {e}", exc_info=True)
            await inter.edit_original_response(content=f"❌ Error uploading file: {str(e)}")

    @distributezip.sub_command(name="list", description="List all uploaded files")
    async def list_files(self, inter: disnake.ApplicationCommandInteraction):
        """List all uploaded files"""
        await inter.response.defer()
        
        files = self.metadata.get("files", {})
        
        if not files:
            await inter.edit_original_response(content="📦 No files have been uploaded yet")
            return
        
        # Sort by upload time (newest first)
        sorted_files = sorted(
            files.items(),
            key=lambda x: x[1].get("uploaded_at", 0),
            reverse=True
        )
        
        embed = disnake.Embed(title="📦 Uploaded Files", color=disnake.Color.blue())
        
        for file_id, file_data in sorted_files[:10]:
            file_name = file_data.get("name", "Unknown")
            uploaded_at = file_data.get("uploaded_at", 0)
            size = file_data.get("size", 0)
            download_count = file_data.get("download_count", 0)
            
            embed.add_field(
                name=f"📦 {file_name}",
                value=(
                    f"Required by: 🎅 A Secret Santa\n"
                    f"Size: {size / 1024 / 1024:.2f} MB\n"
                    f"Sent to: {download_count} members\n"
                    f"Uploaded: <t:{int(uploaded_at)}:R>"
                ),
                inline=False
            )
        
        if len(sorted_files) > 10:
            embed.set_footer(text=f"Showing 10 of {len(sorted_files)} files")
        
        await inter.edit_original_response(embed=embed)
    
    @distributezip.sub_command(name="browse", description="Browse and select files using an interactive file browser")
    async def browse_files(self, inter: disnake.ApplicationCommandInteraction):
        """Browse files using an interactive file browser"""
        await inter.response.defer()
        
        async def handler(interaction, file_id, file_data, file_path):
            embed = disnake.Embed(title=f"📦 {file_data.get('name')}", color=disnake.Color.blue())
            embed.add_field(name="Size", value=f"{file_data.get('size', 0) / 1024 / 1024:.2f} MB", inline=True)
            embed.add_field(name="Required By", value="🎅 A Secret Santa", inline=True)
            embed.add_field(name="Uploaded", value=f"<t:{int(file_data.get('uploaded_at', 0))}:R>", inline=False)
            embed.set_footer(text="Use /distributezip get [file_name] to download this file")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        await self._handle_file_browser(inter, "browse", handler)

    @distributezip.sub_command(name="get", description="Get/download a file (use browse command for easier selection)")
    async def get_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str = commands.Param(default=None, description="File name (leave empty to use file browser)", autocomplete="autocomplete_file_name_get")
    ):
        """Get/download a specific file"""
        await inter.response.defer()
        
        if not file_name:
            async def handler(interaction, file_id, file_data, file_path):
                embed = self._create_file_embed(file_data)
                file = disnake.File(file_path, filename=file_data.get("filename"))
                await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
            
            await self._handle_file_browser(inter, "get", handler)
            return
        
        # Find file by name
        result = self._find_file_by_name(file_name)
        if not result:
            await inter.edit_original_response(
                content=f"❌ File '{file_name}' not found\n\n💡 Try `/distributezip get` (without file_name) to browse all files!"
            )
            return
        
        file_id, file_data = result
        file_path = FILES_DIR / file_data.get("filename")
        
        if not file_path.exists():
            await inter.edit_original_response(content="❌ File not found on disk")
            return
        
        embed = self._create_file_embed(file_data)
        file = disnake.File(file_path, filename=file_data.get("filename"))
        await inter.edit_original_response(embed=embed, file=file)

    @distributezip.sub_command(name="remove", description="Remove a file (moderator only, use browse for easier selection)")
    @mod_check()
    async def remove_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str = commands.Param(default=None, description="File name (leave empty to use file browser)", autocomplete="autocomplete_file_name_remove")
    ):
        """Remove a file (moderator only)"""
        await inter.response.defer()
        
        async def remove_handler(interaction, file_id, file_data, file_path):
            try:
                if file_path.exists():
                    file_path.unlink()
                del self.metadata["files"][file_id]
                save_metadata(self.metadata)
                await interaction.response.send_message(
                    f"✅ File '{file_data.get('name')}' has been removed", ephemeral=True
                )
            except Exception as e:
                self.logger.error(f"Error removing file: {e}", exc_info=True)
                await interaction.response.send_message(f"❌ Error removing file: {str(e)}", ephemeral=True)
        
        if not file_name:
            await self._handle_file_browser(inter, "remove", remove_handler)
            return
        
        # Find and remove file
        result = self._find_file_by_name(file_name)
        if not result:
            await inter.edit_original_response(
                content=f"❌ File '{file_name}' not found\n\n💡 Try `/distributezip remove` (without file_name) to browse all files!"
            )
            return
        
        file_id, file_data = result
        file_path = FILES_DIR / file_data.get("filename")
        
        try:
            if file_path.exists():
                file_path.unlink()
            del self.metadata["files"][file_id]
            save_metadata(self.metadata)
            await inter.edit_original_response(content=f"✅ File '{file_name}' has been removed")
        except Exception as e:
            self.logger.error(f"Error removing file: {e}", exc_info=True)
            await inter.edit_original_response(content=f"❌ Error removing file: {str(e)}")

    # ============ COG LIFECYCLE ============
    async def cog_load(self):
        """Initialize cog"""
        self.logger.info("DistributeZip cog loaded")
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("📦 DistributeZip cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog"""
        self.logger.info("Unloading DistributeZip cog...")
        save_metadata(self.metadata)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(DistributeZipCog(bot))
