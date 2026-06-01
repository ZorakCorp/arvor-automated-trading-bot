"""Validate signals and execute trades."""

from __future__ import annotations

import logging
from typing import Any

from ai_analyzer import TradeSignal, analyze_chart
from config import COIN, LEVERAGE, Settings
from cooldown import CooldownManager
from hyperliquid_client import HyperliquidClient
from risk_manager import RiskManager
from screenshot import capture_chart_screenshot
from trade_journal import TradeJournal

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Orchestrates screenshot → AI → risk → order flow."""

    def __init__(
        self,
        settings: Settings,
        client: HyperliquidClient,
        risk: RiskManager,
        cooldown: CooldownManager,
        journal: TradeJournal,
    ) -> None:
        self.settings = settings
        self.client = client
        self.risk = risk
        self.cooldown = cooldown
        self.journal = journal

    def run_cycle(self) -> None:
        """Single bot iteration: monitor position or seek new trade."""
        mode = "live" if self.settings.live_trading else "paper"

        # Paper: simulate SL/TP fills
        if self.settings.is_paper:
            close_info = self.client.monitor_and_close_paper()
            if close_info:
                self._handle_position_closed(close_info, mode)
                return

        account = self.client.get_account()
        self.risk.sync_balance(account.balance_usd)

        if self.client.has_open_position():
            logger.info("Position open — monitoring only")
            return

        if self.cooldown.is_active():
            logger.info(self.cooldown.reason())
            return

        allowed, reason = self.risk.can_trade(account.balance_usd)
        if not allowed:
            logger.warning("Trading blocked: %s", reason)
            return

        self._attempt_new_trade(account.balance_usd, mode)

    def _handle_position_closed(self, close_info: dict[str, Any], mode: str) -> None:
        outcome = close_info.get("outcome", "unknown")
        pnl = close_info.get("pnl", 0.0)
        balance = close_info.get("balance_after", 0.0)

        self.risk.record_outcome(outcome)
        self.cooldown.start_cooldown()
        self.journal.log_entry(
            {
                "action": "CLOSE",
                "outcome": outcome,
                "pnl": round(pnl, 2),
                "balance_after": round(balance, 2),
                "leverage": LEVERAGE,
                "mode": mode,
                "notes": f"Position closed ({outcome})",
            }
        )
        stats = self.risk.stats
        logger.info(
            "Trade closed: %s pnl=%.2f | Win rate: %s%% (%sW/%sL)",
            outcome,
            pnl,
            stats["win_rate"],
            stats["wins"],
            stats["losses"],
        )

    def _attempt_new_trade(self, balance: float, mode: str) -> None:
        screenshot_path = capture_chart_screenshot(self.settings)
        if screenshot_path is None:
            logger.error("Screenshot failed — no trade")
            return

        signal = analyze_chart(screenshot_path, self.settings)
        if signal is None:
            logger.error("AI analysis failed or invalid JSON — no trade")
            self.journal.log_no_trade(
                "INVALID",
                str(screenshot_path),
                "",
                mode,
                notes="Invalid AI response",
            )
            self.risk.record_outcome("no_trade")
            return

        if signal.action == "NO_TRADE":
            logger.info("AI signal: NO_TRADE")
            self.journal.log_no_trade(
                "NO_TRADE",
                str(screenshot_path),
                signal.raw_response,
                mode,
            )
            self.risk.record_outcome("no_trade")
            return

        if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
            logger.error("Missing SL/TP — no trade")
            return

        size_result = self.risk.calculate_position_size(
            balance,
            signal.entry,
            signal.stop_loss,
            self.client.round_size,
        )
        if size_result is None:
            logger.error("Position size calculation failed — no trade")
            return

        logger.info(
            "Executing %s | size=%.4f ETH | entry=%.2f sl=%.2f tp=%.2f",
            signal.action,
            size_result.size_eth,
            signal.entry,
            signal.stop_loss,
            signal.take_profit,
        )

        try:
            self.client.set_leverage()
            result = self.client.open_position(
                side=signal.action,
                size=size_result.size_eth,
                entry_price=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                use_limit=False,
            )
        except Exception as exc:
            logger.error("Order failed — stopping cycle: %s", exc)
            self.journal.log_entry(
                {
                    "action": signal.action,
                    "entry": signal.entry,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "position_size": size_result.size_eth,
                    "leverage": LEVERAGE,
                    "outcome": "error",
                    "screenshot_path": str(screenshot_path),
                    "ai_raw_response": signal.raw_response,
                    "mode": mode,
                    "notes": str(exc),
                }
            )
            return

        account_after = self.client.get_account()
        self.journal.log_entry(
            {
                "action": signal.action,
                "entry": signal.entry,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "position_size": size_result.size_eth,
                "leverage": LEVERAGE,
                "outcome": "open",
                "balance_after": round(account_after.balance_usd, 2),
                "screenshot_path": str(screenshot_path),
                "ai_raw_response": signal.raw_response,
                "mode": mode,
                "notes": str(result),
            }
        )
        logger.info("Position opened successfully")
