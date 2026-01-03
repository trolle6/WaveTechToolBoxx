# Secret Santa Commands Reference

## 🔧 MODERATOR/OWNER COMMANDS
*(Requires moderator role or owner permissions)*

### Event Management
- `/ss start [announcement_message_id] [role_id]`
  - Start a new Secret Santa event
  - Requires: Message ID (where reactions will be tracked) and Role ID (for participants)

- `/ss shuffle`
  - Make Secret Santa assignments (distributes assignments via DM)
  - Must have at least 2 participants

- `/ss stop`
  - Stop the active event and archive data to `archive/YYYY.json`

### Viewing (Moderator)
- `/ss participants`
  - View all current participants in the active event

- `/ss view_gifts`
  - View all submitted gifts in the active event

- `/ss view_comms`
  - View all communication threads in the active event

---

## 👥 PARTICIPANT COMMANDS
*(Requires being a participant in active event)*

### Communication
- `/ss ask_giftee [question] [use_ai_rewrite]`
  - Ask your giftee a question anonymously
  - Optional: `use_ai_rewrite` (true/false) - uses AI to rewrite for extra anonymity

- `/ss reply_santa [reply]`
  - Reply to your Secret Santa anonymously

### Gift Submission
- `/ss submit_gift [gift_description]`
  - Record your gift description

- `/ss edit_gift [year] [gift_description]`
  - Edit your own gift submission from any past year
  - Example: `/ss edit_gift year:2025 gift_description:Updated gift description`

### Wishlist Management
- `/ss wishlist add [item]`
  - Add an item to your wishlist

- `/ss wishlist remove [number]`
  - Remove an item from your wishlist (by number)

- `/ss wishlist view`
  - View your own wishlist

- `/ss wishlist clear`
  - Clear your entire wishlist

- `/ss view_giftee_wishlist`
  - View your giftee's wishlist (what they want)

---

## 🌐 PUBLIC COMMANDS
*(Available to anyone)*

### History & Archives
- `/ss history`
  - View overview of all archived years

- `/ss history [year]`
  - View detailed information for a specific year
  - Example: `/ss history year:2025`

- `/ss user_history [user]`
  - View a user's complete participation history across all years
  - Example: `/ss user_history user:@username`

### Testing/Debug
- `/ss test_emoji_consistency [user]`
  - Test emoji consistency across years for a user
  - Example: `/ss test_emoji_consistency user:@username`

---

## 📋 TESTING WORKFLOW

### Full Event Cycle:
1. **Setup**: `/ss start [message_id] [role_id]`
2. **Collect Participants**: Users react to the announcement message
3. **Check Participants**: `/ss participants` (moderator)
4. **Make Assignments**: `/ss shuffle` (moderator)
5. **Test Participant Features**:
   - `/ss ask_giftee [question]`
   - `/ss reply_santa [reply]`
   - `/ss wishlist add [item]`
   - `/ss view_giftee_wishlist`
   - `/ss submit_gift [description]`
6. **View Progress**: `/ss view_gifts`, `/ss view_comms` (moderator)
7. **End Event**: `/ss stop` (moderator)
8. **View Archive**: `/ss history`, `/ss history [year]`
9. **Edit Past Gift**: `/ss edit_gift [year] [description]`

### Quick Test Checklist:
- [ ] Start event
- [ ] Add participants (via reactions)
- [ ] View participants
- [ ] Shuffle assignments
- [ ] Test wishlist (add, view, remove, clear)
- [ ] Test giftee wishlist viewing
- [ ] Test anonymous communication (ask, reply)
- [ ] Submit gift
- [ ] View gifts/comms (moderator)
- [ ] Stop event
- [ ] View history
- [ ] Edit past gift
- [ ] View user history
- [ ] Test emoji consistency

---

## 💡 NOTES

- All commands are ephemeral (only visible to you) except DMs sent by the bot
- Assignments are sent via DM when `/ss shuffle` is run
- Events are archived to `archive/YYYY.json` when stopped
- Gift editing works for any past year where you participated
- Communication is fully anonymous (AI rewriting available for extra anonymity)
- Wishlists are only visible to you and your Secret Santa (via `/ss view_giftee_wishlist`)

