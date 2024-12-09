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
BREAK_DURATION = 1.35e-3  # 1.35ms break signal

# PID Definitions
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51  # Нов PID за влажност

# PID Dictionary
PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

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
            "Temperature": 0.0,
            "Humidity": 0.0
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
async def data_route():
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
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

# Log helper
def log_message(message, role="MASTER"):
    print(f"[{datetime.now()}] [{role}] {message}", flush=True)

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

def send_header(pid):
    """
    Изпраща SYNC + PID хедър към слейва и изчиства UART буфера.
    """
    ser.reset_input_buffer()  # Изчистване на UART буфера преди изпращане
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    log_message(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
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
            log_message(f"Received bytes: {data.hex()}", role="MASTER")

            # Търсене на SYNC + PID
            index = buffer.find(sync_pid)
            if index != -1:
                log_message(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}", role="MASTER")
                # Изваждаме всичко преди и включително SYNC + PID
                buffer = buffer[index + 2:]
                log_message(f"Filtered Buffer after SYNC + PID: {buffer.hex()}", role="MASTER")

                # Проверка дали има достатъчно байтове за данните и контролна сума
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    log_message(f"Filtered Response: {response.hex()}", role="MASTER")
                    return response
                else:
                    # Изчакване за останалите данни
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            log_message(f"Received bytes while waiting: {more_data.hex()}", role="MASTER")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        log_message(f"Filtered Response after waiting: {response.hex()}", role="MASTER")
                        return response
        else:
            time.sleep(0.01)

    log_message("No valid response received within timeout.", role="MASTER")
    return None

def process_response(response, pid):
    """
    Обработва получения отговор, проверява контролната сума и извежда данните.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        log_message(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}", role="MASTER")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                log_message(f"Temperature: {value:.2f}°C", role="MASTER")
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
            elif sensor == 'Humidity':
                log_message(f"Humidity: {value:.2f}%", role="MASTER")
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
            else:
                log_message(f"Unknown PID {pid}: Value={value}", role="MASTER")
        else:
            log_message("Checksum mismatch.", role="MASTER")
    else:
        log_message("Invalid response length.", role="MASTER")

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
        for pid in PID_DICT.keys():
            send_header(pid)
            # Изчакване да се изпълни асинхронно, за да не блокира event loop
            response = await asyncio.to_thread(read_response, 3, pid)
            if response:
                process_response(response, pid)
            await asyncio.sleep(0.1)  # Кратка пауза между заявките

        # Изпращане на данни към WebSocket клиенти
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        await asyncio.sleep(2)  # Интервал между цикли

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
