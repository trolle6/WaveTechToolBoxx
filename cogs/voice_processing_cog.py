import asyncio
import os
import re
import tempfile
from typing import Optional

import disnake
from disnake.ext import commands

from . import utils


class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rate_limiter = utils.RateLimiter(20, 60)
        self.breaker = utils.CircuitBreaker()
        self.request_cache = utils.RequestCache()
        self.guild_states = {}
        self.openai_url = "https://api.openai.com/v1/audio/speech"

    def _get_state(self, guild_id: int):
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = {
                "queue": asyncio.Queue(maxsize=30),
                "task": None,
            }
        return self.guild_states[guild_id]

    def _cleanup_state(self, guild_id: int):
        state = self.guild_states.get(guild_id)
        if state and state["task"] and not state["task"].done():
            state["task"].cancel()
        self.guild_states.pop(guild_id, None)

    async def _ensure_voice(self, guild_id: int, channel: disnake.VoiceChannel) -> Optional[disnake.VoiceClient]:
        """Connect to voice - handles already connected gracefully"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None

        vc = guild.voice_client

        # Already in the right channel - reuse connection
        if vc and vc.is_connected() and vc.channel.id == channel.id:
            return vc

        # Disconnect from wrong channel
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

        # Try to connect
        try:
            vc = await channel.connect(timeout=10, reconnect=True)
            return vc
        except disnake.ClientException as e:
            error_str = str(e).lower()
            if "already connected" in error_str:
                existing = guild.voice_client
                if existing and existing.is_connected():
                    return existing
            self.bot.logger.error(f"Voice connection failed: {e}")
            return None
        except asyncio.TimeoutError:
            self.bot.logger.error("Voice connection timeout")
            return None

    async def _get_tts(self, text: str, voice: str) -> Optional[bytes]:
        """Get TTS with caching and circuit breaker"""
        if not self.breaker.can_attempt():
            return None

        key = f"{voice}:{text}"[:100]

        cached = self.request_cache.get(key)
        if cached:
            return cached

        headers = {"Authorization": f"Bearer {self.bot.config.TTS_BEARER_TOKEN}"}
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3",
        }

        try:
            session = await self.bot.http_mgr.get_session(self.bot.config.TTS_TIMEOUT)
            async with session.post(self.openai_url, json=payload, headers=headers) as r:
                if r.status == 200:
                    data = await r.read()
                    self.request_cache.set(key, data)
                    self.breaker.record_success()
                    return data
                elif r.status == 429:
                    self.bot.logger.warning("TTS rate limited")
                    self.breaker.record_failure()
                    return None
                else:
                    self.bot.logger.error(f"TTS error: {r.status}")
                    self.breaker.record_failure()
                    return None

        except asyncio.TimeoutError:
            self.bot.logger.error("TTS timeout")
            self.breaker.record_failure()
            return None
        except Exception as e:
            self.bot.logger.error(f"TTS error: {e}")
            self.breaker.record_failure()
            return None

    async def _play_audio(self, vc: disnake.VoiceClient, audio: bytes, text: str):
        """Play audio with cleanup"""
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(audio)
        tmp.close()

        def cleanup(err=None):
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        try:
            vc.play(disnake.FFmpegPCMAudio(tmp.name), after=cleanup)
        except Exception as e:
            self.bot.logger.error(f"Playback error: {e}")
            cleanup()

    async def _process_queue(self, guild_id: int):
        """Process guild queue with stale message detection"""
        state = self._get_state(guild_id)
        try:
            while True:
                try:
                    msg, text, audio, queued_at = await asyncio.wait_for(
                        state["queue"].get(), timeout=300
                    )

                    # Check if message is too old (stale)
                    if (asyncio.get_event_loop().time() - queued_at) > 60:
                        self.bot.logger.debug(f"Dropped stale message after 60s: {text[:20]}")
                        state["queue"].task_done()
                        continue

                    # Check if user still in voice
                    if not msg.author.voice or not msg.author.voice.channel:
                        state["queue"].task_done()
                        continue

                    vc = await self._ensure_voice(guild_id, msg.author.voice.channel)
                    if vc:
                        await self._play_audio(vc, audio, text)
                    else:
                        # Couldn't get voice connection - notify user
                        try:
                            await msg.add_reaction("❌")
                        except Exception:
                            pass

                    state["queue"].task_done()

                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    self.bot.logger.error(f"Queue error: {e}")
                    await asyncio.sleep(1)
        finally:
            self._cleanup_state(guild_id)

    @commands.Cog.listener()
    async def on_message(self, msg: disnake.Message):
        if (
            msg.author.bot
            or not msg.guild
            or msg.channel.id != self.bot.config.DISCORD_CHANNEL_ID
            or not msg.author.voice
            or not msg.author.voice.channel
        ):
            return

        if not self.rate_limiter.check(str(msg.author.id)):
            return

        text = re.sub(r"\s+", " ", msg.content.strip())
        text = re.sub(r"<:\w+:\d+>", "", text)[:500]
        if not text or text[-1] not in ".!?,;:":
            text += "."
        if not text.strip():
            return

        audio = await self._get_tts(text, "alloy")
        if not audio:
            # Notify user of TTS failure
            try:
                await msg.add_reaction("⚠️")
            except Exception:
                pass
            return

        state = self._get_state(msg.guild.id)
        try:
            # Include timestamp to detect stale messages
            state["queue"].put_nowait((msg, text, audio, asyncio.get_event_loop().time()))
            if not state["task"] or state["task"].done():
                state["task"] = asyncio.create_task(
                    self._process_queue(msg.guild.id)
                )
        except asyncio.QueueFull:
            try:
                await msg.add_reaction("📭")  # Queue full
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if (
            member.id == self.bot.user.id
            and before.channel
            and not after.channel
        ):
            self._cleanup_state(member.guild.id)
            return

        if before.channel and not after.channel:
            await asyncio.sleep(1)
            guild = self.bot.get_guild(member.guild.id)
            if guild and guild.voice_client:
                if not any(not m.bot for m in guild.voice_client.channel.members):
                    try:
                        await guild.voice_client.disconnect()
                    except Exception:
                        pass
                    self._cleanup_state(guild.id)

    def cog_unload(self):
        """Clean up on cog unload"""
        for guild_id in list(self.guild_states.keys()):
            self._cleanup_state(guild_id)
        self.request_cache.cleanup()


def setup(bot):
    bot.add_cog(VoiceProcessingCog(bot))