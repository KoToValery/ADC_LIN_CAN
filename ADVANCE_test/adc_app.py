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

# Changes requested:
# 1. For voltage channels (0-3): Apply a 20-sample Moving Average (MA) then apply Exponential Moving Average (EMA) with alpha=0.2.
# 2. For resistance channels (4-5): Apply a 30-sample MA then EMA with alpha=0.1.
# 3. ADC calculations to run every 0.1s for all channels simultaneously.
# 4. LIN communication every 2s.
# 5. MQTT publishing every 1s.
# 6. WebSocket broadcast every 1s.
# Keep logic intact, reduce unnecessary logs, remove Bulgarian comments, and make all comments in English.

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

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ADC, LIN & MQTT')

logger.info("ADC, LIN & MQTT Add-on started.")

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

# Set intervals for tasks
ADC_INTERVAL = 0.1
LIN_INTERVAL = 2
MQTT_INTERVAL = 1
WS_INTERVAL = 1

SUPERVISOR_WS_URL = os.getenv("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = os.getenv('INGRESS_PATH', '')

if not SUPERVISOR_TOKEN:
    logger.error("SUPERVISOR_TOKEN is not set. Exiting.")
    exit(1)

# LIN constants
SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51

PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

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

app = Quart(__name__)

quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
clients = set()

@app.route('/data')
async def data_route():
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

@app.route('/')
async def index():
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except:
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    clients.add(websocket._get_current_object())
    try:
        while True:
            await websocket.receive()
    except:
        pass
    finally:
        clients.remove(websocket._get_current_object())

# SPI initialization
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = SPI_MODE
    logger.info("SPI interface initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

# UART initialization
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT}.")
except Exception as e:
    logger.error(f"UART init error: {e}")
    exit(1)

# MQTT setup
MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT Broker.")
        client.publish("cis3/status", "online", retain=True)
        publish_mqtt_discovery(client)
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        logger.warning("Unexpected MQTT disconnection.")

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT loop error: {e}")

mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

def enhanced_checksum(data):
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)

def send_header(pid):
    ser.reset_input_buffer()
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    time.sleep(0.1)

def read_response(expected_data_length, pid):
    expected_length = expected_data_length
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            index = buffer.find(sync_pid)
            if index != -1:
                buffer = buffer[index + 2:]
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    return response
                else:
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        return response
        else:
            time.sleep(0.01)
    return None

def process_response(response, pid):
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
            elif sensor == 'Humidity':
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value

# ADC filtering buffers and EMA storage
voltage_buffers = {ch: deque(maxlen=20) for ch in range(4)}
resistance_buffers = {ch: deque(maxlen=30) for ch in range(4,6)}
ema_values = {ch: None for ch in range(6)}

def read_adc(channel):
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            return value
        except:
            return 0
    return 0

def calculate_voltage_from_raw(raw_value):
    return (raw_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance_from_raw(raw_value):
    if raw_value == 0:
        return 0.0
    return ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - raw_value)) / raw_value) / 10

def publish_to_mqtt():
    # ADC
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            state_topic = f"cis3/{channel}/voltage"
            payload = adc_data["voltage"]
            mqtt_client.publish(state_topic, str(payload))
        else:
            state_topic = f"cis3/{channel}/resistance"
            payload = adc_data["resistance"]
            mqtt_client.publish(state_topic, str(payload))

    # Slave sensors
    slave = latest_data["slave_sensors"]["slave_1"]
    for sensor, value in slave.items():
        state_topic = f"cis3/slave_1/{sensor.lower()}"
        mqtt_client.publish(state_topic, str(value))

def publish_mqtt_discovery(client):
    for i in range(6):
        channel = f"channel_{i}"
        if i < 4:
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
            sensor = {
                "name": f"CIS3 Channel {i} Resistance",
                "unique_id": f"cis3_{channel}_resistance",
                "state_topic": f"cis3/{channel}/resistance",
                "unit_of_measurement": "Ω",
                "device_class": "pressure",  # no direct resistance class, using pressure as a placeholder
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

async def process_all_adc_channels():
    # Read raw values for all channels first
    raw_values = [read_adc(ch) for ch in range(6)]

    # Convert to voltage or resistance and apply filtering
    # Voltage channels 0-3
    for ch in range(4):
        voltage = calculate_voltage_from_raw(raw_values[ch])
        voltage_buffers[ch].append(voltage)
        if len(voltage_buffers[ch]) > 0:
            ma_voltage = sum(voltage_buffers[ch]) / len(voltage_buffers[ch])
            alpha = 0.2
            if ema_values[ch] is None:
                ema_values[ch] = ma_voltage
            else:
                ema_values[ch] = alpha * ma_voltage + (1 - alpha) * ema_values[ch]
            latest_data["adc_channels"][f"channel_{ch}"]["voltage"] = round(ema_values[ch], 2)

    # Resistance channels 4-5
    for ch in range(4,6):
        resistance = calculate_resistance_from_raw(raw_values[ch])
        resistance_buffers[ch].append(resistance)
        if len(resistance_buffers[ch]) > 0:
            ma_res = sum(resistance_buffers[ch]) / len(resistance_buffers[ch])
            alpha = 0.1
            if ema_values[ch] is None:
                ema_values[ch] = ma_res
            else:
                ema_values[ch] = alpha * ma_res + (1 - alpha) * ema_values[ch]
            latest_data["adc_channels"][f"channel_{ch}"]["resistance"] = round(ema_values[ch], 2)

async def process_lin_communication():
    for pid in PID_DICT.keys():
        send_header(pid)
        response = read_response(3, pid)
        if response:
            process_response(response, pid)

async def broadcast_via_websocket():
    if clients:
        data_to_send = json.dumps(latest_data)
        await asyncio.gather(*(client.send(data_to_send) for client in clients))

async def mqtt_publish_task():
    publish_to_mqtt()

async def adc_loop():
    while True:
        await process_all_adc_channels()
        await asyncio.sleep(ADC_INTERVAL)

async def lin_loop():
    while True:
        await process_lin_communication()
        await asyncio.sleep(LIN_INTERVAL)

async def mqtt_loop_task():
    while True:
        await mqtt_publish_task()
        await asyncio.sleep(MQTT_INTERVAL)

async def websocket_loop():
    while True:
        await broadcast_via_websocket()
        await asyncio.sleep(WS_INTERVAL)

async def main():
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]
    quart_task = asyncio.create_task(serve(app, config))
    adc_task = asyncio.create_task(adc_loop())
    lin_task = asyncio.create_task(lin_loop())
    mqtt_task = asyncio.create_task(mqtt_loop_task())
    ws_task = asyncio.create_task(websocket_loop())
    await asyncio.gather(quart_task, adc_task, lin_task, mqtt_task, ws_task)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        spi.close()
        ser.close()
        mqtt_client.publish("cis3/status", "offline", retain=True)
        mqtt_client.disconnect()
        logger.info("Shut down complete.")

        mqtt_client.publish("cis3/status", "offline", retain=True)
        mqtt_client.disconnect()
        logger.info("ADC, LIN & MQTT Advanced Add-on has been shut down.")
