"""Hyperliquid API wrapper with paper-trading simulation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from config import COIN, LEVERAGE, PAPER_STATE_PATH, Settings

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Open position snapshot."""

    coin: str
    side: str  # "LONG" or "SHORT"
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: str


@dataclass
class AccountSnapshot:
    """Account balance and position state."""

    balance_usd: float
    available_usd: float
    position: Position | None


class HyperliquidClient:
    """Live Hyperliquid client or paper simulator."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._exchange: Any = None
        self._info: Any = None
        self._asset_index: int | None = None

        if settings.is_paper:
            self._load_paper_state()
            logger.info("Paper trading mode enabled (balance=%.2f)", self._paper_balance)
        else:
            self._init_live_client()

    # ------------------------------------------------------------------ live
    def _init_live_client(self) -> None:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base_url = (
            constants.TESTNET_API_URL
            if self.settings.hyperliquid_testnet
            else constants.MAINNET_API_URL
        )
        wallet = Account.from_key(self.settings.hyperliquid_private_key)
        self._exchange = Exchange(wallet, base_url)
        self._info = Info(base_url, skip_ws=True)
        self._wallet_address = self.settings.hyperliquid_wallet_address
        logger.info("Live Hyperliquid client initialized (%s)", base_url)

    def _get_asset_index(self) -> int:
        if self._asset_index is not None:
            return self._asset_index
        meta = self._info.meta()
        for i, asset in enumerate(meta["universe"]):
            if asset["name"] == COIN:
                self._asset_index = i
                return i
        raise RuntimeError(f"Asset {COIN} not found on Hyperliquid")

    # ----------------------------------------------------------------- paper
    def _load_paper_state(self) -> None:
        self._paper_balance = self.settings.paper_starting_balance
        self._paper_position: Position | None = None
        if PAPER_STATE_PATH.exists():
            try:
                raw = json.loads(PAPER_STATE_PATH.read_text(encoding="utf-8"))
                self._paper_balance = float(raw.get("balance_usd", self._paper_balance))
                pos = raw.get("position")
                if pos:
                    self._paper_position = Position(**pos)
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Could not load paper state: %s", exc)

    def _save_paper_state(self) -> None:
        payload = {
            "balance_usd": self._paper_balance,
            "position": asdict(self._paper_position) if self._paper_position else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PAPER_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -------------------------------------------------------------- public API
    def get_account(self) -> AccountSnapshot:
        """Return balance and open position."""
        if self.settings.is_paper:
            return AccountSnapshot(
                balance_usd=self._paper_balance,
                available_usd=self._paper_balance,
                position=self._paper_position,
            )

        try:
            state = self._info.user_state(self._wallet_address)
        except Exception as exc:
            logger.error("Failed to fetch account state: %s", exc)
            raise

        margin = state.get("marginSummary", {})
        balance = float(margin.get("accountValue", 0))
        withdrawable = float(state.get("withdrawable", balance))

        position = None
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") != COIN:
                continue
            szi = float(pos.get("szi", 0))
            if abs(szi) < 1e-12:
                continue
            entry = float(pos.get("entryPx", 0))
            side = "LONG" if szi > 0 else "SHORT"
            position = Position(
                coin=COIN,
                side=side,
                size=abs(szi),
                entry_price=entry,
                stop_loss=0.0,
                take_profit=0.0,
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            break

        return AccountSnapshot(
            balance_usd=balance,
            available_usd=withdrawable,
            position=position,
        )

    def get_mid_price(self) -> float:
        """Current ETH mid price."""
        if self.settings.is_paper:
            # Use public info endpoint even in paper mode for realistic pricing
            return self._fetch_public_mid_price()

        try:
            mids = self._info.all_mids()
            return float(mids[COIN])
        except Exception as exc:
            logger.error("Failed to fetch mid price: %s", exc)
            raise

    def _fetch_public_mid_price(self) -> float:
        import requests
        from hyperliquid.utils import constants

        base = (
            constants.TESTNET_API_URL
            if self.settings.hyperliquid_testnet
            else constants.MAINNET_API_URL
        )
        resp = requests.post(
            f"{base}/info",
            json={"type": "allMids"},
            timeout=15,
        )
        resp.raise_for_status()
        mids = resp.json()
        return float(mids[COIN])

    def set_leverage(self) -> None:
        """Set ETH leverage to configured value."""
        if self.settings.is_paper:
            logger.info("Paper mode: leverage set to %sx (simulated)", LEVERAGE)
            return

        try:
            result = self._exchange.update_leverage(LEVERAGE, COIN, is_cross=True)
            logger.info("Leverage update result: %s", result)
            if isinstance(result, dict) and result.get("status") != "ok":
                raise RuntimeError(f"Leverage update failed: {result}")
        except Exception as exc:
            logger.error("Failed to set leverage: %s", exc)
            raise

    def open_position(
        self,
        side: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        use_limit: bool = False,
    ) -> dict[str, Any]:
        """
        Open ETH position with SL/TP.
        Returns order result dict.
        """
        is_buy = side.upper() == "LONG"
        if self.settings.is_paper:
            return self._paper_open(is_buy, size, entry_price, stop_loss, take_profit)

        try:
            if use_limit:
                entry_result = self._exchange.order(
                    COIN,
                    is_buy,
                    size,
                    entry_price,
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=False,
                )
            else:
                entry_result = self._exchange.market_open(
                    COIN,
                    is_buy=is_buy,
                    sz=size,
                    slippage=0.01,
                )

            if not self._order_ok(entry_result):
                raise RuntimeError(f"Entry order failed: {entry_result}")

            # Stop loss trigger (reduce only)
            sl_result = self._place_trigger(is_buy, size, stop_loss, tpsl="sl")
            if not self._order_ok(sl_result):
                raise RuntimeError(f"Stop loss order failed: {sl_result}")

            # Take profit trigger (reduce only)
            tp_result = self._place_trigger(is_buy, size, take_profit, tpsl="tp")
            if not self._order_ok(tp_result):
                raise RuntimeError(f"Take profit order failed: {tp_result}")

            return {
                "entry": entry_result,
                "stop_loss": sl_result,
                "take_profit": tp_result,
            }
        except Exception as exc:
            logger.error("Order placement failed: %s", exc)
            raise

    def _place_trigger(
        self, was_buy: bool, size: float, trigger_px: float, tpsl: str
    ) -> dict:
        """Place reduce-only SL or TP trigger."""
        # Closing a long = sell; closing a short = buy
        is_buy = not was_buy
        return self._exchange.order(
            COIN,
            is_buy,
            size,
            trigger_px,
            {
                "trigger": {
                    "triggerPx": str(trigger_px),
                    "isMarket": True,
                    "tpsl": tpsl,
                }
            },
            reduce_only=True,
        )

    @staticmethod
    def _order_ok(result: dict) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("status") == "ok":
            return True
        return False

    def _paper_open(
        self,
        is_buy: bool,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> dict[str, Any]:
        if self._paper_position is not None:
            raise RuntimeError("Paper position already open")

        side = "LONG" if is_buy else "SHORT"
        self._paper_position = Position(
            coin=COIN,
            side=side,
            size=size,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save_paper_state()
        logger.info(
            "Paper %s opened: size=%.4f entry=%.2f sl=%.2f tp=%.2f",
            side,
            size,
            entry_price,
            stop_loss,
            take_profit,
        )
        return {"status": "ok", "mode": "paper", "side": side, "size": size}

    def monitor_and_close_paper(self) -> dict[str, Any] | None:
        """
        In paper mode, check if price hit SL or TP.
        Returns close info if position closed, else None.
        """
        if not self.settings.is_paper or self._paper_position is None:
            return None

        pos = self._paper_position
        price = self.get_mid_price()
        closed = False
        exit_price = price
        outcome = ""

        if pos.side == "LONG":
            if price <= pos.stop_loss:
                closed, exit_price, outcome = True, pos.stop_loss, "loss"
            elif price >= pos.take_profit:
                closed, exit_price, outcome = True, pos.take_profit, "win"
        else:
            if price >= pos.stop_loss:
                closed, exit_price, outcome = True, pos.stop_loss, "loss"
            elif price <= pos.take_profit:
                closed, exit_price, outcome = True, pos.take_profit, "win"

        if not closed:
            return None

        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - exit_price) * pos.size

        self._paper_balance += pnl
        result = {
            "closed": True,
            "outcome": outcome,
            "pnl": pnl,
            "exit_price": exit_price,
            "balance_after": self._paper_balance,
        }
        self._paper_position = None
        self._save_paper_state()
        logger.info(
            "Paper position closed (%s): pnl=%.2f balance=%.2f",
            outcome,
            pnl,
            self._paper_balance,
        )
        return result

    def has_open_position(self) -> bool:
        """True if ETH position is open."""
        if self.settings.is_paper:
            return self._paper_position is not None
        account = self.get_account()
        return account.position is not None

    def round_size(self, size: float) -> float:
        """Round size to exchange precision (3 decimals for ETH)."""
        return round(size, 4)
