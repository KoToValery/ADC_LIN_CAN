import os
import time
import json
import asyncio
import threading
import spidev
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, Response
from collections import deque
import logging

# Конфигурация на логването
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG за максимално детайлно логване
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("mqtt_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
HTTP_PORT = 8091
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED = 1000000
SPI_MODE = 0
VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10000
MOVING_AVERAGE_WINDOW = 30
EMA_ALPHA = 0.1

MQTT_BROKER = 'localhost'  # Променете, ако брокерът е на друга машина
MQTT_PORT = 1883
MQTT_USERNAME = 'adc'       # Вашето потребителско име
MQTT_PASSWORD = 'adc'       # Вашата парола
MQTT_DISCOVERY_PREFIX = 'homeassistant'

# Данни за съхранение
latest_data = {
    "adc_channels": {}
}

# Flask приложение
app = Flask(__name__)

# SPI инициализация
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = SPI_MODE

# MQTT Callback Функции
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("MQTT Свързан успешно към брокера")
        client.connected_flag = True
        # Публикуване на статус "online"
        publish_availability("online")
    else:
        logger.error(f"MQTT Връзка неуспешна с код: {rc}")
        client.connected_flag = False

def on_disconnect(client, userdata, rc):
    logger.warning(f"MQTT Отключване с код: {rc}")
    client.connected_flag = False
    if rc != 0:
        logger.warning("MQTT Неочаквано отключване. Опит за повторно свързване...")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"MQTT Грешка при повторно свързване: {e}")

# Инициализация на MQTT клиента
mqtt.Client.connected_flag = False  # Персонализиран флаг за свързаност

mqtt_client = mqtt.Client(client_id="adc_client")
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Регистриране на callback функции
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
except Exception as e:
    logger.error(f"Неуспешна опит за свързване към MQTT брокера: {e}")
    exit(1)

mqtt_client.loop_start()

# Филтриране
buffers_ma = {i: deque(maxlen=MOVING_AVERAGE_WINDOW) for i in range(6)}
values_ema = {i: None for i in range(6)}

def apply_moving_average(value, channel):
    buffers_ma[channel].append(value)
    if len(buffers_ma[channel]) == MOVING_AVERAGE_WINDOW:
        return sum(buffers_ma[channel]) / MOVING_AVERAGE_WINDOW
    return value

def apply_ema(value, channel):
    if values_ema[channel] is None:
        values_ema[channel] = value
    else:
        values_ema[channel] = EMA_ALPHA * value + (1 - EMA_ALPHA) * values_ema[channel]
    return values_ema[channel]

def read_adc(channel):
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        adc = spi.xfer2(cmd)
        value = ((adc[1] & 3) << 8) + adc[2]
        logger.debug(f"Raw ADC value for channel {channel}: {value}")
        return value
    return 0

def calculate_voltage(adc_value):
    if adc_value < 10:  # Noise threshold
        return 0.0
    return round((adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER, 2)

def calculate_resistance(adc_value):
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return round(resistance, 2)

def setup_mqtt_discovery(channel, sensor_type):
    """Публикуване на минимални MQTT discovery съобщения за Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3/channel_{channel}/config"
    if sensor_type == 'voltage':
        payload = {
            "name": f"CIS3 Channel {channel} Voltage",
            "state_topic": f"cis3/channel_{channel}/voltage",
            "unique_id": f"cis3_channel_{channel}_voltage"
        }
    elif sensor_type == 'resistance':
        payload = {
            "name": f"CIS3 Channel {channel} Resistance",
            "state_topic": f"cis3/channel_{channel}/resistance",
            "unique_id": f"cis3_channel_{channel}_resistance"
        }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)
    logger.info(f"MQTT Discovery съобщение публикувано за канал {channel} ({sensor_type})")

def publish_sensor_data(channel, data, sensor_type):
    """Публикуване на данни от сензора към MQTT."""
    if sensor_type == 'voltage':
        topic = f"cis3/channel_{channel}/voltage"
    else:
        topic = f"cis3/channel_{channel}/resistance"
    payload = json.dumps(data)
    mqtt_client.publish(topic, payload)
    logger.debug(f"Публикувано съобщение на тема {topic}: {payload}")

def publish_availability(status):
    """Публикуване на статус на наличност към MQTT."""
    topic = "cis3/availability"
    mqtt_client.publish(topic, status, retain=True)
    logger.info(f"Публикувано съобщение на тема {topic}: {status}")

async def setup_discovery():
    await asyncio.sleep(10)  # Изчакайте 10 секунди за инициализация на MQTT
    for ch in range(6):
        if ch < 4:
            setup_mqtt_discovery(ch, 'voltage')
        else:
            setup_mqtt_discovery(ch, 'resistance')

async def process_adc_data():
    while True:
        for channel in range(6):
            raw_value = read_adc(channel)
            if channel < 4:
                value = calculate_voltage(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": value, "unit": "V"}
                publish_sensor_data(channel, {"voltage": value}, 'voltage')
            else:
                value = calculate_resistance(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}
                publish_sensor_data(channel, {"resistance": value}, 'resistance')
        await asyncio.sleep(1)

async def monitor_availability():
    publish_availability("online")
    while True:
        await asyncio.sleep(60)  # Публикуване на наличност на всеки 60 секунди
        publish_availability("online")

async def main():
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True).start()
    await asyncio.gather(
        setup_discovery(),
        process_adc_data(),
        monitor_availability()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Скриптът е прекратен от потребителя")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        spi.close()
        logger.info("MQTT клиентът е изключен и SPI е затворен")
