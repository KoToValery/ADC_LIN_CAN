# lin_communication.py

import time
import serial
import asyncio
from logger_config import logger
from config import (SYNC_BYTE, BREAK_DURATION, PID_DICT, 
                    UART_PORT, UART_BAUDRATE, PID_TEMPERATURE, PID_HUMIDITY)
from shared_data import latest_data

def enhanced_checksum(data):
    """
    Изчислява checksum, като събира всички байтове,
    взема най-ниския байт и връща инверсията му.
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
        Изпраща BREAK сигнал за LIN.
        """
        self.ser.break_condition = True
        time.sleep(BREAK_DURATION)
        self.ser.break_condition = False
        time.sleep(0.0001)  # кратка пауза

    def send_header(self, pid):
        """
        Изпраща SYNC + PID. Преди това нулира входния буфер.
        """
        logger.debug(f"[LIN] Preparing to send header for PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
        self.ser.reset_input_buffer()
        self.send_break()
        # SYNC (0x55) + PID
        header = bytes([SYNC_BYTE, pid])
        self.ser.write(header)
        logger.debug(f"[LIN] Sent Header: {header.hex()}")
        time.sleep(0.1)

    def read_response(self, expected_data_length, pid):
        """
        Очакваме (данни + checksum) = expected_data_length байта.
        Не търсим повторно SYNC+PID в самия отговор.
        """
        start_time = time.time()
        buffer = bytearray()
        logger.debug(f"[LIN] Waiting for {expected_data_length} response bytes for PID=0x{pid:02X}...")

        while (time.time() - start_time) < 2.0:  # 2 сек таймаут
            in_wait = self.ser.in_waiting
            if in_wait > 0:
                data = self.ser.read(in_wait)
                buffer.extend(data)
                logger.debug(f"[LIN] Read {len(data)} bytes: {data.hex()} (total buffer={buffer.hex()})")

                if len(buffer) >= expected_data_length:
                    response = buffer[:expected_data_length]
                    logger.debug(f"[LIN] Extracted Response ({expected_data_length} bytes): {response.hex()}")
                    return response
            else:
                time.sleep(0.01)

        logger.debug(f"[LIN] No valid response (PID=0x{pid:02X}). Final buffer={buffer.hex()}")
        return None

    def process_response(self, response, pid):
        """
        Проверява checksum и обновява latest_data в зависимост от PID.
        """
        if response and len(response) == 3:
            data = response[:2]
            received_checksum = response[2]
            calc_chksum = enhanced_checksum([pid] + list(data))
            logger.debug(f"[LIN] Received Checksum=0x{received_checksum:02X}, Calc Checksum=0x{calc_chksum:02X}")

            if received_checksum == calc_chksum:
                value = int.from_bytes(data, 'little') / 100.0
                sensor = PID_DICT.get(pid, 'Unknown')
                logger.debug(f"[LIN] PID=0x{pid:02X} => Sensor={sensor}, Value={value:.2f}")
                if sensor == 'Temperature':
                    latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
                    logger.debug(f"[LIN] Updated Temperature in latest_data: {value:.2f}°C")
                elif sensor == 'Humidity':
                    latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                    logger.debug(f"[LIN] Updated Humidity in latest_data: {value:.2f}%")
                else:
                    logger.debug(f"[LIN] Unknown sensor for PID=0x{pid:02X}")
            else:
                logger.debug("[LIN] Checksum mismatch => ignoring response.")
        else:
            logger.debug(f"[LIN] Invalid response length or no response for PID=0x{pid:02X}")

    async def process_lin_communication(self):
        """
        Асинхронна функция, която да извикваме периодично (примерно на 2 секунди).
        Обхождаме всички PID, пращаме header, четем отговор, обработваме.
        """
        for pid in PID_DICT.keys():
            self.send_header(pid)
            response = self.read_response(3, pid)  # 3 байта = 2 data + 1 checksum
            if response:
                self.process_response(response, pid)
            else:
                logger.debug(f"[LIN] No response for PID=0x{pid:02X}")
            await asyncio.sleep(0.1)  # кратка пауза между заявките

    def close(self):
        try:
            self.ser.close()
            logger.info("Closed LIN serial port.")
        except Exception as e:
            logger.error(f"Error closing serial port: {e}")
