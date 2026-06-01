"""Hyperliquid API wrapper with paper-trading simulation."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from config import (
    COIN,
    ETH_SIZE_DECIMALS,
    LEVERAGE,
    LIVE_POSITION_PATH,
    MIN_ETH_ORDER_SIZE,
    PAPER_STATE_PATH,
    Settings,
)
from security_utils import atomic_write_json, load_json_file, redact_for_log

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
        self._wallet_address: str = ""
        self._sz_decimals = ETH_SIZE_DECIMALS
        self._min_order_size = MIN_ETH_ORDER_SIZE

        if settings.is_paper:
            self._load_paper_state()
            logger.info("Paper trading mode enabled (balance=%.2f)", self._paper_balance)
        else:
            self._init_live_client()
            self._load_min_order_size()

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
        derived = wallet.address
        configured = self.settings.hyperliquid_wallet_address
        if derived.lower() != configured.lower():
            raise ValueError(
                "HYPERLIQUID_PRIVATE_KEY does not match HYPERLIQUID_WALLET_ADDRESS"
            )

        self._exchange = Exchange(wallet, base_url)
        self._info = Info(base_url, skip_ws=True)
        self._wallet_address = configured
        logger.info("Live Hyperliquid client initialized (%s)", base_url)

    def _load_min_order_size(self) -> None:
        try:
            meta = self._info.meta()
            for asset in meta.get("universe", []):
                if asset.get("name") == COIN:
                    self._sz_decimals = int(asset.get("szDecimals", ETH_SIZE_DECIMALS))
                    min_sz = asset.get("minSz")
                    if min_sz is not None:
                        self._min_order_size = max(float(min_sz), MIN_ETH_ORDER_SIZE)
                    break
        except Exception as exc:
            logger.warning("Could not load ETH meta: %s", exc)

    def _load_paper_state(self) -> None:
        self._paper_balance = self.settings.paper_starting_balance
        self._paper_position: Position | None = None
        raw = load_json_file(PAPER_STATE_PATH)
        if not raw:
            return
        try:
            self._paper_balance = float(raw.get("balance_usd", self._paper_balance))
            pos = raw.get("position")
            if isinstance(pos, dict):
                self._paper_position = Position(**pos)
        except (TypeError, KeyError, ValueError) as exc:
            logger.warning("Could not load paper state: %s", exc)

    def _save_paper_state(self) -> None:
        payload = {
            "balance_usd": self._paper_balance,
            "position": asdict(self._paper_position) if self._paper_position else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(PAPER_STATE_PATH, payload)

    def _save_live_position_meta(self, pos: Position) -> None:
        atomic_write_json(LIVE_POSITION_PATH, asdict(pos))

    def _clear_live_position_meta(self) -> None:
        if LIVE_POSITION_PATH.exists():
            try:
                LIVE_POSITION_PATH.unlink()
            except OSError as exc:
                logger.warning("Could not remove live position meta: %s", exc)

    def _load_live_position_meta(self) -> Position | None:
        raw = load_json_file(LIVE_POSITION_PATH)
        if not raw:
            return None
        try:
            return Position(**raw)
        except (TypeError, KeyError) as exc:
            logger.warning("Invalid live position meta: %s", exc)
            return None

    def get_account(self) -> AccountSnapshot:
        """Return balance and open position."""
        if self.settings.is_paper:
            return AccountSnapshot(
                balance_usd=self._paper_balance,
                available_usd=self._paper_balance,
                position=self._paper_position,
            )

        state = self._info.user_state(self._wallet_address)
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
            meta = self._load_live_position_meta()
            position = Position(
                coin=COIN,
                side=side,
                size=abs(szi),
                entry_price=entry,
                stop_loss=meta.stop_loss if meta else 0.0,
                take_profit=meta.take_profit if meta else 0.0,
                opened_at=meta.opened_at if meta else datetime.now(timezone.utc).isoformat(),
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
            return self._fetch_public_mid_price()

        mids = self._info.all_mids()
        price = float(mids[COIN])
        if price <= 0:
            raise RuntimeError("Invalid mid price from exchange")
        return price

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

        result = self._exchange.update_leverage(LEVERAGE, COIN, is_cross=True)
        logger.info("Leverage update result: %s", result)
        if not self._order_ok(result):
            raise RuntimeError(f"Leverage update failed: {redact_for_log(str(result))}")

    def open_position(
        self,
        side: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        use_limit: bool = False,
    ) -> dict[str, Any]:
        """Open ETH position with SL/TP. Rolls back entry if protection orders fail."""
        is_buy = side.upper() == "LONG"
        size = self.round_size(size)
        if size < self._min_order_size:
            raise ValueError(
                f"Order size {size} below minimum {self._min_order_size} ETH"
            )

        if self.settings.is_paper:
            return self._paper_open(is_buy, size, entry_price, stop_loss, take_profit)

        if self.has_open_position():
            raise RuntimeError("Cannot open position: one already open")

        entry_result: dict[str, Any] | None = None
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
                raise RuntimeError(
                    f"Entry order failed: {redact_for_log(str(entry_result))}"
                )

            sl_result = self._place_trigger(is_buy, size, stop_loss, tpsl="sl")
            if not self._order_ok(sl_result):
                raise RuntimeError(
                    f"Stop loss order failed: {redact_for_log(str(sl_result))}"
                )

            tp_result = self._place_trigger(is_buy, size, take_profit, tpsl="tp")
            if not self._order_ok(tp_result):
                raise RuntimeError(
                    f"Take profit order failed: {redact_for_log(str(tp_result))}"
                )

            pos = Position(
                coin=COIN,
                side="LONG" if is_buy else "SHORT",
                size=size,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=datetime.now(timezone.utc).isoformat(),
            )
            self._save_live_position_meta(pos)

            return {
                "entry": entry_result,
                "stop_loss": sl_result,
                "take_profit": tp_result,
            }
        except Exception as exc:
            logger.error("Order placement failed: %s", redact_for_log(str(exc)))
            if entry_result is not None and self._order_ok(entry_result):
                logger.error("Rolling back: closing unprotected entry position")
                self._emergency_close(size)
            raise

    def _emergency_close(self, size: float) -> None:
        """Close position after failed SL/TP placement."""
        try:
            result = self._exchange.market_close(COIN, sz=size, slippage=0.02)
            logger.warning("Emergency close result: %s", redact_for_log(str(result)))
        except Exception as exc:
            logger.critical(
                "EMERGENCY CLOSE FAILED — manual intervention required: %s",
                redact_for_log(str(exc)),
            )
        self._clear_live_position_meta()

    def _place_trigger(
        self, was_buy: bool, size: float, trigger_px: float, tpsl: str
    ) -> dict:
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
    def _order_ok(result: Any) -> bool:
        """Hyperliquid can return status ok with per-order errors in statuses."""
        if not isinstance(result, dict):
            return False
        if result.get("status") != "ok":
            return False
        response = result.get("response") or {}
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            return True
        statuses = data.get("statuses")
        if not statuses:
            return True
        for item in statuses:
            if isinstance(item, dict) and "error" in item:
                return False
        return True

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
        """Paper mode: check if price hit SL or TP."""
        if not self.settings.is_paper or self._paper_position is None:
            return None

        pos = self._paper_position
        try:
            price = self.get_mid_price()
        except Exception as exc:
            logger.error("Cannot fetch price for paper monitor: %s", exc)
            return None

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

        pnl = (
            (exit_price - pos.entry_price) * pos.size
            if pos.side == "LONG"
            else (pos.entry_price - exit_price) * pos.size
        )

        self._paper_balance = max(0.0, self._paper_balance + pnl)
        result = {
            "closed": True,
            "outcome": outcome,
            "pnl": pnl,
            "exit_price": exit_price,
            "balance_after": self._paper_balance,
            "side": pos.side,
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

    def monitor_live_position_close(self) -> dict[str, Any] | None:
        """
        Live mode: detect closed position and estimate outcome for cooldown/journal.
        Uses saved SL/TP meta + last mid to classify win/loss.
        """
        if self.settings.is_paper:
            return None

        meta = self._load_live_position_meta()
        if meta is None:
            return None

        if self.has_open_position():
            return None

        try:
            exit_price = self.get_mid_price()
        except Exception as exc:
            logger.error("Cannot fetch exit price: %s", exc)
            exit_price = meta.entry_price

        if meta.side == "LONG":
            if exit_price >= meta.take_profit * 0.999:
                outcome = "win"
            elif exit_price <= meta.stop_loss * 1.001:
                outcome = "loss"
            else:
                outcome = "win" if exit_price > meta.entry_price else "loss"
            pnl = (exit_price - meta.entry_price) * meta.size
        else:
            if exit_price <= meta.take_profit * 1.001:
                outcome = "win"
            elif exit_price >= meta.stop_loss * 0.999:
                outcome = "loss"
            else:
                outcome = "win" if exit_price < meta.entry_price else "loss"
            pnl = (meta.entry_price - exit_price) * meta.size

        self._clear_live_position_meta()
        account = self.get_account()
        return {
            "closed": True,
            "outcome": outcome,
            "pnl": pnl,
            "exit_price": exit_price,
            "balance_after": account.balance_usd,
            "side": meta.side,
        }

    def has_open_position(self) -> bool:
        """True if ETH position is open."""
        if self.settings.is_paper:
            return self._paper_position is not None
        account = self.get_account()
        return account.position is not None

    def round_size(self, size: float) -> float:
        """Round size to exchange precision."""
        return round(size, self._sz_decimals)
