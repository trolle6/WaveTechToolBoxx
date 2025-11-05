# Debug Logging Guide

## Overview
The bot now supports persistent debug logging that captures **everything** and keeps history across sessions. This is perfect for troubleshooting issues that happen over time.

## Features
- 📝 **Captures all DEBUG level logs** (every function call, every event)
- 🗓️ **Time-based rotation** (creates new file at midnight each day)
- 📦 **Keeps 30 days of history** by default (configurable)
- 🔒 **Not committed to git** (already in .gitignore)
- 📍 **Enhanced formatting** with function names and line numbers

## How to Enable

Add these to your `config.env`:

```bash
# Enable persistent debug logging
ENABLE_DEBUG_LOG=true

# Optional: Change how many days to keep (default: 30)
DEBUG_LOG_DAYS=30
```

## File Structure

When enabled, you'll see:
```
/workspace/
  bot.log              # Standard logs (INFO level, size-rotated)
  debug.log            # Current debug log (ALL levels, time-rotated)
  debug.log.2025-11-04 # Yesterday's debug log
  debug.log.2025-11-03 # Day before
  ... (up to DEBUG_LOG_DAYS worth)
```

## Log Format

**Standard log (bot.log):**
```
2025-11-05 14:23:45 - bot - INFO - Bot started
```

**Debug log (debug.log):**
```
2025-11-05 14:23:45 - bot - DEBUG - setup_logging:326 - Debug logging enabled - keeping 30 days of logs
2025-11-05 14:23:46 - bot - DEBUG - load_cogs:538 - Loading cog: voice_processing_cog
2025-11-05 14:23:46 - voice_processing - DEBUG - __init__:45 - Initializing VoiceProcessing cog
```

Notice the extra details: `funcName:lineNumber` helps pinpoint exactly where each log came from.

## Use Cases

1. **Intermittent Issues**: When errors happen randomly, check debug.log from that day
2. **Performance Analysis**: Track how long operations take over time
3. **User Behavior**: See patterns in command usage
4. **AI Troubleshooting**: When I (the AI) help debug, I can read the full history
5. **Development**: Keep it on in test/dev environments

## Performance Impact

- **Minimal** - async file I/O doesn't block the bot
- Debug logs only write to disk, not Discord
- Old logs auto-delete after configured days

## Disabling

Simply remove or set to false in `config.env`:
```bash
ENABLE_DEBUG_LOG=false
```

Or just don't include it (defaults to false).

## Tips

- **In production**: Keep it disabled or set DEBUG_LOG_DAYS=7 to save disk space
- **In development**: Enable it with DEBUG_LOG_DAYS=30 for full history
- **After incidents**: Check the log from that day: `cat debug.log.2025-11-04 | grep ERROR`
- **Disk space**: Each day typically uses 1-10MB depending on bot activity

## Security Note

Debug logs may contain sensitive information (user IDs, command arguments, etc.). 
- ✅ Already excluded from git
- ⚠️ Keep `debug.log*` in .gitignore
- ⚠️ Don't share debug logs publicly without reviewing content
