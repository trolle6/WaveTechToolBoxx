# Privacy Policy — WaveTechToolBox

**Last updated:** June 8, 2026  
**Operator:** WaveTech community server administrators  
**Repository:** [WaveTechToolBoxx](https://github.com/trolle6/WaveTechToolBoxx)

## Summary

WaveTechToolBox is a private Discord bot for the WaveTech community. We collect and store **very little** user data. We do **not** sell data, run ads, or build user profiles.

## What we collect

### General use

We do **not** store general chat messages or monitor server activity.

### Secret Santa (opt-in only)

If you **choose to join** a Secret Santa event (by reacting to the signup message or using event commands), we store only what is needed to run that event:

- Discord **user ID**
- **Display name** (at time of signup)
- Event data you submit: wishlist items, anonymous messages, gift notes

This data is used only while the event is active and for archived event records kept by the community.

### Voice TTS (opt-in by use)

If you join a voice channel and send messages while TTS is active, your message text is processed **in real time** to generate speech. We do **not** save chat messages to disk for TTS. Text may be sent to OpenAI's API for speech generation only.

### File distribution (opt-in / moderator-initiated)

Files uploaded via `/distribute` are stored on our private server to share with participants. Metadata (filename, uploader, date) is stored locally.

## What we do not do

- We do not sell or share user data with third parties for marketing
- We do not train our own AI or machine learning models on Discord messages
- We do not collect online status, presence, or activity tracking
- We do not store general server chat history

## Third-party services

We use **OpenAI's API** for:

- Text-to-speech (voice channel feature)
- Optional image generation (`/image`) when you run that command
- Optional anonymization of Secret Santa messages

Data sent to OpenAI is used only to provide those features. See [OpenAI's policies](https://openai.com/policies) for how they handle API data.

## Where data is stored

Event and file data is stored on **private, self-hosted infrastructure** (community NAS/server). It is **not** publicly accessible on the internet.

Source code is published on GitHub; **live user and event data is not committed to the repository.**

## Retention

- **Secret Santa:** active event data while the event runs; completed years may be archived as JSON for community records
- **Logs:** rotated automatically on the server
- **TTS:** no long-term storage of message text

## Your choices

- **Secret Santa:** don't react / don't use event commands — no event data stored for you
- **TTS:** don't join voice, or leave voice — your messages won't be read aloud
- **Files:** only shared if you participate in distribution or a moderator sends files to the group

## Data deletion requests

To ask about your data or request removal from an active or archived event, contact a **WaveTech server moderator** in Discord.

## Changes

We may update this policy. The latest version is kept in this repository.
