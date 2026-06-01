"""Capture TradingView chart screenshots via Playwright."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from config import SCREENSHOTS_DIR, Settings

logger = logging.getLogger(__name__)

# TradingView UI selectors (best-effort; site layout can change)
_CHART_SELECTORS = (
    "div.chart-container",
    "div.chart-markup-table",
    "canvas",
    "#tv_chart_container",
)


def _dismiss_overlays(page) -> None:
    """Close cookie banners and onboarding popups."""
    for selector in (
        "button:has-text('Accept all')",
        "button:has-text('Accept')",
        "button:has-text('Got it')",
        "button:has-text('Not now')",
        "[data-name='close']",
        "[aria-label='Close']",
    ):
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=1500):
                loc.click(timeout=1500)
                page.wait_for_timeout(500)
        except Exception:
            pass


def _hide_side_toolbars(page) -> None:
    """Collapse panels so the chart (and Nested Fractal lines) uses more space."""
    try:
        page.keyboard.press("Alt+Shift+D")  # TradingView: hide drawing toolbar
    except Exception:
        pass
    try:
        page.keyboard.press("Control+B")  # toggle left toolbar (Windows/Linux)
    except Exception:
        pass


def _screenshot_chart_area(page, output_path: Path) -> bool:
    """Try to screenshot only the chart widget; fall back to full viewport."""
    for selector in _CHART_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=3000):
                box = loc.bounding_box()
                if box and box["width"] > 400 and box["height"] > 300:
                    loc.screenshot(path=str(output_path))
                    return True
        except Exception:
            continue
    page.screenshot(path=str(output_path), full_page=False)
    return True


def capture_chart_screenshot(settings: Settings) -> Path | None:
    """
    Open TradingView chart URL and save a PNG screenshot.
    Optimized for "Nested Fractal - Clean" (gold TP, orange SL, signal panel).
    Returns path on success, None on failure.
    """
    url = settings.tradingview_chart_url
    if not url:
        logger.error("TRADINGVIEW_CHART_URL is not set")
        return None

    wait_ms = max(8000, settings.screenshot_wait_ms)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = SCREENSHOTS_DIR / f"eth_5m_{timestamp}.png"
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "playwright not installed. Run: pip install playwright && playwright install chromium"
        )
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=2,  # sharper text for TP:/SL: labels
            )
            page = context.new_page()
            page.set_default_timeout(90_000)

            logger.info("Loading TradingView chart: %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)

            # Initial paint + indicator script execution
            page.wait_for_timeout(min(wait_ms, 12_000))
            _dismiss_overlays(page)

            # Extra time for Nested Fractal boxes/lines (drawn on barstate.islast)
            remaining = max(0, wait_ms - 12_000)
            if remaining:
                page.wait_for_timeout(remaining)

            _hide_side_toolbars(page)
            page.wait_for_timeout(2_000)

            # Reload last bar drawings (scroll trick nudges chart refresh)
            try:
                page.mouse.wheel(0, 100)
                page.wait_for_timeout(500)
                page.mouse.wheel(0, -100)
                page.wait_for_timeout(1500)
            except Exception:
                pass

            _screenshot_chart_area(page, output_path)
            browser.close()

        if not output_path.exists() or output_path.stat().st_size < 1000:
            logger.error("Screenshot file missing or too small: %s", output_path)
            return None

        logger.info("Screenshot saved: %s (%d bytes)", output_path, output_path.stat().st_size)
        return output_path

    except Exception as exc:
        logger.error("Screenshot capture failed: %s", exc)
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return None
