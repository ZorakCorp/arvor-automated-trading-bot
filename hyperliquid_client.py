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
        self._unified_account: bool | None = None

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
        configured = self.settings.hyperliquid_wallet_address.lower()

        # Main wallet: private key address must match HYPERLIQUID_WALLET_ADDRESS.
        # API wallet (recommended): key is the API agent; address is your main account.
        account_address: str | None = None
        if derived.lower() == configured:
            self._wallet_address = self.settings.hyperliquid_wallet_address
            logger.info("Live mode: main wallet (%s)", self._wallet_address[:10] + "...")
        else:
            account_address = self.settings.hyperliquid_wallet_address
            self._wallet_address = account_address
            logger.info(
                "Live mode: API wallet signs for main account %s",
                self._wallet_address[:10] + "...",
            )

        self._exchange = Exchange(
            wallet,
            base_url,
            account_address=account_address,
        )
        self._signer_address = wallet.address
        self._info = Info(base_url, skip_ws=True)
        logger.info("Live Hyperliquid client initialized (%s)", base_url)

        if derived.lower() != configured:
            logger.warning(
                "API wallet mode: signer=%s | funded account=%s — "
                "Spot→Perps auto-transfer requires the MAIN wallet private key.",
                self._signer_address,
                self._wallet_address,
            )
        else:
            logger.info("Wallet address: %s", self._wallet_address)

        self._is_unified_account()

    def _is_unified_account(self) -> bool:
        """Hyperliquid unified accounts merge spot + perps (no Spot→Perp transfer)."""
        if self._unified_account is not None:
            return self._unified_account
        if self.settings.is_paper or self._info is None:
            self._unified_account = False
            return False
        try:
            mode = self._info.query_user_abstraction_state(self._wallet_address)
            mode_str = (mode if isinstance(mode, str) else str(mode)).lower()
            self._unified_account = mode_str in (
                "unifiedaccount",
                "portfoliomargin",
            )
        except Exception as exc:
            logger.debug("Could not query account abstraction: %s", exc)
            self._unified_account = False
        if self._unified_account:
            logger.info(
                "Hyperliquid unified account — Spot and Perps share one USDC balance "
                "(no manual Spot→Perp transfer needed)"
            )
        return self._unified_account

    def _effective_usd_balances(
        self, state: dict, spot_usdc: float
    ) -> tuple[float, float]:
        """
        Return (account_value, available_for_trading).
        Unified accounts: perps withdrawable may read $0 while spot holds funds.
        """
        margin = state.get("marginSummary", {})
        account_value = self._to_float(margin.get("accountValue"))
        withdrawable = self._to_float(state.get("withdrawable"), account_value)

        if self._is_unified_account():
            available = max(withdrawable, account_value, spot_usdc)
            balance = account_value if account_value > 0 else available
            return balance, available

        return account_value, withdrawable

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

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _get_spot_usdc_available(self) -> float:
        """USDC in spot wallet (bot trades perps — spot must be transferred)."""
        try:
            spot_state = self._info.spot_user_state(self._wallet_address)
        except Exception as exc:
            logger.debug("Could not fetch spot balance: %s", exc)
            return 0.0
        for bal in spot_state.get("balances", []):
            if bal.get("coin") == "USDC":
                total = self._to_float(bal.get("total"))
                hold = self._to_float(bal.get("hold"))
                return max(0.0, total - hold)
        return 0.0

    def get_account(self) -> AccountSnapshot:
        """Return balance and open position."""
        if self.settings.is_paper:
            return AccountSnapshot(
                balance_usd=self._paper_balance,
                available_usd=self._paper_balance,
                position=self._paper_position,
            )

        state = self._info.user_state(self._wallet_address)
        spot_usdc = self._get_spot_usdc_available()
        balance, available = self._effective_usd_balances(state, spot_usdc)

        if self._is_unified_account():
            if available >= 0.01:
                logger.info(
                    "Unified balance: $%.2f available for ETH perps (spot USDC $%.2f)",
                    available,
                    spot_usdc,
                )
            elif spot_usdc < 0.01 and balance < 0.01:
                logger.warning(
                    "No USDC balance for %s — deposit on Hyperliquid.",
                    self._wallet_address,
                )
        elif spot_usdc > 0 and available < 0.01:
            logger.warning(
                "You have $%.2f USDC in SPOT but $%.2f in PERPS. "
                "Bot will auto-transfer Spot→Perps if AUTO_SPOT_TO_PERP=true.",
                spot_usdc,
                available,
            )
        elif balance < 0.01 and spot_usdc < 0.01:
            logger.warning(
                "No perps or spot USDC for %s — deposit or check HYPERLIQUID_WALLET_ADDRESS.",
                self._wallet_address,
            )
        else:
            logger.info(
                "Balances for %s — perps: $%.2f (withdrawable $%.2f) | spot USDC: $%.2f",
                self._wallet_address[:10] + "...",
                balance,
                available,
                spot_usdc,
            )

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
            available_usd=available,
            position=position,
        )

    def transfer_spot_to_perps_if_needed(self) -> bool:
        """
        Move available spot USDC to perps via Hyperliquid API.
        Use when UI transfer is blocked or unavailable.
        Requires private key to match HYPERLIQUID_WALLET_ADDRESS (main wallet).
        Not used on Hyperliquid unified accounts.
        """
        if self.settings.is_paper or not self.settings.auto_spot_to_perp:
            return False

        if self._is_unified_account():
            return False

        spot = self._get_spot_usdc_available()
        if spot < 0.01:
            return False

        state = self._info.user_state(self._wallet_address)
        withdrawable = self._to_float(state.get("withdrawable"))
        if withdrawable >= 0.01:
            return False

        signer = getattr(self, "_signer_address", "").lower()
        funded = self._wallet_address.lower()
        if signer != funded:
            logger.error(
                "Cannot transfer Spot→Perps: your $%.2f is on %s but "
                "HYPERLIQUID_PRIVATE_KEY controls %s. "
                "Fix Railway: use the private key for %s (MetaMask → Account details → Show private key). "
                "HYPERLIQUID_WALLET_ADDRESS must match that same address.",
                spot,
                self._wallet_address,
                self._signer_address,
                self._wallet_address,
            )
            return False

        amount = round(spot, 2)
        if amount < 0.01:
            logger.warning("Spot balance $%.4f too small to transfer", spot)
            return False

        logger.info("Auto-transferring $%.2f USDC Spot → Perps...", amount)
        try:
            result = self._exchange.usd_class_transfer(amount, to_perp=True)
        except Exception as exc:
            logger.error("Spot→Perps transfer failed: %s", redact_for_log(str(exc)))
            return False

        if isinstance(result, dict) and result.get("status") == "ok":
            logger.info("Spot→Perps transfer OK — $%.2f moved to perps wallet", amount)
            return True

        logger.error(
            "Spot→Perps transfer rejected: %s",
            redact_for_log(str(result)),
        )
        return False

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

    _INTERVAL_MS = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
    }

    def get_candles(self, interval: str = "5m", limit: int = 200) -> list[dict[str, Any]]:
        """Fetch OHLC candles from Hyperliquid (free public API; works in paper mode)."""
        import time

        if interval not in self._INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - limit * self._INTERVAL_MS[interval]

        if self._info is not None:
            return self._info.candles_snapshot(COIN, interval, start_ms, end_ms)

        import requests
        from hyperliquid.utils import constants

        base = (
            constants.TESTNET_API_URL
            if self.settings.hyperliquid_testnet
            else constants.MAINNET_API_URL
        )
        resp = requests.post(
            f"{base}/info",
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": COIN,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected candle response: {type(data)}")
        return data

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
