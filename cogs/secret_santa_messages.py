"""Secret Santa participant-facing embeds (DMs, confirmations, reminders, fallbacks).

Color code by role so a glance tells you what a message is about:
cranberry = you as Santa, pine = you as giftee, gold = the event, frost = notices, slate = errors.
"""

from __future__ import annotations

import random
import time
from typing import Dict, Optional

import disnake

CRANBERRY = disnake.Color(0xA63D4A)
PINE = disnake.Color(0x2E6B4F)
GOLD = disnake.Color(0xC9A15A)
FROST = disnake.Color(0x8FA9BF)

BADGE_SANTA = "🎅 SANTA MODE"
BADGE_GIFTEE = "🎁 GIFTEE MODE"
BADGE_NOTICE = "❄️ NOTICE"

GIFT_LOGGED_TITLES = (
    "📦 Wrapped, packed, and logged!",
    "🎁 Gift recorded. Someone's going to smile.",
    "✨ Santa mission: complete.",
    "🦌 The reindeer have been notified.",
    "🎀 Bow tied. Gift logged.",
    "❄️ Another gift for the archive!",
    "🍪 Logged! You've earned a cookie.",
)


SLATE = disnake.Color(0x6B7280)

# Root command name -> Discord command id, filled at startup so commands render as clickable mentions.
COMMAND_IDS: Dict[str, int] = {}


def cmd(path: str) -> str:
    """`ss giftee` -> clickable </ss giftee:ID> when the id is known, else `/ss giftee`."""
    command_id = COMMAND_IDS.get(path.split()[0])
    return f"</{path}:{command_id}>" if command_id else f"`/{path}`"


COMMAND_GROUPS = {
    "wishlist": ("🎁 Your wishlist", (
        ("ss wishlist add", "add an idea"),
        ("ss wishlist view", "see your list"),
        ("ss wishlist remove", "remove by number"),
        ("ss wishlist clear", "empty it"),
    )),
    "santa": ("🎅 Your giftee", (
        ("ss giftee", "their wishlist"),
        ("ss ask_giftee", "anonymous question"),
        ("ss submit_gift", "log what you gave"),
    )),
    "files": ("📎 Shared files", (
        ("distribute upload", "share a file"),
        ("distribute list", "all shared files"),
        ("distribute browse", "pick from a menu"),
        ("distribute get", "download one"),
    )),
    "archive": ("📚 Past years", (
        ("ss history", "browse past years"),
        ("ss edit_gift", "fix an old gift note"),
    )),
}


def _command_lines(group: str) -> str:
    return "\n".join(f"{cmd(path)} · {what}" for path, what in COMMAND_GROUPS[group][1])


LOST_HINT = "-# Lost? Scroll up to your 📖 commands card."


def commands_card_embed(year: int) -> disnake.Embed:
    """Sent right after the giftee reveal: the one place to find every typed command."""
    embed = _embed(
        BADGE_NOTICE,
        "📖 Your Secret Santa commands",
        "Buttons are the quick way. If one times out or fails, type the command instead.\n"
        "-# Button says *This interaction failed*? The bot may be restarting. "
        "Wait a minute or type the command.",
        FROST,
        footer=f"Secret Santa {year} · Scroll back here anytime",
    )
    for group, (title, _) in COMMAND_GROUPS.items():
        embed.add_field(name=title, value=_command_lines(group), inline=False)
    return embed


# ---- Errors (slate, private): what happened -> not your fault / what to do -> command to use instead.

def _error(year: int, title: str, description: str, try_label: str, command: Optional[str],
           not_done: str, typed: Optional[str] = None) -> disnake.Embed:
    embed = disnake.Embed(title=title, description=description, color=SLATE)
    embed.add_field(
        name=f"➡️ {try_label}",
        value=cmd(command) if command else "Press the button again in a minute.",
        inline=False,
    )
    if typed:
        embed.add_field(name="📝 What you typed (copy it from here)", value=f"```{typed[:1000].replace('```', '´´´')}```", inline=False)
    embed.set_footer(text=f"Secret Santa {year} · {not_done}")
    return embed


def error_bug(year: int, button_name: str, command: Optional[str], not_done: str,
              typed: Optional[str] = None) -> disnake.Embed:
    return _error(
        year, "🔧 That button tripped over its own feet",
        f"The **{button_name}** button hit a snag on our end, not yours.\n"
        "The good news? There's another way in. 🎅",
        "Try this instead", command, not_done, typed,
    )


def error_discord(year: int, command: Optional[str], not_done: str, typed: Optional[str] = None) -> disnake.Embed:
    return _error(
        year, "❄️ Discord caught a little cold",
        "Discord isn't responding properly right now.\n"
        "Give it a minute, then try again. Nothing was lost. ✨",
        "Try this in a minute", command, not_done, typed,
    )


def error_rate_limited(year: int, command: Optional[str], not_done: str,
                       retry_after: Optional[float] = None, typed: Optional[str] = None) -> disnake.Embed:
    when = (f"You can try again <t:{int(time.time() + retry_after) + 1}:R>. 🎁" if retry_after
            else "Wait a few seconds, then try again. 🎁")
    return _error(
        year, "🦌 The reindeer need a breather",
        "Lots of Santas are busy right now, so Discord asked the bot to slow down for a moment.\n\n" + when,
        "Try this shortly", command, not_done, typed,
    )


def error_dms_closed(year: int, to_santa: bool = False) -> disnake.Embed:
    if to_santa:
        embed = disnake.Embed(
            title="📭 Your reply couldn't reach your Santa",
            description="Your Santa has DMs turned off, so the bot can't deliver your reply right now.",
            color=SLATE,
        )
        embed.add_field(name="💡 What you can do",
                        value="• Try again later, in case they open their DMs\n"
                              "• Add the answer to your wishlist with " + cmd("ss wishlist add"),
                        inline=False)
        embed.set_footer(text=f"Secret Santa {year} · Your reply wasn't sent")
        return embed
    embed = disnake.Embed(
        title="📭 Your question couldn't reach them",
        description="Your giftee has DMs turned off, so the bot can't deliver questions to them right now.\n\n"
                    "Don't worry, they won't know you tried. 🤫",
        color=SLATE,
    )
    embed.add_field(name="💡 What you can do",
                    value=f"• Check their wishlist with {cmd('ss giftee')}\n"
                          "• Try again later, in case they open their DMs\n"
                          "• Ask your host to give them a gentle nudge",
                    inline=False)
    embed.set_footer(text=f"Secret Santa {year} · You're still anonymous")
    return embed


def error_question_stuck(year: int, typed: Optional[str] = None) -> disnake.Embed:
    return _error(
        year, "✉️ Your question got stuck in the wrapping paper",
        "We couldn't get your question ready to send.\n"
        "It wasn't delivered, so feel free to try again. 🎅",
        "Try this again shortly", "ss ask_giftee", "Nothing was sent to your giftee", typed,
    )


def error_timed_out_dm(year: int, button_name: str, command: Optional[str], not_done: str) -> disnake.Embed:
    return _error(
        year, "⏳ That button press timed out",
        f"Your **{button_name}** press took too long to reach the bot, so Discord gave up on it.\n"
        "Nothing is broken. Try again, or type the command. 🎅",
        "Try this instead", command, not_done,
    )


def _badge_event(year: int) -> str:
    return f"🎄 SECRET SANTA {year}"


def _embed(badge: str, title: str, description: str, color: disnake.Color, footer: Optional[str] = None) -> disnake.Embed:
    embed = disnake.Embed(title=title, description=description, color=color)
    embed.set_author(name=badge)
    if footer:
        embed.set_footer(text=footer)
    return embed


def _quote(text: str, limit: int = 1000) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines()) or "> …"


def _deadline_text(stop_ts: Optional[float]) -> str:
    if stop_ts:
        return f"<t:{int(stop_ts)}:D>"
    return "**Dec 25**"


def join_embed(year: int, shuffle_ts: Optional[float]) -> disnake.Embed:
    when = f"<t:{int(shuffle_ts)}:R>" if shuffle_ts else "on shuffle day"
    description = (
        f"Welcome aboard. You'll find out who you're gifting {when}, right here in your DMs.\n\n"
        f"### 📝 While you wait\n"
        f"Your Santa will see your wishlist, so give them some ideas.\n"
        f"Favorite snacks, hobbies, colors, things you *don't* want: it all helps.\n\n"
        f"-# Wishlist commands: {cmd('ss wishlist add')} · {cmd('ss wishlist view')} · "
        f"{cmd('ss wishlist remove')} · {cmd('ss wishlist clear')}\n"
        f"-# Changed your mind? Remove your reaction before the shuffle."
    )
    return _embed(_badge_event(year), "You're on the list! ✨", description, GOLD)


def leave_embed(year: int, shuffle_ts: Optional[float]) -> disnake.Embed:
    before = f"**<t:{int(shuffle_ts)}:f>**" if shuffle_ts else "the shuffle"
    description = (
        "No worries, you're off the participant list.\n\n"
        f"Want back in? React to the signup message again before {before}. 🎄"
    )
    return _embed(BADGE_NOTICE, f"You've left Secret Santa {year}", description, FROST)


def giftee_reveal_embed(year: int, receiver_id: int, stop_ts: Optional[float]) -> disnake.Embed:
    description = (
        f"## You're gifting <@{receiver_id}>\n"
        f"The shuffle is done and the sleigh is loaded. ✨\n\n"
        f"### 🎅 Your mission\n"
        f"**1 ·** Check their wishlist\n"
        f"**2 ·** Ask anonymous questions if you're stuck\n"
        f"**3 ·** Give your gift before {_deadline_text(stop_ts)}\n"
        f"**4 ·** Log it with `/ss submit_gift`\n\n"
        f"### 🎁 Meanwhile, someone is gifting *you*\n"
        f"Keep your own wishlist fresh, because your Santa is looking at it.\n\n"
        f"-# 🤫 Keep your giftee a secret in the server. The magic is in the mystery.\n"
        f"-# 📖 Your commands card is right below."
    )
    return _embed(BADGE_SANTA, "🎁 Your giftee has been chosen!", description, CRANBERRY)


def giftee_fallback_embed(year: int) -> disnake.Embed:
    description = (
        f"We couldn't tell you privately who you're gifting for Secret Santa {year}, "
        f"but it's saved safely. 🔒\n\n"
        f"Use `/ss giftee` here. Only **you** will see the answer.\n\n"
        f"-# Tip: Server Settings → Privacy → allow DMs to get future updates."
    )
    return _embed(BADGE_NOTICE, "Psst, your DMs are closed", description, FROST)


def question_embed(year: int, question: str) -> disnake.Embed:
    description = (
        f"{_quote(question)}\n\n"
        f"Your Santa is trying to get this just right. 🎅\n"
        f"Hit the button below to answer. Your Santa stays anonymous.\n\n"
        f"-# Replies only go through the button. If it fails, wait a moment and press it again."
    )
    return _embed(BADGE_GIFTEE, "Your Santa has a question!", description, PINE,
                  footer=f"Secret Santa {year}")


def reply_sent_embed(reply: str) -> disnake.Embed:
    description = f"✅ **Sent to your Santa!**\n{_quote(reply)}\n-# They'll get it in their DMs."
    return _embed(BADGE_NOTICE, "Reply delivered", description, FROST)


def question_delivered_embed(year: int, original: str, anonymized: str) -> disnake.Embed:
    embed = _embed(
        BADGE_SANTA,
        "❓ Question delivered",
        "Your giftee got your question in their DMs. 🤫\n"
        "When they answer, it'll land right in your DMs.",
        CRANBERRY,
        footer=f"Secret Santa {year} · Gifting season · You're anonymous",
    )
    embed.add_field(name="✍️ You wrote", value=_quote(original), inline=False)
    if anonymized and anonymized != original:
        embed.add_field(name="👀 What they see", value=_quote(anonymized), inline=False)
    return embed


def giftee_replied_embed(year: int, giftee_id: int, reply: str, question: Optional[str]) -> disnake.Embed:
    embed = _embed(
        BADGE_SANTA,
        "💌 Your giftee answered!",
        f"<@{giftee_id}> replied to you.",
        CRANBERRY,
        footer=f"Secret Santa {year} · Gifting season",
    )
    if question:
        embed.add_field(name="❓ You asked", value=_quote(question), inline=False)
    embed.add_field(name="💬 Their answer", value=_quote(reply), inline=False)
    embed.add_field(name="\u200b", value=LOST_HINT, inline=False)
    return embed


def gift_logged_embed(year: int, receiver_name: str, description: str) -> disnake.Embed:
    embed = _embed(
        BADGE_SANTA,
        random.choice(GIFT_LOGGED_TITLES),
        "Your gift is in the books. Nice work, Santa. 🎅",
        CRANBERRY,
        footer="Thanks for making someone's season brighter ✨",
    )
    embed.add_field(name="🎁 For", value=receiver_name, inline=True)
    embed.add_field(name="🗓️ Year", value=str(year), inline=True)
    embed.add_field(name="📝 What you gave", value=description, inline=False)
    embed.add_field(name="🕒 Logged", value=f"<t:{int(time.time())}:F>", inline=False)
    return embed


def empty_wishlist_reminder_embed(year: int) -> disnake.Embed:
    return _embed(
        BADGE_GIFTEE,
        "📝 Your Santa is waiting for clues!",
        "Your wishlist is still empty.\n"
        "Even one idea makes your Santa's life much easier. 🎁\n\n"
        f"{LOST_HINT}",
        PINE,
        footer=f"Secret Santa {year} · Gifting season",
    )


def final_week_reminder_embed(year: int, receiver_id: int, stop_ts: float) -> disnake.Embed:
    return _embed(
        BADGE_SANTA,
        "⏰ One week to go!",
        f"The event wraps up <t:{int(stop_ts)}:R>.\n"
        f"Have you sent your gift to <@{receiver_id}> yet? 🎅\n\n"
        f"{LOST_HINT}",
        CRANBERRY,
        footer=f"Secret Santa {year} · Final stretch",
    )


def event_ended_embed(
    year: int,
    participants: int,
    logged: int,
    receiver_name: Optional[str],
    gift: Optional[str],
) -> disnake.Embed:
    embed = _embed(
        _badge_event(year),
        f"🎄 That's a wrap on Secret Santa {year}!",
        "Thank you for being part of it. ✨\nThis year is now saved in the archive.",
        GOLD,
        footer=f"Secret Santa {year} · Archived · See you next year ❄️",
    )
    embed.add_field(name="👥 Santas", value=str(participants), inline=True)
    embed.add_field(name="📦 Gifts logged", value=f"{logged}/{participants}", inline=True)
    if receiver_name:
        note = f"*{gift}*" if gift else "*not logged*"
        embed.add_field(name="🎁 Your gift went to", value=f"{receiver_name}: {note}"[:1024], inline=False)
    embed.add_field(name=COMMAND_GROUPS["archive"][0], value=_command_lines("archive"), inline=False)
    return embed


def event_ended_fallback_embed(year: int) -> disnake.Embed:
    description = (
        f"We couldn't DM you, so here's your thank-you! ✨\n"
        f"Browse this year anytime with `/ss history`.\n\n"
        f"-# Tip: enable DMs from server members to get updates privately."
    )
    return _embed(_badge_event(year), f"🎄 Secret Santa {year} has wrapped up", description, FROST)
