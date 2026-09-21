# BingX Futures Trading Bot

A trend-following trading bot for BingX Futures, with Telegram notifications, a market-regime filter, and a backtesting engine for validating settings before going live.

> The parameter values shown in this repo (indicator thresholds, risk settings, pair list) are illustrative defaults, not the exact numbers used in live trading. Use `backtest.py` to find and validate your own.

## Project structure

```
bingx_trading_bot/
├── main.py              # Entry point
├── backtest.py          # Backtests the strategy against historical data
├── hyperopt.py           # Parameter search over the strategy's settings
├── requirements.txt     # Dependencies
├── .env.example         # Settings template (copy to .env)
├── .gitignore            # What never gets committed (keys, logs)
├── setup_vps.sh          # VPS bootstrap script
├── deploy/                # systemd service files
├── DEPLOY_FORPSI.md       # Deployment guide (Forpsi VPS)
├── DEPLOY_ORACLE.md       # Deployment guide (Oracle Cloud free tier)
├── logs/                  # Runtime logs and trade history (created at runtime)
└── bot/                   # Core package
    ├── config.py          # All bot settings
    ├── exchange.py        # BingX API client
    ├── strategy.py         # Trend-following strategy
    ├── risk_manager.py     # Position sizing and risk limits
    ├── stats.py            # Trade statistics tracking
    ├── notifier.py         # Telegram notifications
    └── trading_bot.py      # Main class wiring everything together
```

Each module owns one responsibility, which keeps the bot easy to read and to extend. All tunable settings live in `bot/config.py`.

## Setup

### 1. Python 3.11+
```bash
python --version
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure API keys

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Fill in:
```
BINGX_API_KEY=your_key
BINGX_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Get Telegram credentials
1. Open Telegram and find **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **token**
4. Message your new bot `/start`
5. Get your chat ID via `https://api.telegram.org/bot<TOKEN>/getUpdates`

## Running

### Simulation mode (no real orders — start here)
In `bot/config.py`, set:
```python
DRY_RUN: bool = True
```

Run:
```bash
python main.py
```

### Live trading
Once you've verified it in simulation:
```python
DRY_RUN: bool = False
```

## Strategy settings

All parameters live in `bot/config.py`:

| Parameter | Example | Description |
|---|---|---|
| `TRADING_PAIRS` | 6 coins | Pairs to scan |
| `TIMEFRAME` | 4h | Candle timeframe |
| `LEVERAGE` | 3 | Leverage |
| `RISK_PER_TRADE` | 1% | Risk per trade |
| `MAX_OPEN_POSITIONS` | 5 | Max simultaneous positions |
| `REENTRY_COOLDOWN` | 0 | Cooldown before re-entering a coin (seconds) |
| `STOP_LOSS_PCT` | 2% | Stop-loss |
| `TAKE_PROFIT_PCT` | 4% | Take-profit |
| `TRAILING_STOP` | True | Trailing stop |

**One position per coin is always guaranteed** — the bot won't open a second trade on a symbol while one is already open. `REENTRY_COOLDOWN` adds an optional extra pause after a position closes before it can be entered again (0 = can re-enter immediately on a new signal).

## Strategy (trend-following)

The bot opens a position once enough of a fixed set of conditions align:

**LONG:**
- EMA9 > EMA21 > EMA50 (uptrend)
- MACD crosses above its signal line
- MACD above zero
- RSI within its normal range
- ADX above the configured threshold (trend strength)
- +DI > -DI
- Volume above its moving average

**SHORT:** — mirrored conditions

A **market-regime filter** checks a reference symbol's ADX on a higher timeframe and only allows new entries when the broader market is actually trending; in a ranging market it manages open positions but stops opening new ones.

## Telegram notifications

The bot reports on: startup and connectivity (balance, open positions found), shutdown, an optional heartbeat; new signals, position opens/closes (stop-loss / take-profit) with a running daily tally, trailing-stop moves, rejected orders; daily and weekly summaries (trade count, win rate, PnL, profit factor, best/worst trade); periodic status of open positions with floating PnL; warnings as the daily loss limit approaches, and error alerts.

Configurable in `bot/config.py`: report timing, status interval, heartbeat, which notifications are sent.

Trade history is persisted to `logs/trades.json` and survives restarts — daily/weekly summaries are computed from real history, not just the current session.

## Backtesting

Before changing live settings, validate them against historical data:

```bash
python backtest.py
```

It pulls history from BingX, runs the strategy over it, and reports per-coin and aggregate: trade count, win rate, return, profit factor, and max drawdown.

Test settings live in a block at the top of `backtest.py` (timeframe, ADX threshold, signal-strength threshold, history length, fee/slippage, symbol list). Run it multiple times with different settings and compare results before moving a change to `bot/config.py`.

**Note:** good historical numbers don't guarantee future profit. Avoid over-fitting to one stretch of data — validate across different periods and coins. Look for a profit factor comfortably above 1 and a drawdown you could actually tolerate live.

## Deployment

`DEPLOY_FORPSI.md` and `DEPLOY_ORACLE.md` walk through running the bot 24/7 on a Linux VPS (SSH access, dependencies, systemd service) so it doesn't depend on your own machine staying on. `deploy/` holds the systemd unit files; `setup_vps.sh` automates the initial server setup.

## Important warnings

1. **Always start with `DRY_RUN = True`**
2. Never trade money you can't afford to lose
3. Test with small size before running at full scale
4. Monitor the bot — it isn't infallible
5. Past results don't guarantee future returns

## Risk management

- Per-trade risk sized as a percentage of equity
- A hard cap on simultaneous open positions
- A daily loss limit
- Trailing stop to protect open profit
- ATR-based dynamic stops
