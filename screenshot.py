"""Capture live ETH 5m chart screenshots via Playwright."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from config import SCREENSHOTS_DIR, Settings

logger = logging.getLogger(__name__)

_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_CHART_SELECTORS = (
    "div.chart-container",
    "div.chart-markup-table",
    "canvas",
    "#tv_chart_container",
    "[class*='chart-container']",
    "[class*='chart']",
)

_BLOCKED_PAGE_MARKERS = (
    "403 forbidden",
    "error 403",
    "access denied",
    "request blocked",
    "cloudflare",
    "just a moment",
    "enable javascript",
    "captcha",
    "sign in to continue",
    "log in to continue",
    "you need to log in",
)

_AI_BLOCKED_REASONING_MARKERS = (
    "403",
    "forbidden",
    "error page",
    "not a chart",
    "instead of a chart",
    "no chart data",
    "cloudflare",
    "access denied",
    "login page",
    "sign in",
)


def is_blocked_page_text(text: str) -> bool:
    """True if page body/title looks like an error or auth wall, not a chart."""
    lowered = text.lower()
    return any(marker in lowered for marker in _BLOCKED_PAGE_MARKERS)


def is_ai_blocked_page_reasoning(reasoning: str) -> bool:
    """True if vision model reports it saw an error/login page instead of a chart."""
    if not reasoning:
        return False
    lowered = reasoning.lower()
    return any(marker in lowered for marker in _AI_BLOCKED_REASONING_MARKERS)


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
    """Collapse panels so the chart uses more of the viewport."""
    try:
        page.keyboard.press("Alt+Shift+D")
    except Exception:
        pass
    try:
        page.keyboard.press("Control+B")
    except Exception:
        pass


def _page_looks_blocked(page) -> tuple[bool, str]:
    """Inspect title, URL, and body for auth walls / 403 / bot challenges."""
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""

    if "403" in title.lower() or "forbidden" in title.lower():
        return True, f"page title: {title!r}"

    if "/accounts/signin" in url or "/accounts/login" in url:
        return True, "redirected to login page"

    try:
        body_snippet = page.evaluate(
            "() => (document.body && document.body.innerText || '').slice(0, 4000)"
        )
    except Exception:
        body_snippet = ""

    combined = f"{title}\n{body_snippet}"
    if is_blocked_page_text(combined):
        return True, "page content matches blocked/error markers"

    return False, ""


def _chart_widget_visible(page) -> bool:
    """True if a chart canvas/container with reasonable size is on screen."""
    for selector in _CHART_SELECTORS:
        try:
            loc = page.locator(selector).first
            if not loc.is_visible(timeout=5000):
                continue
            box = loc.bounding_box()
            if box and box["width"] > 400 and box["height"] > 300:
                return True
        except Exception:
            continue
    return False


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


def _log_chart_access_help() -> None:
    logger.error(
        "Chart did not load (403/login/bot block). Fixes:\n"
        "  1. Open CHART_URL in a private/incognito window — ETH 5m must be visible.\n"
        "  2. TradingView: Share → enable public link.\n"
        "  3. Set CHART_STORAGE_STATE_PATH=/app/data/chart_auth.json from a logged-in "
        "Playwright session (playwright codegen --save-storage=chart_auth.json <CHART_URL>).\n"
        "  4. Increase SCREENSHOT_WAIT_MS (e.g. 30000) if the chart loads slowly."
    )


def capture_chart_screenshot(settings: Settings) -> Path | None:
    """
    Open CHART_URL and save a PNG of the live ETH 5m chart.
    Returns path on success, None on failure.
    """
    url = settings.chart_url

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
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context_kwargs: dict = {
                "viewport": {"width": 1920, "height": 1080},
                "device_scale_factor": 2,
                "user_agent": _CHROME_USER_AGENT,
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "extra_http_headers": {
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                },
            }
            storage_path = settings.chart_storage_state_path
            if storage_path:
                logger.info("Using chart session: %s", storage_path)
                context_kwargs["storage_state"] = str(storage_path)

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(90_000)

            logger.info("Loading chart: %s", url)
            response = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if response is not None and response.status >= 400:
                logger.error("Chart HTTP %s for %s", response.status, response.url)
                browser.close()
                _log_chart_access_help()
                return None

            page.wait_for_timeout(min(wait_ms, 12_000))
            _dismiss_overlays(page)

            remaining = max(0, wait_ms - 12_000)
            if remaining:
                page.wait_for_timeout(remaining)

            blocked, reason = _page_looks_blocked(page)
            if blocked:
                logger.error("Chart page blocked before screenshot: %s", reason)
                debug_path = SCREENSHOTS_DIR / f"eth_5m_{timestamp}_blocked.png"
                try:
                    page.screenshot(path=str(debug_path), full_page=False)
                    logger.info("Debug screenshot saved: %s", debug_path)
                except Exception:
                    pass
                browser.close()
                _log_chart_access_help()
                return None

            if not _chart_widget_visible(page):
                logger.warning(
                    "No chart canvas detected — page may be login-gated or still loading"
                )
                blocked2, reason2 = _page_looks_blocked(page)
                if blocked2:
                    logger.error("Chart blocked: %s", reason2)
                    browser.close()
                    _log_chart_access_help()
                    return None

            _hide_side_toolbars(page)
            page.wait_for_timeout(2_000)

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
