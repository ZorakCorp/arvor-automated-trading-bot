<div align="center">

# ⚡ Arvor

### Hyperliquid ETH Bot · Nested Fractal · AI Vision

**Fully automated ETH bot using free Hyperliquid candle data — no TradingView Pro, no OpenAI**

<br/>

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Hyperliquid](https://img.shields.io/badge/Exchange-Hyperliquid-00D395?style=for-the-badge)](https://hyperliquid.xyz/)
[![OpenAI](https://img.shields.io/badge/Vision-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app/)

<br/>

**Repos (same code, both stay in sync):**  
[shep95/arvor-automated-trading-bot](https://github.com/shep95/arvor-automated-trading-bot) · [ZorakCorp/arvor-automated-trading-bot](https://github.com/ZorakCorp/arvor-automated-trading-bot)

<br/>

[Setup Checklist](#-setup-checklist-zero-to-running) · [Railway Variables](#-railway-variables-copy-paste) · [How to Know It Works](#-how-to-know-its-working) · [Troubleshooting](#-troubleshooting)

</div>

---

## 📋 What you need before you start

Gather these **before** opening Railway:

| # | What | Cost |
|---|------|------|
| 1 | **Nothing extra for signals** | `SIGNAL_MODE=candles` uses Hyperliquid’s free public API |
| 2 | **Railway hosting** | Free trial / ~$5/mo hobby (only ongoing cost) |
| 3 | **Hyperliquid account** | Free to use; you only pay trading fees when live |

> **Paper mode:** set `LIVE_TRADING=false` — no wallet keys needed. Bot runs with fake balance.

> **Optional paid modes:** `SIGNAL_MODE=webhook` needs TradingView Pro. `SIGNAL_MODE=screenshot` needs OpenAI API credits.

---

## 🚀 Setup checklist (zero to running)

### Part 1 — Free automated signals (default, 2 min)

**Goal:** Bot scans **Hyperliquid ETH 5m candles** every 60 seconds for fractal breakouts. Zero TradingView, zero OpenAI.

**Railway variables (paper mode — minimum):**

```env
SIGNAL_MODE=candles
LIVE_TRADING=false
```

That’s it. Deploy and the bot runs 24/7.

**How signals work:**
- Detects **Williams fractal** breakouts on 5m ETH (similar concept to nested fractal, computed locally)
- **Stop loss** = opposite fractal level
- **Take profit** = configurable R multiple (default **2R** via `FRACTAL_RISK_REWARD=2.0`)
- Optional higher-timeframe filter: `FRACTAL_REQUIRE_NESTED=true` + `FRACTAL_HTF_INTERVAL=15m`

> This is **not** a pixel-perfect clone of your TradingView “Nested Fractal - Clean” Pine script — it’s a free, automated fractal strategy on the same 5m ETH market. For exact TV indicator parity, use paid webhook mode or screenshot+AI mode.

---

### Part 1b — TradingView webhooks (optional, requires Pro)

1. Deploy on Railway (Part 4 below).
2. Service → **Settings** → **Networking** → **Generate Domain** (e.g. `arvor-production.up.railway.app`).
3. In Railway **Variables**, set:
   - `SIGNAL_MODE=webhook`
   - `TRADINGVIEW_WEBHOOK_SECRET=` a long random string (e.g. 32+ chars from a password manager)
4. Redeploy. Logs should show: `Webhook mode: TradingView alerts → port ...`

Your webhook URL (save this):

```text
https://YOUR-RAILWAY-DOMAIN.up.railway.app/webhook?secret=YOUR_TRADINGVIEW_WEBHOOK_SECRET
```

Health check: `https://YOUR-RAILWAY-DOMAIN.up.railway.app/health`

#### 1b — Create alert on your ETH 5m chart

1. Open your chart with **Nested Fractal - Clean** on **ETH 5m**.
2. Click **Alert** (clock icon) → **Create alert**.
3. **Condition:** choose your indicator’s signal (e.g. **Nested Fractal - Clean** → “Any alert() function call”, or the indicator’s Long/Short condition if listed).
4. **Notifications** tab → enable **Webhook URL** → paste the URL from step 1a.
5. **Message** — paste **one** of these templates and adjust plot names to match your indicator:

**JSON (recommended):**

```json
{
  "action": "LONG",
  "entry": {{close}},
  "stop_loss": {{plot_1}},
  "take_profit": {{plot_0}},
  "reasoning": "Nested Fractal alert"
}
```

For SHORT alerts, duplicate the alert with `"action": "SHORT"` (or use your indicator’s alert message if it already outputs JSON).

**Simple pipe format** (if JSON placeholders fail):

```text
LONG|{{close}}|{{plot_1}}|{{plot_0}}
```

Replace `plot_0` / `plot_1` with the correct SL/TP plot indices from your Pine script (check indicator source or trial-and-error in the alert preview).

6. **Expiration:** open-ended. **Alert name:** e.g. `Arvor LONG ETH 5m`.
7. Create a **second alert** for SHORT if your indicator uses separate conditions.

TradingView sends the message **only when the signal fires** — the bot opens the trade, sets SL/TP, and monitors the position. No polling, no screenshots.

---

### Part 1 (legacy) — Screenshot mode

**Only if you cannot use TradingView webhooks.** Set `SIGNAL_MODE=screenshot` and provide OpenAI + chart URL.

**Goal:** A link the bot can screenshot every minute.

1. Go to [tradingview.com](https://www.tradingview.com) and log in.
2. Open **ETH** (e.g. `BINANCE:ETHUSDT` or Hyperliquid ETH).
3. Set timeframe to **5m** (bottom toolbar).
4. Click **Indicators** → add your Pine script **Nested Fractal - Clean**.
5. Zoom the chart so the **right side** shows:
   - Gold **TP:** line (when signal active)
   - Orange **SL:** line
   - **LONG** or **SHORT** panel
6. Click **Save** (top toolbar) → save the layout.
7. **Share → Make public** (or copy a link that works when logged out).
8. Copy the **full URL** from your browser bar.  
   Example: `https://www.tradingview.com/chart/AbCdEf123/MyLayout/`
9. **Verify in incognito:** paste the URL in a private window — you must see ETH 5m + Nested Fractal **without** logging in. If you get **403** or a login wall, Railway will too.
10. Keep this URL — you'll paste it as `TRADINGVIEW_CHART_URL`.

> **Railway runs headless** from a datacenter IP. Private/login-only charts often return **403**. Use a public saved chart, or upload a Playwright session file (see `TRADINGVIEW_STORAGE_STATE_PATH` below).

---

### Part 2 — OpenAI API key (5 min)

1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Click **Create new secret key**.
3. Copy the key (starts with `sk-`).
4. Add billing/credits on OpenAI if needed (vision calls cost money).
5. Keep this key — you'll paste it as `OPENAI_API_KEY`.

---

### Part 3 — Hyperliquid wallet (live trading only)

**Skip this entire part if you're testing in paper mode.**

#### Step 3a — Deposit USDC

1. Go to [app.hyperliquid.xyz](https://app.hyperliquid.xyz).
2. Connect **MetaMask** (or your wallet).
3. Click **Deposit** → send **USDC** from Arbitrum to Hyperliquid.
4. Wait until balance shows in the app.

#### Step 3b — Unified account (most users)

New Hyperliquid accounts use a **Unified Account** — Spot and Perps share **one balance**.  
You do **not** need to transfer Spot → Perps manually. The bot handles this automatically.

#### Step 3c — Copy your wallet address

1. In Hyperliquid, click your wallet address (top of page).
2. Copy the full address, e.g. `0x24ff8C760c6433A7507a4C5352e81fCa28806762`.
3. This goes in Railway as `HYPERLIQUID_WALLET_ADDRESS`.

#### Step 3d — Get your private key (same wallet!)

> **There is no separate "Hyperliquid key" or "USDC key".** One Ethereum wallet = one private key.

1. Open **MetaMask**.
2. Click the **account dropdown** (account name at top).
3. Click each account → copy its address until it **matches** the Hyperliquid address from Step 3c.
4. On **that account**: ⋮ → **Account details** → **Show private key** → enter password.
5. Copy the key (starts with `0x`, 64 hex characters).
6. This goes in Railway as `HYPERLIQUID_PRIVATE_KEY`.

> **Critical:** `HYPERLIQUID_PRIVATE_KEY` and `HYPERLIQUID_WALLET_ADDRESS` must be the **same account** — the one with your USDC on Hyperliquid.

---

### Part 4 — Deploy on Railway (20 min)

#### Step 4a — Create project

1. Go to [railway.app](https://railway.app) → log in.
2. Click **New Project**.
3. Choose **Deploy from GitHub repo**.
4. Select **`arvor-automated-trading-bot`** (from shep95 or ZorakCorp).
5. Wait for the first deploy to start.

#### Step 4b — Use Docker (required for screenshots)

1. Click your **service** (the box in the project).
2. Go to **Settings**.
3. Find **Build** section.
4. Set **Builder** → **Dockerfile**.
5. **Dockerfile path** → `Dockerfile` (repo root).
6. Leave **Start command** empty (Dockerfile already runs `python main.py`).

#### Step 4c — Add a volume (keeps your journal)

1. Still in the service → **Volumes** tab.
2. Click **Add Volume**.
3. **Mount path:** `/app/data`
4. Save.

Without this, trade history resets every redeploy.

#### Step 4d — Add variables

1. Click **Variables** tab.
2. Click **Raw Editor** (easiest) or add one-by-one.
3. Paste the block for your mode (see [Railway Variables](#-railway-variables-copy-paste) below).
4. Replace placeholder values with your real keys.
5. Click **Save** → Railway redeploys automatically.

#### Step 4e — Check logs

1. Go to **Deployments** → latest deploy → **View Logs**.
2. Wait 1–2 minutes.
3. Compare to [How to Know It's Working](#-how-to-know-its-working) below.

---

## 🔐 Railway variables (copy-paste)

Open Railway → your service → **Variables** → **Raw Editor**.

---

### Option A — Paper mode (free)

```env
SIGNAL_MODE=candles
LIVE_TRADING=false
LOG_LEVEL=INFO
```

### Option B — Live trading

```env
SIGNAL_MODE=candles
LIVE_TRADING=true
ARVOR_CONFIRM_LIVE_RISK=true
HYPERLIQUID_WALLET_ADDRESS=0xYOUR_WALLET
HYPERLIQUID_PRIVATE_KEY=0xYOUR_KEY
```

---

### Variable reference (every key explained)

| Variable | Paper | Live | What to put |
|----------|:-----:|:----:|-------------|
| `SIGNAL_MODE` | ✅ | ✅ | `candles` (free default), `webhook`, or `screenshot` |
| `FRACTAL_RISK_REWARD` | — | — | TP distance as multiple of risk (default `2.0`) |
| `FRACTAL_REQUIRE_NESTED` | — | — | `true` = require 15m/1h trend alignment |
| `FRACTAL_HTF_INTERVAL` | — | — | `15m`, `1h`, or `none` (default `15m`) |
| `TRADINGVIEW_WEBHOOK_SECRET` | — | — | Only for `SIGNAL_MODE=webhook` (TV Pro) |
| `OPENAI_API_KEY` | — | — | Only for `SIGNAL_MODE=screenshot` |
| `LIVE_TRADING` | ✅ | ✅ | `false` = paper · `true` = real money |
| `ARVOR_CONFIRM_LIVE_RISK` | — | ✅ | Must be `true` when live |
| `HYPERLIQUID_WALLET_ADDRESS` | — | ✅ | Your `0x...` address **with USDC on Hyperliquid** |
| `HYPERLIQUID_PRIVATE_KEY` | — | ✅ | Private key for **that same** `0x...` address |
| `HYPERLIQUID_TESTNET` | — | — | `false` for mainnet (default) |
| `AUTO_SPOT_TO_PERP` | — | — | `true` = auto-move Spot→Perps (ignored on unified accounts) |
| `OPENAI_MODEL` | — | — | `gpt-4o` (default) or `gpt-4o-mini` (cheaper) |
| `SCREENSHOT_WAIT_MS` | — | — | `22000` if chart loads slowly (default `18000`) |
| `TRADINGVIEW_STORAGE_STATE_PATH` | — | — | Path to Playwright `tv_auth.json` if chart requires login |
| `PAPER_STARTING_BALANCE` | — | — | Fake balance in paper mode (default `10000`) |
| `LOG_LEVEL` | — | — | `INFO` (default) or `DEBUG` for more detail |

---

### Boolean rules (avoid typos)

Use **lowercase only**:

| ✅ Works | ❌ Broken |
|----------|-----------|
| `true` | `True`, `TRUE`, `yes` (yes works but use true) |
| `false` | `False`, `0` |

---

## ✅ How to know it's working

### Candles mode (free) — good logs

```text
Hyperliquid ETH Bot starting — mode: PAPER | signals: CANDLES
Candles mode (free): Hyperliquid ETH 5m fractals every 60s
Perps balance: $10000.00 (available $10000.00)
```

When a fractal breakout fires:

```text
AI decision: LONG
Executing LONG | size=0.0012 ETH | entry=3500.00 sl=3475.00 tp=3550.00 | source=hyperliquid_fractal
Position opened successfully
```

Between signals you’ll see debug lines like `Fractal scan: No new 5m fractal breakout` — that’s normal.

---

### Webhook mode — good logs

```text
Hyperliquid ETH Bot starting — mode: PAPER | signals: WEBHOOK
Webhook mode: TradingView alerts → port 8080 (path /webhook?secret=...)
TradingView webhook listening on 0.0.0.0:8080 (/webhook)
Perps balance: $10000.00 (available $10000.00)
```

When your TradingView alert fires:

```text
TradingView webhook received: LONG (source={"action":"LONG",...})
Executing LONG | size=0.0012 ETH | entry=3500.00 sl=3475.00 tp=3550.00
Position opened successfully
```

Between alerts the bot only **monitors** open positions — no screenshots, no OpenAI calls.

---

### Live mode — good logs

```text
Hyperliquid ETH Bot starting — mode: LIVE
LIVE TRADING ENABLED — real funds at risk
Hyperliquid unified account — Spot and Perps share one USDC balance
Unified balance: $8.45 available for ETH perps
Screenshot saved: ... (100000+ bytes)
HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/1.1 200 OK"
AI signal: NO_TRADE
```

When a trade actually happens:

```text
Executing LONG | size=0.0012 ETH | entry=3500.00 sl=3475.00 tp=3550.00
Position opened successfully
```

---

## 🔄 How it works

```mermaid
flowchart LR
    A[Hyperliquid 5m candles] -->|Fractal scan| B[Arvor on Railway]
    B --> C{Risk check}
    C -->|Pass| D[Hyperliquid Order]
    C -->|No signal| E[Wait 60s]
    D --> F[Monitor SL/TP + Journal]
```

| Step | What happens |
|------|----------------|
| 1 | Bot pulls free ETH 5m OHLC from Hyperliquid every 60s |
| 2 | Detects fractal breakout + sets SL/TP |
| 3 | If valid signal → sizes position (50% risk, 5× leverage) |
| 4 | Places ETH entry + stop loss + take profit on Hyperliquid |
| 5 | Logs everything to `data/trade_journal.csv` |

---

## 📐 Nested Fractal indicator

The AI only trades when it sees **all** of these on the chart:

| On chart | Meaning |
|----------|---------|
| 🟡 Gold line + `TP:` | Take profit |
| 🟠 Orange line + `SL:` | Stop loss |
| ⬜ White dashed line | Entry |
| Panel **LONG** or **SHORT** | Direction |
| None of the above | `NO_TRADE` (bot waits) |

Recommended indicator settings: Lookback `30` · Fidelity `70` · Confidence `65` · Min bars `50` (~4h between signals on 5m).

---

## ⚙️ Trading rules (built-in)

| Rule | Value |
|------|-------|
| Asset | ETH only |
| Timeframe | 5-minute chart |
| Leverage | 5× |
| Risk per trade | 50% of balance to stop |
| Max open positions | 1 |
| Cooldown after win/loss | 30 minutes |
| Daily max loss | 10% |

---

## 🔧 Troubleshooting

| Problem | Fix |
|---------|-----|
| `Configuration error: OPENAI_API_KEY` | Add your `sk-...` key in Railway Variables |
| `TRADINGVIEW_CHART_URL must use https` | URL must start with `https://www.tradingview.com/chart/` |
| Playwright / Chromium error | Redeploy latest code (Dockerfile uses Playwright 1.60) |
| `Account balance: $0.00` but you have USDC | Wrong `HYPERLIQUID_WALLET_ADDRESS` — use address from Hyperliquid UI |
| `Must deposit before performing actions` | Private key doesn't match funded wallet — fix key/address pair |
| `Action disabled when unified account is active` | Normal on unified accounts — update to latest code |
| `Trading blocked: No available balance` | Deposit USDC on Hyperliquid for your wallet address |
| `NO_TRADE` every cycle | **Normal** — no fractal signal on chart yet |
| AI says **403 error page** / no chart | Chart URL blocked on Railway — open URL in **incognito**; enable **public share**; or set `TRADINGVIEW_STORAGE_STATE_PATH=/app/data/tv_auth.json` (see below) |
| Screenshot fails | Increase `SCREENSHOT_WAIT_MS=30000`; confirm chart loads without login |
| `TradingView page blocked before screenshot` | Same as 403 — fix public URL or session file |
| `LIVE_TRADING requires ARVOR_CONFIRM_LIVE_RISK` | Add `ARVOR_CONFIRM_LIVE_RISK=true` |

---

## 🏗️ Project structure

```
arvor-automated-trading-bot/
├── main.py                 # Bot entry point
├── config.py               # Settings from env vars
├── hyperliquid_client.py   # Hyperliquid + unified account support
├── fractal_signals.py      # Free 5m fractal logic (default mode)
├── webhook_server.py       # Optional TradingView webhooks (Pro)
├── tv_signal_parser.py     # Parse alert JSON / pipe payloads
├── screenshot.py           # Screenshot mode only (Playwright)
├── ai_analyzer.py          # OpenAI vision (screenshot mode)
├── risk_manager.py         # Position sizing + loss limits
├── trade_executor.py       # Full trade cycle
├── trade_journal.py        # CSV audit log
├── security_utils.py       # URL validation, redaction
├── Dockerfile              # Railway deploy (required)
├── Procfile
├── requirements.txt
├── .env.example            # Local dev template
└── data/                   # Created at runtime (mount on Railway)
    ├── screenshots/
    ├── trade_journal.csv
    └── *.json state files
```

---

## 💻 Run locally (optional)

```bash
git clone https://github.com/shep95/arvor-automated-trading-bot.git
cd arvor-automated-trading-bot
python -m venv .venv

# Windows
.venv\Scripts\activate
# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # Windows
# cp .env.example .env   # Mac/Linux
# Edit .env with your keys
python main.py
```

Run tests:

```bash
python -m unittest tests.test_bot -v
python scripts/smoke_test.py
```

---

## 🛡️ Safety

- Invalid AI response → no trade  
- `NO_TRADE` → no trade  
- Already in a position → no new entry  
- Loss limits hit → trading pauses  
- Order fails → logged, no blind retry  
- Paper mode is default — live requires `LIVE_TRADING=true` + `ARVOR_CONFIRM_LIVE_RISK=true`

**Never commit** `.env` or private keys to GitHub. Use Railway Variables only.

---

## ⚠️ Disclaimer

Educational software. **5× leverage** and **50% risk per trade** can cause rapid loss. Test in paper mode first. You are responsible for your keys, capital, and local laws.

---

<div align="center">

<br/>

**Arvor · Nested Fractal · Hyperliquid · AI Vision**

[shep95/arvor-automated-trading-bot](https://github.com/shep95/arvor-automated-trading-bot) · [ZorakCorp/arvor-automated-trading-bot](https://github.com/ZorakCorp/arvor-automated-trading-bot)

<br/>

[⭐ Star on GitHub](https://github.com/shep95/arvor-automated-trading-bot) · [Report an issue](https://github.com/shep95/arvor-automated-trading-bot/issues)

</div>
