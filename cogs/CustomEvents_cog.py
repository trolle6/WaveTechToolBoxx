"""
Custom Events Cog - Modular Event Framework

A flexible event system supporting multiple matching algorithms:
- Fully random teams/pairs
- Timezone-based grouping
- Skill-balanced teams
- History-aware matching
- And more!

Separate from SecretSanta_cog (annual Secret Santa). This handles other group-matching events.
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

from .secret_santa_views import EventListPaginator
from .secret_santa_checks import manage_guild_check, safe_display_name
from .utils import autocomplete_safety_wrapper


# Paths
ROOT = Path(__file__).parent
EVENTS_DIR = ROOT / "custom_events"
EVENTS_DIR.mkdir(exist_ok=True)


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
        config = metadata.get("_config", {})
        team_size = config.get("team_size", 2) if isinstance(config, dict) else 2
        
        shuffled = participants.copy()
        rng = secrets.SystemRandom()
        rng.shuffle(shuffled)
        
        teams = {}
        for i in range(0, len(shuffled), team_size):
            team_members = shuffled[i:i + team_size]
            team_name = f"Team {(i // team_size) + 1}"
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
        config = metadata.get("_config", {})
        team_size = config.get("team_size", 2) if isinstance(config, dict) else 2
        
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
                team_name = f"Team {team_counter + 1}"
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

    def _resolve_event(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int,
    ) -> tuple[Optional[Event], Optional[str]]:
        """Return (event, None) or (None, user-facing error message)."""
        if not inter.guild:
            return None, "❌ This command must be used in a server"
        event = self._get_event(event_id)
        if not event:
            return None, "❌ Event not found"
        if event.guild_id != inter.guild.id:
            return None, "❌ That event belongs to another server"
        return event, None
    
    def _get_available_events(self, guild_id: int) -> List[Tuple[int, Event]]:
        """Get list of available events for a guild"""
        return [
            (event_id, event) 
            for event_id, event in self.events.items() 
            if event.guild_id == guild_id
        ]
    
    @autocomplete_safety_wrapper
    async def _autocomplete_event_id(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete function for event_id selection - returns event IDs as strings."""
        if not inter.guild:
            return []

        events = self._get_available_events(inter.guild.id)
        if not events:
            return []

        events.sort(key=lambda x: x[0], reverse=True)
        string_lower = (string or "").lower()
        return [
            str(event_id)
            for event_id, event in events
            if not string or string_lower in str(event_id) or string_lower in event.name.lower()
        ][:25]

    async def autocomplete_event_id_join(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for join event_id parameter."""
        return await self._autocomplete_event_id(inter, string)

    async def autocomplete_event_id_shuffle(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for shuffle event_id parameter."""
        return await self._autocomplete_event_id(inter, string)

    async def autocomplete_event_id_view(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for view event_id parameter."""
        return await self._autocomplete_event_id(inter, string)

    async def autocomplete_event_id_stop(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for stop event_id parameter."""
        return await self._autocomplete_event_id(inter, string)

    @autocomplete_safety_wrapper
    async def autocomplete_timezone_join(self, inter: disnake.ApplicationCommandInteraction, string: str) -> List[str]:
        """Autocomplete for join timezone parameter - suggests common timezones."""
        common = ["UTC-12", "UTC-11", "UTC-10", "UTC-9", "UTC-8", "UTC-7", "UTC-6", "UTC-5",
                  "UTC-4", "UTC-3", "UTC-2", "UTC-1", "UTC+0", "UTC+1", "UTC+2", "UTC+3",
                  "UTC+4", "UTC+5", "UTC+6", "UTC+7", "UTC+8", "UTC+9", "UTC+10", "UTC+11",
                  "UTC+12", "UTC+13", "UTC+14"]
        named = ["UTC", "EST", "PST", "CST", "MST", "GMT", "CET", "JST", "AEST"]
        string_lower = (string or "").lower()
        return [tz for tz in common + named if string_lower in tz.lower() or not string][:25]
    
    # ============ COMMANDS ============
    
    @commands.slash_command(name="event")
    async def event_root(self, inter: disnake.ApplicationCommandInteraction):
        """Custom event commands"""
        pass
    
    @event_root.sub_command(name="create", description="Create a new custom event")
    @manage_guild_check()
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

        if not inter.guild:
            await inter.edit_original_response(content="❌ This command must be used in a server")
            return
        
        try:
            if len(name) > 100:
                await inter.edit_original_response(content="❌ Event name too long (max 100 characters)")
                return
            
            async with self._lock:
                existing_names = [e.name for e in self.events.values() if e.guild_id == inter.guild.id]
                if name in existing_names:
                    await inter.edit_original_response(content="❌ An event with this name already exists in this server")
                    return
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
        timezone: str = commands.Param(default="UTC+0", description="Your timezone (e.g., UTC+2, UTC-5)", autocomplete="autocomplete_timezone_join")
    ):
        """Join an event"""
        await inter.response.defer(ephemeral=True)
        
        event, err = self._resolve_event(inter, event_id)
        if err:
            await inter.edit_original_response(content=err)
            return
        
        if event.status != "setup":
            await inter.edit_original_response(content="❌ Event is not accepting new participants")
            return
        
        user_id = str(inter.author.id)

        async with self._lock:
            if user_id in event.participants:
                already_joined = True
            else:
                already_joined = False
                event.participants[user_id] = {
                    "name": safe_display_name(inter.author),
                    "timezone": timezone,
                    "joined_at": time.time(),
                }
                self._save_event(event)

        if already_joined:
            await inter.edit_original_response(content="❌ You've already joined this event")
            return
        
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
    @manage_guild_check()
    async def event_shuffle(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_shuffle")
    ):
        """Run matching algorithm"""
        await inter.response.defer(ephemeral=True)
        
        event, err = self._resolve_event(inter, event_id)
        if err:
            await inter.edit_original_response(content=err)
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
        
        event, err = self._resolve_event(inter, event_id)
        if err:
            await inter.edit_original_response(content=err)
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
    @manage_guild_check()
    async def event_stop(
        self,
        inter: disnake.ApplicationCommandInteraction,
        event_id: int = commands.Param(description="Event ID", autocomplete="autocomplete_event_id_stop")
    ):
        """Stop event"""
        await inter.response.defer(ephemeral=True)
        
        event, err = self._resolve_event(inter, event_id)
        if err:
            await inter.edit_original_response(content=err)
            return
        
        async with self._lock:
            event.status = "completed"
            self._save_event(event)
            
            # Move to archive (sanitize name for filesystem)
            safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in event.name).replace(" ", "_")
            archive_file = EVENTS_DIR / f"archive_{event.event_id}_{safe_name}.json"
            event_file = EVENTS_DIR / f"event_{event.event_id}.json"
            
            try:
                if event_file.exists():
                    event_file.rename(archive_file)
                self.events.pop(event_id, None)
            except Exception as e:
                self.logger.error(f"Failed to archive: {e}")
        
        await inter.edit_original_response(content=f"✅ Event **{event.name}** stopped and archived!")
    
    @event_root.sub_command(name="list", description="List all events")
    async def event_list(self, inter: disnake.ApplicationCommandInteraction):
        """List events"""
        await inter.response.defer(ephemeral=True)
        
        if not inter.guild:
            await inter.edit_original_response(content="❌ This command must be used in a server")
            return

        events_list = [e for e in self.events.values() if e.guild_id == inter.guild.id]
        if not events_list:
            await inter.edit_original_response(content="❌ No active events in this server")
            return
        
        # Use paginator if more than 10 events, otherwise show all
        if len(events_list) > 10:
            paginator = EventListPaginator(events_list, timeout=300)
            embed = paginator.get_embed()
            await inter.edit_original_response(embed=embed, view=paginator)
        else:
            # Show all events on one page (no pagination needed)
            embed = disnake.Embed(
                title="🎲 Active Events",
                description=f"{len(events_list)} event(s)",
                color=disnake.Color.blue()
            )
            
            for event in events_list:
                status_emoji = {"setup": "⏳", "active": "✅", "completed": "🏁"}.get(event.status, "❓")
                
                embed.add_field(
                    name=f"{status_emoji} {event.name} (ID: {event.event_id})",
                    value=f"Algorithm: {event.matcher_type}\n"
                          f"Participants: {len(event.participants)}\n"
                          f"Status: {event.status}",
                    inline=False
                )
            
            embed.set_footer(text=f"Total: {len(events_list)} event(s)")
            await inter.edit_original_response(embed=embed)


def setup(bot):
    """Setup the cog"""
    bot.add_cog(CustomEventsCog(bot))
