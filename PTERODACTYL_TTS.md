# Pterodactyl vs NAS — why TTS dies on Ptero only

## Proof from the same bot (2026-09-24)

Same Discord bot token, same permissions, same codebase:

| | **Home NAS** | **Pterodactyl (Hetzner)** |
|---|---|---|
| Message queued | yes | yes |
| OpenAI TTS audio generated | yes (16512 bytes) | (queue started) |
| `Attempting voice connection` | yes | yes |
| `Connected to … (attempt 1)` | **~1 second later** | **never** |
| DAVE ready + playback | yes | never |
| What happened next | audio played | user left VC 20s later; connect still hung |

NAS log (works):

```
Attempting voice connection … timeout: 10s
Connected to Public (attempt 1)
DAVE voice encryption ready
Playback completed successfully
```

Pterodactyl log (broken host path):

```
Attempting voice connection … timeout: 120s
(no Connected line)
Cleared voice assignment … (left VC)   ← 20s later, handshake still not done
```

## What that means

- **Not** Discord permissions (same bot).
- **Not** missing code / “bot didn’t try” (it logged the attempt).
- **Not** OpenAI / FFmpeg / DAVE (NAS completed the full path).
- **Is** the Pterodactyl host failing Discord’s **voice UDP handshake**. Slash commands keep working because they are TCP only.

## Your full Pterodactyl timeline (decoded)

```
21:54:21  Attempting voice connection … timeout: 120s
21:54:41  You left VC (connect still hung — no "Connected" line)
21:56:21  TimeoutError attempt 1/4   ← exactly +120s
21:58:22  TimeoutError attempt 2/4   ← +120s again
          Task was destroyed but it is pending!  ← half-open voice handshake leftovers
22:00:22  TimeoutError attempt 3/4   ← +120s again
```

So the bot **did** commit to joining. It waited the full 120s, timed out, retried, and left orphaned `Event.wait()` tasks (`Task was destroyed but it is pending!`). That warning is a symptom of cancelled UDP handshakes — same class of failure as blank `Voice connection error:` on Docker bridge.

**NAS:** connect succeeds in ~1s.  
**Ptero:** connect never succeeds; old code then burns ~8 minutes on 4×120s retries.

New code on this branch: fail after the **first** UDP timeout (no multi-minute death spiral), cancel/cleanup the half-open client, and log CRITICAL naming host UDP / Pterodactyl.

## What to do

1. **Run TTS on the NAS** with `network_mode: host` (see `docker-compose.truenas.example.yml`).
2. Or ask the Pterodactyl provider to allow **outbound UDP** to Discord voice / offer host networking.
3. Do **not** set `VOICE_TIMEOUT=120` hoping it tries harder — on a blackholed UDP path it only sits silent longer and multiplies orphaned tasks. Prefer ~30s.

After updating this branch, a stuck Ptero join logs every 5s:

`Still waiting for Discord voice UDP handshake…`

and stops after the first timeout instead of 4×120s.
