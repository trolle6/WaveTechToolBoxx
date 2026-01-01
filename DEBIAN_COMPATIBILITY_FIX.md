# Debian/Linux Compatibility Fix

## Issue
When running the bot on TrueNAS Scale (Debian-based Linux), the following error occurred:

```
AttributeError: module 'disnake.ext.commands' has no attribute 'Interaction'
```

This error prevented `SecretSanta_cog` and `DistributeZip_cog` from loading.

## Root Cause
Python was evaluating type annotations at import time, which can cause issues with forward references or type resolution in some Python/disnake versions.

## Solution
Added `from __future__ import annotations` to the top of both affected cog files:

- `cogs/SecretSanta_cog.py`
- `cogs/DistributeZip_cog.py`

This ensures that type annotations are stored as strings and not evaluated at import time, preventing AttributeError issues during module loading.

## Files Changed
- `cogs/SecretSanta_cog.py` - Added `from __future__ import annotations` after docstring
- `cogs/DistributeZip_cog.py` - Added `from __future__ import annotations` after docstring

## Testing
The fix should work on:
- ✅ Debian-based systems (TrueNAS Scale)
- ✅ Windows (already working)
- ✅ macOS (should continue working)

## Notes
- This is a Python best practice for code using type hints
- No functionality changes - only affects how annotations are stored
- Compatible with Python 3.7+

