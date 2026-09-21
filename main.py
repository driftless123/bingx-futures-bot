"""
BingX Futures Trading Bot - Main Entry Point
Трендовая стратегия с уведомлениями в Telegram
"""

import asyncio
import logging
import sys
import os
from bot.trading_bot import TradingBot
from bot.config import Config

# Создаём папку для логов если нет
os.makedirs("logs", exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск BingX Trading Bot...")

    config = Config()
    bot = TradingBot(config)

    # Обработка остановки через KeyboardInterrupt (Ctrl+C)
    # Работает на Windows, Linux и macOS
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("⛔ Получен сигнал остановки (Ctrl+C)...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
