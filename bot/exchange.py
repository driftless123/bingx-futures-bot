"""
BingX API клиент — работа с биржей
"""

import hashlib
import hmac
import time
import logging
from typing import Optional, Dict, List
from urllib.parse import urlencode
import aiohttp

logger = logging.getLogger(__name__)


class BingXClient:
    """Асинхронный клиент для BingX Futures API"""

    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.time_offset = 0  # разница между временем сервера BingX и локальным (мс)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"X-BX-APIKEY": self.api_key}
            )
        return self.session

    def _sign(self, query: str) -> str:
        """Подпись строки запроса (HMAC SHA256)"""
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _timestamp(self) -> int:
        # Локальное время + смещение относительно сервера BingX
        return int(time.time() * 1000) + self.time_offset

    async def get_server_time(self) -> int:
        """Серверное время BingX в миллисекундах"""
        try:
            session = await self._get_session()
            url = f"{self.base_url}/openApi/swap/v2/server/time"
            async with session.get(url) as resp:
                data = await resp.json()
            # Время может лежать в data.serverTime или прямо в serverTime
            if isinstance(data, dict):
                inner = data.get("data", {})
                if isinstance(inner, dict) and inner.get("serverTime"):
                    return int(inner["serverTime"])
                if data.get("serverTime"):
                    return int(data["serverTime"])
        except Exception as e:
            logger.error(f"Не удалось получить время сервера: {e}")
        return 0

    async def sync_time(self):
        """
        Синхронизировать локальное время с сервером BingX.
        Считает смещение, которое затем добавляется ко всем подписанным запросам.
        Это спасает от ошибки 'timestamp is invalid' при сбитых часах Windows.
        """
        server_time = await self.get_server_time()
        if server_time > 0:
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            logger.info(
                f"Время синхронизировано с BingX. Смещение: {self.time_offset} мс"
            )
        else:
            logger.warning(
                "Не удалось синхронизировать время с сервером — "
                "проверь, что часы Windows синхронизированы вручную."
            )

    async def _request(self, method: str, path: str, params: dict = None, signed: bool = True) -> dict:
        """Базовый запрос к API.

        КРИТИЧНО для BingX: подписывается и отправляется ОДНА И ТА ЖЕ строка
        параметров в одном порядке. Поэтому собираем query вручную и шлём
        полный URL, не передавая dict в aiohttp (иначе он переставит порядок).
        """
        session = await self._get_session()
        params = dict(params or {})

        if signed:
            params["timestamp"] = self._timestamp()

        # Параметры по алфавиту — один и тот же порядок для подписи и отправки
        query = urlencode(sorted(params.items()))

        if signed:
            signature = self._sign(query)
            query = f"{query}&signature={signature}" if query else f"signature={signature}"

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        try:
            # Отправляем готовый URL без params — порядок уже зафиксирован
            async with session.request(method, url) as resp:
                data = await resp.json()

            if data.get("code") != 0:
                logger.error(f"API ошибка: {data}")
                return {}

            return data.get("data", {})

        except Exception as e:
            logger.error(f"Ошибка запроса {path}: {e}")
            return {}

    # ═══════════════════════════════════════════
    # Аккаунт
    # ═══════════════════════════════════════════

    async def get_balance(self) -> dict:
        """Получить баланс аккаунта"""
        data = await self._request("GET", "/openApi/swap/v2/user/balance")
        return data.get("balance", {})

    async def get_positions(self) -> List[dict]:
        """Получить открытые позиции"""
        data = await self._request("GET", "/openApi/swap/v2/user/positions")
        return data if isinstance(data, list) else []

    async def get_position(self, symbol: str) -> Optional[dict]:
        """Получить позицию по символу"""
        positions = await self.get_positions()
        for pos in positions:
            if pos.get("symbol") == symbol and float(pos.get("positionAmt", 0)) != 0:
                return pos
        return None

    # ═══════════════════════════════════════════
    # Рыночные данные
    # ═══════════════════════════════════════════

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[list]:
        """Получить свечи (OHLCV)"""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        data = await self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
        return data if isinstance(data, list) else []

    async def get_ticker(self, symbol: str) -> dict:
        """Текущая цена"""
        params = {"symbol": symbol}
        data = await self._request("GET", "/openApi/swap/v2/quote/ticker", params, signed=False)
        return data if isinstance(data, dict) else {}

    async def get_mark_price(self, symbol: str) -> float:
        """Получить маркировочную цену"""
        params = {"symbol": symbol}
        data = await self._request("GET", "/openApi/swap/v2/quote/premiumIndex", params, signed=False)
        return float(data.get("markPrice", 0))

    async def get_contracts(self) -> list:
        """Список всех USDT-M контрактов с их параметрами (точность и т.д.)"""
        data = await self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
        return data if isinstance(data, list) else []

    async def get_exchange_info(self, symbol: str) -> dict:
        """Информация о конкретном контракте (ищем в общем списке)"""
        contracts = await self.get_contracts()
        for c in contracts:
            if isinstance(c, dict) and c.get("symbol") == symbol:
                return c
        return {}

    async def get_symbol_filters(self, symbol: str) -> dict:
        """
        Возвращает шаг количества и шаг цены для символа.
        BingX отдаёт quantityPrecision / pricePrecision (число знаков),
        из которых вычисляем шаг.
        """
        info = await self.get_exchange_info(symbol)

        qty_step = 0.0
        price_tick = 0.0

        # Вариант 1: точность в знаках
        if "quantityPrecision" in info:
            qty_step = 10 ** (-int(info["quantityPrecision"]))
        if "pricePrecision" in info:
            price_tick = 10 ** (-int(info["pricePrecision"]))

        # Вариант 2: явные шаги (на случай другого формата ответа)
        if not qty_step and info.get("stepSize"):
            qty_step = float(info["stepSize"])
        if not price_tick and info.get("tickSize"):
            price_tick = float(info["tickSize"])

        return {
            "qty_step": qty_step,
            "price_tick": price_tick,
            "min_qty": float(info.get("tradeMinQuantity", 0) or 0),
        }

    # ═══════════════════════════════════════════
    # Ордера
    # ═══════════════════════════════════════════

    async def place_order(
        self,
        symbol: str,
        side: str,           # BUY / SELL
        position_side: str,  # LONG / SHORT
        order_type: str,     # MARKET / LIMIT
        quantity: float,
        price: float = None,
        stop_price: float = None,
        reduce_only: bool = False
    ) -> dict:
        """Разместить ордер"""
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "quantity": quantity,
        }

        if price:
            params["price"] = price
        if stop_price:
            params["stopPrice"] = stop_price
        if reduce_only:
            params["reduceOnly"] = "true"

        return await self._request("POST", "/openApi/swap/v2/trade/order", params)

    async def place_stop_loss(self, symbol: str, side: str, position_side: str,
                               quantity: float, stop_price: float) -> dict:
        """Стоп-лосс ордер"""
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "STOP_MARKET",
            "quantity": quantity,
            "stopPrice": stop_price,
            "reduceOnly": "true"
        }
        return await self._request("POST", "/openApi/swap/v2/trade/order", params)

    async def place_take_profit(self, symbol: str, side: str, position_side: str,
                                 quantity: float, stop_price: float) -> dict:
        """Тейк-профит ордер"""
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": quantity,
            "stopPrice": stop_price,
            "reduceOnly": "true"
        }
        return await self._request("POST", "/openApi/swap/v2/trade/order", params)

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Отменить ордер"""
        params = {"symbol": symbol, "orderId": order_id}
        return await self._request("DELETE", "/openApi/swap/v2/trade/order", params)

    async def cancel_all_orders(self, symbol: str) -> dict:
        """Отменить все ордера по символу"""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders", params)

    async def close_position(self, symbol: str, position_side: str, quantity: float) -> dict:
        """Закрыть позицию по рынку"""
        side = "SELL" if position_side == "LONG" else "BUY"
        return await self.place_order(
            symbol=symbol,
            side=side,
            position_side=position_side,
            order_type="MARKET",
            quantity=quantity,
            reduce_only=True
        )

    # ═══════════════════════════════════════════
    # Настройки
    # ═══════════════════════════════════════════

    async def set_leverage(self, symbol: str, leverage: int, side: str = "BOTH") -> dict:
        """
        Установить кредитное плечо.
        side: BOTH (односторонний режим) либо LONG / SHORT (хедж-режим).
        BingX требует этот параметр.
        """
        params = {"symbol": symbol, "side": side, "leverage": leverage}
        return await self._request("POST", "/openApi/swap/v2/trade/leverage", params)

    async def set_margin_type(self, symbol: str, margin_type: str) -> dict:
        """Установить тип маржи (ISOLATED/CROSS)"""
        params = {"symbol": symbol, "marginType": margin_type}
        return await self._request("POST", "/openApi/swap/v2/trade/marginType", params)

    async def close(self):
        """Закрыть сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
