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

# Features:
# 1. Applies Moving Average (MA) and Exponential Moving Average (EMA) filtering to ADC data.
# 2. Implements a voltage threshold to eliminate minor noise.
# 3. ADC calculations run every 0.1 seconds for all channels simultaneously.
# 4. LIN communication every 2 seconds.
# 5. MQTT publishing every 1 second.
# 6. WebSocket broadcasting every 1 second.

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
import paho.mqtt.client as mqtt
import threading

# ============================
# Logging Configuration
# ============================

logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more verbose output
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),  # Log to file
        logging.StreamHandler()               # Log to console
    ]
)
logger = logging.getLogger('ADC, LIN & MQTT')

logger.info("ADC, LIN & MQTT Add-on started.")

# ============================
# Configuration Parameters
# ============================

# HTTP Server Configuration
HTTP_PORT = 8099

# SPI (ADC) Configuration
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED_HZ = 1_000_000  # 1 MHz
SPI_MODE = 0

# ADC Constants
VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10_000  # Ohms

# Task Intervals (in seconds)
ADC_INTERVAL = 0.1     # ADC readings every 0.1s
LIN_INTERVAL = 2       # LIN communication every 2s
MQTT_INTERVAL = 1      # MQTT publishing every 1s
WS_INTERVAL = 1        # WebSocket broadcasting every 1s

# Voltage Threshold to Eliminate Minor Noise
VOLTAGE_THRESHOLD = 0.02  # Volts

# Environment Variables for Supervisor
SUPERVISOR_WS_URL = os.getenv("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = os.getenv('INGRESS_PATH', '')

# Validate SUPERVISOR_TOKEN
if not SUPERVISOR_TOKEN:
    logger.error("SUPERVISOR_TOKEN is not set. Exiting.")
    exit(1)

# ============================
# LIN Communication Constants
# ============================

SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3  # 1.35 milliseconds

# PID Definitions
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51  # New PID for Humidity

# Mapping of PID to Sensor Names
PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

# ============================
# Data Structures
# ============================

# Latest sensor data to be served via HTTP and WebSockets
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

# ============================
# Quart Web Server Initialization
# ============================

app = Quart(__name__)

# Suppress Quart's default logging to reduce clutter
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

# Base directory for serving static files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Set to keep track of connected WebSocket clients
clients = set()

# ============================
# HTTP Routes
# ============================

@app.route('/data')
async def data_route():
    """
    Returns the latest sensor data in JSON format.
    """
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """
    Health check endpoint. Returns HTTP 200 if the service is running.
    """
    return '', 200

@app.route('/')
async def index():
    """
    Serves the index.html file from the base directory.
    """
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    """
    WebSocket endpoint for real-time data updates.
    """
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            # Keep the connection open by waiting for incoming messages
            await websocket.receive()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")

# ============================
# SPI (ADC) Initialization
# ============================

spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED_HZ
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

# ============================
# UART Initialization for LIN Communication
# ============================

UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

# ============================
# MQTT Configuration and Initialization
# ============================

MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

# Initialize MQTT client
mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

def on_connect(client, userdata, flags, rc):
    """
    Callback when the client connects to the MQTT broker.
    """
    if rc == 0:
        logger.info("Connected to MQTT Broker.")
        client.publish("cis3/status", "online", retain=True)
        publish_mqtt_discovery(client)
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc):
    """
    Callback when the client disconnects from the MQTT broker.
    """
    if rc != 0:
        logger.warning("Unexpected MQTT disconnection. Attempting to reconnect.")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")

# Register MQTT callbacks
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    """
    Runs the MQTT network loop in a separate thread.
    """
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT loop error: {e}")

# Start MQTT loop in a separate daemon thread
mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

# ============================
# Helper Functions
# ============================

def enhanced_checksum(data):
    """
    Calculates the checksum by summing all bytes, taking the lowest byte,
    and returning its inverse.
    
    Args:
        data (list): List of integer byte values.
    
    Returns:
        int: Calculated checksum byte.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    """
    Sends a BREAK signal for LIN communication.
    """
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)  # Brief pause after break

def send_header(pid):
    """
    Sends SYNC + PID header to the slave device and clears the UART buffer.
    
    Args:
        pid (int): Parameter Identifier byte.
    """
    ser.reset_input_buffer()  # Clear UART buffer before sending
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.debug(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)  # Short pause for slave processing

def read_response(expected_data_length, pid):
    """
    Reads the response from the slave, looking for SYNC + PID and then extracting the data.
    
    Args:
        expected_data_length (int): Number of bytes expected after SYNC + PID.
        pid (int): Parameter Identifier byte to look for.
    
    Returns:
        bytes or None: Extracted response bytes if successful, else None.
    """
    expected_length = expected_data_length
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # 2-second timeout
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")

            # Search for SYNC + PID in the buffer
            index = buffer.find(sync_pid)
            if index != -1:
                logger.debug(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")
                buffer = buffer[index + 2:]  # Remove everything before and including SYNC + PID

                # Check if enough bytes are available
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.debug(f"Extracted Response: {response.hex()}")
                    return response
                else:
                    # Wait for the remaining data
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received additional bytes: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.debug(f"Extracted Response after waiting: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)  # Brief pause before checking again

    logger.debug("No valid response received within timeout.")
    return None

def process_response(response, pid):
    """
    Processes the received response, verifies the checksum, and updates the latest_data structure.
    
    Args:
        response (bytes): The response bytes received from the slave.
        pid (int): Parameter Identifier byte associated with the response.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.debug(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0  # Convert to appropriate scale
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
                logger.debug(f"Updated Temperature: {value:.2f}°C")
            elif sensor == 'Humidity':
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                logger.debug(f"Updated Humidity: {value:.2f}%")
            else:
                logger.debug(f"Unknown PID {pid}: Value={value}")
        else:
            logger.debug("Checksum mismatch.")
    else:
        logger.debug("Invalid response length.")

# ============================
# ADC Filtering and Processing
# ============================

# Initialize buffers for Moving Average (MA) filtering
voltage_buffers = {ch: deque(maxlen=20) for ch in range(4)}       # Channels 0-3: Voltage
resistance_buffers = {ch: deque(maxlen=30) for ch in range(4,6)} # Channels 4-5: Resistance

# Initialize EMA values for each channel
ema_values = {ch: None for ch in range(6)}

def read_adc(channel):
    """
    Reads the raw ADC value from a specific channel using SPI.
    
    Args:
        channel (int): ADC channel number (0-7).
    
    Returns:
        int: Raw ADC value.
    """
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]  # Command format for MCP3008
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]  # Combine bytes to get raw value
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage_from_raw(raw_value):
    """
    Converts a raw ADC value to voltage.
    
    Args:
        raw_value (int): Raw ADC value.
    
    Returns:
        float: Calculated voltage in volts.
    """
    return (raw_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance_from_raw(raw_value):
    """
    Converts a raw ADC value to resistance.
    
    Args:
        raw_value (int): Raw ADC value.
    
    Returns:
        float: Calculated resistance in ohms.
    """
    if raw_value == 0:
        
        return 0.0
    return ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - raw_value)) / raw_value) / 10

def publish_to_mqtt():
    """
    Publishes the latest sensor data to MQTT state topics.
    """
    # Publish ADC Channel Data
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            # Voltage Channels
            state_topic = f"cis3/{channel}/voltage"
            payload = adc_data["voltage"]
            mqtt_client.publish(state_topic, str(payload))
            logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
        else:
            # Resistance Channels
            state_topic = f"cis3/{channel}/resistance"
            payload = adc_data["resistance"]
            mqtt_client.publish(state_topic, str(payload))
            logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

    # Publish Slave Sensor Data
    slave = latest_data["slave_sensors"]["slave_1"]
    for sensor, value in slave.items():
        state_topic = f"cis3/slave_1/{sensor.lower()}"
        mqtt_client.publish(state_topic, str(value))
        logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")

def publish_mqtt_discovery(client):
    """
    Publishes MQTT discovery messages for all sensors to enable automatic integration with Home Assistant.
    
    Args:
        client (mqtt.Client): The MQTT client instance.
    """
    # Publish MQTT Discovery for ADC Channels
    for i in range(6):
        channel = f"channel_{i}"
        if i < 4:
            # Voltage Sensors
            sensor = {
                "name": f"CIS3 Channel {i} Voltage",
                "unique_id": f"cis3_{channel}_voltage",
                "state_topic": f"cis3/{channel}/voltage",
                "unit_of_measurement": "V",
                "device_class": "voltage",
                "icon": "mdi:flash",
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
        else:
            # Resistance Sensors
            sensor = {
                "name": f"CIS3 Channel {i} Resistance",
                "unique_id": f"cis3_{channel}_resistance",
                "state_topic": f"cis3/{channel}/resistance",
                "unit_of_measurement": "Ω",
                "device_class": "resistance",  # Note: 'resistance' may not be a standard device class in some home automation platforms
                "icon": "mdi:water-percent",
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
        client.publish(discovery_topic, json.dumps(sensor), retain=True)
        logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

    # Publish MQTT Discovery for Slave Sensors
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
        client.publish(discovery_topic, json.dumps(sensor), retain=True)
        logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

# ============================
# Asynchronous Tasks
# ============================

async def process_all_adc_channels():
    """
    Reads and processes ADC data for all channels with filtering.
    Applies Moving Average (MA) and Exponential Moving Average (EMA).
    Implements a voltage threshold to eliminate minor noise.
    """
    # Read raw ADC values for all channels
    raw_values = [read_adc(ch) for ch in range(6)]

    # Process Voltage Channels (0-3)
    for ch in range(4):
        voltage = calculate_voltage_from_raw(raw_values[ch])
        voltage_buffers[ch].append(voltage)  # Add to MA buffer

        # Calculate Moving Average (MA)
        ma_voltage = sum(voltage_buffers[ch]) / len(voltage_buffers[ch])

        # Apply Exponential Moving Average (EMA)
        alpha = 0.2  # Smoothing factor for voltage
        if ema_values[ch] is None:
            ema_values[ch] = ma_voltage  # Initialize EMA with MA value
        else:
            ema_values[ch] = alpha * ma_voltage + (1 - alpha) * ema_values[ch]

        # Apply Voltage Threshold to Eliminate Minor Noise
        if ema_values[ch] < VOLTAGE_THRESHOLD:
            ema_values[ch] = 0.0

        # Update the latest_data structure with the filtered voltage
        latest_data["adc_channels"][f"channel_{ch}"]["voltage"] = round(ema_values[ch], 2)
        logger.debug(f"Channel {ch} Voltage: {latest_data['adc_channels'][f'channel_{ch}']['voltage']} V")

    # Process Resistance Channels (4-5)
    for ch in range(4, 6):
        resistance = calculate_resistance_from_raw(raw_values[ch])
        resistance_buffers[ch].append(resistance)  # Add to MA buffer

        # Calculate Moving Average (MA)
        ma_resistance = sum(resistance_buffers[ch]) / len(resistance_buffers[ch])

        # Apply Exponential Moving Average (EMA)
        alpha = 0.1  # Smoothing factor for resistance
        if ema_values[ch] is None:
            ema_values[ch] = ma_resistance  # Initialize EMA with MA value
        else:
            ema_values[ch] = alpha * ma_resistance + (1 - alpha) * ema_values[ch]

        # Update the latest_data structure with the filtered resistance
        latest_data["adc_channels"][f"channel_{ch}"]["resistance"] = round(ema_values[ch], 2)
        logger.debug(f"Channel {ch} Resistance: {latest_data['adc_channels'][f'channel_{ch}']['resistance']} Ω")

async def process_lin_communication():
    """
    Handles LIN communication by sending headers and processing responses.
    """
    for pid in PID_DICT.keys():
        send_header(pid)  # Send SYNC + PID header
        response = read_response(3, pid)  # Read response expecting 3 bytes
        if response:
            process_response(response, pid)  # Process the received response
        await asyncio.sleep(0.1)  # Short pause between requests

async def broadcast_via_websocket():
    """
    Broadcasts the latest sensor data to all connected WebSocket clients.
    """
    if clients:
        data_to_send = json.dumps(latest_data)
        await asyncio.gather(*(client.send(data_to_send) for client in clients))
        logger.debug("Sent updated data to WebSocket clients.")

async def mqtt_publish_task():
    """
    Publishes the latest sensor data to MQTT topics.
    """
    publish_to_mqtt()

async def adc_loop():
    """
    Periodically processes ADC data based on ADC_INTERVAL.
    """
    while True:
        await process_all_adc_channels()
        await asyncio.sleep(ADC_INTERVAL)

async def lin_loop():
    """
    Periodically handles LIN communication based on LIN_INTERVAL.
    """
    while True:
        await process_lin_communication()
        await asyncio.sleep(LIN_INTERVAL)

async def mqtt_loop_task():
    """
    Periodically publishes data to MQTT based on MQTT_INTERVAL.
    """
    while True:
        await mqtt_publish_task()
        await asyncio.sleep(MQTT_INTERVAL)

async def websocket_loop():
    """
    Periodically broadcasts data via WebSocket based on WS_INTERVAL.
    """
    while True:
        await broadcast_via_websocket()
        await asyncio.sleep(WS_INTERVAL)

# ============================
# Main Function to Start All Tasks
# ============================

async def main():
    """
    Initializes and starts the Quart HTTP server and all asynchronous tasks.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]  # Bind to all interfaces on specified port
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    
    # Create asynchronous tasks
    quart_task = asyncio.create_task(serve(app, config))
    adc_task = asyncio.create_task(adc_loop())
    lin_task = asyncio.create_task(lin_loop())
    mqtt_task = asyncio.create_task(mqtt_loop_task())
    ws_task = asyncio.create_task(websocket_loop())

    # Log the start of each task
    logger.info("Quart HTTP server started.")
    logger.info("ADC processing task started.")
    logger.info("LIN communication task started.")
    logger.info("MQTT publishing task started.")
    logger.info("WebSocket broadcasting task started.")

    # Run all tasks concurrently
    await asyncio.gather(quart_task, adc_task, lin_task, mqtt_task, ws_task)

# ============================
# Entry Point
# ============================

if __name__ == '__main__':
    try:
        # Run the main asynchronous function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        # Clean up resources on shutdown
        try:
            spi.close()
            ser.close()
            mqtt_client.publish("cis3/status", "offline", retain=True)
            mqtt_client.disconnect()
            logger.info("ADC, LIN & MQTT Add-on has been shut down.")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
