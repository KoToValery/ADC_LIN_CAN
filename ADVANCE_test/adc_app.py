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
# Version: V01.01.11.2024.CIS3 - optimized async handling and logging
# 1. TestSPI,ADC - work. Measurement Voltage 0-10 V, resistive 0-1000 ohm
# 2. Test Power PI5V/4.5A - work
# 3. Test ADC communication - work
# 4. Test LIN communication - work
# 5. - updated with MQTT integration, integrated Moving Average into EMA, optimized logging

import os
import time
import json
import asyncio
import spidev
from collections import deque
from quart import Quart, jsonify, send_from_directory, websocket
import logging
import serial
from hypercorn.asyncio import serve
from hypercorn.config import Config
import aiomqtt
import aiofiles
import signal

# -------------------- Logging Configuration --------------------
logging.basicConfig(
    level=logging.INFO,  # Set to INFO to capture important logs
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ADC, LIN & MQTT')

logger.info("ADC, LIN & MQTT Advanced Add-on started.")

# -------------------- Configuration Settings --------------------
# Ingress Configuration
INGRESS_PATH = os.getenv('INGRESS_PATH', '')  # Добавено: Ingress път

# SPI (Serial Peripheral Interface) Configuration for ADC
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED = 1000000  # SPI speed in Hz
SPI_MODE = 0  # SPI mode

# ADC and Measurement Configuration
VREF = 3.3  # Reference voltage for ADC
ADC_RESOLUTION = 1023.0  # ADC resolution (10-bit)
VOLTAGE_MULTIPLIER = 3.31  # Multiplier for voltage measurement
RESISTANCE_REFERENCE = 10000  # Reference resistor value for resistance measurement

# EMA Configuration
VOLTAGE_EMA_ALPHA = 0.2
RESISTANCE_EMA_ALPHA = 0.1

# Moving Average Configuration
VOLTAGE_MA = 30
RESISTANCE_MA = 30

# MQTT Configuration
MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

# HTTP Server Configuration
HTTP_PORT = 8080  # Define HTTP server port

# Update Intervals (in seconds)
ADC_UPDATE_INTERVAL = 0.01  # Update ADC every 0.01 seconds
LIN_UPDATE_INTERVAL = 1.0  # Update LIN every 1 second (modifiable)
DATA_SEND_INTERVAL = 1.0  # Send data to MQTT and WebSocket every 1 second

# -------------------- Data Initialization --------------------
latest_data = {
    "adc_channels": {
        "channel_0": {"voltage": 0.00, "unit": "V"},
        "channel_1": {"voltage": 0.00, "unit": "V"},
        "channel_2": {"voltage": 0.00, "unit": "V"},
        "channel_3": {"voltage": 0.00, "unit": "V"},
        "channel_4": {"resistance": 0, "unit": "Ω"},
        "channel_5": {"resistance": 0, "unit": "Ω"}
    },
    "slave_sensors": {
        "slave_1": {
            "Temperature": 0.0,
            "Humidity": 0.0
        }
    }
}

ma_deques_voltage = {i: deque(maxlen=VOLTAGE_MA) for i in range(4)}
ma_deques_resistance = {i: deque(maxlen=RESISTANCE_MA) for i in range(4, 6)}

# -------------------- Quart Application Setup --------------------
app = Quart(__name__)

quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
clients = set()

# -------------------- Quart Route Definitions with Ingress --------------------
@app.route(f'{INGRESS_PATH}/data')
async def data_route():
    """
    Endpoint to retrieve the latest sensor data in JSON format.
    """
    return jsonify(latest_data)

@app.route(f'{INGRESS_PATH}/health')
async def health():
    """
    Health check endpoint. Returns 200 OK if the service is running.
    """
    return '', 200

@app.route(f'{INGRESS_PATH}/')
async def index():
    """
    Serves the index.html file from the base directory.
    """
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket(f'{INGRESS_PATH}/ws')
async def ws_route():
    """
    WebSocket endpoint for real-time communication with clients via Ingress.
    """
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            # Keep the websocket open by awaiting incoming messages
            # The server sends updates periodically
            await websocket.receive()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")

# -------------------- SPI Initialization --------------------
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")
    exit(1)

# -------------------- UART Configuration --------------------
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

# -------------------- Filter State --------------------
values_ema_voltage = {i: None for i in range(4)}
values_ema_resistance = {i: None for i in range(4, 6)}

# -------------------- Helper Functions --------------------
SYNC_BYTE = 0x55  # Synchronization byte for LIN communication
BREAK_DURATION = 1.35e-3  # Duration of break signal in seconds (1.35ms)

PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51

PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

async def send_break():
    ser.break_condition = True
    await asyncio.sleep(BREAK_DURATION)
    ser.break_condition = False
    await asyncio.sleep(0.0001)

async def send_header(pid):
    ser.reset_input_buffer()
    await send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.info(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    await asyncio.sleep(0.1)

def apply_ma_then_ema(value, channel, is_voltage):
    if is_voltage:
        ma_deque = ma_deques_voltage[channel]
        ema_values = values_ema_voltage
        alpha = VOLTAGE_EMA_ALPHA
    else:
        ma_deque = ma_deques_resistance[channel]
        ema_values = values_ema_resistance
        alpha = RESISTANCE_EMA_ALPHA

    ma_deque.append(value)
    ma_avg = sum(ma_deque) / len(ma_deque) if ma_deque else 0.0

    if ema_values[channel] is None:
        ema_values[channel] = ma_avg
    else:
        ema_values[channel] = alpha * ma_avg + (1 - alpha) * ema_values[channel]

    return round(ema_values[channel], 2) if is_voltage else int(round(ema_values[channel]))

def read_adc(channel):
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage(adc_value):
    if adc_value < 10:
        return 0.0
    return (adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance(adc_value):
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return resistance

async def read_response_async(expected_data_length, pid):
    expected_length = expected_data_length
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")

            index = buffer.find(sync_pid)
            if index != -1:
                logger.debug(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")
                buffer = buffer[index + 2:]
                logger.debug(f"Filtered Buffer after SYNC + PID: {buffer.hex()}")

                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.debug(f"Filtered Response: {response.hex()}")
                    return response
                else:
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received bytes while waiting: {more_data.hex()}")
                        else:
                            await asyncio.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.debug(f"Filtered Response after waiting: {response.hex()}")
                        return response
        else:
            await asyncio.sleep(0.01)

    logger.debug("No valid response received within timeout.")
    return None

def enhanced_checksum(data):
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def process_response(response, pid):
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.debug(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                value = round(value, 2)
                logger.info(f"Temperature: {value:.2f}°C")
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
            elif sensor == 'Humidity':
                value = round(value, 2)
                logger.info(f"Humidity: {value:.2f}%")
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
            else:
                logger.debug(f"Unknown PID {pid}: Value={value}")
        else:
            logger.warning("Checksum mismatch.")
    else:
        logger.warning("Invalid response length.")

async def publish_to_mqtt(client):
    try:
        for i in range(6):
            channel = f"channel_{i}"
            adc_data = latest_data["adc_channels"][channel]
            if i < 4:
                state_topic = f"cis3/{channel}/voltage"
                payload = adc_data["voltage"]
                await client.publish(state_topic, str(payload), qos=1)
                logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
            else:
                state_topic = f"cis3/{channel}/resistance"
                payload = adc_data["resistance"]
                await client.publish(state_topic, str(payload), qos=1)
                logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

        slave = latest_data["slave_sensors"]["slave_1"]
        for sensor, value in slave.items():
            state_topic = f"cis3/slave_1/{sensor.lower()}"
            await client.publish(state_topic, str(value), qos=1)
            logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")
    except Exception as e:
        logger.error(f"Error publishing to MQTT: {e}")

async def publish_mqtt_discovery(client):
    try:
        for i in range(6):
            channel = f"channel_{i}"
            if i < 4:
                sensor_type = "voltage"
                unit = "V"
                state_topic = f"cis3/{channel}/voltage"
                unique_id = f"cis3_{channel}_voltage"
                name = f"CIS3 Channel {i} Voltage"
                device_class = "voltage"
                icon = "mdi:flash"
                value_template = "{{ value }}"
            else:
                sensor_type = "resistance"
                unit = "Ω"
                state_topic = f"cis3/{channel}/resistance"
                unique_id = f"cis3_{channel}_resistance"
                name = f"CIS3 Channel {i} Resistance"
                device_class = "resistance"
                icon = "mdi:water-percent"
                value_template = "{{ value }}"

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
            discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{sensor['unique_id']}/config"
            await client.publish(discovery_topic, json.dumps(sensor), qos=1, retain=True)
            logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

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
            discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{sensor['unique_id']}/config"
            await client.publish(discovery_topic, json.dumps(sensor), qos=1, retain=True)
            logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")
    except Exception as e:
        logger.error(f"Error publishing MQTT discovery: {e}")

async def adc_reading_task():
    while True:
        start_time = time.time()
        for channel in range(6):
            raw_value = read_adc(channel)
            if channel < 4:
                voltage = calculate_voltage(raw_value)
                filtered_voltage = apply_ma_then_ema(voltage, channel, True)
                latest_data["adc_channels"][f"channel_{channel}"]["voltage"] = filtered_voltage
                logger.debug(f"Channel {channel} Voltage: {filtered_voltage} V (MA + EMA)")
            else:
                resistance = calculate_resistance(raw_value)
                filtered_resistance = apply_ma_then_ema(resistance, channel, False)
                latest_data["adc_channels"][f"channel_{channel}"]["resistance"] = filtered_resistance
                logger.debug(f"Channel {channel} Resistance: {filtered_resistance} Ω (MA + EMA)")
        end_time = time.time()
        processing_time = end_time - start_time
        logger.debug(f"ADC data processing took {processing_time:.4f} seconds")
        await asyncio.sleep(ADC_UPDATE_INTERVAL)

async def lin_communication_task():
    while True:
        start_time = time.time()
        for pid in PID_DICT.keys():
            await send_header(pid)
            response = await read_response_async(3, pid)
            if response:
                process_response(response, pid)
            await asyncio.sleep(0.1)
        end_time = time.time()
        elapsed = end_time - start_time
        sleep_time = LIN_UPDATE_INTERVAL - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            logger.warning("LIN communication tasks are taking longer than the defined interval.")

async def data_sending_task(client):
    while True:
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(c.send(data_to_send) for c in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        await publish_to_mqtt(client)
        await asyncio.sleep(DATA_SEND_INTERVAL)

async def process_adc_and_lin():
    try:
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD
        ) as client:
            await client.publish("cis3/status", "online", qos=1, retain=True)
            await publish_mqtt_discovery(client)

            adc_task = asyncio.create_task(adc_reading_task())
            lin_task = asyncio.create_task(lin_communication_task())
            data_send_task = asyncio.create_task(data_sending_task(client))

            await asyncio.gather(adc_task, lin_task, data_send_task)
    except Exception as e:
        logger.error(f"Error in ADC and LIN processing loop: {e}")
    finally:
        try:
            async with aiomqtt.Client(
                hostname=MQTT_BROKER,
                port=MQTT_PORT,
                username=MQTT_USERNAME,
                password=MQTT_PASSWORD
            ) as client_offline:
                await client_offline.publish("cis3/status", "offline", qos=1, retain=True)
                logger.info("Published offline status to MQTT.")
        except Exception as e:
            logger.error(f"Error publishing offline status: {e}")

async def shutdown(signal_received):
    logger.info(f"Received exit signal {signal_received.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    spi.close()
    ser.close()
    logger.info("ADC, LIN & MQTT Advanced Add-on has been shut down.")
    asyncio.get_event_loop().stop()

async def main():
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")

    quart_task = asyncio.create_task(serve(app, config))
    logger.info("Quart HTTP server started.")
    adc_lin_task = asyncio.create_task(process_adc_and_lin())
    logger.info("ADC and LIN processing task started.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    await asyncio.gather(quart_task, adc_lin_task)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Advanced Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        spi.close()
        ser.close()
