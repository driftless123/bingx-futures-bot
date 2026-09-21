"""
═══════════════════════════════════════════════════════════════════════════
  BACKTEST — trend-following strategy on BingX historical data
═══════════════════════════════════════════════════════════════════════════

Runs the strategy over each coin's history and reports stats: win rate,
return, profit factor, max drawdown, number of trades.

This lets you VALIDATE settings against the past before risking real funds.

RUN:
    python backtest.py

What to tune (in the SETTINGS block below):
  - TIMEFRAME      — candle timeframe ('15m', '1h', '4h')
  - ADX_THRESHOLD  — trend-strength threshold
  - MIN_STRENGTH   — signal-strength threshold (0.0-1.0)
  - HISTORY_BARS   — how many candles of history to pull (up to ~1000)
  - FEE_PCT        — exchange fee per side (BingX taker ≈ 0.05%)
  - SKIP_WEEKENDS  — True = skip opening trades on Sat/Sun
  - STRATEGY_MODE  — "trend" / "mean_reversion" / "multi"
  - USE_MARKET_FILTER — only trade when the reference market is trending
  - SYMBOLS        — which coins to test

Run it several times with different settings and compare the results.
═══════════════════════════════════════════════════════════════════════════
"""

import asyncio
from bisect import bisect_right
from bot.config import Config
from bot.exchange import BingXClient
from bot.strategy import TrendStrategy, make_strategy
from bot.risk_manager import RiskManager

# ─────────────────────────── SETTINGS ───────────────────────────
TIMEFRAME = "4h"          # timeframe for the test
ADX_THRESHOLD = 25.0      # ADX threshold
MIN_STRENGTH = 0.75       # signal-strength threshold (0.0-1.0)
HISTORY_BARS = 1000       # how many candles of history (max ~1000-1440)
FEE_PCT = 0.0005          # fee per side (0.0005 = 0.05%)
SLIPPAGE_PCT = 0.0005     # slippage per side (0.0005 = 0.05%).
                          # Fill price is always slightly WORSE than requested —
                          # matches reality. Can be higher on illiquid pairs.
SKIP_WEEKENDS = False     # True = don't open trades on Saturday/Sunday
STRATEGY_MODE = "trend"   # "trend" / "mean_reversion" / "multi"
USE_MARKET_FILTER = True  # only trade when the reference market is trending
MARKET_FILTER_SYMBOL = "BTC-USDT"   # reference symbol for market state
MARKET_FILTER_TIMEFRAME = "1d"      # market timeframe (1d / 4h)
MARKET_FILTER_ADX = 20.0            # market ADX above -> trade
START_EQUITY = 1000.0     # starting simulated balance, $
WINDOW = 200               # rolling window for indicators (at least 100)

# Coins to test (trim the list for a faster run)
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "AVAX-USDT",
]
# ─────────────────────────────────────────────────────────────────


def _sort_key(k):
    """Ключ сортировки свечей по времени (поддержка dict и list)."""
    if isinstance(k, dict):
        return int(k.get("time") or k.get("timestamp") or 0)
    return int(k[0])


class Backtester:
    def __init__(self, config: Config):
        self.config = config
        self.exchange = BingXClient(
            api_key=config.BINGX_API_KEY,
            secret_key=config.BINGX_SECRET_KEY,
            base_url=config.BINGX_BASE_URL,
        )
        self.strategy = make_strategy(config)
        self.risk = RiskManager(config)
        self._mkt_ts = []      # метки времени свечей рынка (мс)
        self._mkt_adx = []     # ADX рынка на этих свечах
        self._bars_total = 0   # сколько баров просмотрено
        self._bars_blocked = 0 # сколько заблокировал фильтр рынка

    # Длительность таймфрейма в миллисекундах
    _TF_MS = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
        "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000,
    }

    async def _load_market_filter(self):
        """Загрузить ADX рынка (например BTC на дневке) для фильтра тренда."""
        if not USE_MARKET_FILTER:
            return
        raw = await self.exchange.get_klines(
            MARKET_FILTER_SYMBOL, MARKET_FILTER_TIMEFRAME, limit=1000
        )
        if not raw:
            print("⚠️  Нет данных по рынку — фильтр не применяется\n")
            return
        raw_sorted = sorted(raw, key=_sort_key)
        ind = TrendStrategy(self.config)
        dfm = ind._parse_klines(raw_sorted)
        if dfm.empty:
            print("⚠️  Нет данных по рынку — фильтр не применяется\n")
            return
        adx_series, _, _ = ind._adx(dfm, self.config.ADX_PERIOD)
        dfm = dfm.assign(adx=adx_series).dropna(subset=["adx"])
        self._mkt_ts = [int(t.value // 1_000_000) for t in dfm["timestamp"]]
        self._mkt_adx = [float(a) for a in dfm["adx"]]

    def _market_ok(self, bar_ts_ms: int) -> bool:
        """Трендовый ли был рынок на момент этого бара (без подглядывания вперёд)."""
        if not USE_MARKET_FILTER or not self._mkt_ts:
            return True
        # Берём последнюю ЗАКРЫВШУЮСЯ свечу рынка до этого момента
        tf_ms = self._TF_MS.get(MARKET_FILTER_TIMEFRAME, 86_400_000)
        idx = bisect_right(self._mkt_ts, bar_ts_ms - tf_ms) - 1
        if idx < 0:
            return True
        return self._mkt_adx[idx] >= MARKET_FILTER_ADX

    async def run(self):
        all_trades = []
        per_symbol = {}

        wk = "без выходных" if SKIP_WEEKENDS else "с выходными"
        mf = (f"фильтр рынка ВКЛ ({MARKET_FILTER_SYMBOL.replace('-USDT','')} "
              f"{MARKET_FILTER_TIMEFRAME}, ADX≥{MARKET_FILTER_ADX:.0f})"
              if USE_MARKET_FILTER else "фильтр рынка ВЫКЛ")
        print(f"\nРежим: {STRATEGY_MODE} | TF={TIMEFRAME}, ADX>{ADX_THRESHOLD}, "
              f"сила≥{MIN_STRENGTH}, свечей={HISTORY_BARS}, "
              f"комиссия={FEE_PCT*100:.3f}% + проскальзывание={SLIPPAGE_PCT*100:.3f}% "
              f"/сторону, {wk}\n{mf}\n")

        await self._load_market_filter()

        print(f"{'Монета':<12}{'Сделок':>7}{'Винрейт':>9}{'PnL %':>9}{'PF':>7}{'Просадка':>10}")
        print("─" * 54)

        for symbol in SYMBOLS:
            raw = await self.exchange.get_klines(symbol, TIMEFRAME, limit=HISTORY_BARS)
            if not raw or len(raw) < WINDOW + 10:
                print(f"{symbol:<12}{'нет данных':>43}")
                continue

            raw_sorted = sorted(raw, key=_sort_key)
            df = self.strategy._parse_klines(raw_sorted)
            if df.empty:
                print(f"{symbol:<12}{'нет данных':>43}")
                continue

            trades = self._simulate(symbol, raw_sorted, df)
            all_trades.extend(trades)
            stats = self._equity_stats(trades)
            per_symbol[symbol] = stats

            pf = stats["profit_factor"]
            pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
            print(f"{symbol:<12}{stats['count']:>7}{stats['win_rate']:>8.1f}%"
                  f"{stats['return_pct']:>+8.1f}%{pf_str:>7}{stats['max_dd']:>9.1f}%")

        # Итог по всем
        print("─" * 54)
        agg = self._equity_stats(all_trades)
        pf = agg["profit_factor"]
        pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(f"{'ИТОГО':<12}{agg['count']:>7}{agg['win_rate']:>8.1f}%"
              f"{agg['return_pct']:>+8.1f}%{pf_str:>7}{agg['max_dd']:>9.1f}%")
        print()
        self._verdict(agg)
        if USE_MARKET_FILTER and self._bars_total:
            blocked = self._bars_blocked / self._bars_total * 100
            print(f"  • Фильтр рынка не давал входить в {blocked:.0f}% случаев")
        await self.exchange.close()

    def _simulate(self, symbol, raw_sorted, df, start_idx=None, end_idx=None):
        """
        Прогон стратегии по истории одной монеты (без подглядывания вперёд).
        start_idx/end_idx — можно прогнать только кусок истории (для hyperopt).
        """
        trades = []
        position = None
        n = len(raw_sorted) if end_idx is None else min(end_idx, len(raw_sorted))
        i = WINDOW if start_idx is None else max(WINDOW, start_idx)

        while i < n:
            if position is None:
                # Пропускаем открытие в выходные, если включено
                if SKIP_WEEKENDS and df.iloc[i]["timestamp"].weekday() >= 5:
                    i += 1
                    continue
                # Фильтр рынка: не открываем, если рынок в боковике
                self._bars_total += 1
                bar_ts = int(df.iloc[i]["timestamp"].value // 1_000_000)
                if not self._market_ok(bar_ts):
                    self._bars_blocked += 1
                    i += 1
                    continue
                window = raw_sorted[i - WINDOW:i + 1]
                sig = self.strategy.analyze(symbol, window)
                if sig.direction in ("LONG", "SHORT"):
                    position = {
                        "dir": sig.direction,
                        "entry": sig.entry_price,
                        "sl": sig.stop_loss,
                        "tp": sig.take_profit,
                        "init_sl": sig.stop_loss,
                    }
                i += 1
            else:
                bar = df.iloc[i]
                high, low = float(bar["high"]), float(bar["low"])
                exit_price = None
                d = position["dir"]

                if d == "LONG":
                    # Сначала проверяем стоп/тейк по диапазону свечи (консервативно)
                    if low <= position["sl"]:
                        exit_price = position["sl"]
                    elif high >= position["tp"]:
                        exit_price = position["tp"]
                    else:
                        new_stop = self.risk.calculate_trailing_stop(
                            "LONG", high, position["entry"], position["sl"])
                        if new_stop:
                            position["sl"] = new_stop
                else:  # SHORT
                    if high >= position["sl"]:
                        exit_price = position["sl"]
                    elif low <= position["tp"]:
                        exit_price = position["tp"]
                    else:
                        new_stop = self.risk.calculate_trailing_stop(
                            "SHORT", low, position["entry"], position["sl"])
                        if new_stop:
                            position["sl"] = new_stop

                if exit_price is not None:
                    trades.append(self._close(position, exit_price))
                    position = None
                i += 1

        return trades

    def _close(self, position, exit_price):
        """Сформировать запись о закрытой сделке."""
        entry = position["entry"]
        d = position["dir"]
        # Проскальзывание: вход дороже, выход дешевле (для LONG), и наоборот
        if d == "LONG":
            eff_entry = entry * (1 + SLIPPAGE_PCT)
            eff_exit = exit_price * (1 - SLIPPAGE_PCT)
        else:
            eff_entry = entry * (1 - SLIPPAGE_PCT)
            eff_exit = exit_price * (1 + SLIPPAGE_PCT)

        move_pct = (eff_exit - eff_entry) / eff_entry
        if d == "SHORT":
            move_pct = -move_pct
        return {
            "dir": d,
            "entry": entry,
            "exit": exit_price,
            "init_sl": position["init_sl"],
            "move_pct": move_pct,   # доходность по цене (без учёта плеча)
        }

    def _equity_stats(self, trades):
        """Считаем кривую капитала и метрики по списку сделок."""
        if not trades:
            return {"count": 0, "win_rate": 0, "return_pct": 0,
                    "profit_factor": 0, "max_dd": 0}

        equity = START_EQUITY
        peak = equity
        max_dd = 0.0
        wins = losses = 0
        gross_profit = gross_loss = 0.0

        for t in trades:
            entry = t["entry"]
            stop_dist = abs(entry - t["init_sl"]) / entry
            if stop_dist <= 0:
                continue
            # Размер по риску (как в живом боте)
            risk_amount = equity * self.config.RISK_PER_TRADE
            notional = risk_amount / stop_dist
            notional = min(notional, equity * self.config.LEVERAGE * 0.95)

            gross = notional * t["move_pct"]
            fees = notional * FEE_PCT * 2  # вход + выход
            net = gross - fees
            equity += net

            if net >= 0:
                wins += 1
                gross_profit += net
            else:
                losses += 1
                gross_loss += abs(net)

            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        count = wins + losses
        return {
            "count": count,
            "win_rate": (wins / count * 100) if count else 0,
            "return_pct": (equity - START_EQUITY) / START_EQUITY * 100,
            "profit_factor": (gross_profit / gross_loss) if gross_loss > 0
                             else float("inf") if gross_profit > 0 else 0,
            "max_dd": max_dd,
            "final_equity": equity,
        }

    def _verdict(self, agg):
        """Короткий человеческий вывод по итогам."""
        if agg["count"] == 0:
            print("Сделок не было — попробуй другой таймфрейм или больше истории.")
            return
        print("Вывод:")
        print(f"  • Всего сделок: {agg['count']}, винрейт {agg['win_rate']:.1f}%")
        print(f"  • Итоговый результат: {agg['return_pct']:+.1f}% "
              f"(${START_EQUITY:.0f} → ${agg['final_equity']:.0f})")
        print(f"  • Макс. просадка: {agg['max_dd']:.1f}%")
        pf = agg["profit_factor"]
        if pf == float("inf"):
            print("  • Профит-фактор: ∞ (убытков не было — мало данных?)")
        else:
            print(f"  • Профит-фактор: {pf:.2f} "
                  f"({'прибыльно' if pf > 1 else 'убыточно'}; >1.5 — хорошо)")
        print()
        print("⚠️  Это прошлое, а не гарантия будущего. Тестируй на разных периодах")
        print("    и не подгоняй настройки слишком сильно под один отрезок истории.")


async def main():
    config = Config()
    # Применяем настройки бэктеста поверх конфига
    config.TIMEFRAME = TIMEFRAME
    config.ADX_THRESHOLD = ADX_THRESHOLD
    config.MIN_STRENGTH = MIN_STRENGTH
    config.STRATEGY_MODE = STRATEGY_MODE

    bt = Backtester(config)
    await bt.run()


if __name__ == "__main__":
    asyncio.run(main())
