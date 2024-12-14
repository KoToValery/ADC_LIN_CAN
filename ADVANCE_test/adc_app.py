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
# Version: V01.01.10.2024.CIS3 - updated with MA integrated into EMA and optimized logging
# 1. TestSPI,ADC - work. Measurement Voltage 0-10 V, resistive 0-1000 ohm
# 2. Test Power PI5V/4.5A - work
# 3. Test ADC communication - work
# 4. Test LIN communication - work
# 5. - updated with MQTT integration and integrated Moving Average into EMA

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
VOLTAGE_EMA_ALPHA = 0.1
RESISTANCE_EMA_ALPHA = 0.1

# Moving Average Configuration
VOLTAGE_MA = 10
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
# Initialize a dictionary to hold the latest sensor data
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

# Initialize deque for Moving Average (MA) with max length for voltage and resistance
ma_deques_voltage = {i: deque(maxlen=VOLTAGE_MA) for i in range(4)}
ma_deques_resistance = {i: deque(maxlen=RESISTANCE_MA) for i in range(4, 6)}

# -------------------- Quart Application Setup --------------------
# Initialize the Quart web application
app = Quart(__name__)

# Limit Quart logs to WARNING level to reduce verbosity
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.WARNING)

# Determine the base directory for serving files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Set to keep track of connected WebSocket clients
clients = set()

# -------------------- Quart Route Definitions --------------------
@app.route('/data')
async def data_route():
    """
    Endpoint to retrieve the latest sensor data in JSON format.
    """
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """
    Health check endpoint. Returns 200 OK if the service is running.
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
    WebSocket endpoint for real-time communication with clients.
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

# -------------------- SPI Initialization --------------------
# Initialize the SPI interface for ADC communication
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
# UART (Universal Asynchronous Receiver/Transmitter) Configuration for LIN
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

# -------------------- Filter State --------------------
# Initialize EMA state dictionaries
values_ema_voltage = {i: None for i in range(4)}
values_ema_resistance = {i: None for i in range(4, 6)}

# -------------------- Helper Functions --------------------
def apply_ma_then_ema(value, channel, is_voltage):
    """
    Applies Moving Average (MA) followed by Exponential Moving Average (EMA) to the provided value.

    Args:
        value (float): The current ADC value (voltage or resistance).
        channel (int): The ADC channel number.
        is_voltage (bool): Flag indicating if the value is voltage (True) or resistance (False).

    Returns:
        float: The filtered EMA value after applying MA.
    """
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

    if is_voltage:
        return round(ema_values[channel], 2)  # Voltage rounded to two decimals
    else:
        return int(round(ema_values[channel]))  # Resistance as integer

def read_adc(channel):
    """
    Reads the raw ADC value from a specific channel via SPI.

    Args:
        channel (int): ADC channel number (0-7).

    Returns:
        int: Raw ADC value or 0 if an error occurs.
    """
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]  # Command to read the specified channel
        try:
            adc = spi.xfer2(cmd)  # Perform SPI transfer
            value = ((adc[1] & 3) << 8) + adc[2]  # Combine bytes to get the ADC value
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage(adc_value):
    """
    Converts raw ADC value to voltage.

    Args:
        adc_value (int): Raw ADC value.

    Returns:
        float: Calculated voltage.
    """
    if adc_value < 10:
        return 0.0
    return (adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance(adc_value):
    """
    Converts raw ADC value to resistance.

    Args:
        adc_value (int): Raw ADC value.

    Returns:
        float: Calculated resistance.
    """
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return resistance

# LIN (Local Interconnect Network) Constants
SYNC_BYTE = 0x55  # Synchronization byte for LIN communication
BREAK_DURATION = 1.35e-3  # Duration of break signal in seconds (1.35ms)

# PID (Parameter Identifier) Definitions
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51  # New PID for Humidity

# Dictionary mapping PIDs to sensor names
PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

def send_break():
    """
    Sends a BREAK signal for LIN communication by setting the break condition on UART.
    """
    ser.break_condition = True
    time.sleep(BREAK_DURATION)  # Hold break condition
    ser.break_condition = False
    time.sleep(0.0001)  # Short pause after releasing break

def send_header(pid):
    """
    Sends SYNC + PID header to the slave device and clears the UART buffer.

    Args:
        pid (int): Parameter Identifier to send.
    """
    ser.reset_input_buffer()  # Clear UART buffer before sending
    send_break()  # Send BREAK signal
    ser.write(bytes([SYNC_BYTE, pid]))  # Write SYNC and PID bytes
    logger.info(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)  # Short pause for slave processing

async def read_response_async(expected_data_length, pid):
    """
    Reads the response from the slave device asynchronously, looking for SYNC + PID and then extracting the data.

    Args:
        expected_data_length (int): Number of bytes expected after SYNC + PID.
        pid (int): Parameter Identifier to match in the response.

    Returns:
        bytearray or None: The response data if valid, otherwise None.
    """
    expected_length = expected_data_length  # Number of data bytes expected
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # Timeout after 2 seconds
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)  # Read available bytes
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")  # Changed from INFO to DEBUG

            # Search for SYNC + PID sequence in the buffer
            index = buffer.find(sync_pid)
            if index != -1:
                logger.debug(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")  # Changed to DEBUG
                # Remove everything before and including SYNC + PID
                buffer = buffer[index + 2:]
                logger.debug(f"Filtered Buffer after SYNC + PID: {buffer.hex()}")

                # Check if there are enough bytes for data and checksum
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.debug(f"Filtered Response: {response.hex()}")  # Changed to DEBUG
                    return response
                else:
                    # Wait for the remaining data
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received bytes while waiting: {more_data.hex()}")  # Changed to DEBUG
                        else:
                            await asyncio.sleep(0.01)  # Use asyncio.sleep()
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.debug(f"Filtered Response after waiting: {response.hex()}")  # Changed to DEBUG
                        return response
        else:
            await asyncio.sleep(0.01)  # Use asyncio.sleep()

    logger.debug("No valid response received within timeout.")  # Changed to DEBUG
    return None

def enhanced_checksum(data):
    """
    Calculates the checksum by summing all bytes, taking the lowest byte, and returning its inverse.

    Args:
        data (list): List of integer byte values.

    Returns:
        int: Calculated checksum byte.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def process_response(response, pid):
    """
    Processes the received response, checks the checksum, and updates the latest data.

    Args:
        response (bytearray): The response data from the slave.
        pid (int): Parameter Identifier associated with the response.
    """
    if response and len(response) == 3:
        data = response[:2]  # Extract data bytes
        received_checksum = response[2]  # Extract checksum byte
        calculated_checksum = enhanced_checksum([pid] + list(data))  # Calculate expected checksum
        logger.debug(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")  # Changed to DEBUG

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0  # Convert bytes to float value
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                value = round(value, 2)  # Round temperature to two decimals
                logger.info(f"Temperature: {value:.2f}°C")
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value  # Update temperature
            elif sensor == 'Humidity':
                value = round(value, 2)  # Round humidity to two decimals
                logger.info(f"Humidity: {value:.2f}%")
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value  # Update humidity
            else:
                logger.debug(f"Unknown PID {pid}: Value={value}")  # Changed to DEBUG
        else:
            logger.warning("Checksum mismatch.")  # Changed to WARNING
    else:
        logger.warning("Invalid response length.")  # Changed to WARNING

# -------------------- MQTT Publishing Functions --------------------
async def publish_to_mqtt(client):
    """
    Publishes the latest sensor data to MQTT state topics asynchronously.

    Args:
        client (aiomqtt.Client): The MQTT client instance.
    """
    try:
        # ADC Channels
        for i in range(6):
            channel = f"channel_{i}"
            adc_data = latest_data["adc_channels"][channel]
            if i < 4:
                # Voltage Measurement
                state_topic = f"cis3/{channel}/voltage"
                payload = adc_data["voltage"]
                await client.publish(state_topic, str(payload), qos=1)
                logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
            else:
                # Resistance Measurement
                state_topic = f"cis3/{channel}/resistance"
                payload = adc_data["resistance"]
                await client.publish(state_topic, str(payload), qos=1)
                logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

        # Slave Sensors
        slave = latest_data["slave_sensors"]["slave_1"]
        for sensor, value in slave.items():
            state_topic = f"cis3/slave_1/{sensor.lower()}"
            await client.publish(state_topic, str(value), qos=1)
            logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")
    except Exception as e:
        logger.error(f"Error publishing to MQTT: {e}")

async def publish_mqtt_discovery(client):
    """
    Publishes MQTT discovery messages for all sensors to enable automatic discovery in Home Assistant.

    Args:
        client (aiomqtt.Client): The MQTT client instance.
    """
    try:
        # Publish MQTT Discovery for ADC Channels
        for i in range(6):
            channel = f"channel_{i}"
            if i < 4:
                # Voltage Sensor Configuration
                sensor_type = "voltage"
                unit = "V"
                state_topic = f"cis3/{channel}/voltage"
                unique_id = f"cis3_{channel}_voltage"
                name = f"CIS3 Channel {i} Voltage"
                device_class = "voltage"
                icon = "mdi:flash"
                value_template = "{{ value }}"
            else:
                # Resistance Sensor Configuration
                sensor_type = "resistance"
                unit = "Ω"
                state_topic = f"cis3/{channel}/resistance"
                unique_id = f"cis3_{channel}_resistance"
                name = f"CIS3 Channel {i} Resistance"
                device_class = "resistance"  # Device class for resistance
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
            await client.publish(discovery_topic, json.dumps(sensor), qos=1, retain=True)
            logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")
    except Exception as e:
        logger.error(f"Error publishing MQTT discovery: {e}")

# -------------------- ADC Reading Task --------------------
async def adc_reading_task():
    """
    Task to read and process ADC channels at defined intervals.
    Applies MA and EMA filtering to the ADC values.
    """
    while True:
        start_time = time.time()
        for channel in range(6):
            raw_value = read_adc(channel)
            if channel < 4:
                # Calculate voltage
                voltage = calculate_voltage(raw_value)
                # Apply MA and then EMA
                filtered_voltage = apply_ma_then_ema(voltage, channel, True)
                latest_data["adc_channels"][f"channel_{channel}"]["voltage"] = filtered_voltage
                logger.debug(f"Channel {channel} Voltage: {filtered_voltage} V (MA + EMA)")
            else:
                # Calculate resistance
                resistance = calculate_resistance(raw_value)
                # Apply MA and then EMA
                filtered_resistance = apply_ma_then_ema(resistance, channel, False)
                latest_data["adc_channels"][f"channel_{channel}"]["resistance"] = filtered_resistance
                logger.debug(f"Channel {channel} Resistance: {filtered_resistance} Ω (MA + EMA)")
        end_time = time.time()
        processing_time = end_time - start_time
        logger.debug(f"ADC data processing took {processing_time:.4f} seconds")  # Changed to DEBUG
        await asyncio.sleep(ADC_UPDATE_INTERVAL)

# -------------------- LIN Communication Task --------------------
async def lin_communication_task():
    """
    Task to handle LIN communication for each PID at defined intervals.
    """
    while True:
        start_time = time.time()
        for pid in PID_DICT.keys():
            send_header(pid)  # Send SYNC + PID header
            response = await read_response_async(3, pid)  # Async read
            if response:
                process_response(response, pid)  # Process and update data
            await asyncio.sleep(0.1)  # Short pause between requests
        end_time = time.time()
        elapsed = end_time - start_time
        sleep_time = LIN_UPDATE_INTERVAL - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            logger.warning("LIN communication tasks are taking longer than the defined interval.")

# -------------------- Data Sending Task --------------------
async def data_sending_task(client):
    """
    Task to send updated data to MQTT and WebSocket at defined intervals.

    Args:
        client (aiomqtt.Client): The MQTT client instance.
    """
    while True:
        # Send Updated Data to All Connected WebSocket Clients
        if clients:
            data_to_send = json.dumps(latest_data)
            await asyncio.gather(*(client.send(data_to_send) for client in clients))
            logger.debug("Sent updated data to WebSocket clients.")

        # Publish Latest Data to MQTT
        await publish_to_mqtt(client)  # Async MQTT publishing

        await asyncio.sleep(DATA_SEND_INTERVAL)  # Interval between each data send

# -------------------- Main Processing Function --------------------
async def process_adc_and_lin():
    """
    Main loop for handling ADC readings and LIN communication.
    Continuously reads sensor data, updates clients via WebSocket, and publishes to MQTT.
    """
    try:
        async with aiomqtt.Client(
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD
        ) as client:
            # Publish online status
            await client.publish("cis3/status", "online", qos=1, retain=True)
            await publish_mqtt_discovery(client)  # Publish discovery messages once

            # Create separate tasks for ADC, LIN, and Data Sending
            adc_task = asyncio.create_task(adc_reading_task())
            lin_task = asyncio.create_task(lin_communication_task())
            data_send_task = asyncio.create_task(data_sending_task(client))

            # Wait for all tasks to complete (which they won't, unless cancelled)
            await asyncio.gather(adc_task, lin_task, data_send_task)
    except Exception as e:
        logger.error(f"Error in ADC and LIN processing loop: {e}")
    finally:
        try:
            # Publish offline status
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

# -------------------- Graceful Shutdown --------------------
async def shutdown(signal_received):
    """
    Gracefully shuts down the application on receiving termination signals.

    Args:
        signal_received: The signal received.
    """
    logger.info(f"Received exit signal {signal_received.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    spi.close()  # Close SPI connection
    ser.close()  # Close UART connection
    logger.info("ADC, LIN & MQTT Advanced Add-on has been shut down.")
    asyncio.get_event_loop().stop()

# -------------------- Application Entry Point --------------------
async def main():
    """
    Starts the Quart HTTP server and the ADC & LIN processing loop.
    Handles graceful shutdown.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]  # Bind to all network interfaces on specified port
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")

    # Create tasks for Quart server and ADC & LIN processing
    quart_task = asyncio.create_task(serve(app, config))
    logger.info("Quart HTTP server started.")
    adc_lin_task = asyncio.create_task(process_adc_and_lin())
    logger.info("ADC and LIN processing task started.")

    # Handle graceful shutdown on signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, lambda sig=sig: asyncio.create_task(shutdown(sig)))

    await asyncio.gather(
        quart_task,
        adc_lin_task
    )

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
        # Note: MQTT offline status is handled in the processing loop's finally block
