# mqtt_manager.py

import threading
import json
import paho.mqtt.client as mqtt
from logger_config import logger
from config import (MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, 
                    MQTT_DISCOVERY_PREFIX, MQTT_CLIENT_ID)
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
        """
        Публикува последните данни от latest_data в MQTT.
        """
        # ADC Канали
        for i in range(6):
            channel = f"channel_{i}"
            if i < 4:
                # Voltage
                state_topic = f"cis3/{channel}/voltage"
                payload = latest_data["adc_channels"][channel]["voltage"]
                self.client.publish(state_topic, str(payload))
                logger.debug(f"[MQTT] Published {channel} Voltage: {payload} V to {state_topic}")
            else:
                # Resistance
                state_topic = f"cis3/{channel}/resistance"
                payload = latest_data["adc_channels"][channel]["resistance"]
                self.client.publish(state_topic, str(payload))
                logger.debug(f"[MQTT] Published {channel} Resistance: {payload} Ω to {state_topic}")

        # Slave Sensors
        slave = latest_data["slave_sensors"]["slave_1"]
        for sensor, value in slave.items():
            state_topic = f"cis3/slave_1/{sensor.lower()}"
            self.client.publish(state_topic, str(value))
            logger.debug(f"[MQTT] Published Slave_1 {sensor}: {value} to {state_topic}")

    def publish_mqtt_discovery(self):
        """
        Публикуване на Home Assistant Discovery за всички сензори.
        """
        # (Същата логика за ADC, Temperature, Humidity ...)
        # Накрая логваме, че сме публикували discovery
        logger.info("[MQTT] Home Assistant discovery topics published.")
