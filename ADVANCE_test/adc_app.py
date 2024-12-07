# adc_app.py
# AUTH: Kostadin Tosev
# DATE: 2024

# Target: RPi5
# Project CIS3
# Hardware PCB V3.0
# Python 3

# Features:
# 1. ADC reading (Voltage channels 0-3, Resistance channels 4-5) - async tasks
# 2. LIN Communication for LED control & temperature reading (LINMaster class integrated)
# 3. Quart async web server for data retrieval /health and /data
# 4. Logging with Python logging module

import os
import time
import asyncio
import spidev
from collections import deque
from quart import Quart, jsonify, send_from_directory
import logging
import serial
import struct
from hypercorn.asyncio import serve
from hypercorn.config import Config

############################################
# Logging Setup
############################################
logging.basicConfig(
    level=logging.DEBUG,  # Можете да промените нивото на логване тук (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ADC & LIN')

logger.info("ADC & LIN Add-on started.")

############################################
# Configuration
############################################
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
EMA_ALPHA = 0.1
LED_VOLTAGE_THRESHOLD = 3.0

############################################
# Data Storage
############################################
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
            "value": 0.0,
            "led_state": "OFF"
        }
    }
}

############################################
# Quart App Initialization
############################################
app = Quart(__name__)

# Намаляване на нивото на логване на Quart, за да не показва GET /data заявки
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

# Получаване на абсолютния път до директорията на скрипта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route('/data')
async def data():
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

# Маршрут за основната страница
@app.route('/')
async def index():
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

############################################
# SPI Initialization
############################################
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

############################################
# LINMaster Class
############################################
class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=9600, uart_timeout=1):
        self.LIN_SYNC_BYTE = 0x55
        self.LED_ON_COMMAND = 0x01
        self.LED_OFF_COMMAND = 0x00
        self.BREAK_DURATION = 1.35e-3
        self.RESPONSE_TIMEOUT = 0.1

        try:
            self.ser = serial.Serial(
                uart_port,
                uart_baudrate,
                timeout=uart_timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            logger.info(f"LINMaster UART initialized on {uart_port} at {uart_baudrate} baud.")
        except Exception as e:
            logger.error(f"UART initialization error: {e}")
            self.ser = None

    def send_header(self, identifier):
        if self.ser:
            try:
                self.ser.break_condition = True
                time.sleep(self.BREAK_DURATION)
                self.ser.break_condition = False
                time.sleep(0.0001)
                self.ser.write(bytes([self.LIN_SYNC_BYTE]))
                pid = identifier & 0x3F
                self.ser.write(bytes([pid]))
                logger.debug(f"LINMaster sent header with PID: 0x{pid:02X}")
                return pid
            except Exception as e:
                logger.error(f"Error sending header: {e}")
                return None
        else:
            logger.error("UART not initialized, cannot send header.")
            return None

    def control_led(self, identifier, state):
        command = self.LED_ON_COMMAND if state == "ON" else self.LED_OFF_COMMAND
        logger.info(f"LINMaster sending LED command: {state}")
        try:
            pid = self.send_header(identifier)
            if pid is not None:
                frame = bytes([command])
                self.ser.write(frame)
                logger.debug(f"LINMaster sent data frame for LED: {frame.hex()}")
                return True
        except Exception as e:
            logger.error(f"Error sending LED command: {e}")
        return False

    def read_temperature(self, identifier):
        try:
            pid = self.send_header(identifier)
            if pid is not None:
                start_time = time.time()
                response = bytearray()
                while (time.time() - start_time) < self.RESPONSE_TIMEOUT:
                    if self.ser.in_waiting:
                        byte = self.ser.read(1)
                        response.extend(byte)
                        if len(response) >= 2:
                            break
                if len(response) == 2:
                    temperature = int.from_bytes(response, byteorder='little') / 100.0
                    logger.debug(f"LINMaster read temperature: {temperature:.2f} °C")
                    return temperature
        except Exception as e:
            logger.error(f"Error reading temperature: {e}")
        return None

############################################
# Create LIN Master Instance
############################################
lin_master = LINMaster()

############################################
# ADC Filtering
############################################
buffers_ma = {i: deque(maxlen=MOVING_AVERAGE_WINDOW) for i in range(6)}

def read_adc(channel):
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

############################################
# Async Tasks
############################################
async def process_adc_and_lin():
    while True:
        for i in range(6):
            if i < 4:
                latest_data["adc_channels"][f"channel_{i}"]["voltage"] = process_adc_data(i)
            else:
                latest_data["adc_channels"][f"channel_{i}"]["resistance"] = process_adc_data(i)

        # Control LED based on channel 0 voltage
        channel_0_voltage = latest_data["adc_channels"]["channel_0"]["voltage"]
        led_state = "ON" if channel_0_voltage > LED_VOLTAGE_THRESHOLD else "OFF"
        success = lin_master.control_led(0x01, led_state)
        if success:
            latest_data["slave_sensors"]["slave_1"]["led_state"] = led_state
            logger.info(f"LED turned {led_state} based on channel 0 voltage: {channel_0_voltage} V")
        else:
            logger.warning(f"Failed to send LED {led_state} command.")

        # Read temperature from slave
        temperature = lin_master.read_temperature(0x01)
        if temperature is not None:
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature
            logger.debug(f"Slave 1 Temperature: {temperature:.2f} °C")
        else:
            logger.warning("Failed to read temperature from slave.")

        await asyncio.sleep(1)

############################################
# Main Function
############################################
async def main():
    # Start Quart app with Hypercorn
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    quart_task = asyncio.create_task(serve(app, config))
    logger.info("Quart HTTP server started.")

    # Start ADC and LIN processing task
    adc_lin_task = asyncio.create_task(process_adc_and_lin())
    logger.info("ADC and LIN processing task started.")

    await asyncio.gather(
        quart_task,
        adc_lin_task
    )

############################################
# Run Quart App and Main
############################################
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC & LIN Add-on...")
    finally:
        spi.close()
        if lin_master.ser:
            lin_master.ser.close()
        logger.info("ADC & LIN Add-on has been shut down.")
