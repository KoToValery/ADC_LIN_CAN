# lin_communication.py
# Работа с UART (LIN): header, read, write

import time
import serial
from logger_config import logger
from main import (
    UART_PORT, UART_BAUDRATE,
    SYNC_BYTE, BREAK_DURATION,
    PID_DICT
)

# Инициализация на сериен порт
try:
    ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
    logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
except Exception as e:
    logger.error(f"UART initialization error: {e}")
    exit(1)

def enhanced_checksum(data) -> int:
    """Изчислява LIN checksum (inverse на сбора на байтовете)."""
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

def send_break():
    """Изпраща BREAK сигнал за LIN."""
    ser.break_condition = True
    time.sleep(BREAK_DURATION)
    ser.break_condition = False
    time.sleep(0.0001)

def send_header(pid: int):
    """Изпраща SYNC + PID заглавие към LIN slave."""
    ser.reset_input_buffer()
    send_break()
    ser.write(bytes([SYNC_BYTE, pid]))
    logger.debug(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
    time.sleep(0.1)

def read_response(expected_data_length: int, pid: int):
    """Чете отговор от slave, търсейки SYNC+PID и извличайки нужния брой байтове."""
    start_time = time.time()
    buffer = bytearray()
    sync_pid = bytes([SYNC_BYTE, pid])

    while (time.time() - start_time) < 2.0:  # 2 секунди таймаут
        if ser.in_waiting > 0:
            data = ser.read(ser.in_waiting)
            buffer.extend(data)
            logger.debug(f"Received bytes: {data.hex()}")

            index = buffer.find(sync_pid)
            if index != -1:
                logger.debug(f"Found SYNC + PID at index {index}: {buffer[index:index+2].hex()}")
                buffer = buffer[index + 2:]

                if len(buffer) >= expected_data_length:
                    response = buffer[:expected_data_length]
                    logger.debug(f"Extracted Response: {response.hex()}")
                    return response
                else:
                    while len(buffer) < expected_data_length and (time.time() - start_time) < 2.0:
                        if ser.in_waiting > 0:
                            more_data = ser.read(ser.in_waiting)
                            buffer.extend(more_data)
                            logger.debug(f"Received additional bytes: {more_data.hex()}")
                        else:
                            time.sleep(0.01)
                    if len(buffer) >= expected_data_length:
                        response = buffer[:expected_data_length]
                        logger.debug(f"Extracted Response after waiting: {response.hex()}")
                        return response
        else:
            time.sleep(0.01)

    logger.debug("No valid response received within timeout.")
    return None
