# Copy this file to config.py and fill in your own values.
# config.py is git-ignored and must never be committed.

# --- Exchange API credentials ---
BINGX_API_KEY = "your-api-key-here"
BINGX_API_SECRET = "your-api-secret-here"

# --- Telegram ---
TELEGRAM_BOT_TOKEN = "your-telegram-bot-token"
TELEGRAM_CHAT_ID = "your-chat-id"

# --- Trading pairs ---
COIN_LIST = [
    "BTC-USDT",
    "ETH-USDT",
    # add the rest of your backtested pairs here
]

# --- Strategy parameters ---
TIMEFRAME = "4h"
ADX_THRESHOLD = 35          # regime filter: below this, trading pauses
TRAILING_STOP_PCT = 1.5     # example only — tune to your own backtests

# --- Strategy assignment per coin (example) ---
STRATEGY_MAP = {
    "BTC-USDT": "trend_following",
    "ETH-USDT": "impulse_entry",
}
