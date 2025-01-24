# config.py
# Конфигурационни константи

# HTTP Server Configuration
HTTP_PORT = 8099

# SPI Configuration
SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED_HZ = 1_000_000
SPI_MODE = 0

# ADC Constants
VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10_000  # Ohms

# Intervals (seconds)
ADC_INTERVAL = 0.1
LIN_INTERVAL = 2
MQTT_INTERVAL = 1
WS_INTERVAL = 1

# Voltage Threshold
VOLTAGE_THRESHOLD = 0.02

# UART Configuration
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

# MQTT Configuration
MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

# LIN Protocol Constants
SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3

PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51

PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}
