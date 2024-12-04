import time
import struct
from machine import Pin, UART
from dht import DHT11

# Initialize UART1 and LED
uart = UART(1, baudrate=9600, tx=Pin(8), rx=Pin(9))  # Use GPIO8 (TX) and GPIO9 (RX) for UART1
led_pin = Pin(1, Pin.OUT)  # GPIO1 for LED
# Initialize DHT11 Sensor on a separate pin
dhtPin = 0  # Use GPIO2 for DHT11 to avoid conflict
dht = DHT11(Pin(dhtPin, Pin.IN))

def calculate_checksum(data, enhanced=False):
    """Calculate LIN checksum."""
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF if not enhanced else checksum

def calculate_pid(identifier):
    """Calculate PID with parity bits for LIN 2.x."""
    id_bits = identifier & 0x3F  # First 6 bits
    p0 = (id_bits & 0x01) ^ ((id_bits >> 1) & 0x01) ^ ((id_bits >> 2) & 0x01) ^ ((id_bits >> 4) & 0x01)
    p1 = ~(((id_bits >> 1) & 0x01) ^ ((id_bits >> 3) & 0x01) ^ ((id_bits >> 4) & 0x01) ^ ((id_bits >> 5) & 0x01)) & 0x01
    return (id_bits | (p0 << 6) | (p1 << 7))

def handle_led_command(command):
    """Handles LED control commands."""
    if command == 0x01:  # Turn LED on
        led_pin.on()
    elif command == 0x00:  # Turn LED off
        led_pin.off()

def read_temperature():
    """Reads temperature from DHT sensor."""
    dht.measure()
    temp = dht.temperature()  # Temperature in Celsius
    return round(temp, 2)

def linslave():
    """Main LIN Slave loop."""
    while True:
        if uart.any():
            header = uart.read(2)
            if len(header) == 2:
                sync, pid_received = header
                if sync == 0x55:  # Valid Sync byte
                    pid = calculate_pid(0x10)  # Slave ID is 0x10
                    if pid == pid_received:  # Check if PID matches
                        command_frame = uart.read(1)
                        if command_frame:  # If command frame received
                            command = command_frame[0]
                            handle_led_command(command)
                        else:  # No command, send temperature response
                            temperature = read_temperature()
                            data = struct.pack('<f', temperature)
                            checksum = calculate_checksum(data)
                            uart.write(data + bytes([checksum]))
        time.sleep(0.1)

linslave()
