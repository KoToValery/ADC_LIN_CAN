# mqtt_integration.py
# Код за MQTT и Home Assistant Discovery.

import json
import logging
import threading
import paho.mqtt.client as mqtt

from logger_config import logger
from py_configuration import (MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
                    MQTT_DISCOVERY_PREFIX, MQTT_CLIENT_ID)
from data_structures import latest_data

mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

def on_connect(client, userdata, flags, rc):
    """
    Callback when the client connects to the MQTT broker.
    """
    if rc == 0:
        logger.info("Connected to MQTT Broker.")
        client.publish("cis3/status", "online", retain=True)
        publish_mqtt_discovery(client)
    else:
        logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc):
    """
    Callback when the client disconnects from the MQTT broker.
    """
    if rc != 0:
        logger.warning("Unexpected MQTT disconnection. Attempting to reconnect.")
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect

def mqtt_loop():
    """
    Runs the MQTT network loop in a separate thread.
    """
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT loop error: {e}")

# Стартиране на MQTT в отделен нишка
mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
mqtt_thread.start()

def publish_mqtt_discovery(client):
    """
    Publishes MQTT discovery messages for all sensors to enable 
    automatic integration with Home Assistant.
    """
    from config import PID_DICT
    # Publish MQTT Discovery for ADC Channels
    for i in range(6):
        channel = f"channel_{i}"
        if i < 4:
            # Voltage Sensors
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
            # Resistance Sensors
            sensor = {
                "name": f"CIS3 Channel {i} Resistance",
                "unique_id": f"cis3_{channel}_resistance",
                "state_topic": f"cis3/{channel}/resistance",
                "unit_of_measurement": "Ω",
                "device_class": "resistance",  # 'resistance' може да не е стандартен device_class
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

    # Publish MQTT Discovery for Slave Sensors
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
        logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

def publish_to_mqtt():
    """
    Publishes the latest sensor data to MQTT state topics.
    """
    # Publish ADC Channel Data
    for i in range(6):
        channel = f"channel_{i}"
        adc_data = latest_data["adc_channels"][channel]
        if i < 4:
            # Voltage Channels
            state_topic = f"cis3/{channel}/voltage"
            payload = adc_data["voltage"]
            mqtt_client.publish(state_topic, str(payload))
            logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
        else:
            # Resistance Channels
            state_topic = f"cis3/{channel}/resistance"
            payload = adc_data["resistance"]
            mqtt_client.publish(state_topic, str(payload))
            logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

    # Publish Slave Sensor Data
    slave = latest_data["slave_sensors"]["slave_1"]
    for sensor, value in slave.items():
        state_topic = f"cis3/slave_1/{sensor.lower()}"
        mqtt_client.publish(state_topic, str(value))
        logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")
