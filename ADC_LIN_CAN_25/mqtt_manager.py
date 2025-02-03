# mqtt_manager.py

import threading
import json
import paho.mqtt.client as mqtt
import logging

from logger_config import logger
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    MQTT_DISCOVERY_PREFIX, MQTT_CLIENT_ID
)
from shared_data import latest_data

class MqttManager:
    def __init__(self):
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT Broker.")
            client.publish("cis3/status", "online", retain=True)
            self.publish_mqtt_discovery()
        else:
            logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("Unexpected MQTT disconnection. Attempting to reconnect.")
            try:
                client.reconnect()
            except Exception as e:
                logger.error(f"Reconnection failed: {e}")

    def mqtt_loop(self):
        """
        Стартира loop_forever в отделна нишка.
        """
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self.client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT loop error: {e}")

    def start(self):
        """
        Стартира MQTT в daemon Thread.
        """
        thread = threading.Thread(target=self.mqtt_loop, daemon=True)
        thread.start()

    def publish_to_mqtt(self):
        """
        Публикува последните данни от latest_data в MQTT.
        """
        try:
            # ADC Канали
            for i in range(6):
                channel = f"channel_{i}"
                if i < 4:
                    # Voltage
                    state_topic = f"cis3/{channel}/voltage"
                    payload = latest_data["adc_channels"][channel]["voltage"]
                    self.client.publish(state_topic, str(payload))
                    logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
                else:
                    # Resistance
                    state_topic = f"cis3/{channel}/resistance"# mqtt_manager.py

import threading
import json
import paho.mqtt.client as mqtt
import logging

from logger_config import logger
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, 
    MQTT_DISCOVERY_PREFIX, MQTT_CLIENT_ID
)
from shared_data import latest_data

class MqttManager:
    def __init__(self):
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
        self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT Broker.")
            client.publish("cis3/status", "online", retain=True)
            self.publish_mqtt_discovery()
        else:
            logger.error(f"Failed to connect to MQTT Broker, return code {rc}")

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("Unexpected MQTT disconnection. Attempting to reconnect.")
            try:
                client.reconnect()
            except Exception as e:
                logger.error(f"Reconnection failed: {e}")

    def mqtt_loop(self):
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self.client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT loop error: {e}")

    def start(self):
        thread = threading.Thread(target=self.mqtt_loop, daemon=True)
        thread.start()

    def publish_to_mqtt(self):
        try:
            # Publish ADC Channels
            for i in range(6):
                channel = f"channel_{i}"
                if i < 4:
                    state_topic = f"cis3/{channel}/voltage"
                    payload = latest_data["adc_channels"][channel]["voltage"]
                    self.client.publish(state_topic, str(payload))
                    logger.debug(f"Published {channel} Voltage: {payload} V to {state_topic}")
                else:
                    state_topic = f"cis3/{channel}/resistance"
                    payload = latest_data["adc_channels"][channel]["resistance"]
                    self.client.publish(state_topic, str(payload))
                    logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

            # Publish Slave Sensor Data
            slave = latest_data["slave_sensors"]["slave_1"]
            for sensor, value in slave.items():
                state_topic = f"cis3/slave_1/{sensor.lower()}"
                self.client.publish(state_topic, str(value))
                logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")

            # Publish CAN status
            can_status = latest_data.get("can_status", "OFF")
            state_topic = "cis3/can/status"
            self.client.publish(state_topic, can_status)
            logger.debug(f"Published CAN status: {can_status} to {state_topic}")
        except Exception as e:
            logger.error(f"Error publishing to MQTT: {e}")

    def publish_mqtt_discovery(self):
        try:
            # MQTT Discovery for ADC Channels
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
                self.client.publish(discovery_topic, json.dumps(sensor), retain=True)
                logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

            # MQTT Discovery for Slave Sensors (Temperature, Humidity)
            for sensor_key in ["Temperature", "Humidity"]:
                sensor_lower = sensor_key.lower()
                sensor = {
                    "name": f"CIS3 Slave 1 {sensor_key}",
                    "unique_id": f"cis3_slave_1_{sensor_lower}",
                    "state_topic": f"cis3/slave_1/{sensor_lower}",
                    "unit_of_measurement": "%" if sensor_key == "Humidity" else "°C",
                    "device_class": "humidity" if sensor_key == "Humidity" else "temperature",
                    "icon": "mdi:water-percent" if sensor_key == "Humidity" else "mdi:thermometer",
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
                disc_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{sensor['unique_id']}/config"
                self.client.publish(disc_topic, json.dumps(sensor), retain=True)
                logger.info(f"Published MQTT discovery for {sensor['name']} to {disc_topic}")

            # MQTT Discovery for CAN status
            sensor = {
                "name": "CIS3 CAN Communication",
                "unique_id": "cis3_can_status",
                "state_topic": "cis3/can/status",
                "unit_of_measurement": "",
                "icon": "mdi:bus-alert",
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
            disc_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/cis3_can_status/config"
            self.client.publish(disc_topic, json.dumps(sensor), retain=True)
            logger.info(f"Published MQTT discovery for CAN status to {disc_topic}")
        except Exception as e:
            logger.error(f"Error publishing MQTT discovery: {e}")

                    payload = latest_data["adc_channels"][channel]["resistance"]
                    self.client.publish(state_topic, str(payload))
                    logger.debug(f"Published {channel} Resistance: {payload} Ω to {state_topic}")

            # Slave Sensors
            slave = latest_data["slave_sensors"]["slave_1"]
            for sensor, value in slave.items():
                state_topic = f"cis3/slave_1/{sensor.lower()}"
                self.client.publish(state_topic, str(value))
                logger.debug(f"Published Slave_1 {sensor}: {value} to {state_topic}")
        except Exception as e:
            logger.error(f"Error publishing to MQTT: {e}")

    def publish_mqtt_discovery(self):
        """
        Публикуване на Home Assistant Discovery за всички сензори.
        """
        try:
            # ADC канали
            for i in range(6):
                channel = f"channel_{i}"
                if i < 4:
                    # Voltage
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
                    # Resistance
                    sensor = {
                        "name": f"CIS3 Channel {i} Resistance",
                        "unique_id": f"cis3_{channel}_resistance",
                        "state_topic": f"cis3/{channel}/resistance",
                        "unit_of_measurement": "Ω",
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
                self.client.publish(discovery_topic, json.dumps(sensor), retain=True)
                logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")

            # Slave Sensors (Temperature, Humidity)
            for sensor_key in ["Temperature", "Humidity"]:
                sensor_lower = sensor_key.lower()
                sensor = {
                    "name": f"CIS3 Slave 1 {sensor_key}",
                    "unique_id": f"cis3_slave_1_{sensor_lower}",
                    "state_topic": f"cis3/slave_1/{sensor_lower}",
                    "unit_of_measurement": "%" if sensor_key == "Humidity" else "°C",
                    "device_class": "humidity" if sensor_key == "Humidity" else "temperature",
                    "icon": "mdi:water-percent" if sensor_key == "Humidity" else "mdi:thermometer",
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
                self.client.publish(discovery_topic, json.dumps(sensor), retain=True)
                logger.info(f"Published MQTT discovery for {sensor['name']} to {discovery_topic}")
        except Exception as e:
            logger.error(f"Error publishing MQTT discovery: {e}")
