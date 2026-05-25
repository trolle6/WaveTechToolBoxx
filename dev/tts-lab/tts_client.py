"""Standalone OpenAI TTS client (mirrors bot voice cog API settings)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import aiohttp

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cogs import utils

logger = logging.getLogger("tts-lab")

TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_VOICE = "alloy"
AVAILABLE_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
    "onyx", "sage", "shimmer", "verse", "marin", "cedar",
]
TTS_SPEED = 1.0
MIN_VALID_AUDIO_SIZE = 100
TTS_API_TIMEOUT_BASE = 60
TTS_API_TIMEOUT_PER_100_CHARS = 0.15
TTS_API_TIMEOUT_MAX = 180
TTS_API_RETRY_MAX_ATTEMPTS = 3
TTS_API_RETRY_BASE_DELAY = 1.0
TTS_API_RETRY_MAX_DELAY = 30.0

_cache: dict[str, tuple[bytes, float]] = {}
_cache_lock = asyncio.Lock()
_CACHE_TTL = 3600
_CACHE_MAX = 100


def _cache_key(text: str, voice: str) -> str:
    key_str = f"mp3:{voice}:{text}"
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()


def normalize_text_for_api(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    text = text.replace("\x00", " ").replace("\r", "")
    try:
        text.encode("utf-8").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    return text.strip()


async def _cache_get(key: str) -> Optional[bytes]:
    async with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        data, ts = entry
        if time.time() - ts > _CACHE_TTL:
            del _cache[key]
            return None
        return data


async def _cache_set(key: str, data: bytes) -> None:
    async with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            oldest = min(_cache.items(), key=lambda x: x[1][1])[0]
            del _cache[oldest]
        _cache[key] = (data, time.time())


async def generate_tts(
    text: str,
    voice: Optional[str] = None,
    *,
    api_key: Optional[str] = None,
) -> Tuple[Optional[bytes], dict]:
    """
    Generate MP3 bytes. Returns (audio_or_none, meta dict with timings and cache_hit).
    """
    meta: dict = {"cache_hit": False, "voice": voice or DEFAULT_VOICE, "chars": 0}
    text = normalize_text_for_api(text)
    meta["chars"] = len(text)
    if not text:
        meta["error"] = "empty text"
        return None, meta

    voice = voice or DEFAULT_VOICE
    if voice not in AVAILABLE_VOICES:
        logger.warning("Invalid voice %s, using %s", voice, DEFAULT_VOICE)
        voice = DEFAULT_VOICE
        meta["voice"] = voice

    key = api_key or os.getenv("OPENAI_API_KEY", "")
    headers = utils.get_openai_headers(key)
    if not headers:
        meta["error"] = "OPENAI_API_KEY not set (config.env)"
        return None, meta

    cache_key = _cache_key(text, voice)
    cached = await _cache_get(cache_key)
    if cached:
        meta["cache_hit"] = True
        meta["bytes"] = len(cached)
        return cached, meta

    payload = {
        "model": "tts-1-hd",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "speed": TTS_SPEED,
    }
    text_timeout = (len(text) / 100 * TTS_API_TIMEOUT_PER_100_CHARS) + TTS_API_TIMEOUT_BASE
    tts_timeout = max(TTS_API_TIMEOUT_BASE, min(TTS_API_TIMEOUT_MAX, text_timeout))
    logger.info("TTS API request: chars=%s voice=%s", len(text), voice)

    start = time.perf_counter()
    timeout = aiohttp.ClientTimeout(total=tts_timeout)
    last_error: Optional[str] = None

    async with aiohttp.ClientSession() as session:
        for attempt in range(TTS_API_RETRY_MAX_ATTEMPTS):
            try:
                async with session.post(
                    TTS_URL, json=payload, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        if not audio or len(audio) < MIN_VALID_AUDIO_SIZE:
                            meta["error"] = "empty or too-small response"
                            return None, meta
                        await _cache_set(cache_key, audio)
                        meta["api_ms"] = round((time.perf_counter() - start) * 1000)
                        meta["bytes"] = len(audio)
                        return audio, meta

                    body = (await resp.text())[:300]
                    if resp.status in (429, 500, 502, 503) and attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                        delay = TTS_API_RETRY_BASE_DELAY * (2 ** attempt)
                        if resp.status == 429 and "Retry-After" in resp.headers:
                            try:
                                delay = min(float(resp.headers["Retry-After"]), TTS_API_RETRY_MAX_DELAY)
                            except (ValueError, TypeError):
                                pass
                        logger.warning("TTS %s, retry in %.1fs: %s", resp.status, delay, body)
                        await asyncio.sleep(delay)
                        continue
                    meta["error"] = f"API {resp.status}: {body}"
                    return None, meta
            except asyncio.TimeoutError:
                last_error = "timeout"
                if attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(TTS_API_RETRY_BASE_DELAY * (2 ** attempt))
                    continue
            except aiohttp.ClientError as e:
                last_error = str(e)
                if attempt < TTS_API_RETRY_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(TTS_API_RETRY_BASE_DELAY * (2 ** attempt))
                    continue

    meta["error"] = last_error or "request failed"
    meta["api_ms"] = round((time.perf_counter() - start) * 1000)
    return None, meta
