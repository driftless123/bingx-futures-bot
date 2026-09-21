"""
Учёт статистики сделок — для итогов дня/недели и аналитики
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Запись об одной закрытой сделке"""
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    reason: str
    opened_at: str
    closed_at: str


class TradeStats:
    """
    Копит закрытые сделки и считает агрегаты:
    - за день, за неделю, за всё время
    - win rate, profit factor, лучшая/худшая сделка и т.д.
    Сохраняет историю на диск (trades.json), чтобы переживать перезапуск.
    """

    def __init__(self, storage_path: str = "logs/trades.json"):
        self.storage_path = Path(storage_path)
        self.trades: List[TradeRecord] = []
        self.session_start_balance: Optional[float] = None
        self.day_start_balance: Optional[float] = None
        self.current_day: date = datetime.now().date()
        self._load()

    # ═══════════════════════════════════════════
    # Сохранение / загрузка
    # ═══════════════════════════════════════════

    def _load(self):
        """Загрузить историю с диска"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.trades = [TradeRecord(**t) for t in data.get("trades", [])]
                logger.info(f"Загружено {len(self.trades)} сделок из истории")
            except Exception as e:
                logger.error(f"Ошибка загрузки истории сделок: {e}")

    def _save(self):
        """Сохранить историю на диск"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"trades": [asdict(t) for t in self.trades]},
                    f, ensure_ascii=False, indent=2
                )
        except Exception as e:
            logger.error(f"Ошибка сохранения истории сделок: {e}")

    # ═══════════════════════════════════════════
    # Запись сделок
    # ═══════════════════════════════════════════

    def record_trade(self, trade: TradeRecord):
        """Добавить закрытую сделку"""
        self.trades.append(trade)
        self._save()
        logger.info(f"Сделка записана: {trade.symbol} PnL={trade.pnl:.2f}")

    def set_session_start_balance(self, balance: float):
        """Зафиксировать стартовый баланс сессии"""
        self.session_start_balance = balance
        if self.day_start_balance is None:
            self.day_start_balance = balance

    def set_day_start_balance(self, balance: float):
        """Зафиксировать баланс на начало дня"""
        self.day_start_balance = balance
        self.current_day = datetime.now().date()

    # ═══════════════════════════════════════════
    # Фильтры по периодам
    # ═══════════════════════════════════════════

    def _trades_for_day(self, day: date) -> List[TradeRecord]:
        result = []
        for t in self.trades:
            try:
                t_date = datetime.fromisoformat(t.closed_at).date()
                if t_date == day:
                    result.append(t)
            except (ValueError, TypeError):
                continue
        return result

    def _trades_last_n_days(self, n: int) -> List[TradeRecord]:
        from datetime import timedelta
        cutoff = datetime.now().date() - timedelta(days=n)
        result = []
        for t in self.trades:
            try:
                t_date = datetime.fromisoformat(t.closed_at).date()
                if t_date >= cutoff:
                    result.append(t)
            except (ValueError, TypeError):
                continue
        return result

    # ═══════════════════════════════════════════
    # Расчёт агрегатов
    # ═══════════════════════════════════════════

    @staticmethod
    def _compute(trades: List[TradeRecord]) -> dict:
        """Посчитать метрики по списку сделок"""
        if not trades:
            return {
                "count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "total_pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
                "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "best": None, "worst": None,
            }

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        total_pnl = sum(t.pnl for t in trades)

        return {
            "count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
            "total_pnl": total_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
            "avg_win": (gross_profit / len(wins)) if wins else 0.0,
            "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
            "best": max(trades, key=lambda t: t.pnl) if trades else None,
            "worst": min(trades, key=lambda t: t.pnl) if trades else None,
        }

    def day_summary(self, day: Optional[date] = None) -> dict:
        """Метрики за календарный день"""
        day = day or datetime.now().date()
        return self._compute(self._trades_for_day(day))

    def period_summary(self, start_dt: datetime, end_dt: datetime = None) -> dict:
        """
        Метрики за произвольный период [start_dt, end_dt).
        Используется для «торгового дня», привязанного к 9:00.
        """
        end_dt = end_dt or datetime.now()
        result = []
        for t in self.trades:
            try:
                t_dt = datetime.fromisoformat(t.closed_at)
                if start_dt <= t_dt < end_dt:
                    result.append(t)
            except (ValueError, TypeError):
                continue
        return self._compute(result)

    def week_summary(self) -> dict:
        """Метрики за 7 дней"""
        return self._compute(self._trades_last_n_days(7))

    def all_time_summary(self) -> dict:
        """Метрики за всё время"""
        return self._compute(self.trades)
