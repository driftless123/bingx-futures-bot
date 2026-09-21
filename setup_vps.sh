#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Установка BingX-бота на Ubuntu-сервер (Oracle Cloud / любой VPS)
#  Запускать ИЗ ПАПКИ БОТА на сервере:
#     cd ~/bingx_trading_bot
#     bash setup_vps.sh
# ═══════════════════════════════════════════════════════════════════
set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_USER="$(whoami)"

echo ">>> Папка бота:   $BOT_DIR"
echo ">>> Пользователь: $SERVICE_USER"
echo ""

# 1. Часовой пояс — польское время (чтобы отчёт и сброс дня в 9:00 были по-твоему)
echo ">>> Ставлю часовой пояс Europe/Warsaw..."
sudo timedatectl set-timezone Europe/Warsaw || true

# 2. Обновление системы и установка Python
echo ">>> Обновляю систему и ставлю Python..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv unzip

# 3. Виртуальное окружение и зависимости
echo ">>> Создаю venv и ставлю зависимости (может занять пару минут)..."
cd "$BOT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# 4. Проверка .env
if [ ! -f "$BOT_DIR/.env" ]; then
  echo ""
  echo "!!! ВНИМАНИЕ: файла .env нет."
  echo "!!! Создай его ДО запуска бота:"
  echo "    nano $BOT_DIR/.env"
  echo "    (вставь ключи по образцу .env.example, сохрани Ctrl+O, выйди Ctrl+X)"
  echo ""
fi

# 5. systemd-сервис — автозапуск 24/7 + авто-перезапуск при сбое
echo ">>> Создаю systemd-сервис bingxbot..."
sudo tee /etc/systemd/system/bingxbot.service > /dev/null << EOF
[Unit]
Description=BingX Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bingxbot

echo ""
echo "═══════════════════════════════════════════════════════"
echo " Готово! Управление ботом:"
echo "   Запустить:    sudo systemctl start bingxbot"
echo "   Остановить:   sudo systemctl stop bingxbot"
echo "   Перезапуск:   sudo systemctl restart bingxbot"
echo "   Статус:       sudo systemctl status bingxbot"
echo "   Логи (live):  journalctl -u bingxbot -f"
echo "   Лог-файл:     tail -f $BOT_DIR/logs/bot.log"
echo "═══════════════════════════════════════════════════════"
echo " Бот будет сам запускаться при перезагрузке сервера"
echo " и перезапускаться, если упадёт."
echo ""
echo " !!! Если ещё не создал .env с ключами — сделай это,"
echo "     потом:  sudo systemctl start bingxbot"
echo "═══════════════════════════════════════════════════════"
