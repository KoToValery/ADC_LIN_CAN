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
        self.ser.reset_input_buffer()
        self.send_break()
        self.ser.write(bytes([SYNC_BYTE, pid]))
        logger.debug(f"Sent Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
        time.sleep(0.1)

    def read_response(self, expected_data_length, pid):
        """
        Чакаме в отговор: (данни + checksum), без да търсим пак SYNC+PID в отговора.
        Ако искате да търсите SYNC+PID, запазете старата логика,
        но тогава слейвът трябва да ги изпраща обратно (което не е стандартно LIN).
        """
        start_time = time.time()
        buffer = bytearray()

        while (time.time() - start_time) < 2.0:  # 2 сек таймаут
            if self.ser.in_waiting > 0:
                data = self.ser.read(self.ser.in_waiting)
                buffer.extend(data)
                logger.debug(f"Received bytes: {data.hex()}")

                if len(buffer) >= expected_data_length:
                    response = buffer[:expected_data_length]
                    logger.debug(f"Extracted Response: {response.hex()}")
                    return response
            else:
                time.sleep(0.01)
        logger.debug("No valid response received within timeout.")
        return None

    def process_response(self, response, pid):
        """
        Проверява checksum и обновява latest_data в зависимост от PID.
        """
        if response and len(response) == 3:
            data = response[:2]
            received_checksum = response[2]
            calc_chksum = enhanced_checksum([pid] + list(data))
            logger.debug(f"Received Checksum: 0x{received_checksum:02X}, "
                         f"Calculated Checksum: 0x{calc_chksum:02X}")

            if received_checksum == calc_chksum:
                value = int.from_bytes(data, 'little') / 100.0
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

    async def process_lin_communication(self):
        """
        Асинхронна функция, която да извикваме периодично.
        Изпращаме header за всеки PID и четем отговорите.
        """
        for pid in PID_DICT.keys():
            self.send_header(pid)
            response = self.read_response(3, pid)  # 3 байта = 2 data + 1 checksum
            if response:
                self.process_response(response, pid)
            await asyncio.sleep(0.1)

    def close(self):
        try:
            self.ser.close()
        except Exception as e:
            logger.error(f"Error closing serial port: {e}")
