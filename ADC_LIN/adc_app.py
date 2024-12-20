# Copyright 2004 - 2024  biCOMM Design Ltd
#
# AUTH: Kostadin  Tosev
# DATE: 2024
#
# Target: RPi5
# Project CIS3
# Hardware PCB V3.0
# tooll : Python 3 
#  
#  V01.01.09.2024.CIS3  - temporary 01 
# 1. TestSPI,ADC - work. Mesuriment Voltage 0-10 V, resistive 0- 1000 0hm
# 2. Test Power PI5V/4.5A - work
# 3. Test ADC,LIN communication - work
# 4. Init USARTs 
import os
import time
import json
import asyncio
import threading
import spidev
import websockets
from collections import deque
from flask import Flask, send_from_directory, jsonify, Response
from flask_sock import Sock
from lin_master import LINMaster

# Configuration
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
base_path = os.getenv('INGRESS_PATH', '')  # Ingress path configuration

# Data storage
latest_data = {
    "adc_channels": {},
    "slave_sensors": {
        "slave_1": {
            "value": 0,
            "led_state": "OFF"
        }
    }
}

# Flask app and WebSocket
app = Flask(__name__, static_folder='.')
sock = Sock(app)

# SPI initialization
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = SPI_MODE

# LIN Master initialization
lin_master = LINMaster()

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

# HTTP routes
@app.route(f'{base_path}/')
def dashboard():
    return send_from_directory(app.static_folder, 'index.html')

@app.route(f'{base_path}/data')
def data():
    return jsonify(latest_data)

@app.route(f'{base_path}/health')
def health():
    return Response(status=200)

# WebSocket for real-time updates
@sock.route(f'{base_path}/ws')
def websocket_handler(ws):
    while True:
        ws.send(json.dumps(latest_data))
        time.sleep(1)

# Task 1: Separate the processing of ADC data into its own asynchronous function
async def process_adc_data():
    while True:
        for channel in range(6):
            value = process_channel(channel)
            if channel < 4:
                latest_data["adc_channels"][f"channel_{channel}"] = {"voltage": value, "unit": "V"}
            else:
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}
        await asyncio.sleep(0.01)  # Maintain high update frequency for ADC data

# Task 2: Create a separate function for LIN operations (e.g., LED control and temperature reading)
async def process_lin_operations():
    while True:
        # Read voltage from channel 0 and determine LED state
        channel_0_voltage = latest_data["adc_channels"].get("channel_0", {}).get("voltage", 0)
        state = "ON" if channel_0_voltage > LED_VOLTAGE_THRESHOLD else "OFF"
        lin_master.control_led(0x01, state)  # Add the identifier
        latest_data["slave_sensors"]["slave_1"]["led_state"] = state

        # Read slave temperature and update data
        temperature = lin_master.read_slave_temperature(0x01)  # Assuming slave ID is 0x01
        if temperature is not None:
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature

        await asyncio.sleep(0.1)  # Adjusted frequency for LIN operations (less critical)

# Task 3: Keep the Supervisor WebSocket separate and handle real-time events
async def supervisor_websocket():
    try:
        async with websockets.connect(SUPERVISOR_WS_URL, extra_headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}) as ws:
            await ws.send(json.dumps({"type": "subscribe_events", "event_type": "state_changed"}))
            async for message in ws:
                print(f"Message from HA: {message}")
    except Exception as e:
        print(f"Supervisor WebSocket error: {e}")

# Main function: Launch tasks concurrently
async def main():
    # Start Flask app in a separate thread
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True).start()
    
    # Use asyncio.gather to run all tasks concurrently
    await asyncio.gather(
        process_adc_data(),         # Task for processing ADC data
        process_lin_operations(),   # Task for LIN operations
        supervisor_websocket()      # Task for WebSocket communication
    )

if __name__ == '__main__':
    asyncio.run(main())
