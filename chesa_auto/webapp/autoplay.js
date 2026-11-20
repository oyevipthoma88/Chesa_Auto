/*
 * Chesa Auto — autoplay controller.
 *
 * Reads the live board, runs the engine, applies the chosen move on the
 * real GameFactoryBot webapp via human-style pointer events, and loops
 * until the game ends.
 */
(function () {
  "use strict";

  const EG = window.ChesaEngine;
  const GLYPH = { P:"♙",R:"♖",N:"♘",B:"♗",Q:"♕",K:"♔",p:"♟",r:"♜",n:"♞",b:"♝",q:"♛",k:"♚" };

  const elBoard = document.getElementById("board");
  const elStatus = document.getElementById("status");
  const elLog = document.getElementById("moveLog");
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const depthSel = document.getElementById("depthSel");

  let board = new EG.Board();
  let myColor = EG.WHITE;
  let running = false;
  let lastMove = null;

  function logMove(text, mine) {
    const line = document.createElement("div");
    line.className = mine ? "me" : "opp";
    line.textContent = text;
    elLog.appendChild(line);
    elLog.scrollTop = elLog.scrollHeight;
  }

  function render() {
    elBoard.innerHTML = "";
    for (let r = 7; r >= 0; r--) {
      for (let f = 0; f < 8; f++) {
        const idx = r * 8 + f;
        const cell = document.createElement("div");
        cell.className = "cell " + ((r + f) % 2 === 0 ? "light" : "dark");
        cell.dataset.sq = idx;
        const p = board.b[idx];
        if (p) cell.textContent = GLYPH[p] || "";
        if (lastMove && (lastMove[0] === idx || lastMove[1] === idx)) cell.classList.add("last");
        elBoard.appendChild(cell);
      }
    }
  }

  function setStatus(t) { elStatus.textContent = t; }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async function applyMove(mv) {
    board.make(mv); lastMove = mv; render();
    const uci = EG.toUci(mv);
    const justMoved = (board.turn !== myColor);
    logMove(`${justMoved ? "You" : "Opp"}: ${uci}`, justMoved);
  }

  function readLiveFen() {
    try {
      const frame = document.querySelector("iframe.game-frame, iframe#game_frame");
      if (frame && frame.contentDocument) return readFenFromDom(frame.contentDocument);
    } catch (e) {}
    return null;
  }

  function readFenFromDom(doc) {
    const squares = doc.querySelectorAll(".square, .cell, [data-square]");
    if (squares.length !== 64) return null;
    const b = Array(64).fill(null);
    squares.forEach((el, i) => {
      const name = el.getAttribute("data-piece") || el.dataset.piece || el.textContent.trim();
      if (name && /[prnbqkPRNBQK]/.test(name)) b[i] = name[0];
    });
    return boardToFen(b, board.turn);
  }

  function boardToFen(b, turn) {
    const rows = [];
    for (let r = 7; r >= 0; r--) {
      let row = "", empty = 0;
      for (let f = 0; f < 8; f++) {
        const p = b[r * 8 + f];
        if (!p) { empty++; }
        else { if (empty) { row += empty; empty = 0; } row += p; }
      }
      if (empty) row += empty;
      rows.push(row);
    }
    return rows.join("/") + " " + (turn === EG.WHITE ? "w" : "b") + " KQkq - 0 1";
  }

  async function autoplayLoop() {
    while (running) {
      const status = board.gameOver();
      if (status) {
        const won = (status === "checkmate" && board.turn !== myColor);
        const drew = status !== "checkmate";
        setStatus(status === "checkmate" ? (won ? "You won! 🏆" : "Engine lost this round.") : "Draw — game over.");
        reportResult(status, won, drew);
        running = false; startBtn.disabled = false; stopBtn.disabled = true;
        break;
      }
      if (board.turn === myColor) {
        setStatus("Thinking…");
        await sleep(120);
        const depth = parseInt(depthSel.value, 10) || 3;
        const mv = EG.bestMove(boardToFen(board.b, board.turn), depth);
        if (mv) { await applyMove(mv); dispatchMove(mv); }
        await sleep(350);
      } else {
        setStatus("Waiting for opponent…");
        const live = readLiveFen();
        if (live) { const opp = detectOpponentMove(board, live); if (opp) await applyMove(opp); }
        await sleep(700);
      }
    }
  }

  function dispatchMove(mv) {
    const [frm, to] = mv;
    try {
      const frame = document.querySelector("iframe.game-frame, iframe#game_frame");
      const doc = frame && frame.contentDocument ? frame.contentDocument : document;
      const fromEl = doc.querySelector(`[data-square="${frm}"], .square[data-sq="${frm}"]`);
      const toEl = doc.querySelector(`[data-square="${to}"], .square[data-sq="${to}"]`);
      if (fromEl && toEl) {
        ["pointerdown", "pointerup"].forEach((type) => {
          fromEl.dispatchEvent(new PointerEvent(type, { bubbles: true }));
          toEl.dispatchEvent(new PointerEvent(type, { bubbles: true }));
        });
      }
    } catch (e) {}
  }

  function detectOpponentMove(before, afterFen) {
    const after = new EG.Board(afterFen);
    for (let s = 0; s < 64; s++) {
      if (before.b[s] && !after.b[s]) {
        for (const mv of before.legal()) {
          if (mv[0] === s) { const c = before.clone(); c.make(mv); if (c.b.every((v, i) => v === after.b[i])) return mv; }
        }
      }
    }
    return null;
  }

  function reportResult(status, won, drew) {
    const result = won ? "wins" : drew ? "draws" : "losses";
    logMove(`Result: ${result} (${status})`, won);
    try { const tg = window.Telegram && window.Telegram.WebApp; if (tg) tg.sendData && tg.sendData(JSON.stringify({ result, status })); } catch (e) {}
  }

  function start() {
    if (running) return;
    running = true; startBtn.disabled = true; stopBtn.disabled = false;
    setStatus(myColor === EG.WHITE ? "You play White — thinking…" : "Waiting for opponent…");
    autoplayLoop();
  }
  function stop() { running = false; startBtn.disabled = false; stopBtn.disabled = true; setStatus("Stopped."); }

  render();
  try { const tg = window.Telegram && window.Telegram.WebApp; if (tg) { tg.ready(); tg.expand(); } } catch (e) {}
  setStatus("Ready. Tap Start Auto-Play. You play White by default.");
  startBtn.addEventListener("click", start);
  stopBtn.addEventListener("click", stop);

  elBoard.addEventListener("click", (e) => {
    if (running) return;
    const cell = e.target.closest(".cell"); if (!cell) return;
    const idx = +cell.dataset.sq; const p = board.b[idx];
    if (p) { myColor = (p === p.toUpperCase()) ? EG.WHITE : EG.BLACK; setStatus(`Playing as ${myColor===EG.WHITE?"White":"Black"}.`); }
  });
})();
