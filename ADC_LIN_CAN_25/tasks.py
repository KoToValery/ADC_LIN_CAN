# tasks.py
# Асинхронни задачи: ADC цикли, LIN комуникация, MQTT, WebSocket

import json
import asyncio
import threading
from collections import deque

import paho.mqtt.client as mqtt
from logger_config import logger
from quart_app import clients

# Импортираме конфигурации от config.py
from config import (
    ADC_INTERVAL, LIN_INTERVAL, MQTT_INTERVAL, WS_INTERVAL,
    VOLTAGE_THRESHOLD,
    MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    MQTT_DISCOVERY_PREFIX, MQTT_CLIENT_ID,
    PID_DICT
)

# Импортираме ADC функции и SPI обект
from spi_adc import spi, read_adc, calculate_voltage_from_raw, calculate_resistance_from_raw

# Импортираме LIN функции и сериен обект
from lin_communication import ser, enhanced_checksum, send_header, read_response

# ============================
# Данни и буфери
# ============================

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

voltage_buffers = {ch: deque(maxlen=20) for ch in range(4)}  # MA за напрежение
resistance_buffers = {ch: deque(maxlen=30) for ch in range(4, 6)}  # MA за съпротивление
ema_values = {ch: None for ch in range(6)}  # EMA стойности за всички канали

# ============================
# MQTT Клиент и функции
# ============================

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
        logger.warning("Unexpected MQTT disconnection. Attempting to reconnect.")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT loop error: {e}")

# Стартиране на MQTT в отделен thread
mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

# ============================
# MQTT Discovery
# ============================

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
                "device_class": "resistance",
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
        logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

def publish_to_mqtt():
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            mqtt_client.publish(f"cis3/{channel}/voltage", adc_data["voltage"])
        else:
            mqtt_client.publish(f"cis3/{channel}/resistance", adc_data["resistance"])
    for sensor, value in latest_data["slave_sensors"]["slave_1"].items():
        mqtt_client.publish(f"cis3/slave_1/{sensor.lower()}", value)

# ============================
# LIN Обработка
# ============================

def process_response(response, pid):
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        calculated_checksum = enhanced_checksum([pid] + list(data))
        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0
            sensor = PID_DICT.get(pid, 'Unknown')
            latest_data["slave_sensors"]["slave_1"][sensor] = value
        else:
            logger.warning("Checksum mismatch.")
    else:
        logger.warning("Invalid response length.")

# ============================
# Асинхронни Задачи
# ============================

async def process_all_adc_channels():
    raw_values = [read_adc(ch) for ch in range(6)]
    for ch in range(4):
        voltage = calculate_voltage_from_raw(raw_values[ch])
        voltage_buffers[ch].append(voltage)
        ma_voltage = sum(voltage_buffers[ch]) / len(voltage_buffers[ch])
        alpha = 0.2
        ema_values[ch] = alpha * ma_voltage + (1 - alpha) * ema_values[ch] if ema_values[ch] else ma_voltage
        ema_values[ch] = 0.0 if ema_values[ch] < VOLTAGE_THRESHOLD else ema_values[ch]
        latest_data["adc_channels"][f"channel_{ch}"]["voltage"] = round(ema_values[ch], 2)
    for ch in range(4, 6):
        resistance = calculate_resistance_from_raw(raw_values[ch])
        resistance_buffers[ch].append(resistance)
        ma_resistance = sum(resistance_buffers[ch]) / len(resistance_buffers[ch])
        alpha = 0.1
        ema_values[ch] = alpha * ma_resistance + (1 - alpha) * ema_values[ch] if ema_values[ch] else ma_resistance
        latest_data["adc_channels"][f"channel_{ch}"]["resistance"] = round(ema_values[ch], 2)

async def process_lin_communication():
    for pid in PID_DICT.keys():
        send_header(pid)
        response = read_response(3, pid)
        if response:
            process_response(response, pid)
        await asyncio.sleep(0.1)

async def broadcast_via_websocket():
    if clients:
        data_to_send = json.dumps(latest_data)
        await asyncio.gather(*(client.send(data_to_send) for client in clients))

async def mqtt_publish_task():
    publish_to_mqtt()

# ============================
# Loop Корутините
# ============================

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
