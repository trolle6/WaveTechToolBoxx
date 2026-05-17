"""
File Distribution Cog

FEATURES:
- 📦 Upload and distribute files (any type: ZIP, JAR, RAR, etc. - up to 25MB)
- 👤 Track who required the file
- 📨 Automatically send files to Secret Santa participants (if active) or all server members via DM
- 🔒 Upload: active Secret Santa participants; remove: mods/admins
- 💾 Persistent storage of file metadata with atomic writes
- 💻 Cross-platform compatible (Windows, Linux, macOS)
- ⚡ Non-blocking file I/O operations (ThreadPoolExecutor)
- 🎯 Sophisticated error handling (distinguishes Forbidden, HTTPException, etc.)
- 📊 Progress updates for large distributions
- 🚦 Improved rate limiting (Discord API compliant)

COMMANDS:
- /distribute upload [attachment] [required_by] - Upload and DM files (SS participants)
- /distribute list / browse / get - Download shared files (SS participants)
- /distribute remove - Remove a file (moderator only)

DESIGN DECISIONS:
- ThreadPoolExecutor: All file I/O operations run in executor to avoid blocking event loop
- Atomic writes: Uses write-temp-replace pattern to prevent corruption on crashes
- Error handling: Matches SecretSanta_cog patterns for consistency
- Rate limiting: Respects Discord's DM rate limits (5 per 5 seconds per user)
- Progress updates: Shows progress for distributions with 20+ recipients
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import disnake
from disnake.ext import commands

from .secret_santa_checks import mod_check
from .distributezip_file_browser import create_file_browser_view, FileBrowserSelectView
from .secret_santa_views import FileListPaginator
from .utils import autocomplete_safety_wrapper, safe_filename_in_dir

# Paths
ROOT = Path(__file__).parent
FILES_DIR = ROOT / "distributed_files"
METADATA_FILE = ROOT / "distributed_files_metadata.json"

# Ensure files directory exists
FILES_DIR.mkdir(exist_ok=True)

# File size limits and configuration
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB - Discord's limit for attachments
MEGABYTE = 1024 * 1024  # Bytes in one megabyte (for size formatting)

# Network and timeout configuration
FILE_SEND_TIMEOUT = 120  # 2 minutes - timeout for sending files via DM (large files need time)
FILE_SEND_RETRY_DELAY = 2  # Seconds to wait before retry on transient errors
MAX_RETRIES = 2  # Maximum retries for transient network errors


def load_metadata() -> Dict:
    """Load file metadata (synchronous - call from executor). Always returns a dict."""
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def save_metadata(data: Dict, logger=None):
    """
    Save file metadata atomically (synchronous - call from executor).
    
    Uses write-temp-replace pattern to ensure atomic writes:
    writes to temporary file first, then replaces original.
    This prevents corruption if process crashes during write.
    """
    if data is None or not isinstance(data, dict):
        if logger:
            logger.warning("save_metadata: data is None or not a dict, skipping save")
        return
    temp = METADATA_FILE.with_suffix('.tmp')
    try:
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        # Atomic replace - on Unix/Linux this is guaranteed atomic
        # On Windows, this is the best we can do without fsync
        temp.replace(METADATA_FILE)
    except Exception as e:
        # Clean up temp file on error
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass
        if logger:
            logger.error(f"Failed to save file metadata to {METADATA_FILE}: {e}")
        raise


class DistributeZipCog(commands.Cog):
    """File distribution system - supports any file type up to Discord's 25MB limit"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("distributezip")
        
        # Load metadata synchronously during init (acceptable for startup)
        self.metadata = load_metadata()
        if not isinstance(self.metadata, dict):
            self.metadata = {}
        self._sending_lock = asyncio.Lock()
        self._metadata_lock = asyncio.Lock()
        self._executor = bot.executor  # Shared executor from main.py (bot is self.bot)
        
        # Ensure metadata structure (normalize in case file had null or wrong types)
        if not isinstance(self.metadata.get("files"), dict):
            self.metadata["files"] = {}
        if not isinstance(self.metadata.get("history"), list):
            self.metadata["history"] = []
        
        # Migration: Mark history entries as deleted if file no longer exists
        # This handles cases where files were deleted before the status field was added
        files_dict = self.metadata.get("files", {})
        for history_entry in self.metadata.get("history", []):
            file_id = history_entry.get("file_id")
            # If file_id is in history but not in files, mark as deleted
            if file_id and file_id not in files_dict:
                if "status" not in history_entry:
                    history_entry["status"] = "*deleted*"
                    self.logger.debug(f"Marked history entry {file_id} as deleted (migration)")
        
        self.logger.info("DistributeZip cog initialized")

    # ============ FILE UTILITIES ============
    def _find_file_by_name(self, file_name: str) -> Optional[Tuple[str, dict]]:
        """Find file by name (case-insensitive)"""
        if not file_name or not isinstance(file_name, str) or not file_name.strip():
            return None
        files = self.metadata.get("files") or {}
        if not isinstance(files, dict):
            return None
        file_name_lower = file_name.strip().lower()
        for fid, data in files.items():
            if data.get("name", "").lower() == file_name_lower:
                return (fid, data)
        return None
    
    def _get_available_files(self) -> List[str]:
        """Get list of available file names"""
        files = self.metadata.get("files") or {}
        if not isinstance(files, dict):
            return []
        return sorted([
            (data.get("name") or "").strip()
            for data in files.values()
            if isinstance(data, dict) and (data.get("name") or "").strip()
        ])
    
    # ============ SAFE DISCORD API WRAPPERS ============
    async def _safe_edit_response(
        self,
        inter: disnake.ApplicationCommandInteraction,
        content: Optional[str] = None,
        embed: Optional[disnake.Embed] = None,
        view: Optional[disnake.ui.View] = None,
        file: Optional[disnake.File] = None,
        max_retries: int = 3
    ) -> bool:
        """Safely edit interaction response with retry logic for Discord connection issues"""
        for attempt in range(max_retries):
            try:
                # Build kwargs - only include file if it's not None (disnake doesn't handle None files well)
                kwargs = {}
                if content is not None:
                    kwargs['content'] = content
                if embed is not None:
                    kwargs['embed'] = embed
                if view is not None:
                    kwargs['view'] = view
                if file is not None:
                    kwargs['file'] = file
                
                await asyncio.wait_for(
                    inter.edit_original_response(**kwargs),
                    timeout=10.0
                )
                return True
            except disnake.errors.NotFound:
                self.logger.warning(f"Interaction expired before edit: {inter.id}")
                return False
            except disnake.errors.InteractionResponded:
                return True
            except disnake.HTTPException as e:
                status = getattr(e, 'status', None)
                if status == 429:
                    retry_after = getattr(e, 'retry_after', 1.0)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                elif status and status >= 500:
                    if attempt < max_retries - 1:
                        wait_time = min(2 ** attempt, 5.0)
                        await asyncio.sleep(wait_time)
                        continue
                else:
                    self.logger.error(f"HTTP error {status} on edit_response: {e}")
                    return False
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 5.0)
                    self.logger.warning(f"Connection error on edit_response, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"Connection error on edit_response after {max_retries} attempts: {e}")
                    return False
            except Exception as e:
                self.logger.error(f"Unexpected error on edit_response: {e}", exc_info=True)
                return False
        return False
    
    async def _safe_followup_send(
        self,
        inter: disnake.ApplicationCommandInteraction,
        content: Optional[str] = None,
        embed: Optional[disnake.Embed] = None,
        view: Optional[disnake.ui.View] = None,
        file: Optional[disnake.File] = None,
        ephemeral: bool = False,
        max_retries: int = 3
    ) -> Optional[disnake.WebhookMessage]:
        """Safely send followup message with retry logic for Discord connection issues"""
        for attempt in range(max_retries):
            try:
                # Build kwargs - only include file if it's not None (disnake doesn't handle None files well)
                kwargs = {'ephemeral': ephemeral}
                if content is not None:
                    kwargs['content'] = content
                if embed is not None:
                    kwargs['embed'] = embed
                if view is not None:
                    kwargs['view'] = view
                if file is not None:
                    kwargs['file'] = file
                
                msg = await asyncio.wait_for(
                    inter.followup.send(**kwargs),
                    timeout=10.0
                )
                return msg
            except disnake.errors.NotFound:
                self.logger.warning(f"Interaction expired before followup: {inter.id}")
                return None
            except disnake.HTTPException as e:
                status = getattr(e, 'status', None)
                if status == 429:
                    retry_after = getattr(e, 'retry_after', 1.0)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                elif status and status >= 500:
                    if attempt < max_retries - 1:
                        wait_time = min(2 ** attempt, 5.0)
                        await asyncio.sleep(wait_time)
                        continue
                else:
                    self.logger.error(f"HTTP error {status} on followup_send: {e}")
                    return None
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 5.0)
                    self.logger.warning(f"Connection error on followup_send, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    self.logger.error(f"Connection error on followup_send after {max_retries} attempts: {e}")
                    return None
            except Exception as e:
                self.logger.error(f"Unexpected error on followup_send: {e}", exc_info=True)
                return None
        return None
    
    # ============ ASYNC FILE I/O ============
    async def _save_metadata_async(self):
        """Save metadata asynchronously (non-blocking)"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, save_metadata, self.metadata, self.logger)
    
    @autocomplete_safety_wrapper
    async def _autocomplete_file_name(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for file_name selection."""
        available_files = self._get_available_files()
        if not available_files:
            return []
        string_lower = (string or "").lower()
        return [
            fn for fn in available_files
            if string_lower in fn.lower() or not string
        ][:25]

    async def autocomplete_file_name_get(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for get file_name parameter."""
        return await self._autocomplete_file_name(inter, string)

    async def autocomplete_file_name_remove(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for remove file_name parameter."""
        return await self._autocomplete_file_name(inter, string)
    
    async def _require_participant(self, inter: disnake.ApplicationCommandInteraction) -> bool:
        """Active Secret Santa participant check (call after defer)."""
        cog = self.bot.get_cog("SecretSantaCog")
        if not cog:
            await self._safe_edit_response(
                inter, content="❌ Secret Santa is not loaded — cannot verify participant status."
            )
            return False
        event = getattr(cog, "state", {}).get("current_event")
        if not event or not isinstance(event, dict) or not event.get("active"):
            await self._safe_edit_response(
                inter,
                content="❌ No active Secret Santa event — only participants can use this command.",
            )
            return False
        participants = event.get("participants") or {}
        if not isinstance(participants, dict) or str(inter.author.id) not in participants:
            await self._safe_edit_response(
                inter,
                content="❌ You must be signed up for the active Secret Santa event to use this command.",
            )
            return False
        return True

    def _validate_file(self, attachment: disnake.Attachment) -> Optional[str]:
        """Validate file. Returns error message if invalid, None if valid"""
        if not attachment:
            return "❌ No attachment provided"
        raw_name = attachment.filename or ""
        if not raw_name.strip() or Path(raw_name).name != raw_name or ".." in Path(raw_name).parts:
            return "❌ Invalid filename (path characters not allowed)"
        if safe_filename_in_dir(raw_name, FILES_DIR) is None:
            return "❌ Invalid filename"
        # Allow any file type - Discord handles file distribution regardless of format
        # Size check is the main concern (Discord's 25MB limit)
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
    
    async def _send_uploader_summary(
        self,
        uploader: disnake.User,
        file_name: str,
        successful_recipients: List[disnake.Member],
        successful_count: int,
        failed_count: int,
        forbidden_count: int,
        total_count: int,
        distribution_type: str
    ):
        """
        Send a summary DM to the uploader with details about who received the file.
        Handles cases where uploader has DMs disabled gracefully.
        """
        try:
            # Create summary embed
            summary_embed = disnake.Embed(
                title="📦 File Distribution Summary",
                description=f"Your file **{file_name}** has been distributed!",
                color=disnake.Color.green()
            )
            
            summary_embed.add_field(
                name="✅ Successfully Sent",
                value=f"{successful_count} member(s)",
                inline=True
            )
            
            if failed_count > 0:
                summary_embed.add_field(
                    name="❌ Failed",
                    value=f"{failed_count} member(s)",
                    inline=True
                )
            
            summary_embed.add_field(
                name="📊 Total Recipients",
                value=f"{total_count} member(s)",
                inline=True
            )
            
            # Add notes about failures
            notes = []
            if forbidden_count > 0:
                notes.append(f"{forbidden_count} member(s) have DMs disabled")
            if failed_count > 0:
                notes.append(f"{failed_count} member(s) failed to receive (file may be too large or other error)")
            
            if notes:
                summary_embed.add_field(
                    name="ℹ️ Note",
                    value="\n".join(notes),
                    inline=False
                )
            
            # Add list of recipients (limit to first 20 to avoid embed size limits)
            if successful_recipients:
                if len(successful_recipients) <= 20:
                    recipient_list = "\n".join([f"• {member.mention}" for member in successful_recipients])
                else:
                    recipient_list = "\n".join([f"• {member.mention}" for member in successful_recipients[:20]])
                    recipient_list += f"\n\n... and {len(successful_recipients) - 20} more"
                
                summary_embed.add_field(
                    name="👥 Recipients",
                    value=recipient_list or "None",
                    inline=False
                )
            
            summary_embed.set_footer(text=f"Distributed to {distribution_type}")
            
            # Try to send DM to uploader (works with both User and Member - both have .send())
            try:
                # Both User and Member objects support .send() directly in disnake
                display_name = uploader.display_name if isinstance(uploader, disnake.Member) else uploader.name
                await uploader.send(embed=summary_embed)
                self.logger.debug(f"Sent distribution summary DM to uploader {uploader.id} ({display_name})")
            except disnake.Forbidden:
                # Uploader has DMs disabled - log but don't fail
                display_name = uploader.display_name if isinstance(uploader, disnake.Member) else uploader.name
                self.logger.debug(f"Could not send summary DM to uploader {uploader.id} ({display_name}) - DMs disabled")
            except Exception as e:
                # Other errors - log but don't fail
                display_name = uploader.display_name if isinstance(uploader, disnake.Member) else uploader.name
                self.logger.warning(f"Error sending summary DM to uploader {uploader.id} ({display_name}): {e}")
        
        except Exception as e:
            # Don't fail distribution if summary DM fails
            self.logger.error(f"Error creating/sending uploader summary: {e}", exc_info=True)
    
    def _create_file_embed(self, file_data: dict, color: disnake.Color = disnake.Color.green()) -> disnake.Embed:
        """Create a standard file embed (handles missing/None fields)"""
        name = file_data.get("name") if isinstance(file_data, dict) else None
        embed = disnake.Embed(title=f"📦 {name or 'Unknown'}", color=color)
        embed.add_field(name="Required By", value="🎅 A Secret Santa", inline=False)
        uploaded_at = file_data.get("uploaded_at", 0) if isinstance(file_data, dict) else 0
        embed.add_field(
            name="Uploaded",
            value=f"<t:{int(uploaded_at) if uploaded_at else 0}:F>",
            inline=False
        )
        return embed

    # ============ FILE BROWSER ============
    async def _handle_file_browser(self, inter: disnake.ApplicationCommandInteraction, action_type: str, handler_func):
        """Common file browser setup"""
        files = self.metadata.get("files") or {}
        if not isinstance(files, dict) or not files:
            await self._safe_edit_response(inter, content="📦 No files have been uploaded yet")
            return
        
        embed, browser_view = create_file_browser_view(FILES_DIR, self.metadata, action_type)
        if not browser_view:
            await self._safe_edit_response(inter, embed=embed)
            return
        
        browser_view.selection_handler = handler_func
        await self._safe_edit_response(inter, embed=embed, view=browser_view)

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
        self.logger.warning(
            "Distribute: no active Secret Santa participants — refusing guild-wide DM blast"
        )
        return [], "no eligible recipients"

    async def _distribute_uploaded_files(
        self,
        inter: disnake.ApplicationCommandInteraction,
        successful_uploads: List[Dict[str, Any]],
        requester_user: disnake.User,
        *,
        status_message: Optional[str] = None,
    ) -> None:
        """Distribute saved uploads, or explain when DM/server context is missing."""
        if not successful_uploads:
            return
        if not inter.guild:
            lines = "\n".join(f"• {u['file_name']}" for u in successful_uploads)
            await self._safe_edit_response(
                inter,
                content=(
                    f"✅ **File(s) saved** ({len(successful_uploads)})\n{lines}\n\n"
                    "📤 To distribute, run upload again in a **server channel** "
                    "(the bot needs a server to know who receives the file)."
                ),
            )
            return
        if status_message:
            await self._safe_edit_response(inter, content=status_message)
        for index, file_info in enumerate(successful_uploads):
            await self._distribute_file(
                inter,
                file_info["file_id"],
                file_info["file_name"],
                file_info["file_path"],
                requester_user,
            )
            if index + 1 < len(successful_uploads):
                await asyncio.sleep(1)

    async def _distribute_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_id: str,
        file_name: str,
        file_path: Path,
        required_by: disnake.User
    ):
        """
        Distribute file to Secret Santa participants (if active) or all server members.
        Works in both server and DM contexts.
        
        Features:
        - Non-blocking file I/O for metadata updates
        - Sophisticated error handling (distinguishes Forbidden, HTTPException, etc.)
        - Improved rate limiting (Discord-friendly)
        - Progress updates for large distributions
        """
        if not file_path or not file_path.exists():
            await self._safe_followup_send(
                inter,
                content="❌ File no longer found on disk. It may have been removed.",
                ephemeral=True
            )
            return
        guild = inter.guild
        if not guild:
            await self._safe_followup_send(
                inter,
                content="❌ **Cannot distribute from a DM**\n\n"
                        "✅ The file is saved on the bot.\n"
                        "📤 Run the upload command again **in a server channel** to distribute to members.",
                ephemeral=True,
            )
            return
        
        # Get requester as Member if possible (for DM context, use guild.get_member)
        # required_by is a User object (from upload_file), try to get Member from guild
        requester_member = None
        if isinstance(required_by, disnake.Member):
            requester_member = required_by
        elif guild:
            # Try to get member from guild
            requester_member = guild.get_member(required_by.id)
        
        # Fallback: use author as requester
        if not requester_member and guild:
            requester_member = guild.get_member(inter.author.id)
        
        # If still no member (DM with no guild), use None (we'll handle in embed)
        
        members, distribution_type = await self._get_distribution_targets(guild)
        
        if not members:
            await self._safe_followup_send(inter, content="⚠️ No members found to send the file to", ephemeral=True)
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
        embed.set_footer(text=f"This file is required for {distribution_type}")
        
        # Send to all members with improved error handling and rate limiting
        successful = 0
        failed = 0
        forbidden_count = 0  # Users with DMs disabled
        successful_recipients = []  # Track who successfully received the file
        
        # Exclude uploader from total count (they get summary DM instead of file)
        members_to_send = [m for m in members if m.id != inter.author.id]
        total_members = len(members_to_send)
        show_progress = total_members > 20  # Show progress for large distributions
        
        async with self._sending_lock:
            for i, member in enumerate(members_to_send, 1):
                try:
                    
                    # Create file object for each member
                    file = disnake.File(file_path, filename=file_path.name)
                    
                    # Send with timeout and retry logic for connection resilience
                    send_success = False
                    for retry_attempt in range(MAX_RETRIES + 1):
                        try:
                            # Wrap send in timeout to handle connection hiccups
                            await asyncio.wait_for(
                                member.send(embed=embed, file=file),
                                timeout=FILE_SEND_TIMEOUT
                            )
                            successful += 1
                            successful_recipients.append(member)  # Track successful recipient
                            send_success = True
                            break  # Success, exit retry loop
                            
                        except asyncio.TimeoutError:
                            # Connection timeout - file might be too large or network slow
                            if retry_attempt < MAX_RETRIES:
                                self.logger.warning(
                                    f"Timeout sending to {member.id} ({member.display_name}), "
                                    f"retry {retry_attempt + 1}/{MAX_RETRIES} after {FILE_SEND_RETRY_DELAY}s"
                                )
                                await asyncio.sleep(FILE_SEND_RETRY_DELAY)
                                continue
                            else:
                                self.logger.error(
                                    f"Timeout sending to {member.id} ({member.display_name}) "
                                    f"after {MAX_RETRIES + 1} attempts (file may be too large or network issue)"
                                )
                                failed += 1
                                break
                                
                        except disnake.Forbidden as e:
                            # User has DMs disabled or blocked the bot (error code 50007)
                            # This is expected and common - only log at debug level
                            # No retry needed for Forbidden errors
                            error_code = getattr(e, 'code', None)
                            if error_code == 50007:
                                self.logger.debug(f"User {member.id} ({member.display_name}) has DMs disabled (50007) - skipping DM")
                            else:
                                self.logger.debug(f"User {member.id} ({member.display_name}) blocked DM (Forbidden: {error_code})")
                            failed += 1
                            forbidden_count += 1
                            break  # Don't retry Forbidden errors
                            
                        except disnake.HTTPException as e:
                            # HTTP errors - check if retryable (5xx server errors, 429 rate limits)
                            status = getattr(e, 'status', None)
                            
                            # 413 Payload Too Large - file is too big for Discord DMs (don't retry)
                            if status == 413:
                                self.logger.warning(
                                    f"File too large to send via DM to {member.id} ({member.display_name}): "
                                    f"Discord rejected file (413 Payload Too Large). File size: {file_path.stat().st_size / (1024*1024):.2f} MB"
                                )
                                failed += 1
                                break
                            
                            is_retryable = status and (status >= 500 or status == 429)
                            
                            if is_retryable and retry_attempt < MAX_RETRIES:
                                # Retry on server errors or rate limits
                                retry_after = getattr(e, 'retry_after', FILE_SEND_RETRY_DELAY)
                                self.logger.warning(
                                    f"HTTP {status} error sending to {member.id} ({member.display_name}), "
                                    f"retry {retry_attempt + 1}/{MAX_RETRIES} after {retry_after}s"
                                )
                                await asyncio.sleep(retry_after)
                                continue
                            else:
                                # Non-retryable HTTP error or max retries reached
                                self.logger.warning(
                                    f"HTTP error sending DM to {member.id} ({member.display_name}): {e} "
                                    f"(status: {status})"
                                )
                                failed += 1
                                break
                                
                        except (ConnectionError, OSError) as e:
                            # Network connection errors - retry on transient issues
                            if retry_attempt < MAX_RETRIES:
                                self.logger.warning(
                                    f"Connection error sending to {member.id} ({member.display_name}), "
                                    f"retry {retry_attempt + 1}/{MAX_RETRIES} after {FILE_SEND_RETRY_DELAY}s: {e}"
                                )
                                await asyncio.sleep(FILE_SEND_RETRY_DELAY)
                                continue
                            else:
                                self.logger.error(
                                    f"Connection error sending to {member.id} ({member.display_name}) "
                                    f"after {MAX_RETRIES + 1} attempts: {e}"
                                )
                                failed += 1
                                break
                                
                        except Exception as e:
                            # Unexpected errors - log and fail (don't retry unknown errors)
                            self.logger.warning(
                                f"Unexpected error sending DM to {member.id} ({member.display_name}): {e}"
                            )
                            failed += 1
                            break
                    
                    # Improved rate limiting - Discord allows 5 DMs per 5 seconds per user
                    # We're sending to different users, so we can be more aggressive
                    # But still respect overall rate limits
                    if i % 5 == 0:
                        await asyncio.sleep(0.5)  # Small delay every 5 messages
                    elif i % 20 == 0:
                        await asyncio.sleep(1)  # Longer delay every 20 messages
                    
                    # Progress updates for large distributions
                    if show_progress and i % 25 == 0:
                        progress_msg = (
                            f"📤 Distributing... {i}/{total_members} members "
                            f"({successful} successful, {failed} failed)"
                        )
                        try:
                            await self._safe_followup_send(inter, content=progress_msg, ephemeral=True)
                        except Exception:
                            pass  # Don't fail distribution if progress update fails
                    
                except Exception as e:
                    failed += 1
                    self.logger.error(f"Unexpected error processing member {member.id} ({member.display_name}): {e}", exc_info=True)
        
        # Update download count (inside metadata lock to prevent races)
        async with self._metadata_lock:
            if file_id in self.metadata["files"]:
                self.metadata["files"][file_id]["download_count"] = successful
                try:
                    await self._save_metadata_async()
                except Exception as e:
                    self.logger.error(f"Failed to update download count: {e}")
        
        # Send summary with detailed statistics
        summary_embed = disnake.Embed(
            title="📊 Distribution Complete",
            description=f"File '{file_name}' has been distributed to {distribution_type}",
            color=disnake.Color.blue()
        )
        summary_embed.add_field(name="✅ Successful", value=str(successful), inline=True)
        summary_embed.add_field(name="❌ Failed", value=str(failed), inline=True)
        summary_embed.add_field(name="📦 Total Recipients", value=str(total_members), inline=True)
        
        # Add notes about failures
        notes = []
        if forbidden_count > 0:
            notes.append(f"{forbidden_count} member(s) have DMs disabled")
        if failed > 0:
            notes.append(f"{failed} member(s) failed to receive (file may be too large for Discord DMs)")
        
        if notes:
            summary_embed.add_field(
                name="ℹ️ Note",
                value="\n".join(notes),
                inline=False
            )
        
        if distribution_type == "Secret Santa participants":
            summary_embed.set_footer(text="Distributed to Secret Santa participants")
        else:
            summary_embed.set_footer(text="Distributed to all server members")
        
        await self._safe_followup_send(inter, embed=summary_embed, ephemeral=True)
        
        # Send summary DM to uploader
        await self._send_uploader_summary(
            uploader=inter.author,
            file_name=file_name,
            successful_recipients=successful_recipients,
            successful_count=successful,
            failed_count=failed,
            forbidden_count=forbidden_count,
            total_count=total_members,
            distribution_type=distribution_type
        )

    # ============ COMMANDS ============
    @commands.slash_command(name="distribute", description="Share files with Secret Santa participants")
    async def distribute_root(self, inter: disnake.ApplicationCommandInteraction):
        """File distribution for active SS participants"""
        pass

    @distribute_root.sub_command(name="upload", description="Upload file(s) and distribute them (any file type, up to 25MB)")
    async def upload_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        attachment: disnake.Attachment = commands.Param(default=None, description="File to upload (can attach multiple in Discord)"),
        required_by: Optional[disnake.Member] = commands.Param(default=None, description="Optional: Who requires this file (works in DMs too)")
    ):
        """
        Upload one or more files (any type) and send them to Secret Santa participants (if active) or all server members.
        
        Supports any file type (ZIP, JAR, RAR, etc.) up to Discord's 25MB limit.
        You can attach multiple files in Discord - the bot will process all of them!
        """
        # Defer with ephemeral for privacy (only uploader sees the response)
        await inter.response.defer(ephemeral=True)
        if not await self._require_participant(inter):
            return

        # Get all attachments - Discord allows attaching multiple files to slash commands
        attachments = []
        
        # Primary: Check if attachment parameter was provided
        if attachment:
            attachments.append(attachment)
        
        # Secondary: Check for additional attachments in the interaction
        # Discord stores all attachments in the interaction's resolved data
        try:
            # Check interaction data for resolved attachments
            if hasattr(inter, 'data') and hasattr(inter.data, 'resolved'):
                if hasattr(inter.data.resolved, 'attachments') and inter.data.resolved.attachments:
                    for att_id, att in inter.data.resolved.attachments.items():
                        # Avoid duplicates
                        if not attachment or att.id != attachment.id:
                            attachments.append(att)
        except Exception as e:
            self.logger.debug(f"Could not access resolved attachments: {e}")
        
        # Fallback: Check message attachments (if command was invoked with a message)
        if not attachments:
            try:
                # Try to fetch the original message if it exists
                if hasattr(inter, 'message') and inter.message:
                    if hasattr(inter.message, 'attachments') and inter.message.attachments:
                        attachments = list(inter.message.attachments)
            except Exception as e:
                self.logger.debug(f"Could not access message attachments: {e}")
        
        # If no attachments found, show error
        if not attachments:
            await self._safe_edit_response(inter,
                content="❌ **No files attached**\n\n"
                       f"Please attach one or more files to this command.\n"
                       f"💡 **Tip:** You can attach multiple files at once in Discord!"
            )
            return
        
        # Determine requester (handle DM context)
        # In DMs, required_by will be None, so use inter.author
        requester_user = required_by if required_by else inter.author
        
        # Process each file
        successful_uploads = []
        failed_uploads = []
        
        await self._safe_edit_response(inter,
            content=f"📥 Processing {len(attachments)} file(s)..."
        )
        
        for idx, att in enumerate(attachments, 1):
            try:
                # Validate file
                validation_error = self._validate_file(att)
                if validation_error:
                    failed_uploads.append({
                        "filename": att.filename,
                        "error": validation_error
                    })
                    continue
                
                file_name = Path(att.filename).stem
                
                # Download the file
                file_data = await att.read()
                file_path = safe_filename_in_dir(att.filename, FILES_DIR)
                if file_path is None:
                    failed_uploads.append({
                        "filename": att.filename,
                        "error": "❌ Invalid filename",
                    })
                    continue

                # Handle filename conflicts (add timestamp if file exists)
                if file_path.exists():
                    timestamp = int(time.time())
                    name_part = file_path.stem
                    conflict_path = safe_filename_in_dir(
                        f"{name_part}_{timestamp}{file_path.suffix}", FILES_DIR
                    )
                    if conflict_path is None:
                        failed_uploads.append({
                            "filename": att.filename,
                            "error": "❌ Could not allocate safe filename",
                        })
                        continue
                    file_path = conflict_path
                    self.logger.info(f"File {att.filename} already exists, saving as {file_path.name}")
                
                # Save the file
                file_path.write_bytes(file_data)
                
                # Update metadata (inside lock to prevent races with concurrent uploads/removes)
                file_id = str(int(time.time() * 1000) + idx)  # Ensure unique IDs for multiple files
                async with self._metadata_lock:
                    self.metadata["files"][file_id] = {
                        "name": file_name,
                        "filename": file_path.name,  # Use actual saved filename
                        "uploaded_by": inter.author.id,
                        "required_by": requester_user.id,
                        "uploaded_at": time.time(),
                        "size": att.size,
                        "download_count": 0
                    }
                    
                    self.metadata["history"].append({
                        "file_id": file_id,
                        "file_name": file_name,
                        "uploaded_by": inter.author.id,
                        "required_by": requester_user.id,
                        "uploaded_at": time.time()
                    })
                
                successful_uploads.append({
                    "file_id": file_id,
                    "file_name": file_name,
                    "file_path": file_path,
                    "filename": file_path.name
                })
                
                self.logger.info(f"Successfully uploaded file {idx}/{len(attachments)}: {file_name}")
                
            except Exception as e:
                self.logger.error(f"Error uploading file {att.filename}: {e}", exc_info=True)
                failed_uploads.append({
                    "filename": att.filename,
                    "error": f"Upload failed: {str(e)}"
                })
        
        # Save metadata once for all files (inside lock for consistency)
        if successful_uploads:
            async with self._metadata_lock:
                await self._save_metadata_async()
        
        # Send summary
        if successful_uploads and not failed_uploads:
            if len(successful_uploads) == 1:
                name = successful_uploads[0]["file_name"]
                status = f"✅ File '{name}' uploaded successfully!\n📤 Starting distribution..."
            else:
                summary = f"✅ **{len(successful_uploads)} files uploaded successfully!**\n\n"
                for file_info in successful_uploads:
                    summary += f"• {file_info['file_name']}\n"
                status = summary + "\n📤 Starting distribution for all files..."
            await self._distribute_uploaded_files(
                inter, successful_uploads, requester_user, status_message=status
            )
        
        elif successful_uploads and failed_uploads:
            # Partial success
            summary = f"⚠️ **Partial Success**\n\n"
            summary += f"✅ Uploaded: {len(successful_uploads)} file(s)\n"
            summary += f"❌ Failed: {len(failed_uploads)} file(s)\n\n"
            
            if successful_uploads:
                summary += "**Successful:**\n"
                for file_info in successful_uploads:
                    summary += f"• {file_info['file_name']}\n"
            
            if failed_uploads:
                summary += "\n**Failed:**\n"
                for fail_info in failed_uploads:
                    summary += f"• {fail_info['filename']}: {fail_info['error']}\n"
            
            await self._safe_edit_response(inter, content=summary)
            await self._distribute_uploaded_files(inter, successful_uploads, requester_user)
        
        else:
            # All failed
            summary = f"❌ **All {len(failed_uploads)} file(s) failed to upload**\n\n"
            for fail_info in failed_uploads:
                summary += f"• {fail_info['filename']}: {fail_info['error']}\n"
            await self._safe_edit_response(inter, content=summary)

    @distribute_root.sub_command(name="list", description="List all uploaded files")
    async def list_files(self, inter: disnake.ApplicationCommandInteraction):
        """List all uploaded files"""
        await inter.response.defer(ephemeral=True)
        if not await self._require_participant(inter):
            return
        files = self.metadata.get("files") or {}
        if not isinstance(files, dict):
            files = {}
        if not files:
            await self._safe_edit_response(inter, content="📦 No files have been uploaded yet")
            return
        
        # Sort by upload time (newest first); only include entries that are dicts
        sorted_files = sorted(
            [(fid, data) for fid, data in files.items() if isinstance(data, dict)],
            key=lambda x: x[1].get("uploaded_at", 0),
            reverse=True
        )
        
        # Use paginator if more than 10 files, otherwise show all
        if len(sorted_files) > 10:
            paginator = FileListPaginator(sorted_files, timeout=300)
            embed = paginator.get_embed()
            await self._safe_edit_response(inter, embed=embed, view=paginator)
        else:
            # Show all files on one page (no pagination needed)
            embed = disnake.Embed(title="📦 Uploaded Files", color=disnake.Color.blue())
            
            for file_id, file_data in sorted_files:
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
            
            embed.set_footer(text=f"Total: {len(sorted_files)} file(s)")
            await self._safe_edit_response(inter, embed=embed)
    
    @distribute_root.sub_command(name="browse", description="Browse and select files using an interactive file browser")
    async def browse_files(self, inter: disnake.ApplicationCommandInteraction):
        """Browse files using an interactive file browser"""
        await inter.response.defer(ephemeral=True)
        if not await self._require_participant(inter):
            return
        
        async def handler(interaction, file_id, file_data, file_path):
            embed = disnake.Embed(title=f"📦 {file_data.get('name')}", color=disnake.Color.blue())
            embed.add_field(name="Size", value=f"{file_data.get('size', 0) / 1024 / 1024:.2f} MB", inline=True)
            embed.add_field(name="Required By", value="🎅 A Secret Santa", inline=True)
            embed.add_field(name="Uploaded", value=f"<t:{int(file_data.get('uploaded_at', 0))}:R>", inline=False)
            embed.set_footer(text="Use /distribute get to download this file")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        await self._handle_file_browser(inter, "browse", handler)

    @distribute_root.sub_command(name="get", description="Get/download a file (use browse command for easier selection)")
    async def get_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str = commands.Param(default=None, description="File name (leave empty to use file browser)", autocomplete="autocomplete_file_name_get")
    ):
        """Get/download a specific file"""
        await inter.response.defer(ephemeral=True)
        if not await self._require_participant(inter):
            return

        if not file_name or (isinstance(file_name, str) and not file_name.strip()):
            async def handler(interaction, file_id, file_data, file_path):
                embed = self._create_file_embed(file_data)
                fn = (file_data.get("filename") or file_data.get("name") or file_path.name if file_path else "").strip()
                file = disnake.File(file_path, filename=fn or "file") if file_path and file_path.exists() else None
                if not file:
                    await interaction.response.send_message("❌ File not found on disk", ephemeral=True)
                    return
                await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
            await self._handle_file_browser(inter, "get", handler)
            return
        
        # Find file by name
        result = self._find_file_by_name(file_name)
        if not result:
            await self._safe_edit_response(inter,
                content=f"❌ File '{file_name}' not found\n\n💡 Try `/distribute get` (without file_name) to browse all files!"
            )
            return
        
        file_id, file_data = result
        filename = (file_data.get("filename") or file_data.get("name") or "").strip()
        if not filename:
            await self._safe_edit_response(inter, content="❌ File metadata missing filename")
            return
        file_path = safe_filename_in_dir(filename, FILES_DIR)
        if file_path is None or not file_path.exists():
            await self._safe_edit_response(inter, content="❌ File not found on disk")
            return
        embed = self._create_file_embed(file_data)
        file = disnake.File(file_path, filename=file_path.name)
        await self._safe_edit_response(inter, embed=embed, file=file)

    @distribute_root.sub_command(name="remove", description="Remove a file (mod only, use browse for easier selection)")
    @mod_check()
    async def remove_file(
        self,
        inter: disnake.ApplicationCommandInteraction,
        file_name: str = commands.Param(default=None, description="File name (leave empty to use file browser)", autocomplete="autocomplete_file_name_remove")
    ):
        """Remove a file (moderator only)"""
        await inter.response.defer(ephemeral=True)
        
        async def remove_handler(interaction, file_id, file_data, file_path):
            try:
                await interaction.response.defer(ephemeral=True)
                if file_path.exists():
                    file_path.unlink()
                async with self._metadata_lock:
                    del self.metadata["files"][file_id]
                    # Mark as deleted in history (preserve audit trail)
                    for history_entry in self.metadata.get("history", []):
                        if history_entry.get("file_id") == file_id:
                            history_entry["status"] = "*deleted*"
                            break
                    await self._save_metadata_async()
                await interaction.followup.send(
                    f"✅ File '{file_data.get('name')}' has been removed", ephemeral=True
                )
            except Exception as e:
                self.logger.error(f"Error removing file: {e}", exc_info=True)
                try:
                    await interaction.followup.send(f"❌ Error removing file: {str(e)}", ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(f"❌ Error removing file: {str(e)}", ephemeral=True)
                    except Exception:
                        pass
        
        if not file_name:
            await self._handle_file_browser(inter, "remove", remove_handler)
            return
        
        # Find and remove file
        result = self._find_file_by_name(file_name)
        if not result:
            await self._safe_edit_response(inter,
                content=f"❌ File '{file_name}' not found\n\n💡 Try `/distribute remove` (without file_name) to browse all files!"
            )
            return
        
        file_id, file_data = result
        filename = (file_data.get("filename") or file_data.get("name") or "").strip()
        if not filename:
            await self._safe_edit_response(inter, content="❌ File metadata missing filename")
            return
        file_path = safe_filename_in_dir(filename, FILES_DIR)
        try:
            if file_path and file_path.exists():
                file_path.unlink()
            async with self._metadata_lock:
                del self.metadata["files"][file_id]
                # Mark as deleted in history (preserve audit trail)
                for history_entry in self.metadata.get("history", []):
                    if history_entry.get("file_id") == file_id:
                        history_entry["status"] = "*deleted*"
                        break
                await self._save_metadata_async()
            await self._safe_edit_response(inter, content=f"✅ File '{file_name}' has been removed")
        except Exception as e:
            self.logger.error(f"Error removing file: {e}", exc_info=True)
            await self._safe_edit_response(inter, content=f"❌ Error removing file: {str(e)}")

    # ============ COG LIFECYCLE ============
    async def cog_load(self):
        """Initialize cog"""
        self.logger.info("DistributeZip cog loaded")
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("📦 DistributeZip cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog"""
        self.logger.info("Unloading DistributeZip cog...")
        
        # Save metadata synchronously during unload (acceptable for shutdown)
        try:
            save_metadata(self.metadata, logger=self.logger)
        except Exception as e:
            self.logger.error(f"Failed to save metadata during unload: {e}")
        
        # Executor is shared (bot.executor) - shutdown in main.py graceful_shutdown


def setup(bot):
    """Setup the cog"""
    bot.add_cog(DistributeZipCog(bot))
