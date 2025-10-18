"""
Secret Santa Cog - Complete Rewrite
Manages Secret Santa events with gift tracking and anonymous communication
"""

import asyncio
import datetime as dt
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import disnake
from disnake.ext import commands

# Paths
# The cog file is at: PROJECT_ROOT/cogs/SecretSanta_cog.py
# We want archive at: PROJECT_ROOT/cogs/archive/
ROOT = Path(__file__).parent  # This is the 'cogs' directory
STATE_FILE = ROOT / "secret_santa_state.json"
ARCHIVE_DIR = ROOT / "archive"

# Ensure archive directory exists
ARCHIVE_DIR.mkdir(exist_ok=True)

# Log the paths for debugging
import logging
_init_logger = logging.getLogger("bot.santa.init")
_init_logger.info(f"Secret Santa cog file: {__file__}")
_init_logger.info(f"ROOT (cogs dir): {ROOT}")
_init_logger.info(f"Archive directory: {ARCHIVE_DIR}")
_init_logger.info(f"Archive exists: {ARCHIVE_DIR.exists()}")
if ARCHIVE_DIR.exists():
    files = list(ARCHIVE_DIR.glob("*.json"))
    _init_logger.info(f"Archive files found: {[f.name for f in files]}")


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON with error handling"""
    if path.exists():
        try:
            text = path.read_text().strip()
            return json.loads(text) if text else (default or {})
        except (json.JSONDecodeError, OSError):
            pass
    return default or {}


def save_json(path: Path, data: Any):
    """Save JSON atomically with error handling"""
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        temp.replace(path)
    except Exception as e:
        # Clean up temp file if save failed
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass
        raise  # Re-raise so caller knows save failed


def load_history_from_archives(archive_dir: Path, exclude_years: List[int] = None, logger=None) -> tuple[Dict[str, List[int]], List[int]]:
    """
    Load Secret Santa history from archive files.
    
    Args:
        archive_dir: Path to archive directory
        exclude_years: List of years to exclude from history (for fallback)
        logger: Optional logger for debugging
    
    Returns:
        Tuple of (history dict, list of available years sorted oldest to newest)
    """
    exclude_years = exclude_years or []
    history = {}
    available_years = []

    # Load archived history from YYYY.json files
    for archive_file in archive_dir.glob("[0-9]*.json"):
        try:
            year_str = archive_file.stem
            if not year_str.isdigit() or len(year_str) != 4:
                continue

            year = int(year_str)
            available_years.append(year)
            
            # Skip excluded years
            if year in exclude_years:
                if logger:
                    logger.info(f"Excluding year {year} from history (fallback mode)")
                continue

            archive_data = load_json(archive_file)

            # Check for unified format (event key)
            if archive_data.get("event"):
                event_data = archive_data["event"]
                event_assignments = event_data.get("assignments", {})

                if isinstance(event_assignments, dict):
                    for giver, receiver in event_assignments.items():
                        try:
                            receiver_int = int(receiver)
                            history.setdefault(str(giver), []).append(receiver_int)
                        except (ValueError, TypeError):
                            continue
            
            # Handle legacy old format (direct assignments list)
            elif "assignments" in archive_data and isinstance(archive_data["assignments"], list):
                for assignment in archive_data["assignments"]:
                    giver_id = assignment.get("giver_id")
                    receiver_id = assignment.get("receiver_id")

                    if giver_id and receiver_id:
                        try:
                            receiver_int = int(receiver_id)
                            history.setdefault(str(giver_id), []).append(receiver_int)
                        except (ValueError, TypeError):
                            continue

        except Exception as e:
            if logger:
                logger.warning(f"Error loading archive {archive_file}: {e}")
            continue

    # Sort available years (oldest first)
    available_years.sort()
    
    return history, available_years


def validate_assignment_possibility(participants: List[int], history: Dict[str, List[int]]) -> Optional[str]:
    """Check if assignments are possible before attempting them"""
    if len(participants) < 2:
        return "Need at least 2 participants for Secret Santa"
    
    # Check each participant's available options
    problematic_users = []
    for giver in participants:
        unacceptable = history.get(str(giver), [])
        # Remove giver from available options (can't give to self)
        available = [p for p in participants if p not in unacceptable and p != giver]
        
        if len(available) == 0:
            problematic_users.append(str(giver))
        elif len(available) < 2:  # Warn if very limited options
            # This person might get stuck if others are assigned their only option
            pass
    
    if problematic_users:
        return f"Assignment impossible due to history constraints. Users {', '.join(problematic_users)} have no valid receivers. Consider running the event earlier or clearing some history."
    
    # Check for graph connectivity (basic check)
    # If everyone can only give to 1 person, we might have issues
    very_limited = []
    for giver in participants:
        unacceptable = history.get(str(giver), [])
        available = [p for p in participants if p not in unacceptable and p != giver]
        if len(available) == 1:
            very_limited.append(giver)
    
    if len(very_limited) > len(participants) // 2:
        return f"Assignment may be difficult - {len(very_limited)} participants have very limited options due to history. Consider running the event earlier in the year."
    
    return None


def make_assignments(participants: List[int], history: Dict[str, List[int]]) -> Dict[int, int]:
    """
    Create Secret Santa assignments avoiding repeats from history.
    
    ALGORITHM:
    1. Use cryptographically secure randomness (secrets.SystemRandom)
    2. Shuffle participants randomly (different order each attempt)
    3. For each giver, find available receivers (not in history, not self)
    4. Randomly choose from available receivers
    5. Retry up to 10 times if assignment fails
    
    SAFETY:
    - Works with copy of history (doesn't modify unless successful)
    - Only updates real history if ALL assignments succeed
    - Prevents cycles (giver can't receive from their receiver)
    
    RANDOMNESS:
    Uses secrets.SystemRandom() which is cryptographically secure and doesn't
    require manual seeding. It uses the OS's entropy pool directly (os.urandom).
    This is the recommended approach for security-sensitive randomness in Python.
    
    Args:
        participants: List of user IDs participating
        history: Dict mapping giver ID (str) to list of previous receiver IDs
    
    Returns:
        Dict mapping giver ID (int) to receiver ID (int)
    
    Raises:
        ValueError: If assignment is impossible with current history
    """
    if len(participants) < 2:
        raise ValueError("Need at least 2 participants")

    # Use cryptographically secure random number generator
    # This uses os.urandom() internally - no manual seeding needed!
    secure_random = secrets.SystemRandom()
    
    # Try multiple times with different random orderings
    max_attempts = 10
    
    for attempt in range(max_attempts):
        try:
            result: Dict[int, int] = {}
            temp_history = {k: v.copy() for k, v in history.items()}  # Work with copy
            
            # Shuffle participants for different assignment order each attempt
            shuffled_participants = participants.copy()
            secure_random.shuffle(shuffled_participants)
            
            for giver in shuffled_participants:
                unacceptable: List[int] = temp_history.get(str(giver), [])
                
                # Add current assignments where someone else is giving to this giver
                for g, r in result.items():
                    if r == giver:
                        unacceptable.append(g)
                
                # Find available receivers
                available = [p for p in participants if p not in unacceptable and p != giver]
                
                if not available:
                    raise ValueError(f"Cannot assign giver {giver} - no valid receivers available")
                
                receiver = secure_random.choice(available)
                result[giver] = receiver
                temp_history.setdefault(str(giver), []).append(receiver)
            
            # Success! Update the real history
            for giver, receiver in result.items():
                history.setdefault(str(giver), []).append(receiver)
            
            return result
            
        except ValueError:
            if attempt == max_attempts - 1:
                # Last attempt failed - provide detailed error for fallback
                raise ValueError("Assignment failed with current history constraints")
            continue  # Try again with different random order
    
    raise ValueError("Assignment failed - this should not be reached")


def mod_check():
    """Check if user is mod or admin"""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        if inter.author.guild_permissions.administrator:
            return True

        # Check config for mod role
        if hasattr(inter.bot, 'config') and hasattr(inter.bot.config, 'DISCORD_MODERATOR_ROLE_ID'):
            mod_role_id = inter.bot.config.DISCORD_MODERATOR_ROLE_ID
            if mod_role_id and any(r.id == mod_role_id for r in inter.author.roles):
                return True

        return False

    return commands.check(predicate)


def participant_check():
    """Check if user is a participant"""
    async def predicate(inter: disnake.ApplicationCommandInteraction):
        cog = inter.bot.get_cog("SecretSantaCog")
        if not cog:
            return False

        event = cog.state.get("current_event")
        if not event or not event.get("active"):
            return False

        return str(inter.author.id) in event.get("participants", {})

    return commands.check(predicate)


class SecretSantaCog(commands.Cog):
    """Secret Santa event management"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("santa")

        # Load state with validation
        try:
            self.state = load_json(STATE_FILE, {
                "current_year": dt.date.today().year,
                "pair_history": {},
                "current_event": None
            })
            
            # Validate loaded state structure
            if not isinstance(self.state, dict):
                raise ValueError("State is not a dict")
            
            # Ensure required keys exist
            if "current_year" not in self.state:
                self.state["current_year"] = dt.date.today().year
            if "pair_history" not in self.state:
                self.state["pair_history"] = {}
            if "current_event" not in self.state:
                self.state["current_event"] = None
            
            # Validate current event if it exists
            if self.state.get("current_event"):
                event = self.state["current_event"]
                if not isinstance(event, dict):
                    self.logger.error("Invalid event state - not a dict, resetting")
                    self.state["current_event"] = None
                elif not isinstance(event.get("participants"), dict):
                    self.logger.error("Invalid event state - participants not a dict, resetting")
                    self.state["current_event"] = None
                else:
                    # Validate critical event fields
                    required_fields = ["active", "participants", "assignments", "guild_id"]
                    if not all(field in event for field in required_fields):
                        self.logger.warning(f"Event missing required fields, may be incomplete")
            
            active_event = bool(self.state.get("current_event", {}).get("active"))
            self.logger.info(f"State loaded successfully. Active event: {active_event}")
            
        except Exception as e:
            self.logger.error(f"Failed to load state: {e}, using defaults", exc_info=True)
            # Try to load from backup
            backup_path = STATE_FILE.with_suffix('.backup')
            if backup_path.exists():
                try:
                    self.logger.info("Attempting to load from backup...")
                    self.state = load_json(backup_path, {
                        "current_year": dt.date.today().year,
                        "pair_history": {},
                        "current_event": None
                    })
                    self.logger.info("Backup state loaded successfully")
                except Exception as backup_error:
                    self.logger.error(f"Backup load also failed: {backup_error}")
                    self.state = {
                        "current_year": dt.date.today().year,
                        "pair_history": {},
                        "current_event": None
                    }
            else:
                self.state = {
                    "current_year": dt.date.today().year,
                    "pair_history": {},
                    "current_event": None
                }

        self._lock = asyncio.Lock()
        self._backup_task: Optional[asyncio.Task] = None
        self._unloaded = False  # Track if already unloaded

        self.logger.info("Secret Santa cog initialized")

    async def cog_load(self):
        """Initialize cog"""
        self._backup_task = asyncio.create_task(self._backup_loop())
        self.logger.info("Secret Santa cog loaded")
        
        # Notify Discord about cog loading
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("🎄 Secret Santa cog loaded successfully", "SUCCESS")

    def cog_unload(self):
        """Cleanup cog (synchronous wrapper to prevent RuntimeWarning)"""
        if self._unloaded:
            return
        
        self._unloaded = True
        self.logger.info("Unloading Secret Santa cog...")
        
        # Do sync operations immediately
        self._save()  # Final save is sync, safe to call
        
        # Schedule async cleanup for backup task
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running() and self._backup_task:
                # Create task for async cleanup
                loop.create_task(self._async_unload())
            else:
                # No loop or no task, we're done
                self.logger.info("Secret Santa cog unloaded (sync)")
        except RuntimeError:
            # No event loop available
            self.logger.info("Secret Santa cog unloaded (no loop)")
    
    async def _async_unload(self):
        """Async cleanup operations"""
        try:
            if self._backup_task:
                self._backup_task.cancel()
                try:
                    await self._backup_task
                except asyncio.CancelledError:
                    pass
            
            self.logger.info("Secret Santa cog unloaded")
        except Exception as e:
            self.logger.error(f"Async unload error: {e}")

    def _save(self):
        """Save state to disk with error handling and backup"""
        try:
            save_json(STATE_FILE, self.state)
            return True
        except Exception as e:
            self.logger.error(f"CRITICAL: Failed to save state: {e}", exc_info=True)
            # Try to save a backup
            try:
                backup_path = STATE_FILE.with_suffix('.backup')
                save_json(backup_path, self.state)
                self.logger.warning(f"Saved to backup file: {backup_path}")
            except Exception as backup_error:
                self.logger.error(f"Backup save also failed: {backup_error}")
            return False

    async def _backup_loop(self):
        """Periodic backup"""
        try:
            while True:
                await asyncio.sleep(3600)  # Every hour
                async with self._lock:
                    self._save()
        except asyncio.CancelledError:
            pass

    async def _send_dm(self, user_id: int, message: str) -> bool:
        """Send DM to user"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(message)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to DM {user_id}: {e}")
            return False

    def _get_year_emoji_mapping(self, participants: Dict[str, str]) -> Dict[str, str]:
        """Create consistent emoji mapping for all participants in a year"""
        # Christmas emoji pool for participants
        emoji_pattern = ["🎁", "🎄", "🎅", "⭐", "❄️", "☃️", "🦌", "🔔", "🍪", "🥛", "🕯️", "✨", "🌟", "🎈", "🧸", "🍭", "🎂", "🎪", "🎨", "🎯"]
        
        # Sort participants by ID for consistent assignment
        sorted_participants = sorted(participants.keys())
        
        emoji_mapping = {}
        for i, participant_id in enumerate(sorted_participants):
            emoji_mapping[participant_id] = emoji_pattern[i % len(emoji_pattern)]
        
        return emoji_mapping

    async def _anonymize_text(self, text: str, message_type: str = "question") -> str:
        """Use OpenAI to rewrite text for anonymity"""
        if not hasattr(self.bot.config, 'OPENAI_API_KEY') or not self.bot.config.OPENAI_API_KEY:
            return text  # Return original if no API key
        
        try:
            prompts = {
                "question": (
                    "Rewrite this Secret Santa message with slightly different wording while keeping the EXACT same meaning and tone. "
                    "Only change the writing style minimally - use different words but maintain the same message, emotion, and intent. "
                    "Don't make it more polite or change the personality. Just rephrase it subtly for anonymity.\n\n"
                    f"Original: {text}\n\nRewritten:"
                ),
                "reply": (
                    "Rewrite this Secret Santa reply with slightly different wording while keeping the EXACT same meaning and tone. "
                    "Only change the writing style minimally - use different words but maintain the same message, emotion, and intent. "
                    "Don't make it more polite or change the personality. Just rephrase it subtly for anonymity.\n\n"
                    f"Original: {text}\n\nRewritten:"
                )
            }
            
            prompt = prompts.get(message_type, prompts["question"])
            
            headers = {
                "Authorization": f"Bearer {self.bot.config.OPENAI_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,  # Shorter to prevent over-elaboration
                "temperature": 0.3  # Lower temperature for more subtle changes
            }
            
            # Use reasonable timeout for anonymization
            session = await self.bot.http_mgr.get_session(timeout=20)
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    rewritten = result["choices"][0]["message"]["content"].strip()
                    # Remove common AI response prefixes
                    rewritten = rewritten.replace("Rewritten:", "").strip()
                    return rewritten if rewritten else text
                else:
                    self.logger.debug(f"Anonymization failed: {resp.status}")
                    return text
                    
        except Exception as e:
            self.logger.debug(f"Anonymization error: {e}")
            return text

    def _archive_event(self, event: Dict[str, Any], year: int) -> str:
        """
        Archive event data in unified format with CRITICAL overwrite protection.
        
        SAFETY FEATURES:
        - Never overwrites existing archives (data loss prevention!)
        - Creates timestamped backup if year already archived
        - Sends Discord warnings if duplicate year detected
        - Useful for test events or accidental re-runs
        
        If archive already exists, saves to backup file instead.
        Example: 2025.json exists → saves to 2025_backup_20251216_153045.json
        
        Returns:
            Filename of the saved archive (e.g., "2025.json" or "2025_backup_20251216_153045.json")
        """
        archive_data = {
            "year": year,
            "event": event.copy(),
            "archived_at": time.time(),
            "timestamp": dt.datetime.now().isoformat()
        }
        
        archive_path = ARCHIVE_DIR / f"{year}.json"
        
        # CRITICAL SAFETY CHECK: Prevent data loss from accidental overwrites
        # This catches: test runs, accidental re-runs, multiple events per year
        if archive_path.exists():
            # Archive already exists! Save to backup file instead (NEVER overwrite!)
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = ARCHIVE_DIR / f"{year}_backup_{timestamp}.json"
            save_json(backup_path, archive_data)
            
            self.logger.warning(f"⚠️ Archive {year}.json already exists! Saved to {backup_path.name} instead")
            self.logger.warning(f"This suggests you ran multiple events in {year}. Please review archives manually!")
            
            # Also notify via Discord if possible
            if hasattr(self.bot, 'send_to_discord_log'):
                asyncio.create_task(
                    self.bot.send_to_discord_log(
                        f"⚠️ Archive protection: {year}.json already exists! Saved to {backup_path.name} to prevent data loss. Review manually!",
                        "WARNING"
                    )
                )
            
            return backup_path.name
        else:
            # Safe to save normally
            save_json(archive_path, archive_data)
            self.logger.info(f"Archived Secret Santa {year} → {archive_path.name}")
            return archive_path.name

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.ApplicationCommandInteraction):
        """Secret Santa commands"""
        pass

    @ss_root.sub_command(name="start", description="Start a Secret Santa event")
    @mod_check()
    async def ss_start(
        self,
        inter: disnake.ApplicationCommandInteraction,
        announcement_message_id: str = commands.Param(description="Message ID for reactions"),
        role_id: str = commands.Param(description="Role ID to assign participants")
    ):
        """Start new Secret Santa event"""
        await inter.response.defer(ephemeral=True)

        # Validate IDs
        try:
            msg_id = int(announcement_message_id)
            role_id_int = int(role_id)
        except ValueError:
            await inter.edit_original_response(content="❌ Invalid message or role ID")
            return

        # Check if event already active
        event = self.state.get("current_event")
        if event and event.get("active"):
            await inter.edit_original_response(content="❌ Event already active")
            return

        # SAFETY WARNING: Check if current year is already archived
        # Prevents accidental data loss if you test on wrong server or run twice
        current_year = dt.date.today().year
        existing_archive = ARCHIVE_DIR / f"{current_year}.json"
        if existing_archive.exists():
            embed = disnake.Embed(
                title="⚠️ Year Already Archived",
                description=f"An archive already exists for {current_year}!\n\n"
                            f"**This might mean:**\n"
                            f"• You already ran Secret Santa this year\n"
                            f"• You're testing on the wrong server\n"
                            f"• This is intentional (test event)\n\n"
                            f"**If you continue, the old archive will be preserved** and any new archive will be saved to a backup file.",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="🔒 Protection Active",
                value=f"Existing archive: `{current_year}.json`\n"
                      f"New archives will save to: `{current_year}_backup_TIMESTAMP.json`",
                inline=False
            )
            embed.set_footer(text="✅ Your existing archive is safe and won't be overwritten!")
            await inter.edit_original_response(embed=embed)
            
            # Log this warning
            self.logger.warning(f"Starting new event for {current_year} but archive already exists!")
            if hasattr(self.bot, 'send_to_discord_log'):
                await self.bot.send_to_discord_log(
                    f"⚠️ {inter.author.display_name} is starting a new Secret Santa {current_year} event, but {current_year}.json archive already exists!",
                    "WARNING"
                )
            
            # Wait 5 seconds so user can read the warning, then continue
            await asyncio.sleep(5)

        # Find message and collect participants
        participants = {}
        found = False

        for channel in inter.guild.text_channels:
            try:
                message = await channel.fetch_message(msg_id)
                found = True

                # Collect users who reacted
                for reaction in message.reactions:
                    async for user in reaction.users():
                        if user.bot:
                            continue

                        if str(user.id) not in participants:
                            member = inter.guild.get_member(user.id)
                            name = member.display_name if member else user.name
                            participants[str(user.id)] = name

                break
            except (disnake.NotFound, disnake.Forbidden):
                continue

        if not found:
            await inter.edit_original_response(content="❌ Message not found")
            return

        # Create event (current_year already set above during safety check)
        new_event = {
            "active": True,
            "join_closed": False,
            "announcement_message_id": msg_id,
            "role_id": role_id_int,
            "participants": participants,
            "assignments": {},
            "guild_id": inter.guild.id,
            "gift_submissions": {},
            "communications": {}
        }

        async with self._lock:
            self.state["current_year"] = current_year
            self.state["current_event"] = new_event
            self._save()

        # Send confirmation DMs
        dm_tasks = [
            self._send_dm(
                int(uid),
                f"✅ You've joined Secret Santa {current_year}! 🎄\n\n"
                f"React to the announcement to join/leave."
            )
            for uid in participants
        ]

        results = await asyncio.gather(*dm_tasks, return_exceptions=True)
        successful = sum(1 for r in results if r is True)

        response_msg = (
            f"✅ Secret Santa {current_year} started!\n"
            f"• Participants: {len(participants)}\n"
            f"• DMs sent: {successful}/{len(participants)}\n"
            f"• Role ID: {role_id_int}"
        )
        
        await inter.edit_original_response(response_msg)
        
        # Notify Discord log channel
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log(
                f"Secret Santa {current_year} event started by {inter.author.display_name} - {len(participants)} participants joined",
                "SUCCESS"
            )

    @ss_root.sub_command(name="shuffle", description="Assign Secret Santas")
    @mod_check()
    async def ss_shuffle(self, inter: disnake.ApplicationCommandInteraction):
        """Make assignments with progressive year-based fallback"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        # Convert participant IDs to integers
        participants = [int(uid) for uid in event["participants"]]

        if len(participants) < 2:
            await inter.edit_original_response(content="❌ Need at least 2 participants")
            return

        # Load all history to get available years
        history, available_years = load_history_from_archives(ARCHIVE_DIR, exclude_years=[], logger=self.logger)
        
        self.logger.info(f"Attempting Secret Santa assignment with {len(participants)} participants")
        self.logger.info(f"Available history years: {available_years}")
        
        # PROGRESSIVE FALLBACK SYSTEM:
        # Try 1: Use ALL history (2021, 2022, 2023, 2024)
        # Try 2: Exclude 2021 only
        # Try 3: Exclude 2021, 2022
        # Try 4: Exclude 2021, 2022, 2023
        # Try 5: No history (fresh start)
        # This prevents impossible assignments after many years
        exclude_years = []
        assignments = None
        fallback_used = False
        
        for attempt in range(len(available_years) + 1):
            if attempt > 0:
                # Exclude oldest year(s) progressively (2021 first, then 2022, etc.)
                exclude_years = available_years[:attempt]
                fallback_used = True
                self.logger.info(f"Fallback attempt {attempt}: Excluding years {exclude_years}")
                
                # Reload history without excluded years
                history, _ = load_history_from_archives(ARCHIVE_DIR, exclude_years=exclude_years, logger=self.logger)
                
                # Inform user about fallback
                years_str = ", ".join(map(str, exclude_years))
                await inter.edit_original_response(
                    content=f"⚠️ Initial assignment difficult... trying fallback (excluding {years_str})..."
                )
            
            # Pre-validate assignment possibility
            validation_error = validate_assignment_possibility(participants, history)
            if validation_error:
                if attempt == len(available_years):
                    # Last attempt failed
                    await inter.edit_original_response(content=f"❌ {validation_error}")
                    
                    if hasattr(self.bot, 'send_to_discord_log'):
                        await self.bot.send_to_discord_log(
                            f"Secret Santa assignment failed even with all fallbacks - {validation_error}",
                            "ERROR"
                        )
                    return
                # Try next fallback
                continue
            
            # Try to make assignments
            try:
                assignments = make_assignments(participants, history)
                # Success!
                break
            except ValueError as e:
                if attempt == len(available_years):
                    # Last attempt failed
                    await inter.edit_original_response(content=f"❌ Assignment failed: {e}")
                    
                    if hasattr(self.bot, 'send_to_discord_log'):
                        await self.bot.send_to_discord_log(
                            f"Secret Santa assignment failed even with all fallbacks - {e}",
                            "ERROR"
                        )
                    return
                # Try next fallback
                continue
        
        if not assignments:
            await inter.edit_original_response(content="❌ Assignment failed unexpectedly")
            return

        # Assign role to participants
        role = inter.guild.get_role(event["role_id"])
        if role and inter.guild.me.guild_permissions.manage_roles:
            for user_id in participants:
                try:
                    member = inter.guild.get_member(user_id)
                    if member and role not in member.roles:
                        await member.add_roles(role, reason="Secret Santa participant")
                except Exception:
                    pass

        # Send assignment DMs - Santa knows who they're gifting to!
        messages = [
            "🎅 **Ho ho ho!** You're Secret Santa for {receiver}!",
            "🎄 **You've been assigned** to gift {receiver}!",
            "✨ **The magic of Christmas** has paired you with {receiver}!",
            "🦌 **Rudolph has chosen** you to spread joy to {receiver}!",
            "🎁 **Your mission** is to make {receiver}'s Christmas magical!",
            "❄️ **Winter magic** has matched you with {receiver}!"
        ]

        dm_tasks = []
        for giver, receiver in assignments.items():
            # Create clean assignment message with subtle festive touches
            msg = f"🎄✨ **SECRET SANTA {self.state['current_year']}** ✨🎄\n"
            
            # WHO YOU GOT (most important info)
            msg += f"🎯 **YOUR GIFTEE:**\n"
            msg += f"➤ {secrets.choice(messages).format(receiver=f'<@{receiver}>')}\n\n"
            
            # Simple separator
            msg += f"❄️ ❄️ ❄️ ❄️ ❄️ ❄️ ❄️ ❄️ ❄️\n\n"
            
            # Quick reference guide
            msg += f"💡 **QUICK GUIDE:**\n"
            msg += f"• Ask them questions anonymously to learn their interests\n"
            msg += f"• Find a thoughtful gift they'll love\n"
            msg += f"• Your identity stays secret until you choose to reveal it!\n\n"
            
            # Command reference with better formatting
            msg += f"🔧 **COMMANDS:**\n"
            msg += f"`/ss ask_giftee` → Send anonymous questions\n"
            msg += f"`/ss reply_santa` → Reply if they ask you something\n"
            msg += f"`/ss submit_gift` → Log your gift when delivered\n\n"
            
            # Clean footer
            msg += f"🔐 All messages are subtly AI-rewritten for anonymity!"
            
            dm_tasks.append(self._send_dm(giver, msg))

        await asyncio.gather(*dm_tasks)

        # Save assignments
        async with self._lock:
            event["assignments"] = {str(k): v for k, v in assignments.items()}
            event["join_closed"] = True
            self._save()

        # Build success message
        response_msg = f"✅ Assignments complete!\n"
        response_msg += f"• {len(assignments)} pairs created\n"
        response_msg += f"• DMs sent to all participants\n"
        
        if fallback_used:
            years_str = ", ".join(map(str, exclude_years))
            response_msg += f"\n⚠️ **Fallback used:** Excluded history from {years_str} to make assignments possible\n"
            response_msg += f"💡 Consider having Secret Santa more frequently to avoid this!"
        
        await inter.edit_original_response(content=response_msg)
        
        # Notify Discord log channel
        if hasattr(self.bot, 'send_to_discord_log'):
            log_msg = f"Secret Santa assignments completed by {inter.author.display_name} - {len(assignments)} pairs created"
            if fallback_used:
                log_msg += f" (fallback: excluded years {', '.join(map(str, exclude_years))})"
            await self.bot.send_to_discord_log(log_msg, "SUCCESS" if not fallback_used else "WARNING")

    @ss_root.sub_command(name="stop", description="Stop the Secret Santa event")
    @mod_check()
    async def ss_stop(self, inter: disnake.ApplicationCommandInteraction):
        """Stop event"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        year = self.state["current_year"]

        # Archive event (with automatic backup protection)
        saved_filename = self._archive_event(event, year)

        async with self._lock:
            self.state["current_event"] = None
            self._save()

        # Show appropriate message based on what file was saved
        if "backup" in saved_filename:
            # Archive protection was triggered
            embed = disnake.Embed(
                title="✅ Event Stopped & Protected",
                description=f"Secret Santa {year} has been archived with data protection!",
                color=disnake.Color.orange()
            )
            embed.add_field(
                name="🔒 Archive Protection",
                value=f"**Original:** `{year}.json` (preserved)\n"
                      f"**This event:** `{saved_filename}`\n\n"
                      f"⚠️ You ran multiple {year} events! Review archives folder manually.",
                inline=False
            )
            embed.set_footer(text="Your original archive was NOT overwritten!")
            await inter.edit_original_response(embed=embed)
        else:
            # Normal archive
            await inter.edit_original_response(content=f"✅ Event stopped and archived → `{saved_filename}`")
        
        # Notify Discord log channel
        if hasattr(self.bot, 'send_to_discord_log'):
            participants_count = len(event.get("participants", {}))
            gifts_count = len(event.get("gift_submissions", {}))
            await self.bot.send_to_discord_log(
                f"Secret Santa {self.state['current_year']} event stopped by {inter.author.display_name} - {participants_count} participants, {gifts_count} gifts submitted",
                "INFO"
            )

    @ss_root.sub_command(name="participants", description="View participants")
    @mod_check()
    async def ss_participants(self, inter: disnake.ApplicationCommandInteraction):
        """Show participants"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        participants = event.get("participants", {})

        if not participants:
            await inter.edit_original_response(content="❌ No participants yet")
            return

        embed = disnake.Embed(
            title=f"🎄 Participants ({len(participants)})",
            color=disnake.Color.green()
        )

        # Group participants for display
        lines = [f"• {name} (<@{uid}>)" for uid, name in list(participants.items())[:20]]

        if len(participants) > 20:
            lines.append(f"... and {len(participants) - 20} more")

        embed.description = "\n".join(lines)

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="ask_giftee", description="Ask your giftee a question (anonymously rewritten)")
    @participant_check()
    async def ss_ask(
        self,
        inter: disnake.ApplicationCommandInteraction,
        question: str = commands.Param(description="Your question (will be rewritten for anonymity)", max_length=500),
        skip_rewrite: bool = commands.Param(default=False, description="Skip AI rewriting (less anonymous)")
    ):
        """Ask giftee anonymously with AI rewriting"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Check if user has assignment
        if user_id not in event.get("assignments", {}):
            embed = disnake.Embed(
                title="❌ No Assignment",
                description="You don't have an assignment yet! Wait for the event organizer to run `/ss shuffle`.",
                color=disnake.Color.red()
            )
            await inter.edit_original_response(embed=embed)
            return

        receiver_id = event["assignments"][user_id]

        # Rewrite question for anonymity (unless skipped)
        if not skip_rewrite:
            await inter.edit_original_response(content="🤖 Rewriting your question for anonymity...")
            rewritten_question = await self._anonymize_text(question, "question")
        else:
            rewritten_question = question

        # Create beautiful question message
        question_msg = f"🎅✨ **SECRET SANTA MESSAGE** ✨🎅\n\n"
        question_msg += f"💌 **Anonymous question from your Secret Santa:**\n\n"
        question_msg += f"*\"{rewritten_question}\"*\n\n"
        question_msg += "─────────────────────\n"
        question_msg += "🎯 **How to reply:**\n"
        question_msg += "Use `/ss reply_santa` to send an anonymous reply back!\n\n"
        question_msg += "✨ *Your Secret Santa is excited to learn more about you!*"

        # Send question
        success = await self._send_dm(receiver_id, question_msg)

        if success:
            # Save communication
            async with self._lock:
                comms = event.setdefault("communications", {})
                thread = comms.setdefault(user_id, {"giftee_id": receiver_id, "thread": []})
                thread["thread"].append({
                    "type": "question",
                    "message": question,
                    "rewritten": rewritten_question,
                    "timestamp": time.time()
                })
                self._save()

            # Success embed
            embed = disnake.Embed(
                title="✅ Question Sent!",
                description=f"Your question has been delivered anonymously!",
                color=disnake.Color.green()
            )
            embed.add_field(
                name="📝 Original", 
                value=f"*{question[:100]}{'...' if len(question) > 100 else ''}*", 
                inline=False
            )
            if not skip_rewrite and rewritten_question != question:
                embed.add_field(
                    name="🤖 Rewritten", 
                    value=f"*{rewritten_question[:100]}{'...' if len(rewritten_question) > 100 else ''}*", 
                    inline=False
                )
            embed.set_footer(text="💡 Tip: Keep asking questions to find the perfect gift!")
            
            await inter.edit_original_response(embed=embed)
        else:
            embed = disnake.Embed(
                title="❌ Delivery Failed",
                description="Couldn't send your question. Your giftee may have DMs disabled.",
                color=disnake.Color.red()
            )
            await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="reply_santa", description="Reply to your Secret Santa (anonymously rewritten)")
    @participant_check()
    async def ss_reply(
        self,
        inter: disnake.ApplicationCommandInteraction,
        reply: str = commands.Param(description="Your reply (will be rewritten for anonymity)", max_length=500),
        skip_rewrite: bool = commands.Param(default=False, description="Skip AI rewriting (less anonymous)")
    ):
        """Reply to Santa anonymously with AI rewriting"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Find who is the user's Santa
        santa_id = None
        for giver, receiver in event.get("assignments", {}).items():
            if receiver == int(user_id):
                santa_id = int(giver)
                break

        if not santa_id:
            embed = disnake.Embed(
                title="❌ No Secret Santa Found",
                description="No one has asked you a question yet, or you haven't been assigned a Secret Santa!",
                color=disnake.Color.red()
            )
            embed.set_footer(text="💡 Wait for your Secret Santa to ask you something first!")
            await inter.edit_original_response(embed=embed)
            return

        # Rewrite reply for anonymity (unless skipped)
        if not skip_rewrite:
            await inter.edit_original_response(content="🤖 Rewriting your reply for anonymity...")
            rewritten_reply = await self._anonymize_text(reply, "reply")
        else:
            rewritten_reply = reply

        # Create beautiful reply message
        reply_msg = f"🎁✨ **SECRET SANTA REPLY** ✨🎁\n\n"
        reply_msg += f"📨 **Anonymous reply from your giftee:**\n\n"
        reply_msg += f"*\"{rewritten_reply}\"*\n\n"
        reply_msg += "─────────────────────\n"
        reply_msg += "🎯 **Keep the conversation going:**\n"
        reply_msg += "Use `/ss ask_giftee` to ask more questions!\n\n"
        reply_msg += "✨ *Your giftee is happy to help you find the perfect gift!*"

        # Send reply
        success = await self._send_dm(santa_id, reply_msg)

        if success:
            # Save communication
            async with self._lock:
                comms = event.setdefault("communications", {})
                thread = comms.setdefault(str(santa_id), {"giftee_id": user_id, "thread": []})
                thread["thread"].append({
                    "type": "reply",
                    "message": reply,
                    "rewritten": rewritten_reply,
                    "timestamp": time.time()
                })
                self._save()

            # Success embed
            embed = disnake.Embed(
                title="✅ Reply Sent!",
                description="Your reply has been delivered to your Secret Santa!",
                color=disnake.Color.green()
            )
            embed.add_field(
                name="📝 Original", 
                value=f"*{reply[:100]}{'...' if len(reply) > 100 else ''}*", 
                inline=False
            )
            if not skip_rewrite and rewritten_reply != reply:
                embed.add_field(
                    name="🤖 Rewritten", 
                    value=f"*{rewritten_reply[:100]}{'...' if len(rewritten_reply) > 100 else ''}*", 
                    inline=False
                )
            embed.set_footer(text="🎄 Your Secret Santa will be so happy to hear from you!")
            
            await inter.edit_original_response(embed=embed)
        else:
            embed = disnake.Embed(
                title="❌ Delivery Failed",
                description="Couldn't send your reply. Your Secret Santa may have DMs disabled.",
                color=disnake.Color.red()
            )
            await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="submit_gift", description="Submit your gift for records")
    @participant_check()
    async def ss_submit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        gift_description: str = commands.Param(description="Describe what you gave", max_length=500)
    ):
        """Submit gift description"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        user_id = str(inter.author.id)

        # Check if user has assignment
        if user_id not in event.get("assignments", {}):
            embed = disnake.Embed(
                title="❌ No Assignment",
                description="You don't have an assignment yet! Wait for the event organizer to run `/ss shuffle`.",
                color=disnake.Color.red()
            )
            await inter.edit_original_response(embed=embed)
            return

        receiver_id = event["assignments"][user_id]
        receiver_name = event["participants"].get(str(receiver_id), f"User {receiver_id}")

        # Save gift
        async with self._lock:
            event.setdefault("gift_submissions", {})[user_id] = {
                "gift": gift_description,
                "receiver_id": receiver_id,
                "receiver_name": receiver_name,
                "submitted_at": time.time(),
                "timestamp": dt.datetime.now().isoformat()
            }
            self._save()

        # Create beautiful success embed
        embed = disnake.Embed(
            title="🎁 Gift Submitted Successfully!",
            description="Your gift has been recorded in the Secret Santa archives!",
            color=disnake.Color.green()
        )
        embed.add_field(
            name="🎯 Recipient",
            value=f"**{receiver_name}**",
            inline=True
        )
        embed.add_field(
            name="📅 Year",
            value=f"**{self.state['current_year']}**",
            inline=True
        )
        embed.add_field(
            name="⏰ Submitted",
            value=f"<t:{int(time.time())}:R>",
            inline=True
        )
        embed.add_field(
            name="🎁 Gift Description",
            value=f"*{gift_description}*",
            inline=False
        )
        embed.set_footer(text="🎄 Thank you for participating in Secret Santa! Your kindness makes the season brighter.")
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/852616843715395605.png")  # Gift emoji

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_gifts", description="View submitted gifts")
    @mod_check()
    async def ss_view_gifts(self, inter: disnake.ApplicationCommandInteraction):
        """Show gift submissions"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        submissions = event.get("gift_submissions", {})

        if not submissions:
            await inter.edit_original_response(content="❌ No gifts submitted yet")
            return

        embed = disnake.Embed(
            title=f"🎁 Gift Submissions ({len(submissions)})",
            color=disnake.Color.green()
        )

        # Create consistent emoji mapping for all participants this year
        emoji_mapping = self._get_year_emoji_mapping(event["participants"])
        
        for giver_id, submission in list(submissions.items())[:10]:
            giver_name = event["participants"].get(giver_id, f"User {giver_id}")
            receiver_name = submission.get("receiver_name", "Unknown")
            gift = submission["gift"][:200] + "..." if len(submission["gift"]) > 200 else submission["gift"]

            # Get consistent emojis for each person this year
            giver_emoji = emoji_mapping.get(giver_id, "🎁")
            
            # Try to get receiver emoji from their ID if available
            receiver_id = submission.get("receiver_id")
            if receiver_id:
                receiver_emoji = emoji_mapping.get(str(receiver_id), "🎄")
            else:
                receiver_emoji = "🎄"

            embed.add_field(
                name=f"{giver_emoji} {giver_name} → {receiver_emoji} {receiver_name}",
                value=gift,
                inline=False
            )

        if len(submissions) > 10:
            embed.set_footer(text=f"Showing 10 of {len(submissions)} submissions")

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="view_comms", description="View communications")
    @mod_check()
    async def ss_view_comms(self, inter: disnake.ApplicationCommandInteraction):
        """Show communication threads"""
        await inter.response.defer(ephemeral=True)

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            await inter.edit_original_response(content="❌ No active event")
            return

        comms = event.get("communications", {})

        if not comms:
            await inter.edit_original_response(content="❌ No communications yet")
            return

        embed = disnake.Embed(
            title=f"💬 Communications ({len(comms)})",
            color=disnake.Color.blue()
        )

        # Create consistent emoji mapping for all participants this year
        emoji_mapping = self._get_year_emoji_mapping(event["participants"])
        
        for santa_id, data in list(comms.items())[:5]:
            santa_name = event["participants"].get(santa_id, f"User {santa_id}")
            giftee_id = data.get("giftee_id")
            giftee_name = event["participants"].get(str(giftee_id), "Unknown")

            # Get consistent emojis for each person this year
            santa_emoji = emoji_mapping.get(santa_id, "🎅")
            giftee_emoji = emoji_mapping.get(str(giftee_id), "🎄")

            thread = data.get("thread", [])
            thread_text = "\n".join([
                f"{santa_emoji if msg['type'] == 'question' else giftee_emoji} {msg['message'][:50]}..."
                for msg in thread[:3]
            ])

            embed.add_field(
                name=f"💬 {santa_name} → {giftee_name} ({len(thread)} messages)",
                value=thread_text or "No messages",
                inline=False
            )

        if len(comms) > 5:
            embed.set_footer(text=f"Showing 5 of {len(comms)} threads")

        await inter.edit_original_response(embed=embed)

    @ss_root.sub_command(name="history", description="View past Secret Santa events")
    async def ss_history(
            self,
            inter: disnake.ApplicationCommandInteraction,
            year: int = commands.Param(default=None, description="Specific year to view")
    ):
        """Show event history"""
        await inter.response.defer(ephemeral=True)

        # Load archived events - both YYYY.json and event_YYYY.json formats
        # NOTE: Current active event is NOT shown (would reveal secret assignments!)
        # Only archived events (after /ss stop) are visible
        archives = {}

        # Load archive files (unified YEAR.json format)
        for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
            year_str = archive_file.stem

            # Skip non-4-digit year files
            if not year_str.isdigit() or len(year_str) != 4:
                continue

            try:
                year_int = int(year_str)
                data = load_json(archive_file)

                if data and "event" in data:
                    # Unified format with full event data
                    archives[year_int] = data
                elif data and "assignments" in data and isinstance(data["assignments"], list):
                    # Legacy old format - convert to new structure
                    participants = {}
                    gifts = {}
                    assignments_map = {}

                    for assignment in data["assignments"]:
                        giver_id = assignment.get("giver_id", "")
                        giver_name = assignment.get("giver_name", "Unknown")
                        receiver_id = assignment.get("receiver_id", "")
                        receiver_name = assignment.get("receiver_name", "Unknown")
                        gift = assignment.get("gift", "No description")

                        participants[giver_id] = giver_name
                        if receiver_id:
                            participants[receiver_id] = receiver_name

                        # Only add to gifts if there's actual gift data
                        if gift and gift != "No description":
                            gifts[giver_id] = {
                                "gift": gift,
                                "receiver_name": receiver_name,
                                "receiver_id": receiver_id
                            }

                        if giver_id and receiver_id:
                            assignments_map[giver_id] = receiver_id

                    archives[year_int] = {
                        "year": year_int,
                        "event": {
                            "participants": participants,
                            "gift_submissions": gifts,
                            "assignments": assignments_map
                        }
                    }

            except Exception as e:
                self.logger.warning(f"Error loading archive {archive_file}: {e}")
                continue

        if not archives:
            await inter.edit_original_response(content="❌ No archived events found")
            return

        # Sort by year
        sorted_years = sorted(archives.keys(), reverse=True)

        if year:
            # Show specific year with improved layout
            if year not in archives:
                available = ", ".join(str(y) for y in sorted_years)
                await inter.edit_original_response(
                    content=f"❌ No event found for {year}\n**Available years:** {available}"
                )
                return

            archive = archives[year]
            event_data = archive.get("event", {})
            participants = event_data.get("participants", {})
            gifts = event_data.get("gift_submissions", {})

            # Create more accurate description based on event state
            assignments = event_data.get("assignments", {})
            has_assignments = bool(assignments)
            has_gifts = bool(gifts)
            
            if has_gifts:
                description = f"**{len(participants)}** participants, **{len(gifts)}** gifts exchanged"
            elif has_assignments:
                description = f"**{len(participants)}** participants, assignments made but no gifts recorded"
            else:
                description = f"**{len(participants)}** participants signed up, event incomplete"

            embed = disnake.Embed(
                title=f"🎄 Secret Santa {year}",
                description=description,
                color=disnake.Color.gold(),
                timestamp=dt.datetime.now()
            )

                # Always show assignments, with gift info if available
            if has_assignments:
                # Create consistent emoji mapping for all participants this year
                emoji_mapping = self._get_year_emoji_mapping(participants)
                exchange_lines = []

                for giver_id, receiver_id in list(assignments.items()):
                    # Get names/mentions for giver and receiver
                    giver_name = participants.get(str(giver_id), f"User {giver_id}")
                    receiver_name = participants.get(str(receiver_id), f"User {receiver_id}")
                    
                    giver_mention = f"<@{giver_id}>" if str(giver_id).isdigit() else giver_name
                    receiver_mention = f"<@{receiver_id}>" if str(receiver_id).isdigit() else receiver_name
                    
                    # Get consistent emojis for each person this year
                    giver_emoji = emoji_mapping.get(str(giver_id), "🎁")
                    receiver_emoji = emoji_mapping.get(str(receiver_id), "🎄")
                    
                    # Check if this assignment has a gift submission
                    submission = gifts.get(str(giver_id))
                    
                    if submission and isinstance(submission, dict):
                        # Has gift - show with description
                        gift_desc = submission.get("gift", "No description provided")
                        
                        # Format gift description (truncate if needed)
                        if isinstance(gift_desc, str) and len(gift_desc) > 60:
                            gift_desc = gift_desc[:57] + "..."
                        elif not isinstance(gift_desc, str):
                            gift_desc = "Invalid gift description"
                        
                        exchange_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention}")
                        exchange_lines.append(f"    ⤷ *{gift_desc}*")
                    else:
                        # No gift submitted - show assignment only
                        exchange_lines.append(f"{giver_emoji} {giver_mention} → {receiver_emoji} {receiver_mention} *(no gift recorded)*")

                # Split into multiple fields if needed
                chunks = []
                current_chunk = []
                current_length = 0

                for line in exchange_lines:
                    line_length = len(line)
                    if current_length + line_length > 900:  # Leave buffer for field limits
                        chunks.append("\n".join(current_chunk))
                        current_chunk = [line]
                        current_length = line_length
                    else:
                        current_chunk.append(line)
                        current_length += line_length + 1

                if current_chunk:
                    chunks.append("\n".join(current_chunk))

                # Add fields
                for i, chunk in enumerate(chunks[:3]):  # Max 3 fields
                    if i == 0:
                        gifts_count = len([g for g in gifts.keys() if g in [str(a) for a in assignments.keys()]])
                        field_name = f"🎄 Assignments & Gifts ({gifts_count}/{len(assignments)} gifts submitted)"
                    else:
                        field_name = "​"  # Zero width space for continuation
                    embed.add_field(name=field_name, value=chunk, inline=False)

                if len(assignments) > 10:
                    embed.add_field(
                        name="​",
                        value=f"*... and {len(assignments) - 10} more assignments*",
                        inline=False
                    )
            else:
                # Event incomplete - no assignments made at all
                status_text = f"⏸️ Signup completed ({len(participants)} joined)\n❌ No assignments made\n❌ No gifts recorded"
                
                embed.add_field(
                    name="📝 Event Status",
                    value=status_text,
                    inline=False
                )

            # Add statistics
            completion_rate = (len(gifts) / len(participants) * 100) if participants else 0
            embed.add_field(
                name="📊 Statistics",
                value=f"**Completion:** {completion_rate:.0f}%\n**Total Gifts:** {len(gifts)}",
                inline=True
            )

            embed.set_footer(text=f"Requested by {inter.author.display_name}")
            await inter.edit_original_response(embed=embed)

        else:
            # Show all years overview with better layout
            embed = disnake.Embed(
                title="🎄 Secret Santa Archive",
                description="Complete history of all Secret Santa events",
                color=disnake.Color.blue(),
                timestamp=dt.datetime.now()
            )

            # Create year timeline
            timeline_text = []
            for year_val in sorted_years:
                archive = archives[year_val]
                event_data = archive.get("event", {})
                participants = event_data.get("participants", {})
                gifts = event_data.get("gift_submissions", {})

                completion_rate = (len(gifts) / len(participants) * 100) if participants else 0

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
                    f"**{year_val}** {status} — {len(participants)} participants, {len(gifts)} gifts ({completion_rate:.0f}%)"
                )

            # Split timeline into chunks if needed
            if len(timeline_text) <= 10:
                embed.add_field(
                    name="📅 Event Timeline",
                    value="\n".join(timeline_text),
                    inline=False
                )
            else:
                embed.add_field(
                    name="📅 Recent Events",
                    value="\n".join(timeline_text[:5]),
                    inline=False
                )
                embed.add_field(
                    name="📅 Earlier Events",
                    value="\n".join(timeline_text[5:10]),
                    inline=False
                )
                if len(timeline_text) > 10:
                    embed.add_field(
                        name="​",
                        value=f"*... and {len(timeline_text) - 10} more years*",
                        inline=False
                    )

            # Calculate all-time statistics
            total_participants = sum(
                len(archives[y].get("event", {}).get("participants", {}))
                for y in sorted_years
            )
            total_gifts = sum(
                len(archives[y].get("event", {}).get("gift_submissions", {}))
                for y in sorted_years
            )
            avg_participants = total_participants / len(sorted_years) if sorted_years else 0
            avg_completion = (total_gifts / total_participants * 100) if total_participants else 0

            # Add statistics with better formatting
            stats_text = [
                f"**Total Events:** {len(sorted_years)}",
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

            # Add legend
            embed.add_field(
                name="📖 Status Legend",
                value="✅ 90%+ complete | 🟨 70-89% | 🟧 Under 70% | ⏳ No gifts recorded",
                inline=False
            )

            embed.set_footer(
                text=f"Use /ss history [year] for detailed view • Requested by {inter.author.display_name}")
            await inter.edit_original_response(embed=embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction adds for joining"""
        if payload.user_id == self.bot.user.id:
            return

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            return

        if event.get("join_closed"):
            return

        if payload.message_id != event.get("announcement_message_id"):
            return

        user_id = str(payload.user_id)

        # Already joined
        if user_id in event.get("participants", {}):
            return

        # Get user name
        name = f"User {payload.user_id}"
        if payload.guild_id:
            try:
                guild = self.bot.get_guild(payload.guild_id)
                if guild:
                    member = guild.get_member(payload.user_id)
                    if member:
                        name = member.display_name
            except Exception:
                pass

        # Add participant
        async with self._lock:
            event["participants"][user_id] = name
            self._save()

        # Send confirmation
        await self._send_dm(
            payload.user_id,
            f"✅ You've joined Secret Santa {self.state['current_year']}! 🎄\n\n"
            f"You'll receive your assignment when the event starts!"
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: disnake.RawReactionActionEvent):
        """Handle reaction removes for leaving"""
        if payload.user_id == self.bot.user.id:
            return

        event = self.state.get("current_event")
        if not event or not event.get("active"):
            return

        if event.get("join_closed"):
            return

        if payload.message_id != event.get("announcement_message_id"):
            return

        user_id = str(payload.user_id)

        # Not a participant
        if user_id not in event.get("participants", {}):
            return

        # Check if user has other reactions on the message
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return

            message = await channel.fetch_message(payload.message_id)

            # Check all reactions
            has_reaction = False
            for reaction in message.reactions:
                async for user in reaction.users():
                    if user.id == payload.user_id:
                        has_reaction = True
                        break
                if has_reaction:
                    break

            # Remove if no reactions
            if not has_reaction:
                async with self._lock:
                    event["participants"].pop(user_id, None)
                    self._save()

                await self._send_dm(
                    payload.user_id,
                    "❌ You've left Secret Santa. React again to rejoin!"
                )

        except Exception as e:
            self.logger.error(f"Error handling reaction remove: {e}")


def setup(bot):
    """Setup the cog"""
    bot.add_cog(SecretSantaCog(bot))