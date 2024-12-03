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
from flask import Flask, jsonify, Response
from collections import deque

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
MOVING_AVERAGE_WINDOW = 30
EMA_ALPHA = 0.1

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
@app.route('/')
def dashboard():
    return jsonify(latest_data)

@app.route('/data')
def data():
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
            else:
                latest_data["adc_channels"][f"channel_{channel}"] = {"resistance": value, "unit": "Ω"}
        await asyncio.sleep(0.01)  # Maintain high update frequency for ADC data

# Main function: Launch tasks concurrently
async def main():
    # Start Flask app in a separate thread
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True).start()
    
    # Run ADC processing task
    await asyncio.gather(
        process_adc_data()  # Task for processing ADC data
    )

if __name__ == '__main__':
    asyncio.run(main())
