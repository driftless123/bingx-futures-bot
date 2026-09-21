#!/bin/bash
# Установка бота на сервер (Ubuntu). Запускать из папки проекта:
#   cd ~/bingx_trading_bot && bash deploy/setup.sh
set -e
cd "$(dirname "$0")/.."

echo "==> Обновление системы..."
sudo apt update && sudo apt upgrade -y

echo "==> Установка Python..."
sudo apt install -y python3 python3-pip python3-venv

echo "==> Создание виртуального окружения и установка зависимостей..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> Установка часового пояса (Польша)..."
sudo timedatectl set-timezone Europe/Warsaw

echo ""
echo "================================================================"
echo "Готово! Дальше:"
echo "  1. Создай .env с ключами:   nano .env"
echo "  2. Проверь запуск вручную:  ./venv/bin/python main.py"
echo "  3. Настрой автозапуск (см. DEPLOY_ORACLE.md, шаг 8)"
echo "================================================================"
