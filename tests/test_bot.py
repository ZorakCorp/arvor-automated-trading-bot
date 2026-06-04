"""Integration-style unit tests (no live trading, no OpenAI calls)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT_ROOT))

from dataclasses import replace

from ai_analyzer import (
    _extract_json,
    apply_nestal_score,
    parse_ai_response,
    parse_nestal_response,
    parse_trade_signal,
)
from nestal_score import Bar, NestalScore, compute_nestal_score
from config import Settings, load_settings, seconds_until_next_candle_scan
from cooldown import CooldownManager
from hyperliquid_client import HyperliquidClient
from risk_manager import RiskManager
from security_utils import (
    atomic_write_json,
    sanitize_csv_cell,
    validate_chart_url,
    validate_eth_price,
    validate_eth_wallet_address,
)
from config import is_placeholder_chart_url
from screenshot import is_ai_blocked_page_reasoning, is_blocked_page_text
from trade_journal import TradeJournal


def _paper_settings(tmp: Path, balance: float = 10_000.0) -> Settings:
    return Settings(
        hyperliquid_private_key="",
        hyperliquid_wallet_address="",
        openai_api_key="sk-test" + "x" * 40,
        chart_url="https://www.tradingview.com/chart/abc123/",
        ai_prompt="test prompt",
        live_trading=False,
        hyperliquid_testnet=False,
        openai_model="gpt-5.2",
        paper_starting_balance=balance,
        log_level="ERROR",
        screenshot_wait_ms=8000,
        auto_spot_to_perp=False,
        chart_storage_state_path=None,
        chart_source="hyperliquid",
        nestal_gates=False,
    )


class TestChartImage(unittest.TestCase):
    def test_render_hyperliquid_chart(self) -> None:
        import tempfile
        from unittest.mock import MagicMock

        from chart_image import render_hyperliquid_chart_image

        step_ms = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}

        def fake_candles(interval: str, limit: int) -> list[dict]:
            step = step_ms.get(interval, 300_000)
            return [
                {
                    "t": i * step,
                    "o": str(3500 + i),
                    "h": str(3510 + i),
                    "l": str(3490 + i),
                    "c": str(3505 + i),
                }
                for i in range(30)
            ]

        client = MagicMock()
        client.get_candles.side_effect = fake_candles

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "chart.png"
            self.assertTrue(render_hyperliquid_chart_image(client, out))
            self.assertGreater(out.stat().st_size, 1000)


class TestChartUrl(unittest.TestCase):
    def test_placeholder_detection(self) -> None:
        self.assertTrue(is_placeholder_chart_url("https://www.tradingview.com/chart/.../"))
        self.assertTrue(is_placeholder_chart_url(""))
        self.assertFalse(
            is_placeholder_chart_url("https://www.tradingview.com/chart/AbCd123/MyLayout/")
        )


class TestScreenshotBlockedDetection(unittest.TestCase):
    def test_blocked_page_text(self) -> None:
        self.assertTrue(is_blocked_page_text("Error 403 Forbidden"))
        self.assertTrue(is_blocked_page_text("Sign in to continue"))
        self.assertFalse(is_blocked_page_text("ETHUSDT 5m chart with visible candles"))

    def test_ai_blocked_reasoning(self) -> None:
        self.assertTrue(
            is_ai_blocked_page_reasoning("The image shows a 403 error page instead of a chart.")
        )
        self.assertFalse(
            is_ai_blocked_page_reasoning("5m ETH holding above swing low with clear structure.")
        )


class TestSecurityUtils(unittest.TestCase):
    def test_chart_url_allowlist(self) -> None:
        url = validate_chart_url("https://www.tradingview.com/chart/ETH/")
        self.assertTrue(url.startswith("https://"))
        hl = validate_chart_url("https://app.hyperliquid.xyz/trade/ETH")
        self.assertIn("hyperliquid", hl)

        with self.assertRaises(ValueError):
            validate_chart_url("http://www.tradingview.com/chart/x")
        with self.assertRaises(ValueError):
            validate_chart_url("https://evil.com/chart/x")
        with self.assertRaises(ValueError):
            validate_chart_url("file:///etc/passwd")

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
    def test_parse_nestal_long(self) -> None:
        raw = """LONG

Entry:
3500.50

Take Profit:
3550.50

Stop Loss:
3475.50

Confidence:
89%"""
        sig = parse_nestal_response(raw)
        assert sig is not None
        self.assertEqual(sig.action, "LONG")
        self.assertEqual(sig.entry, 3500.50)
        self.assertEqual(sig.confidence, 89.0)

    def test_parse_nestal_no_trade(self) -> None:
        raw = """NO TRADE

Confidence:
58%

Reason:
Low fractal fidelity"""
        sig = parse_nestal_response(raw)
        assert sig is not None
        self.assertEqual(sig.action, "NO_TRADE")
        self.assertEqual(sig.confidence, 58.0)
        self.assertIn("fractal", sig.reasoning.lower())

    def test_parse_nestal_low_confidence_still_parses_long(self) -> None:
        raw = """LONG

Entry:
3500

Take Profit:
3600

Stop Loss:
3450

Confidence:
60%"""
        sig = parse_nestal_response(raw)
        assert sig is not None
        self.assertEqual(sig.action, "LONG")
        self.assertEqual(sig.confidence, 60.0)

    def test_apply_nestal_score_blocks_low_fidelity(self) -> None:
        sig = parse_nestal_response(
            """LONG

Entry:
3500

Take Profit:
3600

Stop Loss:
3450"""
        )
        assert sig is not None
        sig = replace(sig, model_used="gpt-5.2-2025-12-11")
        score = NestalScore(
            micro_trend="Bullish",
            meso_trend="Bullish",
            macro_trend="Bullish",
            fractal_fidelity=55.0,
            pattern_size=25.0,
            last_close=3500.0,
            bar_count=100,
        )
        gated = apply_nestal_score(sig, score)
        self.assertEqual(gated.action, "NO_TRADE")
        self.assertIn("fidelity", gated.reasoning.lower())
        self.assertEqual(gated.model_used, "gpt-5.2-2025-12-11")

    def test_apply_nestal_score_ignores_canned_ai_confidence(self) -> None:
        raw = """SHORT

Entry:
3500

Take Profit:
3400

Stop Loss:
3525

Confidence:
60%"""
        sig = parse_nestal_response(raw)
        assert sig is not None
        score = NestalScore(
            micro_trend="Bearish",
            meso_trend="Bearish",
            macro_trend="Bearish",
            fractal_fidelity=85.0,
            pattern_size=20.0,
            last_close=3500.0,
            bar_count=100,
        )
        gated = apply_nestal_score(sig, score)
        self.assertEqual(gated.action, "SHORT")
        self.assertGreaterEqual(gated.confidence or 0, 65.0)
        self.assertNotEqual(gated.confidence, 60.0)

    def test_compute_nestal_score_bullish_trend(self) -> None:
        bars = [
            Bar(t=i * 300_000, open=3500 + i, high=3510 + i, low=3490 + i, close=3505 + i)
            for i in range(50)
        ]
        score = compute_nestal_score(
            bars, meso_trend="Bullish", macro_trend="Bullish"
        )
        assert score is not None
        self.assertEqual(score.micro_trend, "Bullish")
        self.assertTrue(score.trends_aligned("LONG"))
        self.assertGreater(score.confidence_for("LONG"), score.confidence_for("SHORT"))

    def test_apply_nestal_score_blocks_misaligned_timeframes(self) -> None:
        sig = parse_nestal_response(
            """LONG

Entry:
3500

Take Profit:
3600

Stop Loss:
3450"""
        )
        assert sig is not None
        score = NestalScore(
            micro_trend="Bullish",
            meso_trend="Bearish",
            macro_trend="Bearish",
            fractal_fidelity=90.0,
            pattern_size=25.0,
            last_close=3500.0,
            bar_count=100,
        )
        gated = apply_nestal_score(sig, score, full_gates=False)
        self.assertEqual(gated.action, "NO_TRADE")
        self.assertIn("alignment", gated.reasoning.lower())

    def test_parse_ai_response_prefers_nestal(self) -> None:
        raw = """SHORT

Entry:
3500

Take Profit:
3400

Stop Loss:
3525

Confidence:
72%"""
        sig = parse_ai_response(raw)
        assert sig is not None
        self.assertEqual(sig.action, "SHORT")

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
                "reasoning": "Bullish 5m structure with higher lows.",
            }
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, "LONG")

    def test_parse_no_trade_with_reasoning(self) -> None:
        sig = parse_trade_signal(
            {
                "action": "NO_TRADE",
                "reasoning": "Choppy range, no clear bias.",
            }
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.action, "NO_TRADE")

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

    def test_position_size_respects_margin_headroom(self) -> None:
        import config as cfg

        with patch.object(cfg, "MARGIN_UTILIZATION_MAX", 0.90):
            rm = RiskManager()
            result = rm.calculate_position_size(
                49.04,
                entry=1793.60,
                stop_loss=1782.20,
                round_fn=lambda x: round(x, 4),
            )
        assert result is not None
        self.assertLessEqual(result.margin_required_usd, 49.04 * 0.90 + 0.01)

    def test_daily_and_weekly_loss_do_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg

            risk_path = Path(td) / "risk.json"
            with patch.object(cfg, "RISK_STATE_PATH", risk_path):
                rm = RiskManager()
                rm.sync_balance(10_000.0)
                allowed, reason = rm.can_trade(8_900.0)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_large_drawdown_still_allows_trade(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import config as cfg

            risk_path = Path(td) / "risk.json"
            with patch.object(cfg, "RISK_STATE_PATH", risk_path):
                rm = RiskManager()
                rm.sync_balance(10_000.0)
                allowed, reason = rm.can_trade(2_900.0)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")


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

    def test_round_price_passes_px_to_sdk(self) -> None:
        from unittest.mock import MagicMock

        client = HyperliquidClient(_paper_settings(Path(tempfile.mkdtemp())))
        mock_exchange = MagicMock()
        mock_exchange._slippage_price.return_value = 1768.3
        client._exchange = mock_exchange

        result = client._round_price(1768.3, is_buy=False)

        self.assertEqual(result, 1768.3)
        mock_exchange._slippage_price.assert_called_once_with("ETH", False, 0.0, 1768.3)


class TestConfig(unittest.TestCase):
    def test_load_settings_requires_openai_and_chart(self) -> None:
        with patch.dict(
            os.environ,
            {"LIVE_TRADING": "false", "ARVOR_CONFIRM_LIVE_RISK": "false"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_settings()

    def test_load_settings_defaults_live(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "CHART_URL": "https://www.tradingview.com/chart/test/",
            "HYPERLIQUID_PRIVATE_KEY": "0x" + "1" * 64,
            "HYPERLIQUID_WALLET_ADDRESS": "0x" + "b" * 40,
            "ARVOR_CONFIRM_LIVE_RISK": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_settings()
        self.assertTrue(s.live_trading)
        self.assertFalse(s.is_paper)

    def test_seconds_until_next_candle_scan(self) -> None:
        with patch("config.time.time", return_value=1_700_000_000.0):
            wait = seconds_until_next_candle_scan(interval_minutes=5, buffer_seconds=3)
        self.assertGreater(wait, 0)
        self.assertLessEqual(wait, 300 + 3)

        # Exactly on a 5m boundary → scan after buffer
        with patch("config.time.time", return_value=1_700_000_100.0):
            self.assertEqual(
                seconds_until_next_candle_scan(interval_minutes=5, buffer_seconds=3),
                3.0,
            )

    def test_load_settings_paper_without_chart_url(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "CHART_SOURCE": "hyperliquid",
            "LIVE_TRADING": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_settings()
        self.assertFalse(s.live_trading)
        self.assertEqual(s.chart_source, "hyperliquid")
        self.assertEqual(s.chart_url, "")

    def test_load_settings_paper(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "CHART_URL": "https://www.tradingview.com/chart/testlayout/",
            "LIVE_TRADING": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_settings()
        self.assertFalse(s.live_trading)
        self.assertTrue(s.is_paper)

    def test_live_requires_confirm(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-" + "a" * 48,
            "CHART_URL": "https://www.tradingview.com/chart/test/",
            "LIVE_TRADING": "true",
            "HYPERLIQUID_PRIVATE_KEY": "0x" + "1" * 64,
            "HYPERLIQUID_WALLET_ADDRESS": "0x" + "b" * 40,
            "ARVOR_CONFIRM_LIVE_RISK": "false",
        }
        with patch.dict(os.environ, env, clear=True):
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
