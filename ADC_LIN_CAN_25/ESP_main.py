# ESP_main.py (for ESP32 running MicroPython)

import machine
import time

try:
    # Initialize the CAN interface (adjust TX/RX pins as required)
    can = machine.CAN(0, tx=machine.Pin(5), rx=machine.Pin(4), mode=machine.CAN.NORMAL, baudrate=500000)
    print("CAN interface initialized.")
except Exception as e:
    print("Error initializing CAN interface:", e)
    can = None

def send_can_message():
    """
    Sends a simple CAN message periodically.
    Message ID: 0x123, Data: b'CAN_OK'
    """
    try:
        message_id = 0x123
        data = b'CAN_OK'  # 6 bytes of data
        can.send(data, message_id)
        print("CAN message sent:", data)
    except Exception as e:
        print("Error sending CAN message:", e)

while True:
    if can:
        send_can_message()
    time.sleep(1)  # send every 1 second
