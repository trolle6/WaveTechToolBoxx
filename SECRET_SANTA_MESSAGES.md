# Secret Santa - All User-Facing Messages

## 1. Join Message (when user reacts to join)
**Function:** `_get_join_message(year)`
```
✅ You've joined Secret Santa {year}! 🎄

**What happens next:**
• Build your wishlist: `/ss wishlist add [item]`
• When the organizer starts assignments, I'll message you here
• You'll see your giftee's wishlist once you're their Santa

🔒 *Your wishlist is hidden from everyone except your Secret Santa!*
💡 *Start adding items now so your Santa knows what to get you!*
```

## 2. Leave Message (when user removes reaction)
**Function:** `_get_leave_message(year)`
```
👋 You've left Secret Santa {year}

Your wishlist has been removed and you won't receive an assignment.

💡 *Changed your mind? React to the announcement message again to rejoin!*
```

## 3. Assignment Message (when shuffle happens)
**Function:** `_get_assignment_message(year, receiver_id, receiver_name)`
```
**SECRET SANTA {year}**

**YOUR GIFTEE:** [Random message from list below]

━━━━━━━━━━━━━━━━━━━━━━━

**SEE WHAT THEY WANT:**
• `/ss giftee` - Check {receiver_name}'s wishlist

**OTHER COMMANDS:**
• `/ss ask_giftee` - Ask {receiver_name} questions anonymously
• `/ss reply_santa` - Reply if they message you
• `/ss submit_gift` - Log your gift when ready

━━━━━━━━━━━━━━━━━━━━━━━

**BUILD YOUR WISHLIST TOO:**
• `/ss wishlist add [item]` - So your Santa knows what to get you!

**NEED HELP?**
• Contact a moderator if you have any issues
• They'll sort it out for you!

━━━━━━━━━━━━━━━━━━━━━━━

*Optional: Use `/ss ask_giftee use_ai_rewrite:True` for extra anonymity*
*Don't reveal your identity during the event!*
```

**Random assignment messages (one is chosen):**
- 🎅 **Ho ho ho!** You're Secret Santa for {receiver}!
- 🎄 **You've been assigned** to gift {receiver}!
- ✨ **The magic of Christmas** has paired you with {receiver}!
- 🦌 **Rudolph has chosen** you to spread joy to {receiver}!
- 🎁 **Your mission** is to make {receiver}'s Christmas magical!
- ❄️ **Winter magic** has matched you with {receiver}!

## 4. Question Message (when Santa asks giftee)
**Function:** `_format_dm_question(rewritten_question)`
```
**SECRET SANTA MESSAGE**

**Anonymous question from your Secret Santa:**

*"{rewritten_question}"*

─────────────────────
**Quick Reply:**
Click the button below to reply instantly!
*If the button doesn't work, use `/ss reply_santa [your reply]`*

*Your Secret Santa is excited to learn more about you!*
```

## 5. Reply Message (when giftee replies to Santa)
**Function:** `_format_dm_reply(rewritten_reply)`
```
**SECRET SANTA REPLY**

**Anonymous reply from your giftee:**

*"{rewritten_reply}"*

─────────────────────
**Keep the conversation going:**
Use `/ss ask_giftee` to ask more questions!

*Your giftee is happy to help you find the perfect gift!*
```

## 6. Event End Message (when event stops)
**Function:** `_get_event_end_message(year)`
```
**SECRET SANTA {year} - EVENT ENDED**

Thank you for being part of Secret Santa this year! Your kindness made someone's holiday brighter.

━━━━━━━━━━━━━━━━━━━━━━━

Hope you had as much fun as your giftee!

See you next year!
```

## 7. Scheduled Shuffle Notification (to organizer)
**Location:** Line 436-438
```
✅ Your scheduled Secret Santa shuffle just happened!

All participants have been assigned and notified via DM.

*You can check the results with `/ss participants` or `/ss view_gifts`*
```

## 8. Scheduled Shuffle Error Notification (to organizer)
**Location:** Line 449-452
```
❌ **Scheduled shuffle failed!**

An error occurred while executing the scheduled shuffle:
`{error_message}`

Please run `/ss shuffle` manually to make assignments.
```

## 9. Scheduled Stop Success Notification (to organizer)
**Location:** Line 472-474
```
🛑 **Auto-stop complete!** Your scheduled Secret Santa event just ended!

Event has been archived to: `{saved_filename}`

All participants have been notified via DM.
```

## 10. Scheduled Stop Error Notification (to organizer)
**Location:** Line 484-487 and 497-500
```
❌ **Scheduled stop failed!**

An error occurred while executing the scheduled stop:
`{error_message}`

Please run `/ss stop` manually.
```

## 11. Reply Success Embed (in command response)
**Location:** Line 548-552
```
Title: ✅ Reply Sent!
Description: Your reply has been delivered to your Secret Santa!
Footer: 🎄 Your Secret Santa will be so happy to hear from you!
Field: 📝 Your Reply: *{truncated_reply}*
```

## 12. Reply Failed Embed (in command response)
**Location:** Line 556-559
```
Title: ❌ Delivery Failed
Description: Couldn't send your reply. Your Secret Santa may have DMs disabled.
```

## 13. Reply Error (generic error)
**Location:** Line 564
```
❌ An error occurred while sending your reply
```

## 14. No Assignment Error
**Location:** Line 223-225
```
Title: ❌ No Assignment
Description: You don't have an assignment yet! Wait for the event organizer to run `/ss shuffle`.
```

## 15. Not a Participant Error
**Location:** Line 187
```
❌ You're not a participant in this event
```

## 16. No Active Event Error
**Location:** Line 182
```
❌ No active Secret Santa event
```
