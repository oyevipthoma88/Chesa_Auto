"""
Interactive user-session login flow (owner only).

The control bot (BOT_TOKEN) talks to the owner. When the owner runs /login,
this module drives a pyrogram *user* client through the Telegram auth flow:
  1. ask for phone number  → client.send_code()
  2. ask for login code    → client.sign_in()          (3 tries)
  3. ask for 2FA password  → client.check_password()   (only if needed, 3 tries)

On success the session is exported as a *session string* and stored in
MongoDB (settings.user_session). Heroku's filesystem is wiped on every dyno
restart, so a local ``.session`` file would be lost daily; the string in
MongoDB survives restarts and is restored automatically on boot.
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from pyrogram import Client
from pyrogram.errors import (
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberBanned,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
    FloodWait,
)

from . import database as db
from .config import config
from .logger import get_logger

log = get_logger("Auth")

SESSION_NAME = os.environ.get("USER_SESSION_NAME", "chesa_user")
SESSION_SETTING_KEY = "user_session"
MAX_TRIES = 3

_user_client: Optional[Client] = None
_user_me: Optional[dict] = None
_user_lock = asyncio.Lock()

Ask = Callable[[str], Awaitable[None]]
Collect = Callable[[], Awaitable[Optional[str]]]


class LoginCancelled(Exception):
    pass


def _client_from_string(session_string: str) -> Client:
    return Client(
        SESSION_NAME,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=True,  # the user client only sends/reads, never needs handlers
    )


def _fresh_client() -> Client:
    return Client(
        f"{SESSION_NAME}_login",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
        no_updates=True,
    )


def _me_dict(me) -> dict:
    return {
        "id": me.id,
        "first_name": me.first_name or "",
        "username": me.username or "",
        "phone": getattr(me, "phone_number", None),
    }


async def session_exists() -> bool:
    return bool(await db.get_setting(SESSION_SETTING_KEY))


async def _activate(session_string: str) -> bool:
    """Start a user client from a session string and make it the active one."""
    global _user_client, _user_me
    client = _client_from_string(session_string)
    try:
        await client.start()
        me = await client.get_me()
    except Exception as e:
        log.warning("Could not start user session: %s", e)
        try:
            if client.is_connected:
                await client.stop()
        except Exception:
            pass
        return False
    old = _user_client
    _user_client, _user_me = client, _me_dict(me)
    if old is not None and old is not client and old.is_connected:
        try:
            await old.stop()
        except Exception:
            pass
    log.info("User session active: %s (@%s, id=%s)", _user_me["first_name"], _user_me["username"], _user_me["id"])
    return True


async def load_existing_session() -> bool:
    """Restore the saved session from MongoDB without prompting."""
    session_string = await db.get_setting(SESSION_SETTING_KEY)
    if not session_string:
        return False
    ok = await _activate(session_string)
    if not ok:
        log.warning("Saved session is invalid/revoked — run /login again.")
    return ok


async def get_user_client() -> Optional[Client]:
    if _user_client is not None and _user_client.is_connected:
        return _user_client
    if await load_existing_session():
        return _user_client
    return None


def get_user_info() -> Optional[dict]:
    return _user_me


async def shutdown() -> None:
    global _user_client
    if _user_client is not None and _user_client.is_connected:
        try:
            await _user_client.stop()
        except Exception:
            pass


async def logout() -> bool:
    """Revoke the Telegram session and delete it from MongoDB."""
    global _user_client, _user_me
    async with _user_lock:
        if _user_client is not None:
            try:
                if _user_client.is_connected:
                    await _user_client.log_out()
            except Exception as e:
                log.warning("logout error: %s", e)
        _user_client = None
        _user_me = None
        await db.delete_setting(SESSION_SETTING_KEY)
        return not await session_exists()


class LoginFlow:
    """Stateful login flow. Call .run() with ask/collect callables."""

    def __init__(self, ask: Ask, collect: Collect):
        self.ask = ask
        self.collect = collect
        self.client: Optional[Client] = None

    async def _get(self) -> str:
        reply = await self.collect()
        if reply is None:
            raise LoginCancelled()
        return reply.strip()

    async def run(self) -> bool:
        try:
            await self.ask(
                "🔑 <b>Login flow started</b>\n\nSend your phone number with country code "
                "(e.g. <code>+91XXXXXXXXXX</code>).\nSend /cancellogin to abort."
            )
            phone = (await self._get()).replace(" ", "").replace("-", "")
            if not phone.startswith("+"):
                phone = "+" + phone

            self.client = _fresh_client()
            await self.client.connect()
            sent = await self.client.send_code(phone)

            signed_in = False
            for attempt in range(1, MAX_TRIES + 1):
                await self.ask(
                    "📲 A login code was sent to your Telegram app.\n"
                    "Send it here with spaces so Telegram doesn't expire it, e.g. <code>1 2 3 4 5</code>."
                )
                code = (await self._get()).replace(" ", "")
                try:
                    result = await self.client.sign_in(phone, sent.phone_code_hash, code)
                    if result is False or not hasattr(result, "id"):
                        await self.ask("❌ This phone number has no Telegram account. Run /login again.")
                        return False
                    signed_in = True
                    break
                except SessionPasswordNeeded:
                    signed_in = await self._password()
                    break
                except PhoneCodeInvalid:
                    if attempt == MAX_TRIES:
                        await self.ask("❌ Too many wrong codes. Run /login again.")
                        return False
                    await self.ask(f"❌ Wrong code ({attempt}/{MAX_TRIES}). Try again.")
            if not signed_in:
                return False

            session_string = await self.client.export_session_string()
            await self.client.disconnect()
            self.client = None

            await db.set_setting(SESSION_SETTING_KEY, session_string)
            if not await _activate(session_string):
                await self.ask("❌ Signed in, but the session could not be started. Run /login again.")
                return False

            me = _user_me or {}
            await self.ask(
                f"✅ <b>Login successful!</b>\n\nSigned in as <b>{me.get('first_name')}</b> "
                f"(@{me.get('username') or '—'}, id <code>{me.get('id')}</code>).\n"
                "The session is saved and will be restored automatically after restarts.\n\n"
                "🚀 <b>Core auto-play starting automatically...</b>"
            )
            try:
                from . import autoplay
                autoplay.start(rounds=0, depth=config.AUTOPLAY_DEPTH)
                log.info("Autoplay launched automatically after successful login.")
            except Exception as e:
                log.warning("Could not auto-start autoplay after login: %s", e)
            return True

        except LoginCancelled:
            await self.ask("Login cancelled.")
        except PhoneNumberInvalid:
            await self.ask("❌ That phone number is invalid. Run /login again.")
        except PhoneNumberBanned:
            await self.ask("❌ This phone number is banned by Telegram.")
        except PhoneCodeExpired:
            await self.ask("❌ The login code expired. Run /login again (send the code with spaces).")
        except FloodWait as e:
            await self.ask(f"⏳ Telegram flood-wait: try again in {e.value} seconds.")
        except Exception as e:
            log.warning("login flow error: %s", e)
            await self.ask(f"❌ Login failed: <code>{type(e).__name__}: {e}</code>\nRun /login again.")
        finally:
            if self.client is not None and self.client.is_connected:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.client = None
        return False

    async def _password(self) -> bool:
        assert self.client is not None
        for attempt in range(1, MAX_TRIES + 1):
            await self.ask("🔒 Your account has 2-Step Verification. Send your cloud password.")
            password = await self._get()
            try:
                await self.client.check_password(password)
                return True
            except PasswordHashInvalid:
                if attempt == MAX_TRIES:
                    await self.ask("❌ Too many wrong passwords. Run /login again.")
                    return False
                await self.ask(f"❌ Wrong password ({attempt}/{MAX_TRIES}). Try again.")
        return False


def login_in_progress() -> bool:
    return _user_lock.locked()


async def login_with_flow(ask: Ask, collect: Collect) -> bool:
    if _user_lock.locked():
        await ask("A login flow is already running. Finish it or send /cancellogin.")
        return False
    async with _user_lock:
        return await LoginFlow(ask, collect).run()
