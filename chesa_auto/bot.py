"""
Telegram bot core (pyrogram / Kurigram) — control bot.

The control bot (BOT_TOKEN) is the dashboard. The owner uses it to:
  - /login  → interactive phone + code (+ 2FA) flow that authorizes the
              USER account (session is stored in MongoDB)
  - /logout, /whoami, /session, /cancellogin
  - /autoplay [rounds] [depth], /stopplay, /autostatus
  - /start, /play, /stats, /ping, /id, /users, /broadcast, /help
"""

from __future__ import annotations

import asyncio
import html
import time
from typing import Optional

from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    Forbidden,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
    UserNotParticipant,
)
from pyrogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from . import auth, autoplay
from . import database as db
from .config import config
from .logger import get_logger

log = get_logger("Bot")

app = Client(
    "chesa_auto",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    parse_mode=ParseMode.HTML,
    in_memory=True,
)

HELP_TEXT = (
    "<b>Chesa Auto — Commands</b>\n\n"
    "<b>Auth</b> (owner)\n"
    "/login — Sign in your user account (phone + code + 2FA)\n"
    "/cancellogin — Abort a running login\n"
    "/logout — Revoke and delete the saved user session\n"
    "/whoami — Show the signed-in user account\n"
    "/session — Show session status\n\n"
    "<b>Auto-play</b> (owner)\n"
    "/autoplay [rounds] [depth]\n"
    "/stopplay\n"
    "/autostatus\n\n"
    "<b>General</b>\n"
    "/start — Welcome\n"
    "/play — Open the Chesa webapp\n"
    "/stats — Your win / loss / draw record\n"
    "/ping — Bot latency &amp; uptime\n"
    "/id — Get your Telegram ID\n"
    "/users — Total users (owner)\n"
    "/broadcast — Reply to a message to broadcast it (owner)\n"
    "/help — This message\n"
)

BOT_COMMANDS = [
    BotCommand("start", "Welcome"),
    BotCommand("help", "All commands"),
    BotCommand("play", "Open the Chesa webapp"),
    BotCommand("stats", "Your record"),
    BotCommand("login", "Owner: sign in user account"),
    BotCommand("logout", "Owner: delete user session"),
    BotCommand("whoami", "Owner: signed-in account"),
    BotCommand("session", "Owner: session status"),
    BotCommand("ping", "Latency & uptime"),
    BotCommand("id", "Your Telegram ID"),
]

_start_time = time.time()
# owner_id -> future resolved with the next private text message (None = cancelled)
_login_waits: dict[int, asyncio.Future] = {}
_bg_tasks: set[asyncio.Task] = set()


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _is_owner(user_id: Optional[int]) -> bool:
    return bool(user_id) and user_id == config.OWNER_ID


def _owner_only(message: Message) -> bool:
    return message.from_user is not None and _is_owner(message.from_user.id)


async def _log(text: str) -> None:
    if not config.LOG_CHANNEL:
        return
    try:
        await app.send_message(config.LOG_CHANNEL, text)
    except Exception as e:
        log.warning("log channel send failed: %s", e)


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _webapp_url() -> Optional[str]:
    """Telegram only accepts https WebApp URLs; return None if we don't have one."""
    url = config.WEBAPP_URL
    if not url and config.HEROKU_APP_NAME:
        url = f"https://{config.HEROKU_APP_NAME}.herokuapp.com/webapp/"
    if url and url.startswith("https://"):
        return url if url.endswith("/") else url + "/"
    return None


def _play_markup() -> Optional[InlineKeyboardMarkup]:
    url = _webapp_url()
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("▶ Open Chesa Auto", web_app=WebAppInfo(url=url))]])


async def _check_force_sub(user_id: int) -> bool:
    if not config.UPDATE_CHANNEL:
        return True
    try:
        member = await app.get_chat_member(config.UPDATE_CHANNEL, user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            ChatMemberStatus.RESTRICTED,
        )
    except UserNotParticipant:
        return False
    except (ChatAdminRequired, Forbidden, PeerIdInvalid, ValueError, KeyError):
        # Bot isn't admin in the channel / bad channel config: don't lock users out.
        return True
    except Exception as e:
        log.warning("force-sub check error: %s", e)
        return True


def _force_sub_markup() -> InlineKeyboardMarkup:
    link = config.UPDATE_CHANNEL_LINK or (
        f"https://t.me/{config.UPDATE_CHANNEL.lstrip('@')}" if config.UPDATE_CHANNEL else "https://t.me"
    )
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Join Update Channel", url=link)],
            [InlineKeyboardButton("I Joined ✅", callback_data="fsub_check")],
        ]
    )


# --- Login reply collector (runs before every other handler) --------------

def _awaiting_login_reply(_, __, message: Message) -> bool:
    return (
        message.from_user is not None
        and message.from_user.id in _login_waits
        and bool(message.text)
        and not message.text.startswith("/")
    )


@app.on_message(filters.private & filters.create(_awaiting_login_reply), group=-1)
async def _collect_login_reply(_, message: Message):
    fut = _login_waits.pop(message.from_user.id, None)
    if fut is not None and not fut.done():
        fut.set_result(message.text)
    # Don't let the phone/code/password trigger any other handler.
    message.stop_propagation()


# --- General commands ------------------------------------------------------

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    user = message.from_user
    if user is None:
        return
    await db.add_user(user.id, user.first_name, user.username or "")
    if not await _check_force_sub(user.id):
        await message.reply(
            "<b>You must join our Update Channel to use Chesa Auto.</b>",
            reply_markup=_force_sub_markup(),
        )
        return
    welcome = (
        f"<b>Welcome, {_esc(user.first_name)}!</b>\n\n"
        "Chesa Auto control bot.\n\n"
        "Use /help to see all commands."
    )
    await message.reply(welcome, reply_markup=_play_markup(), disable_web_page_preview=True)
    await _log(f"#NewUser <code>{user.id}</code> @{_esc(user.username) or '—'} started the bot.")


@app.on_callback_query(filters.regex("^fsub_check$"))
async def fsub_cb(_, query: CallbackQuery):
    if await _check_force_sub(query.from_user.id):
        await query.answer()
        await query.message.edit_text("<b>Thanks for joining!</b> Send /start to continue.")
    else:
        await query.answer("You haven't joined the channel yet.", show_alert=True)


@app.on_message(filters.command("play"))
async def play_cmd(_, message: Message):
    if message.from_user is None:
        return
    if not await _check_force_sub(message.from_user.id):
        await message.reply("<b>Join the Update Channel first.</b>", reply_markup=_force_sub_markup())
        return
    markup = _play_markup()
    if markup is None:
        await message.reply("Webapp URL is not configured (set <code>WEBAPP_URL</code> to an https URL).")
        return
    await message.reply("<b>Chesa Auto webapp</b>\nTap below to open it.", reply_markup=markup)


@app.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    if message.from_user is None:
        return
    stats = await db.user_stats(message.from_user.id)
    await message.reply(
        "<b>Your Chesa record</b>\n"
        f"🏆 Wins: {stats['wins']}\n❌ Losses: {stats['losses']}\n"
        f"🤝 Draws: {stats['draws']}\n♟️ Total: {stats['total']}"
    )


@app.on_message(filters.command("ping"))
async def ping_cmd(_, message: Message):
    start = time.monotonic()
    msg = await message.reply("Pong!")
    latency_ms = round((time.monotonic() - start) * 1000)
    uptime_s = int(time.time() - _start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    await msg.edit_text(f"<b>Pong!</b>\nLatency: {latency_ms} ms\nUptime: {h}h {m}m {s}s")


@app.on_message(filters.command("id"))
async def id_cmd(_, message: Message):
    uid = message.from_user.id if message.from_user else "—"
    await message.reply(f"Your ID: <code>{uid}</code>\nChat ID: <code>{message.chat.id}</code>")


@app.on_message(filters.command("users"))
async def users_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    await message.reply(f"Total users: <b>{await db.count_users()}</b>")


@app.on_message(filters.command("broadcast"))
async def broadcast_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    if not message.reply_to_message:
        await message.reply("Reply to a message to broadcast it.")
        return
    ids = await db.list_user_ids()
    status = await message.reply(f"Broadcasting to {len(ids)} users…")
    _spawn(_do_broadcast(message.reply_to_message, ids, status))


async def _do_broadcast(src: Message, ids: list[int], status: Message) -> None:
    sent = failed = removed = 0
    for uid in ids:
        try:
            await src.copy(uid)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await src.copy(uid)
                sent += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            await db.remove_user(uid)
            removed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"Broadcast done.\nSent: {sent}\nFailed: {failed}\nRemoved (blocked/deleted): {removed}"
    )


@app.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    await message.reply(HELP_TEXT)


# --- Login flow (owner only, private chat) --------------------------------

@app.on_message(filters.command("login"))
async def login_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    if message.chat.id != message.from_user.id:
        await message.reply("For safety, run /login in a private chat with me.")
        return
    info = auth.get_user_info()
    if info is not None:
        await message.reply(
            f"Already signed in as <b>{_esc(info['first_name'])}</b> (@{_esc(info['username']) or '—'}). "
            "Use /logout first."
        )
        return
    if auth.login_in_progress():
        await message.reply("A login flow is already running. Send /cancellogin to abort it.")
        return

    owner_id = message.from_user.id

    async def ask(text: str) -> None:
        await app.send_message(owner_id, text)

    async def collect() -> Optional[str]:
        fut = asyncio.get_running_loop().create_future()
        _login_waits[owner_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            await ask("⌛ Timed out waiting for your reply.")
            return None
        finally:
            _login_waits.pop(owner_id, None)

    async def flow() -> None:
        if await auth.login_with_flow(ask, collect):
            await _log("✅ Owner user session created via /login.")

    # Run in the background so the handler returns and the next replies can arrive.
    _spawn(flow())


@app.on_message(filters.command("cancellogin"))
async def cancel_login_cmd(_, message: Message):
    if message.from_user is None:
        return
    fut = _login_waits.pop(message.from_user.id, None)
    if fut is not None and not fut.done():
        fut.set_result(None)
    else:
        await message.reply("No active login flow.")


@app.on_message(filters.command("logout"))
async def logout_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    ok = await auth.logout()
    await message.reply("Logged out. Session deleted." if ok else "Logout failed — check logs.")
    if ok:
        await _log("🛑 Owner logged out the user session.")


@app.on_message(filters.command("whoami"))
async def whoami_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    info = auth.get_user_info()
    if info is None:
        await message.reply("No user session. Run /login first.")
        return
    await message.reply(
        f"Signed in as:\nName: {_esc(info['first_name'])}\nUsername: @{_esc(info['username']) or '—'}\n"
        f"ID: <code>{info['id']}</code>\nPhone: {_esc(info.get('phone')) or '—'}"
    )


@app.on_message(filters.command("session"))
async def session_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    saved = await auth.session_exists()
    info = auth.get_user_info()
    user = f"{_esc(info['first_name'])} @{_esc(info['username']) or '—'}" if info else "—"
    await message.reply(
        f"Saved session (MongoDB): {'✅' if saved else '❌'}\n"
        f"Client active: {'✅' if info else '❌'}\n"
        f"User: {user}"
    )


# --- Autoplay control (owner only) — unchanged behaviour ------------------

@app.on_message(filters.command("autoplay"))
async def autoplay_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    if auth.get_user_info() is None:
        await message.reply("No user session. Run /login first.")
        return
    parts = (message.text or "").split()
    rounds = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    depth = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else config.AUTOPLAY_DEPTH
    depth = max(2, min(4, depth))
    if autoplay.start(rounds=rounds, depth=depth):
        mode_desc = "continuous (unlimited)" if rounds == 0 else f"{rounds} rounds"
        await message.reply(f"🎮 Autoplay started — {mode_desc}, depth {depth}. See /autostatus.")
    else:
        await message.reply("Autoplay is already running. Use /stopplay first.")


@app.on_message(filters.command("stopplay"))
async def stopplay_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    autoplay.stop()
    await message.reply("⏹ Autoplay stopped.")


@app.on_message(filters.command("autostatus"))
async def autostatus_cmd(_, message: Message):
    if not _owner_only(message):
        await message.reply("Owner only.")
        return
    s = autoplay.status()
    user = s.get("user")
    await message.reply(
        "<b>Autoplay status</b>\n"
        f"State: {_esc(s['state'])}\nActive: {'✅' if s['active'] else '❌'}\n"
        f"Round: {s['rounds']}\n🏆 Wins: {s['wins']}\n❌ Losses: {s['losses']}\n"
        f"🤝 Draws: {s['draws']}\nLast result: {_esc(s['last_result']) or '—'}\n"
        f"User session: {'✅ ' + _esc(user['first_name']) if user else '❌'}"
    )


# --- Lifecycle -------------------------------------------------------------

async def run() -> None:
    missing = config.validate()
    if missing:
        log.error("Missing required config vars: %s — set them and restart.", ", ".join(missing))
        raise SystemExit(1)

    if not await db.ping():
        log.error("MongoDB is unreachable — check MONGO_URI / Atlas network access (allow 0.0.0.0/0).")

    log.info("Starting Chesa Auto control bot…")
    await app.start()
    try:
        await app.set_bot_commands(BOT_COMMANDS)
    except Exception as e:
        log.warning("set_bot_commands failed: %s", e)
    await db.set_setting("bot_started_at", time.time())

    if await auth.load_existing_session():
        log.info("User session restored automatically.")
        if config.AUTOPLAY_ENABLED:
            log.info("Starting autoplay automatically for restored session...")
            try:
                autoplay.start(rounds=0, depth=config.AUTOPLAY_DEPTH)
            except Exception as e:
                log.warning("Could not auto-start autoplay on startup: %s", e)

    await _log("✅ Chesa Auto control bot started.")
    try:
        await idle()
    finally:
        autoplay.stop()
        await _log("🛑 Chesa Auto control bot stopped.")
        await auth.shutdown()
        await app.stop()
        await db.close()
