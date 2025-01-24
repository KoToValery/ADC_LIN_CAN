# lin_communication.py
# Функционалност за LIN: изпращане на header, четене на данни, обработка на отговори.

import time
import logging
import serial

from logger_config import logger
from config import (SYNC_BYTE, BREAK_DURATION, PID_DICT, UART_PORT, 
                    UART_BAUDRATE)
from data_structures import latest_data

# Инициализация на UART за LIN
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

def enhanced_checksum(data):
    """
    Calculates the checksum by summing all bytes, taking the lowest byte,
    and returning its inverse.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    """
    Sends a BREAK signal for LIN communication.
    """
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)  # Brief pause after break

def send_header(pid):
    """
    Sends SYNC + PID header to the slave device and clears the UART buffer.
    
    Args:
        pid (int): Parameter Identifier byte.
    """
    ser.reset_input_buffer()  # Clear UART buffer before sending
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.debug(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)  # Short pause for slave processing

def read_response(expected_data_length, pid):
    """
    Reads the response from the slave, looking for SYNC + PID and then extracting the data.
    """
    expected_length = expected_data_length
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # 2-second timeout
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")

            # Search for SYNC + PID in the buffer
            index = buffer.find(sync_pid)
            if index != -1:
                logger.debug(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")
                buffer = buffer[index + 2:]  # Remove everything before and including SYNC + PID

                # Check if enough bytes are available
                if len(buffer) >= expected_length:
                    response = buffer[:expected_length]
                    logger.debug(f"Extracted Response: {response.hex()}")
                    return response
                else:
                    # Wait for the remaining data
                    while len(buffer) < expected_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received additional bytes: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_length:
                        response = buffer[:expected_length]
                        logger.debug(f"Extracted Response after waiting: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)  # Brief pause before checking again

    logger.debug("No valid response received within timeout.")
    return None

def process_response(response, pid):
    """
    Processes the received response, verifies the checksum, 
    and updates the latest_data structure.
    """
    if response and len(response) == 3:
        data = response[:2]
        received_checksum = response[2]
        from config import enhanced_checksum as _placeholder  # само за пояснение, реално не трябва
        calculated_checksum = enhanced_checksum([pid] + list(data))
        logger.debug(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

        if received_checksum == calculated_checksum:
            value = int.from_bytes(data, 'little') / 100.0  # Convert to appropriate scale
            sensor = PID_DICT.get(pid, 'Unknown')
            if sensor == 'Temperature':
                latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
                logger.debug(f"Updated Temperature: {value:.2f}°C")
            elif sensor == 'Humidity':
                latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                logger.debug(f"Updated Humidity: {value:.2f}%")
            else:
                logger.debug(f"Unknown PID {pid}: Value={value}")
        else:
            logger.debug("Checksum mismatch.")
    else:
        logger.debug("Invalid response length.")
