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
    Изчислява checksum, като събира всички байтове и връща инверсията му.
    """
    checksum = sum(data) & 0xFF
    return (~checksum) & 0xFF

class LinCommunication:
    def __init__(self):
        try:
            self.ser = serial.Serial(UART_PORT, UART_BAUDRATE, timeout=1)
            logger.info(f"UART интерфейс инициализиран на {UART_PORT} с скорост {UART_BAUDRATE} baud.")
        except Exception as e:
            logger.error(f"Грешка при инициализация на UART: {e}")
            raise e

    def send_break(self):
        """
        Изпраща BREAK сигнал за LIN комуникация.
        """
        try:
            self.ser.break_condition = True
            logger.debug("Изпратен BREAK сигнал.")
            time.sleep(BREAK_DURATION)
            self.ser.break_condition = False
            logger.debug("Пуснат BREAK сигнал.")
            time.sleep(0.0001)  # Кратка пауза
        except Exception as e:
            logger.error(f"Грешка при изпращане на BREAK: {e}")

    def send_header(self, pid):
        """
        Изпраща SYNC + PID към слейва и нулира UART буфера.
        """
        try:
            self.ser.reset_input_buffer()
            logger.debug("Нулиран входен буфер на UART.")
            self.send_break()
            header = bytes([SYNC_BYTE, pid])
            self.ser.write(header)
            logger.info(f"Изпратен Header: SYNC=0x{SYNC_BYTE:02X}, PID=0x{pid:02X} ({PID_DICT.get(pid, 'Unknown')})")
            time.sleep(0.1)  # Кратка пауза за слейва да обработи
        except Exception as e:
            logger.error(f"Грешка при изпращане на Header: {e}")

    async def read_response(self, expected_data_length, pid):
        """
        Чете отговор от слейва, игнорирайки ехо байтовете.
        Очаква `expected_data_length` байта след [SYNC_BYTE, PID].
        """
        try:
            start_time = time.time()
            buffer = bytearray()

            while (time.time() - start_time) < 2.0:  # 2 секунди таймаут
                if self.ser.in_waiting > 0:
                    # Четене на наличните байтове в отделна нишка
                    data = await asyncio.to_thread(self.ser.read, self.ser.in_waiting)
                    buffer.extend(data)
                    logger.debug(f"Получени байтове: {data.hex()}")

                    # Търсене на [SYNC_BYTE, PID] в буфера
                    sync_pid_index = buffer.find(bytes([SYNC_BYTE, pid]))
                    if sync_pid_index != -1:
                        # Изрязване на байтовете преди [SYNC_BYTE, PID]
                        if sync_pid_index > 0:
                            logger.debug(f"Пропускане на {sync_pid_index} байта преди SYNC + PID.")
                            buffer = buffer[sync_pid_index:]

                        # Проверка дали има достатъчно байтове след [SYNC_BYTE, PID]
                        if len(buffer) >= 2 + expected_data_length:
                            # Пропускане на [SYNC_BYTE, PID]
                            buffer = buffer[2:]
                            response = buffer[:expected_data_length]
                            logger.info(f"Извлечен Response: {response.hex()}")
                            return response
                else:
                    await asyncio.sleep(0.01)  # Кратка пауза преди следваща проверка
            logger.warning("Не е получен валиден отговор в рамките на таймаута.")
            return None
        except Exception as e:
            logger.error(f"Грешка при четене на отговор: {e}")
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
                logger.debug(f"Получен Checksum: 0x{received_checksum:02X}, Изчислен Checksum: 0x{calculated_checksum:02X}")

                if received_checksum == calculated_checksum:
                    value = int.from_bytes(data, 'little') / 100.0
                    sensor = PID_DICT.get(pid, 'Unknown')
                    if sensor == 'Temperature':
                        latest_data["slave_sensors"]["slave_1"]["Temperature"] = value
                        logger.info(f"Обновена Температура: {value:.2f}°C")
                    elif sensor == 'Humidity':
                        latest_data["slave_sensors"]["slave_1"]["Humidity"] = value
                        logger.info(f"Обновена Влажност: {value:.2f}%")
                    else:
                        logger.warning(f"Неочакван PID {pid}: Стойност={value}")
                else:
                    logger.error(f"Checksum несъвпадение. Очакван: 0x{calculated_checksum:02X}, Получен: 0x{received_checksum:02X}")
            else:
                logger.error(f"Невалидна дължина на отговора. Очаквани 3 байта, получени {len(response)} байта.")
        except Exception as e:
            logger.error(f"Грешка при обработка на отговора: {e}")

    async def process_lin_communication(self):
        """
        Асинхронно изпраща LIN заявки и обработва отговорите.
        """
        for pid in PID_DICT.keys():
            logger.debug(f"Обработка на PID: 0x{pid:02X}")
            self.send_header(pid)
            response = await self.read_response(3, pid)  # 3 байта: 2 data + 1 checksum
            if response:
                self.process_response(response, pid)
            else:
                logger.warning(f"Няма отговор за PID 0x{pid:02X}")
            await asyncio.sleep(0.1)  # Кратка пауза между заявките

    def close(self):
        """
        Затваря UART връзката.
        """
        try:
            if self.ser.is_open:
                self.ser.close()
                logger.info("UART интерфейс затворен.")
        except Exception as e:
            logger.error(f"Грешка при затваряне на UART: {e}")
