# Prompt for DeepSeek: Fix Secret Santa Message Formatting

I need you to update all the Secret Santa DM messages to have consistent formatting with clear year indicators and message type labels.

## Current Problem:
- The **end message** format is perfect: `**✨ Secret Santa {year} Has Come to a Close ✨**` - it clearly shows the year
- The **question message** just says `**📮 A Message From Your Secret Santa**` - no year, no clear indication it's a QUESTION
- The **reply message** just says `**📬 A Reply From Your Giftee!**` - no year, no clear indication it's a REPLY
- The **join message** has the year but it's indirect: `🎉 You're in! Welcome to Secret Santa {year}!`
- The **assignment message** has year but could be clearer: `**🎁 Your Secret Santa Mission for {year} 🎁**`

## What I Want:
All messages should follow a consistent format similar to the end message, with:
1. **Clear year indicator** in the header (like "Secret Santa {year}")
2. **Clear message type** (QUESTION, REPLY, ASSIGNMENT, JOINED, EVENT ENDED, etc.)
3. **Consistent formatting** across all message types

## Functions to Update:

### 1. `_format_dm_question(rewritten_question: str)` 
Currently: `**📮 A Message From Your Secret Santa**`
Should be: Something like `**📮 Secret Santa {year} - QUESTION FROM YOUR SANTA 📮**` or similar, with the year clearly visible

### 2. `_format_dm_reply(rewritten_reply: str)`
Currently: `**📬 A Reply From Your Giftee!**`
Should be: Something like `**📬 Secret Santa {year} - REPLY FROM YOUR GIFTEE 📬**` or similar, with the year clearly visible

### 3. `_get_join_message(year: int)`
Currently: `🎉 You're in! Welcome to Secret Santa {year}!`
Should be: Update to have a clear header format like the end message, e.g., `**🎉 Secret Santa {year} - YOU'RE IN! 🎉**` or similar

### 4. `_get_assignment_message(year: int, receiver_id: int, receiver_name: str)`
Currently: `**🎁 Your Secret Santa Mission for {year} 🎁**`
This one is okay but could be more consistent - maybe `**🎁 Secret Santa {year} - YOUR ASSIGNMENT 🎁**` or similar

### 5. `_get_event_end_message(year: int)`
Currently: `**✨ Secret Santa {year} Has Come to a Close ✨**`
This one is perfect! Keep this format as the template for others.

## Requirements:
- All messages must include the year in a clear, prominent header
- All messages must clearly indicate what type of message it is (QUESTION, REPLY, ASSIGNMENT, etc.)
- Keep the warm, human tone we already have
- Maintain all the existing content, just fix the headers/formatting
- The year should be passed as a parameter where needed (you may need to update function signatures)

## Note:
The question and reply functions don't currently receive the year parameter. You have two options:

**Option 1:** Update function signatures to accept `year: int` and pass it from call sites
- `_format_dm_question` is called at line 1347 (has access to `self.state['current_year']`)
- `_format_dm_reply` is called at lines 543 and 1400 (both have access to `self.state['current_year']`)

**Option 2:** Access `self.state['current_year']` directly inside the functions (simpler, but less flexible)

I recommend Option 1 for consistency, but either works. The call sites already have access to the year via `self.state['current_year']`.

Please update all these message functions to have consistent, clear formatting with year indicators and message type labels, following the pattern of the end message.
