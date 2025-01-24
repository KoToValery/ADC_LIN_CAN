# tasks.py
# Асинхронни задачи (loops) за периодично изпълнение:
# - Четене на ADC
# - LIN комуникация
# - Публикуване в MQTT
# - WebSocket broadcast

import asyncio
import json

from logger_config import logger
from data_structures import latest_data, voltage_buffers, resistance_buffers, ema_values
from config import (ADC_INTERVAL, LIN_INTERVAL, MQTT_INTERVAL, WS_INTERVAL, 
                    VOLTAGE_THRESHOLD)
from spi_adc import (read_adc, calculate_voltage_from_raw, calculate_resistance_from_raw, spi)
from lin_communication import (send_header, read_response, process_response, ser)
from mqtt_integration import publish_to_mqtt
from quart_app import clients

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
    from config import PID_DICT
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
