# adc_app.py
# AUTH: Kostadin Tosev
# DATE: 2024

import os
import time
import asyncio
import spidev
from collections import deque
from quart import Quart, jsonify, send_from_directory, websocket
import logging
import serial
import json
from hypercorn.asyncio import serve
from hypercorn.config import Config
from datetime import datetime

# Настройка на логиране
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ADC & LIN')

logger.info("ADC & LIN Advanced Add-on started.")

# Конфигурация
HTTP_PORT = 8099
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED = 1000000
SPI_MODE = 0

VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10000
MOVING_AVERAGE_WINDOW = 10

SUPERVISOR_WS_URL = os.getenv("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = os.getenv('INGRESS_PATH', '')

if not SUPERVISOR_TOKEN:
    logger.error("SUPERVISOR_TOKEN is not set. Exiting.")
    exit(1)

# LIN Constants
SYNC_BYTE = 0x55
PID_TEMPERATURE = 0x50  # Статичен PID за температура
BREAK_DURATION = 1.35e-3  # 1.35ms break signal

# Инициализация на данните
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
            "value": 0.0
        }
    }
}

# Инициализация на Quart приложението
app = Quart(__name__)

# Ограничаване на логовете на Quart
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

clients = set()

@app.route('/data')
async def data():
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

@app.route('/')
async def index():
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            await websocket.receive()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")

# Инициализация на SPI
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

# UART Configuration
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600
ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)

# Log helper
def log_message(message):
    print(f"[{datetime.now()}] [MASTER] {message}", flush=True)

def enhanced_checksum(data):
    """
    Изчислява контролната сума като сумира всички байтове, взема само най-ниската част
    и връща обратната стойност.
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
    time.sleep(0.0001)

def send_header():
    """
    Изпраща SYNC + PID хедър към слейва и изчиства UART буфера.
    """
    ser.reset_input_buffer()  # Изчистване на UART буфера преди изпращане
    send_break()
    ser.write(bytes([SYNC_BYTE, PID_TEMPERATURE]))
    log_message(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{PID_TEMPERATURE:02X}")
    time.sleep(0.1)  # Кратка пауза за обработка от слейва

def read_response():
    """
    Чете отговора от слейва, като търси SYNC + PID и след това извлича данните.
    """
    expected_length = 3  # 3 байта: 2 данни + 1 checksum
    start_time = time.time()
    response = bytearray()
    while (time.time() - start_time) < 1.0:  # 1 секунда таймаут
        if ser.in_waiting > 0:
            response.extend(ser.read(ser.in_waiting))
        else:
            time.sleep(0.01)

    if response:
        log_message(f"Raw Received Data: {response.hex()}")

        # Търсене на SYNC + PID
        sync_pid = bytes([SYNC_BYTE, PID_TEMPERATURE])
        index = response.find(sync_pid)
        if index != -1:
            log_message(f"Found SYNC + PID at position {index}: SYNC=0x{SYNC_BYTE:02X}, PID=0x{PID_TEMPERATURE:02X}")
            # Изваждаме всичко преди и включително SYNC + PID
            response = response[index + 2:]
            log_message(f"Filtered Response: {response.hex()}")

            # Проверка дали има достатъчно байтове за данните и контролна сума
            if len(response) >= expected_length:
                data = response[:expected_length]
                return data
            else:
                log_message("Incomplete response received after SYNC + PID.")
                return None
        else:
            log_message("SYNC + PID not found in response.")
            return None
    else:
        log_message("No data received.")
        return None

def process_response(response):
    """
    Обработва получения отговор, проверява контролната сума и извежда температурата.
    """
    if response and len(response) == 3:
        data = response[:2]  # Първите 2 байта са данни
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([PID_TEMPERATURE] + list(data))
        log_message(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")
        if received_checksum == calculated_checksum:
            # Интерпретиране на температурата в малко-ендий формат
            temperature = int.from_bytes(data, 'little') / 100.0
            log_message(f"Temperature: {temperature:.2f}°C")
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature
        else:
            log_message("Checksum mismatch.")
    else:
        log_message("Invalid response length.")

buffers_ma = {i: deque(maxlen=MOVING_AVERAGE_WINDOW) for i in range(6)}

def read_adc(channel):
    """
    Чете суровата стойност на ADC от конкретен канал.
    """
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            logger.debug(f"ADC Channel {channel} raw value: {value}")
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def process_adc_data(channel):
    """
    Изчислява средната стойност за ADC канал и я конвертира във волтаж или съпротивление.
    """
    raw_value = read_adc(channel)
    buffers_ma[channel].append(raw_value)
    average = sum(buffers_ma[channel]) / len(buffers_ma[channel])
    if channel < 4:
        voltage = (average / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER
        return round(voltage, 2)
    else:
        if average == 0:
            logger.warning(f"ADC Channel {channel} average is zero, cannot calculate resistance.")
            return 0.0
        resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - average)) / average) / 10
        return round(resistance, 2)

async def process_adc_and_lin():
    """
    Основен цикъл за LIN и ADC комуникацията.
    """
    while True:
        # Обработка на ADC
        for i in range(6):
            if i < 4:
                voltage = process_adc_data(i)
                latest_data["adc_channels"][f"channel_{i}"]["voltage"] = voltage
                logger.debug(f"ADC Channel {i} Voltage: {voltage} V")
            else:
                resistance = process_adc_data(i)
                latest_data["adc_channels"][f"channel_{i}"]["resistance"] = resistance
                logger.debug(f"ADC Channel {i} Resistance: {resistance} Ω")

        # Обработка на LIN
        send_header()
        response = read_response()
        process_response(response)

        # Изпращане на данни към WebSocket клиенти
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        await asyncio.sleep(2)  # Интервал между заявките

async def main():
    """
    Стартиране на задачите.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    quart_task = asyncio.create_task(serve(app, config))
    logger.info("Quart HTTP server started.")
    adc_lin_task = asyncio.create_task(process_adc_and_lin())
    logger.info("ADC and LIN processing task started.")
    await asyncio.gather(
        quart_task,
        adc_lin_task
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC & LIN Advanced Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        spi.close()
        ser.close()
        logger.info("ADC & LIN Advanced Add-on has been shut down.")
