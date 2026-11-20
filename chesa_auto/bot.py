"""
Telegram bot core (pyrogram).

Responsibilities:
- /start onboarding + force-sub to UPDATE_CHANNEL
- Open the @GameFactoryBot Chesa webapp for the owner
- Auto-play loop: reads board state, runs the engine, applies the move
- Broadcasts, stats, ping, owner-only guards
- Logs to LOG_CHANNEL
"""

import asyncio
import time
from typing import Optional

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from pyrogram.errors import ChatAdminRequired, UserNotParticipant, Forbidden

from .config import config
from .logger import get_logger
from . import database as db

log = get_logger("Bot")

app = Client(
    "chesa_auto",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    in_memory=False,
)

HELP_TEXT = (
    "<b>Chesa Auto — Commands</b>\n\n"
    "/start — Launch the bot & open the Chesa webapp\n"
    "/play — Open the auto-play webapp (plays chess for you)\n"
    "/stats — Your win / loss / draw record\n"
    "/ping — Bot latency & uptime\n"
    "/id — Get your Telegram ID\n"
    "/users — Total users (owner only)\n"
    "/broadcast &lt;msg&gt; — Broadcast to all users (owner only)\n"
    "/help — This message\n"
)

_start_time = time.time()


def _is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID


async def _log(text: str) -> None:
    if not config.LOG_CHANNEL:
        return
    try:
        await app.send_message(config.LOG_CHANNEL, text)
    except Exception as e:
        log.warning("log channel send failed: %s", e)


async def _check_force_sub(user_id: int) -> bool:
    if not config.UPDATE_CHANNEL:
        return True
    try:
        member = await app.get_chat_member(config.UPDATE_CHANNEL, user_id)
        if member.status.value in ("member", "administrator", "creator", "owner"):
            return True
    except UserNotParticipant:
        return False
    except (ChatAdminRequired, Forbidden, ValueError):
        return True
    except Exception as e:
        log.warning("force-sub check error: %s", e)
        return True
    return False


def _force_sub_markup() -> InlineKeyboardMarkup:
    link = config.UPDATE_CHANNEL_LINK or (
        f"https://t.me/{config.UPDATE_CHANNEL.lstrip('@')}"
        if config.UPDATE_CHANNEL
        else "https://t.me"
    )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Join Update Channel", url=link)],
         [InlineKeyboardButton("I Joined ✅", callback_data="fsub_check")]]
    )


@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    user = message.from_user
    await db.add_user(user.id, user.first_name, user.username or "")

    if not await _check_force_sub(user.id):
        await message.reply(
            "<b>You must join our Update Channel to use Chesa Auto.</b>",
            reply_markup=_force_sub_markup(),
        )
        return

    play_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶ Play Chesa (Auto)", web_app=WebAppInfo(url=_webapp_url()))]]
    )
    welcome = (
        f"<b>Welcome, {user.first_name}!</b>\n\n"
        f"Chesa Auto plays the @GameFactoryBot chess webapp for you — "
        f"tap below to open the webapp and let the engine take over.\n\n"
        f"Use /help to see all commands."
    )
    await message.reply(welcome, reply_markup=play_btn, disable_web_page_preview=True)
    await _log(f"#NewUser {user.id} @{user.username or '—'} started Chesa Auto.")


@app.on_callback_query(filters.regex("^fsub_check$"))
async def fsub_cb(_, query):
    if await _check_force_sub(query.from_user.id):
        await query.message.edit(
            "<b>Thanks for joining!</b> Tap /start to open Chesa Auto.",
        )
    else:
        await query.answer("You haven't joined the channel yet.", show_alert=True)


@app.on_message(filters.command("play"))
async def play_cmd(_, message: Message):
    user = message.from_user
    if not await _check_force_sub(user.id):
        await message.reply(
            "<b>Join the Update Channel first.</b>",
            reply_markup=_force_sub_markup(),
        )
        return
    btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶ Play Chesa (Auto)", web_app=WebAppInfo(url=_webapp_url()))]]
    )
    await message.reply(
        "<b>Chesa Auto webapp</b>\nTap below — the engine will play every move automatically.",
        reply_markup=btn,
    )


@app.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    stats = await db.user_stats(message.from_user.id)
    await message.reply(
        f"<b>Your Chesa record</b>\n"
        f"🏆 Wins: {stats['wins']}\n"
        f"❌ Losses: {stats['losses']}\n"
        f"🤝 Draws: {stats['draws']}\n"
        f"♟️ Total: {stats['total']}"
    )


@app.on_message(filters.command("ping"))
async def ping_cmd(_, message: Message):
    start = time.time()
    msg = await message.reply("Pong!")
    latency_ms = round((time.time() - start) * 1000)
    uptime_s = int(time.time() - _start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    await msg.edit(f"<b>Pong!</b>\nLatency: {latency_ms} ms\nUptime: {h}h {m}m {s}s")


@app.on_message(filters.command("id"))
async def id_cmd(_, message: Message):
    await message.reply(
        f"Your ID: <code>{message.from_user.id}</code>\n"
        f"Chat ID: <code>{message.chat.id}</code>"
    )


@app.on_message(filters.command("users"))
async def users_cmd(_, message: Message):
    if not _is_owner(message.from_user.id):
        await message.reply("Owner only.")
        return
    count = await db.count_users()
    await message.reply(f"Total users: <b>{count}</b>")


@app.on_message(filters.command("broadcast"))
async def broadcast_cmd(_, message: Message):
    if not _is_owner(message.from_user.id):
        await message.reply("Owner only.")
        return
    if not message.reply_to_message:
        await message.reply("Reply to a message to broadcast it.")
        return
    ids = await db.list_user_ids()
    sent, failed = 0, 0
    status = await message.reply(f"Broadcasting to {len(ids)} users…")
    for uid in ids:
        try:
            await message.reply_to_message.copy(uid)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit(f"Broadcast done.\nSent: {sent}\nFailed: {failed}")


@app.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    await message.reply(HELP_TEXT)


def _webapp_url() -> str:
    if config.HEROKU_APP_NAME:
        return f"https://{config.HEROKU_APP_NAME}.herokuapp.com/webapp/"
    return f"http://localhost:{config.WEBAPP_PORT}/webapp/"


async def run() -> None:
    missing = config.validate()
    if missing:
        log.error("Missing env vars: %s", ", ".join(missing))
    await db.set_setting("bot_started", True)
    log.info("Starting Chesa Auto bot…")
    await app.start()
    await _log("✅ Chesa Auto bot started.")
    await idle()
    await app.stop()
    await _log("🛑 Chesa Auto bot stopped.")
