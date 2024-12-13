# adc_app.py
# Copyright 2004 - 2024 biCOMM Design Ltd
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
# 5. - updated with MQTT integration


import os
import time
import json
import asyncio
import threading
import spidev
from quart import Quart, jsonify, send_from_directory, websocket
from collections import deque
import logging
import paho.mqtt.client as mqtt

# --------------------------- Logging Configuration --------------------------- #

# Конфигуриране на логиране за записване на важни съобщения и грешки
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ADC, LIN & MQTT')

logger.info("ADC, LIN & MQTT Advanced Add-on started.")

# ------------------------------- Configuration ------------------------------- #

# HTTP Server Configuration
HTTP_PORT = 8099

# SPI Configuration for ADC
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED = 1000000
SPI_MODE = 0

# ADC and Voltage Divider Configuration
VREF = 3.3                       # Reference voltage for ADC
ADC_RESOLUTION = 1023.0          # 10-bit ADC resolution (0-1023)
VOLTAGE_MULTIPLIER = 3.31        # Multiplier based on voltage divider or amplifier
RESISTANCE_REFERENCE = 10000     # Reference resistance in ohms

# Moving Average Window Sizes (Configurable via environment variables)
try:
    MOVING_AVERAGE_WINDOW_VOLTAGE = int(os.getenv("MOVING_AVERAGE_WINDOW_VOLTAGE", "10"))
    MOVING_AVERAGE_WINDOW_RESISTANCE = int(os.getenv("MOVING_AVERAGE_WINDOW_RESISTANCE", "10"))
except ValueError as e:
    logger.error(f"Invalid moving average window size: {e}")
    MOVING_AVERAGE_WINDOW_VOLTAGE = 10
    MOVING_AVERAGE_WINDOW_RESISTANCE = 10

# EMA Configuration (Configurable via environment variables)
try:
    EMA_ALPHA_VOLTAGE = float(os.getenv("EMA_ALPHA_VOLTAGE", "0.1"))
    EMA_ALPHA_RESISTANCE = float(os.getenv("EMA_ALPHA_RESISTANCE", "0.1"))
except ValueError as e:
    logger.error(f"Invalid EMA alpha value: {e}")
    EMA_ALPHA_VOLTAGE = 0.1
    EMA_ALPHA_RESISTANCE = 0.1

# Low-Pass Filter Configuration (Премахнат)
# LOW_PASS_ALPHA = float(os.getenv("LOW_PASS_ALPHA", "0.1"))  # Премахнато

# UART Configuration for LIN Communication
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

# MQTT Configuration (Credentials and Broker Details)
MQTT_BROKER = 'localhost'         # MQTT broker address
MQTT_PORT = 1883                  # MQTT broker port
MQTT_USERNAME = os.getenv("MQTT_USER", "mqtt")    # Fetch from environment
MQTT_PASSWORD = os.getenv("MQTT_PASS", "mqtt_pass") # Fetch from environment
MQTT_DISCOVERY_PREFIX = 'homeassistant'  # MQTT discovery prefix
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"  # MQTT client ID

# --------------------------- Initialize Components --------------------------- #

# Initialize Quart application for Web UI
app = Quart(__name__)

# Suppress Quart's default logging to prevent clutter
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

# Determine the base directory for serving static files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Set to keep track of connected WebSocket clients
clients = set()

# Initialize SPI for ADC communication
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

# Initialize MQTT client with paho-mqtt
mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)

# Set MQTT credentials
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# --------------------------- Define PID Constants --------------------------- #

# LIN Communication Constants
SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3  # 1.35ms break signal

# PID Definitions
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51  # New PID for Humidity

# PID Dictionary Mapping
PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

# ------------------------------- Initialize Data ------------------------------- #

# Data structure to store latest readings
latest_data = {
    "adc_channels": {
        "channel_0": {"voltage": 0.0, "unit": "V"},
        "channel_1": {"voltage": 0.0, "unit": "V"},
        "channel_2": {"voltage": 0.0, "unit": "V"},
        "channel_3": {"voltage": 0.0, "unit": "V"},
        "channel_4": {"resistance": 0.0, "unit": "Ω"},
        "channel_5": {"resistance": 0.0, "unit": "Ω"}
    },
    "slave_sensors": {
        "slave_1": {
            "Temperature": 0.0,
            "Humidity": 0.0
        }
    }
}

# --------------------------- Define Web Routes --------------------------- #

@app.route('/data')
async def data_route():
    """
    Route за предоставяне на последните данни на сензорите във формат JSON.
    """
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """
    Health check route.
    """
    return '', 200

@app.route('/')
async def index():
    """
    Serve the index.html file.
    """
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    """
    WebSocket route за реално време обновяване на данните.
    """
    logger.info("Нова WebSocket връзка установена.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            await websocket.receive()  # Поддържане на връзката
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket грешка: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket връзката е затворена.")

# --------------------------- Define Filtering Functions --------------------------- #

# Инициализиране на буфери за Moving Average
buffers_ma_voltage = {f"channel_{i}": deque(maxlen=MOVING_AVERAGE_WINDOW_VOLTAGE) for i in range(4)}
buffers_ma_resistance = {f"channel_{i}": deque(maxlen=MOVING_AVERAGE_WINDOW_RESISTANCE) for i in range(4,6)}

# Инициализиране на EMA стойности
values_ema_voltage = {f"channel_{i}": None for i in range(4)}
values_ema_resistance = {f"channel_{i}": None for i in range(4,6)}

def moving_average(value, channel, is_voltage=True):
    """
    Изчислява движещата се средна стойност за даден канал.
    """
    if is_voltage:
        buffers_ma_voltage[channel].append(value)
        if len(buffers_ma_voltage[channel]) == MOVING_AVERAGE_WINDOW_VOLTAGE:
            return sum(buffers_ma_voltage[channel]) / MOVING_AVERAGE_WINDOW_VOLTAGE
        return sum(buffers_ma_voltage[channel]) / len(buffers_ma_voltage[channel])
    else:
        buffers_ma_resistance[channel].append(value)
        if len(buffers_ma_resistance[channel]) == MOVING_AVERAGE_WINDOW_RESISTANCE:
            return sum(buffers_ma_resistance[channel]) / MOVING_AVERAGE_WINDOW_RESISTANCE
        return sum(buffers_ma_resistance[channel]) / len(buffers_ma_resistance[channel])

def exponential_moving_average(value, channel, is_voltage=True):
    """
    Изчислява експоненциалната движеща се средна стойност (EMA) за даден канал.
    """
    if is_voltage:
        if values_ema_voltage[channel] is None:
            values_ema_voltage[channel] = value
        else:
            values_ema_voltage[channel] = EMA_ALPHA_VOLTAGE * value + (1 - EMA_ALPHA_VOLTAGE) * values_ema_voltage[channel]
        return values_ema_voltage[channel]
    else:
        if values_ema_resistance[channel] is None:
            values_ema_resistance[channel] = value
        else:
            values_ema_resistance[channel] = EMA_ALPHA_RESISTANCE * value + (1 - EMA_ALPHA_RESISTANCE) * values_ema_resistance[channel]
        return values_ema_resistance[channel]

# --------------------------- Define Helper Functions --------------------------- #

def enhanced_checksum(data):
    """
    Изчислява чексумата чрез сумиране на всички байтове, вземане на най-ниския байт и връщане на неговия инверс.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    """
    Изпраща BREAK сигнал за LIN комуникация.
    """
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)  # Кратка пауза след break

def send_header(pid):
    """
    Изпраща SYNC + PID заглавие към LIN slave и изчиства UART буфера.
    """
    ser.reset_input_buffer()  # Изчистване на UART буфера преди изпращане
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.info(f"Изпратен Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.05)  # Кратка пауза за обработка от slave

def read_response(expected_data_length, pid):
    """
    Чете отговора от LIN slave, търсейки SYNC + PID и след това извлича данните.
    """
    expected_length = expected_data_length  # 3 байта: 2 данни + 1 чексума
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # 2-секунден timeout
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Получени байтове: {data.hex()}")  # Debug лог за получени байтове

            # Търсене на SYNC + PID в буфера
            index = buffer.find(sync_pid)
            if index != -1:
                logger.info(f"Намерено SYNC + PID на индекс {index}: {buffer[index:index+2].hex()}")
                # Премахване на всичко преди и включително SYNC + PID
                buffer = buffer[index + 2:]
                logger.debug(f"Филтриран буфер след SYNC + PID: {buffer.hex()}")

                # Проверка дали има достатъчно байтове за данни и чексума
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.info(f"Филтриран отговор: {response.hex()}")
                    return response
                else:
                    # Изчакване за останалите данни
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Получени байтове при изчакване: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.info(f"Филтриран отговор след изчакване: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)

    logger.error("Не е получен валиден отговор в рамките на timeout-а.")
    return None

def process_response(response, pid):
    """
    Обработва получения отговор, проверява чексумата и обновява данните.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.info(f"Получена чексума: 0x{received_checksum:02X}, Изчислена чексума: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                logger.info(f"Температура: {value:.2f}°C")
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
            elif sensor == 'Humidity':
                logger.info(f"Влажност: {value:.2f}%")
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
            else:
                logger.warning(f"Unknown PID {pid}: Value={value}")
        else:
            logger.error("Несъответствие на чексумата.")
    else:
        logger.error("Невалидна дължина на отговора.")

# --------------------------- Define MQTT Callback Functions --------------------------- #

def on_connect(client, userdata, flags, rc):
    """
    Callback функция при свързване към MQTT брокера.
    """
    if rc == 0:
        logger.info("Свързан към MQTT Broker.")
        client.publish("cis3/status", "online", retain=True)
        publish_mqtt_discovery(client)
    else:
        logger.error(f"Неуспешно свързване към MQTT Broker, код {rc}")

def on_disconnect(client, userdata, rc):
    """
    Callback функция при разединяване от MQTT брокера.
    """
    logger.error(f"Разединено от MQTT Broker с код {rc}")
    if rc != 0:
        logger.warning("Неочаквано разединяване. Опит за повторно свързване.")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Неуспешен опит за повторно свързване: {e}")

# --------------------------- MQTT Initialization and Loop --------------------------- #

# Регистриране на MQTT callback функции
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    """
    Стартира MQTT клиентния цикъл в отделен тред.
    """
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT цикъл грешка: {e}")

# Стартиране на MQTT цикъла в отделен тред
mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

# --------------------------- MQTT Discovery Function --------------------------- #

def publish_mqtt_discovery(client):
    """
    Публикува MQTT съобщения за откриване на всички сензори за автоматично откриване в Home Assistant.
    """
    # Списък за всички конфигурации на сензори
    sensors = []

    # Откриване на ADC канали
    for i in range(6):
        channel = f"channel_{i}"
        if i < 4:
            # Сензори за напрежение
            sensor_type = "voltage"
            unit = "V"
            state_topic = f"cis3/{channel}/voltage"
            unique_id = f"cis3_{channel}_voltage"
            name = f"CIS3 Channel {i} Voltage"
            device_class = "voltage"
            icon = "mdi:flash"
            value_template = "{{ value }}"
        else:
            # Сензори за съпротивление
            sensor_type = "resistance"
            unit = "Ω"
            state_topic = f"cis3/{channel}/resistance"
            unique_id = f"cis3_{channel}_resistance"
            name = f"CIS3 Channel {i} Resistance"
            device_class = "resistance"  # Уверете се, че Home Assistant разпознава това
            icon = "mdi:water-percent"
            value_template = "{{ value }}"

        # Конфигурация на сензора
        sensor = {
            "name": name,
            "unique_id": unique_id,
            "state_topic": state_topic,
            "unit_of_measurement": unit,
            "device_class": device_class,
            "icon": icon,
            "value_template": value_template,
            "availability_topic": "cis3/status",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["cis3_device"],
                "name": "CIS3 Device",
                "model": "CIS3 PCB V3.0",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
        sensors.append(sensor)

    # Откриване на Slave сензори
    for pid, sensor_name in PID_DICT.items():
        sensor = {
            "name": f"CIS3 Slave 1 {sensor_name}",
            "unique_id": f"cis3_slave_1_{sensor_name.lower()}",
            "state_topic": f"cis3/slave_1/{sensor_name.lower()}",
            "unit_of_measurement": "%" if sensor_name == "Humidity" else "°C",
            "device_class": "humidity" if sensor_name == "Humidity" else "temperature",
            "icon": "mdi:water-percent" if sensor_name == "Humidity" else "mdi:thermometer",
            "value_template": "{{ value }}",
            "availability_topic": "cis3/status",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["cis3_device"],
                "name": "CIS3 Device",
                "model": "CIS3 PCB V3.0",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
        sensors.append(sensor)

    # Публикуване на откривателни съобщения за всеки сензор
    for sensor in sensors:
        discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{sensor['unique_id']}/config"
        client.publish(discovery_topic, json.dumps(sensor), retain=True)
        logger.info(f"Публикувано MQTT откриване за {sensor['name']} на {discovery_topic}")

# --------------------------- ADC Reading and Processing --------------------------- #

def read_adc(channel):
    """
    Чете сурова ADC стойност от посочения канал.
    """
    try:
        adc_response = spi.xfer2([1, (8 + channel) << 4, 0])
        adc_value = ((adc_response[1] & 3) << 8) + adc_response[2]
        logger.debug(f"ADC Channel {channel} Raw Value: {adc_value}")
        return adc_value
    except Exception as e:
        logger.error(f"Грешка при четене на ADC канал {channel}: {e}")
        return 0

def calculate_voltage(adc_value):
    """
    Изчислява напрежението въз основа на ADC стойността.
    """
    if adc_value < 10:  # Noise threshold
        return 0.0
    return round((adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER, 2)

def calculate_resistance(adc_value):
    """
    Изчислява съпротивлението въз основа на ADC стойността.
    """
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return round(resistance, 2)

def process_channel(channel):
    """
    Обработва даден канал, прилага филтри и изчислява стойността.
    """
    raw_value = read_adc(channel)
    is_voltage = channel < 4
    filtered_ma = moving_average(raw_value, channel, is_voltage=is_voltage)
    filtered_ema = exponential_moving_average(filtered_ma, channel, is_voltage=is_voltage)
    if is_voltage:
        return calculate_voltage(filtered_ema)
    return calculate_resistance(filtered_ema)

async def process_adc_and_lin():
    """
    Главен цикъл за комуникация с LIN и ADC.
    """
    while True:
        # Обработка на ADC канали
        for channel in range(6):
            value = process_channel(channel)
            if channel < 4:
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": value, "unit": "V"}
            else:
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}

        # Обработка на LIN комуникация за всеки PID
        for pid in PID_DICT.keys():
            send_header(pid)
            response = read_response(3, pid)
            if response:
                process_response(response, pid)
            await asyncio.sleep(0.05)  # Кратка пауза между заявките за PID

        # Изпращане на данните към свързаните WebSocket клиенти
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.info("Изпратени обновени данни към WebSocket клиенти.")

        # Публикуване на данните към MQTT
        publish_to_mqtt()

        # Изчакване за следващия цикъл
        await asyncio.sleep(0.5)  # Намаляване на интервала за обновяване

# --------------------------- Define MQTT Publishing Function --------------------------- #

def publish_to_mqtt():
    """
    Публикува последните данни към MQTT state теми.
    """
    # Публикуване на напрежение и съпротивление от ADC
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            # Тематика за напрежение
            state_topic = f"cis3/{channel}/voltage"
            payload = adc_data["voltage"]
            mqtt_client.publish(state_topic, str(payload))
            logger.info(f"Публикувано {channel} Напрежение: {payload} V към {state_topic}")
        else:
            # Тематика за съпротивление
            state_topic = f"cis3/{channel}/resistance"
            payload = adc_data["resistance"]
            mqtt_client.publish(state_topic, str(payload))
            logger.info(f"Публикувано {channel} Съпротивление: {payload} Ω към {state_topic}")

    # Публикуване на Slave сензори (Температура и Влажност)
    slave = latest_data["slave_sensors"]["slave_1"]
    for sensor, value in slave.items():
        if value is not None:
            state_topic = f"cis3/slave_1/{sensor.lower()}"
            mqtt_client.publish(state_topic, str(value))
            logger.info(f"Публикувано Slave_1 {sensor}: {value} към {state_topic}")
        else:
            logger.error(f"Slave_1 {sensor} стойност е None, пропускане на MQTT публикуване.")

# --------------------------- Define Quart HTTP Server --------------------------- #

async def start_quart():
    """
    Стартира HTTP сървъра Quart.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Стартиране на Quart HTTP сървър на порт {HTTP_PORT}")
    await serve(app, config)
    logger.info("Quart HTTP сървър стартиран.")

# --------------------------- Main Function --------------------------- #

async def main():
    """
    Главна функция за стартиране на Quart сървъра и обработка на ADC & LIN данни.
    """
    await asyncio.gather(
        start_quart(),
        process_adc_and_lin()
    )

# --------------------------- Entry Point --------------------------- #

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Спиране на ADC, LIN & MQTT Advanced Add-on...")
    except Exception as e:
        logger.error(f"Неочаквана грешка: {e}")
    finally:
        # Затваряне на SPI интерфейса
        spi.close()
        # Публикуване на статус offline и разединяване на MQTT
        mqtt_client.publish("cis3/status", "offline", retain=True)
        mqtt_client.disconnect()
        logger.info("ADC, LIN & MQTT Advanced Add-on е спрян.")
