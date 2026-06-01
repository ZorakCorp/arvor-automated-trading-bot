"""Position sizing and daily/weekly/monthly loss limits."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from config import (
    DAILY_MAX_LOSS_FRACTION,
    LEVERAGE,
    MONTHLY_MAX_LOSS_FRACTION,
    RISK_FRACTION,
    RISK_STATE_PATH,
    WEEKLY_MAX_LOSS_FRACTION,
)
from security_utils import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Persisted risk tracking across restarts."""

    day_start_balance: float
    week_start_balance: float
    month_start_balance: float
    day_key: str
    week_key: str
    month_key: str
    wins: int = 0
    losses: int = 0
    no_trades: int = 0


@dataclass
class PositionSizeResult:
    """Calculated position size."""

    size_eth: float
    risk_usd: float
    notional_usd: float
    margin_required_usd: float


class RiskManager:
    """Enforce loss limits and compute position size."""

    def __init__(self) -> None:
        self._state = self._load_state()

    def _load_state(self) -> RiskState | None:
        raw = load_json_file(RISK_STATE_PATH)
        if not raw:
            return None
        try:
            state = RiskState(
                day_start_balance=float(raw["day_start_balance"]),
                week_start_balance=float(raw["week_start_balance"]),
                month_start_balance=float(raw["month_start_balance"]),
                day_key=str(raw["day_key"]),
                week_key=str(raw["week_key"]),
                month_key=str(raw["month_key"]),
                wins=int(raw.get("wins", 0)),
                losses=int(raw.get("losses", 0)),
                no_trades=int(raw.get("no_trades", 0)),
            )
            return state
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Could not load risk state: %s", exc)
            return None

    def _save_state(self) -> None:
        if self._state is None:
            return
        atomic_write_json(RISK_STATE_PATH, asdict(self._state))

    def _period_keys(self) -> tuple[str, str, str]:
        now = datetime.now(timezone.utc)
        day_key = now.strftime("%Y-%m-%d")
        iso = now.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        month_key = now.strftime("%Y-%m")
        return day_key, week_key, month_key

    def sync_balance(self, balance: float) -> None:
        """Reset period baselines when day/week/month rolls over."""
        if balance < 0:
            balance = 0.0

        day_key, week_key, month_key = self._period_keys()

        if self._state is None:
            self._state = RiskState(
                day_start_balance=balance,
                week_start_balance=balance,
                month_start_balance=balance,
                day_key=day_key,
                week_key=week_key,
                month_key=month_key,
            )
            self._save_state()
            return

        if self._state.day_key != day_key:
            self._state.day_start_balance = balance
            self._state.day_key = day_key
            logger.info("New trading day — daily loss baseline reset to %.2f", balance)

        if self._state.week_key != week_key:
            self._state.week_start_balance = balance
            self._state.week_key = week_key
            logger.info("New trading week — weekly loss baseline reset to %.2f", balance)

        if self._state.month_key != month_key:
            self._state.month_start_balance = balance
            self._state.month_key = month_key
            logger.info("New trading month — monthly loss baseline reset to %.2f", balance)

        self._save_state()

    def can_trade(self, current_balance: float) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        if current_balance <= 0:
            return (
                False,
                "No perps balance — transfer USDC Spot→Perps on Hyperliquid (bot trades ETH perps only)",
            )

        if self._state is None:
            self.sync_balance(current_balance)
            return True, ""

        day_loss = (self._state.day_start_balance - current_balance) / max(
            self._state.day_start_balance, 1e-9
        )
        week_loss = (self._state.week_start_balance - current_balance) / max(
            self._state.week_start_balance, 1e-9
        )
        month_loss = (self._state.month_start_balance - current_balance) / max(
            self._state.month_start_balance, 1e-9
        )

        if day_loss >= DAILY_MAX_LOSS_FRACTION:
            return False, f"Daily max loss reached ({day_loss:.1%})"
        if week_loss >= WEEKLY_MAX_LOSS_FRACTION:
            return False, f"Weekly max loss reached ({week_loss:.1%})"
        if month_loss >= MONTHLY_MAX_LOSS_FRACTION:
            return False, f"Monthly max loss reached ({month_loss:.1%})"

        return True, ""

    def calculate_position_size(
        self,
        available_balance: float,
        entry: float,
        stop_loss: float,
        round_fn,
    ) -> PositionSizeResult | None:
        """
        Risk fraction of available capital to stop distance.
        size = risk_usd / |entry - stop_loss|, capped by margin at leverage.
        """
        if available_balance <= 0:
            logger.error("No available balance for sizing")
            return None

        risk_usd = available_balance * RISK_FRACTION
        stop_distance = abs(entry - stop_loss)
        if stop_distance < 1e-6:
            logger.error("Stop loss too close to entry")
            return None

        size_eth = risk_usd / stop_distance
        notional = size_eth * entry
        margin_required = notional / LEVERAGE

        if margin_required > available_balance:
            scale = available_balance / margin_required
            size_eth *= scale
            notional = size_eth * entry
            margin_required = notional / LEVERAGE
            logger.warning(
                "Position capped by margin: size=%.4f margin=%.2f",
                size_eth,
                margin_required,
            )

        size_eth = round_fn(size_eth)
        if size_eth <= 0:
            logger.error("Computed position size is zero")
            return None

        return PositionSizeResult(
            size_eth=size_eth,
            risk_usd=risk_usd,
            notional_usd=size_eth * entry,
            margin_required_usd=margin_required,
        )

    def record_outcome(self, outcome: str) -> None:
        """Track win/loss/no_trade for win rate."""
        if self._state is None:
            return
        if outcome == "win":
            self._state.wins += 1
        elif outcome == "loss":
            self._state.losses += 1
        elif outcome == "no_trade":
            self._state.no_trades += 1
        self._save_state()

    @property
    def win_rate(self) -> float:
        if self._state is None:
            return 0.0
        total = self._state.wins + self._state.losses
        if total == 0:
            return 0.0
        return self._state.wins / total

    @property
    def stats(self) -> dict:
        if self._state is None:
            return {"wins": 0, "losses": 0, "no_trades": 0, "win_rate": 0.0}
        return {
            "wins": self._state.wins,
            "losses": self._state.losses,
            "no_trades": self._state.no_trades,
            "win_rate": round(self.win_rate * 100, 2),
        }
