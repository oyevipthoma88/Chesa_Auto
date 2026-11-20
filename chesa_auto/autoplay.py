"""
Autoplay core.

Uses the authorized USER session (created by auth.py /login) to:
  1. open a private chat with @GameFactoryBot (or configured GAME_BOT_USERNAME)
  2. initiate game via /start, /start chess, /game and inline bot queries
  3. auto-click buttons for game entry and color selection (White/Random)
  4. watch incoming messages for game state (FEN, Unicode emoji board, 8x8 buttons, move notation)
  5. run the chess engine and execute the best move (via buttons and/or text commands)
  6. loop until the game ends (win / loss / draw) and automatically continue playing

Everything is driven through the user account so it behaves like an active human player.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .config import config
from .logger import get_logger
from . import auth
from . import database as db
from .engine import (
    Board,
    WHITE,
    BLACK,
    best_move,
    move_to_uci,
    parse_uci,
    square_name,
    _sq,
    _file,
    _rank,
)

log = get_logger("Autoplay")

GAME_BOT = config.GAME_BOT_USERNAME

UNICODE_PIECES = {
    "♙": (WHITE, "P"), "♘": (WHITE, "N"), "♗": (WHITE, "B"),
    "♖": (WHITE, "R"), "♕": (WHITE, "Q"), "♔": (WHITE, "K"),
    "♟": (BLACK, "P"), "♞": (BLACK, "N"), "♝": (BLACK, "B"),
    "♜": (BLACK, "R"), "♛": (BLACK, "Q"), "♚": (BLACK, "K"),
}
EMPTY_BOARD_SYMBOLS = {"⬜", "⬛", "▫️", "▪️", "🟩", "🟨", "·", ".", "_", "x", "-"}

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
    """Extract standard or partial FEN string from text."""
    if not text:
        return None
    # 1. Full FEN pattern
    m = re.search(
        r'([rnbqkpRNBQKP1-8]{1,8}/){7}[rnbqkpRNBQKP1-8]{1,8}\s+[wb]\s+([KQkq\-]+|\-)\s+([a-h][36]|\-)\s+\d+\s+\d+',
        text,
    )
    if m:
        return m.group(0)

    # 2. Board + turn
    m = re.search(r'([rnbqkpRNBQKP1-8]{1,8}/){7}[rnbqkpRNBQKP1-8]{1,8}\s+[wb]', text)
    if m:
        return m.group(0) + " KQkq - 0 1"

    # 3. Bare board piece layout
    m = re.search(r'([rnbqkpRNBQKP1-8]{1,8}/){7}[rnbqkpRNBQKP1-8]{1,8}', text)
    if m:
        return m.group(0) + " w KQkq - 0 1"

    # 4. Token fallback
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


def _extract_unicode_board(text: str) -> Optional[Board]:
    """Parse text containing 8x8 Unicode piece / board symbols into a Board object."""
    if not text:
        return None
    lines = text.splitlines()
    board_rows = []
    for line in lines:
        row = []
        for ch in line:
            if ch in UNICODE_PIECES:
                row.append(UNICODE_PIECES[ch])
            elif ch in EMPTY_BOARD_SYMBOLS:
                row.append(None)
        if len(row) == 8:
            board_rows.append(row)
    if len(board_rows) == 8:
        b = Board()
        b.squares = [None] * 64
        for r, row in enumerate(board_rows):
            rank = 7 - r
            for f, piece in enumerate(row):
                b.squares[_sq(f, rank)] = piece
        return b
    return None


def _extract_keyboard_board(reply_markup) -> Optional[Board]:
    """Parse an 8x8 inline keyboard grid into a Board object."""
    if not reply_markup or not hasattr(reply_markup, "inline_keyboard"):
        return None
    grid = reply_markup.inline_keyboard
    if len(grid) != 8:
        return None
    for row in grid:
        if len(row) != 8:
            return None
    b = Board()
    b.squares = [None] * 64
    for r_idx, row in enumerate(grid):
        rank = 7 - r_idx
        for f_idx, btn in enumerate(row):
            txt = (btn.text or "").strip()
            piece = None
            for ch in txt:
                if ch in UNICODE_PIECES:
                    piece = UNICODE_PIECES[ch]
                    break
            b.squares[_sq(f_idx, rank)] = piece
    return b


def _extract_from_moves(text: str) -> Optional[Board]:
    """Replay standard moves list onto a starting board."""
    if not text:
        return None
    tokens = text.replace("\n", " ").split()
    clean_moves = []
    for t in tokens:
        t = re.sub(r"^\d+[\.\)]+", "", t).strip()
        t = re.sub(r"[\+\#\?\!]", "", t).strip()
        if t and re.match(r"^[a-h][1-8][a-h][1-8][qrbn]?$", t.lower()):
            clean_moves.append(t.lower())
    if clean_moves:
        b = Board()
        for mv_uci in clean_moves:
            try:
                mv = parse_uci(mv_uci)
                if mv in b.legal_moves():
                    b.make_move(mv)
            except Exception:
                break
        return b
    return None


def _detect_result(text: str) -> Optional[str]:
    low = (text or "").lower()
    if any(w in low for w in ("you won", "you win", "victory", "game over: win", "won the game", "menang")):
        return "win"
    if any(w in low for w in ("you lost", "you lose", "defeat", "game over: lose", "lost the game", "kalah")):
        return "loss"
    if any(w in low for w in ("draw", "stalemate", "game over: draw", "seri", "remis")):
        return "draw"
    return None


async def _handle_game_bot_buttons(client: Client, msg: Message, chat_id: str | int) -> bool:
    """Click GameFactoryBot setup/entry/color buttons automatically."""
    if not msg.reply_markup or not hasattr(msg.reply_markup, "inline_keyboard"):
        return False
    clicked = False
    for row in msg.reply_markup.inline_keyboard:
        for btn in row:
            txt = (btn.text or "").strip().lower()
            is_start_btn = any(w in txt for w in (
                "create new game", "create game", "enter the game", "new game",
                "play chesa", "play chess", "play game", "join", "start",
                "white", "⚪", "random", "🎲"
            ))
            if is_start_btn:
                try:
                    if btn.callback_data:
                        await client.request_callback_answer(
                            chat_id=chat_id,
                            message_id=msg.id,
                            callback_data=btn.callback_data,
                        )
                        clicked = True
                        log.info("Clicked GameFactoryBot button: '%s'", btn.text)
                        await asyncio.sleep(1.0)
                        break
                    elif getattr(btn, "callback_game", None):
                        res = await client.request_callback_answer(
                            chat_id=chat_id,
                            message_id=msg.id,
                            game=True,
                        )
                        clicked = True
                        if hasattr(res, "url") and res.url:
                            log.info("Received game URL: %s", res.url)
                        break
                except Exception as e:
                    log.debug("Callback query click failed: %s", e)
        if clicked:
            break
    return clicked


async def _execute_move(client: Client, msg: Message, move: tuple[int, int, str], board: Board) -> None:
    """Execute move via keyboard buttons if available or send as UCI/SAN text."""
    from_sq, to_sq, promo = move
    uci = move_to_uci(move)
    f_rank_idx, f_file_idx = 7 - _rank(from_sq), _file(from_sq)
    t_rank_idx, t_file_idx = 7 - _rank(to_sq), _file(to_sq)
    from_coord = square_name(from_sq)
    to_coord = square_name(to_sq)
    coord_move = f"{from_coord}-{to_coord}"

    # 1. 8x8 inline keyboard grid click
    if msg.reply_markup and hasattr(msg.reply_markup, "inline_keyboard"):
        grid = msg.reply_markup.inline_keyboard
        if len(grid) == 8 and all(len(r) == 8 for r in grid):
            btn_from = grid[f_rank_idx][f_file_idx]
            btn_to = grid[t_rank_idx][t_file_idx]
            try:
                if btn_from.callback_data:
                    await client.request_callback_answer(GAME_BOT, msg.id, callback_data=btn_from.callback_data)
                    await asyncio.sleep(0.5)
                if btn_to.callback_data:
                    await client.request_callback_answer(GAME_BOT, msg.id, callback_data=btn_to.callback_data)
                    await asyncio.sleep(0.5)
                log.info("Played move via keyboard buttons: %s -> %s (%s)", from_coord, to_coord, uci)
                return
            except Exception as e:
                log.debug("Grid button click error: %s", e)

        # 2. Check for move buttons in inline keyboard
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                txt = (btn.text or "").strip().lower()
                cb = (btn.callback_data or "") if isinstance(btn.callback_data, str) else ""
                if uci in txt or uci in cb.lower() or coord_move in txt or coord_move in cb.lower():
                    try:
                        if btn.callback_data:
                            await client.request_callback_answer(GAME_BOT, msg.id, callback_data=btn.callback_data)
                            log.info("Played move via button: %s", uci)
                            return
                    except Exception as e:
                        log.debug("Move button click error: %s", e)

    # 3. Send as text commands
    await _send(client, uci)
    log.info("Sent move to %s: %s (coord: %s)", GAME_BOT, uci, coord_move)


async def _play_round(client: Client, depth: int) -> str:
    me = await client.get_me()
    log.info("Initiating round with %s...", GAME_BOT)

    # Wake up bot and request game
    await _send(client, "/start")
    await asyncio.sleep(1.0)
    await _send(client, "/start chess")
    await asyncio.sleep(1.0)

    # Trigger inline query for GameFactoryBot
    try:
        res = await client.get_inline_bot_results(GAME_BOT, "chess")
        if not res or not res.results:
            res = await client.get_inline_bot_results(GAME_BOT, "")
        if res and res.results:
            await client.send_inline_bot_result(GAME_BOT, res.query_id, res.results[0].id)
            await asyncio.sleep(1.5)
    except Exception as e:
        log.debug("Inline query error: %s", e)

    board = Board()
    my_color = None
    idle_moves = 0
    max_idle = 35
    last_moved_uci = None

    while idle_moves < max_idle and _status["active"]:
        recent_messages: list[Message] = []
        try:
            async for msg in client.get_chat_history(GAME_BOT, limit=8):
                recent_messages.append(msg)
        except Exception as e:
            log.debug("Chat history fetch error: %s", e)

        if not recent_messages:
            idle_moves += 1
            await asyncio.sleep(2.0)
            continue

        latest_msg = recent_messages[0]

        # 1. Detect game over in recent messages
        for msg in recent_messages[:4]:
            text = (msg.text or msg.caption or "")
            res = _detect_result(text)
            if res:
                await db.record_game(me.id, res, idle_moves)
                _status["last_result"] = res
                log.info("Game finished: %s", res)
                return res

        # 2. Click any pending entry/color buttons
        for msg in recent_messages[:3]:
            if await _handle_game_bot_buttons(client, msg, GAME_BOT):
                idle_moves = 0
                await asyncio.sleep(1.5)
                break

        # 3. Detect player color
        for msg in recent_messages[:4]:
            text = (msg.text or msg.caption or "").lower()
            if my_color is None:
                if any(w in text for w in ("you play as white", "white: you", "⚪ you", "white: @" + (me.username or "").lower())):
                    my_color = WHITE
                    log.info("Player color confirmed: WHITE")
                elif any(w in text for w in ("you play as black", "black: you", "⚫ you", "black: @" + (me.username or "").lower())):
                    my_color = BLACK
                    log.info("Player color confirmed: BLACK")

        # 4. Extract board state
        detected_board = None
        board_msg = None
        for msg in recent_messages:
            text = (msg.text or msg.caption or "")
            fen = _extract_fen(text)
            if fen:
                try:
                    detected_board = Board.from_fen(fen)
                    board_msg = msg
                    break
                except Exception:
                    pass
            ub = _extract_unicode_board(text)
            if ub:
                detected_board = ub
                board_msg = msg
                break
            kb = _extract_keyboard_board(msg.reply_markup)
            if kb:
                detected_board = kb
                board_msg = msg
                break
            mb = _extract_from_moves(text)
            if mb:
                detected_board = mb
                board_msg = msg
                break

        if detected_board is not None:
            board = detected_board
            if my_color is None:
                my_color = board.turn
                log.info("Inferred color from turn: %s", "WHITE" if my_color == WHITE else "BLACK")

        turn_text = (latest_msg.text or latest_msg.caption or "").lower()
        is_opp_turn = any(w in turn_text for w in ("waiting for opponent", "opponent's turn", "opp's turn", "menunggu lawan"))
        is_my_turn = any(w in turn_text for w in ("your turn", "your move", "it's your turn", "giliranmu", "giliran anda"))

        if is_opp_turn:
            idle_moves = 0
            await asyncio.sleep(2.0)
            continue

        should_move = False
        if is_my_turn:
            should_move = True
        elif my_color is not None and board.turn == my_color and not board.game_over():
            should_move = True
        elif detected_board is None and idle_moves > 0 and idle_moves % 6 == 0:
            await _send(client, "/start chess")
            await asyncio.sleep(1.0)

        if should_move and not board.game_over():
            mv = best_move(board, depth)
            if mv:
                uci = move_to_uci(mv)
                if uci != last_moved_uci:
                    board.make_move(mv)
                    target_msg = board_msg or latest_msg
                    await _execute_move(client, target_msg, mv, board)
                    last_moved_uci = uci
                    idle_moves = 0
                    await asyncio.sleep(2.0)
                else:
                    idle_moves += 1
            else:
                idle_moves += 1
        else:
            idle_moves += 1

        await asyncio.sleep(2.0)

    # Game over evaluation
    status_g = board.game_over() if my_color is not None else None
    if status_g == "checkmate":
        result = "win" if my_color is not None and board.turn != my_color else "loss"
    elif status_g in ("stalemate", "draw"):
        result = "draw"
    else:
        result = "draw"

    await db.record_game(me.id, result, idle_moves)
    _status["last_result"] = result
    log.info("Round ended with result: %s", result)
    return result


async def _autoplay_loop(rounds: int, depth: int) -> None:
    _status["active"] = True
    _status["state"] = "starting"
    rounds_desc = "continuous" if rounds <= 0 else f"{rounds} rounds"
    await _log(f"🎮 Autoplay started: {rounds_desc}, depth {depth}.")
    client = await auth.get_user_client()
    if client is None:
        log.error("No active user session. Run /login first.")
        _status["active"] = False
        _status["state"] = "idle"
        return

    _status["state"] = "playing"
    current_round = 0
    try:
        while _status["active"]:
            current_round += 1
            if rounds > 0 and current_round > rounds:
                break
            _status["rounds"] = current_round
            try:
                result = await _play_round(client, depth)
            except Exception as e:
                log.warning("round %d error: %s", current_round, e)
                result = "draw"
            if result == "win":
                _status["wins"] += 1
            elif result == "loss":
                _status["losses"] += 1
            else:
                _status["draws"] += 1

            total_str = f"Round {current_round}" if rounds <= 0 else f"Round {current_round}/{rounds}"
            await _log(f"{total_str}: {result}. W{_status['wins']} L{_status['losses']} D{_status['draws']}.")
            await asyncio.sleep(3.0)
    finally:
        _status["state"] = "ended"
        _status["active"] = False
        await _log(f"🏁 Autoplay finished. Total rounds: {current_round} · Wins {_status['wins']} · Losses {_status['losses']} · Draws {_status['draws']}.")


def start(rounds: int = 0, depth: int = 3) -> bool:
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
