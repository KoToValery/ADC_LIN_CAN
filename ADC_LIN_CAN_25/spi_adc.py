# spi_adc.py
# Работа със SPI (MCP3008 ADC)

import spidev
from logger_config import logger
from main import (
    SPI_BUS, SPI_DEVICE, SPI_SPEED_HZ, SPI_MODE,
    VREF, ADC_RESOLUTION, VOLTAGE_MULTIPLIER, RESISTANCE_REFERENCE
)

# Инициализация на SPI
spi = spidev.SpiDev()
try:
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED_HZ
    spi.mode = SPI_MODE
    logger.info("SPI interface for ADC initialized.")
except Exception as e:
    logger.error(f"SPI initialization error: {e}")

def read_adc(channel: int) -> int:
    """Чете сурова стойност от MCP3008 ADC."""
    if 0 <= channel <= 7:
        cmd = [1, (8 + channel) << 4, 0]
        try:
            adc = spi.xfer2(cmd)
            value = ((adc[1] & 3) << 8) + adc[2]
            return value
        except Exception as e:
            logger.error(f"Error reading ADC channel {channel}: {e}")
            return 0
    logger.warning(f"Invalid ADC channel: {channel}")
    return 0

def calculate_voltage_from_raw(raw_value: int) -> float:
    """Преобразува сурова ADC стойност в напрежение [V]."""
    return (raw_value / ADC_RESOLUTION) * VREF * VOLTAGE_MULTIPLIER

def calculate_resistance_from_raw(raw_value: int) -> float:
    """Преобразува сурова ADC стойност в съпротивление [Ω]."""
    if raw_value == 0:
        logger.warning("Raw ADC value is zero. Cannot calculate resistance.")
        return 0.0
    return ((RESISTANCE_REFERENCE * (ADC_RESOLUTION - raw_value)) / raw_value) / 10
