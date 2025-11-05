import lgpio
import time

# Конфигурация
LED_PIN = 18
CHIP = 4  # gpiochip0 на Raspberry Pi 5
FREQUENCY = 1000  # Hz

# Инициализация
h = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_output(h, LED_PIN)

print(f"Стартиране на PWM на GPIO {LED_PIN}...")
try:
    # Демо: Плавно променяща се яркост
    while True:
        # Увеличаване на яркостта
        for duty_cycle in range(0, 101, 5):
            lgpio.tx_pwm(h, LED_PIN, FREQUENCY, duty_cycle)
            time.sleep(0.05)
        # Намаляване на яркостта
        for duty_cycle in range(100, -1, -5):
            lgpio.tx_pwm(h, LED_PIN, FREQUENCY, duty_cycle)
            time.sleep(0.05)
except KeyboardInterrupt:
    pass
finally:
    # Спиране на PWM и затваряне
    lgpio.tx_pwm(h, LED_PIN, FREQUENCY, 0)
    lgpio.gpiochip_close(h)
    print("PWM спрян.")
