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
#- Updated with detailed comments, optimized logging,
#          Low-Pass Filtering, separate EMA for voltage and resistance,
#          and configurable parameters via config.yaml

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
import paho.mqtt.client as mqtt
import threading

# --------------------------- Logging Configuration --------------------------- #

# Configure logging to capture only errors and important system messages.
logging.basicConfig(
    level=logging.INFO,  # Set to INFO to capture important messages and errors
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),  # Log to file
        logging.StreamHandler()              # Log to console
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
MOVING_AVERAGE_WINDOW_VOLTAGE = int(os.getenv("MOVING_AVERAGE_WINDOW_VOLTAGE", "10"))
MOVING_AVERAGE_WINDOW_RESISTANCE = int(os.getenv("MOVING_AVERAGE_WINDOW_RESISTANCE", "30"))

# EMA Configuration (Configurable via environment variables)
EMA_ALPHA_VOLTAGE = float(os.getenv("EMA_ALPHA_VOLTAGE", "0.4"))
EMA_ALPHA_RESISTANCE = float(os.getenv("EMA_ALPHA_RESISTANCE", "0.1"))

# Low-Pass Filter Configuration (Configurable via environment variables)
LOW_PASS_ALPHA = float(os.getenv("LOW_PASS_ALPHA", "0.5"))

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

# Initialize UART for LIN communication
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

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
    Route to provide the latest sensor data in JSON format.
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
    WebSocket route for real-time data updates.
    """
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            await websocket.receive()  # Keep the connection open
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")

# --------------------------- Define Filtering Functions --------------------------- #

# Initialize deques for Moving Average
buffers_ma_voltage = {f"channel_{i}": deque(maxlen=MOVING_AVERAGE_WINDOW_VOLTAGE) for i in range(4)}
buffers_ma_resistance = {f"channel_{i}": deque(maxlen=MOVING_AVERAGE_WINDOW_RESISTANCE) for i in range(4,6)}

# Initialize EMA values
ema_voltage = {f"channel_{i}": None for i in range(4)}
ema_resistance = {f"channel_{i}": None for i in range(4,6)}

# Initialize Low-Pass Filter values
low_pass_voltage = {f"channel_{i}": None for i in range(4)}
low_pass_resistance = {f"channel_{i}": None for i in range(4,6)}

def moving_average(value, buffer):
    """
    Calculate the moving average for a given buffer.
    """
    buffer.append(value)
    return sum(buffer) / len(buffer)

def exponential_moving_average(value, previous_ema, alpha):
    """
    Calculate the Exponential Moving Average (EMA).
    """
    if previous_ema is None:
        return value
    return alpha * value + (1 - alpha) * previous_ema

def low_pass_filter(value, previous_filtered, alpha):
    """
    Apply a Low-Pass Filter to the value.
    """
    if previous_filtered is None:
        return value
    return alpha * value + (1 - alpha) * previous_filtered

# --------------------------- Define Helper Functions --------------------------- #

def enhanced_checksum(data):
    """
    Calculates the checksum by summing all bytes, taking the lowest byte,
    and returning its inverse.
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
    time.sleep(0.0001)  # Short pause after break

def send_header(pid):
    """
    Sends SYNC + PID header to the LIN slave and clears the UART buffer.
    """
    ser.reset_input_buffer()  # Clear UART buffer before sending
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.info(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)  # Short pause for slave processing

def read_response(expected_data_length, pid):
    """
    Reads the response from the LIN slave, looking for SYNC + PID,
    and then extracting the data.
    """
    expected_length = expected_data_length  # 3 bytes: 2 data + 1 checksum
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # 2-second timeout
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")  # Debug log for received bytes

            # Search for SYNC + PID in the buffer
            index = buffer.find(sync_pid)
            if index != -1:
                logger.info(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")
                # Remove everything before and including SYNC + PID
                buffer = buffer[index + 2:]
                logger.debug(f"Filtered Buffer after SYNC + PID: {buffer.hex()}")

                # Check if enough bytes are available for data and checksum
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.info(f"Filtered Response: {response.hex()}")
                    return response
                else:
                    # Wait for remaining data
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received bytes while waiting: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.info(f"Filtered Response after waiting: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)

    logger.error("No valid response received within timeout.")
    return None

def process_response(response, pid):
    """
    Processes the received response, checks the checksum,
    and updates the data.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.info(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                logger.info(f"Temperature: {value:.2f}°C")
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
            elif sensor == 'Humidity':
                logger.info(f"Humidity: {value:.2f}%")
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
            else:
                logger.warning(f"Unknown PID {pid}: Value={value}")
        else:
            logger.error("Checksum mismatch.")
    else:
        logger.error("Invalid response length.")

# --------------------------- Define MQTT Callback Functions --------------------------- #

def on_connect(client, userdata, flags, rc):
    """
    Callback function for when the MQTT client connects to the broker.
    """
    if rc == 0:
        logger.info("Connected to MQTT Broker.")
        client.publish("cis3/status", "online", retain=True)
        publish_mqtt_discovery(client)
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc):
    """
    Callback function for when the MQTT client disconnects from the broker.
    """
    logger.error(f"Disconnected from MQTT Broker with return code {rc}")
    if rc != 0:
        logger.warning("Unexpected disconnection. Attempting to reconnect.")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")

# --------------------------- MQTT Initialization and Loop --------------------------- #

# Register MQTT callback functions
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    """
    Runs the MQTT client loop in a separate thread.
    """
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT loop error: {e}")

# Start MQTT loop in a separate daemon thread
mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

# --------------------------- MQTT Discovery Function --------------------------- #

def publish_mqtt_discovery(client):
    """
    Publishes MQTT discovery messages for all sensors to enable auto-discovery in Home Assistant.
    """
    # List to hold all sensor configurations
    sensors = []

    # ADC Channels Discovery
    for i in range(6):
        channel = f"channel_{i}"
        if i < 4:
            # Voltage Sensors
            sensor_type = "voltage"
            unit = "V"
            state_topic = f"cis3/{channel}/voltage"
            unique_id = f"cis3_{channel}_voltage"
            name = f"CIS3 Channel {i} Voltage"
            device_class = "voltage"
            icon = "mdi:flash"
            value_template = "{{ value }}"
        else:
            # Resistance Sensors
            sensor_type = "resistance"
            unit = "Ω"
            state_topic = f"cis3/{channel}/resistance"
            unique_id = f"cis3_{channel}_resistance"
            name = f"CIS3 Channel {i} Resistance"
            device_class = "resistance"  # Ensure Home Assistant recognizes this
            icon = "mdi:water-percent"
            value_template = "{{ value }}"

        # Sensor Configuration Dictionary
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

    # Slave Sensors Discovery
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

    # Publish discovery messages for each sensor
    for sensor in sensors:
        discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{sensor['unique_id']}/config"
        client.publish(discovery_topic, json.dumps(sensor), retain=True)
        logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

# --------------------------- ADC Reading and Processing --------------------------- #

def read_adc(channel):
    """
    Reads raw ADC value from the specified channel.
    """
    try:
        adc_response = spi.xfer2([1, (8 + channel) << 4, 0])
        adc_value = ((adc_response[1] & 3) << 8) + adc_response[2]
        logger.debug(f"ADC Channel {channel} Raw Value: {adc_value}")
        return adc_value
    except Exception as e:
        logger.error(f"Error reading ADC channel {channel}: {e}")
        return 0

async def process_adc_and_lin():
    """
    Main loop for LIN and ADC communication.
    """
    global ema_voltage, ema_resistance, low_pass_voltage, low_pass_resistance

    while True:
        # Process ADC Channels
        for i in range(6):
            channel = f"channel_{i}"
            raw_adc = read_adc(i)

            if i < 4:
                # Voltage Channels
                # Apply Moving Average
                avg_voltage = moving_average(raw_adc, buffers_ma_voltage[channel])

                # Apply Low-Pass Filter
                low_pass_voltage[channel] = low_pass_filter(avg_voltage, low_pass_voltage[channel], LOW_PASS_ALPHA)

                # Apply Exponential Moving Average
                ema_voltage[channel] = exponential_moving_average(low_pass_voltage[channel], ema_voltage[channel], EMA_ALPHA_VOLTAGE)

                # Calculate Voltage
                voltage = (ema_voltage[channel] / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER
                voltage = round(voltage, 2)

                # Update Latest Data
                latest_data["adc_channels"][channel]["voltage"] = voltage

            else:
                # Resistance Channels
                # Apply Moving Average
                avg_resistance = moving_average(raw_adc, buffers_ma_resistance[channel])

                # Apply Low-Pass Filter
                low_pass_resistance[channel] = low_pass_filter(avg_resistance, low_pass_resistance[channel], LOW_PASS_ALPHA)

                # Apply Exponential Moving Average
                ema_resistance[channel] = exponential_moving_average(low_pass_resistance[channel], ema_resistance[channel], EMA_ALPHA_RESISTANCE)

                # Prevent division by zero
                if ema_resistance[channel] == 0:
                    resistance = 0.0
                else:
                    # Calculate Resistance based on voltage divider formula
                    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - ema_resistance[channel])) / ema_resistance[channel]) / 10
                    resistance = round(resistance, 2)

                # Update Latest Data
                latest_data["adc_channels"][channel]["resistance"] = resistance

        # Process LIN Communication for each PID
        for pid in PID_DICT.keys():
            send_header(pid)
            response = read_response(3, pid)
            if response:
                process_response(response, pid)
            await asyncio.sleep(0.1)  # Short pause between PID requests

        # Send data to connected WebSocket clients
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.info("Sent updated data to WebSocket clients.")

        # Publish data to MQTT
        publish_to_mqtt()

        # Wait for the next cycle
        await asyncio.sleep(2)  # Update interval in seconds

# --------------------------- Define MQTT Publishing Function --------------------------- #

def publish_to_mqtt():
    """
    Publishes the latest data to MQTT state topics.
    """
    # Publish ADC Voltage and Resistance
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            # Voltage Topic
            state_topic = f"cis3/{channel}/voltage"
            payload = adc_data["voltage"]
            mqtt_client.publish(state_topic, str(payload))
            logger.info(f"Published {channel} Voltage: {payload} V to {state_topic}")
        else:
            # Resistance Topic
            state_topic = f"cis3/{channel}/resistance"
            payload = adc_data["resistance"]
            mqtt_client.publish(state_topic, str(payload))
            logger.info(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

    # Publish Slave Sensors (Temperature and Humidity)
    slave = latest_data["slave_sensors"]["slave_1"]
    for sensor, value in slave.items():
        if value is not None:
            state_topic = f"cis3/slave_1/{sensor.lower()}"
            mqtt_client.publish(state_topic, str(value))
            logger.info(f"Published Slave_1 {sensor}: {value} to {state_topic}")
        else:
            logger.error(f"Slave_1 {sensor} value is None, skipping MQTT publish.")

# --------------------------- Define Quart HTTP Server --------------------------- #

async def start_quart():
    """
    Starts the Quart HTTP server.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    await serve(app, config)
    logger.info("Quart HTTP server started.")

# --------------------------- Main Function --------------------------- #

async def main():
    """
    Main function to start the Quart server and process ADC & LIN data.
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
        logger.info("Shutting down ADC, LIN & MQTT Advanced Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        # Close SPI and UART interfaces
        spi.close()
        ser.close()
        # Publish offline status and disconnect MQTT
        mqtt_client.publish("cis3/status", "offline", retain=True)
        mqtt_client.disconnect()
        logger.info("ADC, LIN & MQTT Advanced Add-on has been shut down.")
