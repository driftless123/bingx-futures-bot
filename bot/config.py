"""
Bot configuration — all settings in one place.

Note: the trading parameters below are conservative starting values for
demonstration, not the exact numbers used in live trading. Use backtest.py
to find and tune your own before running with real funds.
"""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # ═══════════════════════════════════════════
    # BingX API
    # ═══════════════════════════════════════════
    BINGX_API_KEY: str = field(default_factory=lambda: os.getenv("BINGX_API_KEY", ""))
    BINGX_SECRET_KEY: str = field(default_factory=lambda: os.getenv("BINGX_SECRET_KEY", ""))
    BINGX_BASE_URL: str = "https://open-api.bingx.com"

    # ═══════════════════════════════════════════
    # Telegram
    # ═══════════════════════════════════════════
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    TELEGRAM_CHAT_ID: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # ═══════════════════════════════════════════
    # Trading parameters
    # ═══════════════════════════════════════════
    # Example pair list — pick liquid perpetuals with tight spreads.
    # The bot skips any symbol not listed on BingX, and skips an order
    # if the position size would fall below the exchange minimum.
    # Format: "COIN-USDT".
    TRADING_PAIRS: List[str] = field(default_factory=lambda: [
        "BTC-USDT",
        "ETH-USDT",
        "SOL-USDT",
        "XRP-USDT",
        "DOGE-USDT",
        "AVAX-USDT",
    ])

    TIMEFRAME: str = "4h"            # Candle timeframe (1m, 5m, 15m, 1h, 4h)
    LEVERAGE: int = 3                # Leverage (1-125)
    MARGIN_TYPE: str = "ISOLATED"    # ISOLATED or CROSS
    HEDGE_MODE: bool = False         # True = hedge mode (separate LONG/SHORT),
                                     # False = one-way mode (BOTH).
                                     # Must match the account setting on BingX!

    # ═══════════════════════════════════════════
    # Risk management
    # ═══════════════════════════════════════════
    RISK_PER_TRADE: float = 0.01     # Risk per trade (1% of equity)
    MAX_OPEN_POSITIONS: int = 5      # Max simultaneous open positions
    STOP_LOSS_PCT: float = 0.02      # Stop-loss 2%
    TAKE_PROFIT_PCT: float = 0.04    # Take-profit 4% (1:2 ratio)
    TRAILING_STOP: bool = True       # Trailing stop
    TRAILING_STOP_PCT: float = 0.015 # Trailing stop distance 1.5%
    # Minimum step for the trailing stop to move (fraction of price).
    # 0.003 = 0.3%. Protects against frequent micro-updates that would
    # otherwise hammer the exchange API. Larger = fewer updates.
    TRAILING_STOP_MIN_STEP: float = 0.003

    # A single position per coin is always guaranteed. This setting sets
    # how many seconds to wait AFTER a position closes before re-entering
    # the same coin. 0 = can re-enter immediately on a new signal.
    REENTRY_COOLDOWN: int = 0

    # ═══════════════════════════════════════════
    # Strategy (trend-following)
    # ═══════════════════════════════════════════
    # EMA
    EMA_FAST: int = 9
    EMA_SLOW: int = 21
    EMA_TREND: int = 50

    # RSI
    RSI_PERIOD: int = 14
    RSI_OVERBOUGHT: float = 70.0
    RSI_OVERSOLD: float = 30.0

    # MACD
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9

    # ATR (for the dynamic stop)
    ATR_PERIOD: int = 14
    ATR_MULTIPLIER: float = 2.0

    # ADX (trend strength)
    ADX_PERIOD: int = 14
    ADX_THRESHOLD: float = 25.0      # Minimum trend strength required to enter

    # Signal-strength threshold (fraction of conditions met, 0..1).
    # Lower = more trades, lower average quality.
    # Higher = fewer trades, higher average quality.
    MIN_STRENGTH: float = 0.75

    # ═══════════════════════════════════════════
    # Strategy mode
    # ═══════════════════════════════════════════
    # "trend"          — trend-following only
    # "mean_reversion"  — mean-reversion only (for ranging markets)
    # "multi"          — auto-select by ADX (trend vs range)
    STRATEGY_MODE: str = "trend"

    # Regime thresholds for "multi" mode
    REGIME_TREND_ADX: float = 30.0   # ADX above -> trend -> trend-following
    REGIME_RANGE_ADX: float = 20.0   # ADX below -> range -> mean-reversion

    # Bollinger Bands (for mean-reversion)
    BB_PERIOD: int = 20
    BB_STD: float = 2.0

    # ═══════════════════════════════════════════
    # Market regime filter
    # ═══════════════════════════════════════════
    # The bot checks a reference symbol's ADX on a higher timeframe and
    # only opens NEW trades when the broader market is actually trending.
    # In a ranging market it manages open positions but stops opening new
    # ones — this is the guard against trading through choppy conditions.
    USE_MARKET_FILTER: bool = True          # on/off
    MARKET_FILTER_SYMBOL: str = "BTC-USDT"  # reference symbol for market state
    MARKET_FILTER_TIMEFRAME: str = "1d"     # timeframe (1d, 4h also usable)
    MARKET_FILTER_ADX: float = 20.0         # ADX above -> market trending -> trade

    # ═══════════════════════════════════════════
    # Drawdown protection
    # ═══════════════════════════════════════════
    # Pause after a streak of consecutive losses. Protects against a
    # "bad luck" stretch where market conditions don't suit the strategy.
    # 0 = disabled.
    MAX_CONSECUTIVE_LOSSES: int = 4      # this many losses in a row -> pause
    PAUSE_AFTER_LOSSES_HOURS: int = 6    # how many hours to pause for

    # Kill switch: if equity drops this much from its peak, the bot stops
    # opening new trades entirely (existing positions are still managed).
    # Last line of defense for the account. 0 = disabled.
    MAX_TOTAL_DRAWDOWN: float = 0.20     # 20% from peak

    # Volume
    VOLUME_MA_PERIOD: int = 20
    VOLUME_MULTIPLIER: float = 1.5   # Volume must be 1.5x the average

    # ═══════════════════════════════════════════
    # System settings
    # ═══════════════════════════════════════════
    SCAN_INTERVAL: int = 300         # Signal-check interval (seconds)
    LOG_LEVEL: str = "INFO"
    TESTNET: bool = False            # True for the exchange's testnet
    DRY_RUN: bool = False            # True = simulate, no real orders

    # ═══════════════════════════════════════════
    # Telegram notifications
    # ═══════════════════════════════════════════
    DAILY_REPORT_HOUR: int = 9              # Hour to send the daily summary (0-23)
    WEEKLY_REPORT_WEEKDAY: int = 0          # Weekday for the weekly summary (0=Mon)
    POSITION_STATUS_INTERVAL: int = 3600    # Position status every N seconds (0=off)
    HEARTBEAT_INTERVAL: int = 0             # "Bot alive" heartbeat every N seconds (0=off)
    NOTIFY_SIGNALS: bool = True             # Notify on every signal
    NOTIFY_TRAILING_STOP: bool = False      # Notify on trailing-stop moves
    DAILY_LIMIT_WARN_AT: float = 0.7        # Warn at 70% of the daily loss limit
