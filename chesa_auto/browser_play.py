import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from chesa_auto.logger import log

READER_PATH = Path(__file__).parent / "board_reader.js"
GRID_CENTERS = [22, 66, 111, 156, 201, 245, 290, 335]


class GameFactoryBrowser:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._reader_script: str = ""
        self._current_url: Optional[str] = None
        self._load_reader()

    def _load_reader(self):
        if READER_PATH.exists():
            with open(READER_PATH, "r", encoding="utf-8") as f:
                self._reader_script = f.read()
        else:
            log.error(f"Board reader script not found at {READER_PATH}")

    async def start(self):
        if self._browser:
            return
        self._playwright = await async_playwright().start()
        # Launch Chromium with args suitable for Heroku/Docker
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--single-process",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 400, "height": 700},
            user_agent="Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        )
        self._page = await self._context.new_page()
        log.info("Playwright Chromium browser started successfully")

    async def open_url(self, url: str) -> bool:
        if not self._page:
            await self.start()
        self._current_url = url
        try:
            log.info(f"Navigating to GameFactory URL...")
            await self._page.goto(url, wait_until="networkidle", timeout=45000)
            await asyncio.sleep(2)
            log.info(f"Page loaded: title='{await self._page.title()}'")
            return True
        except Exception as e:
            log.error(f"Failed to load GameFactory URL: {e}")
            return False

    async def read_state(self) -> Dict[str, Any]:
        if not self._page:
            return {"ok": False, "error": "Browser page not initialized"}

        try:
            state = await self._page.evaluate(self._reader_script)
            # If piece is animating, wait and re-read for stable snapshot
            if state.get("animating"):
                await asyncio.sleep(0.3)
                state = await self._page.evaluate(self._reader_script)
            return state
        except Exception as e:
            log.error(f"Error evaluating board_reader.js: {e}")
            return {"ok": False, "error": str(e)}

    def square_to_coords(self, sq: str, white_at_bottom: bool = True) -> Tuple[float, float]:
        sq = sq.lower().strip()
        f = ord(sq[0]) - ord("a")
        r = int(sq[1]) - 1

        if white_at_bottom:
            col = f
            row = 7 - r
        else:
            col = 7 - f
            row = r

        col = max(0, min(7, col))
        row = max(0, min(7, row))
        return GRID_CENTERS[col], GRID_CENTERS[row]

    async def click_move(self, from_sq: str, to_sq: str, white_at_bottom: bool = True) -> bool:
        if not self._page:
            return False

        try:
            board_el = await self._page.query_selector(".board")
            if not board_el:
                log.error("Board element not found during move click")
                return False

            rect = await board_el.bounding_box()
            if not rect:
                log.error("Could not obtain board bounding box")
                return False

            bx, by = rect["x"], rect["y"]

            x1, y1 = self.square_to_coords(from_sq, white_at_bottom)
            x2, y2 = self.square_to_coords(to_sq, white_at_bottom)

            log.info(f"Clicking move {from_sq}->{to_sq} at ({bx + x1:.1f}, {by + y1:.1f}) -> ({bx + x2:.1f}, {by + y2:.1f})")

            # Click source square
            await self._page.mouse.click(bx + x1, by + y1)
            await asyncio.sleep(0.18)

            # Click target square
            await self._page.mouse.click(bx + x2, by + y2)
            await asyncio.sleep(0.2)

            # Check if promotion dialog appears
            promo = await self._page.query_selector(".boardPromotionPopupPanel")
            if promo and await promo.is_visible():
                log.info("Promotion popup detected, selecting Queen")
                # Click the first piece in promotion popup (Queen)
                promo_queen = await promo.query_selector(".boardPiece, div")
                if promo_queen:
                    await promo_queen.click()
                else:
                    await self._page.mouse.click(bx + x2, by + y2)

            return True
        except Exception as e:
            log.error(f"Failed to click move {from_sq}->{to_sq}: {e}")
            return False

    async def get_screenshot(self) -> Optional[bytes]:
        if not self._page:
            return None
        try:
            return await self._page.screenshot(type="png")
        except Exception as e:
            log.error(f"Screenshot capture failed: {e}")
            return None

    async def start_rematch_or_lobby(self) -> bool:
        """Clicks Rematch, New Game, or Chess Lobby to play another round."""
        if not self._page:
            return False
        try:
            for text in ["Rematch", "New Game", "Play Again", "Play", "Chess Lobby"]:
                btn = await self._page.query_selector(f"button:has-text('{text}'), div:has-text('{text}'), a:has-text('{text}')")
                if btn and await btn.is_visible():
                    log.info(f"Clicking lobby/rematch button: '{text}'*)
                    await btn.click()
                    await asyncio.sleep(2)
                    return True
            return False
        except Exception as e:
            log.warning(f"Failed to join rematch/lobby: {e}")
            return False

    async def close(self):
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            log.error(f"Error closing browser: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            log.info("Browser closed")
