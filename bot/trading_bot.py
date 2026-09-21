"""
Основной класс торгового бота — оркестрирует всё
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from bot.config import Config
from bot.exchange import BingXClient
from bot.strategy import TrendStrategy, Signal, make_strategy
from bot.risk_manager import RiskManager
from bot.notifier import TelegramNotifier
from bot.stats import TradeStats, TradeRecord

logger = logging.getLogger(__name__)


class TradingBot:
    """Главный торговый бот"""

    def __init__(self, config: Config):
        self.config = config
        self.running = False

        # Компоненты
        self.exchange = BingXClient(
            api_key=config.BINGX_API_KEY,
            secret_key=config.BINGX_SECRET_KEY,
            base_url=config.BINGX_BASE_URL
        )
        self.strategy = make_strategy(config)   # трендовая / реверсия / мульти — по config.STRATEGY_MODE
        self._market_ind = TrendStrategy(config)  # для расчёта ADX рынка (фильтр тренда)
        self.risk_manager = RiskManager(config)
        self.notifier = TelegramNotifier(
            token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID
        )
        self.stats = TradeStats()

        # Состояние
        self.open_positions: Dict[str, dict] = {}  # symbol -> position info
        self.recently_closed: Dict[str, datetime] = {}  # symbol -> время закрытия
        self.last_daily_reset = datetime.now().date()
        # Начало текущего «торгового дня» (привязан к DAILY_REPORT_HOUR, по умолч. 9:00)
        self.day_start_dt = datetime.now()
        # Файл для хранения открытых позиций (чтобы переживали перезапуск)
        self.positions_file = Path("logs/open_positions.json")
        # Состояние рынка (трендовый/боковик) — для фильтра тренда и уведомлений
        self._market_trending_state = None

    # ═══════════════════════════════════════════
    # Запуск / Остановка
    # ═══════════════════════════════════════════

    async def start(self):
        """Запустить бота"""
        logger.info("Инициализация...")

        await self.notifier.notify_bot_start(self.config.TRADING_PAIRS, self.config)

        # Синхронизируем время с сервером BingX (спасает от 'timestamp is invalid')
        await self.exchange.sync_time()

        # Проверка подключения к BingX
        connected = await self._startup_check()
        if not connected:
            logger.error("Не удалось подключиться к BingX. Остановка.")
            await self.exchange.close()
            return

        await self._setup()

        # Сверяем открытые позиции с биржей (восстановление после перезапуска)
        await self._reconcile_positions()

        self.running = True
        logger.info("Бот запущен. Начинаю сканирование рынка...")

        # Список фоновых задач
        tasks = [
            self._main_loop(),
            self._position_monitor_loop(),
            self._daily_report_loop(),
        ]
        if self.config.POSITION_STATUS_INTERVAL > 0:
            tasks.append(self._position_status_loop())
        if self.config.HEARTBEAT_INTERVAL > 0:
            tasks.append(self._heartbeat_loop())

        await asyncio.gather(*tasks)

    async def _startup_check(self) -> bool:
        """Проверить подключение к бирже при старте"""
        try:
            balance_data = await self.exchange.get_balance()
            balance = float(
                balance_data.get("availableMargin")
                or balance_data.get("balance")
                or 0
            )
            positions = await self.exchange.get_positions()
            open_count = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)

            # Считаем подключение успешным, если биржа ответила структурой баланса
            ok = bool(balance_data)
            if ok:
                self.stats.set_session_start_balance(balance)
            await self.notifier.notify_startup_check(
                ok=ok, balance=balance, open_positions=open_count,
                error="" if ok else "Пустой ответ от API баланса"
            )
            return ok
        except Exception as e:
            logger.error(f"Ошибка проверки подключения: {e}")
            await self.notifier.notify_startup_check(ok=False, error=str(e))
            return False

    async def stop(self):
        """Остановить бота"""
        logger.info("Останавливаю бота...")
        self.running = False
        await self.notifier.notify_bot_stop()
        await self.exchange.close()

    async def _setup(self):
        """Настройка плеча и типа маржи для всех пар"""
        for symbol in self.config.TRADING_PAIRS:
            try:
                # Тип маржи
                await self.exchange.set_margin_type(symbol, self.config.MARGIN_TYPE)
                # Плечо: в хедж-режиме отдельно LONG и SHORT, иначе BOTH
                if self.config.HEDGE_MODE:
                    await self.exchange.set_leverage(symbol, self.config.LEVERAGE, "LONG")
                    await self.exchange.set_leverage(symbol, self.config.LEVERAGE, "SHORT")
                else:
                    await self.exchange.set_leverage(symbol, self.config.LEVERAGE, "BOTH")
                logger.info(f"{symbol}: плечо x{self.config.LEVERAGE}, {self.config.MARGIN_TYPE}")
            except Exception as e:
                logger.warning(f"Ошибка настройки {symbol}: {e}")

    # ═══════════════════════════════════════════
    # Хранение открытых позиций (переживают перезапуск)
    # ═══════════════════════════════════════════

    def _save_positions(self):
        """Сохранить открытые позиции на диск"""
        try:
            self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for sym, pos in self.open_positions.items():
                p = dict(pos)
                # datetime -> строка
                oa = p.get("opened_at")
                if hasattr(oa, "isoformat"):
                    p["opened_at"] = oa.isoformat()
                data[sym] = p
            with open(self.positions_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения позиций: {e}")

    def _load_positions(self):
        """Загрузить открытые позиции с диска"""
        if not self.positions_file.exists():
            return
        try:
            with open(self.positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sym, p in data.items():
                oa = p.get("opened_at")
                if isinstance(oa, str):
                    try:
                        p["opened_at"] = datetime.fromisoformat(oa)
                    except ValueError:
                        p["opened_at"] = datetime.now()
                self.open_positions[sym] = p
            if self.open_positions:
                logger.info(f"Загружено {len(self.open_positions)} позиций из файла")
        except Exception as e:
            logger.error(f"Ошибка загрузки позиций: {e}")

    async def _reconcile_positions(self):
        """
        Сверить сохранённые позиции с реальными на бирже:
        - закрытые пока бот был офлайн — убрать (и сообщить),
        - открытые на бирже, но не у бота — взять под управление.
        """
        self._load_positions()
        try:
            positions = await self.exchange.get_positions()
        except Exception as e:
            logger.error(f"Не удалось получить позиции для сверки: {e}")
            return

        active = {p["symbol"]: p for p in positions if float(p.get("positionAmt", 0)) != 0}

        # 1. Сохранённые, но уже закрытые на бирже
        for sym in list(self.open_positions.keys()):
            if sym not in active:
                logger.info(f"{sym}: позиция закрылась, пока бот был офлайн — убираю из отслеживания")
                del self.open_positions[sym]

        # 2. Открытые на бирже, но не отслеживаемые ботом — взять под управление
        for sym, p in active.items():
            if sym not in self.open_positions:
                amt = float(p.get("positionAmt", 0))
                direction = "LONG" if amt > 0 else "SHORT"
                entry = float(p.get("avgPrice") or p.get("entryPrice") or 0)
                if entry <= 0:
                    continue
                # SL/TP приблизительно из конфига (точные могли быть выставлены раньше)
                if direction == "LONG":
                    sl = entry * (1 - self.config.STOP_LOSS_PCT)
                    tp = entry * (1 + self.config.TAKE_PROFIT_PCT)
                else:
                    sl = entry * (1 + self.config.STOP_LOSS_PCT)
                    tp = entry * (1 - self.config.TAKE_PROFIT_PCT)
                self.open_positions[sym] = {
                    "direction": direction,
                    "entry_price": entry,
                    "quantity": abs(amt),
                    "stop_loss": round(sl, 6),
                    "take_profit": round(tp, 6),
                    "opened_at": datetime.now(),
                }
                logger.info(f"{sym}: взял под управление существующую позицию {direction}")

        self._save_positions()

    # ═══════════════════════════════════════════
    # Основной цикл — поиск сигналов
    # ═══════════════════════════════════════════

    async def _main_loop(self):
        """Основной цикл — сканирует рынок каждые N секунд"""
        while self.running:
            try:
                await self._scan_market()
            except Exception as e:
                logger.error(f"Ошибка основного цикла: {e}")
                await self.notifier.notify_error(str(e))

            await asyncio.sleep(self.config.SCAN_INTERVAL)

    async def _market_is_trending(self) -> tuple:
        """
        Трендовый ли рынок сейчас (по ADX биткоина на старшем ТФ).
        Возвращает (трендовый: bool, значение ADX: float).
        При выключенном фильтре или ошибке — считаем, что можно торговать.
        """
        if not self.config.USE_MARKET_FILTER:
            return True, 0.0
        try:
            klines = await self.exchange.get_klines(
                self.config.MARKET_FILTER_SYMBOL,
                self.config.MARKET_FILTER_TIMEFRAME,
                limit=200,
            )
            if not klines:
                return True, 0.0
            df = self._market_ind._parse_klines(klines)
            adx_series, _, _ = self._market_ind._adx(df, self.config.ADX_PERIOD)
            adx = float(adx_series.iloc[-1])
            return adx >= self.config.MARKET_FILTER_ADX, adx
        except Exception as e:
            logger.warning(f"Не удалось проверить режим рынка: {e}")
            return True, 0.0  # при ошибке не блокируем торговлю

    async def _scan_market(self):
        """Сканирование всех пар на сигналы"""
        # Фильтр глобального тренда: торгуем только когда рынок трендит
        trending, market_adx = await self._market_is_trending()

        # Уведомляем при СМЕНЕ режима рынка (без спама)
        if trending != self._market_trending_state:
            self._market_trending_state = trending
            if self.config.USE_MARKET_FILTER:
                if trending:
                    await self.notifier.send(
                        f"📈 Рынок снова трендовый (ADX {self.config.MARKET_FILTER_SYMBOL} "
                        f"{market_adx:.1f}) — бот возобновляет открытие сделок"
                    )
                else:
                    await self.notifier.send(
                        f"😴 Рынок в боковике (ADX {self.config.MARKET_FILTER_SYMBOL} "
                        f"{market_adx:.1f} < {self.config.MARKET_FILTER_ADX}) — "
                        f"бот приостанавливает новые сделки, открытые ведёт как обычно"
                    )

        if not trending:
            logger.info(
                f"Рынок в боковике (ADX {market_adx:.1f} < {self.config.MARKET_FILTER_ADX}) "
                f"— новые сделки не открываю"
            )
            return

        # Получаем открытые позиции с биржи
        positions = await self.exchange.get_positions()
        exchange_symbols = [p["symbol"] for p in positions if float(p.get("positionAmt", 0)) != 0]

        # Объединяем с локально открытыми — гарантия одной позиции на монету
        # (биржа может ответить с задержкой, локальный список закрывает этот зазор)
        open_symbols = list(set(exchange_symbols) | set(self.open_positions.keys()))

        # Получаем баланс
        balance_data = await self.exchange.get_balance()
        balance = float(balance_data.get("availableMargin", 0))

        if balance <= 0:
            logger.warning("Баланс нулевой или недоступен")
            return

        for symbol in self.config.TRADING_PAIRS:
            try:
                # Пропускаем монеты на «перезарядке» после недавнего закрытия
                if self._in_cooldown(symbol):
                    logger.debug(f"{symbol}: перезарядка после закрытия, пропуск")
                    continue

                can_open, reason = self.risk_manager.can_open_position(
                    len(open_symbols), symbol, open_symbols
                )

                if not can_open:
                    logger.debug(f"{symbol}: {reason}")
                    continue

                # Получаем свечи
                klines = await self.exchange.get_klines(
                    symbol, self.config.TIMEFRAME, limit=200
                )

                if not klines:
                    continue

                # Анализируем
                signal = self.strategy.analyze(symbol, klines)

                if signal.direction == "NONE":
                    logger.debug(f"{symbol}: {signal.reason}")
                    continue

                # Проверяем, хватит ли свободной маржи (берём СВЕЖИЙ баланс,
                # чтобы учесть только что открытые в этом же цикле позиции).
                # Если не хватает — тихо пропускаем: без попытки открыть,
                # без ошибок и без сообщений в Telegram.
                affordable, need_margin, avail = await self._check_affordable(signal)
                if not affordable:
                    logger.info(
                        f"{symbol}: сигнал {signal.direction}, но не хватает маржи "
                        f"(нужно ~${need_margin:.2f}, свободно ${avail:.2f}) — пропуск"
                    )
                    continue

                logger.info(f"📡 Сигнал: {signal.direction} {symbol} | {signal.reason}")

                # Уведомляем о сигнале (если включено)
                if self.config.NOTIFY_SIGNALS:
                    await self.notifier.notify_signal(
                        symbol=symbol,
                        direction=signal.direction,
                        strength=signal.strength,
                        entry=signal.entry_price,
                        sl=signal.stop_loss,
                        tp=signal.take_profit,
                        reason=signal.reason,
                        indicators=signal.indicators
                    )

                # Открываем позицию (передаём свежую доступную маржу)
                await self._open_position(signal, avail)

                # Небольшая пауза между ордерами
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Ошибка обработки {symbol}: {e}")

    # ═══════════════════════════════════════════
    # Открытие позиции
    # ═══════════════════════════════════════════

    async def _check_affordable(self, signal: Signal):
        """
        Хватает ли свободной маржи на позицию по сигналу.
        Берёт СВЕЖИЙ доступный баланс (учитывает уже открытые позиции).
        Возвращает (можно_открыть, нужно_маржи_$, доступно_$).
        """
        balance_data = await self.exchange.get_balance()
        available = float(balance_data.get("availableMargin", 0) or 0)

        if available <= 0:
            return False, 0.0, available

        quantity = self.risk_manager.calculate_position_size(
            balance=available,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            leverage=self.config.LEVERAGE
        )
        if quantity <= 0:
            return False, 0.0, available

        notional = quantity * signal.entry_price
        required_margin = notional / max(self.config.LEVERAGE, 1)
        # Запас 10% на комиссии и колебания цены
        ok = required_margin <= available * 0.9
        return ok, required_margin, available

    async def _open_position(self, signal: Signal, balance: float):
        """Открыть позицию по сигналу"""
        symbol = signal.symbol
        direction = signal.direction

        # Рассчитываем размер
        quantity = self.risk_manager.calculate_position_size(
            balance=balance,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            leverage=self.config.LEVERAGE
        )

        if quantity <= 0:
            logger.warning(f"{symbol}: нулевой размер позиции")
            return

        # Округляем количество и цены под требования биржи (шаг лота / тик цены)
        filters = await self.exchange.get_symbol_filters(symbol)
        qty_step = filters.get("qty_step", 0)
        price_tick = filters.get("price_tick", 0)
        min_qty = filters.get("min_qty", 0)

        quantity = self.risk_manager.round_quantity(quantity, qty_step)
        sl_price = self.risk_manager.round_price(signal.stop_loss, price_tick)
        tp_price = self.risk_manager.round_price(signal.take_profit, price_tick)

        if quantity <= 0 or (min_qty and quantity < min_qty):
            logger.warning(
                f"{symbol}: размер {quantity} меньше минимального {min_qty} — пропуск"
            )
            return

        # Стороны ордера
        order_side = "BUY" if direction == "LONG" else "SELL"
        # В одностороннем режиме BingX требует positionSide = BOTH
        position_side = direction if self.config.HEDGE_MODE else "BOTH"
        close_side = "SELL" if direction == "LONG" else "BUY"

        if self.config.DRY_RUN:
            logger.info(f"[DRY RUN] Открываю {direction} {symbol}: qty={quantity}")
        else:
            # Рыночный ордер на вход
            order = await self.exchange.place_order(
                symbol=symbol,
                side=order_side,
                position_side=position_side,
                order_type="MARKET",
                quantity=quantity
            )

            if not order:
                logger.error(f"Ошибка открытия ордера {symbol}")
                await self.notifier.notify_order_rejected(
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    reason="Биржа отклонила ордер (см. лог). Проверь плечо, баланс, режим позиций."
                )
                return

            # Стоп-лосс
            await self.exchange.place_stop_loss(
                symbol=symbol,
                side=close_side,
                position_side=position_side,
                quantity=quantity,
                stop_price=sl_price
            )

            # Тейк-профит
            await self.exchange.place_take_profit(
                symbol=symbol,
                side=close_side,
                position_side=position_side,
                quantity=quantity,
                stop_price=tp_price
            )

        # Сохраняем позицию локально
        self.open_positions[symbol] = {
            "direction": direction,
            "entry_price": signal.entry_price,
            "quantity": quantity,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "opened_at": datetime.now(),
        }
        self._save_positions()

        # Уведомление
        await self.notifier.notify_order_opened(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry=signal.entry_price,
            sl=sl_price,
            tp=tp_price,
            balance=balance,
            dry_run=self.config.DRY_RUN
        )

        logger.info(f"✅ Позиция открыта: {direction} {symbol} qty={quantity}")

    # ═══════════════════════════════════════════
    # Мониторинг позиций
    # ═══════════════════════════════════════════

    async def _position_monitor_loop(self):
        """Мониторинг открытых позиций и трейлинг стоп"""
        while self.running:
            try:
                await self._monitor_positions()
            except Exception as e:
                logger.error(f"Ошибка мониторинга позиций: {e}")
            await asyncio.sleep(10)  # Каждые 10 секунд

    async def _monitor_positions(self):
        """Проверить все открытые позиции"""
        if not self.open_positions:
            return

        positions = await self.exchange.get_positions()
        active_symbols = {
            p["symbol"]: p for p in positions
            if float(p.get("positionAmt", 0)) != 0
        }

        for symbol, local_pos in list(self.open_positions.items()):

            # Позиция закрылась (SL/TP сработал)
            if symbol not in active_symbols:
                await self._handle_closed_position(symbol, local_pos)
                continue

            exchange_pos = active_symbols[symbol]
            current_price = float(exchange_pos.get("markPrice", 0))

            if current_price <= 0:
                continue

            # Трейлинг стоп
            if self.config.TRAILING_STOP:
                await self._update_trailing_stop(symbol, local_pos, current_price)

    async def _handle_closed_position(self, symbol: str, local_pos: dict):
        """Позиция закрыта — отправить уведомление"""
        direction = local_pos["direction"]
        entry = local_pos["entry_price"]
        quantity = local_pos["quantity"]

        # Текущую цену для расчёта PnL (примерная)
        ticker = await self.exchange.get_ticker(symbol)
        exit_price = float(ticker.get("lastPrice", entry))

        if direction == "LONG":
            pnl = (exit_price - entry) * quantity
        else:
            pnl = (entry - exit_price) * quantity

        # Определяем причину закрытия
        if exit_price <= local_pos["stop_loss"] and direction == "LONG":
            reason = "🛑 Стоп-лосс"
        elif exit_price >= local_pos["take_profit"] and direction == "LONG":
            reason = "🎯 Тейк-профит"
        elif exit_price >= local_pos["stop_loss"] and direction == "SHORT":
            reason = "🛑 Стоп-лосс"
        elif exit_price <= local_pos["take_profit"] and direction == "SHORT":
            reason = "🎯 Тейк-профит"
        else:
            reason = "❓ Закрыта"

        pnl_pct = (exit_price - entry) / entry * 100
        if direction == "SHORT":
            pnl_pct = -pnl_pct

        # Записываем сделку в статистику
        self.stats.record_trade(TradeRecord(
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            exit_price=exit_price,
            quantity=quantity,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            reason=reason,
            opened_at=local_pos.get("opened_at", datetime.now()).isoformat()
                if hasattr(local_pos.get("opened_at"), "isoformat")
                else str(local_pos.get("opened_at", "")),
            closed_at=datetime.now().isoformat(),
        ))

        del self.open_positions[symbol]
        self._save_positions()
        # Запоминаем время закрытия — для перезарядки перед повторным входом
        self.recently_closed[symbol] = datetime.now()

        # Обновляем дневной лимит по ЧИСТОМУ результату за ТОРГОВЫЙ ДЕНЬ
        # (период с 9:00; прибыльный день лимит не копит — считается только чистый убыток)
        day_stats = self.stats.period_summary(self.day_start_dt)
        self.risk_manager.update_daily_pnl(
            net_pnl=day_stats.get("total_pnl", 0.0),
            start_balance=self.stats.day_start_balance or 0.0
        )

        # Уведомление с бегущим итогом за день
        await self.notifier.notify_position_closed(
            symbol=symbol,
            direction=direction,
            entry=entry,
            exit_price=exit_price,
            quantity=quantity,
            pnl=pnl,
            reason=reason,
            dry_run=self.config.DRY_RUN,
            day_stats=day_stats
        )

        # Защита от серии убытков
        event = self.risk_manager.register_trade_result(pnl)
        if event:
            logger.warning(event)
            await self.notifier.send(event)

        # Kill switch по общей просадке счёта
        try:
            bal = await self.exchange.get_balance()
            equity = float(bal.get("balance") or bal.get("availableMargin") or 0)
            halt = self.risk_manager.check_drawdown(equity)
            if halt:
                logger.warning(halt)
                await self.notifier.send(halt)
        except Exception as e:
            logger.warning(f"Не удалось проверить просадку: {e}")

        # Проверяем приближение к дневному лимиту потерь
        await self._check_daily_limit_warning()

    def _in_cooldown(self, symbol: str) -> bool:
        """
        True, если монета на «перезарядке» после недавнего закрытия.
        Защищает от повторного входа в ту же монету сразу после сделки.
        При REENTRY_COOLDOWN = 0 перезарядки нет.
        """
        cooldown = self.config.REENTRY_COOLDOWN
        if cooldown <= 0:
            return False
        closed_at = self.recently_closed.get(symbol)
        if not closed_at:
            return False
        elapsed = (datetime.now() - closed_at).total_seconds()
        if elapsed >= cooldown:
            # Перезарядка прошла — убираем из списка
            del self.recently_closed[symbol]
            return False
        return True

    async def _check_daily_limit_warning(self):
        """Предупредить при приближении к дневному лимиту потерь"""
        rm = self.risk_manager
        limit = rm.daily_loss_limit
        warn_at = self.config.DAILY_LIMIT_WARN_AT * limit
        if limit > 0 and warn_at <= rm.daily_loss < limit:
            await self.notifier.notify_daily_limit_warning(rm.daily_loss, limit)
        elif rm.daily_loss >= limit:
            await self.notifier.notify_daily_limit(rm.daily_loss)

    async def _update_trailing_stop(self, symbol: str, local_pos: dict, current_price: float):
        """Обновить трейлинг стоп"""
        direction = local_pos["direction"]
        current_stop = local_pos["stop_loss"]

        new_stop = self.risk_manager.calculate_trailing_stop(
            direction=direction,
            current_price=current_price,
            entry_price=local_pos["entry_price"],
            current_stop=current_stop
        )

        if new_stop is None:
            return

        quantity = local_pos["quantity"]
        close_side = "SELL" if direction == "LONG" else "BUY"
        position_side = direction if self.config.HEDGE_MODE else "BOTH"

        if not self.config.DRY_RUN:
            # Отменяем старый стоп и ставим новый
            await self.exchange.cancel_all_orders(symbol)
            await self.exchange.place_stop_loss(
                symbol=symbol,
                side=close_side,
                position_side=position_side,
                quantity=quantity,
                stop_price=new_stop
            )
            # Восстанавливаем тейк-профит
            await self.exchange.place_take_profit(
                symbol=symbol,
                side=close_side,
                position_side=position_side,
                quantity=quantity,
                stop_price=local_pos["take_profit"]
            )

        local_pos["stop_loss"] = new_stop
        self._save_positions()
        logger.info(f"🔄 Трейлинг стоп {symbol}: {current_stop} → {new_stop}")

        # Уведомление в Telegram — только если включено в настройках
        if self.config.NOTIFY_TRAILING_STOP:
            await self.notifier.notify_trailing_stop_moved(
                symbol=symbol,
                direction=direction,
                old_stop=current_stop,
                new_stop=new_stop,
                current_price=current_price
            )

    # ═══════════════════════════════════════════
    # Ежедневный / недельный отчёт
    # ═══════════════════════════════════════════

    async def _daily_report_loop(self):
        """Отправлять итоги дня в заданный час"""
        while self.running:
            now = datetime.now()
            hour = self.config.DAILY_REPORT_HOUR
            target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target + timedelta(days=1)   # безопасный перенос на след. день

            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)

            if self.running:
                await self._send_daily_report()
                # По нужным дням — ещё и итоги недели
                if datetime.now().weekday() == self.config.WEEKLY_REPORT_WEEKDAY:
                    await self._send_weekly_report()

    async def _get_balance_and_positions(self):
        """Вспомогательное: баланс + сводка позиций"""
        balance_data = await self.exchange.get_balance()
        balance = float(
            balance_data.get("availableMargin")
            or balance_data.get("balance")
            or 0
        )
        positions = await self.exchange.get_positions()
        open_count = sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)
        unrealized = sum(float(p.get("unrealizedProfit", 0)) for p in positions)
        return balance, open_count, unrealized, positions

    async def _send_daily_report(self):
        """Итоги ТОРГОВОГО ДНЯ (период с прошлого отчёта в 9:00) со статистикой сделок"""
        balance, open_count, unrealized, _ = await self._get_balance_and_positions()
        # Полный торговый день: с момента начала дня (прошлые 9:00) до сейчас
        day_stats = self.stats.period_summary(self.day_start_dt)

        # Состояние рынка — чтобы было видно, почему бот торгует или молчит
        market_info = None
        if self.config.USE_MARKET_FILTER:
            trending, market_adx = await self._market_is_trending()
            coin = self.config.MARKET_FILTER_SYMBOL.replace("-USDT", "")
            if trending:
                market_info = (
                    f"🌍 Рынок: ADX {coin} <b>{market_adx:.1f}</b> — тренд, торговля активна"
                )
            else:
                market_info = (
                    f"🌍 Рынок: ADX {coin} <b>{market_adx:.1f}</b> "
                    f"(&lt; {self.config.MARKET_FILTER_ADX:.0f}) — боковик, новые сделки на паузе"
                )

        await self.notifier.notify_daily_summary(
            day_stats=day_stats,
            balance=balance,
            day_start_balance=self.stats.day_start_balance,
            open_positions=open_count,
            floating_pnl=unrealized,
            market_info=market_info
        )

        # Начинаем НОВЫЙ торговый день: фиксируем точку отсчёта, баланс и сбрасываем лимит
        self.day_start_dt = datetime.now()
        self.stats.set_day_start_balance(balance)
        self.risk_manager.reset_daily_stats()

    async def _send_weekly_report(self):
        """Итоги недели"""
        balance, _, _, _ = await self._get_balance_and_positions()
        week_stats = self.stats.week_summary()
        await self.notifier.notify_weekly_summary(week_stats, balance)

    # ═══════════════════════════════════════════
    # Периодический статус позиций
    # ═══════════════════════════════════════════

    async def _position_status_loop(self):
        """Периодически слать статус открытых позиций"""
        while self.running:
            await asyncio.sleep(self.config.POSITION_STATUS_INTERVAL)
            if not self.running:
                break
            try:
                await self._send_position_status()
            except Exception as e:
                logger.error(f"Ошибка статуса позиций: {e}")

    async def _send_position_status(self):
        """Собрать и отправить статус позиций"""
        positions = await self.exchange.get_positions()
        active = [p for p in positions if float(p.get("positionAmt", 0)) != 0]

        report = []
        for p in active:
            entry = float(p.get("avgPrice", 0) or p.get("entryPrice", 0) or 0)
            current = float(p.get("markPrice", 0) or 0)
            pnl = float(p.get("unrealizedProfit", 0) or 0)
            amt = float(p.get("positionAmt", 0))
            direction = "LONG" if amt > 0 else "SHORT"
            pnl_pct = (pnl / (abs(amt) * entry) * 100) if entry and amt else 0.0
            report.append({
                "symbol": p.get("symbol", "?"),
                "direction": direction,
                "entry": round(entry, 4),
                "current": round(current, 4),
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            })

        await self.notifier.notify_position_status(report)

    # ═══════════════════════════════════════════
    # Heartbeat (бот жив)
    # ═══════════════════════════════════════════

    async def _heartbeat_loop(self):
        """Периодический сигнал, что бот работает"""
        while self.running:
            await asyncio.sleep(self.config.HEARTBEAT_INTERVAL)
            if not self.running:
                break
            try:
                balance, open_count, _, _ = await self._get_balance_and_positions()
                await self.notifier.notify_heartbeat(
                    balance=balance,
                    open_positions=open_count,
                    scanned_pairs=len(self.config.TRADING_PAIRS)
                )
            except Exception as e:
                logger.error(f"Ошибка heartbeat: {e}")
