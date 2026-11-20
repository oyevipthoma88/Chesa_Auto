/*
 * Chesa Auto — chess engine (browser-side), ported from the Python engine.
 * Full legal move generation, alpha-beta minimax, material + PST eval.
 * Pieces: uppercase=White, lowercase=black. Board = 64 entries.
 */
(function () {
  "use strict";

  const WHITE = 0, BLACK = 1;
  const VAL = { p: 100, n: 320, b: 330, r: 500, q: 900, k: 20000 };

  const PST = {
    p: [0,0,0,0,0,0,0,0,5,10,10,-20,-20,10,10,5,5,-5,-10,0,0,-10,-5,5,0,0,0,20,20,0,0,0,5,5,10,25,25,10,5,5,10,10,10,20,20,10,10,10,50,50,50,50,50,50,50,50,0,0,0,0,0,0,0,0],
    n: [-50,-40,-30,-30,-30,-30,-40,-50,-40,-20,0,5,5,0,-20,-40,-30,5,10,15,15,10,5,-30,-30,0,15,20,20,15,0,-30,-30,5,15,20,20,15,5,-30,-30,0,10,15,15,10,0,-30,-40,-20,0,0,0,0,-20,-40,-50,-40,-30,-30,-30,-30,-40,-50],
    b: [-20,-10,-10,-10,-10,-10,-10,-20,-10,5,0,0,0,0,5,-10,-10,10,10,10,10,10,10,-10,-10,0,10,10,10,10,0,-10,-10,5,5,10,10,5,5,-10,-10,0,5,10,10,5,0,-10,-10,0,0,0,0,0,0,-10,-20,-10,-10,-10,-10,-10,-10,-20],
    r: [0,0,0,5,5,0,0,0,-5,-10,-10,-10,-10,-10,-10,-5,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,0,-10,-10,-10,-10,-10,-10,0,5,10,10,10,10,10,5,0,0,0,0,0,0,0,0],
    q: [-20,-10,-10,-5,-5,-10,-10,-20,-10,0,5,0,0,0,0,-10,-10,5,5,5,5,5,0,-10,0,0,5,5,5,5,0,-5,-5,0,5,5,5,5,0,-5,-10,0,5,5,5,5,0,-10,-10,0,0,0,0,0,0,-10,-20,-10,-10,-5,-5,-10,-10,-20],
    k: [20,30,10,0,0,10,30,20,20,20,0,0,0,0,20,20,-10,-20,-20,-20,-20,-20,-20,-10,-20,-30,-30,-40,-40,-30,-30,-20,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30,-30,-40,-40,-50,-50,-40,-40,-30],
    ke: [-50,-30,-30,-30,-30,-30,-30,-50,-30,-30,0,0,0,0,-30,-30,-30,-10,20,30,30,20,-10,-30,-30,-10,30,40,40,30,-10,-30,-30,-10,30,40,40,30,-10,-30,-30,-10,20,30,30,20,-10,-30,-30,-20,-10,0,0,-10,-20,-30,-50,-40,-30,-20,-20,-30,-40,-50],
  };

  const sq = (f, r) => r * 8 + f;
  const fileOf = (s) => s % 8;
  const rankOf = (s) => Math.floor(s / 8);
  const onBoard = (f, r) => f >= 0 && f < 8 && r >= 0 && r < 8;
  const mirror = (s) => sq(fileOf(s), 7 - rankOf(s));
  const isWhiteP = (p) => p && p === p.toUpperCase();
  const colorOf = (p) => (isWhiteP(p) ? WHITE : BLACK);
  const letterOf = (p) => p.toLowerCase();

  class Board {
    constructor(fen) { if (fen) this.loadFen(fen); else this.reset(); }
    reset() {
      this.b = Array(64).fill(null);
      const back = ["r","n","b","q","k","b","n","r"];
      for (let f = 0; f < 8; f++) {
        this.b[sq(f, 0)] = back[f].toUpperCase();
        this.b[sq(f, 1)] = "P";
        this.b[sq(f, 6)] = "p";
        this.b[sq(f, 7)] = back[f];
      }
      this.turn = WHITE;
      this.castling = { K: true, Q: true, k: true, q: true };
      this.ep = null;
    }
    loadFen(fen) {
      this.b = Array(64).fill(null);
      const parts = fen.trim().split(/\s+/);
      const rows = parts[0].split("/");
      for (let r = 0; r < 8; r++) {
        let f = 0;
        for (const ch of rows[r]) {
          if (/\d/.test(ch)) { f += +ch; continue; }
          this.b[sq(f, 7 - r)] = ch; f++;
        }
      }
      this.turn = parts[1] === "w" ? WHITE : BLACK;
      this.castling = { K: /K/.test(parts[2]||""), Q: /Q/.test(parts[2]||""), k: /k/.test(parts[2]||""), q: /q/.test(parts[2]||"") };
      this.ep = parts[3] && parts[3] !== "-" ? sq(parts[3].charCodeAt(0)-97, +parts[3][1]-1) : null;
    }
    clone() {
      const n = new Board(); n.b = this.b.slice(); n.turn = this.turn;
      n.castling = { ...this.castling }; n.ep = this.ep; return n;
    }
    kingSq(color) { return this.b.indexOf(color === WHITE ? "K" : "k"); }
    isAttacked(t, by) {
      const f = fileOf(t), r = rankOf(t);
      const enemyPawn = by === WHITE ? "P" : "p";
      const dir = by === WHITE ? -1 : 1;
      for (const df of [-1, 1]) {
        const pf = f + df, pr = r + dir;
        if (onBoard(pf, pr) && this.b[sq(pf, pr)] === enemyPawn) return true;
      }
      const knight = by === WHITE ? "N" : "n";
      for (const [df, dr] of [[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]]) {
        if (onBoard(f+df, r+dr) && this.b[sq(f+df, r+dr)] === knight) return true;
      }
      const bishop = by === WHITE ? "B" : "b", queen = by === WHITE ? "Q" : "q";
      for (const [df, dr] of [[1,1],[1,-1],[-1,1],[-1,-1]]) {
        let pf = f+df, pr = r+dr;
        while (onBoard(pf, pr)) {
          const p = this.b[sq(pf, pr)];
          if (p) { if (p === bishop || p === queen) return true; break; }
          pf += df; pr += dr;
        }
      }
      const rook = by === WHITE ? "R" : "r";
      for (const [df, dr] of [[1,0],[-1,0],[0,1],[0,-1]]) {
        let pf = f+df, pr = r+dr;
        while (onBoard(pf, pr)) {
          const p = this.b[sq(pf, pr)];
          if (p) { if (p === rook || p === queen) return true; break; }
          pf += df; pr += dr;
        }
      }
      const king = by === WHITE ? "K" : "k";
      for (let df = -1; df <= 1; df++) for (let dr = -1; dr <= 1; dr++) {
        if (!df && !dr) continue;
        if (onBoard(f+df, r+dr) && this.b[sq(f+df, r+dr)] === king) return true;
      }
      return false;
    }
    inCheck(color) { const ks = this.kingSq(color); return ks >= 0 && this.isAttacked(ks, 1 - color); }
    pseudo() {
      const moves = []; const color = this.turn;
      for (let s = 0; s < 64; s++) {
        const p = this.b[s]; if (!p || colorOf(p) !== color) continue;
        const f = fileOf(s), r = rankOf(s), letter = letterOf(p);
        if (letter === "p") {
          const dir = color === WHITE ? 1 : -1;
          const startRank = color === WHITE ? 1 : 6;
          const promoRank = color === WHITE ? 7 : 0;
          if (onBoard(f, r + dir) && !this.b[sq(f, r + dir)]) {
            if (r + dir === promoRank) ["q","r","b","n"].forEach(pr => moves.push([s, sq(f, r+dir), pr]));
            else { moves.push([s, sq(f, r+dir), ""]); if (r === startRank && !this.b[sq(f, r+2*dir)]) moves.push([s, sq(f, r+2*dir), ""]); }
          }
          for (const df of [-1, 1]) {
            const cf = f+df, cr = r+dir; if (!onBoard(cf, cr)) continue;
            const t = sq(cf, cr), tp = this.b[t];
            if (tp && colorOf(tp) !== color) {
              if (cr === promoRank) ["q","r","b","n"].forEach(pr => moves.push([s, t, pr])); else moves.push([s, t, ""]);
            } else if (this.ep === t && cr === r+dir) moves.push([s, t, "ep"]);
          }
        } else if (letter === "n") {
          for (const [df, dr] of [[1,2],[2,1],[-1,2],[-2,1],[1,-2],[2,-1],[-1,-2],[-2,-1]]) {
            const nf = f+df, nr = r+dr;
            if (onBoard(nf, nr)) { const tp = this.b[sq(nf,nr)]; if (!tp || colorOf(tp) !== color) moves.push([s, sq(nf,nr), ""]); }
          }
        } else if ("brq".includes(letter)) {
          const dirs = [];
          if ("bq".includes(letter)) dirs.push([1,1],[1,-1],[-1,1],[-1,-1]);
          if ("rq".includes(letter)) dirs.push([1,0],[-1,0],[0,1],[0,-1]);
          for (const [df, dr] of dirs) {
            let nf = f+df, nr = r+dr;
            while (onBoard(nf, nr)) {
              const tp = this.b[sq(nf,nr)];
              if (!tp) moves.push([s, sq(nf,nr), ""]);
              else { if (colorOf(tp) !== color) moves.push([s, sq(nf,nr), ""]); break; }
              nf += df; nr += dr;
            }
          }
        } else if (letter === "k") {
          for (let df = -1; df <= 1; df++) for (let dr = -1; dr <= 1; dr++) {
            if (!df && !dr) continue;
            const nf = f+df, nr = r+dr;
            if (onBoard(nf, nr)) { const tp = this.b[sq(nf,nr)]; if (!tp || colorOf(tp) !== color) moves.push([s, sq(nf,nr), ""]); }
          }
          const home = color === WHITE ? 0 : 7;
          const kc = color === WHITE ? "K" : "k", qc = color === WHITE ? "Q" : "q";
          if (r === home && f === 4) {
            if (this.castling[kc] && !this.b[sq(5,home)] && !this.b[sq(6,home)] && this.b[sq(7,home)] === (color===WHITE?"R":"r")
              && !this.isAttacked(sq(4,home),1-color) && !this.isAttacked(sq(5,home),1-color) && !this.isAttacked(sq(6,home),1-color))
              moves.push([s, sq(6,home), "castle"]);
            if (this.castling[qc] && !this.b[sq(3,home)] && !this.b[sq(2,home)] && !this.b[sq(1,home)] && this.b[sq(0,home)] === (color===WHITE?"R":"r")
              && !this.isAttacked(sq(4,home),1-color) && !this.isAttacked(sq(3,home),1-color) && !this.isAttacked(sq(2,home),1-color))
              moves.push([s, sq(2,home), "castle"]);
          }
        }
      }
      return moves;
    }
    make(move) {
      const [frm, to, flag] = move; const piece = this.b[frm];
      this.b[to] = piece; this.b[frm] = null;
      if (flag === "ep") this.b[sq(fileOf(to), rankOf(to) + (colorOf(piece)===WHITE?-1:1))] = null;
      else if (flag === "castle") {
        const hr = rankOf(frm);
        if (fileOf(to) === 6) { this.b[sq(5,hr)] = this.b[sq(7,hr)]; this.b[sq(7,hr)] = null; }
        else { this.b[sq(3,hr)] = this.b[sq(0,hr)]; this.b[sq(0,hr)] = null; }
      } else if (["q","r","b","n"].includes(flag)) this.b[to] = colorOf(piece)===WHITE ? flag.toUpperCase() : flag;
      if (letterOf(piece) === "k") {
        if (colorOf(piece)===WHITE) { this.castling.K=false; this.castling.Q=false; }
        else { this.castling.k=false; this.castling.q=false; }
      }
      if (letterOf(piece) === "r") {
        if (frm === 0) this.castling.Q=false; else if (frm === 7) this.castling.K=false;
        else if (frm === 56) this.castling.q=false; else if (frm === 63) this.castling.k=false;
      }
      if (to === 0) this.castling.Q=false; else if (to === 7) this.castling.K=false;
      else if (to === 56) this.castling.q=false; else if (to === 63) this.castling.k=false;
      this.ep = null;
      if (letterOf(piece) === "p" && Math.abs(rankOf(to)-rankOf(frm)) === 2)
        this.ep = sq(fileOf(frm), (rankOf(frm)+rankOf(to))/2);
      this.turn = 1 - this.turn;
    }
    legal() {
      const out = []; const color = this.turn;
      for (const mv of this.pseudo()) { const c = this.clone(); c.make(mv); if (!c.inCheck(color)) out.push(mv); }
      return out;
    }
    gameOver() {
      const moves = this.legal();
      if (!moves.length) return this.inCheck(this.turn) ? "checkmate" : "stalemate";
      const pieces = this.b.filter(Boolean);
      const nonK = pieces.filter(p => letterOf(p) !== "k");
      if (!nonK.length) return "draw";
      if (nonK.length === 1 && ["n","b"].includes(letterOf(nonK[0]))) return "draw";
      if (nonK.length === 2 && nonK.every(p => letterOf(p) === "b")) return "draw";
      return null;
    }
  }

  function evalBoard(board) {
    let total = 0;
    for (const p of board.b) if (p && letterOf(p) !== "k") total += VAL[letterOf(p)];
    const endgame = total < 1300;
    let score = 0;
    for (let s = 0; s < 64; s++) {
      const p = board.b[s]; if (!p) continue;
      const c = colorOf(p), l = letterOf(p);
      const table = l === "k" ? (endgame ? PST.ke : PST.k) : PST[l];
      const idx = c === WHITE ? s : mirror(s);
      const v = VAL[l] + table[idx];
      score += c === WHITE ? v : -v;
    }
    return score;
  }

  function orderMoves(board, moves) {
    return moves.map(mv => {
      const [frm, to, flag] = mv; let s = 0;
      const victim = board.b[to];
      if (victim) s += 10 * VAL[letterOf(victim)] - VAL[letterOf(board.b[frm])];
      if (["q","r","b","n"].includes(flag)) s += 800;
      if (flag === "ep") s += 100;
      if (board.isAttacked(to, 1 - board.turn)) s -= Math.floor(VAL[letterOf(board.b[frm])] / 2);
      return { mv, s: -s };
    }).sort((a, b) => a.s - b.s).map(o => o.mv);
  }

  function minimax(board, depth, alpha, beta, maximizing) {
    const status = board.gameOver();
    if (status === "checkmate") return maximizing ? -100000 : 100000;
    if (status) return 0;
    if (depth === 0) return evalBoard(board);
    const moves = orderMoves(board, board.legal());
    if (maximizing) {
      let best = -1e9;
      for (const mv of moves) { const c = board.clone(); c.make(mv); best = Math.max(best, minimax(c, depth-1, alpha, beta, false)); alpha = Math.max(alpha, best); if (beta <= alpha) break; }
      return best;
    } else {
      let best = 1e9;
      for (const mv of moves) { const c = board.clone(); c.make(mv); best = Math.min(best, minimax(c, depth-1, alpha, beta, true)); beta = Math.min(beta, best); if (beta <= alpha) break; }
      return best;
    }
  }

  function bestMove(fen, depth) {
    const board = new Board(fen);
    if (board.gameOver()) return null;
    const moves = orderMoves(board, board.legal());
    if (!moves.length) return null;
    const maximizing = board.turn === WHITE;
    let best = null, bestVal = maximizing ? -1e9 : 1e9, alpha = -1e9, beta = 1e9;
    for (const mv of moves) {
      const c = board.clone(); c.make(mv);
      const val = minimax(c, depth-1, alpha, beta, !maximizing);
      if (maximizing && val > bestVal) { bestVal = val; best = mv; alpha = Math.max(alpha, val); }
      else if (!maximizing && val < bestVal) { bestVal = val; best = mv; beta = Math.min(beta, val); }
    }
    return best;
  }

  const sqName = (s) => `${String.fromCharCode(97 + fileOf(s))}${rankOf(s)+1}`;
  function toUci(mv) { const [frm, to, flag] = mv; return sqName(frm) + sqName(to) + (["q","r","b","n"].includes(flag) ? flag : ""); }

  window.ChesaEngine = { Board, bestMove, toUci, sqName, WHITE, BLACK };
})();
