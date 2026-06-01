"""Quick smoke test: imports, config, one paper trade cycle (mocked AI)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))


def main() -> int:
    os.environ.setdefault("OPENAI_API_KEY", "sk-" + "test" * 20)
    os.environ.setdefault(
        "TRADINGVIEW_CHART_URL",
        "https://www.tradingview.com/chart/smoke/",
    )
    os.environ.setdefault("LIVE_TRADING", "false")

    print("1. Loading settings...")
    from config import ensure_data_dirs, load_settings

    settings = load_settings()
    assert settings.is_paper
    print("   OK — paper mode")

    print("2. Security validators...")
    from security_utils import validate_tradingview_url

    validate_tradingview_url(settings.tradingview_chart_url)
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

        patches: dict[str, Path] = {
            "PAPER_STATE_PATH": td_path / "paper.json",
            "RISK_STATE_PATH": td_path / "risk.json",
            "COOLDOWN_STATE_PATH": td_path / "cooldown.json",
            "JOURNAL_PATH": td_path / "journal.csv",
            "LIVE_POSITION_PATH": td_path / "live.json",
            "DATA_DIR": td_path,
            "SCREENSHOTS_DIR": td_path / "shots",
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

            fake_png = td_path / "shots" / "x.png"
            fake_png.parent.mkdir(parents=True, exist_ok=True)
            fake_png.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT"
                b"x\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            with (
                patch(
                    "trade_executor.capture_chart_screenshot",
                    return_value=fake_png,
                ),
                patch(
                    "trade_executor.analyze_chart",
                    return_value=TradeSignal(
                        action="LONG",
                        entry=3500.0,
                        stop_loss=3475.0,
                        take_profit=3550.0,
                        raw_response="{}",
                    ),
                ),
                patch.object(client, "get_mid_price", return_value=3500.0),
            ):
                executor.run_cycle()

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
