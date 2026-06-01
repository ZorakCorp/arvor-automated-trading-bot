# Hyperliquid ETH Trading Bot (Nested Fractal + AI Vision)

Automated ETH perpetuals bot for [Hyperliquid](https://hyperliquid.xyz). It captures your **TradingView** ETH 5-minute chart with your **Nested Fractal - Clean** indicator, sends the screenshot to an **OpenAI vision** model, and trades based on structured JSON (LONG / SHORT / NO_TRADE with entry, stop loss, take profit).

**Default mode is paper trading.** Live trading only runs when `LIVE_TRADING=true`.

---

## Strategy flow

1. Capture TradingView screenshot (`screenshot.py`)
2. Ask AI to read **gold TP**, **orange SL**, and **LONG/SHORT panel** from Nested Fractal (`ai_analyzer.py`)
3. Validate JSON and risk rules (`trade_executor.py`, `risk_manager.py`)
4. Size position: **50% of balance at risk** to stop distance, **5x leverage** (`risk_manager.py`)
5. Place entry + SL + TP on Hyperliquid (`hyperliquid_client.py`)
6. Monitor position, journal trades, **30-minute cooldown** after win/loss (`cooldown.py`, `trade_journal.py`)

---

## Trading rules (implemented)

| Rule | Value |
|------|--------|
| Asset | ETH only |
| Timeframe | 5-minute chart screenshot |
| Max positions | 1 |
| Leverage | 5x |
| Risk per trade | 50% of available capital (to SL) |
| Daily max loss | 10% |
| Weekly max loss | 70% |
| Monthly max loss | 70% |
| Cooldown after win/loss | 30 minutes |
| Reversals | Blocked while position open |
| Live trading | Only if `LIVE_TRADING=true` |

---

## Project layout

```
bot/
├── main.py                 # Entry point, main loop
├── config.py               # Env vars and constants
├── hyperliquid_client.py   # Hyperliquid API + paper simulator
├── screenshot.py           # Playwright TradingView capture
├── ai_analyzer.py          # OpenAI vision + JSON parsing
├── risk_manager.py         # Sizing + loss limits + win rate
├── trade_executor.py       # Cycle orchestration
├── trade_journal.py        # CSV journal
├── cooldown.py             # Post-trade cooldown
├── requirements.txt
├── .env.example
├── data/                   # Created at runtime
│   ├── screenshots/
│   ├── trade_journal.csv
│   ├── paper_state.json
│   ├── risk_state.json
│   └── cooldown_state.json
└── README.md
```

---

## Setup (local)

### 1. Clone / copy project and create venv

```bash
cd bot
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for vision |
| `TRADINGVIEW_CHART_URL` | Yes | Your saved ETH 5m chart URL with NESTAL indicator |
| `HYPERLIQUID_PRIVATE_KEY` | Live only | Wallet private key |
| `HYPERLIQUID_WALLET_ADDRESS` | Live only | Main wallet address (not API wallet address) |
| `LIVE_TRADING` | No | `false` (default) = paper |
| `PAPER_STARTING_BALANCE` | No | Default `10000` |
| `HYPERLIQUID_TESTNET` | No | Use testnet API if `true` |

### 3. TradingView + Nested Fractal setup

1. Open **ETH** perpetual chart (e.g. `BINANCE:ETHUSDT` or Hyperliquid symbol) on **5m**.
2. Add your Pine script: **Nested Fractal - Clean**.
3. Recommended indicator settings for the bot (match your script defaults or tune):
   - Fractal Lookback: `30`
   - Min Fidelity %: `70`
   - Min Confidence %: `65`
   - TP Multiplier: `2.0` / SL Multiplier: `1.0`
   - Min Bars Between Signals: `50` (~4 hours on 5m — bot will often see `NO_TRADE` between signals, which is correct)
4. Save the layout and copy the full chart URL → `TRADINGVIEW_CHART_URL`.
5. Zoom so the **right side** shows gold **TP:** and orange **SL:** labels and the **LONG/SHORT panel** when a signal is active.

**What the AI looks for on your indicator:**

| Visual | Meaning |
|--------|---------|
| Gold line + `TP:` label | Take profit price |
| Orange line + `SL:` label | Stop loss price |
| White dashed line | Entry |
| Panel `LONG` / `SHORT` + confidence % | Direction |
| No TP + SL + panel together | `NO_TRADE` |

> **Railway:** Use a **public** saved chart link if possible. Private/login-only charts often fail in headless Chromium. Increase `SCREENSHOT_WAIT_MS=22000` if lines render slowly.

**Optional (future):** Your script already has `alertcondition(goLong)` / `alertcondition(goShort)` — we can add a TradingView webhook path later for faster, exact prices without vision.

### 4. Run (paper mode)

```bash
python main.py
```

Logs show balance, AI decisions, and journal path: `data/trade_journal.csv`.

### 5. Enable live trading (only when ready)

```env
LIVE_TRADING=true
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_WALLET_ADDRESS=0x...
```

Fund the wallet on Hyperliquid and authorize API trading in the Hyperliquid UI if using an API wallet key.

---

## Deploy on Railway

### `Procfile` (optional — add to `bot/`)

```
worker: python main.py
```

### Railway settings

1. **New Project** → deploy from repo or upload `bot/` folder.
2. **Variables**: copy all keys from `.env.example` (use Railway secrets, not committed `.env`).
3. **Build command**:

   ```bash
   pip install -r requirements.txt && playwright install chromium
   ```

4. **Start command**: `python main.py`
5. Use a **worker** service (long-running), not a one-shot web service.
6. Attach a **volume** mounted at `/app/data` (or `bot/data`) so journal and state persist across restarts.

### Railway + Playwright

Playwright needs Chromium system deps. If the default image fails, use a Dockerfile:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

---

## Trade journal

`data/trade_journal.csv` columns:

- timestamp, action, entry, stop_loss, take_profit, position_size, leverage  
- outcome, pnl, balance_after, screenshot_path, ai_raw_response, mode, notes  

---

## Safety behavior

- Invalid AI JSON → no trade  
- `NO_TRADE` → no trade  
- Open position → no new entry  
- Loss limits (day/week/month) → trading paused for that period  
- API / screenshot / order failure → logged, no reckless retry on order failure  
- Missing SL/TP → no trade  

---

## Customizing

- **AI model**: `OPENAI_MODEL=gpt-4o` (or `gpt-4o-mini` for cost)
- **Loop interval**: `LOOP_INTERVAL_SECONDS` in `config.py` (default 60s)
- **Prompt**: edit `AI_PROMPT` in `ai_analyzer.py`
- **Risk**: edit fractions in `config.py`

---

## Disclaimer

This software is for educational purposes. Trading perpetual futures with high leverage and 50% risk per trade can cause rapid total loss. Test thoroughly in paper mode. You are responsible for your keys, capital, and compliance with local laws.

---

## Next step

The AI prompt in `ai_analyzer.py` is tuned for **Nested Fractal - Clean**. Edit `AI_PROMPT` there if you change colors or layout in Pine.
