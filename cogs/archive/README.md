# Secret Santa Archive Directory

This directory contains completed Secret Santa event data from past years.

## 📁 File Structure

```
archive/
├── 2021.json  ← Secret Santa 2021 event data
├── 2022.json  ← Secret Santa 2022 event data
├── 2023.json  ← Secret Santa 2023 event data
├── 2024.json  ← Secret Santa 2024 event data
└── README.md  ← This file
```

## 📋 Archive File Format

### Current Format (Unified)
```json
{
  "year": 2024,
  "archived_at": 1703001234.567,
  "timestamp": "2024-12-15T18:30:45.123456",
  "event": {
    "active": false,
    "participants": {
      "user_id": "display_name"
    },
    "assignments": {
      "giver_id": "receiver_id"
    },
    "gift_submissions": {
      "giver_id": {
        "gift": "Description of gift",
        "receiver_id": "receiver_id",
        "receiver_name": "receiver_name",
        "submitted_at": 1703001234.567,
        "timestamp": "2024-12-15T18:30:45"
      }
    },
    "communications": {
      "santa_id": {
        "giftee_id": "receiver_id",
        "thread": [...]
      }
    }
  }
}
```

### Legacy Format (Pre-2025)
```json
{
  "year": 2024,
  "assignments": [
    {
      "giver_id": "user_id",
      "giver_name": "username",
      "receiver_id": "user_id",
      "receiver_name": "username",
      "gift": "Gift description"
    }
  ]
}
```

## 🔒 Archive Protection

**IMPORTANT:** Archive files are protected from accidental overwrites!

If you try to archive an event for a year that already has an archive:
- ✅ Original archive is PRESERVED (never overwritten)
- ✅ New event saves to: `YYYY_backup_TIMESTAMP.json`
- ⚠️ You'll get a warning in Discord and logs

**Example:**
```
2024.json already exists
→ New archive saves as: 2024_backup_20251215_183045.json
```

## 🔧 Manual Editing

**DO NOT manually edit these files while the bot is running!**

If you need to fix data:
1. Stop the bot (`/ss stop` or shutdown)
2. Edit the JSON file
3. Restart the bot

## 📊 Commands That Use Archives

- `/ss history` - View all years overview
- `/ss history [year]` - View specific year details
- `/ss user_history @user` - View one user across all years

## 🗂️ Archive Retention

Archives are kept forever by default. To clean up old archives:
- Delete or move old JSON files manually
- Bot won't re-create them unless you run events for those years again

## 🔍 Reading Archives Programmatically

Archives are standard JSON files. You can:
- Open in any text editor
- Parse with any JSON library
- Import into spreadsheets (convert JSON to CSV)
- Analyze with Python scripts

## ❓ FAQ

**Q: Can I delete old archives?**
A: Yes! The bot only reads them for history commands. Deleting them won't affect current events.

**Q: What if I run two events in one year?**
A: The second event saves to a backup file. You'll need to manually review which one to keep.

**Q: Can I merge backup files into the main archive?**
A: Yes, but do it manually with the bot stopped. Merge the data carefully to avoid losing information.

**Q: Why are there two formats?**
A: Legacy format (2021-2024) is from older bot versions. New unified format has more features. Both work fine!

