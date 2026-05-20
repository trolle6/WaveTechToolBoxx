"""
Quality Control — centralized, narrow tuning for TTS, DALL-E, and Secret Santa.

Every knob is defined in QUALITY_SETTINGS with type, bounds, and category.
Values load from config.env (via bot.config) and can be overridden at runtime
with /quality set (admin, in-memory until restart).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

import disnake
from disnake.ext import commands

from .secret_santa_checks import manage_guild_check

SettingType = Literal["bool", "int", "float", "str", "enum"]


@dataclass(frozen=True)
class QualitySetting:
    """Single quality knob with validation metadata."""

    key: str
    default: Union[bool, int, float, str]
    setting_type: SettingType
    category: str
    description: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[tuple[str, ...]] = None
    env_key: Optional[str] = None  # defaults to key


def _s(
    key: str,
    default: Union[bool, int, float, str],
    setting_type: SettingType,
    category: str,
    description: str,
    *,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    choices: Optional[tuple[str, ...]] = None,
) -> QualitySetting:
    return QualitySetting(
        key=key,
        default=default,
        setting_type=setting_type,
        category=category,
        description=description,
        min_value=min_value,
        max_value=max_value,
        choices=choices,
    )


# ---------------------------------------------------------------------------
# Every quality knob — narrow, documented, validated
# ---------------------------------------------------------------------------
QUALITY_SETTINGS: dict[str, QualitySetting] = {}

def _register(*settings: QualitySetting) -> None:
    for s in settings:
        QUALITY_SETTINGS[s.key] = s


_register(
    # ---- TTS: audio output ----
    _s("TTS_MODEL", "tts-1-hd", "enum", "TTS Audio",
       "OpenAI TTS model (tts-1-hd = higher fidelity, tts-1 = faster/cheaper)",
       choices=("tts-1-hd", "tts-1")),
    _s("TTS_SPEED", 1.0, "float", "TTS Audio",
       "Speech speed multiplier (0.25 = slow, 4.0 = fast)",
       min_value=0.25, max_value=4.0),
    _s("TTS_AUDIO_VOLUME", 0.7, "float", "TTS Audio",
       "Playback volume after decode (0.0–2.0; >1.0 may clip)",
       min_value=0.0, max_value=2.0),
    _s("TTS_DEFAULT_VOICE", "alloy", "str", "TTS Audio",
       "Fallback voice when assignment fails"),
    _s("TTS_MP3_BYTES_PER_SECOND", 16000, "int", "TTS Audio",
       "Estimated MP3 bitrate for playback timeout calculation",
       min_value=8000, max_value=32000),

    # ---- TTS: text pipeline ----
    _s("TTS_CHUNK_SIZE", 4000, "int", "TTS Text",
       "Max characters per TTS chunk before sentence-boundary split",
       min_value=500, max_value=4096),
    _s("TTS_PRONUNCIATION_ENABLED", True, "bool", "TTS Text",
       "Use GPT to expand acronyms and speakable usernames"),
    _s("TTS_PRONUNCIATION_MAX_CHARS", 3500, "int", "TTS Text",
       "Skip pronunciation AI above this length (text will be chunked anyway)",
       min_value=500, max_value=4000),
    _s("TTS_PRONUNCIATION_MODEL", "gpt-3.5-turbo", "str", "TTS Text",
       "Chat model for pronunciation rewrites"),
    _s("TTS_PRONUNCIATION_TEMPERATURE", 0.1, "float", "TTS Text",
       "Pronunciation rewrite randomness (lower = more deterministic)",
       min_value=0.0, max_value=1.0),
    _s("TTS_PRONUNCIATION_TIMEOUT", 10, "int", "TTS Text",
       "Seconds before pronunciation API call times out",
       min_value=5, max_value=30),
    _s("TTS_PRONUNCIATION_CACHE_SIZE", 200, "int", "TTS Text",
       "Max cached pronunciation rewrites",
       min_value=50, max_value=500),
    _s("TTS_SENTENCE_BOUNDARY_MIN_PERCENT", 0.8, "float", "TTS Text",
       "Min fraction of text kept when truncating at sentence boundary",
       min_value=0.5, max_value=1.0),
    _s("TTS_GRAMMAR_CORRECTIONS_ENABLED", True, "bool", "TTS Text",
       "Apply contraction/grammar fixes before TTS"),

    # ---- TTS: API reliability ----
    _s("TTS_API_TIMEOUT_BASE", 60, "int", "TTS API",
       "Base TTS API timeout in seconds",
       min_value=30, max_value=120),
    _s("TTS_API_TIMEOUT_PER_100_CHARS", 0.15, "float", "TTS API",
       "Extra seconds per 100 characters of input text",
       min_value=0.05, max_value=0.5),
    _s("TTS_API_TIMEOUT_MAX", 180, "int", "TTS API",
       "Hard cap on TTS API timeout",
       min_value=60, max_value=300),
    _s("TTS_API_RETRY_MAX_ATTEMPTS", 3, "int", "TTS API",
       "Total TTS API attempts (initial + retries)",
       min_value=1, max_value=5),
    _s("TTS_API_RETRY_BASE_DELAY", 1.0, "float", "TTS API",
       "Initial retry backoff delay in seconds",
       min_value=0.5, max_value=5.0),
    _s("TTS_API_RETRY_MAX_DELAY", 30.0, "float", "TTS API",
       "Max retry delay (also caps Retry-After header)",
       min_value=10.0, max_value=60.0),
    _s("TTS_MIN_VALID_AUDIO_SIZE", 100, "int", "TTS API",
       "Reject API responses smaller than this (bytes)",
       min_value=50, max_value=1000),
    _s("TTS_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5, "int", "TTS API",
       "Consecutive failures before circuit opens",
       min_value=1, max_value=20),
    _s("TTS_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", 60, "int", "TTS API",
       "Seconds before half-open recovery attempt",
       min_value=30, max_value=300),
    _s("TTS_CIRCUIT_BREAKER_SUCCESS_THRESHOLD", 2, "int", "TTS API",
       "Successes in half-open needed to close circuit",
       min_value=1, max_value=5),

    # ---- TTS: playback & voice ----
    _s("TTS_AUDIO_PLAYBACK_TIMEOUT_BASE", 120, "int", "TTS Playback",
       "Base playback wait timeout in seconds",
       min_value=60, max_value=300),
    _s("TTS_AUDIO_PLAYBACK_TIMEOUT_MULTIPLIER", 2.0, "float", "TTS Playback",
       "Multiplier applied to estimated audio duration",
       min_value=1.0, max_value=5.0),
    _s("TTS_AUDIO_PLAYBACK_TIMEOUT_BUFFER", 30, "int", "TTS Playback",
       "Extra seconds added to playback timeout",
       min_value=10, max_value=120),
    _s("TTS_AUDIO_PLAYBACK_TIMEOUT_MAX", 600, "int", "TTS Playback",
       "Hard cap on playback timeout",
       min_value=300, max_value=900),
    _s("TTS_AUDIO_PLAYBACK_START_DELAY", 0.3, "float", "TTS Playback",
       "Delay after creating audio source before playback starts",
       min_value=0.1, max_value=2.0),
    _s("TTS_DAVE_ENCRYPT_READY_TIMEOUT", 20.0, "float", "TTS Playback",
       "Max seconds to wait for Discord DAVE E2EE key ratchet",
       min_value=5.0, max_value=60.0),
    _s("TTS_DAVE_ENCRYPT_READY_POLL", 0.05, "float", "TTS Playback",
       "Poll interval while waiting for DAVE readiness",
       min_value=0.01, max_value=0.2),
    _s("TTS_VOICE_DISCONNECT_DELAY", 3.0, "float", "TTS Playback",
       "Seconds before re-checking voice channel after disconnect",
       min_value=1.0, max_value=10.0),
    _s("VOICE_TIMEOUT", 10, "int", "TTS Playback",
       "Voice connection attempt timeout in seconds",
       min_value=5, max_value=30),
    _s("AUTO_DISCONNECT_TIMEOUT", 300, "int", "TTS Playback",
       "Auto-disconnect from voice after idle seconds",
       min_value=60, max_value=3600),

    # ---- TTS: cache & queue ----
    _s("MAX_TTS_CACHE", 50, "int", "TTS Cache",
       "Max cached TTS audio blobs",
       min_value=10, max_value=500),
    _s("TTS_CACHE_TTL_AUDIO", 3600, "int", "TTS Cache",
       "Audio cache TTL in seconds",
       min_value=300, max_value=86400),
    _s("TTS_CACHE_TTL_PRONUNCIATION", 7200, "int", "TTS Cache",
       "Pronunciation cache TTL in seconds",
       min_value=300, max_value=86400),
    _s("MESSAGE_EXPIRY_TIME", 60, "int", "TTS Cache",
       "Drop queued TTS items older than this (seconds)",
       min_value=30, max_value=300),
    _s("GUILD_IDLE_TIMEOUT", 600, "int", "TTS Cache",
       "Guild considered idle after this many seconds",
       min_value=300, max_value=3600),

    # ---- DALL-E ----
    _s("DALLE_DEFAULT_QUALITY", "hd", "enum", "DALL-E",
       "Default /image quality when not specified",
       choices=("standard", "hd")),
    _s("DALLE_DEFAULT_SIZE", "1024x1024", "enum", "DALL-E",
       "Default /image size when not specified",
       choices=("1024x1024", "1792x1024", "1024x1792")),
    _s("DALLE_DEFAULT_STYLE", "vivid", "enum", "DALL-E",
       "Default DALL-E style (vivid = hyper-real, natural = softer)",
       choices=("vivid", "natural")),
    _s("DALLE_MODEL", "dall-e-3", "str", "DALL-E",
       "OpenAI image generation model"),
    _s("DALLE_CACHE_TTL", 3600, "int", "DALL-E",
       "Image URL cache TTL in seconds",
       min_value=300, max_value=86400),
    _s("DALLE_MAX_RETRIES", 3, "int", "DALL-E",
       "Max DALL-E API retry attempts",
       min_value=1, max_value=5),
    _s("DALLE_REQUEST_TIMEOUT", 45, "int", "DALL-E",
       "DALL-E API request timeout in seconds",
       min_value=30, max_value=120),
    _s("DALLE_JOB_EXPIRY_SECONDS", 300, "int", "DALL-E",
       "Drop queued jobs older than this",
       min_value=60, max_value=600),

    # ---- Secret Santa anonymization ----
    _s("SS_ANONYMIZE_MODEL", "gpt-3.5-turbo", "str", "Secret Santa",
       "Chat model for anonymizing SS messages"),
    _s("SS_ANONYMIZE_TEMPERATURE", 0.2, "float", "Secret Santa",
       "Anonymization rewrite randomness (lower = stricter)",
       min_value=0.0, max_value=1.0),
    _s("SS_ANONYMIZE_MAX_TOKENS", 150, "int", "Secret Santa",
       "Max tokens for anonymized rewrite",
       min_value=50, max_value=500),
    _s("SS_ANONYMIZE_AGGRESSIVENESS", "medium", "enum", "Secret Santa",
       "How aggressively to strip identifying details",
       choices=("low", "medium", "high")),
    _s("SS_ANONYMIZE_FAIL_CLOSED", False, "bool", "Secret Santa",
       "If true, block message on anonymization failure instead of passing original"),
)


def quality_config_defaults() -> dict[str, Any]:
    """Defaults dict for main.CONFIG_DEFAULTS merge."""
    return {k: spec.default for k, spec in QUALITY_SETTINGS.items()}


def parse_quality_value(key: str, raw: Any) -> Any:
    """Parse and validate a single quality setting value."""
    spec = QUALITY_SETTINGS.get(key)
    if spec is None:
        return raw

    if raw is None:
        return spec.default

    if spec.setting_type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("true", "1", "yes", "on")

    if spec.setting_type == "int":
        try:
            val = int(raw)
        except (TypeError, ValueError):
            warnings.warn(f"Invalid int for {key!r}, using default {spec.default!r}", UserWarning)
            return spec.default
        if spec.min_value is not None and val < spec.min_value:
            val = int(spec.min_value)
        if spec.max_value is not None and val > spec.max_value:
            val = int(spec.max_value)
        return val

    if spec.setting_type == "float":
        try:
            val = float(raw)
        except (TypeError, ValueError):
            warnings.warn(f"Invalid float for {key!r}, using default {spec.default!r}", UserWarning)
            return spec.default
        if spec.min_value is not None and val < spec.min_value:
            val = spec.min_value
        if spec.max_value is not None and val > spec.max_value:
            val = spec.max_value
        return val

    if spec.setting_type == "enum":
        val = str(raw).strip().lower()
        if spec.choices and val not in spec.choices:
            warnings.warn(
                f"Invalid choice for {key!r}: {raw!r}, using default {spec.default!r}",
                UserWarning,
            )
            return spec.default
        return val

    return str(raw).strip()


class QualityConfig:
    """Effective quality settings: env config + optional runtime overrides."""

    def __init__(self, bot_config: Any):
        self._base = bot_config
        self._overrides: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        key = key.upper()
        if key in self._overrides:
            return self._overrides[key]
        if hasattr(self._base, key):
            val = getattr(self._base, key)
            if val is not None:
                return parse_quality_value(key, val)
        spec = QUALITY_SETTINGS.get(key)
        return spec.default if spec else None

    def set_override(self, key: str, raw: Any) -> Any:
        key = key.upper()
        if key not in QUALITY_SETTINGS:
            raise ValueError(f"Unknown quality setting: {key}")
        val = parse_quality_value(key, raw)
        self._overrides[key] = val
        return val

    def clear_override(self, key: str) -> bool:
        key = key.upper()
        if key in self._overrides:
            del self._overrides[key]
            return True
        return False

    def clear_all_overrides(self) -> int:
        count = len(self._overrides)
        self._overrides.clear()
        return count

    def is_overridden(self, key: str) -> bool:
        return key.upper() in self._overrides

    def all_effective(self) -> dict[str, dict[str, Any]]:
        """Grouped settings for display."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for key, spec in QUALITY_SETTINGS.items():
            grouped.setdefault(spec.category, []).append({
                "key": key,
                "value": self.get(key),
                "default": spec.default,
                "overridden": self.is_overridden(key),
                "description": spec.description,
                "type": spec.setting_type,
                "min": spec.min_value,
                "max": spec.max_value,
                "choices": spec.choices,
            })
        return {cat: items for cat, items in sorted(grouped.items())}

    def anonymize_prompt_suffix(self) -> str:
        level = self.get("SS_ANONYMIZE_AGGRESSIVENESS")
        if level == "low":
            return (
                "Remove obvious names and @mentions only. "
                "Preserve personal tone and casual phrasing."
            )
        if level == "high":
            return (
                "Aggressively remove ALL identifying details: names, nicknames, "
                "locations, hobbies, writing quirks, and anything that could fingerprint the writer. "
                "Use fully neutral, generic wording."
            )
        return (
            "Remove ALL names, nicknames, @mentions, Discord tags, and anything that identifies the writer. "
            "Keep the same meaning and tone but use neutral wording."
        )


# ---------------------------------------------------------------------------
# Admin slash commands: /quality show | set | reset
# ---------------------------------------------------------------------------
class QualityControlCog(commands.Cog):
    """Inspect and narrowly adjust output quality settings."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger.getChild("quality")
        self.qc = bot.qc

    def cog_unload(self):
        pass

    @commands.slash_command(name="quality", description="View or adjust quality control settings")
    async def quality_cmd(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @quality_cmd.sub_command(name="show", description="Show all quality settings by category")
    async def quality_show(
        self,
        inter: disnake.ApplicationCommandInteraction,
        category: str = commands.Param(
            default=None,
            choices=sorted({s.category for s in QUALITY_SETTINGS.values()}),
            description="Filter to one category (optional)",
        ),
    ):
        await inter.response.defer(ephemeral=True)
        grouped = self.qc.all_effective()
        if category:
            grouped = {category: grouped.get(category, [])}

        if not any(grouped.values()):
            await inter.edit_original_response(content=f"❌ Unknown category: {category}")
            return

        embeds: list[disnake.Embed] = []
        for cat, items in grouped.items():
            lines = []
            for item in items:
                flag = " 🔧" if item["overridden"] else ""
                val = item["value"]
                if isinstance(val, float):
                    val_str = f"{val:g}"
                else:
                    val_str = str(val)
                lines.append(f"`{item['key']}` = **{val_str}**{flag}")
            # Discord embed field limit: split long categories
            chunk_size = 15
            for i in range(0, len(lines), chunk_size):
                chunk = lines[i:i + chunk_size]
                title = cat if i == 0 else f"{cat} (cont.)"
                embed = disnake.Embed(
                    title=f"🎛️ Quality Control — {title}",
                    description="\n".join(chunk),
                    color=disnake.Color.blurple(),
                )
                if i == 0:
                    embed.set_footer(text="🔧 = runtime override (lost on restart)")
                embeds.append(embed)

        await inter.edit_original_response(embeds=embeds[:10])

    @quality_cmd.sub_command(name="set", description="Runtime override for one quality setting")
    @manage_guild_check()
    async def quality_set(
        self,
        inter: disnake.ApplicationCommandInteraction,
        setting: str = commands.Param(
            description="Setting key (e.g. TTS_SPEED, DALLE_DEFAULT_STYLE)",
            choices=sorted(QUALITY_SETTINGS.keys()),
        ),
        value: str = commands.Param(description="New value"),
    ):
        await inter.response.defer(ephemeral=True)
        spec = QUALITY_SETTINGS[setting]
        try:
            parsed = self.qc.set_override(setting, value)
        except ValueError as e:
            await inter.edit_original_response(content=f"❌ {e}")
            return

        hint = ""
        if spec.min_value is not None or spec.max_value is not None:
            hint = f" (range: {spec.min_value}–{spec.max_value})"
        if spec.choices:
            hint = f" (choices: {', '.join(spec.choices)})"

        await inter.edit_original_response(
            content=(
                f"✅ **`{setting}`** → **`{parsed}`**{hint}\n"
                f"Runtime override active until bot restart.\n"
                f"_{spec.description}_"
            )
        )
        self.logger.info(f"Quality override: {setting}={parsed} by {inter.author.id}")

    @quality_cmd.sub_command(name="reset", description="Clear runtime quality overrides")
    @manage_guild_check()
    async def quality_reset(
        self,
        inter: disnake.ApplicationCommandInteraction,
        setting: str = commands.Param(
            default=None,
            description="Single setting to reset (omit to reset all overrides)",
            choices=sorted(QUALITY_SETTINGS.keys()),
        ),
    ):
        await inter.response.defer(ephemeral=True)
        if setting:
            cleared = self.qc.clear_override(setting)
            if cleared:
                await inter.edit_original_response(
                    content=f"✅ Cleared runtime override for **`{setting}`** (now using config.env value)."
                )
            else:
                await inter.edit_original_response(
                    content=f"ℹ️ **`{setting}`** had no runtime override."
                )
        else:
            count = self.qc.clear_all_overrides()
            await inter.edit_original_response(
                content=f"✅ Cleared **{count}** runtime override(s). All settings use config.env values."
            )


def setup(bot):
    if not hasattr(bot, "qc") or bot.qc is None:
        bot.qc = QualityConfig(bot.config)
    bot.add_cog(QualityControlCog(bot))
