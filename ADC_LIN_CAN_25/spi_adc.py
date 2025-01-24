# spi_adc.py
# Работа с SPI (ADC), включително инициализация и функции за четене.

import spidev
import logging

from logger_config import logger
from config import SPI_BUS, SPI_DEVICE, SPI_SPEED_HZ, SPI_MODE, VREF, ADC_RESOLUTION, VOLTAGE_MULTIPLIER, RESISTANCE_REFERENCE

# Инициализираме SPI
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED_HZ
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

def read_adc(channel):
    """
    Reads the raw ADC value from a specific channel using SPI.
    
    Args:
        channel (int): ADC channel number (0-7).
    
    Returns:
        int: Raw ADC value.
    """
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]  # Command format for MCP3008
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]  # Combine bytes to get raw value
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage_from_raw(raw_value):
    """
    Converts a raw ADC value to voltage.
    
    Args:
        raw_value (int): Raw ADC value.
    
    Returns:
        float: Calculated voltage in volts.
    """
    return (raw_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance_from_raw(raw_value):
    """
    Converts a raw ADC value to resistance.
    
    Args:
        raw_value (int): Raw ADC value.
    
    Returns:
        float: Calculated resistance in ohms.
    """
    if raw_value == 0:
       
        return 0.0
    return ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - raw_value)) / raw_value) / 10
