# lin_communication.py

import time
import serial
import asyncio
import logging
from logger_config import logger
from config import (
    SYNC_BYTE, BREAK_DURATION, PID_DICT,
    UART_PORT, UART_BAUDRATE
)
from shared_data import latest_data


def enhanced_checksum(data):
    """
    Calculates checksum by summing all bytes and returning its inversion.
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
        Sends a BREAK signal for LIN communication.
        """
        try:
            self.ser.break_condition = True
            logger.debug("BREAK signal sent.")
            time.sleep(BREAK_DURATION)
            self.ser.break_condition = False
            logger.debug("BREAK signal released.")
            time.sleep(0.0001)  # Short pause
        except Exception as e:
            logger.error(f"Error sending BREAK: {e}")

    def send_header(self, pid):
        """
        Sends SYNC + PID to the slave and clears the UART buffer.
        """
        try:
            self.ser.reset_input_buffer()
            logger.debug("UART input buffer cleared.")
            self.send_break()
            header = bytes([SYNC_BYTE, pid])
            self.ser.write(header)
            logger.debug(f"Header sent: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
            time.sleep(0.1)  # Short pause for slave to process
        except Exception as e:
            logger.error(f"Error sending header: {e}")

    async def read_response(self, expected_data_length, pid):
        """
        Reads response from the slave, ignoring echoed bytes.
        Expects `expected_data_length` bytes after [SYNC_BYTE, PID].
        """
        try:
            start_time = time.time()
            buffer = bytearray()

            while (time.time() - start_time) < 2.0:  # 2-second timeout
                if self.ser.in_waiting > 0:
                    # Read available bytes in a separate thread
                    data = await asyncio.to_thread(self.ser.read, self.ser.in_waiting)
                    buffer.extend(data)
                    logger.debug(f"Received bytes: {data.hex()}")

                    # Search for [SYNC_BYTE, PID] in the buffer
                    sync_pid_index = buffer.find(bytes([SYNC_BYTE, pid]))
                    if sync_pid_index != -1:
                        # Remove bytes before [SYNC_BYTE, PID]
                        if sync_pid_index > 0:
                            logger.debug(f"Skipping {sync_pid_index} bytes before SYNC + PID.")
                            buffer = buffer[sync_pid_index:]

                        # Check if there are enough bytes after [SYNC_BYTE, PID]
                        if len(buffer) >= 2 + expected_data_length:
                            # Remove [SYNC_BYTE, PID]
                            buffer = buffer[2:]
                            response = buffer[:expected_data_length]
                            logger.debug(f"Extracted Response: {response.hex()}")
                            return response
                else:
                    await asyncio.sleep(0.01)  # Short pause before next check
            logger.warning("No valid response received within timeout.")
            return None
        except Exception as e:
            logger.error(f"Error reading response: {e}")
            return None

    def process_response(self, response, pid):
        """
        Validates checksum and updates latest_data.
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
                        logger.debug(f"Updated Temperature: {value:.2f}°C")
                    elif sensor == 'Humidity':
                        latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                        logger.debug(f"Updated Humidity: {value:.2f}%")
                    else:
                        logger.warning(f"Unknown PID {pid}: Value={value}")
                else:
                    logger.error(f"Checksum mismatch. Expected: 0x{calculated_checksum:02X}, Received: 0x{received_checksum:02X}")
            else:
                logger.error(f"Invalid response length. Expected 3 bytes, got {len(response)} bytes.")
        except Exception as e:
            logger.error(f"Error processing response: {e}")

    async def process_lin_communication(self):
        """
        Asynchronously sends LIN requests and processes responses.
        """
        for pid in PID_DICT.keys():
            logger.debug(f"Processing PID: 0x{pid:02X}")
            self.send_header(pid)
            response = await self.read_response(3, pid)  # 3 bytes: 2 data + 1 checksum
            if response:
                self.process_response(response, pid)
            else:
                logger.warning(f"No response for PID 0x{pid:02X}")
            await asyncio.sleep(0.1)  # Short pause between requests

    def close(self):
        """
        Closes the UART connection.
        """
        try:
            if self.ser.is_open:
                self.ser.close()
                logger.info("UART interface closed.")
        except Exception as e:
            logger.error(f"Error closing UART: {e}")
