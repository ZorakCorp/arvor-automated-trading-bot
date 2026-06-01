"""
Hyperliquid ETH trading bot — Nested Fractal (TradingView) + AI vision.

Default: paper trading. Set LIVE_TRADING=true only when ready for real funds.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from config import LOOP_INTERVAL_SECONDS, ensure_data_dirs, load_settings
from cooldown import CooldownManager
from hyperliquid_client import HyperliquidClient
from risk_manager import RiskManager
from trade_executor import TradeExecutor
from trade_journal import TradeJournal

_running = True


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _handle_shutdown(signum: int, frame) -> None:  # noqa: ARG001
    global _running
    logging.getLogger(__name__).info("Shutdown signal received (%s)", signum)
    _running = False


def main() -> None:
    global _running

    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    ensure_data_dirs()
    _configure_logging(settings.log_level)
    log = logging.getLogger("bot")

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    mode_label = "LIVE" if settings.live_trading else "PAPER"
    log.info("=" * 60)
    log.info("Hyperliquid ETH Bot starting — mode: %s", mode_label)
    if settings.live_trading:
        log.warning("LIVE TRADING ENABLED — real funds at risk")
    else:
        log.info("Paper trading (set LIVE_TRADING=true for live)")
    log.info("=" * 60)

    client = HyperliquidClient(settings)
    risk = RiskManager()
    cooldown = CooldownManager()
    journal = TradeJournal()
    executor = TradeExecutor(settings, client, risk, cooldown, journal)

    # Initial balance sync
    try:
        account = client.get_account()
        risk.sync_balance(account.balance_usd)
        log.info(
            "Perps balance: $%.2f (available $%.2f) | Win rate: %s",
            account.balance_usd,
            account.available_usd,
            risk.stats,
        )
    except Exception as exc:
        log.error("Failed to read account on startup: %s", exc)
        if settings.live_trading:
            sys.exit(1)

    while _running:
        try:
            executor.run_cycle()
        except Exception as exc:
            log.exception("Unhandled error in cycle: %s", exc)

        if not _running:
            break

        log.debug("Sleeping %s seconds until next cycle", LOOP_INTERVAL_SECONDS)
        for _ in range(LOOP_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    log.info("Bot stopped.")


if __name__ == "__main__":
    main()
