"""
Zip File Distribution Cog

FEATURES:
- 📦 Upload and distribute zip files (e.g., Minecraft texture packs)
- 👤 Track who required the file
- 📨 Automatically send files to Secret Santa participants (if active) or all server members via DM
- 🔒 Permission checks (only authorized users can upload)
- 💾 Persistent storage of file metadata
- 💻 Cross-platform compatible (Windows, Linux, macOS)

CROSS-PLATFORM COMPATIBILITY:
- ZIP format is standardized and works on all operating systems
- Validates filenames to prevent issues with invalid characters
- Files can be extracted on Windows, Linux, and macOS without issues
- Minecraft texture packs work identically across all platforms

COMMANDS:
- /distributezip upload [attachment] [required_by] - Upload a zip file and distribute it
- /distributezip list - List all uploaded files
- /distributezip get [file_name] - Get a specific file
- /distributezip remove [file_name] - Remove a file (moderator only)

DATA STORAGE:
- distributed_files/ - Directory containing uploaded files
- distributed_files_metadata.json - Metadata about uploaded files
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import disnake
from disnake.ext import commands

from .owner_utils import owner_check, get_owner_mention, is_owner

# Paths
ROOT = Path(__file__).parent  # This is the 'cogs' directory
FILES_DIR = ROOT / "distributed_files"
METADATA_FILE = ROOT / "distributed_files_metadata.json"

# Ensure files directory exists
FILES_DIR.mkdir(exist_ok=True)

# Maximum file size (25MB - Discord's limit for attachments)
MAX_FILE_SIZE = 25 * 1024 * 1024


def load_metadata() -> Dict:
    """Load file metadata"""
    if METADATA_FILE.exists():
        try:
            return json.loads(METADATA_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_metadata(data: Dict):
    """Save file metadata"""
    try:
        METADATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except OSError as e:
        logging.getLogger("bot").error(f"Failed to save file metadata: {e}")


def mod_check():
    """Check if user is a moderator"""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        try:
            # Check if user has moderator role
            if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'DISCORD_MODERATOR_ROLE_ID'):
                role_id = inter.bot.config.DISCORD_MODERATOR_ROLE_ID
                if role_id:
                    member = inter.author
                    if isinstance(member, disnake.Member):
                        roles = [role.id for role in member.roles]
                        if role_id in roles:
                            return True
            
            # Fall back to administrator check
            if isinstance(inter.author, disnake.Member):
                return inter.author.guild_permissions.administrator
        except (AttributeError, TypeError):
            pass
        return False
    return commands.check(predicate)


class DistributeZipCog(commands.Cog):
    """Zip file distribution system"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("distributezip")
        self.metadata = load_metadata()
        self._sending_lock = asyncio.Lock()  # Prevent concurrent sends
        
        # Ensure metadata structure
        if "files" not in self.metadata:
            self.metadata["files"] = {}
        if "history" not in self.metadata:
            self.metadata["history"] = []
        
        self.logger.info("DistributeZip cog initialized")

    async def cog_load(self):
        """Initialize cog"""
        self.logger.info("DistributeZip cog loaded")
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("📦 DistributeZip cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog"""
        self.logger.info("Unloading DistributeZip cog...")
        # Save metadata on unload
        save_metadata(self.metadata)

    @commands.slash_command(
        name="distributezip",
        description="Zip file distribution management"
    )
    async def distributezip(self, inter: disnake.ApplicationCommandInteraction):
        """Main distributezip command group"""
        pass

    @distributezip.sub_command(
        name="upload",
        description="Upload a zip file and distribute it to Secret Santa participants or all members"
    )
    async def upload_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        attachment: disnake.Attachment,
        required_by: disnake.Member = None
    ):
        """
        Upload a zip file and send it to Secret Santa participants (if active) or all server members
        
        Parameters
        ----------
        attachment: The zip file to upload
        required_by: The user who requires this file (defaults to you)
        """
        await inter.response.defer()
        
        # PERMISSION CHECK: Only bot owner can upload files
        # This does NOT affect Secret Santa commands (ask_giftee, reply_santa, etc.)
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
        if not attachment.filename.lower().endswith('.zip'):
            await inter.edit_original_response(
                content="❌ Error: File must be a .zip file"
            )
            return
        
        if attachment.size > MAX_FILE_SIZE:
            await inter.edit_original_response(
                content=f"❌ Error: File size ({attachment.size / 1024 / 1024:.2f}MB) exceeds maximum allowed size (25MB)"
            )
            return
        
        # Validate filename for cross-platform compatibility
        filename_issues = []
        if len(attachment.filename) > 255:  # Max filename length on most systems
            filename_issues.append("Filename too long (max 255 characters)")
        
        # Check for problematic characters (Windows doesn't like: < > : " | ? * \)
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*', '\\']
        found_chars = [c for c in invalid_chars if c in attachment.filename]
        if found_chars:
            filename_issues.append(f"Contains invalid characters: {', '.join(found_chars)}")
        
        if filename_issues:
            await inter.edit_original_response(
                content=f"⚠️ Warning: Filename may cause issues on some systems:\n" +
                       "\n".join(f"• {issue}" for issue in filename_issues) +
                       "\n\nConsider renaming the file before uploading."
            )
            return
        
        # Determine who required it
        requester = required_by or inter.author
        file_name = Path(attachment.filename).stem  # Remove .zip extension
        
        try:
            # Download the file
            await inter.edit_original_response(
                content=f"📥 Downloading file '{file_name}'..."
            )
            
            file_data = await attachment.read()
            file_path = FILES_DIR / attachment.filename
            
            # Save the file
            file_path.write_bytes(file_data)
            
            # Update metadata
            file_id = str(int(time.time()))  # Use timestamp as ID
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
                content=f"✅ File '{file_name}' uploaded successfully!\n"
                       f"📤 Starting distribution..."
            )
            
            # Distribute to members
            await self._distribute_file(inter, file_id, file_name, file_path, requester)
            
        except Exception as e:
            self.logger.error(f"Error uploading file: {e}", exc_info=True)
            await inter.edit_original_response(
                content=f"❌ Error uploading file: {str(e)}"
            )

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
        
        # Check if Secret Santa event is active and get participants
        secret_santa_cog = self.bot.get_cog("SecretSantaCog")
        use_secret_santa = False
        participant_ids = []
        
        if secret_santa_cog:
            try:
                state = secret_santa_cog.state
                event = state.get("current_event")
                if event and event.get("active"):
                    participants = event.get("participants", {})
                    if participants:
                        # Get participant user IDs (they're stored as strings in the dict keys)
                        participant_ids = [int(uid) for uid in participants.keys() if uid.isdigit()]
                        use_secret_santa = True
                        self.logger.info(f"Using Secret Santa participants: {len(participant_ids)} participants")
            except Exception as e:
                self.logger.debug(f"Could not check Secret Santa state: {e}")
        
        # Get members to send to
        if use_secret_santa and participant_ids:
            # Get Secret Santa participants
            members = []
            for user_id in participant_ids:
                try:
                    member = guild.get_member(user_id)
                    if member and not member.bot:
                        members.append(member)
                except Exception as e:
                    self.logger.debug(f"Could not get member {user_id}: {e}")
            
            distribution_type = "Secret Santa participants"
        else:
            # Get all server members
            members = [member for member in guild.members if not member.bot]
            distribution_type = "all server members"
        
        total_members = len(members)
        
        if total_members == 0:
            await inter.followup.send("⚠️ No members found to send the file to")
            return
        
        # Create embed
        embed = disnake.Embed(
            title="📦 File Distribution",
            description=f"**{file_name}**",
            color=disnake.Color.green()
        )
        embed.add_field(
            name="Required By",
            value=f"{required_by.mention} ({required_by.display_name})",
            inline=False
        )
        embed.add_field(
            name="Uploaded At",
            value=f"<t:{int(time.time())}:F>",
            inline=False
        )
        embed.add_field(
            name="💻 Cross-Platform Compatible",
            value="✅ This ZIP file works on **Windows, Linux, and macOS**\n"
                  "The ZIP format is standardized and supported on all platforms.",
            inline=False
        )
        if use_secret_santa:
            embed.set_footer(text="This file is required for Secret Santa participants")
        else:
            embed.set_footer(text="This file is required for the server")
        
        # Send to all members
        successful = 0
        failed = 0
        
        async with self._sending_lock:
            for i, member in enumerate(members, 1):
                try:
                    # Skip if member is the uploader (they already have it)
                    if member.id == inter.author.id:
                        successful += 1
                        continue
                    
                    # Create a new file object for each member (Discord file objects can only be used once)
                    file = disnake.File(file_path, filename=file_path.name)
                    
                    # Try to send DM
                    try:
                        await member.send(embed=embed, file=file)
                        successful += 1
                        self.logger.debug(f"Sent file to {member.display_name} ({member.id})")
                    except disnake.Forbidden:
                        # User has DMs disabled
                        failed += 1
                        self.logger.debug(f"Could not send DM to {member.display_name} (DMs disabled)")
                    except Exception as e:
                        failed += 1
                        self.logger.warning(f"Error sending to {member.display_name}: {e}")
                    
                    # Rate limiting - wait a bit between sends
                    if i % 10 == 0:
                        await asyncio.sleep(1)  # Small delay every 10 sends
                    
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
        summary_embed.add_field(name="📦 Total Recipients", value=str(total_members), inline=True)
        if use_secret_santa:
            summary_embed.set_footer(text="Distributed to Secret Santa participants")
        
        await inter.followup.send(embed=summary_embed)

    @distributezip.sub_command(
        name="list",
        description="List all uploaded files"
    )
    async def list_files(self, inter: disnake.ApplicationCommandInteraction):
        """List all uploaded files"""
        await inter.response.defer()
        
        files = self.metadata.get("files", {})
        
        if not files:
            await inter.edit_original_response(
                content="📦 No files have been uploaded yet"
            )
            return
        
        # Sort by upload time (newest first)
        sorted_files = sorted(
            files.items(),
            key=lambda x: x[1].get("uploaded_at", 0),
            reverse=True
        )
        
        embed = disnake.Embed(
            title="📦 Uploaded Files",
            color=disnake.Color.blue()
        )
        
        for file_id, file_data in sorted_files[:10]:  # Show top 10
            file_name = file_data.get("name", "Unknown")
            uploaded_at = file_data.get("uploaded_at", 0)
            size = file_data.get("size", 0)
            download_count = file_data.get("download_count", 0)
            
            # Get user info
            required_by_id = file_data.get("required_by")
            required_by_text = "Unknown"
            if required_by_id:
                try:
                    user = await self.bot.fetch_user(required_by_id)
                    required_by_text = user.display_name
                except:
                    required_by_text = f"User ID: {required_by_id}"
            
            embed.add_field(
                name=f"📦 {file_name}",
                value=(
                    f"Required by: {required_by_text}\n"
                    f"Size: {size / 1024 / 1024:.2f} MB\n"
                    f"Sent to: {download_count} members\n"
                    f"Uploaded: <t:{int(uploaded_at)}:R>"
                ),
                inline=False
            )
        
        if len(sorted_files) > 10:
            embed.set_footer(text=f"Showing 10 of {len(sorted_files)} files")
        
        await inter.edit_original_response(embed=embed)

    @distributezip.sub_command(
        name="get",
        description="Get a specific file"
    )
    async def get_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str
    ):
        """Get a specific file by name"""
        await inter.response.defer()
        
        files = self.metadata.get("files", {})
        
        # Find file by name
        file_id = None
        file_data = None
        for fid, data in files.items():
            if data.get("name", "").lower() == file_name.lower():
                file_id = fid
                file_data = data
                break
        
        if not file_data:
            await inter.edit_original_response(
                content=f"❌ File '{file_name}' not found"
            )
            return
        
        # Get file path
        filename = file_data.get("filename")
        file_path = FILES_DIR / filename
        
        if not file_path.exists():
            await inter.edit_original_response(
                content=f"❌ File not found on disk"
            )
            return
        
        # Create embed
        required_by_id = file_data.get("required_by")
        required_by_text = "Unknown"
        if required_by_id:
            try:
                user = await self.bot.fetch_user(required_by_id)
                required_by_text = user.mention
            except:
                required_by_text = f"User ID: {required_by_id}"
        
        embed = disnake.Embed(
            title=f"📦 {file_data.get('name')}",
            color=disnake.Color.green()
        )
        embed.add_field(name="Required By", value=required_by_text, inline=False)
        embed.add_field(
            name="Uploaded",
            value=f"<t:{int(file_data.get('uploaded_at', 0))}:F>",
            inline=False
        )
        
        # Send file
        file = disnake.File(file_path, filename=filename)
        await inter.edit_original_response(embed=embed, file=file)

    @distributezip.sub_command(
        name="remove",
        description="Remove a file (moderator only)"
    )
    @mod_check()
    async def remove_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str
    ):
        """Remove a file"""
        await inter.response.defer()
        
        files = self.metadata.get("files", {})
        
        # Find file by name
        file_id = None
        file_data = None
        for fid, data in files.items():
            if data.get("name", "").lower() == file_name.lower():
                file_id = fid
                file_data = data
                break
        
        if not file_data:
            await inter.edit_original_response(
                content=f"❌ File '{file_name}' not found"
            )
            return
        
        # Delete file
        filename = file_data.get("filename")
        file_path = FILES_DIR / filename
        
        try:
            if file_path.exists():
                file_path.unlink()
            
            # Remove from metadata
            del self.metadata["files"][file_id]
            save_metadata(self.metadata)
            
            await inter.edit_original_response(
                content=f"✅ File '{file_name}' has been removed"
            )
        except Exception as e:
            self.logger.error(f"Error removing file: {e}", exc_info=True)
            await inter.edit_original_response(
                content=f"❌ Error removing file: {str(e)}"
            )


def setup(bot):
    bot.add_cog(DistributeZipCog(bot))

