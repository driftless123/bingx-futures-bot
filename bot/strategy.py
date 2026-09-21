"""
Трендовая стратегия торговли
Комбинация: EMA + MACD + RSI + ADX + Volume
"""

import logging
from dataclasses import dataclass
from typing import Optional, List
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Торговый сигнал"""
    symbol: str
    direction: str          # LONG / SHORT / NONE
    strength: float         # Сила сигнала 0.0 - 1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str             # Описание сигнала
    indicators: dict        # Значения индикаторов


class TrendStrategy:
    """
    Трендовая стратегия:
    
    LONG сигнал:
    - EMA9 > EMA21 > EMA50 (восходящий тренд)
    - MACD линия > Signal линии и выше нуля
    - RSI 45-65 (не перекуплен)
    - ADX > 25 (сильный тренд)
    - Объём выше среднего
    
    SHORT сигнал:
    - EMA9 < EMA21 < EMA50 (нисходящий тренд)
    - MACD линия < Signal линии и ниже нуля
    - RSI 35-55 (не перепродан)
    - ADX > 25 (сильный тренд)
    - Объём выше среднего
    """

    def __init__(self, config):
        self.config = config

    def _parse_klines(self, klines: List) -> pd.DataFrame:
        """
        Парсинг свечей в DataFrame.
        Поддерживает ОБА формата BingX:
        - список словарей: [{"time":..,"open":..,"high":..,"low":..,"close":..,"volume":..}]
        - список списков:  [[time, open, high, low, close, volume], ...]
        """
        if not klines:
            return pd.DataFrame()

        first = klines[0]

        if isinstance(first, dict):
            # Формат "список словарей" (актуальный BingX v3)
            df = pd.DataFrame(klines)
            # Нормализуем имя колонки времени
            if "time" in df.columns:
                df = df.rename(columns={"time": "timestamp"})
            elif "t" in df.columns:
                df = df.rename(columns={"t": "timestamp"})
        else:
            # Формат "список списков"
            df = pd.DataFrame(klines, columns=[
                "timestamp", "open", "high", "low", "close", "volume"
            ])

        # Проверяем наличие нужных колонок
        required = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                logger.error(f"В свечах нет колонки '{col}'. Колонки: {list(df.columns)}")
                return pd.DataFrame()

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)
        return df

    # ═══════════════════════════════════════════
    # Индикаторы
    # ═══════════════════════════════════════════

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def _macd(self, series: pd.Series, fast: int, slow: int, signal: int):
        ema_fast = self._ema(series, fast)
        ema_slow = self._ema(series, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.ewm(span=period, adjust=False).mean()

    def _adx(self, df: pd.DataFrame, period: int):
        """Average Directional Index"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        plus_dm[(plus_dm > 0) & (plus_dm <= minus_dm)] = 0
        minus_dm[(minus_dm > 0) & (minus_dm <= plus_dm)] = 0

        atr = self._atr(df, period)
        plus_di = 100 * self._ema(plus_dm, period) / atr
        minus_di = 100 * self._ema(minus_dm, period) / atr

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = self._ema(dx, period)
        return adx, plus_di, minus_di

    def _volume_ma(self, series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period).mean()

    # ═══════════════════════════════════════════
    # Анализ сигнала
    # ═══════════════════════════════════════════

    def analyze(self, symbol: str, klines: List[list]) -> Signal:
        """Анализ рынка и генерация сигнала"""
        try:
            df = self._parse_klines(klines)

            if len(df) < 100:
                return self._no_signal(symbol, "Недостаточно данных")

            cfg = self.config

            # Вычисляем индикаторы
            df["ema_fast"] = self._ema(df["close"], cfg.EMA_FAST)
            df["ema_slow"] = self._ema(df["close"], cfg.EMA_SLOW)
            df["ema_trend"] = self._ema(df["close"], cfg.EMA_TREND)
            df["rsi"] = self._rsi(df["close"], cfg.RSI_PERIOD)
            df["macd"], df["macd_signal"], df["macd_hist"] = self._macd(
                df["close"], cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL
            )
            df["atr"] = self._atr(df, cfg.ATR_PERIOD)
            df["adx"], df["plus_di"], df["minus_di"] = self._adx(df, cfg.ADX_PERIOD)
            df["vol_ma"] = self._volume_ma(df["volume"], cfg.VOLUME_MA_PERIOD)

            # Последние значения
            last = df.iloc[-1]
            prev = df.iloc[-2]

            indicators = {
                "ema_fast": round(last["ema_fast"], 4),
                "ema_slow": round(last["ema_slow"], 4),
                "ema_trend": round(last["ema_trend"], 4),
                "rsi": round(last["rsi"], 2),
                "macd": round(last["macd"], 6),
                "macd_signal": round(last["macd_signal"], 6),
                "macd_hist": round(last["macd_hist"], 6),
                "adx": round(last["adx"], 2),
                "atr": round(last["atr"], 6),
                "volume": round(last["volume"], 2),
                "vol_ma": round(last["vol_ma"], 2),
                "price": round(last["close"], 4),
            }

            entry_price = last["close"]
            atr = last["atr"]

            # ─── LONG ────────────────────────────
            # Обязательные условия тренда (должны выполняться ВСЕ)
            long_mandatory = [
                last["ema_fast"] > last["ema_slow"] > last["ema_trend"],   # EMA выстроены вверх
                last["adx"] > cfg.ADX_THRESHOLD,                            # Сильный тренд
                last["plus_di"] > last["minus_di"],                         # Направление вверх
            ]
            # Подтверждения — держатся всё время, пока идёт тренд (не редкий кроссовер)
            long_confirmations = [
                last["macd"] > last["macd_signal"],                         # MACD бычий
                last["macd_hist"] > 0,                                      # Гистограмма MACD растёт
                50 < last["rsi"] < cfg.RSI_OVERBOUGHT,                      # RSI в бычьей зоне
                last["close"] > last["ema_fast"],                           # Цена выше быстрой EMA
                last["adx"] > prev["adx"],                                  # Тренд усиливается (ADX растёт)
                last["volume"] > last["vol_ma"] * cfg.VOLUME_MULTIPLIER,    # Всплеск объёма
            ]

            # ─── SHORT ───────────────────────────
            short_mandatory = [
                last["ema_fast"] < last["ema_slow"] < last["ema_trend"],   # EMA выстроены вниз
                last["adx"] > cfg.ADX_THRESHOLD,                            # Сильный тренд
                last["minus_di"] > last["plus_di"],                         # Направление вниз
            ]
            short_confirmations = [
                last["macd"] < last["macd_signal"],                         # MACD медвежий
                last["macd_hist"] < 0,                                      # Гистограмма MACD падает
                cfg.RSI_OVERSOLD < last["rsi"] < 50,                        # RSI в медвежьей зоне
                last["close"] < last["ema_fast"],                           # Цена ниже быстрой EMA
                last["adx"] > prev["adx"],                                  # Тренд усиливается (ADX растёт)
                last["volume"] > last["vol_ma"] * cfg.VOLUME_MULTIPLIER,    # Всплеск объёма
            ]

            # Сила сигнала = доля выполненных условий (обязательные + подтверждения), 0..1
            total = len(long_mandatory) + len(long_confirmations)   # = 9
            long_score = (sum(long_mandatory) + sum(long_confirmations)) / total
            short_score = (sum(short_mandatory) + sum(short_confirmations)) / total

            # Порог входа (по умолчанию 0.86 = 86%). Вход только если ВСЕ обязательные
            # выполнены И общая сила сигнала не ниже порога.
            min_strength = getattr(cfg, "MIN_STRENGTH", 0.86)
            long_ok = all(long_mandatory) and long_score >= min_strength
            short_ok = all(short_mandatory) and short_score >= min_strength

            if long_ok:
                sl = entry_price - atr * cfg.ATR_MULTIPLIER
                tp = entry_price + atr * cfg.ATR_MULTIPLIER * 2
                return Signal(
                    symbol=symbol,
                    direction="LONG",
                    strength=long_score,
                    entry_price=entry_price,
                    stop_loss=round(sl, 4),
                    take_profit=round(tp, 4),
                    reason=f"Трендовый LONG | EMA↑ MACD↑ ADX={last['adx']:.1f} RSI={last['rsi']:.1f}",
                    indicators=indicators
                )

            if short_ok:
                sl = entry_price + atr * cfg.ATR_MULTIPLIER
                tp = entry_price - atr * cfg.ATR_MULTIPLIER * 2
                return Signal(
                    symbol=symbol,
                    direction="SHORT",
                    strength=short_score,
                    entry_price=entry_price,
                    stop_loss=round(sl, 4),
                    take_profit=round(tp, 4),
                    reason=f"Трендовый SHORT | EMA↓ MACD↓ ADX={last['adx']:.1f} RSI={last['rsi']:.1f}",
                    indicators=indicators
                )

            return self._no_signal(
                symbol,
                f"Нет сигнала | LONG:{long_score:.0%} SHORT:{short_score:.0%}",
                indicators
            )

        except Exception as e:
            logger.error(f"Ошибка анализа {symbol}: {e}")
            return self._no_signal(symbol, f"Ошибка: {e}")

    def _no_signal(self, symbol: str, reason: str, indicators: dict = None) -> Signal:
        return Signal(
            symbol=symbol,
            direction="NONE",
            strength=0.0,
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            reason=reason,
            indicators=indicators or {}
        )

# ═══════════════════════════════════════════════════════════════════
#  Мин-реверсия (mean reversion) — работает в боковике
# ═══════════════════════════════════════════════════════════════════

class MeanReversionStrategy:
    """
    Стратегия возврата к среднему. Работает в БОКОВИКЕ.
    Покупает у нижней полосы Боллинджера при перепроданности (RSI низкий),
    продаёт у верхней при перекупленности. Цель — середина (средняя).
    Стоп — по ATR (защита, если коридор сломался в тренд).
    """

    def __init__(self, config):
        self.config = config
        self._ind = TrendStrategy(config)  # переиспользуем расчёт индикаторов

    def _parse_klines(self, klines):
        return self._ind._parse_klines(klines)

    def analyze(self, symbol: str, klines) -> Signal:
        cfg = self.config
        df = self._ind._parse_klines(klines)
        if df.empty or len(df) < 60:
            return self._ind._no_signal(symbol, "Недостаточно данных")

        try:
            df["rsi"] = self._ind._rsi(df["close"], cfg.RSI_PERIOD)
            df["atr"] = self._ind._atr(df, cfg.ATR_PERIOD)

            # Полосы Боллинджера
            period = getattr(cfg, "BB_PERIOD", 20)
            k = getattr(cfg, "BB_STD", 2.0)
            ma = df["close"].rolling(period).mean()
            std = df["close"].rolling(period).std()
            df["bb_mid"] = ma
            df["bb_upper"] = ma + k * std
            df["bb_lower"] = ma - k * std

            df = df.dropna().reset_index(drop=True)
            if len(df) < 2:
                return self._ind._no_signal(symbol, "Недостаточно данных")

            last = df.iloc[-1]
            entry = last["close"]
            atr = last["atr"]

            indicators = {
                "rsi": round(last["rsi"], 2),
                "price": round(entry, 6),
                "bb_lower": round(last["bb_lower"], 6),
                "bb_mid": round(last["bb_mid"], 6),
                "bb_upper": round(last["bb_upper"], 6),
            }

            # Сила сигнала — насколько RSI ушёл в крайность (0.5..1.0)
            strength = min(1.0, max(0.5, abs(50 - last["rsi"]) / 40))

            # LONG: цена у нижней полосы И перепроданность -> ждём отскок к середине
            long_ok = (
                entry <= last["bb_lower"]
                and last["rsi"] < cfg.RSI_OVERSOLD
                and last["bb_mid"] > entry
            )
            # SHORT: цена у верхней полосы И перекупленность -> ждём откат к середине
            short_ok = (
                entry >= last["bb_upper"]
                and last["rsi"] > cfg.RSI_OVERBOUGHT
                and last["bb_mid"] < entry
            )

            if long_ok:
                sl = entry - atr * cfg.ATR_MULTIPLIER
                tp = last["bb_mid"]  # цель — средняя линия
                return Signal(
                    symbol=symbol, direction="LONG", strength=strength,
                    entry_price=entry, stop_loss=round(sl, 6), take_profit=round(tp, 6),
                    reason=f"Реверсия LONG | у нижней полосы, RSI={last['rsi']:.1f}",
                    indicators=indicators
                )

            if short_ok:
                sl = entry + atr * cfg.ATR_MULTIPLIER
                tp = last["bb_mid"]
                return Signal(
                    symbol=symbol, direction="SHORT", strength=strength,
                    entry_price=entry, stop_loss=round(sl, 6), take_profit=round(tp, 6),
                    reason=f"Реверсия SHORT | у верхней полосы, RSI={last['rsi']:.1f}",
                    indicators=indicators
                )

            return self._ind._no_signal(symbol, "Нет реверсии", indicators)

        except Exception as e:
            logger.error(f"Ошибка анализа реверсии {symbol}: {e}")
            return self._ind._no_signal(symbol, f"Ошибка: {e}")


# ═══════════════════════════════════════════════════════════════════
#  Мульти-стратегия — выбирает стратегию по режиму рынка (ADX)
# ═══════════════════════════════════════════════════════════════════

class MultiStrategy:
    """
    Роутер: по ADX определяет режим рынка и включает нужную стратегию.
      ADX >= REGIME_TREND_ADX  -> ТРЕНД    -> трендовая
      ADX <= REGIME_RANGE_ADX  -> БОКОВИК  -> мин-реверсия
      между ними               -> неясно  -> не торгуем
    """

    def __init__(self, config):
        self.config = config
        self.trend = TrendStrategy(config)
        self.mr = MeanReversionStrategy(config)
        self._ind = self.trend

    def _parse_klines(self, klines):
        return self._ind._parse_klines(klines)

    def analyze(self, symbol: str, klines) -> Signal:
        cfg = self.config
        df = self._ind._parse_klines(klines)
        if df.empty or len(df) < 60:
            return self._ind._no_signal(symbol, "Недостаточно данных")

        try:
            adx_series, _, _ = self._ind._adx(df, cfg.ADX_PERIOD)
            adx = float(adx_series.iloc[-1])
        except Exception as e:
            logger.error(f"Ошибка ADX для режима {symbol}: {e}")
            return self._ind._no_signal(symbol, f"Ошибка: {e}")

        trend_adx = getattr(cfg, "REGIME_TREND_ADX", 30.0)
        range_adx = getattr(cfg, "REGIME_RANGE_ADX", 20.0)

        if adx >= trend_adx:
            return self.trend.analyze(symbol, klines)      # тренд -> трендовая
        elif adx <= range_adx:
            return self.mr.analyze(symbol, klines)          # боковик -> реверсия
        else:
            return self._ind._no_signal(symbol, f"Неясный режим (ADX={adx:.1f})")


def make_strategy(config):
    """Фабрика: возвращает стратегию по config.STRATEGY_MODE."""
    mode = getattr(config, "STRATEGY_MODE", "trend")
    if mode == "mean_reversion":
        return MeanReversionStrategy(config)
    if mode == "multi":
        return MultiStrategy(config)
    return TrendStrategy(config)
