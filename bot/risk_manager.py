"""
Управление рисками и расчёт размера позиции
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Управляет рисками:
    - Размер позиции по % риска от депозита
    - Максимум открытых позиций
    - Трейлинг стоп
    - Дневной лимит потерь
    """

    def __init__(self, config):
        self.config = config
        self.daily_loss = 0.0
        self.daily_loss_limit = 0.06  # 6% в день максимум
        self.trade_count_today = 0
        # Защита от серии убытков
        self.consecutive_losses = 0
        self.paused_until = None       # пауза после серии минусов
        # Kill switch по общей просадке
        self.peak_balance = 0.0
        self.halted = False            # True = торговля остановлена совсем

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss: float,
        leverage: int
    ) -> float:
        """
        Расчёт размера позиции по фиксированному % риска
        
        Формула: size = (balance * risk%) / (|entry - stop_loss| / entry)
        """
        if entry_price <= 0 or stop_loss <= 0:
            return 0.0

        risk_amount = balance * self.config.RISK_PER_TRADE
        price_diff_pct = abs(entry_price - stop_loss) / entry_price

        if price_diff_pct == 0:
            return 0.0

        # Размер позиции в USDT
        position_usdt = risk_amount / price_diff_pct

        # С учётом плеча (не превышаем доступный капитал * плечо)
        max_position = balance * leverage
        position_usdt = min(position_usdt, max_position * 0.95)  # 95% от макс

        # Размер в монетах
        size = position_usdt / entry_price

        logger.info(
            f"Размер позиции: {size:.4f} | "
            f"Риск: ${risk_amount:.2f} | "
            f"Стоп: {price_diff_pct:.2%}"
        )

        return round(size, 4)

    def can_open_position(
        self,
        open_positions_count: int,
        symbol: str,
        existing_symbols: list
    ) -> tuple[bool, str]:
        """Проверка возможности открытия позиции"""

        if symbol in existing_symbols:
            return False, f"Позиция по {symbol} уже открыта"

        # Kill switch: счёт просел слишком сильно от максимума
        if self.halted:
            return False, "Торговля остановлена (достигнут лимит общей просадки)"

        # Пауза после серии убытков
        if self.paused_until and datetime.now() < self.paused_until:
            left = int((self.paused_until - datetime.now()).total_seconds() / 60)
            return False, f"Пауза после серии убытков (ещё {left} мин)"

        if open_positions_count >= self.config.MAX_OPEN_POSITIONS:
            return False, f"Достигнут лимит позиций ({self.config.MAX_OPEN_POSITIONS})"

        if self.daily_loss >= self.daily_loss_limit:
            return False, f"Достигнут дневной лимит потерь ({self.daily_loss:.1%})"

        return True, "OK"

    def register_trade_result(self, pnl: float) -> str:
        """
        Учесть результат закрытой сделки. Возвращает текст события,
        если сработала защита (иначе пустую строку).
        """
        if pnl < 0:
            self.consecutive_losses += 1
            limit = getattr(self.config, "MAX_CONSECUTIVE_LOSSES", 0)
            if limit and self.consecutive_losses >= limit:
                hours = getattr(self.config, "PAUSE_AFTER_LOSSES_HOURS", 4)
                self.paused_until = datetime.now() + timedelta(hours=hours)
                self.consecutive_losses = 0
                return (f"⏸ {limit} убытка подряд — пауза на {hours} ч. "
                        f"Открытые позиции ведутся как обычно.")
        else:
            self.consecutive_losses = 0
        return ""

    def check_drawdown(self, balance: float) -> str:
        """
        Kill switch: следим за просадкой от максимума счёта.
        Возвращает текст события, если торговля остановлена.
        """
        limit = getattr(self.config, "MAX_TOTAL_DRAWDOWN", 0)
        if not limit or balance <= 0:
            return ""
        if balance > self.peak_balance:
            self.peak_balance = balance
            return ""
        if self.peak_balance <= 0 or self.halted:
            return ""
        dd = (self.peak_balance - balance) / self.peak_balance
        if dd >= limit:
            self.halted = True
            return (f"🛑 СТОП: просадка {dd:.1%} от максимума "
                    f"(${self.peak_balance:.2f} → ${balance:.2f}). "
                    f"Новые сделки остановлены. Проверь стратегию перед возобновлением.")
        return ""

    def calculate_trailing_stop(
        self,
        direction: str,
        current_price: float,
        entry_price: float,
        current_stop: float
    ) -> Optional[float]:
        """
        Расчёт трейлинг стопа.
        Двигает стоп только в сторону прибыли И только если он сдвинется
        хотя бы на TRAILING_STOP_MIN_STEP (защита от частых микро-обновлений,
        которые зря нагружают API биржи).
        """
        if not self.config.TRAILING_STOP:
            return None

        trail_pct = self.config.TRAILING_STOP_PCT
        min_step = self.config.TRAILING_STOP_MIN_STEP  # минимальное движение, доля

        if direction == "LONG":
            new_stop = current_price * (1 - trail_pct)
            # Двигаем, только если стоп поднимется заметно (≥ min_step)
            if new_stop > current_stop * (1 + min_step):
                return round(new_stop, 4)

        elif direction == "SHORT":
            new_stop = current_price * (1 + trail_pct)
            # Двигаем, только если стоп опустится заметно (≥ min_step)
            if new_stop < current_stop * (1 - min_step):
                return round(new_stop, 4)

        return None

    def update_daily_pnl(self, net_pnl: float, start_balance: float):
        """
        Обновить дневной убыток на основе ЧИСТОГО результата за день.
        net_pnl — чистая прибыль/убыток за день в USDT (может быть + или −).
        Лимит считается ТОЛЬКО от чистого убытка: прибыльный день не копит лимит.
        """
        if start_balance and start_balance > 0 and net_pnl < 0:
            self.daily_loss = abs(net_pnl) / start_balance
        else:
            self.daily_loss = 0.0

    def reset_daily_stats(self):
        """Сброс дневной статистики"""
        self.daily_loss = 0.0
        self.trade_count_today = 0
        logger.info("Дневная статистика сброшена")

    def round_quantity(self, quantity: float, step: float) -> float:
        """Округление количества до шага лота"""
        if step <= 0:
            return quantity
        return round(round(quantity / step) * step, 8)

    def round_price(self, price: float, tick: float) -> float:
        """Округление цены до тика"""
        if tick <= 0:
            return price
        return round(round(price / tick) * tick, 8)
