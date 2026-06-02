"""Capture chart images for AI vision — URL (Playwright) or Hyperliquid API fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chart_image import render_hyperliquid_chart_image
from config import SCREENSHOTS_DIR, Settings, is_placeholder_chart_url

if TYPE_CHECKING:
    from hyperliquid_client import HyperliquidClient

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
    lowered = text.lower()
    return any(marker in lowered for marker in _BLOCKED_PAGE_MARKERS)


def is_ai_blocked_page_reasoning(reasoning: str) -> bool:
    if not reasoning:
        return False
    lowered = reasoning.lower()
    return any(marker in lowered for marker in _AI_BLOCKED_REASONING_MARKERS)


def _dismiss_overlays(page) -> None:
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
    try:
        page.keyboard.press("Alt+Shift+D")
    except Exception:
        pass
    try:
        page.keyboard.press("Control+B")
    except Exception:
        pass


def _page_looks_blocked(page) -> tuple[bool, str]:
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

    if is_blocked_page_text(f"{title}\n{body_snippet}"):
        return True, "page content matches blocked/error markers"
    return False, ""


def _chart_widget_visible(page) -> bool:
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


def _capture_url_screenshot(settings: Settings, output_path: Path) -> bool:
    """Playwright screenshot of CHART_URL. Returns True on success."""
    url = settings.chart_url
    wait_ms = max(8000, settings.screenshot_wait_ms)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright not installed")
        return False

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
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            }
            if settings.chart_storage_state_path:
                logger.info("Using chart session: %s", settings.chart_storage_state_path)
                context_kwargs["storage_state"] = str(settings.chart_storage_state_path)

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(90_000)

            logger.info("Loading chart URL: %s", url)
            response = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            if response is not None and response.status >= 400:
                logger.warning("Chart HTTP %s for %s", response.status, response.url)
                browser.close()
                return False

            page.wait_for_timeout(min(wait_ms, 12_000))
            _dismiss_overlays(page)
            remaining = max(0, wait_ms - 12_000)
            if remaining:
                page.wait_for_timeout(remaining)

            blocked, reason = _page_looks_blocked(page)
            if blocked:
                logger.warning("Chart page blocked: %s", reason)
                browser.close()
                return False

            if not _chart_widget_visible(page):
                blocked2, reason2 = _page_looks_blocked(page)
                if blocked2:
                    logger.warning("Chart blocked (no canvas): %s", reason2)
                    browser.close()
                    return False

            _hide_side_toolbars(page)
            page.wait_for_timeout(2_000)
            _screenshot_chart_area(page, output_path)
            browser.close()

        return output_path.exists() and output_path.stat().st_size >= 1000
    except Exception as exc:
        logger.warning("URL screenshot failed: %s", exc)
        return False


def _capture_hyperliquid_api_chart(client: Any, output_path: Path) -> bool:
    """Render 5m / 15m / 1h ETH candles from Hyperliquid (works on Railway)."""
    if client is None:
        logger.error("Hyperliquid client required for API chart render")
        return False
    return render_hyperliquid_chart_image(client, output_path)


def capture_chart_screenshot(
    settings: Settings,
    client: HyperliquidClient | None = None,
) -> Path | None:
    """
    Produce a chart PNG for OpenAI vision.

    CHART_SOURCE:
      - hyperliquid: API-rendered 5m/15m/1h panels only
      - url: Playwright CHART_URL only
      - auto (default): try URL if set, then Hyperliquid API fallback
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = SCREENSHOTS_DIR / f"eth_chart_{timestamp}.png"
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    source = settings.chart_source

    if source == "hyperliquid":
        if _capture_hyperliquid_api_chart(client, output_path):
            return output_path
        return None

    try_url = source == "url" or (
        source == "auto"
        and settings.chart_url
        and not is_placeholder_chart_url(settings.chart_url)
    )

    if try_url:
        if _capture_url_screenshot(settings, output_path):
            logger.info(
                "Screenshot saved (URL): %s (%d bytes)",
                output_path,
                output_path.stat().st_size,
            )
            return output_path
        if source == "url":
            logger.error(
                "CHART_SOURCE=url but screenshot failed — set a public CHART_URL "
                "or use CHART_SOURCE=auto / hyperliquid"
            )
            return None
        logger.warning("URL chart failed — using Hyperliquid API chart (5m/15m/1h)")

    if _capture_hyperliquid_api_chart(client, output_path):
        return output_path

    logger.error(
        "All chart capture methods failed. Set CHART_SOURCE=hyperliquid or fix CHART_URL."
    )
    return None
