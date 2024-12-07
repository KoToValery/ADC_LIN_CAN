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

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ADC & LIN')

logger.info("ADC & LIN Advanced Add-on started.")

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

app = Quart(__name__)

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

spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=9600, uart_timeout=1):
        self.SYNC_BYTE = 0x55
        self.PID_BYTE = 0x50
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

    def calculate_pid(self, identifier):
        id_bits = identifier & 0x3F
        p0 = ((id_bits >> 0) & 1) ^ ((id_bits >> 1) & 1) ^ ((id_bits >> 2) & 1) ^ ((id_bits >> 4) & 1)
        p1 = (~(((id_bits >> 1) & 1) ^ ((id_bits >> 3) & 1) ^ ((id_bits >> 4) & 1) ^ ((id_bits >> 5) & 1))) & 1
        pid = id_bits | (p0 << 6) | (p1 << 7)
        logger.debug(f"Calculated PID: 0x{pid:02X} for identifier: 0x{identifier:02X}")
        return pid

    def calculate_checksum(self, data):
        checksum = sum(data) & 0xFF
        checksum = (~checksum) & 0xFF
        logger.debug(f"Calculated Checksum: 0x{checksum:02X} for data: {data}")
        return checksum

    def send_break(self):
        if self.ser:
            try:
                self.ser.break_condition = True
                logger.debug(f"Sending BREAK condition for {self.BREAK_DURATION} seconds.")
                time.sleep(self.BREAK_DURATION)
                self.ser.break_condition = False
                time.sleep(0.0001)
                logger.debug("BREAK condition sent.")
            except Exception as e:
                logger.error(f"Error sending BREAK: {e}")
        else:
            logger.error("UART not initialized. Cannot send BREAK.")

    def send_header(self, identifier):
        if self.ser:
            try:
                self.send_break()
                pid = self.calculate_pid(identifier)
                self.ser.write(bytes([self.SYNC_BYTE]))
                logger.debug(f"Sent SYNC byte: 0x{self.SYNC_BYTE:02X}")
                self.ser.write(bytes([pid]))
                logger.debug(f"Sent PID byte: 0x{pid:02X}")
                return pid
            except Exception as e:
                logger.error(f"Error sending header: {e}")
                return None
        else:
            logger.error("UART not initialized. Cannot send header.")
            return None

    def read_response(self, expected_length):
        if self.ser:
            try:
                start_time = time.time()
                response = bytearray()

                while (time.time() - start_time) < self.RESPONSE_TIMEOUT:
                    if self.ser.in_waiting:
                        byte = self.ser.read(1)
                        response.extend(byte)
                        if len(response) >= expected_length:
                            break

                filtered_response = bytearray(b for b in response if b != 0)
                logger.debug(f"Received response (hex): {filtered_response.hex()}")

                if len(filtered_response) == expected_length:
                    return filtered_response
                else:
                    logger.warning(f"Incomplete or invalid response. Expected: {expected_length} bytes, Received: {len(filtered_response)} bytes.")
                    return None
            except Exception as e:
                logger.error(f"Error reading response: {e}")
                return None
        else:
            logger.error("UART not initialized. Cannot read response.")
            return None

    def send_request_frame(self, identifier):
        logger.info(f"Sending temperature request to identifier: 0x{identifier:02X}")
        pid = self.send_header(identifier)
        if pid is None:
            return None

        expected_length = 3
        response = self.read_response(expected_length)

        if response:
            data = response[:2]
            received_checksum = response[2]
            calculated_checksum = self.calculate_checksum([pid] + list(data))

            logger.debug(f"Received data (hex): {data.hex()}, Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

            if received_checksum == calculated_checksum:
                temperature = struct.unpack('<H', data)[0] / 100.0
                logger.info(f"Valid response. Temperature: {temperature:.2f} °C")
                return temperature
            else:
                logger.warning("Checksum mismatch!")
                return None
        else:
            logger.warning("No valid response from slave.")
            return None

    def send_data_frame(self, identifier, data_bytes):
        logger.info(f"Sending data frame to identifier: 0x{identifier:02X} with data: {data_bytes.hex()}")
        pid = self.send_header(identifier)
        if pid is None:
            return False

        csum = self.calculate_checksum([pid] + list(data_bytes))
        frame = data_bytes + bytes([csum])
        try:
            self.ser.write(frame)
            logger.debug(f"Sent Data Frame (hex): {frame.hex()}")
            return True
        except Exception as e:
            logger.error(f"Error sending Data Frame: {e}")
            return False

    def control_led(self, identifier, state):
        command = 0x01 if state == "ON" else 0x00
        logger.info(f"Sending LED command: {state}")
        success = self.send_data_frame(identifier, bytes([command]))
        if success:
            logger.info(f"LED {state} command successfully sent.")
        else:
            logger.warning(f"Failed to send LED {state} command.")
        return success

    def read_slave_temperature(self, identifier):
        logger.info(f"Requesting temperature from identifier: 0x{identifier:02X}")
        temperature = self.send_request_frame(identifier)
        if temperature is not None:
            logger.info(f"Read temperature: {temperature:.2f} °C")
        else:
            logger.warning("Failed to read temperature.")
        return temperature

lin_master = LINMaster()

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
        async with websockets.connect(uri, extra_headers=headers) as websocket_conn:
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
        success = lin_master.control_led(0x01, led_state)
        if success:
            latest_data["slave_sensors"]["slave_1"]["led_state"] = led_state
            logger.info(f"LED turned {led_state} based on channel 0 voltage: {channel_0_voltage} V")
        else:
            logger.warning(f"Failed to send LED {led_state} command.")

        temperature = lin_master.read_slave_temperature(0x01)
        if temperature is not None:
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature
            logger.debug(f"Slave 1 Temperature: {temperature:.2f} °C")
        else:
            logger.warning("Failed to read temperature from slave.")

        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        await asyncio.sleep(1)

async def main():
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
    finally:
        spi.close()
        if lin_master.ser:
            lin_master.ser.close()
        logger.info("ADC & LIN Advanced Add-on has been shut down.")
