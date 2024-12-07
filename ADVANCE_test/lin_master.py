import time
import serial
import struct
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
from datetime import datetime

# UART for LIN
UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600  # Standard LIN baudrate
ser = serial.Serial(
    UART_PORT,
    UART_BAUDRATE,
    timeout=1,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE
)

# LIN Constants
SYNC_BYTE = 0x55
PID_BYTE = 0x50
BREAK_DURATION = 1.35e-3  # 1.35ms break signal
RESPONSE_TIMEOUT = 0.1  # 100ms timeout for slave response
TIME_INTERVAL = 2  # Period of 2 seconds per channel

latest_data = {}

def enhanced_checksum(data):
    """Calculate Enhanced checksum according to LIN 2.x"""
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    """Send LIN break field"""
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)  # Small delay after break
    print(f"[{datetime.now()}] [MASTER] Sent BREAK.", flush=True)

def send_header():
    """Send LIN header (Break + Sync + PID)"""
    pid = PID_BYTE
    print(f"[{datetime.now()}] [MASTER] Sending LIN header (SYNC+PID). Expected PID: 0x50", flush=True)
    send_break()
    ser.write([SYNC_BYTE])  # Send Sync Byte
    print(f"[{datetime.now()}] [MASTER] Sent SYNC byte: 0x55", flush=True)
    ser.write([pid])        # Send PID
    print(f"[{datetime.now()}] [MASTER] Sent PID: 0x50", flush=True)
    return pid

def read_response(expected_length):
    """Read response from slave with timeout"""
    start_time = time.time()
    response = bytearray()
    
    while (time.time() - start_time) < RESPONSE_TIMEOUT:
        if ser.in_waiting:
            byte = ser.read(1)
            response.extend(byte)
            if len(response) >= expected_length:
                break
    
    # Remove null bytes if any
    filtered_response = bytearray(b for b in response if b != 0)

    if len(filtered_response) == expected_length:
        print(f"[{datetime.now()}] [MASTER] Raw Response (hex): {filtered_response.hex()}", flush=True)
    else:
        print(f"[{datetime.now()}] [MASTER] No complete response or incomplete data. Raw read: {response.hex()} Filtered: {filtered_response.hex()}", flush=True)
        
    return filtered_response if len(filtered_response) == expected_length else None

def lin_transaction():
    """Perform LIN transaction to request temperature"""
    start_tx_time = time.time()
    print(f"\n[{datetime.now()}] [MASTER] Initiating LIN transaction...", flush=True)
    pid = send_header()  # Send header with fixed PID
    
    # Expecting 2 bytes (temperature) + 1 byte (checksum) = 3 bytes
    expected_length = 3
    response = read_response(expected_length)
    end_tx_time = time.time()
    tx_duration = end_tx_time - start_tx_time

    # Филтриране на "ехо" пакети: Ако response е None или нещо друго
    # Виждаме в логовете, че "ехо" пакетите имат formata 0x55 0x50 след филтър, но тук очакваме 3 байта.
    # Ако не получим 3 байта, отиваме в условието response == None така или иначе.
    #
    # Но ако все пак искаме допълнителна сигурност (ако в бъдеще логиката се промени), можем да проверим:
    if response is not None:
        # Ако response изглежда като eхо: SYNC+PID = 0x55,0x50, но имаме нужда от 3 байта за валиден отговор.
        # Тук имаме 3 байта, така че ако получим точно 2 байта 0x55 0x50, това ще бъде response=None, тъй като очакваме 3 байта.
        # Ако в бъдеще очакваме друг размер, проверяваме така:
        # if len(response) == 2 and response[0] == SYNC_BYTE and response[1] == PID_BYTE:
        #     print(f"[{datetime.now()}] [MASTER] Echo packet detected (just SYNC+PID returned). Filtering out.", flush=True)
        #     response = None
        #
        # В нашия случай полученият валиден отговор винаги е 3 байта. Ехо пакетите, които се виждат в логовете, са само 2 байта (след филтър).
        # Затова response=None ще покрива вече случаите на ехо (тъй като не постигаме 3 байта).

        # Тук сме в случай, че имаме 3 байта (може да е валидно или невалидно).
        data = response[:2]  # First 2 bytes for temperature
        received_checksum = response[2]  # Last byte
        calculated_checksum = enhanced_checksum(data)
        
        print(f"[{datetime.now()}] [MASTER] Received data (hex): {data.hex()} Checksum: {received_checksum:02X}, Calculated: {calculated_checksum:02X}", flush=True)
        if received_checksum == calculated_checksum:
            temperature = struct.unpack('<H', data)[0] / 100.0
            print(f"[{datetime.now()}] [MASTER] Valid response. Temperature: {temperature:.2f} °C | Transaction time: {tx_duration:.4f} s", flush=True)
            return temperature
        else:
            print(f"[{datetime.now()}] [MASTER] Checksum mismatch! | Transaction time: {tx_duration:.4f} s", flush=True)
    else:
        # Тук response е None - няма валиден пакет от 3 байта.
        print(f"[{datetime.now()}] [MASTER] No valid response from slave. | Transaction time: {tx_duration:.4f} s", flush=True)
    return None

class RequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler"""
    def log_message(self, format, *args):
        # Suppress default HTTP logs
        pass
    
    def do_GET(self):
        global latest_data
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(latest_data).encode())

def http_server():
    """Run HTTP server"""
    server_address = ('0.0.0.0', 8080)
    httpd = HTTPServer(server_address, RequestHandler)
    print(f"[{datetime.now()}] [MASTER] HTTP server running at http://0.0.0.0:8080", flush=True)
    httpd.serve_forever()

def send_data():
    """Send LIN requests and update responses"""
    global latest_data
    while True:
        print(f"\n[{datetime.now()}] [MASTER] --- Attempting to communicate with LIN slave ---", flush=True)
        response_temperature = lin_transaction()
        
        if response_temperature is not None:
            latest_data["temperature"] = {
                "value": response_temperature,
                "unit": "°C"
            }
        else:
            print(f"[{datetime.now()}] [MASTER] Received invalid or no data from slave.", flush=True)
        
        time.sleep(TIME_INTERVAL)  # Wait for next communication

def main():
    """Main function to start threads"""
    print(f"[{datetime.now()}] [MASTER] Starting main function...", flush=True)
    threading.Thread(target=send_data, daemon=True).start()
    threading.Thread(target=http_server, daemon=True).start()
    while True:
        time.sleep(1)

if __name__ == '__main__':
    main()

