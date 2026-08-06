# Secret Santa Commands Reference

Permissions: **Moderator** = server Administrator OR role `DISCORD_MODERATOR_ROLE_ID` in `config.env`.  
**Participant** = joined the active event (reacted on the signup message).  
Testing: `SS_DEBUG_START=true` in `config.env` skips the “year already archived” warning on `/ss start`.

File sharing uses **`/distribute`** (not under `/ss`).

---

## Moderator — run the event

| Command | Description |
|---------|-------------|
| `/ss start` | Start event. **Required:** `message` (signup post). **Optional:** `role` (added on react), `shuffle`, `end` (auto-stop; **defaults to Dec 25 23:59** in your Discord language timezone if omitted). |
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

## Annual moderator checklist (copy each year)

Use this so you don’t have to remember the flow from scratch.

### Before you start

- [ ] **Last year is closed** — if an event is still active, run `/ss stop` first (archives to `cogs/archive/YYYY.json`).
- [ ] **Bot is healthy** — online, slash commands sync, log channel works.
- [ ] **Mod access** — your account is server Admin **or** has `DISCORD_MODERATOR_ROLE_ID` from `config.env`.
- [ ] **Roles (if using a join role)** — create e.g. `Secret Santa 2026`; in Server Settings → Roles, drag the **bot’s role above** that role (required for auto role on react).
- [ ] **OpenAI key** — TTS/DALL·E/anonymized Q&A need credits; SS core commands do not.

### 1 — Announce & open signup

- [ ] Post your **signup announcement** in the channel (rules, dates, “react to join”, etc.).
- [ ] Run **`/ss start`**:
  - **`message`** — the signup post (pick the message).
  - **`role`** *(optional but recommended)* — role added when someone reacts; removed if they un-react.
  - **`shuffle`** *(optional)* — e.g. `2025-12-24 18:00` (your Discord language timezone, else UTC). Bot pairs everyone automatically at that time.
  - **`end`** *(optional)* — custom auto-stop time. **If omitted, defaults to 25 Dec 23:59** (your Discord language timezone, else UTC) as a safety net if you forget `/ss stop` after showcase.
- [ ] Bot DMs everyone already on the message and logs the start.

**Signup stays open until shuffle** — there is no separate “close signup after a week” command. A week (or any length) is just “don’t run shuffle yet.” People can react/un-react until you shuffle.

### 2 — Signup period (your “~a week”, or whatever you choose)

- [ ] Remind people to **react on the signup message** (any emoji on that message counts).
- [ ] **`/ss status`** — participant count, signup link, scheduled shuffle/stop times.
- [ ] Optional: tell people they can **`/ss wishlist add`** before shuffle (wishlists work after join; **`/ss giftee`** only after shuffle).

### 3 — Pair everyone (closes signup)

- [ ] Run **`/ss shuffle`** when ready — **or** wait for scheduled auto-shuffle.
- [ ] Shuffle **closes joining** (no new reacts). Bot DMs each person their giftee.
- [ ] If DMs fail, check `/ss status` / logs (users need DMs open from server members).

### 4 — Event running (until showcase / end of Christmas)

- [ ] Participants: **`/ss giftee`**, **`/ss ask_giftee`**, wishlist commands, **`/ss submit_gift`** when they’ve given their gift.
- [ ] Giftees answer via **Reply to Santa** on the DM (not a slash command).
- [ ] Mods: **`/ss oversight`** (gifts / anonymous Q&A spoilers), **`/distribute upload`** if sharing files with participants.

### 5 — End & archive

- [ ] Run **`/ss stop`** after showcase / when the year is done — **or** wait for scheduled **`end`** from `/ss start`.
- [ ] Confirm with **`/ss history`** (or `year`).

### Quick reference — what you had in your head

| Your step | Bot equivalent |
|-----------|----------------|
| Make a role + start from announcement | `/ss start` + `message` + `role` |
| Let people react ~a week | Signup open until **`/ss shuffle`** (no fixed timer unless you set `shuffle`) |
| Shuffle, run until Christmas done | **`/ss shuffle`** → event mode → **`/ss stop`** (or `end` schedule) |

You didn’t miss the big picture — the main gaps are **pre-flight** (last year stopped, bot role order), **optional schedules on start**, and **explicit stop/archive** at the end.

---

## Notes

- Commands are ephemeral unless noted; assignment DMs are sent by the bot.
- Join role: bot role must be **above** the SS role in Server Settings → Roles; bot needs **Manage Roles**.
- Shuffle uses past archives to avoid repeat pairings.
