import os
import time
import struct
import serial

class LINMaster:
    def __init__(self, uart_port='/dev/ttyAMA2', uart_baudrate=9600, uart_timeout=1):
        self.LIN_SYNC_BYTE = 0x55
        self.LED_ON_COMMAND = 0x01
        self.LED_OFF_COMMAND = 0x00

        try:
            self.ser = serial.Serial(
                uart_port, 
                uart_baudrate, 
                timeout=uart_timeout, 
                parity=serial.PARITY_NONE, 
                stopbits=serial.STOPBITS_ONE
            )
        except Exception as e:
            print(f"UART initialization error: {e}")
            self.ser = None

    def calculate_pid(self, identifier):
        """Calculate PID with parity bits for LIN 2.x."""
        id_bits = identifier & 0x3F  # First 6 bits
        p0 = (id_bits & 0x01) ^ ((id_bits >> 1) & 0x01) ^ ((id_bits >> 2) & 0x01) ^ ((id_bits >> 4) & 0x01)
        p1 = ~(((id_bits >> 1) & 0x01) ^ ((id_bits >> 3) & 0x01) ^ ((id_bits >> 4) & 0x01) ^ ((id_bits >> 5) & 0x01)) & 0x01
        return (id_bits | (p0 << 6) | (p1 << 7))

    def calculate_checksum(self, data):
        """Calculate Enhanced LIN checksum."""
        checksum = sum(data) & 0xFF  # Sum all data bytes
        return (~checksum) & 0xFF  # Invert the result

    def send_lin_command(self, identifier, command):
        """Send a command frame to a LIN slave."""
        if self.ser:
            try:
                pid = self.calculate_pid(identifier)
                checksum = self.calculate_checksum([pid, command])
                frame = struct.pack('BBBB', self.LIN_SYNC_BYTE, pid, command, checksum)
                self.ser.write(frame)
                return True
            except Exception as e:
                print(f"LIN communication error: {e}")
        return False

    def read_slave_temperature(self, identifier):
        """Request temperature reading from the slave."""
        if self.ser:
            try:
                pid = self.calculate_pid(identifier)
                checksum = self.calculate_checksum([pid])
                frame = struct.pack('BBB', self.LIN_SYNC_BYTE, pid, checksum)
                self.ser.write(frame)
                time.sleep(0.1)  # Wait for slave response
                if self.ser.in_waiting >= 5:  # Expect 4 bytes of data + checksum
                    response = self.ser.read(5)
                    temperature, checksum_received = struct.unpack('<fB', response)
                    expected_checksum = self.calculate_checksum(response[:4])
                    if checksum_received == expected_checksum:
                        return round(temperature, 2)
                    else:
                        print("Checksum mismatch!")
            except Exception as e:
                print(f"Temperature reading error: {e}")
        return None

    def control_led(self, identifier, state):
        """Control the LED on the slave."""
        command = self.LED_ON_COMMAND if state == "ON" else self.LED_OFF_COMMAND
        return self.send_lin_command(identifier, command)

if __name__ == "__main__":
    master = LINMaster()
    slave_id = 0x10

    # Test LED control
    master.control_led(slave_id, "ON")
    time.sleep(2)
    master.control_led(slave_id, "OFF")

    # Test temperature reading
    temp = master.read_slave_temperature(slave_id)
    if temp is not None:
        print(f"Temperature from slave: {temp}°C")
    else:
        print("Failed to read temperature from slave.")
