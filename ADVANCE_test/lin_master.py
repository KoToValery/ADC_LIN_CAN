# lin_master.py
# AUTH: Kostadin Tosev
# DATE: 2024

import time
import serial
import struct
from datetime import datetime

class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=9600, uart_timeout=1):
        # LIN типично работи на 9600 бод в този случай
        self.SYNC_BYTE = 0x55
        self.PID_BYTE = 0x50
        self.BREAK_DURATION = 1.35e-3  # 1.35ms break signal
        self.RESPONSE_TIMEOUT = 0.1  # 100ms timeout for slave response
        
        # Конфигуриране на UART
        try:
            self.ser = serial.Serial(
                uart_port,
                uart_baudrate,
                timeout=uart_timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            print(f"[{datetime.now()}] [LINMaster] UART успешно инициализиран на порт {uart_port} с скорост {uart_baudrate} бод.")
        except Exception as e:
            print(f"[{datetime.now()}] [LINMaster] Грешка при инициализация на UART: {e}")
            self.ser = None

    def calculate_pid(self, identifier):
        """Изчислява PID с parity битове според LIN 2.x."""
        id_bits = identifier & 0x3F
        p0 = ((id_bits >> 0) & 1) ^ ((id_bits >> 1) & 1) ^ ((id_bits >> 2) & 1) ^ ((id_bits >> 4) & 1)
        p1 = ~(((id_bits >> 1) & 1) ^ ((id_bits >> 3) & 1) ^ ((id_bits >> 4) & 1) ^ ((id_bits >> 5) & 1)) & 1
        pid = id_bits | (p0 << 6) | (p1 << 7)
        print(f"[{datetime.now()}] [LINMaster] Изчислен PID: 0x{pid:02X} за идентификатор: 0x{identifier:02X}")
        return pid

    def calculate_checksum(self, data):
        """Enhanced Checksum според LIN 2.x."""
        checksum = sum(data) & 0xFF
        checksum = (~checksum) & 0xFF
        print(f"[{datetime.now()}] [LINMaster] Изчислен Checksum: 0x{checksum:02X} за данни: {data}")
        return checksum

    def send_break(self):
        """Изпраща LIN break field."""
        if self.ser:
            try:
                self.ser.break_condition = True
                time.sleep(self.BREAK_DURATION)
                self.ser.break_condition = False
                time.sleep(0.0001)  # Малка пауза след Break
                print(f"[{datetime.now()}] [LINMaster] Изпратен BREAK.")
            except Exception as e:
                print(f"[{datetime.now()}] [LINMaster] Грешка при изпращане на BREAK: {e}")
        else:
            print(f"[{datetime.now()}] [LINMaster] UART не е инициализиран. Не може да се изпрати BREAK.")

    def send_header(self, identifier):
        """Изпраща LIN header (Break + Sync + PID)."""
        if self.ser:
            try:
                self.send_break()
                pid = self.calculate_pid(identifier)
                self.ser.write(bytes([self.SYNC_BYTE]))
                print(f"[{datetime.now()}] [LINMaster] Изпратен SYNC байт: 0x{self.SYNC_BYTE:02X}")
                self.ser.write(bytes([pid]))
                print(f"[{datetime.now()}] [LINMaster] Изпратен PID байт: 0x{pid:02X}")
                return pid
            except Exception as e:
                print(f"[{datetime.now()}] [LINMaster] Грешка при изпращане на Header: {e}")
                return None
        else:
            print(f"[{datetime.now()}] [LINMaster] UART не е инициализиран. Не може да се изпрати Header.")
            return None

    def read_response(self, expected_length):
        """Чете отговор от slave с timeout."""
        if self.ser:
            try:
                start_time = time.time()
                response = bytearray()
                
                while (time.time() - start_time) < self.RESPONSE_TIMEOUT:
                    if self.ser.in_waiting:
                        byte = self.ser.read(1)
                        response.extend(byte)
                        if len(response) >= expected_length:
                            break

                # Премахване на null байтове, ако има
                filtered_response = bytearray(b for b in response if b != 0)
                print(f"[{datetime.now()}] [LINMaster] Прочетен отговор (hex): {filtered_response.hex()}")

                if len(filtered_response) == expected_length:
                    return filtered_response
                else:
                    print(f"[{datetime.now()}] [LINMaster] Не пълен или невалиден отговор. Очаквано: {expected_length} байта, Получено: {len(filtered_response)} байта.")
                    return None
            except Exception as e:
                print(f"[{datetime.now()}] [LINMaster] Грешка при четене на отговор: {e}")
                return None
        else:
            print(f"[{datetime.now()}] [LINMaster] UART не е инициализиран. Не може да се прочете отговор.")
            return None

    def send_request_frame(self, identifier):
        """Изпраща header и очаква отговор (Data + Checksum)."""
        if self.ser:
            pid = self.send_header(identifier)
            if pid is None:
                return None

            # Очакваме 3 байта: 2 за температура + 1 за checksum
            expected_length = 3
            response = self.read_response(expected_length)

            if response:
                data = response[:2]
                received_checksum = response[2]
                calculated_checksum = self.calculate_checksum([pid] + list(data))

                print(f"[{datetime.now()}] [LINMaster] Получени данни (hex): {data.hex()}, Получен Checksum: 0x{received_checksum:02X}, Изчислен Checksum: 0x{calculated_checksum:02X}")

                if received_checksum == calculated_checksum:
                    temperature = struct.unpack('<H', data)[0] / 100.0
                    print(f"[{datetime.now()}] [LINMaster] Валиден отговор. Температура: {temperature:.2f} °C")
                    return temperature
                else:
                    print(f"[{datetime.now()}] [LINMaster] Несъвпадение на Checksum!")
                    return None
            else:
                print(f"[{datetime.now()}] [LINMaster] Няма валиден отговор от slave.")
                return None
        else:
            print(f"[{datetime.now()}] [LINMaster] UART не е инициализиран. Не може да се изпрати Request Frame.")
            return None

    def send_data_frame(self, identifier, data_bytes):
        """Изпраща header (Break + Sync + PID) и след това данни + checksum."""
        if self.ser:
            try:
                pid = self.send_header(identifier)
                if pid is None:
                    return False

                csum = self.calculate_checksum([pid] + list(data_bytes))
                frame = data_bytes + bytes([csum])
                self.ser.write(frame)
                print(f"[{datetime.now()}] [LINMaster] Изпратен Data Frame (hex): {frame.hex()}")
                return True
            except Exception as e:
                print(f"[{datetime.now()}] [LINMaster] Грешка при изпращане на Data Frame: {e}")
                return False
        else:
            print(f"[{datetime.now()}] [LINMaster] UART не е инициализиран. Не може да се изпрати Data Frame.")
            return False

    def control_led(self, identifier, state):
        """Контролира LED на slave: ON или OFF."""
        command = 0x01 if state == "ON" else 0x00
        print(f"[{datetime.now()}] [LINMaster] Изпращане на команда за LED: {state}")
        success = self.send_data_frame(identifier, bytes([command]))
        if success:
            print(f"[{datetime.now()}] [LINMaster] Команда за LED {state} успешно изпратена.")
        else:
            print(f"[{datetime.now()}] [LINMaster] Неуспешно изпращане на команда за LED {state}.")
        return success

    def read_slave_temperature(self, identifier):
        """Изпраща заявка към slave за температура."""
        print(f"[{datetime.now()}] [LINMaster] Изпращане на заявка за температура към идентификатор: 0x{identifier:02X}")
        temperature = self.send_request_frame(identifier)
        if temperature is not None:
            print(f"[{datetime.now()}] [LINMaster] Четене на температура: {temperature:.2f} °C")
        else:
            print(f"[{datetime.now()}] [LINMaster] Неуспешно четене на температура.")
        return temperature
