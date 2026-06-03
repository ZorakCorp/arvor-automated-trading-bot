"""Validate AI signals and execute trades on Hyperliquid."""

from __future__ import annotations

import logging
from typing import Any

from ai_analyzer import TradeSignal, analyze_chart, log_signal_decision
from config import LEVERAGE, Settings
from cooldown import CooldownManager
from hyperliquid_client import HyperliquidClient
from risk_manager import RiskManager
from screenshot import capture_chart_screenshot, is_ai_blocked_page_reasoning
from security_utils import redact_for_log
from trade_journal import TradeJournal

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Screenshot → OpenAI vision → risk checks → Hyperliquid orders."""

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
        """Monitor open position, or capture chart and trade on AI decision."""
        mode = "live" if self.settings.live_trading else "paper"

        close_info = None
        if self.settings.is_paper:
            close_info = self.client.monitor_and_close_paper()
        else:
            close_info = self.client.monitor_live_position_close()

        if close_info:
            self._handle_position_closed(close_info, mode)
            return

        try:
            account = self.client.get_account()
        except Exception as exc:
            logger.error("Cannot read account — skipping cycle: %s", exc)
            return

        self.risk.sync_balance(account.balance_usd)

        if (
            not self.settings.is_paper
            and account.available_usd < 0.01
            and self.client.transfer_spot_to_perps_if_needed()
        ):
            account = self.client.get_account()
            self.risk.sync_balance(account.balance_usd)

        blocked, block_reason = self.client.blocks_new_trade()
        if blocked:
            logger.info("No new trades — %s (monitoring only)", block_reason)
            return

        if self.cooldown.is_active():
            logger.info(self.cooldown.reason())
            return

        allowed, reason = self.risk.can_trade(account.balance_usd)
        if not allowed:
            logger.warning("Trading blocked: %s", reason)
            return

        self._attempt_new_trade_from_screenshot(account.available_usd, mode)

    def _handle_position_closed(self, close_info: dict[str, Any], mode: str) -> None:
        outcome = close_info.get("outcome", "unknown")
        pnl = close_info.get("pnl", 0.0)
        balance = close_info.get("balance_after", 0.0)

        self.risk.record_outcome(outcome)
        self.risk.sync_balance(float(balance))
        self.cooldown.start_cooldown()
        self.journal.log_entry(
            {
                "action": "CLOSE",
                "outcome": outcome,
                "pnl": round(pnl, 2),
                "balance_after": round(balance, 2),
                "leverage": LEVERAGE,
                "mode": mode,
                "notes": f"Position closed ({outcome}) side={close_info.get('side', '')}",
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

    def _attempt_new_trade_from_screenshot(self, available_balance: float, mode: str) -> None:
        screenshot_path = capture_chart_screenshot(self.settings, self.client)
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

        if is_ai_blocked_page_reasoning(signal.reasoning):
            logger.error("Screenshot blocked or chart did not load — no trade")
            self.journal.log_no_trade(
                "NO_TRADE",
                str(screenshot_path),
                signal.raw_response,
                mode,
                reasoning=signal.reasoning,
                notes="Chart page blocked or login required",
            )
            self.risk.record_outcome("no_trade")
            return

        log_signal_decision(signal)

        if signal.action == "NO_TRADE":
            self.journal.log_no_trade(
                "NO_TRADE",
                str(screenshot_path),
                signal.raw_response,
                mode,
                reasoning=signal.reasoning,
            )
            self.risk.record_outcome("no_trade")
            return

        self._execute_signal(
            signal,
            source_label="openai_vision",
            screenshot_path=str(screenshot_path),
            available_balance=available_balance,
            mode=mode,
        )

    def _execute_signal(
        self,
        signal: TradeSignal,
        source_label: str,
        screenshot_path: str,
        available_balance: float,
        mode: str,
    ) -> tuple[bool, str]:
        """Shared execution path. Returns (success, message)."""
        if signal.action == "NO_TRADE":
            self.journal.log_no_trade(
                "NO_TRADE",
                screenshot_path,
                signal.raw_response,
                mode,
                reasoning=signal.reasoning,
                notes=f"source={source_label}",
            )
            self.risk.record_outcome("no_trade")
            return True, "no_trade"

        if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
            logger.error("Missing SL/TP — no trade")
            return False, "missing_prices"

        size_result = self.risk.calculate_position_size(
            available_balance,
            signal.entry,
            signal.stop_loss,
            self.client.round_size,
        )
        if size_result is None:
            logger.error("Position size calculation failed — no trade")
            return False, "position_size_failed"

        logger.info(
            "Executing %s | size=%.4f ETH | entry=%.2f sl=%.2f tp=%.2f | source=%s",
            signal.action,
            size_result.size_eth,
            signal.entry,
            signal.stop_loss,
            signal.take_profit,
            source_label,
        )
        if signal.confidence is not None:
            logger.info("Nestal confidence: %.0f%%", signal.confidence)

        try:
            self.client.set_leverage()
            self.client.open_position(
                side=signal.action,
                size=size_result.size_eth,
                entry_price=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                use_limit=True,
            )
        except Exception as exc:
            safe = redact_for_log(str(exc))
            logger.error("Order failed: %s", safe)
            self.journal.log_entry(
                {
                    "action": signal.action,
                    "entry": signal.entry,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "position_size": size_result.size_eth,
                    "leverage": LEVERAGE,
                    "outcome": "error",
                    "screenshot_path": screenshot_path,
                    "ai_reasoning": signal.reasoning,
                    "ai_raw_response": signal.raw_response,
                    "mode": mode,
                    "notes": f"{source_label}: {safe}",
                }
            )
            return False, "order_failed"

        account = self.client.get_account()
        self.journal.log_entry(
            {
                "action": signal.action,
                "entry": signal.entry,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "position_size": size_result.size_eth,
                "leverage": LEVERAGE,
                "outcome": "open",
                "balance_after": round(account.balance_usd, 2),
                "screenshot_path": screenshot_path,
                "ai_reasoning": signal.reasoning,
                "ai_raw_response": signal.raw_response,
                "mode": mode,
                "notes": "paper_open" if self.settings.is_paper else f"orders_accepted ({source_label})",
            }
        )
        logger.info("Position opened successfully")
        return True, "opened"
