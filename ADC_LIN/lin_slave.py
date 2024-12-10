from machine import Pin, UART
import time
import struct
import dht

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

# DHT Sensor Configuration
DHT_PIN = Pin(0)
dht_sensor = dht.DHT11(DHT_PIN)

def log_message(message):
    print(f"[{time.time():.3f}] [SLAVE] {message}")

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
    # Initialize synchronizer
    synchronizer = LINSynchronizer(uart1, VALID_PIDS)
    
    log_message("LIN Slave is ready.")
    
    while True:
        try:
            # Attempt to find a valid frame
            sync, pid = synchronizer.find_valid_frame()
            
            if sync is not None and pid is not None:
                log_message(f"Received Header: SYNC=0x{sync:02X}, PID=0x{pid:02X}")
                
                # Process based on PID
                if pid == PID_TEMPERATURE:
                    handle_temperature_request()
                elif pid == PID_HUMIDITY:
                    handle_humidity_request()
        
        except Exception as e:
            log_message(f"Communication error: {e}")
        
        # Short sleep to prevent tight looping
        time.sleep(0.01)

# Start the LIN slave code
linslave()
