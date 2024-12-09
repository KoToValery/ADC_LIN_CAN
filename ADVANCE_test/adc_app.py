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
import struct
import json
import websockets
from hypercorn.asyncio import serve
from hypercorn.config import Config
from datetime import datetime
import serial_asyncio

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
LED_VOLTAGE_THRESHOLD = 3.0

SUPERVISOR_WS_URL = os.getenv("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = os.getenv('INGRESS_PATH', '')

if not SUPERVISOR_TOKEN:
    logger.error("SUPERVISOR_TOKEN is not set. Exiting.")
    exit(1)

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
            "value": 0.0,
            "led_state": "OFF"
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

# Дефиниране на PID за различните команди
PID_TEMPERATURE = 0xC1
PID_LED_CONTROL = 0xC2

class LINMasterProtocol(asyncio.Protocol):
    def __init__(self, master):
        self.master = master
        self.buffer = bytearray()
        self.transport = None
        self.state = 'WAIT_SYNC'
        self.expected_bytes = 0
        self.current_frame = {}

    def connection_made(self, transport):
        self.transport = transport
        logger.info("LINMaster: UART connection established.")

    def data_received(self, data):
        for byte in data:
            self.process_byte(byte)

    def connection_lost(self, exc):
        logger.warning("LINMaster: UART connection lost.")
        # Опитайте да се свържете отново
        asyncio.create_task(self.master.handle_connection_lost())

    def process_byte(self, byte):
        if self.state == 'WAIT_SYNC':
            if byte == 0x55:
                self.state = 'WAIT_PID'
                logger.debug("LINMaster: SYNC byte detected.")
        elif self.state == 'WAIT_PID':
            self.current_frame['pid'] = byte
            # Определяне на очакваната дължина на данните въз основа на PID
            if byte == PID_TEMPERATURE:
                self.expected_bytes = 3  # 2 data bytes + 1 checksum
                self.current_frame['data'] = bytearray()
                self.state = 'READ_DATA'
                logger.debug(f"LINMaster: PID {byte:#04x} detected. Expecting {self.expected_bytes} bytes.")
            elif byte == PID_LED_CONTROL:
                self.expected_bytes = 2  # 1 data byte + 1 checksum
                self.current_frame['data'] = bytearray()
                self.state = 'READ_DATA'
                logger.debug(f"LINMaster: PID {byte:#04x} detected. Expecting {self.expected_bytes} bytes.")
            else:
                logger.warning(f"LINMaster: Unknown PID {byte:#04x}. Resetting state.")
                self.reset_state()
        elif self.state == 'READ_DATA':
            self.current_frame['data'].append(byte)
            if len(self.current_frame['data']) == (self.expected_bytes - 1):
                self.state = 'READ_CHECKSUM'
                logger.debug("LINMaster: Data bytes received. Waiting for checksum.")
        elif self.state == 'READ_CHECKSUM':
            self.current_frame['checksum'] = byte
            self.process_frame()
            self.reset_state()

    def process_frame(self):
        pid = self.current_frame.get('pid')
        data = self.current_frame.get('data')
        checksum = self.current_frame.get('checksum')

        # Изчисляване на Checksum
        calculated_checksum = self.master.calculate_checksum([pid] + list(data))
        if checksum == calculated_checksum:
            if pid == PID_TEMPERATURE:
                # Интерпретиране на данните като температура
                temperature = struct.unpack('<H', data)[0] / 100.0
                logger.info(f"LINMaster: Valid temperature response. Temperature: {temperature:.2f} °C")
                self.master.latest_data["slave_sensors"]["slave_1"]["value"] = temperature
            elif pid == PID_LED_CONTROL:
                # Интерпретиране на данните като статус на LED (ако е необходимо)
                logger.info(f"LINMaster: LED Control Response received.")
                # Имплементирайте обработка на отговора, ако е необходимо
        else:
            logger.warning(f"LINMaster: Checksum mismatch! Received: 0x{checksum:02X}, Calculated: 0x{calculated_checksum:02X}")

    def reset_state(self):
        self.state = 'WAIT_SYNC'
        self.current_frame = {}
        self.expected_bytes = 0

class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=9600, uart_timeout=1, latest_data=None):
        self.SYNC_BYTE = 0x55
        self.PID_TEMPERATURE = PID_TEMPERATURE
        self.PID_LED_CONTROL = PID_LED_CONTROL
        self.BREAK_DURATION = 1.35e-3
        self.RESPONSE_TIMEOUT = 0.1

        self.uart_port = uart_port
        self.uart_baudrate = uart_baudrate
        self.latest_data = latest_data

        self.protocol = None
        self.transport = None

    async def connect(self):
        try:
            loop = asyncio.get_running_loop()
            self.transport, self.protocol = await serial_asyncio.create_serial_connection(
                loop, lambda: LINMasterProtocol(self), self.uart_port, baudrate=self.uart_baudrate
            )
            logger.info(f"LINMaster: Connected to UART port {self.uart_port} at {self.uart_baudrate} baud.")
        except Exception as e:
            logger.error(f"LINMaster: UART connection error: {e}")
            await asyncio.sleep(5)
            await self.connect()

    async def handle_connection_lost(self):
        logger.info("LINMaster: Attempting to reconnect UART...")
        await self.connect()

    def calculate_pid(self, identifier):
        id_bits = identifier & 0x3F
        p0 = ((id_bits >> 0) & 1) ^ ((id_bits >> 1) & 1) ^ ((id_bits >> 2) & 1) ^ ((id_bits >> 4) & 1)
        p1 = (~(((id_bits >> 1) & 1) ^ ((id_bits >> 3) & 1) ^ ((id_bits >> 4) & 1) ^ ((id_bits >> 5) & 1))) & 1
        pid = id_bits | (p0 << 6) | (p1 << 7)
        logger.debug(f"LINMaster: Calculated PID: 0x{pid:02X} for identifier: 0x{identifier:02X}")
        return pid

    def calculate_checksum(self, data):
        checksum = sum(data) & 0xFF
        checksum = (~checksum) & 0xFF
        logger.debug(f"LINMaster: Calculated Checksum: 0x{checksum:02X} for data: {data}")
        return checksum

    async def send_break(self):
        if self.transport:
            try:
                # Симулиране на BREAK чрез задаване на break_condition
                self.transport.serial.break_condition = True
                logger.debug(f"LINMaster: Sending BREAK condition for {self.BREAK_DURATION} seconds.")
                await asyncio.sleep(self.BREAK_DURATION)
                self.transport.serial.break_condition = False
                await asyncio.sleep(0.0001)
                logger.debug("LINMaster: BREAK condition sent.")
            except Exception as e:
                logger.error(f"LINMaster: Error sending BREAK: {e}")
        else:
            logger.error("LINMaster: UART not connected. Cannot send BREAK.")

    async def send_header(self, pid):
        if self.transport:
            try:
                await self.send_break()
                header = bytes([self.SYNC_BYTE, pid])
                self.transport.write(header)
                logger.debug(f"LINMaster: Sent SYNC byte: 0x{self.SYNC_BYTE:02X}")
                logger.debug(f"LINMaster: Sent PID byte: 0x{pid:02X}")
                return pid
            except Exception as e:
                logger.error(f"LINMaster: Error sending header: {e}")
                return None
        else:
            logger.error("LINMaster: UART not connected. Cannot send header.")
            return None

    async def send_request_frame(self, identifier):
        pid = self.calculate_pid(identifier)
        logger.info(f"LINMaster: Sending temperature request to identifier: 0x{identifier:02X}")
        await self.send_header(pid)
        # Отговорът ще бъде обработен асинхронно от протокола
        await asyncio.sleep(self.RESPONSE_TIMEOUT)
        temperature = self.latest_data["slave_sensors"]["slave_1"]["value"]
        if temperature > 0:
            logger.info(f"LINMaster: Read temperature: {temperature:.2f} °C")
            return temperature
        else:
            logger.warning("LINMaster: Failed to read temperature.")
            return None

    async def send_data_frame(self, identifier, data_bytes):
        pid = self.calculate_pid(identifier)
        logger.info(f"LINMaster: Sending LED command to identifier: 0x{identifier:02X} with data: {data_bytes.hex()}")
        await self.send_header(pid)
        csum = self.calculate_checksum([pid] + list(data_bytes))
        frame = data_bytes + bytes([csum])
        try:
            self.transport.write(frame)
            logger.debug(f"LINMaster: Sent Data Frame (hex): {frame.hex()}")
            return True
        except Exception as e:
            logger.error(f"LINMaster: Error sending Data Frame: {e}")
            return False

    async def control_led(self, identifier, state):
        command = 0x01 if state == "ON" else 0x00
        logger.info(f"LINMaster: Sending LED command: {state}")
        success = await self.send_data_frame(identifier, bytes([command]))
        if success:
            self.latest_data["slave_sensors"]["slave_1"]["led_state"] = state
            logger.info(f"LINMaster: LED {state} command successfully sent.")
        else:
            logger.warning(f"LINMaster: Failed to send LED {state} command.")
        return success

    async def read_slave_temperature(self, identifier):
        temperature = await self.send_request_frame(identifier)
        return temperature

# Инстанциране на LINMaster с latest_data
lin_master = LINMaster(latest_data=latest_data)

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

async def supervisor_ws_client():
    uri = SUPERVISOR_WS_URL
    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        async with websockets.connect(uri, headers=headers) as websocket_conn:
            logger.info("Connected to Supervisor WebSocket API.")
            subscribe_message = json.dumps({
                "type": "subscribe_events",
                "event_type": "state_changed"
            })
            await websocket_conn.send(subscribe_message)
            logger.debug("Subscribed to state_changed events.")

            async for message in websocket_conn:
                data = json.loads(message)
                logger.debug(f"Supervisor WebSocket message: {data}")
    except Exception as e:
        logger.error(f"Supervisor WebSocket connection error: {e}")

async def process_adc_and_lin():
    while True:
        for i in range(6):
            if i < 4:
                voltage = process_adc_data(i)
                latest_data["adc_channels"][f"channel_{i}"]["voltage"] = voltage
                logger.debug(f"ADC Channel {i} Voltage: {voltage} V")
            else:
                resistance = process_adc_data(i)
                latest_data["adc_channels"][f"channel_{i}"]["resistance"] = resistance
                logger.debug(f"ADC Channel {i} Resistance: {resistance} Ω")

        channel_0_voltage = latest_data["adc_channels"]["channel_0"]["voltage"]
        led_state = "ON" if channel_0_voltage > LED_VOLTAGE_THRESHOLD else "OFF"
        success = await lin_master.control_led(0x01, led_state)
        if success:
            latest_data["slave_sensors"]["slave_1"]["led_state"] = led_state
            logger.info(f"LED turned {led_state} based on channel 0 voltage: {channel_0_voltage} V")
        else:
            logger.warning(f"Failed to send LED {led_state} command.")

        temperature = await lin_master.read_slave_temperature(0x01)
        if temperature is not None:
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature
            logger.debug(f"Slave 1 Temperature: {temperature:.2f} °C")
        else:
            logger.warning("Failed to read temperature from slave.")

        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        await asyncio.sleep(5)  # Увеличен интервал до 5 секунди

async def main():
    # Стартиране на LINMaster връзката
    await lin_master.connect()

    supervisor_task = asyncio.create_task(supervisor_ws_client())
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    quart_task = asyncio.create_task(serve(app, config))
    logger.info("Quart HTTP server started.")
    adc_lin_task = asyncio.create_task(process_adc_and_lin())
    logger.info("ADC and LIN processing task started.")
    await asyncio.gather(
        supervisor_task,
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
        if lin_master.transport and lin_master.transport.serial.is_open:
            lin_master.transport.close()
        logger.info("ADC & LIN Advanced Add-on has been shut down.")
