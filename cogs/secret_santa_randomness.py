"""Atmospheric randomness for the Secret Santa shuffle (random.org).

random.org generates its numbers from atmospheric noise. When RANDOM_ORG_API_KEY
is set, the shuffle seeds its RNG with 256 bits from random.org mixed with 256
bits from os.urandom, so the result is never weaker than local OS entropy. Any
failure (no key, network, quota) falls back to secrets.SystemRandom.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import random
import secrets
from typing import Optional

import aiohttp

RANDOM_ORG_URL = "https://api.random.org/json-rpc/4/invoke"
RANDOM_ORG_TIMEOUT = 10

SOURCE_ATMOSPHERIC = "random.org (atmospheric noise)"
SOURCE_LOCAL = "local OS entropy"


async def fetch_atmospheric_bytes(api_key: str, session: aiohttp.ClientSession) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "method": "generateBlobs",
        "params": {"apiKey": api_key, "n": 1, "size": 256, "format": "base64"},
        "id": secrets.randbelow(2**31),
    }
    async with session.post(
        RANDOM_ORG_URL,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=RANDOM_ORG_TIMEOUT),
    ) as resp:
        resp.raise_for_status()
        body = await resp.json(content_type=None)
    if "error" in body:
        err = body["error"]
        raise RuntimeError(f"random.org error {err.get('code')}: {err.get('message')}")
    data = base64.b64decode(body["result"]["random"]["data"][0])
    if len(data) != 32:
        raise RuntimeError(f"random.org returned {len(data)} bytes, expected 32")
    return data


async def get_shuffle_rng(
    api_key: Optional[str], session: Optional[aiohttp.ClientSession], logger=None
) -> tuple[random.Random, str]:
    """Return (rng, source description) for the shuffle."""
    api_key = (api_key or "").strip()
    if api_key and session is not None:
        try:
            atmospheric = await fetch_atmospheric_bytes(api_key, session)
            seed = hashlib.sha512(atmospheric + os.urandom(32)).digest()
            return random.Random(int.from_bytes(seed, "big")), SOURCE_ATMOSPHERIC
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, KeyError, ValueError) as e:
            if logger:
                logger.warning("random.org unavailable, using local entropy: %s", e)
    return secrets.SystemRandom(), SOURCE_LOCAL
