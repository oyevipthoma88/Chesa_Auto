"""
Chesa (Chess) auto-play engine.

A self-contained chess engine with:
- Full legal move generation (including castling, en passant, promotion)
- Minimax search with alpha-beta pruning
- Material + positional (piece-square table) evaluation
- Game-over detection (checkmate / stalemate / insufficient material)

The browser webapp loads board state from the GameFactoryBot webapp's DOM,
hands each position to this engine, and plays the best move back into the UI.
"""

from __future__ import annotations
from typing import Optional


WHITE, BLACK = 0, 1

PIECE_VALUES = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 20000}

PST_P = [0,0,0,0,0,0,0,0,5,10,10,-20,-20,10,10,5,5,-5,-10,0,0,-10,-5,5,0,0,0,20,20,0,0,0,5,5,10,25,25,10,5,5,10,10,10,20,20,10,10,10,50,50,50,50,50,50,50,50,0,0,0,0,0,0,0,0]
PST_N = [-50,-40,-30,-30,-30,-30,-40,-50,-40,-20,0,5,5,0,-20,-40,-30,5,10,15,15,10,5,-30,-30,0,15,20,20,15,0,-30,-30,5,15,20,20,15,5,-30,-30,0,10,15,15,10,0,-30,-40,-20,0,0,0,0,-20,-40,-50,-40,-30,-30,-30,-30,-40,-50]
PST_B = [-20,-10,-10,-10,-10,-10,-10,-20,-10,5,0,0,0,0,5,-10,-10,10,10,10,10,10,10,-10,-10,0,10,10,10,10,0,-10,-10,5,5,10,10,5,5,-10,-10,0,5,10,10,5,0,-10,-10,0,0,0,0,0,0,-10,-20,-10,-10,-10,-10,-10,-10,-20]
PST_R = [0,0,0,5,5,0,0,0,-5,-10,-10,-10,-10,-10,-10,-5,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,5,10,10,10,10,10,5,0,0,0,0,0,0,0,0]
PST_Q = [-20,-10,-10,-5,-5,-10,-10,-20,-10,0,5,0,0,0,0,-10,-10,5,5,5,5,5,0,-10,0,0,5,5,5,5,0,-5,-5,0,5,5,5,5,0,-5,-10,0,5,5,5,5,0,-10,-10,0,0,0,0,0,0,-10,-20,-10,-10,-5,-5,-10,-10,-20]
PST_K_MID = [20,30,10,0,0,10,30,20,20,20,0,0,0,0,20,20,-10,-20,-20,-20,-20,-20,-20,-10,-20,-30,-30,-40,-40,-30,-30,-20,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30]
PST_K_END = [-50,-30,-30,-30,-30,-30,-30,-50,-30,-30,0,0,0,0,-30,-30,-30,-10,20,30,30,20,-10,-30,-30,-10,30,40,40,30,-10,-30,-30,-10,30,40,40,30,-10,-30,-30,-10,20,30,30,20,-10,-30,-30,-20,-10,0,0,-10,-20,-30,-50,-40,-30,-20,-20,-30,-40,-50]
PST = {"P": PST_P, "N": PST_N, "B": PST_B, "R": PST_R, "Q": PST_Q, "K": PST_K_MID}


def _sq(file: int, rank: int) -> int:
    return rank * 8 + file


def _file(sq: int) -> int:
    return sq % 8


def _rank(sq: int) -> int:
    return sq // 8


def _on_board(file: int, rank: int) -> bool:
    return 0 <= file < 8 and 0 <= rank < 8


class Board:
    """Minimal chess board representation adequate for the autoplay engine."""

    def __init__(self) -> None:
        self.squares: list[Optional[tuple[int, str]]] = [None] * 64
        self.turn = WHITE
        self.castling = {"K": True, "Q": True, "k": True, "q": True}
        self.en_passant: Optional[int] = None
        self.halfmove = 0
        self.fullmove = 1
        self.setup_standard()

    def setup_standard(self) -> None:
        back = ["R", "N", "B", "Q", "K", "B", "N", "R"]
        for f in range(8):
            self.squares[_sq(f, 0)] = (WHITE, back[f])
            self.squares[_sq(f, 1)] = (WHITE, "P")
            self.squares[_sq(f, 6)] = (BLACK, "P")
            self.squares[_sq(f, 7)] = (BLACK, back[f])

    @staticmethod
    def from_fen(fen: str) -> "Board":
        b = Board()
        b.squares = [None] * 64
        parts = fen.strip().split()
        rows = parts[0].split("/")
        for r, row in enumerate(rows):
            f = 0
            for ch in row:
                if ch.isdigit():
                    f += int(ch)
                    continue
                color = WHITE if ch.isupper() else BLACK
                b.squares[_sq(f, 7 - r)] = (color, ch.upper())
                f += 1
        b.turn = WHITE if parts[1] == "w" else BLACK
        if len(parts) > 2:
            b.castling = {"K": "K" in parts[2], "Q": "Q" in parts[2], "k": "k" in parts[2], "q": "q" in parts[2]}
        if len(parts) > 3 and parts[3] != "-":
            b.en_passant = _sq(ord(parts[3][0]) - ord("a"), int(parts[3][1]) - 1)
        return b

    def copy(self) -> "Board":
        import copy
        return copy.deepcopy(self)

    def king_square(self, color: int) -> Optional[int]:
        for sq, p in enumerate(self.squares):
            if p and p[0] == color and p[1] == "K":
                return sq
        return None

    def is_attacked(self, sq: int, by_color: int) -> bool:
        f, r = _file(sq), _rank(sq)
        pawn_dir = -1 if by_color == WHITE else 1
        for df in (-1, 1):
            pf, pr = f + df, r + pawn_dir
            if _on_board(pf, pr):
                p = self.squares[_sq(pf, pr)]
                if p and p[0] == by_color and p[1] == "P":
                    return True
        for df, dr in ((1,2),(2,1),(-1,2),(-2,1),(1,-2),(2,-1),(-1,-2),(-2,-1)):
            pf, pr = f + df, r + dr
            if _on_board(pf, pr):
                p = self.squares[_sq(pf, pr)]
                if p and p[0] == by_color and p[1] == "N":
                    return True
        for df, dr in ((1,1),(1,-1),(-1,1),(-1,-1)):
            pf, pr = f + df, r + dr
            while _on_board(pf, pr):
                p = self.squares[_sq(pf, pr)]
                if p:
                    if p[0] == by_color and p[1] in ("B", "Q"):
                        return True
                    break
                pf += df; pr += dr
        for df, dr in ((1,0),(-1,0),(0,1),(0,-1)):
            pf, pr = f + df, r + dr
            while _on_board(pf, pr):
                p = self.squares[_sq(pf, pr)]
                if p:
                    if p[0] == by_color and p[1] in ("R", "Q"):
                        return True
                    break
                pf += df; pr += dr
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if df == 0 and dr == 0:
                    continue
                pf, pr = f + df, r + dr
                if _on_board(pf, pr):
                    p = self.squares[_sq(pf, pr)]
                    if p and p[0] == by_color and p[1] == "K":
                        return True
        return False

    def in_check(self, color: int) -> bool:
        ks = self.king_square(color)
        if ks is None:
            return False
        return self.is_attacked(ks, 1 - color)

    def _pseudo_moves(self) -> list[tuple[int, int, str]]:
        moves: list[tuple[int, int, str]] = []
        color = self.turn
        for sq in range(64):
            p = self.squares[sq]
            if not p or p[0] != color:
                continue
            piece = p[1]
            f, r = _file(sq), _rank(sq)
            if piece == "P":
                direction = 1 if color == WHITE else -1
                start_rank = 1 if color == WHITE else 6
                promo_rank = 7 if color == WHITE else 0
                nf, nr = f, r + direction
                if _on_board(nf, nr) and self.squares[_sq(nf, nr)] is None:
                    if nr == promo_rank:
                        for promo in ("Q", "R", "B", "N"):
                            moves.append((sq, _sq(nf, nr), promo))
                    else:
                        moves.append((sq, _sq(nf, nr), ""))
                        if r == start_rank:
                            nr2 = r + 2 * direction
                            if _on_board(nf, nr2) and self.squares[_sq(nf, nr2)] is None:
                                moves.append((sq, _sq(nf, nr2), ""))
                for df in (-1, 1):
                    cf, cr = f + df, r + direction
                    if not _on_board(cf, cr):
                        continue
                    tgt = _sq(cf, cr)
                    tp = self.squares[tgt]
                    if tp and tp[0] != color:
                        if cr == promo_rank:
                            for promo in ("Q", "R", "B", "N"):
                                moves.append((sq, tgt, promo))
                        else:
                            moves.append((sq, tgt, ""))
                    elif self.en_passant == tgt and cr == r + direction:
                        moves.append((sq, tgt, "ep"))
            elif piece == "N":
                for df, dr in ((1,2),(2,1),(-1,2),(-2,1),(1,-2),(2,-1),(-1,-2),(-2,-1)):
                    nf, nr = f + df, r + dr
                    if _on_board(nf, nr):
                        tp = self.squares[_sq(nf, nr)]
                        if tp is None or tp[0] != color:
                            moves.append((sq, _sq(nf, nr), ""))
            elif piece in ("B", "R", "Q"):
                dirs = []
                if piece in ("B", "Q"):
                    dirs += [(1,1),(1,-1),(-1,1),(-1,-1)]
                if piece in ("R", "Q"):
                    dirs += [(1,0),(-1,0),(0,1),(0,-1)]
                for df, dr in dirs:
                    nf, nr = f + df, r + dr
                    while _on_board(nf, nr):
                        tp = self.squares[_sq(nf, nr)]
                        if tp is None:
                            moves.append((sq, _sq(nf, nr), ""))
                        else:
                            if tp[0] != color:
                                moves.append((sq, _sq(nf, nr), ""))
                            break
                        nf += df; nr += dr
            elif piece == "K":
                for df in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        if df == 0 and dr == 0:
                            continue
                        nf, nr = f + df, r + dr
                        if _on_board(nf, nr):
                            tp = self.squares[_sq(nf, nr)]
                            if tp is None or tp[0] != color:
                                moves.append((sq, _sq(nf, nr), ""))
                home = 0 if color == WHITE else 7
                if r == home and f == 4:
                    if (color == WHITE and self.castling["K"]) or (color == BLACK and self.castling["k"]):
                        if self.squares[_sq(5, home)] is None and self.squares[_sq(6, home)] is None:
                            if self.squares[_sq(7, home)] == (color, "R"):
                                if not self.is_attacked(_sq(4, home), 1 - color) and not self.is_attacked(_sq(5, home), 1 - color) and not self.is_attacked(_sq(6, home), 1 - color):
                                    moves.append((sq, _sq(6, home), "castle"))
                    if (color == WHITE and self.castling["Q"]) or (color == BLACK and self.castling["q"]):
                        if self.squares[_sq(3, home)] is None and self.squares[_sq(2, home)] is None and self.squares[_sq(1, home)] is None:
                            if self.squares[_sq(0, home)] == (color, "R"):
                                if not self.is_attacked(_sq(4, home), 1 - color) and not self.is_attacked(_sq(3, home), 1 - color) and not self.is_attacked(_sq(2, home), 1 - color):
                                    moves.append((sq, _sq(2, home), "castle"))
        return moves

    def _make_raw(self, move: tuple[int, int, str]) -> None:
        frm, to, flag = move
        piece = self.squares[frm]
        self.squares[to] = piece
        self.squares[frm] = None
        if flag == "ep":
            cap_sq = _sq(_file(to), _rank(to) + (1 if piece[0] == WHITE else -1))
            self.squares[cap_sq] = None
        elif flag == "castle":
            home_rank = _rank(frm)
            if _file(to) == 6:
                rook = self.squares[_sq(7, home_rank)]
                self.squares[_sq(5, home_rank)] = rook
                self.squares[_sq(7, home_rank)] = None
            else:
                rook = self.squares[_sq(0, home_rank)]
                self.squares[_sq(3, home_rank)] = rook
                self.squares[_sq(0, home_rank)] = None
        elif flag in ("Q", "R", "B", "N"):
            self.squares[to] = (piece[0], flag)
        if piece[1] == "K":
            if piece[0] == WHITE:
                self.castling["K"] = False; self.castling["Q"] = False
            else:
                self.castling["k"] = False; self.castling["q"] = False
        if piece[1] == "R":
            if frm == 0: self.castling["Q"] = False
            elif frm == 7: self.castling["K"] = False
            elif frm == 56: self.castling["q"] = False
            elif frm == 63: self.castling["k"] = False
        if to == 0: self.castling["Q"] = False
        elif to == 7: self.castling["K"] = False
        elif to == 56: self.castling["q"] = False
        elif to == 63: self.castling["k"] = False
        self.en_passant = None
        if piece[1] == "P" and abs(_rank(to) - _rank(frm)) == 2:
            self.en_passant = _sq(_file(frm), (_rank(frm) + _rank(to)) // 2)
        self.turn = 1 - self.turn

    def legal_moves(self) -> list[tuple[int, int, str]]:
        legal = []
        color = self.turn
        for mv in self._pseudo_moves():
            b2 = self.copy()
            b2._make_raw(mv)
            if not b2.in_check(color):
                legal.append(mv)
        return legal

    def make_move(self, move: tuple[int, int, str]) -> None:
        self._make_raw(move)

    def is_insufficient_material(self) -> bool:
        pieces = [p for p in self.squares if p]
        non_kings = [p for p in pieces if p[1] != "K"]
        if not non_kings:
            return True
        if len(non_kings) == 1 and non_kings[0][1] in ("N", "B"):
            return True
        if len(non_kings) == 2 and all(p[1] == "B" for p in non_kings):
            return True
        return False

    def game_over(self) -> Optional[str]:
        moves = self.legal_moves()
        if not moves:
            if self.in_check(self.turn):
                return "checkmate"
            return "stalemate"
        if self.is_insufficient_material():
            return "draw"
        if self.halfmove >= 100:
            return "draw"
        return None


def _mirror(sq: int) -> int:
    return _sq(_file(sq), 7 - _rank(sq))


def _pst_value(piece: str, color: int, sq: int, endgame: bool) -> int:
    if piece == "K" and endgame:
        table = PST_K_END
    else:
        table = PST[piece]
    idx = sq if color == WHITE else _mirror(sq)
    return table[idx]


def evaluate(board: Board) -> int:
    total = 0
    for p in board.squares:
        if p and p[1] != "K":
            total += PIECE_VALUES[p[1]]
    endgame = total < 1300
    score = 0
    for sq, p in enumerate(board.squares):
        if not p:
            continue
        val = PIECE_VALUES[p[1]] + _pst_value(p[1], p[0], sq, endgame)
        score += val if p[0] == WHITE else -val
    return score


def _order_moves(board: Board, moves: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    def score(mv):
        frm, to, flag = mv
        s = 0
        victim = board.squares[to]
        if victim:
            s += 10 * PIECE_VALUES[victim[1]] - PIECE_VALUES[board.squares[frm][1]]
        if flag in ("Q", "R", "B", "N"):
            s += 800
        if flag == "ep":
            s += 100
        if board.is_attacked(to, 1 - board.turn):
            s -= PIECE_VALUES[board.squares[frm][1]] // 2
        return -s
    return sorted(moves, key=score)


def minimax(board: Board, depth: int, alpha: int, beta: int, maximizing: bool) -> int:
    status = board.game_over()
    if status == "checkmate":
        return -100000 if maximizing else 100000
    if status in ("stalemate", "draw"):
        return 0
    if depth == 0:
        return evaluate(board)
    moves = _order_moves(board, board.legal_moves())
    if maximizing:
        best = -10**9
        for mv in moves:
            b2 = board.copy()
            b2.make_move(mv)
            val = minimax(b2, depth - 1, alpha, beta, False)
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if beta <= alpha:
                break
        return best
    else:
        best = 10**9
        for mv in moves:
            b2 = board.copy()
            b2.make_move(mv)
            val = minimax(b2, depth - 1, alpha, beta, True)
            if val < best:
                best = val
            if best < beta:
                beta = best
            if beta <= alpha:
                break
        return best


def best_move(board: Board, depth: int = 3) -> Optional[tuple[int, int, str]]:
    if board.game_over():
        return None
    moves = _order_moves(board, board.legal_moves())
    if not moves:
        return None
    maximizing = board.turn == WHITE
    best = None
    best_val = -10**9 if maximizing else 10**9
    alpha, beta = -10**9, 10**9
    for mv in moves:
        b2 = board.copy()
        b2.make_move(mv)
        val = minimax(b2, depth - 1, alpha, beta, not maximizing)
        if maximizing and val > best_val:
            best_val = val; best = mv
            if val > alpha:
                alpha = val
        elif not maximizing and val < best_val:
            best_val = val; best = mv
            if val < beta:
                beta = val
    return best


def square_name(sq: int) -> str:
    return f"{chr(ord('a') + _file(sq))}{_rank(sq) + 1}"


def move_to_uci(move: tuple[int, int, str]) -> str:
    frm, to, flag = move
    promo = flag.lower() if flag in ("Q", "R", "B", "N") else ""
    return f"{square_name(frm)}{square_name(to)}{promo}"


def parse_uci(uci: str) -> tuple[int, int, str]:
    frm = _sq(ord(uci[0]) - ord("a"), int(uci[1]) - 1)
    to = _sq(ord(uci[2]) - ord("a"), int(uci[3]) - 1)
    flag = ""
    if len(uci) == 5:
        flag = uci[4].upper()
    return (frm, to, flag)
