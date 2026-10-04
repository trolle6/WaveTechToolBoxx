# Secret Santa Commands

Everyone who joins is both a **gifter** (a Santa, buying for someone) and a **giftee** (getting a gift from someone).

---

## 1. Host commands (moderators)

Hosts are server admins, the server owner, or anyone with the `DISCORD_MODERATOR_ROLE_ID` role.

| Command | What it does |
|---|---|
| `/ss start` | Opens signup. Pick your announcement `message`; people react to it to join. Optional: `role` (given to people who join), `shuffle` (auto-pair time, e.g. `2026-12-01 18:00`), `end` (auto-stop time; defaults to Dec 25 23:59). |
| `/ss status` | Shows who joined, the scheduled times, and the pairings. |
| `/ss shuffle` | Closes signup, pairs everyone, and DMs each person their giftee. Avoids repeating last year's pairs. |
| `/ss oversight` | Spoilers: see submitted gifts and the anonymous questions and replies. |
| `/ss stop` | Ends the event and saves the year to the archive. |
| `/ss user_history` | One person's Secret Santa history across all years. |
| `/ss archive backups` / `delete` / `restore` | Manage saved years (delete moves a year to backups). |
| `/distribute remove` | Remove a shared file. |

### Yearly checklist

1. Post an announcement ("react to join!"), then run `/ss start` and pick that message.
2. Wait for people to react. Check headcount with `/ss status`.
3. Run `/ss shuffle` (or let the `shuffle` time do it).
4. Let everyone gift. Peek with `/ss oversight` if needed.
5. Run `/ss stop` after Christmas (or let the `end` time do it).

If you use a join role, the bot's own role must sit **above** it in Server Settings → Roles.

---

## 2. Giftee commands (help your Santa)

| Command | What it does |
|---|---|
| `/ss wishlist add` | Add something you'd like. |
| `/ss wishlist view` | See your wishlist. |
| `/ss wishlist remove` | Remove an item by its number. |
| `/ss wishlist clear` | Empty your wishlist. |
| **Reply to Santa** button | When your Santa asks you something, you get a DM. Press the button on it to answer. You never find out who your Santa is. |

---

## 3. Gifter commands (be a Santa)

These work after the shuffle.

| Command | What it does |
|---|---|
| `/ss giftee` | See who you're gifting to and their wishlist. |
| `/ss ask_giftee` | Send your giftee an anonymous question. |
| `/ss submit_gift` | Record what you gave (for the yearly archive). |
| `/distribute upload` / `list` / `browse` / `get` | Share files with other participants. |

---

## For everyone

| Command | What it does |
|---|---|
| `/ss history` | Look at past years (add `year` for one year). |
| `/ss edit_gift` | Fix your gift description from a past year. |
