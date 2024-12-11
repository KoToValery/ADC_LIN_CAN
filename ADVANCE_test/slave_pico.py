# slave_pico.py
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
# 4. Test LIN communication - work

from machine import Pin, UART
import time
import struct
import dht
from neopixel import NeoPixel

# UART Configuration
uart1 = UART(1, baudrate=9600, tx=Pin(8), rx=Pin(9))
uart1.init(bits=8, parity=None, stop=1)

# LIN Constants
SYNC_BYTE = 0x55
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51

# Advanced Communication Parameters
VALID_PIDS = [PID_TEMPERATURE, PID_HUMIDITY]
MAX_BUFFER_SIZE = 10
SYNC_WINDOW = 0.5  # seconds

# LED Configuration
ledPin = 1  # GP1
ledCount = 4
led = NeoPixel(Pin(ledPin, Pin.OUT), ledCount)

# Define Colors
GREEN = (0, 255, 0)
RED = (255, 0, 0)
OFF = (0, 0, 0)

# Initial LED State
led_state = False
led_toggle_interval = 1.0
last_led_toggle_time = 0

# DHT Sensor Configuration
DHT_PIN = Pin(0)
dht_sensor = dht.DHT11(DHT_PIN)

class LINSynchronizer:
    def __init__(self, uart, valid_pids):
        self.uart = uart
        self.valid_pids = valid_pids
        self.buffer = []
        self.last_sync_time = time.time()

    def find_valid_frame(self):
        """
        Attempt to find a valid LIN frame in the received data.
        Returns (sync, pid) if found, or (None, None) otherwise.
        """
        while self.uart.any():
            # Limit buffer size to prevent memory issues
            if len(self.buffer) > MAX_BUFFER_SIZE:
                self.buffer.pop(0)
            
            # Read next byte
            byte = self.uart.read(1)[0]
            self.buffer.append(byte)
            
            # Look for potential valid frames
            for i in range(len(self.buffer) - 1):
                if (self.buffer[i] == SYNC_BYTE and 
                    self.buffer[i+1] in self.valid_pids):
                    # Found a potential valid frame
                    sync = self.buffer[i]
                    pid = self.buffer[i+1]
                    
                    # Clear buffer up to this point
                    self.buffer = self.buffer[i+2:]
                    return sync, pid
        
        return None, None

def log_message(message):
    print(f"[{time.time():.3f}] [SLAVE] {message}")

def set_color(color):
    """Задава посочения цвят на всички NeoPixel LED-ове."""
    for i in range(ledCount):
        led[i] = color
    led.write()

def blink_color(color, times=3, delay_duration=0.2):
    """Мига с посочения цвят определен брой пъти с определена пауза."""
    for _ in range(times):
        set_color(color)
        time.sleep(delay_duration)
        set_color(OFF)
        time.sleep(delay_duration)

def turn_off_leds():
    """Изключва всички NeoPixel LED-ове."""
    set_color(OFF)

def turn_on_leds_green():
    """Задава всички NeoPixel LED-ове на зелено."""
    set_color(GREEN)

def read_temperature():
    """
    Read temperature from DHT11 sensor.
    Returns temperature in hundredths of a degree Celsius.
    """
    try:
        dht_sensor.measure()
        temperature = dht_sensor.temperature()
        temperature_encoded = int(temperature * 100)
        return temperature_encoded
    except OSError as e:
        log_message(f"Failed to read temperature: {e}")
        return None

def read_humidity():
    """
    Read humidity from DHT11 sensor.
    Returns humidity in hundredths of a percent.
    """
    try:
        dht_sensor.measure()
        humidity = dht_sensor.humidity()
        humidity_encoded = int(humidity * 100)
        return humidity_encoded
    except OSError as e:
        log_message(f"Failed to read humidity: {e}")
        return None

def calculate_checksum(data):
    """
    Calculate checksum by summing all bytes and returning the complement.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def handle_temperature_request():
    """Prepare and send temperature response."""
    temperature = read_temperature()
    if temperature is None:
        log_message("Sending error response due to sensor read failure.")
        temperature = 0  # Example: 0.00°C as error
    
    data = struct.pack('<H', temperature)  # Little-endian format
    checksum = calculate_checksum([PID_TEMPERATURE] + list(data))
    response = data + bytes([checksum])
    
    log_message(f"Temperature Response Sent: Data={data.hex()}, Checksum=0x{checksum:02X}")
    uart1.write(response)

def handle_humidity_request():
    """Prepare and send humidity response."""
    humidity = read_humidity()
    if humidity is None:
        log_message("Sending error response due to sensor read failure.")
        humidity = 0  # Example: 0.00% as error
    
    data = struct.pack('<H', humidity)  # Little-endian format
    checksum = calculate_checksum([PID_HUMIDITY] + list(data))
    response = data + bytes([checksum])
    
    log_message(f"Humidity Response Sent: Data={data.hex()}, Checksum=0x{checksum:02X}")
    uart1.write(response)

def linslave():
    global last_led_toggle_time
    
    # Initialize synchronizer
    synchronizer = LINSynchronizer(uart1, VALID_PIDS)
    
    log_message("LIN Slave is ready.")
    
    # Инициализиране на LED-овете със зелено
    turn_on_leds_green()
    
    try:
        while True:
            try:
                # Определяне на текущото време
                current_time = time.time()
                
                # Проверка за мигане на LED-а ако е необходимо (не е нужно с новата логика)
                
                # Опит за намиране на валидна LIN рамка
                sync, pid = synchronizer.find_valid_frame()
                
                if sync is not None and pid is not None:
                    log_message(f"Received Header: SYNC=0x{sync:02X}, PID=0x{pid:02X}")
                    
                    # Обработка според PID
                    if pid == PID_TEMPERATURE:
                        handle_temperature_request()
                        # Мигане на LED-а с червено при комуникация
                        blink_color(RED, times=3, delay_duration=0.2)
                        # Връщане на LED-а към зелено
                        turn_on_leds_green()
                    elif pid == PID_HUMIDITY:
                        handle_humidity_request()
                        # Мигане на LED-а с червено при комуникация
                        blink_color(RED, times=3, delay_duration=0.2)
                        # Връщане на LED-а към зелено
                        turn_on_leds_green()
            
            except Exception as e:
                log_message(f"Communication error: {e}")
            
            # Кратка пауза, за да се предотврати прекомерно натоварване на процесора
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        log_message("Shutting down LIN Slave...")
    
    finally:
        # При изход, изключва всички LED-ове
        turn_off_leds()
        log_message("LEDs turned off. LIN Slave has been shut down.")

# Start the LIN slave code
linslave()
