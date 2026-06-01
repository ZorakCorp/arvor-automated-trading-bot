"""Quick smoke test: imports, config, one paper trade cycle (mocked candles)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))


def main() -> int:
    os.environ.setdefault("SIGNAL_MODE", "candles")
    os.environ.setdefault("LIVE_TRADING", "false")

    print("1. Loading settings...")
    from config import ensure_data_dirs, load_settings

    settings = load_settings()
    assert settings.is_paper
    assert settings.uses_candles
    print("   OK — paper + candles mode")

    print("2. Fractal parser...")
    from fractal_signals import Candle, evaluate_fractal_signal

    candles = [
        Candle(t=i * 300_000, open=3500 + i, high=3510 + i, low=3490 + i, close=3505 + i)
        for i in range(30)
    ]
    sig = evaluate_fractal_signal(
        candles, None, risk_reward=2.0, require_nested=False, last_signal_candle_t=None
    )
    assert sig is not None
    print("   OK")

    print("3. Paper client + risk...")
    from unittest.mock import patch

    from ai_analyzer import TradeSignal
    from cooldown import CooldownManager
    from hyperliquid_client import HyperliquidClient
    from risk_manager import RiskManager
    from trade_executor import TradeExecutor
    from trade_journal import TradeJournal

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        import config as cfg

        patches = {
            "DATA_DIR": td_path,
            "PAPER_STATE_PATH": td_path / "paper.json",
            "RISK_STATE_PATH": td_path / "risk.json",
            "COOLDOWN_STATE_PATH": td_path / "cooldown.json",
            "JOURNAL_PATH": td_path / "journal.csv",
            "FRACTAL_SIGNAL_STATE_PATH": td_path / "fractal_state.json",
        }
        ctx = [patch.object(cfg, name, path) for name, path in patches.items()]
        for p in ctx:
            p.start()
        try:
            client = HyperliquidClient(settings)
            risk = RiskManager()
            executor = TradeExecutor(
                settings,
                client,
                risk,
                CooldownManager(),
                TradeJournal(),
            )

            long_signal = TradeSignal(
                action="LONG",
                entry=3500.0,
                stop_loss=3475.0,
                take_profit=3550.0,
                raw_response='{"signal_candle_t":1}',
                reasoning="smoke test fractal",
            )

            with patch.object(client, "get_mid_price", return_value=3500.0):
                ok, msg = executor._execute_signal(
                    long_signal,
                    source_label="smoke_test",
                    screenshot_path="",
                    available_balance=10_000.0,
                    mode="paper",
                )

            assert ok, msg
            assert client.has_open_position()
            print("   OK — paper position opened")

            with patch.object(client, "get_mid_price", return_value=3560.0):
                executor.run_cycle()

            assert not client.has_open_position()
            print("   OK — paper position closed (TP hit)")

        finally:
            for p in ctx:
                p.stop()

    ensure_data_dirs()
    print("\nSmoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
