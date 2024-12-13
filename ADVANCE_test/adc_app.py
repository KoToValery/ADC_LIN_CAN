# adc_app.py
# Copyright 2004 - 2024  biCOMM Design Ltd
#
# AUTH: Kostadin Tosev
# DATE: 2024
#
# Target: RPi5
# Project CIS3
# Hardware PCB V3.0
# Tool: Python 3
#
# Version: V01.01.09.2024.CIS3 - temporary 01
# 1. TestSPI,ADC - work. Measurement Voltage 0-10 V, resistive 0-1000 ohm
# 2. Test Power PI5V/4.5A - work
# 3. Test ADC communication - work
# 4. Test LIN communication - work
# 5. MQTT auto discovery of sensors - work

import os
import time
import json
import asyncio
import threading
import spidev
import paho.mqtt.client as mqtt
from quart import Quart, jsonify, websocket, send_from_directory
from collections import deque
import logging
import serial
from hypercorn.asyncio import serve
from hypercorn.config import Config
from datetime import datetime

# Настройка на логиране (само грешки и важни системни съобщения)
logging.basicConfig(
    level=logging.ERROR,  # Само грешки
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("adc_error.log"),
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

# MQTT конфигурация
MQTT_BROKER = 'localhost'  # Променете, ако брокерът е на друга машина
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'

# LIN конфигурация
SYNC_BYTE = 0x55
BREAK_DURATION = 0.00135  # 1.35 ms
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

# Функция за изчисляване на LIN checksum
def enhanced_checksum(data):
    """Изчислява LIN контролната сума."""
    return (~sum(data) & 0xFF)

# Инициализация на данните
latest_data = {
    "adc_channels": {
        "channel_0": "0.00 V",
        "channel_1": "0.00 V",
        "channel_2": "0.00 V",
        "channel_3": "0.00 V",
        "channel_4": "0.00 Ω",
        "channel_5": "0.00 Ω"
    },
    "lin_sensors": {
        "Temperature": "0.00 °C",
        "Humidity": "0.00 %"
    }
}

# Инициализация на Quart приложението
app = Quart(__name__)

# Ограничаване на логовете на Quart до грешки
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

clients = set()

@app.route('/data')
async def data_route():
    """HTTP маршрут за получаване на последните ADC и LIN данни."""
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """Маршрут за проверка на здравето на приложението."""
    return '', 200

@app.route('/')
async def index():
    """Коренов маршрут за Ingress."""
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Грешка при обслужване на index.html: {e}")
        return jsonify({"error": "Файлът index.html не е намерен."}), 404

@app.websocket('/ws')
async def ws_route():
    """WebSocket маршрут за реалновремеви актуализации."""
    try:
        while True:
            await websocket.send(json.dumps(latest_data))
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket грешка: {e}")

# Инициализация на SPI
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI интерфейс за ADC е инициализиран.")
except Exception as e:
    logger.error(f"Грешка при инициализация на SPI: {e}")

# Инициализация на UART за LIN
try:
    uart = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART интерфейс е инициализиран на {UART_PORT} с baudrate {UART_BAUDRATE}.")
except Exception as e:
    logger.error(f"Грешка при инициализация на LIN UART: {e}")
    uart = None

# MQTT callback функции
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.error("MQTT е свързан успешно към брокера")
        client.connected_flag = True
        # Публикуване на статус "online"
        publish_availability("online")
    else:
        logger.error(f"MQTT връзка неуспешна с код: {rc}")
        client.connected_flag = False

def on_disconnect(client, userdata, rc):
    logger.error(f"MQTT е прекъснал връзката с код: {rc}")
    client.connected_flag = False
    if rc != 0:
        logger.error("Неочаквано прекъсване на MQTT връзката. Опит за повторно свързване...")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Грешка при повторно свързване към MQTT: {e}")

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

# Стартиране на MQTT цикъла в отделен нишка
mqtt_thread = threading.Thread(target=mqtt_client.loop_forever)
mqtt_thread.start()

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
    """
    Чете суровата стойност на ADC от конкретен канал.
    """
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            logger.debug(f"ADC Канал {channel} сурова стойност: {value}")
            return value
        except Exception as e:
            logger.error(f"Грешка при четене на ADC канал {channel}: {e}")
            return 0
    logger.warning(f"Невалиден ADC канал: {channel}")
    return 0

def calculate_voltage(adc_value):
    if adc_value < 10:  # Ниво на шум
        return "0.00 V"
    voltage = (adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER
    return f"{round(voltage, 2)} V"

def calculate_resistance(adc_value):
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return "0.00 Ω"
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return f"{round(resistance, 2)} Ω"

def setup_mqtt_discovery(channel, sensor_type):
    """Публикуване на MQTT discovery съобщения за Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3_channel_{channel}_{sensor_type}/config"
    payload = {
        "name": f"CIS3 Канал {channel} {sensor_type.capitalize()}",
        "state_topic": f"cis3/channel_{channel}/{sensor_type}",
        "unique_id": f"cis3_channel_{channel}_{sensor_type}",
        "unit_of_measurement": "V" if sensor_type == 'voltage' else "Ω",
        "device_class": "voltage" if sensor_type == 'voltage' else "resistance",
        "device": {
            "identifiers": ["cis3_device"],
            "name": "CIS3 Устройство",
            "model": "CIS3 Model",
            "manufacturer": "Your Company"
        }
    }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)
    logger.error(f"MQTT Discovery съобщение публикувано за канал {channel} ({sensor_type})")

def setup_mqtt_discovery_lin(sensor):
    """Публикуване на MQTT discovery съобщения за LIN сензори в Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/lin_{sensor}/config"
    unit = "°C" if sensor == "Temperature" else "%"
    device_class = "temperature" if sensor == "Temperature" else "humidity"
    payload = {
        "name": f"LIN Сензор {sensor}",
        "state_topic": f"cis3/lin/{sensor}",
        "unique_id": f"cis3_lin_{sensor}",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "device": {
            "identifiers": ["cis3_device"],
            "name": "CIS3 Устройство",
            "model": "CIS3 Model",
            "manufacturer": "Your Company"
        }
    }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)
    logger.error(f"MQTT Discovery съобщение публикувано за LIN сензор {sensor}")

def publish_sensor_data(channel, data, sensor_type):
    """Публикуване на данни от сензор към MQTT."""
    topic = f"cis3/channel_{channel}/{sensor_type}"
    mqtt_client.publish(topic, data, retain=True)
    logger.error(f"Публикувано съобщение на тема {topic}: {data}")

def publish_lin_data(sensor, value):
    """Публикуване на LIN данни към MQTT."""
    topic = f"cis3/lin/{sensor}"
    mqtt_client.publish(topic, value, retain=True)
    logger.error(f"Публикувано LIN съобщение на тема {topic}: {value}")

def publish_availability(status):
    """Публикуване на статус на наличност към MQTT."""
    topic = "cis3/availability"
    mqtt_client.publish(topic, status, retain=True)
    logger.error(f"Публикувано съобщение на тема {topic}: {status}")

@app.route('/data')
async def data_route():
    """HTTP маршрут за получаване на последните ADC и LIN данни."""
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """Маршрут за проверка на здравето на приложението."""
    return '', 200

@app.route('/')
async def index():
    """Коренов маршрут за Ingress."""
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Грешка при обслужване на index.html: {e}")
        return jsonify({"error": "Файлът index.html не е намерен."}), 404

@app.websocket('/ws')
async def ws_route():
    """WebSocket маршрут за реалновремеви актуализации."""
    try:
        while True:
            await websocket.send(json.dumps(latest_data))
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket грешка: {e}")

def send_break():
    """
    Изпраща BREAK сигнал за LIN комуникация.
    """
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)

def send_header(pid):
    """
    Изпраща SYNC + PID хедър към слейва и изчиства UART буфера.
    """
    ser.reset_input_buffer()  # Изчистване на UART буфера преди изпращане
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.error(f"Изпратен Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)  # Кратка пауза за обработка от слейва

def read_response(expected_data_length, pid):
    """
    Чете отговора от слейва, като търси SYNC + PID и след това извлича данните.
    """
    expected_length = expected_data_length  # 3 байта: 2 данни + 1 checksum
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # Увеличен таймаут до 2 секунди
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.error(f"Получени байтове: {data.hex()}")

            # Търсене на SYNC + PID
            index = buffer.find(sync_pid)
            if index != -1:
                logger.error(f"Намерен SYNC + PID на индекс {index}: {buffer[index:index+2].hex()}")
                # Изваждаме всичко преди и включително SYNC + PID
                buffer = buffer[index + 2:]
                logger.error(f"Филтриран буфер след SYNC + PID: {buffer.hex()}")

                # Проверка дали има достатъчно байтове за данните и контролна сума
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.error(f"Филтриран отговор: {response.hex()}")
                    return response
                else:
                    # Изчакване за останалите данни
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.error(f"Получени байтове при изчакване: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.error(f"Филтриран отговор след изчакване: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)

    logger.error("Не е получен валиден отговор в рамките на таймаута.")
    return None

def process_response(response, pid):
    """
    Обработва получения отговор, проверява контролната сума и извежда данните.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.error(f"Получена Checksum: 0x{received_checksum:02X}, Изчислена Checksum: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                formatted_value = f"{value:.2f} °C"
                latest_data["lin_sensors"]["Temperature"] = formatted_value
                publish_lin_data(sensor, formatted_value)
            elif sensor == 'Humidity':
                formatted_value = f"{value:.2f} %"
                latest_data["lin_sensors"]["Humidity"] = formatted_value
                publish_lin_data(sensor, formatted_value)
            else:
                logger.error(f"Непознат PID {pid}: Стойност={value}")
        else:
            logger.error("Несъответствие на Checksum.")
    else:
        logger.error("Невалидна дължина на отговора.")

def setup_mqtt_discovery_all():
    """Публикуване на всички MQTT discovery съобщения за ADC и LIN сензори."""
    # Настройка за ADC каналите
    for ch in range(6):
        if ch < 4:
            setup_mqtt_discovery(ch, 'voltage')
        else:
            setup_mqtt_discovery(ch, 'resistance')
    # Настройка за LIN сензорите
    for sensor in ["Temperature", "Humidity"]:
        setup_mqtt_discovery_lin(sensor)

async def setup_discovery():
    """Настройка на MQTT auto-discovery след инициализация."""
    await asyncio.sleep(10)  # Изчакайте 10 секунди за инициализация на MQTT
    setup_mqtt_discovery_all()

async def process_adc_and_lin():
    """Основен цикъл за LIN и ADC комуникацията."""
    while True:
        # Обработка на ADC
        for i in range(6):
            if i < 4:
                voltage = calculate_voltage(read_adc(i))
                latest_data["adc_channels"][f"channel_{i}"] = voltage
                publish_sensor_data(i, voltage, 'voltage')
            else:
                resistance = calculate_resistance(read_adc(i))
                latest_data["adc_channels"][f"channel_{i}"] = resistance
                publish_sensor_data(i, resistance, 'resistance')

        # Обработка на LIN
        for pid in PID_DICT.keys():
            if uart:
                send_header(pid)
                # Изчакване да се изпълни асинхронно, за да не блокира event loop
                response = await asyncio.to_thread(read_response, 3, pid)
                if response:
                    process_response(response, pid)
                await asyncio.sleep(0.1)  # Кратка пауза между заявките

        await asyncio.sleep(2)  # Интервал между цикли

async def monitor_availability():
    """Мониторинг на наличността и публикуване на статус."""
    publish_availability("online")
    while True:
        await asyncio.sleep(60)  # Публикуване на наличност на всеки 60 секунди
        publish_availability("online")

async def main():
    """Стартиране на Quart приложението и фоновите задачи."""
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    quart_task = asyncio.create_task(serve(app, config))
    await asyncio.gather(
        quart_task,
        setup_discovery(),
        process_adc_and_lin(),
        monitor_availability()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.error("Прекратяване на ADC & LIN Advanced Add-on от потребителя...")
    except Exception as e:
        logger.error(f"Неочаквана грешка: {e}")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        spi.close()
        if uart:
            uart.close()
        logger.error("ADC & LIN Advanced Add-on е затворен.")
