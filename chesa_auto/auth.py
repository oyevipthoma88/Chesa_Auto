"""
Interactive user-session login flow.

The control bot (BOT_TOKEN) talks to the owner. When the owner runs /login,
this module drives a pyrogram *user* client through the Telegram auth flow:
  1. ask for phone number  → client.send_code()
  2. ask for login code    → client.sign_in()
  3. ask for 2FA password  → client.check_password()  (only if needed)

On success the session is saved to SESSION_FILE (default chesa_user.session)
and the client is ready to be used by the autoplay core.
"""

from __future__ import annotations

import os
import asyncio
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)

from .config import config
from .logger import get_logger

log = get_logger("Auth")

SESSION_NAME = os.environ.get("USER_SESSION_NAME", "chesa_user")
SESSION_FILE = f"{SESSION_NAME}.session"

_user_client: Optional[Client] = None
_user_me: Optional[dict] = None
_user_lock = asyncio.Lock()


def session_exists() -> bool:
    return os.path.exists(SESSION_FILE)


def _new_client() -> Client:
    return Client(SESSION_NAME, api_id=config.API_ID, api_hash=config.API_HASH, in_memory=False)


async def load_existing_session() -> bool:
    """Try to start the saved session without prompting. Returns True if active."""
    global _user_client, _user_me
    if not session_exists():
        return False
    try:
        c = _new_client()
        await c.start()
        me = await c.get_me()
        _user_client = c
        _user_me = {
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username,
            "phone": getattr(me, "phone_number", None),
        }
        log.info("User session loaded: %s (@%s, id=%s)", _user_me["first_name"], _user_me["username"], _user_me["id"])
        return True
    except Exception as e:
        log.warning("Could not load existing session: %s", e)
        return False


async def get_user_client() -> Optional[Client]:
    global _user_client
    if _user_client and _user_client.is_connected:
        return _user_client
    if await load_existing_session():
        return _user_client
    return None


def get_user_info() -> Optional[dict]:
    return _user_me


async def logout() -> bool:
    """Log out the user session and delete the session file."""
    global _user_client, _user_me
    async with _user_lock:
        try:
            if _user_client and _user_client.is_connected:
                await _user_client.log_out()
            elif session_exists():
                os.remove(SESSION_FILE)
        except Exception as e:
            log.warning("logout error: %s", e)
            if session_exists():
                try:
                    os.remove(SESSION_FILE)
                except OSError:
                    pass
        _user_client = None
        _user_me = None
        return not session_exists()


class LoginFlow:
    """Stateful login flow. Call .run() with ask/collect callables."""

    def __init__(self, ask, collect):
        self.ask = ask
        self.collect = collect
        self.client: Optional[Client] = None
        self.phone: Optional[str] = None
        self.code_hash: Optional[str] = None

    async def run(self) -> bool:
        try:
            await self.ask("🔑 *Login flow started*\n\nSend your phone number (with country code, e.g. +91…).")
            self.phone = (await self.collect()).strip()
            if not self.phone.startswith("+"):
                self.phone = "+" + self.phone

            self.client = _new_client()
            await self.client.connect()
            sent = await self.client.send_code(self.phone)
            self.code_hash = sent.phone_code_hash

            await self.ask("📲 A login code was sent to your Telegram app.\nSend it here (format: `1 2 3 4 5` or `12345`).")
            code = (await self.collect()).strip().replace(" ", "")

            try:
                await self.client.sign_in(self.phone, self.code_hash, code)
            except SessionPasswordNeeded:
                await self.ask("🔒 Your account has 2-Step Verification. Send your cloud password.")
                password = (await self.collect()).strip()
                await self.client.check_password(password)

            me = await self.client.get_me()
            global _user_client, _user_me
            _user_client = self.client
            _user_me = {
                "id": me.id,
                "first_name": me.first_name,
                "username": me.username,
                "phone": getattr(me, "phone_number", None),
            }
            await self.ask(
                f"✅ *Login successful!*\n\nSigned in as *{me.first_name}* (@{me.username}, id `{me.id}`).\n"
                f"The session is saved. The autoplay core can now drive @GameFactoryBot."
            )
            await self.client.stop()
            await self.client.start()
            log.info("User session created for %s (@%s).", me.first_name, me.username)
            return True

        except PhoneNumberInvalid:
            await self.ask("❌ That phone number is invalid. Run /login again.")
        except PhoneCodeInvalid:
            await self.ask("❌ The login code is invalid. Run /login again.")
        except PhoneCodeExpired:
            await self.ask("❌ The login code expired. Run /login again.")
        except PasswordHashInvalid:
            await self.ask("❌ The 2FA password is wrong. Run /login again.")
        except Exception as e:
            await self.ask(f"❌ Login failed: `{e}`\nRun /login again.")
            log.warning("login flow error: %s", e)
        finally:
            if self.client and self.client.is_connected:
                try:
                    await self.client.stop()
                except Exception:
                    pass
        return False


async def login_with_flow(ask, collect) -> bool:
    flow = LoginFlow(ask, collect)
    async with _user_lock:
        return await flow.run()
