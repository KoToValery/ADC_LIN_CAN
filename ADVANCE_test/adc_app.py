# adc_app.py
# Copyright 2004 - 2024  biCOMM Design Ltd
#
# AUTH: Kostadin  Tosev
# DATE: 2024
#
# Target: RPi5
# Project CIS3
# Hardware PCB V3.0 & Lin slave-Pico
# Tool: Python 3
#
# 1. TestSPI,ADC - work. Measurement Voltage 0-10 V, resistive 0-1000 ohm
# 2. Test Power PI5V/4.5A - work
# 3. Test ADC communication - work
# 4. Test LIN communication - work
# 5. MQTT auto discovery of sensors- work
import os
import time
import json
import asyncio
import threading
import spidev
import paho.mqtt.client as mqtt
from quart import Quart, jsonify, websocket
from collections import deque
import logging

# Configure logging (errors only)
logging.basicConfig(
    level=logging.ERROR,  # Only log errors
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("mqtt_error.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
HTTP_PORT = 8091
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED = 1000000
SPI_MODE = 0
VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10000
MOVING_AVERAGE_WINDOW = 30
EMA_ALPHA = 0.1

MQTT_BROKER = 'localhost'  # Update if the broker is on another machine
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'

# LIN configuration
SYNC_BYTE = 0x55
BREAK_DURATION = 0.00135  # 1.35 ms
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 19200

def enhanced_checksum(data):
    """Calculate LIN checksum."""
    return (~sum(data) & 0xFF)

# Data storage
latest_data = {
    "adc_channels": {},
    "lin_sensors": {}
}

# Quart application
app = Quart(__name__)

# Initialize SPI
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = SPI_MODE

# Initialize UART for LIN
try:
    import serial
    uart = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
except Exception as e:
    logger.error(f"Failed to initialize LIN UART: {e}")
    uart = None

# MQTT callback functions
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.error("MQTT connected successfully to the broker")
        client.connected_flag = True
        # Publish availability status
        publish_availability("online")
    else:
        logger.error(f"MQTT connection failed with code: {rc}")
        client.connected_flag = False

def on_disconnect(client, userdata, rc):
    logger.error(f"MQTT disconnected with code: {rc}")
    client.connected_flag = False
    if rc != 0:
        logger.error("Unexpected MQTT disconnection. Attempting to reconnect...")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"MQTT reconnection error: {e}")

# Initialize MQTT client
mqtt.Client.connected_flag = False  # Custom connected flag

mqtt_client = mqtt.Client(client_id="adc_client")
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

# Register callback functions
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
except Exception as e:
    logger.error(f"Failed to connect to MQTT broker: {e}")
    exit(1)

mqtt_client.loop_start()

# Filtering
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
        adc = spi.xfer2(cmd)
        value = ((adc[1] & 3) << 8) + adc[2]
        return value
    return 0

def calculate_voltage(adc_value):
    if adc_value < 10:  # Noise threshold
        return 0.0
    return round((adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER, 2)

def calculate_resistance(adc_value):
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return round(resistance, 2)

def setup_mqtt_discovery(channel, sensor_type):
    """Publish minimal MQTT Discovery messages for Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3_channel_{channel}_{sensor_type}/config"
    payload = {
        "name": f"CIS3 Channel {channel} {sensor_type.capitalize()}",
        "state_topic": f"cis3/channel_{channel}/{sensor_type}",
        "unique_id": f"cis3_channel_{channel}_{sensor_type}",
        "device": {
            "identifiers": ["cis3_device"],
            "name": "CIS3 Device",
            "model": "CIS3 Model",
            "manufacturer": "Your Company"
        }
    }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)

def setup_mqtt_discovery_lin(sensor):
    """Publish LIN sensor Discovery messages for Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/lin_{sensor}/config"
    payload = {
        "name": f"LIN Sensor {sensor}",
        "state_topic": f"cis3/lin/{sensor}",
        "unique_id": f"cis3_lin_{sensor}",
        "device": {
            "identifiers": ["cis3_device"],
            "name": "CIS3 Device",
            "model": "CIS3 Model",
            "manufacturer": "Your Company"
        }
    }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)

def publish_sensor_data(channel, data, sensor_type):
    """Publish sensor data to MQTT."""
    if sensor_type == 'voltage':
        topic = f"cis3/channel_{channel}/voltage"
    else:
        topic = f"cis3/channel_{channel}/resistance"
    payload = json.dumps(data)
    mqtt_client.publish(topic, payload)

def publish_lin_data(sensor, value):
    """Publish LIN data to MQTT."""
    topic = f"cis3/lin/{sensor}"
    payload = json.dumps({"value": value})
    mqtt_client.publish(topic, payload)

def publish_availability(status):
    """Publish availability status to MQTT."""
    topic = "cis3/availability"
    mqtt_client.publish(topic, status, retain=True)

@app.route('/data')
async def data_route():
    """HTTP route to get the latest ADC and LIN data."""
    return jsonify(latest_data)

@app.websocket('/ws')
async def ws_route():
    """WebSocket route for real-time updates."""
    while True:
        await websocket.send(json.dumps(latest_data))
        await asyncio.sleep(1)

async def setup_discovery():
    await asyncio.sleep(10)  # Wait for MQTT initialization
    for ch in range(6):
        if ch < 4:
            setup_mqtt_discovery(ch, 'voltage')
        else:
            setup_mqtt_discovery(ch, 'resistance')
    for sensor in ["temperature", "humidity"]:
        setup_mqtt_discovery_lin(sensor)

async def process_adc_data():
    while True:
        for channel in range(6):
            raw_value = read_adc(channel)
            if channel < 4:
                value = calculate_voltage(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": value, "unit": "V"}
                publish_sensor_data(channel, {"voltage": value}, 'voltage')
            else:
                value = calculate_resistance(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}
                publish_sensor_data(channel, {"resistance": value}, 'resistance')
        await asyncio.sleep(1)

async def process_lin_data():
    """Process LIN sensor data and publish it."""
    while True:
        if uart:
            for sensor in ["temperature", "humidity"]:
                # Simulate LIN data read
                value = 25.0 if sensor == "temperature" else 60.0
                latest_data["lin_sensors"][sensor] = value
                publish_lin_data(sensor, value)
        await asyncio.sleep(5)

async def monitor_availability():
    publish_availability("online")
    while True:
        await asyncio.sleep(60)  # Publish availability every 60 seconds
        publish_availability("online")

async def main():
    """Start the Quart application and background tasks."""
    quart_task = asyncio.create_task(app.run_task(host='0.0.0.0', port=HTTP_PORT))
    await asyncio.gather(
        quart_task,
        setup_discovery(),
        process_adc_data(),
        process_lin_data(),
        monitor_availability()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        spi.close()
        if uart:
            uart.close()
