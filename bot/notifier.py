"""
Telegram уведомления
"""

import logging
import aiohttp
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Отправка уведомлений в Telegram"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{token}"

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Отправить сообщение"""
        url = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    result = await resp.json()
                    if not result.get("ok"):
                        logger.error(f"Telegram ошибка: {result}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"Ошибка отправки Telegram: {e}")
            return False

    def _now(self) -> str:
        return datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    # ═══════════════════════════════════════════
    # Шаблоны сообщений
    # ═══════════════════════════════════════════

    async def notify_bot_start(self, pairs: list, config):
        """Бот запущен"""
        pairs_str = " | ".join(pairs)
        text = (
            f"🤖 <b>BingX Trading Bot запущен</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 Время: {self._now()}\n"
            f"📊 Пары: <code>{pairs_str}</code>\n"
            f"⏱ Таймфрейм: <b>{config.TIMEFRAME}</b>\n"
            f"🔧 Плечо: <b>x{config.LEVERAGE}</b>\n"
            f"💰 Риск/сделку: <b>{config.RISK_PER_TRADE * 100:.1f}%</b>\n"
            f"📉 Стоп-лосс: <b>{config.STOP_LOSS_PCT * 100:.1f}%</b>\n"
            f"📈 Тейк-профит: <b>{config.TAKE_PROFIT_PCT * 100:.1f}%</b>\n"
            f"🔄 Режим: <b>{'СИМУЛЯЦИЯ 🧪' if config.DRY_RUN else 'РЕАЛЬНАЯ ТОРГОВЛЯ 💸'}</b>"
        )
        await self.send(text)

    async def notify_bot_stop(self):
        """Бот остановлен"""
        text = (
            f"⛔ <b>BingX Trading Bot остановлен</b>\n"
            f"🕐 Время: {self._now()}"
        )
        await self.send(text)

    async def notify_signal(self, symbol: str, direction: str, strength: float,
                             entry: float, sl: float, tp: float, reason: str,
                             indicators: dict):
        """Новый торговый сигнал"""
        emoji = "🟢" if direction == "LONG" else "🔴"
        arrow = "📈" if direction == "LONG" else "📉"

        sl_pct = abs(entry - sl) / entry * 100
        tp_pct = abs(tp - entry) / entry * 100
        rr = tp_pct / sl_pct if sl_pct > 0 else 0

        text = (
            f"{emoji} <b>СИГНАЛ: {direction} {symbol}</b> {arrow}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Вход: <code>{entry}</code>\n"
            f"🛑 Стоп-лосс: <code>{sl}</code> (-{sl_pct:.2f}%)\n"
            f"🎯 Тейк-профит: <code>{tp}</code> (+{tp_pct:.2f}%)\n"
            f"⚖️ RR соотношение: <b>1:{rr:.1f}</b>\n"
            f"💪 Сила сигнала: <b>{strength:.0%}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Индикаторы:</b>\n"
            f"   RSI: <code>{indicators.get('rsi', '-')}</code>\n"
            f"   ADX: <code>{indicators.get('adx', '-')}</code>\n"
            f"   MACD: <code>{indicators.get('macd_hist', '-')}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 {reason}\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_order_opened(self, symbol: str, direction: str, quantity: float,
                                   entry: float, sl: float, tp: float,
                                   balance: float, dry_run: bool = False):
        """Позиция открыта"""
        emoji = "🟢" if direction == "LONG" else "🔴"
        mode = "🧪 СИМУЛЯЦИЯ" if dry_run else "✅ ИСПОЛНЕНО"
        position_usdt = quantity * entry

        text = (
            f"{emoji} <b>ПОЗИЦИЯ ОТКРЫТА — {direction} {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Статус: <b>{mode}</b>\n"
            f"💵 Цена входа: <code>{entry}</code>\n"
            f"📦 Объём: <code>{quantity}</code> (~${position_usdt:.2f})\n"
            f"🛑 Стоп-лосс: <code>{sl}</code>\n"
            f"🎯 Тейк-профит: <code>{tp}</code>\n"
            f"💼 Баланс: <code>${balance:.2f}</code>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_position_closed(self, symbol: str, direction: str,
                                      entry: float, exit_price: float,
                                      quantity: float, pnl: float,
                                      reason: str, dry_run: bool = False,
                                      day_stats: dict = None):
        """Позиция закрыта"""
        pnl_pct = (exit_price - entry) / entry * 100
        if direction == "SHORT":
            pnl_pct = -pnl_pct

        emoji = "💚" if pnl >= 0 else "❤️"
        pnl_sign = "+" if pnl >= 0 else ""
        mode = "🧪 СИМУЛЯЦИЯ" if dry_run else ""

        text = (
            f"{emoji} <b>ПОЗИЦИЯ ЗАКРЫТА — {direction} {symbol}</b> {mode}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Вход: <code>{entry}</code>\n"
            f"💵 Выход: <code>{exit_price}</code>\n"
            f"📦 Объём: <code>{quantity}</code>\n"
            f"💰 PnL: <b>{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)</b>\n"
            f"📝 Причина: {reason}\n"
        )

        # Бегущий итог за сегодня
        if day_stats and day_stats.get("count", 0) > 0:
            text += (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Сегодня: {day_stats['count']} сделок | "
                f"✅ {day_stats['wins']} / ❌ {day_stats['losses']} | "
                f"PnL ${day_stats['total_pnl']:+.2f}\n"
            )

        text += f"🕐 {self._now()}"
        await self.send(text)

    async def notify_trailing_stop_moved(self, symbol: str, direction: str,
                                          old_stop: float, new_stop: float,
                                          current_price: float):
        """Трейлинг стоп передвинут"""
        text = (
            f"🔄 <b>Трейлинг стоп обновлён — {symbol}</b>\n"
            f"📊 Направление: {direction}\n"
            f"💵 Текущая цена: <code>{current_price}</code>\n"
            f"🛑 Старый стоп: <code>{old_stop}</code>\n"
            f"🛑 Новый стоп: <code>{new_stop}</code>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_balance(self, balance: float, unrealized_pnl: float,
                              open_positions: int):
        """Отчёт о балансе"""
        pnl_emoji = "📈" if unrealized_pnl >= 0 else "📉"
        text = (
            f"💼 <b>Отчёт о балансе</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Баланс: <code>${balance:.2f}</code>\n"
            f"{pnl_emoji} Нереализованный PnL: <code>${unrealized_pnl:+.2f}</code>\n"
            f"📊 Открытых позиций: <b>{open_positions}</b>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_error(self, error: str, symbol: str = ""):
        """Ошибка"""
        sym = f" [{symbol}]" if symbol else ""
        text = (
            f"⚠️ <b>Ошибка{sym}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{error[:500]}</code>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_daily_limit(self, loss_pct: float):
        """Достигнут дневной лимит потерь"""
        text = (
            f"🚨 <b>ДНЕВНОЙ ЛИМИТ ПОТЕРЬ ДОСТИГНУТ!</b>\n"
            f"📉 Потери за день: <b>{loss_pct:.1%}</b>\n"
            f"⛔ Торговля приостановлена до завтра\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    # ═══════════════════════════════════════════
    # Новые уведомления
    # ═══════════════════════════════════════════

    async def notify_startup_check(self, ok: bool, balance: float = 0.0,
                                    open_positions: int = 0, error: str = ""):
        """Проверка подключения при старте"""
        if ok:
            text = (
                f"✅ <b>Подключение к BingX успешно</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Доступный баланс: <code>${balance:.2f}</code>\n"
                f"📊 Найдено открытых позиций: <b>{open_positions}</b>\n"
                f"🕐 {self._now()}"
            )
        else:
            text = (
                f"❌ <b>ОШИБКА ПОДКЛЮЧЕНИЯ К BingX</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Проверь API-ключи в .env\n"
                f"<code>{error[:300]}</code>\n"
                f"🕐 {self._now()}"
            )
        await self.send(text)

    async def notify_order_rejected(self, symbol: str, direction: str,
                                     quantity: float, reason: str = ""):
        """Ордер отклонён биржей"""
        text = (
            f"🚫 <b>ОРДЕР ОТКЛОНЁН — {direction} {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Объём: <code>{quantity}</code>\n"
            f"📝 Причина: <code>{reason[:300] if reason else 'неизвестно'}</code>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_daily_limit_warning(self, current_loss_pct: float,
                                          limit_pct: float):
        """Предупреждение: приближение к дневному лимиту потерь"""
        used = current_loss_pct / limit_pct * 100 if limit_pct > 0 else 0
        text = (
            f"⚠️ <b>Внимание: приближение к дневному лимиту</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 Текущие потери: <b>{current_loss_pct:.1%}</b> из {limit_pct:.1%}\n"
            f"📊 Использовано лимита: <b>{used:.0f}%</b>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)

    async def notify_position_status(self, positions: list):
        """
        Периодический статус открытых позиций.
        positions — список словарей: {symbol, direction, entry, current, pnl, pnl_pct}
        """
        if not positions:
            text = (
                f"📊 <b>Статус позиций</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Открытых позиций нет. Бот сканирует рынок.\n"
                f"🕐 {self._now()}"
            )
            await self.send(text)
            return

        lines = [f"📊 <b>Статус открытых позиций ({len(positions)})</b>", "━━━━━━━━━━━━━━━━━━━━"]
        total_pnl = 0.0
        for p in positions:
            emoji = "🟢" if p["direction"] == "LONG" else "🔴"
            pnl_emoji = "📈" if p["pnl"] >= 0 else "📉"
            sign = "+" if p["pnl"] >= 0 else ""
            total_pnl += p["pnl"]
            lines.append(
                f"{emoji} <b>{p['symbol']}</b> {p['direction']}\n"
                f"   Вход: <code>{p['entry']}</code> → Сейчас: <code>{p['current']}</code>\n"
                f"   {pnl_emoji} PnL: <b>{sign}${p['pnl']:.2f} ({sign}{p['pnl_pct']:.2f}%)</b>"
            )
        total_emoji = "📈" if total_pnl >= 0 else "📉"
        total_sign = "+" if total_pnl >= 0 else ""
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"{total_emoji} Итого плавающий PnL: <b>{total_sign}${total_pnl:.2f}</b>")
        lines.append(f"🕐 {self._now()}")
        await self.send("\n".join(lines))

    def _format_summary(self, title: str, stats: dict,
                        balance: float = None, start_balance: float = None,
                        open_positions: int = 0, floating_pnl: float = 0.0,
                        market_info: str = None) -> str:
        """Сформировать текст сводки по статистике"""
        if stats["count"] == 0:
            body = "Закрытых сделок за период нет."
        else:
            pf = stats["profit_factor"]
            pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
            total_sign = "+" if stats["total_pnl"] >= 0 else ""
            result_emoji = "🟢" if stats["total_pnl"] >= 0 else "🔴"

            lines = [
                f"📈 Сделок: <b>{stats['count']}</b>",
                f"✅ Прибыльных: <b>{stats['wins']}</b>  ❌ Убыточных: <b>{stats['losses']}</b>",
                f"🎯 Винрейт: <b>{stats['win_rate']:.1f}%</b>",
                f"{result_emoji} Итог PnL: <b>{total_sign}${stats['total_pnl']:.2f}</b>",
                f"💵 Профит-фактор: <b>{pf_str}</b>",
                f"📊 Средняя прибыль: <code>${stats['avg_win']:.2f}</code>  "
                f"Средний убыток: <code>${stats['avg_loss']:.2f}</code>",
            ]
            if stats["best"]:
                b = stats["best"]
                lines.append(f"🏆 Лучшая: {b.symbol} <b>+${b.pnl:.2f}</b>")
            if stats["worst"] and stats["worst"].pnl < 0:
                w = stats["worst"]
                lines.append(f"💔 Худшая: {w.symbol} <b>${w.pnl:.2f}</b>")
            body = "\n".join(lines)

        header = f"📋 <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\n"

        balance_block = ""
        if balance is not None:
            balance_block = f"\n━━━━━━━━━━━━━━━━━━━━\n💼 Баланс: <code>${balance:.2f}</code>"
            if start_balance is not None and start_balance > 0:
                change = balance - start_balance
                change_pct = change / start_balance * 100
                sign = "+" if change >= 0 else ""
                balance_block += f"  ({sign}{change_pct:.2f}% за период)"
            if open_positions > 0:
                balance_block += (
                    f"\n📊 Открыто позиций: <b>{open_positions}</b> "
                    f"(плавающий PnL ${floating_pnl:+.2f})"
                )

        if market_info:
            balance_block += f"\n{market_info}"

        return f"{header}{body}{balance_block}\n🕐 {self._now()}"

    async def notify_daily_summary(self, day_stats: dict, balance: float,
                                    day_start_balance: float = None,
                                    open_positions: int = 0,
                                    floating_pnl: float = 0.0,
                                    market_info: str = None):
        """Итоги дня"""
        today = datetime.now().strftime("%d.%m.%Y")
        text = self._format_summary(
            f"ИТОГИ ДНЯ — {today}",
            day_stats, balance, day_start_balance, open_positions, floating_pnl,
            market_info
        )
        await self.send(text)

    async def notify_weekly_summary(self, week_stats: dict, balance: float):
        """Итоги недели"""
        text = self._format_summary("ИТОГИ НЕДЕЛИ (7 дней)", week_stats, balance)
        await self.send(text)

    async def notify_heartbeat(self, balance: float, open_positions: int,
                                scanned_pairs: int):
        """Периодический сигнал 'бот жив'"""
        text = (
            f"💓 <b>Бот работает</b>\n"
            f"💰 Баланс: <code>${balance:.2f}</code> | "
            f"📊 Позиций: <b>{open_positions}</b> | "
            f"🔍 Пар в работе: <b>{scanned_pairs}</b>\n"
            f"🕐 {self._now()}"
        )
        await self.send(text)
