# Copyright 2004 - 2024  biCOMM Design Ltd
#
# AUTH: Kostadin  Tosev
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

import os
import time
import json
import asyncio
import threading
import spidev
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, Response
from collections import deque

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

MQTT_BROKER = 'localhost'  # Change if broker is on another machine
MQTT_PORT = 1883
MQTT_USERNAME = 'adc'  # Set if authentication is enabled
MQTT_PASSWORD = 'adc'
MQTT_DISCOVERY_PREFIX = 'homeassistant'

# Data storage
latest_data = {
    "adc_channels": {}
}

# Flask app
app = Flask(__name__)

# SPI initialization
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = SPI_MODE

# MQTT Initialization
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
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
        print(f"Raw ADC value for channel {channel}: {value}")
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

def process_channel(channel):
    raw_value = read_adc(channel)
    filtered_ma = apply_moving_average(raw_value, channel)
    filtered_ema = apply_ema(filtered_ma, channel)
    if channel < 4:
        return calculate_voltage(filtered_ema)
    return calculate_resistance(filtered_ema)

def setup_mqtt_discovery(channel, sensor_type):
    """Publish MQTT discovery messages for Home Assistant."""
    base_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3/channel_{channel}/config"
    if sensor_type == 'voltage':
        payload = {
            "name": f"CIS3 Channel {channel} Voltage",
            "state_topic": f"cis3/channel_{channel}/voltage",
            "unit_of_measurement": "V",
            "value_template": "{{ value_json.voltage }}",
            "device_class": "voltage",
            "unique_id": f"cis3_channel_{channel}_voltage",
            "availability_topic": "cis3/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["cis3_rpi5"],
                "name": "CIS3 RPi5",
                "model": "PCB V3.0",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
    elif sensor_type == 'resistance':
        payload = {
            "name": f"CIS3 Channel {channel} Resistance",
            "state_topic": f"cis3/channel_{channel}/resistance",
            "unit_of_measurement": "Ω",
            "value_template": "{{ value_json.resistance }}",
            "device_class": "current",
            "unique_id": f"cis3_channel_{channel}_resistance",
            "availability_topic": "cis3/availability",
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": {
                "identifiers": ["cis3_rpi5"],
                "name": "CIS3 RPi5",
                "model": "PCB V3.0",
                "manufacturer": "biCOMM Design Ltd"
            }
        }
    mqtt_client.publish(base_topic, json.dumps(payload), retain=True)

# Setup MQTT discovery for all channels
for ch in range(6):
    if ch < 4:
        setup_mqtt_discovery(ch, 'voltage')
    else:
        setup_mqtt_discovery(ch, 'resistance')

def publish_sensor_data(channel, data, sensor_type):
    """Publish sensor data to MQTT."""
    if sensor_type == 'voltage':
        topic = f"cis3/channel_{channel}/voltage"
    else:
        topic = f"cis3/channel_{channel}/resistance"
    payload = json.dumps(data)
    mqtt_client.publish(topic, payload)

# HTTP routes
@app.route('/')
def dashboard():
    return jsonify(latest_data)

@app.route('/data')
def data_route():
    return jsonify(latest_data)

@app.route('/health')
def health():
    return Response(status=200)

# Task 1: Separate the processing of ADC data into its own asynchronous function
async def process_adc_data():
    while True:
        for channel in range(6):
            value = process_channel(channel)
            if channel < 4:
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": value, "unit": "V"}
                publish_sensor_data(channel, {"voltage": value}, 'voltage')
            else:
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}
                publish_sensor_data(channel, {"resistance": value}, 'resistance')
        await asyncio.sleep(1)  # Adjust the sleep time as needed

# Task 2: Publish availability status
def publish_availability(status):
    mqtt_client.publish("cis3/availability", status, retain=True)

async def monitor_availability():
    publish_availability("online")
    while True:
        await asyncio.sleep(60)  # Keep publishing availability every 60 seconds
        publish_availability("online")

# Main function: Launch tasks concurrently
async def main():
    # Start Flask app in a separate thread
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True).start()
    
    # Run ADC processing and availability monitoring tasks
    await asyncio.gather(
        process_adc_data(),      # Task for processing ADC data
        monitor_availability()  # Task for publishing availability
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

