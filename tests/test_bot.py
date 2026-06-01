"""Integration-style unit tests (no live trading, no OpenAI calls)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure bot package root is on path
BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))

from ai_analyzer import _extract_json, parse_trade_signal
from config import Settings, load_settings
from cooldown import CooldownManager
from hyperliquid_client import HyperliquidClient
from risk_manager import RiskManager
from security_utils import (
    atomic_write_json,
    sanitize_csv_cell,
    validate_eth_price,
    validate_eth_wallet_address,
    validate_tradingview_url,
)
from trade_journal import TradeJournal


def _paper_settings(tmp: Path, balance: float = 10_000.0) -> Settings:
    return Settings(
        hyperliquid_private_key="",
        hyperliquid_wallet_address="",
        openai_api_key="sk-test" + "x" * 40,
        tradingview_chart_url="https://www.tradingview.com/chart/abc123/",
        live_trading=False,
        hyperliquid_testnet=False,
        openai_model="gpt-4o",
        paper_starting_balance=balance,
        log_level="ERROR",
        screenshot_wait_ms=8000,
    )


class TestSecurityUtils(unittest.TestCase):
    def test_tradingview_url_allowlist(self) -> None:
        url = validate_tradingview_url("https://www.tradingview.com/chart/ETH/")
        self.assertTrue(url.startswith("https://"))

        with self.assertRaises(ValueError):
            validate_tradingview_url("http://www.tradingview.com/chart/x")
        with self.assertRaises(ValueError):
            validate_tradingview_url("https://evil.com/chart/x")
        with self.assertRaises(ValueError):
            validate_tradingview_url("file:///etc/passwd")

    def test_wallet_validation(self) -> None:
        addr = "0x" + "a" * 40
        self.assertEqual(validate_eth_wallet_address(addr), addr)
        with self.assertRaises(ValueError):
            validate_eth_wallet_address("not-an-address")

    def test_csv_injection(self) -> None:
        self.assertTrue(sanitize_csv_cell("=cmd").startswith("'"))

    def test_atomic_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            atomic_write_json(p, {"ok": True})
            self.assertEqual(json.loads(p.read_text()), {"ok": True})

    def test_eth_price_bounds(self) -> None:
        validate_eth_price(3500.0, "entry")
        with self.assertRaises(ValueError):
            validate_eth_price(1.0, "entry")


class TestAiAnalyzer(unittest.TestCase):
    def test_extract_json_direct(self) -> None:
        data = _extract_json('{"action": "NO_TRADE"}')
        self.assertEqual(data, {"action": "NO_TRADE"})

    def test_extract_json_markdown_fence(self) -> None:
        raw = '```json\n{"action": "LONG", "entry": 3500, "stop_loss": 3475, "take_profit": 3550}\n```'
        data = _extract_json(raw)
        self.assertEqual(data["action"], "LONG")

    def test_parse_long_signal(self) -> None:
        sig = parse_trade_signal(
            {
                "action": "LONG",
                "entry": 3500.0,
                "stop_loss": 3475.0,
                "take_profit": 3550.0,
            }
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, "LONG")

    def test_parse_invalid_geometry(self) -> None:
        self.assertIsNone(
            parse_trade_signal(
                {
                    "action": "LONG",
                    "entry": 3500.0,
                    "stop_loss": 3600.0,
                    "take_profit": 3550.0,
                }
            )
        )


class TestRiskManager(unittest.TestCase):
    def test_position_size(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg

            risk_path = Path(td) / "risk.json"
            with patch.object(cfg, "RISK_STATE_PATH", risk_path):
                rm = RiskManager()
                rm.sync_balance(10_000.0)
                result = rm.calculate_position_size(
                    10_000.0,
                    entry=3500.0,
                    stop_loss=3475.0,
                    round_fn=lambda x: round(x, 4),
                )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result.size_eth, 0)

    def test_daily_loss_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg

            risk_path = Path(td) / "risk.json"
            with patch.object(cfg, "RISK_STATE_PATH", risk_path):
                rm = RiskManager()
                rm.sync_balance(10_000.0)
                allowed, _ = rm.can_trade(8_900.0)  # 11% daily loss
        self.assertFalse(allowed)


class TestHyperliquidPaper(unittest.TestCase):
    def test_paper_open_and_close_long_win(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg

            paper_path = Path(td) / "paper.json"
            with (
                patch.object(cfg, "PAPER_STATE_PATH", paper_path),
                patch.object(cfg, "DATA_DIR", Path(td)),
            ):
                client = HyperliquidClient(_paper_settings(Path(td)))
                client.open_position(
                    side="LONG",
                    size=0.1,
                    entry_price=3500.0,
                    stop_loss=3400.0,
                    take_profit=3600.0,
                )
                self.assertTrue(client.has_open_position())

                with patch.object(client, "get_mid_price", return_value=3650.0):
                    close = client.monitor_and_close_paper()
                self.assertIsNotNone(close)
                assert close is not None
                self.assertEqual(close["outcome"], "win")
                self.assertFalse(client.has_open_position())


class TestConfig(unittest.TestCase):
    def test_load_settings_paper(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "TRADINGVIEW_CHART_URL": "https://www.tradingview.com/chart/test/",
            "LIVE_TRADING": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            s = load_settings()
        self.assertFalse(s.live_trading)
        self.assertTrue(s.is_paper)

    def test_live_requires_confirm(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "TRADINGVIEW_CHART_URL": "https://www.tradingview.com/chart/test/",
            "LIVE_TRADING": "true",
            "HYPERLIQUID_PRIVATE_KEY": "0x" + "1" * 64,
            "HYPERLIQUID_WALLET_ADDRESS": "0x" + "b" * 40,
            "ARVOR_CONFIRM_LIVE_RISK": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ValueError):
                load_settings()


class TestTradeJournal(unittest.TestCase):
    def test_journal_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg
            import importlib
            import trade_journal as tj_mod

            journal_path = Path(td) / "journal.csv"
            with patch.object(cfg, "JOURNAL_PATH", journal_path):
                importlib.reload(tj_mod)
                j = tj_mod.TradeJournal()
                j.log_entry({"action": "NO_TRADE", "outcome": "no_trade", "mode": "paper"})
            self.assertTrue(journal_path.exists())
            content = journal_path.read_text(encoding="utf-8")
            self.assertIn("NO_TRADE", content)
            importlib.reload(tj_mod)


class TestTradeExecutorCycle(unittest.TestCase):
    def test_no_trade_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg
            from trade_executor import TradeExecutor

            td_path = Path(td)
            with (
                patch.object(cfg, "DATA_DIR", td_path),
                patch.object(cfg, "PAPER_STATE_PATH", td_path / "paper.json"),
                patch.object(cfg, "RISK_STATE_PATH", td_path / "risk.json"),
                patch.object(cfg, "COOLDOWN_STATE_PATH", td_path / "cooldown.json"),
                patch.object(cfg, "JOURNAL_PATH", td_path / "journal.csv"),
                patch.object(cfg, "SCREENSHOTS_DIR", td_path / "screenshots"),
            ):
                settings = _paper_settings(td_path)
                client = HyperliquidClient(settings)
                risk = RiskManager()
                cooldown = CooldownManager()
                journal = TradeJournal()
                executor = TradeExecutor(settings, client, risk, cooldown, journal)

                fake_png = td_path / "screenshots" / "test.png"
                fake_png.parent.mkdir(parents=True, exist_ok=True)
                # Minimal valid PNG header
                fake_png.write_bytes(
                    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                )

                from ai_analyzer import TradeSignal

                with (
                    patch(
                        "trade_executor.capture_chart_screenshot",
                        return_value=fake_png,
                    ),
                    patch(
                        "trade_executor.analyze_chart",
                        return_value=TradeSignal(action="NO_TRADE", raw_response="{}"),
                    ),
                ):
                    executor.run_cycle()

                self.assertFalse(client.has_open_position())


if __name__ == "__main__":
    unittest.main(verbosity=2)
