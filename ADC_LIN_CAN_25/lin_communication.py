# lin_communication.py

import time
import serial
import asyncio
import logging
from logger_config import logger
from config import (
    SYNC_BYTE, BREAK_DURATION, PID_DICT,
    UART_PORT, UART_BAUDRATE, PID_TEMPERATURE, PID_HUMIDITY
)
from shared_data import latest_data

def enhanced_checksum(data):
    """
    Изчислява checksum, като събира всички байтове и връща инверсията му.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

class LinCommunication:
    def __init__(self):
        try:
            self.ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
            logger.info(f"UART interface initialized on {UART_PORT} at {UART_BAUDRATE} baud.")
        except Exception as e:
            logger.error(f"UART initialization error: {e}")
            raise e

    def send_break(self):
        """
        Изпраща BREAK сигнал за LIN комуникация.
        """
        try:
            self.ser.break_condition = True
            logger.debug("Sent BREAK condition.")
            time.sleep(BREAK_DURATION)
            self.ser.break_condition = False
            logger.debug("Released BREAK condition.")
            time.sleep(0.0001)  # кратка пауза
        except Exception as e:
            logger.error(f"Error sending BREAK: {e}")

    def send_header(self, pid):
        """
        Изпраща SYNC + PID към слейва и нулира UART буфера.
        """
        try:
            self.ser.reset_input_buffer()
            logger.debug("UART input buffer reset.")
            self.send_break()
            header = bytes([SYNC_BYTE, pid])
            self.ser.write(header)
            logger.info(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
            time.sleep(0.1)  # кратка пауза за слейва да обработи
        except Exception as e:
            logger.error(f"Error sending header: {e}")

    def read_response(self, expected_data_length):
        """
        Чете отговор от слейва, очаква `expected_data_length` байта.
        """
        try:
            start_time = time.time()
            buffer = bytearray()
            while (time.time() - start_time) < 2.0:  # 2 сек таймаут
                if self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    buffer.extend(data)
                    logger.debug(f"Received bytes: {data.hex()}")

                    if len(buffer) >= expected_data_length:
                        response = buffer[:expected_data_length]
                        logger.info(f"Extracted Response: {response.hex()}")
                        return response
                else:
                    time.sleep(0.01)
            logger.warning("No valid response received within timeout.")
            return None
        except Exception as e:
            logger.error(f"Error reading response: {e}")
            return None

    def process_response(self, response, pid):
        """
        Проверява checksum и обновява latest_data.
        """
        try:
            if response and len(response) == 3:
                data = response[:2]
                received_checksum = response[2]
                calculated_checksum = enhanced_checksum([pid] + list(data))
                logger.debug(f"Received Checksum: 0x{received_checksum:02X}, Calculated Checksum: 0x{calculated_checksum:02X}")

                if received_checksum == calculated_checksum:
                    value = int.from_bytes(data, 'little') / 100.0
                    sensor = PID_DICT.get(pid, 'Unknown')
                    if sensor == 'Temperature':
                        latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
                        logger.info(f"Updated Temperature: {value:.2f}°C")
                    elif sensor == 'Humidity':
                        latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                        logger.info(f"Updated Humidity: {value:.2f}%")
                    else:
                        logger.warning(f"Unknown PID {pid}: Value={value}")
                else:
                    logger.error("Checksum mismatch.")
            else:
                logger.error("Invalid response length.")
        except Exception as e:
            logger.error(f"Error processing response: {e}")

    async def process_lin_communication(self):
        """
        Асинхронно изпраща LIN заявки и обработва отговорите.
        """
        for pid in PID_DICT.keys():
            logger.debug(f"Processing PID: 0x{pid:02X}")
            self.send_header(pid)
            response = self.read_response(3)  # 2 data + 1 checksum
            if response:
                self.process_response(response, pid)
            else:
                logger.warning(f"No response for PID 0x{pid:02X}")
            await asyncio.sleep(0.1)  # кратка пауза между заявките

    def close(self):
        """
        Затваря UART връзката.
        """
        try:
            if self.ser.is_open:
                self.ser.close()
                logger.info("UART interface closed.")
        except Exception as e:
            logger.error(f"Error closing UART: {e}")
