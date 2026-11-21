"""
GameFactory WebApp integration helpers.

Handles obtaining the WebApp game URL (via saved setting or via Telegram user account
querying @GameFactoryBot), and parsing game parameters.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from pyrogram import Client, raw
from pyrogram.types import Message

from .config import config
from .logger import get_logger
from . import database as db
from . import auth

log = get_logger("GameFactory")


async def get_saved_game_url() -> Optional[str]:
    """Retrieve manually set game URL from database or config."""
    url = await db.get_setting("game_url")
    if url and isinstance(url, str) and url.startswith("http"):
        return url
    if config.WEBAPP_URL and "gamefactory.zone" in config.WEBAPP_URL:
        return config.WEBAPP_URL
    return None


async def set_game_url(url: str) -> None:
    """Save user-provided GameFactory URL to database."""
    await db.set_setting("game_url", url.strip())
    log.info(f"Saved GameFactory URL: {url[:50]}...")


async def fetch_game_url_from_telegram(client: Client) -> Optional[str]:
    """
    Attempt to fetch the GameFactory webapp URL by asking @GameFactoryBot in Telegram
    using the authorized user account.
    """
    bot_username = config.GAME_BOT_USERNAME
    try:
        log.info(f"Sending /start to @{bot_username}...")
        await client.send_message(bot_username, "/start")
        await asyncio.sleep(2)

        # Check recent messages from the bot
        async for msg in client.get_chat_history(bot_username, limit=5):
            if msg.from_user and msg.from_user.is_bot:
                # Check inline buttons
                if msg.reply_markup and msg.reply_markup.inline_keyboard:
                    for row in msg.reply_markup.inline_keyboard:
                        for btn in row:
                            if btn.web_app and btn.web_app.url:
                                log.info(f"Found WebApp button URL: {btn.web_app.url[:50]}...")
                                return btn.web_app.url
                            if btn.url and "gamefactory.zone" in btn.url:
                                log.info(f"Found direct button URL: {btn.url[:50]}...")
                                return btn.url

                # Check if it has a Game object
                if msg.game:
                    try:
                        peer = await client.resolve_peer(bot_username)
                        res = await client.invoke(
                            raw.functions.messages.GetBotCallbackAnswer(
                                peer=peer,
                                msg_id=msg.id,
                                game=True,
                            )
                        )
                        if hasattr(res, "url") and res.url:
                            log.info(f"Obtained game callback URL: {res.url[:50]}...")
                            return res.url
                    except Exception as e:
                        log.debug(f"GetBotCallbackAnswer failed: {e}")

        # Try sending /game or chess inline
        await client.send_message(bot_username, "/game")
        await asyncio.sleep(2)
        async for msg in client.get_chat_history(bot_username, limit=3):
            if msg.from_user and msg.from_user.is_bot and msg.reply_markup:
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.web_app and btn.web_app.url:
                            return btn.web_app.url
                        if btn.url and "gamefactory.zone" in btn.url:
                            return btn.url

    except Exception as e:
        log.error(f"Error fetching game URL from Telegram: {e}")

    return None


async def get_or_fetch_game_url(client: Optional[Client] = None) -> Optional[str]:
    """Get the active game URL, preferring saved DB URL, falling back to Telegram query."""
    saved = await get_saved_game_url()
    if saved:
        return saved

    if client and client.is_connected:
        return await fetch_game_url_from_telegram(client)

    return None
