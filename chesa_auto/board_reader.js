/**
 * GameFactory Chess DOM reader script.
 * Injected via Playwright page.evaluate() to extract live board position,
 * turn, active players, orientation, and game over status.
 */
(() => {
    const boardEl = document.querySelector(".board");
    if (!boardEl) {
        return { ok: false, error: "Board element .board not found" };
    }

    const boardRect = boardEl.getBoundingClientRect();
    const coords = Array.from(document.querySelectorAll(".boardCoord"));

    // Detect board orientation: check if rank "1" is at the bottom
    let whiteAtBottom = true;
    for (const c of coords) {
        if (c.innerText.trim() === "1") {
            const r = c.getBoundingClientRect();
            whiteAtBottom = r.top > (boardRect.top + boardRect.height / 2);
            break;
        }
    }

    // Grid center positions (GameFactory standard board: 0, 44, 89, 134, 179, 223, 268, 313 + 22px offset)
    const GRID_CENTERS = [22, 66, 111, 156, 201, 245, 290, 335];

    function getSquare(tx, ty) {
        let bestCol = 0, minColDiff = Infinity;
        let bestRow = 0, minRowDiff = Infinity;
        for (let i = 0; i < 8; i++) {
            const dX = Math.abs(tx - GRID_CENTERS[i]);
            if (dX < minColDiff) { minColDiff = dX; bestCol = i; }
            const dY = Math.abs(ty - GRID_CENTERS[i]);
            if (dY < minRowDiff) { minRowDiff = dY; bestRow = i; }
        }

        let f, r;
        if (whiteAtBottom) {
            f = bestCol;
            r = 7 - bestRow;
        } else {
            f = 7 - bestCol;
            r = bestRow;
        }

        return String.fromCharCode(97 + f) + (r + 1);
    }

    // Piece sprite map (background-position horizontal offset)
    const PIECE_TYPES = {
        "0%": "K",
        "20%": "Q",
        "40%": "R",
        "60%": "B",
        "80%": "N",
        "100%": "P"
    };

    const boardMap = {}; // sq -> { color: 'w'|'b', type: 'P'..'K' }
    const pieces = Array.from(document.querySelectorAll(".boardPiece"));
    let animating = false;

    for (const p of pieces) {
        const style = p.getAttribute("style") || "";
        if (p.style.opacity === "0" || style.includes("opacity: 0")) continue;

        const transformMatch = style.match(/matrix\([^,]+,[^,]+,[^,]+,[^,]+,\s*([^,]+),\s*([^)]+)\)/);
        if (!transformMatch) continue;
        const tx = parseFloat(transformMatch[1]);
        const ty = parseFloat(transformMatch[2]);
        if (tx === 0 && ty === 0) continue;

        // Check if piece is in mid-animation
        const nearestXDiff = Math.min(...GRID_CENTERS.map(c => Math.abs(tx - c)));
        const nearestYDiff = Math.min(...GRID_CENTERS.map(c => Math.abs(ty - c)));
        if (nearestXDiff > 12 || nearestYDiff > 12) {
            animating = true;
        }

        const bgMatch = style.match(/background-position:\s*([^;]+)/);
        if (!bgMatch) continue;
        const bg = bgMatch[1].trim().split(/\s+/);
        const posX = bg[0] || "";
        const posY = bg[1] || "";

        const color = posY.includes("100%") ? "b" : "w";
        const ptype = PIECE_TYPES[posX] || "?";

        const sq = getSquare(tx, ty);
        boardMap[sq] = { color, type: ptype, tx, ty };
    }

    // Build FEN board string (ranks 8 down to 1)
    let fenRows = [];
    for (let r = 8; r >= 1; r--) {
        let emptyCount = 0;
        let rowStr = "";
        for (let f = 0; f < 8; f++) {
            const sq = String.fromCharCode(97 + f) + r;
            const p = boardMap[sq];
            if (!p) {
                emptyCount++;
            } else {
                if (emptyCount > 0) {
                    rowStr += emptyCount;
                    emptyCount = 0;
                }
                rowStr += (p.color === "w" ? p.type.toUpperCase() : p.type.toLowerCase());
            }
        }
        if (emptyCount > 0) rowStr += emptyCount;
        fenRows.push(rowStr);
    }
    const boardFen = fenRows.join("/");

    // Players and timers
    const p0 = document.querySelector(".player0");
    const p1 = document.querySelector(".player1");

    // Check game over dialog / status
    const centralPanel = document.querySelector(".central-panel");
    const fullText = (centralPanel ? centralPanel.innerText : "") + " " + document.body.innerText;
    
    let gameOver = false;
    let result = null; // 'win', 'loss', 'draw'
    if (fullText.includes("Checkmate") || fullText.includes("resigned") || fullText.includes("time out") || fullText.includes("Draw") || fullText.includes("stalemate") || fullText.includes("Game Over")) {
        gameOver = true;
        if (fullText.includes("Won") || fullText.includes("Victorious") || fullText.includes("wins")) {
            result = "win";
        } else if (fullText.includes("Lost") || fullText.includes("Checkmate") || fullText.includes("time out")) {
            result = "loss";
        } else if (fullText.includes("Draw") || fullText.includes("½-½") || fullText.includes("stalemate")) {
            result = "draw";
        }
    }

    return {
        ok: true,
        whiteAtBottom,
        animating,
        pieceCount: Object.keys(boardMap).length,
        boardFen,
        boardMap,
        boardRect: {
            x: boardRect.x,
            y: boardRect.y,
            width: boardRect.width,
            height: boardRect.height
        },
        gameOver,
        result,
        p0Text: p0 ? p0.innerText.replace(/\n+/g, " ") : "",
        p1Text: p1 ? p1.innerText.replace(/\n+/g, " ") : ""
    };
})();
