# Secret Santa Commands Reference

Permissions: **Moderator** = server Administrator OR role `DISCORD_MODERATOR_ROLE_ID` in `config.env`.  
**Participant** = joined the active event (reacted on the signup message).  
Testing: `SS_DEBUG_START=true` in `config.env` skips the “year already archived” warning on `/ss start`.

File sharing uses **`/distribute`** (not under `/ss`).

---

## Moderator — run the event

| Command | Description |
|---------|-------------|
| `/ss start` | Start event. **Required:** `message` (signup post). **Optional:** `role` (added on react), `shuffle`, `end` (auto times; uses your Discord language timezone, else UTC). |
| `/ss status` | Dashboard: participant count, names, schedules, assignments, signup link. |
| `/ss shuffle` | Pair participants and DM assignments. Cancels pending auto-shuffle. |
| `/ss stop` | End event and archive to `cogs/archive/YYYY.json`. |
| `/ss oversight` | Spoilers: view gift submissions and/or anonymous Q&A (`view`: gifts, comms, all). |

---

## Participant — play

| Command | Description |
|---------|-------------|
| `/ss ask_giftee` | Ask your giftee anonymously (DM includes **Reply to Santa** button). |
| `/ss giftee` | View your giftee's wishlist (after shuffle). |
| `/ss wishlist add` | Add wishlist item. |
| `/ss wishlist remove` | Remove item by number. |
| `/ss wishlist view` | View your wishlist. |
| `/ss wishlist clear` | Clear your wishlist. |
| `/ss submit_gift` | Log what you gave (active event or current-year archive). |

Giftees reply via the **button on the DM**, not a slash command.

---

## Anyone — history

| Command | Description |
|---------|-------------|
| `/ss history` | All archived years, or one year with `year`. |
| `/ss edit_gift` | Edit your own gift text for a past year. |

---

## Moderator — archives

| Command | Description |
|---------|-------------|
| `/ss archive delete` | Delete an archive year (careful). |
| `/ss archive restore` | Restore a year from `cogs/archive/backups/`. |
| `/ss archive backups` | List backup files. |
| `/ss user_history` | Full participation history for one user. |

---

## File distribution (`/distribute`)

| Command | Who |
|---------|-----|
| `/distribute upload` | Active SS participant |
| `/distribute list`, `browse`, `get` | Active SS participant |
| `/distribute remove` | Moderator |

---

## Typical workflow

1. `/ss start` with signup message (+ optional role, shuffle, end).
2. Members **react** to join (role applied on react if set).
3. `/ss status` — check headcount.
4. `/ss shuffle` (or wait for auto-shuffle).
5. Participants: `/ss giftee`, `/ss ask_giftee`, wishlist, `/ss submit_gift`.
6. Mods: `/ss oversight` as needed.
7. `/ss stop` — archive.
8. `/ss history` — browse results.

---

## Notes

- Commands are ephemeral unless noted; assignment DMs are sent by the bot.
- Join role: bot role must be **above** the SS role in Server Settings → Roles; bot needs **Manage Roles**.
- Shuffle uses past archives to avoid repeat pairings.
