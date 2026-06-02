<div align="center">

# Arvor

### Hyperliquid ETH Bot · Nestal Fractal · OpenAI Vision

**Live by default:** every ~60s the bot screenshots your ETH chart, asks OpenAI *"Would you long or short here?"*, and executes on Hyperliquid when Nestal rules pass.

<br/>

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Hyperliquid](https://img.shields.io/badge/Exchange-Hyperliquid-00D395?style=for-the-badge)](https://hyperliquid.xyz/)
[![OpenAI](https://img.shields.io/badge/Vision-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app/)

<br/>

**Repository:** [github.com/ZorakCorp/arvor-automated-trading-bot](https://github.com/ZorakCorp/arvor-automated-trading-bot)

</div>

---

## What this bot does

| Step | What happens |
|------|----------------|
| 1 | Builds a chart PNG: **Hyperliquid API** (5m/15m/1h) or optional `CHART_URL` screenshot |
| 2 | OpenAI vision runs the **Nestal Fractal** brain (`nestal_prompt.py`) |
| 3 | The model answers only: **LONG**, **SHORT**, or **NO TRADE** + entry / TP / SL / confidence % |
| 4 | If confidence ≥ 65% and geometry is valid → **Hyperliquid** places the trade (live by default) |
| 5 | While a position is open → monitor only (SL/TP on exchange); no new screenshots until flat |

There is **no** local fractal math, **no** TradingView webhooks, and **no** alternate signal modes — **OpenAI + your chart screenshot is the only signal source.**

---

## Nestal Fractal brain

Default instructions live in `nestal_prompt.py`. The model:

- Compares **micro** (current chart), **meso** (15m), and **macro** (1h) trend alignment  
- Scores **fractal fidelity** (min 70% to trade)  
- Computes **confidence** (min 65% to trade; bot rejects lower values)  
- Uses **pattern size** for SL (1×) and TP (2×) → **2:1** risk/reward  
- Asks itself: **"Would you long or short here?"** on every screenshot (**Simple Question, Simple Answer** — no essays in the reply)  

Override the full prompt with `AI_PROMPT` in environment variables.

### Expected AI output

```
LONG

Entry:
3500.50

Take Profit:
3550.50

Stop Loss:
3475.50

Confidence:
89%
```

Or when rules fail:

```
NO TRADE

Confidence:
58%

Reason:
Low fractal fidelity
```

---

## Quick start (Railway + Docker)

Use the included **Dockerfile** (Chromium + Playwright required for screenshots).

### 1. Fork / clone

```bash
git clone https://github.com/ZorakCorp/arvor-automated-trading-bot.git
cd arvor-automated-trading-bot
```

### 2. Chart source (fixes TradingView 403 on Railway)

**Recommended:**

```env
CHART_SOURCE=hyperliquid
```

No `CHART_URL` needed. The bot renders **ETH 5m + 15m + 1h** candle panels from Hyperliquid’s free API (matches Nestal micro/meso/macro).

**Optional** — TradingView screenshot:

```env
CHART_SOURCE=auto
CHART_URL=https://www.tradingview.com/chart/YOUR_REAL_ID/
```

`auto` tries `CHART_URL` first, then falls back to Hyperliquid API if blocked (403).

### 3. Railway variables

```env
OPENAI_API_KEY=sk-...
CHART_SOURCE=hyperliquid
LIVE_TRADING=true
ARVOR_CONFIRM_LIVE_RISK=true
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_WALLET_ADDRESS=0x...
OPENAI_MODEL=gpt-4o
SCREENSHOT_WAIT_MS=18000
```

`LIVE_TRADING` defaults to **`true`** if omitted — real orders once wallet keys are set.

### 4. Deploy

- Connect repo on [Railway](https://railway.app)  
- Build with **Dockerfile**  
- Add variables above → **Deploy**

### 5. Healthy logs

```
Hyperliquid ETH Bot starting — mode: LIVE | signals: AI vision
LIVE TRADING ENABLED — real funds at risk
Chart scan every 60s → OpenAI (gpt-4o) → Hyperliquid execution
Cycle 1 — running...
Screenshot saved: ...
AI decision: NO_TRADE
AI confidence: 58%
```

`NO_TRADE` on most cycles is normal — Nestal is designed for **fewer, higher-quality** signals.

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `CHART_SOURCE` | — | `hyperliquid` (recommended), `auto`, or `url` |
| `CHART_URL` | If `url`/`auto` | Real HTTPS chart link (not `...` placeholder) |
| `HYPERLIQUID_PRIVATE_KEY` | Live | API wallet private key |
| `HYPERLIQUID_WALLET_ADDRESS` | Live | Main `0x` wallet address |
| `ARVOR_CONFIRM_LIVE_RISK` | Live | Must be `true` when live |
| `LIVE_TRADING` | — | Default `true`; set `false` for paper |
| `OPENAI_MODEL` | — | Default `gpt-4o` |
| `AI_PROMPT` | — | Override `nestal_prompt.py` |
| `SCREENSHOT_WAIT_MS` | — | Chart load wait (default `18000`) |
| `CHART_STORAGE_STATE_PATH` | — | Playwright login state JSON |
| `HYPERLIQUID_TESTNET` | — | Default `false` |
| `AUTO_SPOT_TO_PERP` | — | Move spot USDC to perps if needed (default `true` live) |
| `LOG_LEVEL` | — | Default `INFO` |

Legacy aliases still work: `TRADINGVIEW_CHART_URL`, `TRADINGVIEW_STORAGE_STATE_PATH`.

---

## Risk & execution (after AI signal)

| Setting | Value |
|---------|--------|
| Leverage | 15× |
| Risk per trade | 50% of available balance (sized from SL distance) |
| Cooldown | 30 minutes after a closed trade |
| Loss limits | Daily / weekly / monthly caps in `risk_manager.py` |

---

## Paper mode (local testing only)

```env
LIVE_TRADING=false
```

No Hyperliquid keys required. Paper balance defaults to $10,000 (`PAPER_STARTING_BALANCE`).

---

## Project layout

```
├── main.py              # 60s loop
├── nestal_prompt.py     # Nestal Fractal AI instructions
├── ai_analyzer.py       # OpenAI vision + response parser
├── screenshot.py        # Playwright chart capture
├── trade_executor.py    # AI signal → Hyperliquid orders
├── hyperliquid_client.py
├── risk_manager.py
├── cooldown.py
├── config.py
├── Dockerfile           # Required for Railway (Playwright)
└── tests/
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `OPENAI_API_KEY is required` | Add key in Railway |
| `CHART_URL is required` | Set public HTTPS chart URL |
| `HYPERLIQUID_PRIVATE_KEY is required` | Add keys for live mode, or `LIVE_TRADING=false` |
| `ARVOR_CONFIRM_LIVE_RISK` | Set to `true` for live |
| Screenshot / 403 / login page | Set `CHART_SOURCE=hyperliquid` (no TradingView needed) |
| `NO_TRADE` every cycle | Normal — low fidelity / misaligned trends |
| Confidence below 65% | Bot blocks trade even if model says LONG/SHORT |
| Playwright locally | `pip install -r requirements.txt && playwright install chromium` |

---

## Local development

```bash
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — use LIVE_TRADING=false for local tests

python -m unittest tests.test_bot -v
python scripts/smoke_test.py
```

---

## Disclaimer

This software trades **real funds** when `LIVE_TRADING=true`. You are responsible for API keys, position sizing, exchange fees, and model errors. Use at your own risk.

---

<div align="center">

**[ZorakCorp/arvor-automated-trading-bot](https://github.com/ZorakCorp/arvor-automated-trading-bot)**

</div>
