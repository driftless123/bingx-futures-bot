# BingX Futures Trading Bot

Automated trading system for crypto futures on BingX. Runs multiple strategies in parallel across a 16-pair portfolio, with a market-regime filter that pauses trading in ranging conditions.

## Features

- **Multi-strategy execution** — different coins run trend-following or impulse-entry strategies simultaneously, based on backtested performance per pair
- **Market-regime filter** — ADX-based filter (threshold 35) pauses trading when the market lacks direction
- **Risk management** — trailing stops, per-strategy position sizing
- **Telegram control** — `/status`, `/balance`, `/positions` commands for monitoring without SSH access
- **Backtest-driven configuration** — coin list and strategy parameters chosen through iterative backtesting, not fixed defaults

## Architecture

- `strategy/` — strategy implementations (trend-following, impulse-entry) and the regime filter
- `exchange/` — BingX API client (REST/WebSocket)
- `execution/` — order management, position sizing, trailing stops
- `telegram/` — bot command handlers
- `backtest/` — backtesting engine and reports

## Stack

Python, asyncio, BingX API (REST/WebSocket), pandas, python-telegram-bot. Deployed on a Linux VPS with systemd.

## Status

Live, in production. Strategy parameters and coin list are tuned through ongoing backtesting.

## Note

This repository shows the system architecture. API keys, account configuration, and the exact strategy parameters are excluded (see `.gitignore`) and not published.
