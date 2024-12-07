# adc_app.py
# AUTH: Kostadin Tosev
# DATE: 2024

# Target: RPi5
# Project CIS3
# Hardware PCB V3.0
# Python 3
#
# V01.01.09.2024.CIS3 - Combined ADC, LIN Master, MQTT, Quart with logging
#
# Features:
# 1. ADC reading (Voltage channels 0-3, Resistance channels 4-5) - async tasks
# 2. LIN Communication for LED control & temperature reading (LINMaster class integrated)
# 3. Quart async web server for data retrieval /health and /data
# 4. MQTT Discovery for Home Assistant sensors
# 5. Logging with Python logging module
# 6. Future-proof for adding CAN or other protocols

import os
import time
import json
import asyncio
import threading
import spidev
import websockets
import paho.mqtt.client as mqtt
from collections import deque
from datetime import datetime
from quart import Quart, jsonify
import logging
import serial
import struct

############################################
# Logging Setup
############################################
logging.basicConfig(
    level=logging.DEBUG,  # Можете да промените нивото на логване тук (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('ADC & LIN')

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

SUPERVISOR_WS_URL = "ws://supervisor/core/websocket"
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")

MQTT_BROKER = os.getenv("MQTT_BROKER", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

base_path = os.getenv('INGRESS_PATH', '')

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
# LINMaster Class (integrated here)
############################################
class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=19200, uart_timeout=1):
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

    def calculate_pid(self, identifier):
        id_bits = identifier & 0x3F
        p0 = ((id_bits >> 0) & 1) ^ ((id_bits >> 1) & 1) ^ ((id_bits >> 2) & 1) ^ ((id_bits >> 4) & 1)
        p1 = ~(((id_bits >> 1) & 1) ^ ((id_bits >> 3) & 1) ^ ((id_bits >> 4) & 1) ^ ((id_bits >> 5) & 1)) & 1
        pid = id_bits | (p0 << 6) | (p1 << 7)
        logger.debug(f"LINMaster PID calculated: 0x{pid:02X} for ID: 0x{identifier:02X}")
        return pid

    def calculate_checksum(self, data):
        checksum = sum(data) & 0xFF
        checksum = (~checksum) & 0xFF
        logger.debug(f"LINMaster checksum calculated: 0x{checksum:02X} for data: {data}")
        return checksum

    def send_break(self):
        if self.ser:
            try:
                self.ser.break_condition = True
                time.sleep(self.BREAK_DURATION)
                self.ser.break_condition = False
                time.sleep(0.0001)
                logger.debug("LINMaster sent BREAK.")
            except Exception as e:
                logger.error(f"Error sending BREAK: {e}")

    def send_header(self, identifier):
        if self.ser:
            try:
                self.send_break()
                pid = self.calculate_pid(identifier)
                self.ser.write(bytes([self.LIN_SYNC_BYTE]))
                logger.debug("LINMaster sent SYNC byte: 0x55")
                self.ser.write(bytes([pid]))
                logger.debug(f"LINMaster sent PID byte: 0x{pid:02X}")
                return pid
            except Exception as e:
                logger.error(f"Error sending header: {e}")
                return None
        else:
            logger.error("UART not initialized, cannot send header.")
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
                logger.debug(f"LINMaster read response: {filtered_response.hex()}")
                if len(filtered_response) == expected_length:
                    return filtered_response
                else:
                    logger.debug(f"Incomplete/invalid response. Expected {expected_length} bytes, got {len(filtered_response)}.")
                    return None
            except Exception as e:
                logger.error(f"Error reading response: {e}")
                return None
        else:
            logger.error("UART not initialized, cannot read response.")
            return None

    def send_request_frame(self, identifier):
        if self.ser:
            pid = self.send_header(identifier)
            if pid is None:
                return None
            expected_length = 3
            response = self.read_response(expected_length)
            if response:
                data = response[:2]
                received_checksum = response[2]
                calculated_checksum = self.calculate_checksum([pid] + list(data))
                logger.debug(f"LINMaster received data: {data.hex()}, Received CS: 0x{received_checksum:02X}, Calc CS: 0x{calculated_checksum:02X}")
                if received_checksum == calculated_checksum:
                    temperature = struct.unpack('<H', data)[0] / 100.0
                    logger.info(f"LINMaster valid response. Temperature: {temperature:.2f} °C")
                    return temperature
                else:
                    logger.warning("LINMaster checksum mismatch!")
                    return None
            else:
                logger.warning("LINMaster no valid response from slave.")
                return None
        else:
            logger.error("UART not initialized, cannot send request frame.")
            return None

    def send_data_frame(self, identifier, data_bytes):
        if self.ser:
            try:
                pid = self.send_header(identifier)
                if pid is None:
                    return False
                csum = self.calculate_checksum([pid] + list(data_bytes))
                frame = data_bytes + bytes([csum])
                self.ser.write(frame)
                logger.debug(f"LINMaster sent Data Frame: {frame.hex()}")
                return True
            except Exception as e:
                logger.error(f"Error sending Data Frame: {e}")
                return False
        else:
            logger.error("UART not initialized, cannot send data frame.")
            return False

    def control_led(self, identifier, state):
        command = 0x01 if state == "ON" else 0x00
        logger.info(f"LINMaster sending LED command: {state}")
        success = self.send_data_frame(identifier, bytes([command]))
        if success:
            logger.info(f"LINMaster LED {state} command successfully sent.")
        else:
            logger.warning(f"LINMaster failed to send LED {state} command.")
        return success

    def read_slave_temperature(self, identifier):
        logger.debug(f"LINMaster sending temperature request to ID: 0x{identifier:02X}")
        temperature = self.send_request_frame(identifier)
        if temperature is not None:
            logger.debug(f"LINMaster read temperature: {temperature:.2f} °C")
        else:
            logger.warning("LINMaster failed to read temperature.")
        return temperature

############################################
# Create LIN Master Instance
############################################
lin_master = LINMaster()

############################################
# Filtering for ADC
############################################
buffers_ma = {i: deque(maxlen=MOVING_AVERAGE_WINDOW) for i in range(6)}
values_ema = {i: None for i in range(6)}

def apply_moving_average(value, channel):
    buffers_ma[channel].append(value)
    if len(buffers_ma[channel]) == MOVING_AVERAGE_WINDOW:
        return sum(buffers_ma[channel]) / MOVING_AVERAGE_WINDOW
    return value

def apply_ema(value, channel):
    if values_ema[channel] is None:
        values_ema[channel] = value
    else:
        values_ema[channel] = EMA_ALPHA * value + (1 - EMA_ALPHA) * values_ema[channel]
    return values_ema[channel]

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

def calculate_voltage(adc_value):
    if adc_value < 10:
        return 0.0
    voltage = (adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER
    return round(voltage, 2)

def calculate_resistance(adc_value):
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return round(resistance, 2)

def process_channel(channel):
    raw_value = read_adc(channel)
    filtered_ma = apply_moving_average(raw_value, channel)
    filtered_ema = apply_ema(filtered_ma, channel)
    if channel < 4:
        return calculate_voltage(filtered_ema)
    else:
        return calculate_resistance(filtered_ema)

############################################
# Quart Routes
############################################
@app.route('/data')
async def data():
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

############################################
# MQTT Setup
############################################
mqtt_client = mqtt.Client(protocol=mqtt.MQTTv5)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    logger.debug(f"MQTT credentials set: user='{MQTT_USER}', pass_length={len(MQTT_PASS)}")
else:
    logger.warning("MQTT_USER and/or MQTT_PASS not set. Attempting to connect without credentials.")

def on_mqtt_connect(client, userdata, flags, reasonCode, properties=None):
    if reasonCode == 0:
        logger.info("Connected to MQTT Broker!")
        publish_mqtt_discovery_topics()
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {reasonCode}")

def on_mqtt_disconnect(client, userdata, reasonCode, properties=None):
    logger.warning(f"Disconnected from MQTT Broker with reason code {reasonCode}")

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect

def mqtt_connect():
    logger.info(f"Attempting MQTT connect with user='{MQTT_USER}', pass_length={len(MQTT_PASS)}")
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"Initiated connection to MQTT Broker at {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        logger.error(f"MQTT connection error: {e}")

def publish_mqtt_discovery_topics():
    # Voltage sensors: channel_0 to channel_3
    for i in range(4):
        sensor_id = f"adc_lin_channel_{i}_voltage"
        config_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/config"
        state_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/state"
        payload = {
            "name": f"ADC Channel {i} Voltage",
            "state_topic": state_topic,
            "unit_of_measurement": "V",
            "unique_id": sensor_id,
            "device": {
                "identifiers": ["adc_lin_addon"],
                "name": "ADC & LIN Addon",
                "model": "CIS3",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
        mqtt_client.publish(config_topic, json.dumps(payload), retain=True)
        logger.debug(f"Published MQTT discovery topic for {sensor_id}")

    # Resistance sensors: channel_4, channel_5
    for i in range(4, 6):
        sensor_id = f"adc_lin_channel_{i}_resistance"
        config_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/config"
        state_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/state"
        payload = {
            "name": f"ADC Channel {i} Resistance",
            "state_topic": state_topic,
            "unit_of_measurement": "Ω",
            "unique_id": sensor_id,
            "device": {
                "identifiers": ["adc_lin_addon"],
                "name": "ADC & LIN Addon",
                "model": "CIS3",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
        mqtt_client.publish(config_topic, json.dumps(payload), retain=True)
        logger.debug(f"Published MQTT discovery topic for {sensor_id}")

    # Temperature sensor from slave
    sensor_id = "adc_lin_slave_1_temperature"
    config_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/config"
    state_topic = f"homeassistant/sensor/adc_lin/{sensor_id}/state"
    payload = {
        "name": "Slave 1 Temperature",
        "state_topic": state_topic,
        "unit_of_measurement": "°C",
        "unique_id": sensor_id,
        "device": {
            "identifiers": ["adc_lin_addon"],
            "name": "ADC & LIN Addon",
            "model": "CIS3",
            "manufacturer": "biCOMM Design Ltd"
        }
    }
    mqtt_client.publish(config_topic, json.dumps(payload), retain=True)
    logger.debug(f"Published MQTT discovery topic for {sensor_id}")

    # LED state as a binary sensor
    sensor_id = "adc_lin_slave_1_led"
    config_topic = f"homeassistant/binary_sensor/adc_lin/{sensor_id}/config"
    state_topic = f"homeassistant/binary_sensor/adc_lin/{sensor_id}/state"
    payload = {
        "name": "Slave 1 LED",
        "state_topic": state_topic,
        "unique_id": sensor_id,
        "device_class": "light",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device": {
            "identifiers": ["adc_lin_addon"],
            "name": "ADC & LIN Addon",
            "model": "CIS3",
            "manufacturer": "biCOMM Design Ltd"
        }
    }
    mqtt_client.publish(config_topic, json.dumps(payload), retain=True)
    logger.debug(f"Published MQTT discovery topic for {sensor_id}")

def publish_mqtt_states():
    # Publish states for each sensor
    for i in range(4):
        val = latest_data["adc_channels"][f"channel_{i}"]["voltage"]
        state_topic = f"homeassistant/sensor/adc_lin/adc_lin_channel_{i}_voltage/state"
        mqtt_client.publish(state_topic, str(val), retain=False)
        logger.debug(f"Published state for adc_lin_channel_{i}_voltage: {val} V")

    for i in range(4, 6):
        val = latest_data["adc_channels"][f"channel_{i}"]["resistance"]
        state_topic = f"homeassistant/sensor/adc_lin/adc_lin_channel_{i}_resistance/state"
        mqtt_client.publish(state_topic, str(val), retain=False)
        logger.debug(f"Published state for adc_lin_channel_{i}_resistance: {val} Ω")

    temp_val = latest_data["slave_sensors"]["slave_1"]["value"]
    state_topic = "homeassistant/sensor/adc_lin/adc_lin_slave_1_temperature/state"
    mqtt_client.publish(state_topic, str(temp_val), retain=False)
    logger.debug(f"Published state for adc_lin_slave_1_temperature: {temp_val} °C")

    led_state = latest_data["slave_sensors"]["slave_1"]["led_state"]
    led_topic = "homeassistant/binary_sensor/adc_lin/adc_lin_slave_1_led/state"
    mqtt_client.publish(led_topic, led_state, retain=False)
    logger.debug(f"Published state for adc_lin_slave_1_led: {led_state}")

############################################
# Async Tasks
############################################
async def process_adc_data():
    logger.info("Starting process_adc_data task")
    while True:
        try:
            for channel in range(6):
                value = process_channel(channel)
                if channel < 4:
                    latest_data["adc_channels"][f"channel_{channel}"]["voltage"] = value
                    logger.debug(f"Channel {channel} Voltage: {value} V")
                else:
                    latest_data["adc_channels"][f"channel_{channel}"]["resistance"] = value
                    logger.debug(f"Channel {channel} Resistance: {value} Ω")
        except Exception as e:
            logger.error(f"Error in process_adc_data: {e}")
        await asyncio.sleep(0.05)

async def process_lin_operations():
    logger.info("Starting process_lin_operations task")
    while True:
        try:
            channel_0_voltage = latest_data["adc_channels"].get("channel_0", {}).get("voltage", 0)
            state = "ON" if channel_0_voltage > LED_VOLTAGE_THRESHOLD else "OFF"

            if latest_data["slave_sensors"]["slave_1"]["led_state"] != state:
                success = lin_master.control_led(0x01, state)
                if success:
                    latest_data["slave_sensors"]["slave_1"]["led_state"] = state
                    logger.info(f"LED turned {state} based on channel 0 voltage: {channel_0_voltage} V")
                else:
                    logger.warning(f"Failed to send LED {state} command.")

            temperature = lin_master.read_slave_temperature(0x01)
            if temperature is not None:
                latest_data["slave_sensors"]["slave_1"]["value"] = temperature
                logger.debug(f"Slave 1 Temperature: {temperature} °C")

        except Exception as e:
            logger.error(f"Error in process_lin_operations: {e}")
        await asyncio.sleep(0.5)

async def publish_states_to_mqtt():
    logger.info("Starting publish_states_to_mqtt task")
    while True:
        try:
            publish_mqtt_states()
        except Exception as e:
            logger.error(f"Error in publish_mqtt_states: {e}")
        await asyncio.sleep(2)

async def supervisor_websocket():
    try:
        async with websockets.connect(
            SUPERVISOR_WS_URL,
            extra_headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
        ) as ws:
            await ws.send(json.dumps({"type": "subscribe_events", "event_type": "state_changed"}))
            async for message in ws:
                pass  # Може да добавите обработка на събития тук, ако е необходимо
    except Exception as e:
        logger.error(f"Supervisor WebSocket error: {e}")

############################################
# Main Function
############################################
async def main():
    if lin_master.ser:
        logger.info("LINMaster successfully initialized.")
    else:
        logger.error("LINMaster not initialized. Check UART settings.")

    mqtt_connect()
    logger.info("Initiated MQTT connection.")

    while not mqtt_client.is_connected():
        logger.info("Waiting for MQTT connection...")
        await asyncio.sleep(1)

    logger.info("MQTT connected. Starting async tasks.")

    await asyncio.gather(
        process_adc_data(),
        process_lin_operations(),
        supervisor_websocket(),
        publish_states_to_mqtt()
    )

############################################
# Run Quart App and Main
############################################
if __name__ == '__main__':
    try:
        # Run Quart in a separate thread
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True).start()
        logger.info(f"Quart HTTP server started on port {HTTP_PORT}")

        # Run main async function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC & LIN Add-on...")
    finally:
        mqtt_client.loop_stop()
        if mqtt_client.is_connected():
            mqtt_client.disconnect()
        spi.close()
        if lin_master.ser:
            lin_master.ser.close()
        logger.info("ADC & LIN Add-on has been shut down.")
