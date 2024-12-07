# adc_app.py
# AUTH: Kostadin Tosev
# DATE: 2024
#
# Target: RPi5
# Project CIS3
# Hardware PCB V3.0
# Tool: Python 3
#
# V01.01.09.2024.CIS3 - optimized with MQTT integration
#
# Features:
# 1. ADC reading (Voltage channels 0-3, Resistance channels 4-5)
# 2. LIN Communication for LED control & temperature reading
# 3. Real-time dashboard via WebSocket and Ingress
# 4. MQTT Discovery for Home Assistant Sensors
# 5. Improved code structure & error handling

import os
import time
import json
import asyncio
import threading
import spidev
import websockets
import paho.mqtt.client as mqtt
from collections import deque
from flask import Flask, send_from_directory, jsonify, Response
from flask_sock import Sock
from lin_master import LINMaster  # Уверете се, че lin_master.py съдържа LINMaster класа

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

# MQTT Configuration
MQTT_BROKER = os.getenv("MQTT_BROKER", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

base_path = os.getenv('INGRESS_PATH', '')  # Ingress path configuration

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
# Flask App and WebSocket
############################################
app = Flask(__name__, static_folder='.')
sock = Sock(app)

############################################
# SPI Initialization
############################################
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)
spi.max_speed_hz = SPI_SPEED
spi.mode = SPI_MODE

############################################
# LIN Master Initialization
############################################
lin_master = LINMaster()

############################################
# Filtering
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

def process_channel(channel):
    raw_value = read_adc(channel)
    filtered_ma = apply_moving_average(raw_value, channel)
    filtered_ema = apply_ema(filtered_ma, channel)
    if channel < 4:
        return calculate_voltage(filtered_ema)
    else:
        return calculate_resistance(filtered_ema)

############################################
# Flask Routes
############################################
@app.route(f'{base_path}/')
def dashboard():
    return send_from_directory(app.static_folder, 'index.html')

@app.route(f'{base_path}/data')
def data():
    return jsonify(latest_data)

@app.route(f'{base_path}/health')
def health():
    return Response(status=200)

@sock.route(f'{base_path}/ws')
def websocket_handler(ws):
    while True:
        try:
            ws.send(json.dumps(latest_data))
            time.sleep(1)
        except websockets.exceptions.ConnectionClosed:
            print(f"[{datetime.now()}] [ADC & LIN] WebSocket connection closed")
            break
        except Exception as e:
            print(f"[{datetime.now()}] [ADC & LIN] WebSocket error: {e}")
            break

############################################
# MQTT Setup for Discovery
############################################

mqtt_client = mqtt.Client(protocol=mqtt.MQTTv5)
if MQTT_USER and MQTT_PASS:
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_mqtt_connect(client, userdata, flags, reasonCode, properties=None):
    if reasonCode == 0:
        print(f"[{datetime.now()}] [ADC & LIN] Connected to MQTT Broker!")
        publish_mqtt_discovery_topics()
    else:
        print(f"[{datetime.now()}] [ADC & LIN] Failed to connect to MQTT Broker, return code {reasonCode}")

def on_mqtt_disconnect(client, userdata, reasonCode, properties=None):
    print(f"[{datetime.now()}] [ADC & LIN] Disconnected from MQTT Broker with reason code {reasonCode}")

mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect

def mqtt_connect():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        print(f"[{datetime.now()}] [ADC & LIN] Initiated connection to MQTT Broker at {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        print(f"[{datetime.now()}] [ADC & LIN] MQTT connection error: {e}")

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
        print(f"[{datetime.now()}] [ADC & LIN] Published MQTT discovery topic for {sensor_id}")

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
        print(f"[{datetime.now()}] [ADC & LIN] Published MQTT discovery topic for {sensor_id}")

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
    print(f"[{datetime.now()}] [ADC & LIN] Published MQTT discovery topic for {sensor_id}")

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
    print(f"[{datetime.now()}] [ADC & LIN] Published MQTT discovery topic for {sensor_id}")

def publish_mqtt_states():
    # Publish states for each sensor
    # Voltage channels
    for i in range(4):
        val = latest_data["adc_channels"][f"channel_{i}"]["voltage"]
        state_topic = f"homeassistant/sensor/adc_lin/adc_lin_channel_{i}_voltage/state"
        mqtt_client.publish(state_topic, str(val), retain=False)
        print(f"[{datetime.now()}] [ADC & LIN] Published state for adc_lin_channel_{i}_voltage: {val} V")

    # Resistance channels
    for i in range(4, 6):
        val = latest_data["adc_channels"][f"channel_{i}"]["resistance"]
        state_topic = f"homeassistant/sensor/adc_lin/adc_lin_channel_{i}_resistance/state"
        mqtt_client.publish(state_topic, str(val), retain=False)
        print(f"[{datetime.now()}] [ADC & LIN] Published state for adc_lin_channel_{i}_resistance: {val} Ω")

    # Temperature
    temp_val = latest_data["slave_sensors"]["slave_1"]["value"]
    state_topic = "homeassistant/sensor/adc_lin/adc_lin_slave_1_temperature/state"
    mqtt_client.publish(state_topic, str(temp_val), retain=False)
    print(f"[{datetime.now()}] [ADC & LIN] Published state for adc_lin_slave_1_temperature: {temp_val} °C")

    # LED State
    led_state = latest_data["slave_sensors"]["slave_1"]["led_state"]
    led_topic = "homeassistant/binary_sensor/adc_lin/adc_lin_slave_1_led/state"
    mqtt_client.publish(led_topic, led_state, retain=False)
    print(f"[{datetime.now()}] [ADC & LIN] Published state for adc_lin_slave_1_led: {led_state}")

############################################
# Async Tasks
############################################

async def process_adc_data():
    while True:
        for channel in range(6):
            value = process_channel(channel)
            if channel < 4:
                latest_data["adc_channels"][f"channel_{channel}"]["voltage"] = value
                print(f"[{datetime.now()}] [ADC & LIN] Channel {channel} Voltage: {value} V")
            else:
                latest_data["adc_channels"][f"channel_{channel}"]["resistance"] = value
                print(f"[{datetime.now()}] [ADC & LIN] Channel {channel} Resistance: {value} Ω")
        await asyncio.sleep(0.05)  # Slightly slower to reduce CPU usage

async def process_lin_operations():
    while True:
        # LED control based on channel_0 voltage
        channel_0_voltage = latest_data["adc_channels"].get("channel_0", {}).get("voltage", 0)
        state = "ON" if channel_0_voltage > LED_VOLTAGE_THRESHOLD else "OFF"
        
        # Check if state has changed to avoid sending redundant commands
        if latest_data["slave_sensors"]["slave_1"]["led_state"] != state:
            success = lin_master.control_led(0x01, state)  # Използвайте правилния идентификатор
            if success:
                latest_data["slave_sensors"]["slave_1"]["led_state"] = state
                print(f"[{datetime.now()}] [ADC & LIN] LED turned {state} based on channel 0 voltage: {channel_0_voltage} V")
            else:
                print(f"[{datetime.now()}] [ADC & LIN] Failed to send LED {state} command to slave.")
        
        # Read temperature
        temperature = lin_master.read_slave_temperature(0x01)
        if temperature is not None:
            latest_data["slave_sensors"]["slave_1"]["value"] = temperature
            print(f"[{datetime.now()}] [ADC & LIN] Slave 1 Temperature: {temperature} °C")
        
        await asyncio.sleep(0.5)

async def publish_states_to_mqtt():
    # Periodically publish sensor states to MQTT
    while True:
        publish_mqtt_states()
        await asyncio.sleep(2)  # Update states every 2 seconds

async def supervisor_websocket():
    # Subscribe to state_changed but no special handling required for now
    try:
        async with websockets.connect(
            SUPERVISOR_WS_URL, 
            extra_headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
        ) as ws:
            await ws.send(json.dumps({"type": "subscribe_events", "event_type": "state_changed"}))
            async for message in ws:
                pass  # Just read events; no special handling
    except Exception as e:
        print(f"[{datetime.now()}] [ADC & LIN] Supervisor WebSocket error: {e}")

############################################
# Main Function
############################################

async def main():
    # Start Flask app in a separate thread for Ingress dashboard
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT), daemon=True)
    flask_thread.start()
    print(f"[{datetime.now()}] [ADC & LIN] Flask HTTP server started on port {HTTP_PORT}")

    # Initialize LIN Master
    if lin_master.ser:
        print(f"[{datetime.now()}] [ADC & LIN] LINMaster успешно инициализиран.")
    else:
        print(f"[{datetime.now()}] [ADC & LIN] LINMaster не е инициализиран. Проверете UART настройките.")

    # Connect to MQTT for discovery and state updates
    mqtt_connect()
    
    # Wait until MQTT is connected
    while not mqtt_client.is_connected():
        print(f"[{datetime.now()}] [ADC & LIN] Waiting for MQTT connection...")
        await asyncio.sleep(1)
    
    # Run tasks concurrently
    await asyncio.gather(
        process_adc_data(),
        process_lin_operations(),
        supervisor_websocket(),
        publish_states_to_mqtt()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"[{datetime.now()}] [ADC & LIN] Shutting down ADC & LIN Add-on...")
    finally:
        mqtt_client.loop_stop()
        if mqtt_client.is_connected():
            mqtt_client.disconnect()
        spi.close()
        if lin_master.ser:
            lin_master.ser.close()
