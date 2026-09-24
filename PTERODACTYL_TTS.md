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

## What to do

1. **Run TTS on the NAS** with `network_mode: host` (see `docker-compose.truenas.example.yml`).
2. Or ask the Pterodactyl provider to allow **outbound UDP** to Discord voice endpoints / offer host networking.
3. Do not raise `VOICE_TIMEOUT` to 120 hoping it “tries harder” — on a broken UDP path it only sits silent longer. Prefer ~30s and read the new progress warnings.

After updating this branch, a stuck Ptero join logs every 5s:

`Still waiting for Discord voice UDP handshake…`

instead of looking idle until you leave the channel.
