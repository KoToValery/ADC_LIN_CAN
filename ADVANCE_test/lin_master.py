import os
import time
import struct
import serial

class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=19200, uart_timeout=1):
        # LIN типично работи на 19200 бод.
        self.LIN_SYNC_BYTE = 0x55
        self.LED_ON_COMMAND = 0x01
        self.LED_OFF_COMMAND = 0x00

        try:
            self.ser = serial.Serial(
                uart_port, 
                uart_baudrate, 
                timeout=uart_timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
        except Exception as e:
            print(f"UART initialization error: {e}")
            self.ser = None

    def calculate_pid(self, identifier):
        """Изчислява PID с parity битове според LIN 2.x.
        ID е 6 бита: ID0-ID5.
        P0 = ID0 ^ ID1 ^ ID2 ^ ID4
        P1 = ~(ID1 ^ ID3 ^ ID4 ^ ID5)
        """
        id_bits = identifier & 0x3F
        p0 = ((id_bits >> 0) & 1) ^ ((id_bits >> 1) & 1) ^ ((id_bits >> 2) & 1) ^ ((id_bits >> 4) & 1)
        p1 = ~(((id_bits >> 1) & 1) ^ ((id_bits >> 3) & 1) ^ ((id_bits >> 4) & 1) ^ ((id_bits >> 5) & 1)) & 1
        pid = id_bits | (p0 << 6) | (p1 << 7)
        return pid

    def calculate_checksum(self, data):
        """
        Enhanced Checksum според LIN 2.x:
        Включва PID в сметката. Проверява се цялост на данните.
        """
        checksum = 0
        for b in data:
            checksum += b
            if checksum > 0xFF:
                checksum -= 0xFF
        checksum = (~checksum) & 0xFF
        return checksum

    def send_header(self, identifier):
        """Изпраща стандартен LIN header: Break, Sync(0x55), PID."""
        if self.ser:
            # Изпращане на Break (минимум 13 бит време на low)
            self.ser.send_break()  
            time.sleep(0.001) # Кратка пауза, ако е необходимо

            pid = self.calculate_pid(identifier)
            header = bytes([self.LIN_SYNC_BYTE, pid])
            self.ser.write(header)
            return pid
        return None

    def send_request_frame(self, identifier):
        """Изпраща header и очаква отговор. (Data + Checksum)"""
        if self.ser:
            pid = self.send_header(identifier)
            if pid is None:
                return None
            # Изчакваме slave да отговори
            time.sleep(0.01)
            # Прочитаме например 5 байта: 4 за данни + 1 за чексум (пример)
            # Трябва да е по спецификация. Ако знаете колко байта очаквате - коригирайте.
            frame_len = 5
            response = self.ser.read(frame_len)
            if len(response) == frame_len:
                data = response[:-1]
                csum = response[-1]
                calc_csum = self.calculate_checksum([pid] + list(data))
                if csum == calc_csum:
                    # Данните са валидни
                    # Пример: ако са 4 байта float
                    try:
                        temperature = struct.unpack('<f', data)[0]
                        return temperature
                    except:
                        return None
                else:
                    print("Checksum mismatch in response!")
                    return None
            else:
                print("No valid response from slave.")
        return None

    def send_data_frame(self, identifier, data_bytes):
        """Изпраща header (break+sync+pid) и след това данни + checksum."""
        if self.ser:
            pid = self.send_header(identifier)
            if pid is None:
                return False
            # Изчисляване на checksum върху PID + данните
            csum = self.calculate_checksum([pid] + list(data_bytes))
            frame = data_bytes + bytes([csum])
            self.ser.write(frame)
            return True
        return False

    def control_led(self, identifier, state):
        """Контролира LED на slave: ON или OFF."""
        command = self.LED_ON_COMMAND if state == "ON" else self.LED_OFF_COMMAND
        return self.send_data_frame(identifier, bytes([command]))

    def read_slave_temperature(self, identifier):
        """Изпраща заявка към slave за температура."""
        # Предполагаме, че slave при заявка без данни в payload 
        # просто връща отговор (температура).
        # Тук изпращаме само header и чакаме отговор.
        if self.ser:
            # Вместо да пращаме данни, просто header (request frame)
            return self.send_request_frame(identifier)
        return None


if __name__ == "__main__":
    master = LINMaster()
    slave_id = 0x10

    # Test LED control
    master.control_led(slave_id, "ON")
    time.sleep(1)
    master.control_led(slave_id, "OFF")

    # Test temperature reading
    temp = master.read_slave_temperature(slave_id)
    if temp is not None:
        print(f"Temperature from slave: {temp}°C")
    else:
        print("Failed to read temperature from slave.")
