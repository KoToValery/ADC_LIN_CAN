# config.py

import os

# ============================
# Basic Logging & Config
# ============================

HTTP_PORT = 8099

# SPI (ADC) Configuration
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED_HZ = 1_000_000  # 1 MHz
SPI_MODE = 0

# ADC Constants
VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10_000  # Ohms

# Task Intervals (in seconds)
ADC_INTERVAL = 0.1     # ADC readings every 0.1s
LIN_INTERVAL = 2       # LIN communication every 2s
MQTT_INTERVAL = 1      # MQTT publishing every 1s
WS_INTERVAL = 1        # WebSocket broadcasting every 1s

# Voltage Threshold to Eliminate Minor Noise
VOLTAGE_THRESHOLD = 0.02  # Volts

# ============================
# LIN Communication Constants
# ============================
SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3  # 1.35 milliseconds

PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51  # New PID for Humidity

PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

# ============================
# UART Configuration for LIN
# ============================
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

# ============================
# MQTT Configuration
# ============================
MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

# ============================
# Supervisor & Ingress
# ============================
SUPERVISOR_WS_URL = os.getenv("SUPERVISOR_WS_URL", "ws://supervisor/core/websocket")
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = os.getenv('INGRESS_PATH', '')

if not SUPERVISOR_TOKEN:
    raise RuntimeError("SUPERVISOR_TOKEN is not set. Exiting.")
