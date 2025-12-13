# Secret Santa Deployment Checklist

## Pre-Deployment Checks
- [ ] Python version matches (3.9+)
- [ ] All dependencies installed: `pip install -r requirements.txt`
- [ ] Environment variables set
- [ ] File permissions correct
- [ ] Archive directory exists: `mkdir -p cogs/archive/backups`

## Post-Deployment Verification
- [ ] Bot starts without errors
- [ ] Cog loads successfully
- [ ] Commands register properly
- [ ] State file loads correctly
- [ ] Archive directory accessible

## Common Issues & Fixes

### Issue: "Module not found"
**Fix:** `pip install -r requirements.txt`

### Issue: "Permission denied"
**Fix:** `chmod +x main.py` and check file ownership

### Issue: "Archive directory not found"
**Fix:** `mkdir -p cogs/archive/backups`

### Issue: "State file corrupted"
**Fix:** Delete `secret_santa_state.json` and restart

### Issue: "Commands not working"
**Fix:** Check bot permissions and role IDs

## Environment Variables Required
```
DISCORD_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_key (optional)
DISCORD_MODERATOR_ROLE_ID=your_mod_role_id
```

## File Structure Check
```
project/
├── main.py
├── cogs/
│   ├── SecretSanta_cog.py
│   └── archive/
│       └── backups/
├── secret_santa_state.json
└── requirements.txt
```
