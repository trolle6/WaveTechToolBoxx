# Owner Commands Reference

## Centralized Owner System

All owner-only commands now use a **centralized reference** in `cogs/owner_utils.py`.

### Quick Reference

**File**: `cogs/owner_utils.py`
- `OWNER_USERNAME = "trolle6"` - Change this to update ALL owner restrictions
- `owner_check()` - Decorator for owner-only commands
- `is_owner(inter)` - Inline check function
- `get_owner_mention()` - Get formatted owner name for messages

## Current Owner-Only Commands

### Secret Santa
- ✅ `/ss start` - Start a Secret Santa event
- ✅ `/ss shuffle` - Make Secret Santa assignments

### DistributeZip
- ✅ `/distributezip upload` - Upload and distribute zip files

## How to Add More Owner-Only Commands

### Method 1: Using Decorator (Recommended)
```python
from .owner_utils import owner_check

@commands.slash_command(name="mycommand")
@owner_check()  # ← Only trolle6 can use this
async def my_command(self, inter):
    # Your command code here
    pass
```

### Method 2: Using Inline Check
```python
from .owner_utils import is_owner, get_owner_mention

@commands.slash_command(name="mycommand")
async def my_command(self, inter):
    if not is_owner(inter):
        await inter.response.send_message(
            f"❌ Only {get_owner_mention()} can use this command!"
        )
        return
    
    # Your command code here
    pass
```

## Changing the Owner

To change the owner username for ALL commands:

1. Open `cogs/owner_utils.py`
2. Change `OWNER_USERNAME = "trolle6"` to your new username
3. Restart the bot

That's it! All owner restrictions will automatically update.

## Benefits

✅ **Single Source of Truth** - Change owner in one place, updates everywhere
✅ **Easy to Use** - Simple decorator or function call
✅ **Consistent** - All owner checks work the same way
✅ **Maintainable** - Easy to add new owner-only commands

## Examples

### Example 1: New Owner-Only Command
```python
from disnake.ext import commands
from .owner_utils import owner_check

class MyCog(commands.Cog):
    @commands.slash_command(name="admin")
    @owner_check()  # Only trolle6
    async def admin_command(self, inter):
        await inter.response.send_message("Owner-only command!")
```

### Example 2: Conditional Owner Check
```python
from .owner_utils import is_owner, get_owner_mention

@commands.slash_command(name="special")
async def special_command(self, inter):
    # Regular users can use basic features
    if is_owner(inter):
        # Owner gets extra features
        await inter.response.send_message("Owner mode activated!")
    else:
        await inter.response.send_message("Regular mode")
```

## Verification

All owner checks:
- ✅ Case-insensitive (trolle6, Trolle6, TROLLE6 all work)
- ✅ Log unauthorized attempts
- ✅ Provide clear error messages
- ✅ Work consistently across all commands

