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

import os
import time
import json
import asyncio
import threading
import spidev
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, Response
from collections import deque
import logging

# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for detailed logging
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("mqtt_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -------------------------
# Configuration
# -------------------------
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

MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'

# Data Storage
latest_data = {
    "adc_channels": {}
}

# -------------------------
# Flask Application Setup
# -------------------------
app = Flask(__name__)

# -------------------------
# SPI Initialization
# -------------------------
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize SPI interface: {e}")
    exit(1)

# -------------------------
# MQTT Client Initialization
# -------------------------
mqtt_client = mqtt.Client(client_id="adc_client")
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

@mqtt_client.on_connect
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Successfully connected to MQTT broker.")
        client.connected_flag = True
    else:
        logger.error(f"Failed to connect to MQTT broker with return code {rc}")

@mqtt_client.on_disconnect
def on_disconnect(client, userdata, rc):
    logger.warning(f"Disconnected from MQTT broker with return code {rc}")
    client.connected_flag = False
    if rc != 0:
        logger.warning("Unexpected disconnection. Attempting to reconnect...")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Error while reconnecting to MQTT broker: {e}")

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
except Exception as e:
    logger.error(f"Failed to connect to MQTT broker: {e}")
    exit(1)

mqtt_client.loop_start()

# -------------------------
# ADC Functions
# -------------------------
def read_adc(channel):
    """Reads the raw ADC value from the specified channel."""
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            logger.debug(f"Raw ADC value for channel {channel}: {value}")
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage(adc_value):
    """Calculates voltage from ADC value."""
    if adc_value < 10:  # Noise threshold
        return 0.0
    return round((adc_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER, 2)

def calculate_resistance(adc_value):
    """Calculates resistance from ADC value."""
    if adc_value <= 10 or adc_value >= (ADC_RESOLUTION - 10):
        return 0.0
    resistance = ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - adc_value)) / adc_value) / 10
    return round(resistance, 2)

# -------------------------
# MQTT Discovery Functions
# -------------------------
def setup_mqtt_discovery(channel, sensor_type):
    """Publishes minimal MQTT discovery messages for Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3_channel_{channel}_{sensor_type}/config"
    if sensor_type == 'voltage':
        payload = {
            "name": f"CIS3 Channel {channel} Voltage",
            "state_topic": f"cis3/channel_{channel}/voltage",
            "unit_of_measurement": "V",
            "value_template": "{{ value }}",
            "unique_id": f"cis3_channel_{channel}_voltage",
            "device": {
                "identifiers": ["cis3_device"],
                "name": "CIS3 ADC Device",
                "manufacturer": "biCOMM Design Ltd",
                "model": "CIS3 PCB V3.0"
            },
            "availability_topic": "cis3/availability",
            "payload_available": "online",
            "payload_not_available": "offline"
        }
    elif sensor_type == 'resistance':
        payload = {
            "name": f"CIS3 Channel {channel} Resistance",
            "state_topic": f"cis3/channel_{channel}/resistance",
            "unit_of_measurement": "\u2126",  # Ohm symbol
            "value_template": "{{ value }}",
            "unique_id": f"cis3_channel_{channel}_resistance",
            "device": {
                "identifiers": ["cis3_device"],
                "name": "CIS3 ADC Device",
                "manufacturer": "biCOMM Design Ltd",
                "model": "CIS3 PCB V3.0"
            },
            "availability_topic": "cis3/availability",
            "payload_available": "online",
            "payload_not_available": "offline"
        }
    else:
        logger.error(f"Unknown sensor type: {sensor_type}")
        return

    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)
    logger.info(f"Published MQTT discovery message for channel {channel} ({sensor_type})")

def publish_sensor_data(channel, value, sensor_type):
    """Publishes sensor data to MQTT."""
    topic = f"cis3/channel_{channel}/{sensor_type}"
    mqtt_client.publish(topic, value, retain=True)
    logger.debug(f"Published value {value} to topic {topic}")

def publish_availability(status):
    """Publishes availability status to MQTT."""
    mqtt_client.publish("cis3/availability", status, retain=True)
    logger.info(f"Published availability status: {status}")

# -------------------------
# Flask Routes
# -------------------------
@app.route('/data')
def get_data():
    """Endpoint to retrieve the latest sensor data."""
    return jsonify(latest_data)

@app.route('/health')
def health_check():
    """Health check endpoint."""
    return Response(status=200)

# -------------------------
# Asynchronous Tasks
# -------------------------
async def setup_discovery():
    """Initializes MQTT discovery for all channels."""
    await asyncio.sleep(10)  # Wait for MQTT to connect
    for channel in range(6):
        if channel < 4:
            setup_mqtt_discovery(channel, 'voltage')
        else:
            setup_mqtt_discovery(channel, 'resistance')

async def process_adc_data():
    """Continuously reads ADC data and publishes to MQTT."""
    while True:
        for channel in range(6):
            raw_value = read_adc(channel)
            if channel < 4:
                voltage = calculate_voltage(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": voltage, "unit": "V"}
                publish_sensor_data(channel, voltage, 'voltage')
            else:
                resistance = calculate_resistance(raw_value)
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": resistance, "unit": "\u2126"}
                publish_sensor_data(channel, resistance, 'resistance')
        await asyncio.sleep(1)  # Adjust the sleep interval as needed

async def monitor_availability():
    """Publishes availability status periodically."""
    publish_availability("online")
    while True:
        await asyncio.sleep(60)  # Publish availability every 60 seconds
        publish_availability("online")

async def run_flask():
    """Runs the Flask web server in a separate thread."""
    def run_app():
        app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, use_reloader=False)
    flask_thread = threading.Thread(target=run_app, daemon=True)
    flask_thread.start()
    await asyncio.sleep(0)  # Yield control to the event loop

async def main():
    """Main asynchronous function to start all tasks."""
    await run_flask()
    await asyncio.gather(
        setup_discovery(),
        process_adc_data(),
        monitor_availability()
    )

# -------------------------
# Entry Point
# -------------------------
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script terminated by user.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        spi.close()
        logger.info("MQTT client disconnected and SPI closed.")
