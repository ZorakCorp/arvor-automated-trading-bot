"""
Hyperliquid ETH trading bot — live 5m chart screenshot → OpenAI vision → trade.

Live trading is on by default. Set LIVE_TRADING=false for paper-only testing.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from config import (
    CANDLE_CLOSE_BUFFER_SECONDS,
    TIMEFRAME_MINUTES,
    ensure_data_dirs,
    load_settings,
    next_candle_scan_utc,
    seconds_until_next_candle_scan,
)
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


def _sleep_interruptible(seconds: float) -> None:
    """Sleep in 1s slices so SIGINT/SIGTERM can stop promptly."""
    end = time.time() + seconds
    while _running and time.time() < end:
        time.sleep(min(1.0, end - time.time()))


def _wait_for_next_candle_scan(log: logging.Logger, label: str) -> None:
    wait = seconds_until_next_candle_scan()
    next_scan = next_candle_scan_utc()
    log.info(
        "%s — next scan at %s UTC (in %.0fs, %dm candle + %ds buffer)",
        label,
        next_scan.strftime("%Y-%m-%d %H:%M:%S"),
        wait,
        TIMEFRAME_MINUTES,
        CANDLE_CLOSE_BUFFER_SECONDS,
    )
    _sleep_interruptible(wait)


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
    log.info("Hyperliquid ETH Bot starting — mode: %s | signals: AI vision", mode_label)
    if settings.live_trading:
        log.warning("LIVE TRADING ENABLED — real funds at risk")
    else:
        log.info("Paper mode (LIVE_TRADING=false)")
    log.info(
        "Chart scan every %dm on UTC candle close (%s) → OpenAI (%s) → Hyperliquid",
        TIMEFRAME_MINUTES,
        settings.chart_source,
        settings.openai_model,
    )
    log.info("Multi-TF trend alignment: ON (5m + 15m + 1h enforced in Python)")
    if settings.nestal_gates:
        log.info("Nestal code gates: ON (fidelity/confidence also enforced in Python)")
    else:
        log.info("Nestal fidelity/confidence gates: OFF — AI sets levels; alignment still required")
    log.info("=" * 60)

    client = HyperliquidClient(settings)
    risk = RiskManager()
    cooldown = CooldownManager()
    journal = TradeJournal()
    executor = TradeExecutor(settings, client, risk, cooldown, journal)

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

    _wait_for_next_candle_scan(log, "Startup")

    cycle_num = 0
    while _running:
        cycle_num += 1
        log.info("Cycle %d — running...", cycle_num)
        try:
            executor.run_cycle()
        except Exception as exc:
            log.exception("Unhandled error in cycle: %s", exc)

        if not _running:
            break

        _wait_for_next_candle_scan(log, f"Cycle {cycle_num} complete")

    log.info("Bot stopped.")


if __name__ == "__main__":
    main()
