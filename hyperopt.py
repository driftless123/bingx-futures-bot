"""
HYPEROPT — автоподбор параметров стратегии с ЧЕСТНОЙ проверкой.

Как работает:
  1. История делится на две части:
       ОБУЧЕНИЕ (первые ~70%)  — тут перебираются параметры
       ПРОВЕРКА (последние 30%) — эти данные при подборе НЕ используются
  2. Перебираются все комбинации параметров на ОБУЧЕНИИ
  3. Лучшие по метрике проверяются на ПРОВЕРКЕ (данные, которых он не видел)

Зачем так: если параметры хороши только на обучении, а на проверке
результат разваливается — значит они подогнаны под прошлое (переоптимизация)
и вживую работать не будут. Обычный подбор без такой проверки этого не покажет.

Запуск:   python hyperopt.py

Настройки ниже — что перебирать. Чем больше значений, тем дольше прогон
(время растёт как произведение всех вариантов).
"""

import asyncio
from itertools import product

import backtest as bt
from bot.config import Config
from bot.strategy import make_strategy
from bot.risk_manager import RiskManager

# ─────────────────────────── НАСТРОЙКИ ───────────────────────────

# Что перебирать (добавляй/убирай значения)
GRID = {
    "ADX_THRESHOLD":     [25.0, 30.0, 35.0, 40.0],
    "MIN_STRENGTH":      [0.71, 0.78, 0.86],
    "TRAILING_STOP_PCT": [0.015, 0.025, 0.035],
}

TRAIN_RATIO = 0.7        # доля истории на обучение (0.7 = 70% / 30%)
METRIC = "profit_factor" # по чему выбирать: "profit_factor" или "return_pct"
MIN_TRADES = 30          # меньше сделок — результат считаем шумом, отбрасываем
TOP_N = 5                # сколько лучших комбинаций проверять на отложенном куске

# Остальное берётся из backtest.py (TIMEFRAME, SYMBOLS, HISTORY_BARS,
# фильтр рынка и т.д.) — меняй там, чтобы совпадало с боевым конфигом.

# ─────────────────────────────────────────────────────────────────


class Hyperopt:
    def __init__(self):
        self.config = Config()
        self.config.TIMEFRAME = bt.TIMEFRAME
        self.config.STRATEGY_MODE = bt.STRATEGY_MODE
        self.tester = bt.Backtester(self.config)
        self.data = {}      # symbol -> (raw_sorted, df)
        self.split = {}     # symbol -> индекс разделения train/test

    async def load(self):
        """Скачиваем историю ОДИН раз, дальше гоняем комбинации по ней."""
        print(f"Загружаю историю ({bt.TIMEFRAME}, {bt.HISTORY_BARS} свечей)...")
        await self.tester._load_market_filter()

        for symbol in bt.SYMBOLS:
            raw = await self.tester.exchange.get_klines(
                symbol, bt.TIMEFRAME, limit=bt.HISTORY_BARS)
            if not raw or len(raw) < bt.WINDOW + 60:
                print(f"  {symbol}: нет данных, пропускаю")
                continue
            raw_sorted = sorted(raw, key=bt._sort_key)
            df = self.tester.strategy._parse_klines(raw_sorted)
            if df.empty:
                continue
            self.data[symbol] = (raw_sorted, df)
            n = len(raw_sorted)
            self.split[symbol] = bt.WINDOW + int((n - bt.WINDOW) * TRAIN_RATIO)

        await self.tester.exchange.close()
        if not self.data:
            print("Нет данных — прогон невозможен.")
            return False

        any_sym = next(iter(self.data))
        n = len(self.data[any_sym][0])
        sp = self.split[any_sym]
        print(f"Монет загружено: {len(self.data)}")
        print(f"Обучение: свечи {bt.WINDOW}-{sp} | Проверка: {sp}-{n}\n")
        return True

    def _apply(self, params):
        """Применить набор параметров к конфигу и пересобрать стратегию."""
        for key, val in params.items():
            setattr(self.config, key, val)
        # backtest читает эти два из своих глобальных переменных
        bt.ADX_THRESHOLD = self.config.ADX_THRESHOLD
        bt.MIN_STRENGTH = self.config.MIN_STRENGTH
        self.tester.strategy = make_strategy(self.config)
        self.tester.risk = RiskManager(self.config)

    def _run(self, train: bool):
        """Прогон всех монет на куске истории. train=True — обучение, иначе проверка."""
        all_trades = []
        for symbol, (raw_sorted, df) in self.data.items():
            sp = self.split[symbol]
            if train:
                trades = self.tester._simulate(symbol, raw_sorted, df, end_idx=sp)
            else:
                trades = self.tester._simulate(symbol, raw_sorted, df, start_idx=sp)
            all_trades.extend(trades)
        return self.tester._equity_stats(all_trades)

    def optimize(self):
        keys = list(GRID.keys())
        combos = list(product(*[GRID[k] for k in keys]))
        print(f"Комбинаций для перебора: {len(combos)}\n")
        print("─" * 62)

        results = []
        for idx, values in enumerate(combos, 1):
            params = dict(zip(keys, values))
            self._apply(params)
            stats = self._run(train=True)

            ok = stats["count"] >= MIN_TRADES
            score = stats[METRIC] if ok else -1
            if score == float("inf"):
                score = -1  # бесконечный PF = не было убытков, слишком мало данных
            results.append((score, params, stats))

            mark = "" if ok else "  (мало сделок)"
            pf = stats["profit_factor"]
            pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
            print(f"[{idx:>3}/{len(combos)}] "
                  f"ADX={params['ADX_THRESHOLD']:<5} "
                  f"сила={params['MIN_STRENGTH']:<5} "
                  f"трейл={params['TRAILING_STOP_PCT']:<6} "
                  f"→ сделок {stats['count']:>4}, PF {pf_s:>5}, "
                  f"PnL {stats['return_pct']:>+7.1f}%{mark}")

        results.sort(key=lambda r: r[0], reverse=True)
        return results

    def validate(self, results):
        """Проверяем лучших на отложенном куске истории."""
        good = [r for r in results if r[0] > 0][:TOP_N]
        if not good:
            print("\nНи одна комбинация не набрала достаточно сделок. "
                  "Уменьши MIN_TRADES или увеличь HISTORY_BARS.")
            return

        print("\n" + "═" * 62)
        print("ПРОВЕРКА ЛУЧШИХ НА ОТЛОЖЕННЫХ ДАННЫХ (их не было при подборе)")
        print("═" * 62)
        print(f"{'Параметры':<34}{'ОБУЧЕНИЕ':>13}{'ПРОВЕРКА':>14}")
        print(f"{'':34}{'PF / PnL':>13}{'PF / PnL':>14}")
        print("─" * 62)

        checked = []
        for score, params, tr_stats in good:
            self._apply(params)
            te_stats = self._run(train=False)
            checked.append((params, tr_stats, te_stats))

            def fmt(s):
                pf = s["profit_factor"]
                pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
                return f"{pf_s}/{s['return_pct']:+.0f}%"

            p = (f"ADX{params['ADX_THRESHOLD']:.0f} "
                 f"сила{params['MIN_STRENGTH']} "
                 f"трейл{params['TRAILING_STOP_PCT']}")
            print(f"{p:<34}{fmt(tr_stats):>13}{fmt(te_stats):>14}"
                  f"  ({te_stats['count']} сд.)")

        self._verdict(checked)

    def _verdict(self, checked):
        print("\n" + "─" * 62)
        params, tr, te = checked[0]
        tr_pf, te_pf = tr["profit_factor"], te["profit_factor"]

        print("ЧТО ЭТО ЗНАЧИТ:")
        if te["count"] < 10:
            print("  ⚠️  На проверке слишком мало сделок — выводы делать рано.")
        elif te_pf >= 1.3 and te_pf >= tr_pf * 0.7:
            print("  ✅ Лучший набор держится и на новых данных — это хороший знак.")
            print("     Параметры можно осторожно попробовать в бою.")
        elif te_pf < 1.0:
            print("  ❌ На проверке параметры УБЫТОЧНЫ — это переоптимизация.")
            print("     Они подогнаны под прошлое. В бой брать НЕЛЬЗЯ.")
        else:
            print("  ⚠️  На проверке результат заметно слабее, чем на обучении.")
            print("     Скорее всего частичная подгонка. Доверять осторожно.")

        print("\n  Смотри на колонку ПРОВЕРКА, а не ОБУЧЕНИЕ — обучение всегда")
        print("  выглядит красиво, это и есть ловушка автоподбора.")
        print("  Хороший признак: параметры-соседи по таблице дают похожий")
        print("  результат. Если лучший резко выделяется — это, скорее, случайность.")
        print("─" * 62)


async def main():
    print("\n" + "═" * 62)
    print("HYPEROPT — подбор параметров с проверкой на отложенных данных")
    print("═" * 62)
    print(f"Режим: {bt.STRATEGY_MODE} | TF={bt.TIMEFRAME} | "
          f"фильтр рынка: {'вкл' if bt.USE_MARKET_FILTER else 'выкл'}")
    print(f"Метрика отбора: {METRIC} | минимум сделок: {MIN_TRADES}\n")

    h = Hyperopt()
    if not await h.load():
        return
    results = h.optimize()
    h.validate(results)


if __name__ == "__main__":
    asyncio.run(main())
