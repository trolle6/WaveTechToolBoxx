"""
Custom Events Cog - Modular Event Framework

A flexible event system supporting multiple matching algorithms:
- Fully random teams/pairs
- Timezone-based grouping
- Skill-balanced teams
- History-aware matching
- And more!

Separate from SecretSanta_cog - that stays untouched and perfect for annual Secret Santa!
This handles everything else.
"""

import asyncio
import json
import secrets
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import disnake
from disnake.ext import commands


# Paths
ROOT = Path(__file__).parent
EVENTS_DIR = ROOT / "custom_events"
EVENTS_DIR.mkdir(exist_ok=True)


def autocomplete_safety_wrapper(func):
    """Decorator to ensure autocomplete functions always return a list"""
    async def wrapper(self, inter: disnake.ApplicationCommandInteraction, string: str):
        try:
            result = await func(self, inter, string)
            # Ensure result is always a list
            if isinstance(result, list):
                return [str(item) for item in result]  # Ensure all items are strings
            elif result is None:
                return []
            elif isinstance(result, str):
                self.logger.error(f"{func.__name__} returned string: '{result}'")
                return []
            else:
                try:
                    return [str(item) for item in list(result)]
                except Exception:
                    self.logger.error(f"{func.__name__} returned invalid type: {type(result)}")
                    return []
        except Exception as e:
            self.logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
            return []
    return wrapper


# ============ BASE CLASSES ============

class MatcherInterface(ABC):
    """Base interface for all matching algorithms"""
    
    @abstractmethod
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """Create matches/teams from participants"""
        pass
    
    @abstractmethod
    def get_required_metadata(self) -> List[str]:
        """Return list of required metadata fields"""
        pass
    
    @abstractmethod
    def get_config_options(self) -> Dict[str, Any]:
        """Return available configuration options"""
        pass


class Event:
    """Represents a custom event"""
    
    def __init__(
        self,
        event_id: int,
        name: str,
        matcher_type: str,
        config: Dict[str, Any],
        guild_id: int
    ):
        self.event_id = event_id
        self.name = name
        self.matcher_type = matcher_type
        self.config = config
        self.guild_id = guild_id
        self.participants: Dict[str, Dict[str, Any]] = {}
        self.results: Optional[Dict] = None
        self.status = "setup"  # setup, active, completed
        self.created_at = time.time()
    
    def to_dict(self) -> Dict:
        """Serialize to dict for saving"""
        return {
            "event_id": self.event_id,
            "name": self.name,
            "matcher_type": self.matcher_type,
            "config": self.config,
            "guild_id": self.guild_id,
            "participants": self.participants,
            "results": self.results,
            "status": self.status,
            "created_at": self.created_at,
            "timestamp": datetime.now().isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Event':
        """Deserialize from dict"""
        event = cls(
            event_id=data["event_id"],
            name=data["name"],
            matcher_type=data["matcher_type"],
            config=data["config"],
            guild_id=data["guild_id"]
        )
        event.participants = data.get("participants", {})
        event.results = data.get("results")
        event.status = data.get("status", "setup")
        event.created_at = data.get("created_at", time.time())
        return event


# ============ MATCHER IMPLEMENTATIONS ============

class FullyRandomMatcher(MatcherInterface):
    """Pure random matching - no constraints, no history, just chaos!"""
    
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """Create random teams or pairs"""
        team_size = metadata.get("_config", {}).get("team_size", 2)
        
        shuffled = participants.copy()
        rng = secrets.SystemRandom()
        rng.shuffle(shuffled)
        
        teams = {}
        for i in range(0, len(shuffled), team_size):
            team_members = shuffled[i:i + team_size]
            team_name = f"Team {chr(65 + i // team_size)}"
            teams[team_name] = team_members
        
        return {"teams": teams}
    
    def get_required_metadata(self) -> List[str]:
        return []
    
    def get_config_options(self) -> Dict[str, Any]:
        return {
            "team_size": {
                "type": "int",
                "default": 2,
                "description": "Number of people per team"
            }
        }


class TimezoneGroupedMatcher(MatcherInterface):
    """Groups people by similar timezones"""
    
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """Group by timezone, then create teams within groups"""
        team_size = metadata.get("_config", {}).get("team_size", 2)
        
        # Group by timezone
        tz_groups: Dict[str, List[int]] = {}
        for user_id in participants:
            user_data = metadata.get(user_id, {})
            tz = user_data.get("timezone", "UTC+0")
            tz_groups.setdefault(tz, []).append(user_id)
        
        # Create teams within timezone groups
        teams = {}
        team_counter = 0
        
        for tz, users in tz_groups.items():
            rng = secrets.SystemRandom()
            rng.shuffle(users)
            
            for i in range(0, len(users), team_size):
                team_members = users[i:i + team_size]
                team_name = f"Team {chr(65 + team_counter)}"
                teams[team_name] = team_members
                team_counter += 1
        
        return {"teams": teams, "timezone_groups": tz_groups}
    
    def get_required_metadata(self) -> List[str]:
        return ["timezone"]
    
    def get_config_options(self) -> Dict[str, Any]:
        return {
            "team_size": {
                "type": "int",
                "default": 2,
                "description": "Number of people per team"
            },
            "timezone_tolerance": {
                "type": "int",
                "default": 2,
                "description": "How many hours difference is acceptable"
            }
        }


# ============ FUTURE MATCHERS (Documentation Only) ============
# See original file for extensive documentation of future matcher ideas
# This section intentionally kept minimal to preserve the "from moon to ground" vision


# ============ MAIN COG ============

class CustomEventsCog(commands.Cog):
    """Custom event management with modular matching algorithms"""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("custom_events")
        
        # Matcher registry
        self.matchers = {
            "fully_random": FullyRandomMatcher(),
            "timezone_grouped": TimezoneGroupedMatcher(),
        }
        
        # Active events (in memory)
        self.events: Dict[int, Event] = {}
        self._lock = asyncio.Lock()
        self._next_event_id = 1
        
        self.logger.info("Custom Events cog initialized")
    
    async def cog_load(self):
        """Load saved events"""
        for event_file in EVENTS_DIR.glob("event_*.json"):
            try:
                data = json.loads(event_file.read_text(encoding='utf-8'))
                event = Event.from_dict(data)
                self.events[event.event_id] = event
                if event.event_id >= self._next_event_id:
                    self._next_event_id = event.event_id + 1
            except Exception as e:
                self.logger.error(f"Failed to load event {event_file}: {e}")
        
        self.logger.info("Custom Events cog loaded")
        
        if hasattr(self.bot, 'send_to_discord_log'):
            await self.bot.send_to_discord_log("🎲 Custom Events cog loaded successfully", "SUCCESS")
    
    def cog_unload(self):
        """Save all events"""
        self.logger.info("Saving all events...")
        for event in self.events.values():
            self._save_event(event)
        self.logger.info("Custom Events cog unloaded")
    
    def _save_event(self, event: Event):
        """Save event to disk"""
        try:
            event_file = EVENTS_DIR / f"event_{event.event_id}.json"
            event_file.write_text(
                json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception as e:
            self.logger.error(f"Failed to save event {event.event_id}: {e}")
    
    def _get_event(self, event_id: int) -> Optional[Event]:
        """Get event by ID"""
        return self.events.get(event_id)
    
    def _get_available_events(self, guild_id: int) -> List[Tuple[int, Event]]:
        """Get list of available events for a guild"""
        return [
            (event_id, event) 
            for event_id, event in self.events.items() 
            if event.guild_id == guild_id
        ]
    
    def _ensure_list_result(self, result: Any, function_name: str) -> List[str]:
        """Universal safety wrapper - ensures autocomplete always returns a list"""
        if isinstance(result, list):
            # Ensure all items are strings
            return [str(item) for item in result]
        elif result is None:
            return []
        elif isinstance(result, str):
            # If somehow a string was returned, log it and return empty list
            self.logger.error(f"{function_name} returned string instead of list: {result}")
            return []
        else:
            # Try to convert to list, or return empty
            try:
                return list(result) if result else []
            except Exception:
                self.logger.error(f"{function_name} returned invalid type: {type(result)}")
                return []
    
    async def _autocomplete_event_id(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete function for event_id selection - returns event IDs as strings"""
        try:
            if not inter.guild:
                return []
            
            events = self._get_available_events(inter.guild.id)
            if not events:
                return []
            
            # Sort by event ID (most recent first)
            events.sort(key=lambda x: x[0], reverse=True)
            
            # Filter events that match the input string (by ID or name)
            string_lower = string.lower() if string else ""
            matching_ids = []
            for event_id, event in events:
                # Match by ID or name
                if not string or string_lower in str(event_id) or string_lower in event.name.lower():
                    matching_ids.append(str(event_id))
            
            # Return up to 25 options (Discord limit)
            result = matching_ids[:25]
            return self._ensure_list_result(result, "_autocomplete_event_id")
        except Exception as e:
            self.logger.error(f"Error in event_id autocomplete: {e}", exc_info=True)
            return []  # Always return a list, even on error
    
    @autocomplete_safety_wrapper
    async def autocomplete_event_id_join(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for join event_id parameter"""
        try:
            result = await self._autocomplete_event_id(inter, string)
            return self._ensure_list_result(result, "autocomplete_event_id_join")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_event_id_join: {e}", exc_info=True)
            return []
    
    @autocomplete_safety_wrapper
    async def autocomplete_event_id_shuffle(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for shuffle event_id parameter"""
        try:
            result = await self._autocomplete_event_id(inter, string)
            return self._ensure_list_result(result, "autocomplete_event_id_shuffle")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_event_id_shuffle: {e}", exc_info=True)
            return []
    
    @autocomplete_safety_wrapper
    async def autocomplete_event_id_view(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for view event_id parameter"""
        try:
            result = await self._autocomplete_event_id(inter, string)
            return self._ensure_list_result(result, "autocomplete_event_id_view")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_event_id_view: {e}", exc_info=True)
            return []
    
    @autocomplete_safety_wrapper
    async def autocomplete_event_id_stop(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for stop event_id parameter"""
        try:
            result = await self._autocomplete_event_id(inter, string)
            return self._ensure_list_result(result, "autocomplete_event_id_stop")
        except Exception as e:
            self.logger.error(f"Error in autocomplete_event_id_stop: {e}", exc_info=True)
            return []
    
    # ============ COMMANDS ============
    
    @commands.slash_command(name="event")
    async def event_root(self, inter: disnake.ApplicationCommandInteraction):
        """Custom event commands"""
        pass
    
    @event_root.sub_command(name="create", description="Create a new custom event")
    @commands.has_permissions(manage_guild=True)
    async def event_create(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(description="Event name"),
        matcher: str = commands.Param(
            description="Matching algorithm",
            choices=["fully_random", "timezone_grouped"]
        ),
        team_size: int = commands.Param(default=2, description="Team size", ge=2, le=10)
    ):
        """Create new event"""
        await inter.response.defer(ephemeral=True)
        
        try:
            if len(name) > 100:
                await inter.edit_original_response(content="❌ Event name too long (max 100 characters)")
                return
            
            existing_names = [e.name for e in self.events.values() if e.guild_id == inter.guild.id]
            if name in existing_names:
                await inter.edit_original_response(content="❌ An event with this name already exists in this server")
                return
            
            async with self._lock:
                event_id = self._next_event_id
                self._next_event_id += 1
                
                event = Event(
                    event_id=event_id,
                    name=name,
                    matcher_type=matcher,
                    config={"team_size": team_size},
                    guild_id=inter.guild.id
                )
                
                self.events[event_id] = event
                self._save_event(event)
        except Exception as e:
            self.logger.error(f"Event creation failed: {e}")
            await inter.edit_original_response(content="❌ Failed to create event. Please try again.")
            return
        
        embed = disnake.Embed(
            title="✅ Event Created!",
            description=f"**{name}** (ID: {event_id})",
            color=disnake.Color.green()
        )
        embed.add_field(name="Algorithm", value=matcher, inline=True)
        embed.add_field(name="Team Size", value=str(team_size), inline=True)
        embed.add_field(
            name="Next Steps",
            value=f"• Users can join with `/event join {event_id}`\n"
                  f"• When ready, run `/event shuffle {event_id}`",
            inline=False
        )
        
        await inter.edit_original_response(embed=embed)
    
    @event_root.sub_command(name="join", description="Join an event")
    async def event_join(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_join"),
        timezone: str = commands.Param(default="UTC+0", description="Your timezone (e.g., UTC+2, UTC-5)")
    ):
        """Join an event"""
        await inter.response.defer(ephemeral=True)
        
        event = self._get_event(event_id)
        if not event:
            await inter.edit_original_response(content="❌ Event not found")
            return
        
        if event.status != "setup":
            await inter.edit_original_response(content="❌ Event is not accepting new participants")
            return
        
        user_id = str(inter.author.id)
        
        if user_id in event.participants:
            await inter.edit_original_response(content="❌ You've already joined this event")
            return
        
        async with self._lock:
            event.participants[user_id] = {
                "name": inter.author.display_name,
                "timezone": timezone,
                "joined_at": time.time()
            }
            self._save_event(event)
        
        embed = disnake.Embed(
            title="✅ Joined Event!",
            description=f"You've joined **{event.name}**",
            color=disnake.Color.green()
        )
        embed.add_field(name="Event ID", value=str(event_id), inline=True)
        embed.add_field(name="Participants", value=str(len(event.participants)), inline=True)
        embed.set_footer(text="Wait for the organizer to shuffle teams!")
        
        await inter.edit_original_response(embed=embed)
    
    @event_root.sub_command(name="shuffle", description="Run the matching algorithm")
    @commands.has_permissions(manage_guild=True)
    async def event_shuffle(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_shuffle")
    ):
        """Run matching algorithm"""
        await inter.response.defer(ephemeral=True)
        
        event = self._get_event(event_id)
        if not event:
            await inter.edit_original_response(content="❌ Event not found")
            return
        
        if len(event.participants) < 2:
            await inter.edit_original_response(content="❌ Need at least 2 participants")
            return
        
        matcher = self.matchers.get(event.matcher_type)
        if not matcher:
            await inter.edit_original_response(content=f"❌ Unknown matcher: {event.matcher_type}")
            return
        
        # Prepare metadata
        participant_ids = [int(uid) for uid in event.participants.keys()]
        metadata = {int(uid): data for uid, data in event.participants.items()}
        metadata["_config"] = event.config
        
        try:
            results = matcher.match(participant_ids, metadata)
            
            async with self._lock:
                event.results = results
                event.status = "active"
                self._save_event(event)
            
            # Format results
            embed = disnake.Embed(
                title="✅ Teams Created!",
                description=f"**{event.name}** - Matching complete!",
                color=disnake.Color.blue()
            )
            
            # Show teams
            if "teams" in results:
                for team_name, members in list(results["teams"].items())[:10]:
                    member_names = []
                    for uid in members:
                        member = inter.guild.get_member(uid)
                        if member:
                            member_names.append(member.display_name)
                        else:
                            member_names.append(f"User {uid}")
                    
                    embed.add_field(
                        name=f"🎯 {team_name}",
                        value="\n".join(f"• {name}" for name in member_names),
                        inline=True
                    )
            
            embed.set_footer(text=f"Algorithm: {event.matcher_type} | Participants: {len(event.participants)}")
            
            await inter.edit_original_response(embed=embed)
            
        except Exception as e:
            self.logger.error(f"Matching failed: {e}", exc_info=True)
            await inter.edit_original_response(content=f"❌ Matching failed: {e}")
    
    @event_root.sub_command(name="view", description="View event results")
    async def event_view(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_view")
    ):
        """View event results"""
        await inter.response.defer(ephemeral=True)
        
        event = self._get_event(event_id)
        if not event:
            await inter.edit_original_response(content="❌ Event not found")
            return
        
        if not event.results:
            await inter.edit_original_response(content="❌ Event hasn't been shuffled yet")
            return
        
        embed = disnake.Embed(
            title=f"🎯 {event.name}",
            description=f"Event Results (ID: {event_id})",
            color=disnake.Color.blue()
        )
        
        if "teams" in event.results:
            for team_name, members in list(event.results["teams"].items())[:10]:
                member_names = []
                for uid in members:
                    member = inter.guild.get_member(uid)
                    if member:
                        member_names.append(member.display_name)
                    else:
                        member_names.append(f"User {uid}")
                
                embed.add_field(
                    name=f"🎯 {team_name}",
                    value="\n".join(f"• {name}" for name in member_names),
                    inline=True
                )
        
        embed.set_footer(text=f"Algorithm: {event.matcher_type} | Status: {event.status}")
        
        await inter.edit_original_response(embed=embed)
    
    @event_root.sub_command(name="stop", description="Stop and archive event")
    @commands.has_permissions(manage_guild=True)
    async def event_stop(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_stop")
    ):
        """Stop event"""
        await inter.response.defer(ephemeral=True)
        
        event = self._get_event(event_id)
        if not event:
            await inter.edit_original_response(content="❌ Event not found")
            return
        
        async with self._lock:
            event.status = "completed"
            self._save_event(event)
            
            # Move to archive
            archive_file = EVENTS_DIR / f"archive_{event.event_id}_{event.name.replace(' ', '_')}.json"
            event_file = EVENTS_DIR / f"event_{event.event_id}.json"
            
            try:
                event_file.rename(archive_file)
            except Exception as e:
                self.logger.error(f"Failed to archive: {e}")
        
        await inter.edit_original_response(content=f"✅ Event **{event.name}** stopped and archived!")
    
    @event_root.sub_command(name="list", description="List all events")
    async def event_list(self, inter: disnake.ApplicationCommandInteraction):
        """List events"""
        await inter.response.defer(ephemeral=True)
        
        if not self.events:
            await inter.edit_original_response(content="❌ No active events")
            return
        
        embed = disnake.Embed(
            title="🎲 Active Events",
            description=f"{len(self.events)} event(s)",
            color=disnake.Color.blue()
        )
        
        for event in list(self.events.values())[:10]:
            status_emoji = {"setup": "⏳", "active": "✅", "completed": "🏁"}.get(event.status, "❓")
            
            embed.add_field(
                name=f"{status_emoji} {event.name} (ID: {event.event_id})",
                value=f"Algorithm: {event.matcher_type}\n"
                      f"Participants: {len(event.participants)}\n"
                      f"Status: {event.status}",
                inline=False
            )
        
        await inter.edit_original_response(embed=embed)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(CustomEventsCog(bot))
