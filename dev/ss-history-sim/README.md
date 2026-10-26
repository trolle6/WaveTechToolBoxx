# Secret Santa History Simulator (local only)

A **browser preview** of your archive files. This is **not** a Discord command and **does not** change the bot.

Reads real data from `cogs/archive/2021.json` … `2025.json` and adds a **simulated year 3000** row in the UI only (everyone ever archived merged together).

## Start

From repo root:

```bash
python start_ss_history_sim.py --open-browser
```

Or:

```bash
python dev/ss-history-sim/app.py --open-browser
```

Open **http://127.0.0.1:8770/** — leave the terminal open.

## What you see

- List like your screenshot: **Secret Santa 2025 — 24 👤**, etc.
- Click a year to expand names / assignments
- **Year 3000 (sim)** — mock aggregate, not written to `cogs/archive/`

## Not this

- No `/ss` slash command changes
- No new archive files on disk for 3000
- No TrueNAS / Docker requirement
