# adc_manager.py

import spidev
import logging
from collections import deque

from logger_config import logger
from config import (SPI_BUS, SPI_DEVICE, SPI_SPEED_HZ, SPI_MODE, 
                    ADC_RESOLUTION, VREF, VOLTAGE_MULTIPLIER, 
                    RESISTANCE_REFERENCE, VOLTAGE_THRESHOLD)
from shared_data import latest_data

# За Moving Average и Exponential Moving Average
voltage_buffers = {ch: deque(maxlen=20) for ch in range(4)}      # Channels 0-3: Voltage
resistance_buffers = {ch: deque(maxlen=30) for ch in range(4, 6)}# Channels 4-5: Resistance
ema_values = {ch: None for ch in range(6)}                       # EMA стойности за всички канали

class ADCManager:
    def __init__(self):
        self.spi = spidev.SpiDev()
        try:
            self.spi.open(SPI_BUS, SPI_DEVICE)
            self.spi.max_speed_hz = SPI_SPEED_HZ
            self.spi.mode = SPI_MODE
            logger.info("SPI interface for ADC initialized.")
        except Exception as e:
            logger.error(f"SPI initialization error: {e}")

    def read_adc(self, channel):
        """
        Чете raw стойност от конкретен ADC канал (0-7) през MCP3008.
        """
        if 0 <= channel <= 7:
            cmd = [1, (8 + channel) << 4, 0]  # MCP3008 формат
            try:
                adc = self.spi.xfer2(cmd)
                value = ((adc[1] & 3) << 8) + adc[2]
                return value
            except Exception as e:
                logger.error(f"Error reading ADC channel {channel}: {e}")
                return 0
        else:
            logger.warning(f"Invalid ADC channel: {channel}")
            return 0

    def calculate_voltage_from_raw(self, raw_value):
        return (raw_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

    def calculate_resistance_from_raw(self, raw_value):
        if raw_value == 0:
            return 0.0
        return ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - raw_value)) / raw_value) / 10

    def process_all_adc_channels(self):
        """
        Чете и филтрира всички канали (0-3 волтаж, 4-5 резист).
        Обновява latest_data.
        """
        raw_values = [self.read_adc(ch) for ch in range(6)]

        # Обработка на канали 0-3 (волтаж)
        for ch in range(4):
            voltage = self.calculate_voltage_from_raw(raw_values[ch])
            voltage_buffers[ch].append(voltage)

            # Moving Average (MA)
            ma_voltage = sum(voltage_buffers[ch]) / len(voltage_buffers[ch])

            # Exponential Moving Average (EMA)
            alpha = 0.2
            if ema_values[ch] is None:
                ema_values[ch] = ma_voltage
            else:
                ema_values[ch] = alpha * ma_voltage + (1 - alpha) * ema_values[ch]

            # Минимален праг на напрежение
            if ema_values[ch] < VOLTAGE_THRESHOLD:
                ema_values[ch] = 0.0

            latest_data["adc_channels"][f"channel_{ch}"]["voltage"] = round(ema_values[ch], 2)
            logger.debug(f"Channel {ch} Voltage: {latest_data['adc_channels'][f'channel_{ch}']['voltage']} V")

        # Обработка на канали 4-5 (резист)
        for ch in range(4, 6):
            resistance = self.calculate_resistance_from_raw(raw_values[ch])
            resistance_buffers[ch].append(resistance)

            # Moving Average (MA)
            ma_resistance = sum(resistance_buffers[ch]) / len(resistance_buffers[ch])

            alpha = 0.1
            if ema_values[ch] is None:
                ema_values[ch] = ma_resistance
            else:
                ema_values[ch] = alpha * ma_resistance + (1 - alpha) * ema_values[ch]

            latest_data["adc_channels"][f"channel_{ch}"]["resistance"] = round(ema_values[ch], 2)
            logger.debug(f"Channel {ch} Resistance: {latest_data['adc_channels'][f'channel_{ch}']['resistance']} Ω")

    def close(self):
        try:
            self.spi.close()
        except Exception as e:
            logger.error(f"Error closing SPI: {e}")
