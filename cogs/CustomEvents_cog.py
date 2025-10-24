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


# ============ BASE CLASSES ============

class MatcherInterface(ABC):
    """
    Base interface for all matching algorithms.
    Each matcher implements a different strategy for assigning participants.
    """
    
    @abstractmethod
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """
        Create matches/teams from participants.
        
        Args:
            participants: List of user IDs
            metadata: Dict mapping user_id → their data (timezone, skill, etc.)
        
        Returns:
            Dict of results (structure depends on matcher type)
            Example for teams: {"Team A": [user1, user2], "Team B": [user3, user4]}
            Example for pairs: {"pairs": [[user1, user2], [user3, user4]]}
        """
        pass
    
    @abstractmethod
    def get_required_metadata(self) -> List[str]:
        """Return list of required metadata fields (e.g., ["timezone", "skill"])"""
        pass
    
    @abstractmethod
    def get_config_options(self) -> Dict[str, Any]:
        """Return available configuration options for this matcher"""
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
        self.participants: Dict[str, Dict[str, Any]] = {}  # user_id → data
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

"""
MATCHER LIBRARY - "FROM THE MOON TO THE GROUND"

Below are ALL the matcher types you could implement.
Currently implemented matchers are coded.
Future matchers are documented for when you need them!
"""


# ============ IMPLEMENTED MATCHERS ============

class FullyRandomMatcher(MatcherInterface):
    """
    Pure random matching - no constraints, no history, just chaos!
    Fastest and simplest matcher.
    """
    
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """Create random teams or pairs"""
        
        team_size = metadata.get("_config", {}).get("team_size", 2)
        
        # Shuffle participants
        shuffled = participants.copy()
        rng = secrets.SystemRandom()
        rng.shuffle(shuffled)
        
        # Create teams
        teams = {}
        for i in range(0, len(shuffled), team_size):
            team_members = shuffled[i:i + team_size]
            team_name = f"Team {chr(65 + i // team_size)}"  # Team A, Team B, etc.
            teams[team_name] = team_members
        
        return {"teams": teams}
    
    def get_required_metadata(self) -> List[str]:
        return []  # No metadata needed!
    
    def get_config_options(self) -> Dict[str, Any]:
        return {
            "team_size": {
                "type": "int",
                "default": 2,
                "description": "Number of people per team"
            }
        }


class TimezoneGroupedMatcher(MatcherInterface):
    """
    Groups people by similar timezones.
    Useful for international servers wanting coordinated events.
    """
    
    def match(
        self,
        participants: List[int],
        metadata: Dict[int, Dict[str, Any]]
    ) -> Dict[str, List[int]]:
        """Group by timezone, then create teams within groups"""
        
        team_size = metadata.get("_config", {}).get("team_size", 2)
        tolerance = metadata.get("_config", {}).get("timezone_tolerance", 2)  # ±2 hours
        
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
            # Shuffle users in this timezone
            rng = secrets.SystemRandom()
            rng.shuffle(users)
            
            # Make teams
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


# ============ FUTURE MATCHERS (To Implement When Needed) ============

"""
# SkillBalancedMatcher - Balance teams by skill level
#
# USE: Building competitions, PvP events, any competitive teams
# COLLECTS: skill_level (1-10 or beginner/intermediate/expert)
# LOGIC:
#   - Calculate average skill per team
#   - Distribute high/medium/low skill evenly
#   - Each team gets balanced mix
#   Example: Team A [skill 9, 5, 7, 3] avg=6, Team B [skill 8, 4, 6, 4] avg=5.5
#
# class SkillBalancedMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Sort by skill
#         # Distribute evenly (snake draft style)
#         # Team A gets: 1st, 8th, 9th, 16th
#         # Team B gets: 2nd, 7th, 10th, 15th
#         # etc.
"""

"""
# RoleBalancedMatcher - Each team gets one of each role
#
# USE: Team events needing role diversity (Builder, Redstoner, Fighter, Explorer)
# COLLECTS: preferred_role
# LOGIC:
#   - Group participants by role
#   - Each team gets 1 of each role
#   - Ensures balanced team composition
#   Example: Team A [1 Builder, 1 Redstoner, 1 Fighter], Team B [same]
#
# class RoleBalancedMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Group by role
#         # Distribute one of each role per team
#         # If uneven, some teams get extras
"""

"""
# AvoidRecentMatcher - Lightweight history (only last event)
#
# USE: Weekly/monthly recurring events
# COLLECTS: Nothing (reads last event data)
# LOGIC:
#   - Load last event results
#   - Avoid pairing people who were teamed last time
#   - Much lighter than Secret Santa's multi-year tracking
#   Example: Last week A-B → This week A gets C, B gets D
#
# class AvoidRecentMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load last event from archive
#         # Extract last pairings
#         # Shuffle avoiding those pairs
#         # Like Secret Santa but only 1 event back
"""

"""
# RoundRobinMatcher - Everyone meets everyone eventually
#
# USE: Speed friending, networking, mentorship rotation
# COLLECTS: Nothing (tracks internally)
# LOGIC:
#   - Tracks who has met who across multiple events
#   - Each shuffle creates NEW pairs nobody has had
#   - Eventually everyone pairs with everyone
#   - Like a tournament schedule but for socializing
#   Example: Week 1: A-B, C-D | Week 2: A-C, B-D | Week 3: A-D, B-C
#
# class RoundRobinMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load pairing history
#         # Find pairs that haven't met
#         # Schedule next round
"""

"""
# GeographicMatcher - Match by region/country
#
# USE: Regional meetups, language groups, local coordination
# COLLECTS: country, region, or city
# LOGIC:
#   - Groups by geographic proximity
#   - Can create regional teams
#   - Good for servers with IRL meetup potential
#   Example: EU Team, NA Team, Asia Team
#
# class GeographicMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Group by region
#         # Create region-based teams
#         # Balance team sizes if possible
"""

"""
# LanguageMatcher - Match by shared languages
#
# USE: International servers, language learning, inclusion
# COLLECTS: languages (list of spoken languages)
# LOGIC:
#   - Pairs/groups people who share a language
#   - Prioritizes less common languages for inclusion
#   - Helps non-English speakers connect
#   Example: [English, Swedish] matches with [English, Norwegian]
#
# class LanguageMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Extract language preferences
#         # Find common languages
#         # Group by shared languages
"""

"""
# DiscordRoleMatcher - Match or separate by Discord roles
#
# USE: Role-specific events, cross-role mixing, permission-based
# COLLECTS: Nothing (auto-fetches Discord roles)
# LOGIC:
#   - Reads member.roles from Discord
#   - Mode 1: Group people WITH same role
#   - Mode 2: Mix people ACROSS roles
#   Example: "Builder" role only event, or "Mix builders with redstoners"
#
# class DiscordRoleMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Fetch roles from Discord
#         # Group or mix based on config
#         # Filter by role requirements
"""

"""
# ActivityBasedMatcher - Match by online patterns
#
# USE: Find people active at same times
# COLLECTS: Nothing (checks real-time status)
# LOGIC:
#   - Checks who's online NOW
#   - Or tracks activity patterns over time
#   - Pairs people likely to be online together
#   Example: Night owls matched together, day people matched together
#
# class ActivityBasedMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Check current online status
#         # Or load activity history
#         # Group similar activity patterns
"""

"""
# PreferenceMatcher - Match by user preferences
#
# USE: Interest-based pairing, compatibility matching
# COLLECTS: preferences (favorite games, interests, etc.)
# LOGIC:
#   - Users rank preferences
#   - Algorithm tries to match compatible preferences
#   - Can weight preferences (primary vs secondary)
#   Example: Match people who like same games
#
# class PreferenceMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Compare preference lists
#         # Calculate compatibility scores
#         # Match highest compatibility
"""

"""
# SkillProgressionMatcher - Mentor/mentee pairing
#
# USE: Learning events, skill progression, teaching
# COLLECTS: skill_level + wants_mentor (bool)
# LOGIC:
#   - Pairs experienced with beginners
#   - Or similar skills for fair competition
#   - Can track improvement over multiple events
#   Example: Skill 8 paired with Skill 3 for mentoring
#
# class SkillProgressionMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Sort by skill
#         # Pair high with low (mentoring mode)
#         # Or pair similar (competition mode)
"""

"""
# RotationMatcher - Ensure variety over multiple events
#
# USE: Recurring events where you want everyone to team with everyone
# COLLECTS: Nothing (tracks past teams)
# LOGIC:
#   - Tracks who's teamed recently (last N events)
#   - Prioritizes NEW combinations
#   - Ensures maximum variety over time
#   Example: In 10 events, you team with 10 different people
#
# class RotationMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load last N events
#         # Track who's teamed with who
#         # Create teams maximizing NEW pairings
"""

"""
# BracketMatcher - Tournament bracket creation
#
# USE: Competitions, tournaments, elimination events
# COLLECTS: skill_level (optional for seeding)
# LOGIC:
#   - Creates single/double elimination brackets
#   - Skill-based seeding (high vs low first)
#   - Or random seeding
#   Example: 16 people → 8 matches → 4 matches → 2 matches → winner
#
# class BracketMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Create tournament tree
#         # Seed by skill or random
#         # Return bracket structure
"""

"""
# VoiceChannelMatcher - Match people currently in same VC
#
# USE: Organize people already in voice
# COLLECTS: Nothing (reads real-time voice state)
# LOGIC:
#   - Checks who's in which voice channel
#   - Groups people already in same VC
#   - Or creates new teams and moves them
#   Example: Auto-organize 12 people in VC into 3 teams of 4
#
# class VoiceChannelMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Query voice state
#         # Group by current channel
#         # Or create new distribution
"""

"""
# BalancedPairsMatcher - Create pairs, balanced by multiple factors
#
# USE: 1-on-1 events with balance needs
# COLLECTS: Multiple factors (skill, timezone, role, etc.)
# LOGIC:
#   - Weighted combination of factors
#   - Example: 50% skill balance, 30% timezone, 20% role
#   - Creates fair, compatible pairs
#   Example: High skill + Low skill pairs, same timezone
#
# class BalancedPairsMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Calculate compatibility scores
#         # Weight multiple factors
#         # Create optimal pairs
"""

"""
# ChainMatcher - Create chains (A→B→C→D→A)
#
# USE: Gift chains, message chains, tag events
# COLLECTS: Optional history for variety
# LOGIC:
#   - Creates one long chain through all participants
#   - Like Secret Santa structure but for other purposes
#   - Can avoid recent chains for recurring events
#   Example: Message chain, build relay, tag-you're-it
#
# class ChainMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Shuffle participants
#         # Create chain: [0]→[1]→[2]→...→[0]
#         # Avoid recent chains if history exists
"""

"""
# SnakeDraftMatcher - Teams pick participants in snake order
#
# USE: Team building with strategy, captain selection
# COLLECTS: team_captains (who picks)
# LOGIC:
#   - Team A picks, then Team B, then Team C
#   - Then Team C picks, Team B, Team A (reverse!)
#   - Like sports draft
#   Example: 3 captains pick teams of 5
#
# class SnakeDraftMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Captains pick in order: A, B, C, C, B, A, A, B, C...
#         # Or auto-draft by skill ranking
"""

"""
# ExperienceSpreadMatcher - Each team gets mix of experience levels
#
# USE: Learning events, knowledge sharing
# COLLECTS: experience_years or experience_level
# LOGIC:
#   - Groups: Newbies (0-1yr), Medium (1-3yr), Experts (3+yr)
#   - Each team gets mix: 1 expert, 2 medium, 1 newbie
#   - Balances knowledge distribution
#   Example: Build teams with mentors and learners
#
# class ExperienceSpreadMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Categorize by experience
#         # Distribute evenly across teams
"""

"""
# OnlineNowMatcher - Only match people currently online
#
# USE: Impromptu events, real-time coordination
# COLLECTS: Nothing (checks Discord status)
# LOGIC:
#   - Filters to only online/active users
#   - Creates teams from available people
#   - Ignores offline participants
#   Example: "Quick game night with whoever's around"
#
# class OnlineNowMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Check member.status
#         # Filter to online only
#         # Create teams from available
"""

"""
# AvoidanceListMatcher - Respect user blacklists
#
# USE: Drama prevention, preference respect
# COLLECTS: avoid_list (users they don't want to team with)
# LOGIC:
#   - Each user can mark people to avoid
#   - Algorithm respects all blacklists
#   - Creates drama-free teams
#   Example: "I don't want to team with UserX"
#   NOTE: Could be used for good or evil 😂
#
# class AvoidanceListMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load avoid lists
#         # Create teams respecting all constraints
#         # Might be impossible with too many avoidances!
"""

"""
# SeasonalRotationMatcher - Rotate through different team structures
#
# USE: Long-term events with variety
# COLLECTS: Nothing (uses event number)
# LOGIC:
#   - Event 1: Random teams
#   - Event 2: Timezone teams
#   - Event 3: Skill balanced
#   - Event 4: Back to random
#   - Automatically rotates algorithm each time!
#   Example: Monthly events with different matching each time
#
# class SeasonalRotationMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Check event count
#         # Rotate through matcher types
#         # Keeps events fresh!
"""

"""
# FriendGroupMatcher - Keep friend groups together
#
# USE: Social events, coordinated teams
# COLLECTS: friend_group_id (optional)
# LOGIC:
#   - Users can mark "I'm with these friends"
#   - Algorithm keeps friend groups on same team
#   - Fills remaining spots randomly
#   Example: Pre-made duos in larger teams
#
# class FriendGroupMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Extract friend groups
#         # Place groups on same team
#         # Fill remaining with randoms
"""

"""
# WeightedRandomMatcher - Random with preference weights
#
# USE: Mostly random but respect some preferences
# COLLECTS: preferences with weights
# LOGIC:
#   - 80% random
#   - 20% tries to honor preferences
#   - Balance between chaos and choice
#   Example: "Prefer builders but okay with anyone"
#
# class WeightedRandomMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Random base assignment
#         # Swap some pairs to honor preferences
#         # Don't overthink it
"""

"""
# MinMaxBalancedMatcher - Ensure no team too strong/weak
#
# USE: Competitive balance
# COLLECTS: power_level, skill, rating, etc.
# LOGIC:
#   - Calculate team total power
#   - Minimize difference between strongest and weakest team
#   - No stomps, all teams competitive
#   Example: Team totals: 24, 25, 23, 26 (very close!)
#
# class MinMaxBalancedMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Calculate power levels
#         # Balance teams to minimize variance
#         # Closest total power possible
"""

"""
# NewcomerPriorityMatcher - Give newbies good teams
#
# USE: Welcoming new members
# COLLECTS: join_date or is_new flag
# LOGIC:
#   - Identifies new members (joined <30 days ago)
#   - Pairs them with friendly veterans
#   - Or ensures each team has veteran guide
#   Example: Each team gets 1 newbie + 3 veterans
#
# class NewcomerPriorityMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Check join dates
#         # Pair newbies with veterans
#         # Welcoming experience!
"""

"""
# ParticipationRewardMatcher - Reward frequent participants
#
# USE: Loyalty rewards, engagement
# COLLECTS: Nothing (reads participation history)
# LOGIC:
#   - Tracks how many events each person has joined
#   - Frequent participants get captain roles
#   - Or get first pick of teammates
#   - Encourages participation!
#   Example: Top 3 participants become team captains
#
# class ParticipationRewardMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Count past participations
#         # Reward high participation
#         # Give them choice/priority
"""

"""
# HybridMultiFactorMatcher - Combine MULTIPLE matchers!
#
# USE: Complex events with many requirements
# COLLECTS: Everything (timezone, skill, role, preferences)
# LOGIC:
#   - Primary: Timezone grouping (40% weight)
#   - Secondary: Skill balancing (30% weight)
#   - Tertiary: Role distribution (20% weight)
#   - Fallback: Random (10% weight)
#   - Optimizes for all factors simultaneously!
#   Example: Timezone teams that are also skill-balanced
#
# class HybridMultiFactorMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Run multiple matchers
#         # Combine results with weights
#         # Optimization algorithm
#         # MOST COMPLEX but MOST FLEXIBLE!
"""

"""
# ReverseSecretSantaMatcher - Fun twist on Secret Santa
#
# USE: Post-Secret Santa revenge gifting
# COLLECTS: Nothing (reads Secret Santa archives)
# LOGIC:
#   - Load last year's Secret Santa
#   - Reverse assignments: If A gave to B, now B gives to A!
#   - Full circle completion
#   - Fun callback to previous year
#   Example: 2024 huntoon→trolle, 2025 trolle→huntoon
#
# class ReverseSecretSantaMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Read cogs/archive/2024.json
#         # Reverse all assignments
#         # Create opposite direction pairs
"""

"""
# AvailabilityMatcher - Match by schedule compatibility
#
# USE: Coordinated events, scheduled activities
# COLLECTS: available_days, available_hours
# LOGIC:
#   - Finds people with overlapping schedules
#   - Creates teams that can actually meet
#   - Good for time-sensitive coordination
#   Example: Match people free on same weekends
#
# class AvailabilityMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Parse availability windows
#         # Find overlaps
#         # Group compatible schedules
"""

"""
# FairnessTrackingMatcher - Ensure everyone gets good teams over time
#
# USE: Long-term fairness, prevent favoritism
# COLLECTS: Nothing (tracks team quality history)
# LOGIC:
#   - Tracks past team quality per person
#   - If you got weak team last time, get strong team this time
#   - Balances luck over multiple events
#   - Nobody feels left out long-term
#   Example: Person had bad teams 3 times → gets good team next
#
# class FairnessTrackingMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load past team assignments
#         # Calculate "luck score" per person
#         # Compensate for past bad luck
"""

"""
# SeededRandomMatcher - Reproducible random (for testing)
#
# USE: Testing, demonstrations
# COLLECTS: seed (number for reproducibility)
# LOGIC:
#   - Same seed = same results every time
#   - Good for testing algorithm changes
#   - Can recreate exact same teams
#   Example: Seed 12345 always produces same teams
#
# class SeededRandomMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Use seed for random generator
#         # Shuffle with seed
#         # Reproducible results
"""

"""
# MinecraftStatsMatcher - Match by in-game statistics
#
# USE: Minecraft-specific events
# COLLECTS: External stats (playtime, achievements, etc.)
# LOGIC:
#   - Import stats from game server
#   - Balance teams by playtime, builds, kills, etc.
#   - Server-specific matching
#   Example: Balance PvP teams by kill/death ratio
#
# class MinecraftStatsMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load external stats file
#         # Extract relevant stats
#         # Balance teams accordingly
"""

"""
# AdaptiveMatcher - Learns from past events
#
# USE: Machine learning approach (advanced!)
# COLLECTS: Nothing (analyzes past event success)
# LOGIC:
#   - Tracks which team compositions worked well
#   - Learns what makes good teams
#   - Adapts algorithm based on feedback
#   - Gets smarter over time!
#   Example: "Teams with X+Y combo win more → create more X+Y combos"
#   NOTE: This is ADVANCED and probably overkill 😂
#
# class AdaptiveMatcher(MatcherInterface):
#     def match(self, participants, metadata):
#         # Load past event results
#         # Analyze successful patterns
#         # Apply learned patterns
#         # AI-powered matching!
"""


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
        # Load any saved events
        for event_file in EVENTS_DIR.glob("event_*.json"):
            try:
                with open(event_file, 'r') as f:
                    data = json.load(f)
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
            with open(event_file, 'w') as f:
                json.dump(event.to_dict(), f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save event {event.event_id}: {e}")
    
    def _get_event(self, event_id: int) -> Optional[Event]:
        """Get event by ID"""
        return self.events.get(event_id)
    
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
        
        # Create event
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
        event_id: int = commands.Param(description="Event ID"),
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
        
        # Check if already joined
        if user_id in event.participants:
            await inter.edit_original_response(content="❌ You've already joined this event")
            return
        
        # Add participant
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
        event_id: int = commands.Param(description="Event ID")
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
        
        # Get matcher
        matcher = self.matchers.get(event.matcher_type)
        if not matcher:
            await inter.edit_original_response(content=f"❌ Unknown matcher: {event.matcher_type}")
            return
        
        # Prepare metadata
        participant_ids = [int(uid) for uid in event.participants.keys()]
        metadata = {int(uid): data for uid, data in event.participants.items()}
        metadata["_config"] = event.config
        
        try:
            # Run matcher!
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
        event_id: int = commands.Param(description="Event ID")
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
        
        # Format results
        embed = disnake.Embed(
            title=f"🎯 {event.name}",
            description=f"Event Results (ID: {event_id})",
            color=disnake.Color.blue()
        )
        
        # Show teams
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
        event_id: int = commands.Param(description="Event ID")
    ):
        """Stop event"""
        await inter.response.defer(ephemeral=True)
        
        event = self._get_event(event_id)
        if not event:
            await inter.edit_original_response(content="❌ Event not found")
            return
        
        # Mark as completed
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
        
        await inter.edit_original_response(
            content=f"✅ Event **{event.name}** stopped and archived!"
        )
    
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


# ============ WILD BRAINSTORM IDEAS - "SKY IS THE LIMIT" ============

"""
EVEN MORE MATCHER IDEAS - GO CRAZY!

These are brainstorm ideas - some practical, some wild, some hilarious!
Implement whatever sounds fun or useful!
"""

"""
# BuildStyleMatcher - Match by Minecraft building style preference
# COLLECTS: Building style (medieval, modern, steampunk, fantasy, etc.)
# LOGIC: Group people with similar or complementary styles
# USE: Themed build competitions, style-specific teams
"""

"""
# PlaytimeMatcher - Match by how much they play
# COLLECTS: Weekly playtime hours
# LOGIC: Casual players with casuals, hardcore with hardcore
# USE: Events that need time commitment matching
"""

"""
# VeteranNewbieMatcher - Always pair veterans with newbies
# COLLECTS: Account age or server join date
# LOGIC: Every team gets 50% veterans, 50% newbies
# USE: Welcoming events, mentorship
"""

"""
# CompleteOppositesMatcher - Match DIFFERENT people
# COLLECTS: Multiple traits
# LOGIC: Find people with LEAST similarity
# USE: Diversity events, stepping out of comfort zones
# Example: Builder + Redstoner, Day player + Night owl
"""

"""
# BestFriendsMatcher - Match people who interact most
# COLLECTS: Nothing (analyzes Discord message history)
# LOGIC: Finds people who chat together often, teams them up
# USE: Keep friend groups together
# NOTE: Requires message analysis (privacy concerns!)
"""

"""
# EnemiesMatcher - Match people who NEVER interact (joke)
# COLLECTS: Nothing (analyzes Discord interactions)
# LOGIC: Find people who never talk, force them together 😂
# USE: Ice breaker events, chaos mode
# NOTE: Chaotic evil alignment
"""

"""
# AgeMatcher - Match by player age (if appropriate)
# COLLECTS: Age or age range
# LOGIC: Similar ages together, or mix for diversity
# USE: Age-appropriate events, generational mixing
"""

"""
# ServerLoyaltyMatcher - Reward long-time members
# COLLECTS: Server join date
# LOGIC: Long-time members get captain roles or priority
# USE: Loyalty rewards, veteran appreciation
"""

"""
# RandomWithVetoMatcher - Random but users can veto
# COLLECTS: veto_list (max 2-3 people they don't want)
# LOGIC: Random assignment but respects limited vetoes
# USE: Mostly random with some user control
"""

"""
# EloRatingMatcher - Match by competitive rating
# COLLECTS: Elo rating (from past competitions)
# LOGIC: Balance teams by Elo, or match similar Elos for fair fights
# USE: Competitive events, ranked play
"""

"""
# CreativityMatcher - Match by creative/logical preference
# COLLECTS: "Creative" vs "Logical" player type
# LOGIC: Mix or match based on playstyle
# USE: Varied team compositions
"""

"""
# ChaosTierMatcher - Escalating randomness levels
# COLLECTS: chaos_tolerance (1-10)
# LOGIC: Higher chaos = more random, lower = more structured
# USE: Let users choose their chaos level!
"""

"""
# MoodBasedMatcher - Match by current mood/energy
# COLLECTS: Current mood (chill, competitive, social, focused)
# LOGIC: Match compatible moods
# USE: Events where vibe matters
# Example: Chill people play casual, competitive people do PvP
"""

"""
# ItemBasedMatcher - Match by in-game items/resources
# COLLECTS: What items/resources they have
# LOGIC: Complement what people have (item trading optimization)
# USE: Trading events, resource sharing
"""

"""
# QuestGroupMatcher - Create quest parties
# COLLECTS: Quest progress, goals
# LOGIC: Match people on similar quest stages
# USE: Coordinated quest completion
"""

"""
# BuildingProjectMatcher - Match for long-term projects
# COLLECTS: Project interests, time commitment
# LOGIC: Match people wanting same type of project
# USE: Team build projects, collaborations
"""

"""
# MemeMatcher - Match by meme preferences (joke but could work?)
# COLLECTS: Favorite memes, humor style
# LOGIC: Match compatible humor
# USE: Fun social events
"""

"""
# SurvivalMatcher - Minecraft survival team balancing
# COLLECTS: Survival skills (mining, farming, building, combat)
# LOGIC: Each team gets balanced survival skill set
# USE: Survival events, team survival challenges
"""

"""
# RedstoneTeamMatcher - Technical vs building split
# COLLECTS: Technical level (redstone knowledge)
# LOGIC: Mix technical with builders, or separate
# USE: Redstone competitions, mixed build events
"""

"""
# NocturnalMatcher - Night owls vs morning people
# COLLECTS: Preferred play time (morning/afternoon/evening/night)
# LOGIC: Match by when they play
# USE: Timezone events, activity timing
"""

"""
# LuckyUnluckyMatcher - Track "luck" and compensate
# COLLECTS: Nothing (tracks past team performance)
# LOGIC: If you got unlucky teams 3 times, get lucky this time
# USE: Fairness over time, karma balancing
"""

"""
# CollaborationHistoryMatcher - Match people who work well together
# COLLECTS: Nothing (analyzes past team success)
# LOGIC: Find pairs/groups that succeeded before, reunite them
# USE: Project teams, competitive events
"""

"""
# AntiRepeatMatcher - Maximize NEW teammate experiences
# COLLECTS: Nothing (tracks ALL past teams)
# LOGIC: Ensure you team with someone NEW every single time
# USE: Long-running events with variety goal
# Example: 20 events = 20 different teammates
"""

"""
# BalancedChaosMode - Random but with guardrails
# COLLECTS: Optional preferences
# LOGIC: Mostly random, but prevents disaster scenarios
# USE: Fun randomness with safety net
# Example: Random but ensures no team is ALL newbies
"""

"""
# TimeZoneBridgeMatcher - Connect different timezone clusters
# COLLECTS: Timezone + flexibility
# LOGIC: Creates some cross-timezone teams for integration
# USE: International bonding, language mixing
"""

"""
# WildCardMatcher - One random element per team
# COLLECTS: Various
# LOGIC: Structured teams + one wildcard random person
# USE: Predictable with surprise element
"""

"""
# SymmetryMatcher - Create mirrored teams
# COLLECTS: Skills/roles
# LOGIC: Teams are exact mirrors (both have same composition)
# USE: Ultra-fair competitions
"""

"""
YOUR CUSTOM MATCHER IDEAS:
- ??? - Whatever you dream up!
- ??? - Sky's the limit!
- ??? - Moon to ground!
- ??? - If you can imagine it, you can code it!

Remember: Each matcher is just a class with a match() function.
That's it! Infinite possibilities! 🚀🌙
"""
