"""
Autoplay core.

Uses the authorized USER session (created by auth.py /login) to:
  1. open a private chat with @GameFactoryBot
  2. send /start and start a Chesa game
  3. watch incoming messages for game state / board updates
  4. run the chess engine and send back the best move
  5. loop until the game ends (win / loss / draw)

Everything is driven through the user account so it behaves exactly like a
human opening the game and moving pieces.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pyrogram import Client

from .config import config
from .logger import get_logger
from . import auth
from . import database as db
from .engine import Board, best_move, move_to_uci

log = get_logger("Autoplay")

GAME_BOT = config.GAME_BOT_USERNAME

_status = {
    "active": False,
    "state": "idle",
    "wins": 0,
    "losses": 0,
    "draws": 0,
    "rounds": 0,
    "last_result": None,
}
_task: Optional[asyncio.Task] = None


def status() -> dict:
    info = dict(_status)
    info["logged_in"] = auth.get_user_info() is not None
    info["user"] = auth.get_user_info()
    return info


async def _log(text: str) -> None:
    try:
        from .bot import app as ctrl
        if ctrl and ctrl.is_connected and config.LOG_CHANNEL:
            await ctrl.send_message(config.LOG_CHANNEL, text)
    except Exception:
        pass


async def _send(client: Client, text: str) -> None:
    try:
        await client.send_message(GAME_BOT, text)
    except Exception as e:
        log.warning("send to %s failed: %s", GAME_BOT, e)


def _extract_fen(text: str) -> Optional[str]:
    if not text:
        return None
    tokens = text.split()
    for i, t in enumerate(tokens):
        if "/" in t and len(t) >= 8 and t.count("/") == 7 and all(
            ch.isdigit() or ch in "prnbqkPRNBQK" for ch in t.replace("/", "")
        ):
            rest = " ".join(tokens[i:])
            if len(rest.split()) >= 2:
                return rest
            return f"{t} w KQkq - 0 1"
    return None


def _detect_result(text: str) -> Optional[str]:
    low = (text or "").lower()
    if any(w in low for w in ("you won", "you win", "victory", "game over: win", "won the game")):
        return "win"
    if any(w in low for w in ("you lost", "you lose", "defeat", "game over: lose", "lost the game")):
        return "loss"
    if any(w in low for w in ("draw", "stalemate", "game over: draw")):
        return "draw"
    return None


async def _play_round(client: Client, depth: int) -> str:
    me = await client.get_me()
    await _send(client, "/start")
    await asyncio.sleep(2.0)
    await _send(client, "/start")
    await asyncio.sleep(2.0)

    board = Board()
    my_color = None
    idle_moves = 0
    max_idle = 40

    for _ in range(max_idle):
        if not _status["active"]:
            break
        try:
            async for msg in client.get_chat_history(GAME_BOT, limit=5):
                text = (msg.text or msg.caption or "")
                result = _detect_result(text)
                if result:
                    await db.record_game(me.id, result, idle_moves)
                    _status["last_result"] = result
                    return result
                fen = _extract_fen(text)
                if fen and my_color is None:
                    fb = Board.from_fen(fen)
                    my_color = fb.turn
                    board = fb
                    break
        except Exception as e:
            log.debug("history read error: %s", e)

        if my_color is not None and board.turn == my_color and not board.game_over():
            mv = best_move(board, depth)
            if mv:
                board.make_move(mv)
                await _send(client, move_to_uci(mv))
                idle_moves = 0
                await asyncio.sleep(1.5)
            else:
                idle_moves += 1
        else:
            idle_moves += 1
        await asyncio.sleep(2.0)

    status_g = board.game_over() if my_color is not None else None
    if status_g == "checkmate":
        result = "win" if my_color is not None and board.turn != my_color else "loss"
    elif status_g in ("stalemate", "draw"):
        result = "draw"
    else:
        result = "draw"
    await db.record_game(me.id, result, idle_moves)
    _status["last_result"] = result
    return result


async def _autoplay_loop(rounds: int, depth: int) -> None:
    _status["active"] = True
    _status["state"] = "starting"
    await _log(f"🎮 Autoplay started: {rounds} rounds, depth {depth}.")
    client = await auth.get_user_client()
    if client is None:
        log.error("No active user session. Run /login first.")
        _status["active"] = False
        _status["state"] = "idle"
        return

    _status["state"] = "playing"
    for r in range(1, rounds + 1):
        if not _status["active"]:
            break
        _status["rounds"] = r
        try:
            result = await _play_round(client, depth)
        except Exception as e:
            log.warning("round %d error: %s", r, e)
            result = "draw"
        if result == "win":
            _status["wins"] += 1
        elif result == "loss":
            _status["losses"] += 1
        else:
            _status["draws"] += 1
        await _log(f"Round {r}/{rounds}: {result}. W{_status['wins']} L{_status['losses']} D{_status['draws']}.")
        await asyncio.sleep(3.0)

    _status["state"] = "ended"
    _status["active"] = False
    await _log(f"🏁 Autoplay finished. Wins {_status['wins']} · Losses {_status['losses']} · Draws {_status['draws']}.")


def start(rounds: int = 10, depth: int = 3) -> bool:
    global _task
    if _status["active"]:
        return False
    if auth.get_user_info() is None:
        return False
    _status["wins"] = 0
    _status["losses"] = 0
    _status["draws"] = 0
    _status["rounds"] = 0
    _status["last_result"] = None
    _task = asyncio.get_event_loop().create_task(_autoplay_loop(rounds, depth))
    return True


def stop() -> bool:
    global _task
    _status["active"] = False
    if _task and not _task.done():
        _task.cancel()
    _task = None
    _status["state"] = "idle"
    return True
