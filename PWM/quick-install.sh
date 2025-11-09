#!/bin/bash
# Бърза инсталация на PWM Daemon от GitHub
# Usage: curl -sSL https://raw.githubusercontent.com/KoToValery/ADC_LIN_CAN/main/PWM/quick-install.sh | sudo bash

set -e

GITHUB_REPO="https://raw.githubusercontent.com/KoToValery/ADC_LIN_CAN/main/PWM"
TEMP_DIR="/tmp/pwm-daemon-install"

echo "=========================================="
echo "PWM Daemon Quick Install"
echo "=========================================="
echo ""

# Провери за root права
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Моля стартирай с sudo"
    echo "Usage: curl -sSL https://raw.githubusercontent.com/KoToValery/ADC_LIN_CAN/main/PWM/quick-install.sh | sudo bash"
    exit 1
fi

# Създай временна директория
echo "📁 Създаване на временна директория..."
mkdir -p "$TEMP_DIR"
cd "$TEMP_DIR"

# Изтегли файловете
echo "⬇️  Изтегляне на файлове от GitHub..."
curl -sSL "$GITHUB_REPO/host-daemon/pwm_daemon.py" -o pwm_daemon.py
curl -sSL "$GITHUB_REPO/host-daemon/pwm-daemon.service" -o pwm-daemon.service

if [ ! -f pwm_daemon.py ] || [ ! -f pwm-daemon.service ]; then
    echo "❌ Грешка при изтегляне на файлове"
    exit 1
fi

echo "✓ Файловете са изтеглени"

# Копирай daemon скрипта
echo "📋 Копиране на pwm_daemon.py..."
cp pwm_daemon.py /usr/local/bin/
chmod +x /usr/local/bin/pwm_daemon.py

# Копирай systemd service
echo "📋 Копиране на systemd service..."
cp pwm-daemon.service /etc/systemd/system/

# Reload systemd
echo "🔄 Reload на systemd..."
systemctl daemon-reload

# Enable service
echo "✅ Активиране на service..."
systemctl enable pwm-daemon.service

# Start service
echo "▶️  Стартиране на service..."
systemctl start pwm-daemon.service

# Изчакай малко
sleep 2

# Провери статус
echo ""
echo "=========================================="
echo "✅ Инсталацията завърши успешно!"
echo "=========================================="
echo ""

# Покажи статус
systemctl status pwm-daemon.service --no-pager || true

echo ""
echo "📊 Тест на API:"
if command -v curl &> /dev/null; then
    curl -s http://localhost:9000/status 2>/dev/null && echo "" || echo "⚠️  API не отговаря (може да е нормално ако PWM не е конфигуриран)"
fi

echo ""
echo "=========================================="
echo "📚 Полезни команди:"
echo "=========================================="
echo "  sudo systemctl status pwm-daemon   - Провери статус"
echo "  sudo systemctl stop pwm-daemon     - Спри daemon"
echo "  sudo systemctl start pwm-daemon    - Стартирай daemon"
echo "  sudo systemctl restart pwm-daemon  - Рестартирай daemon"
echo "  sudo journalctl -u pwm-daemon -f   - Виж логове"
echo ""
echo "  curl http://localhost:9000/status  - Тест на API"
echo ""
echo "=========================================="
echo "⚠️  ВАЖНО: Не забравяйте да конфигурирате config.txt!"
echo "=========================================="
echo ""
echo "Редактирайте /boot/firmware/config.txt и добавете:"
echo "  dtoverlay=pwm,pin=12,func=4"
echo ""
echo "След това рестартирайте системата:"
echo "  sudo reboot"
echo ""

# Почисти временните файлове
cd /
rm -rf "$TEMP_DIR"

echo "✅ Готово!"
