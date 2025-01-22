# data_structures.py
# Глобални структури от данни и буфери

from collections import deque

# ============================
# Data Structures
# ============================

latest_data = {
    "adc_channels": {
        "channel_0": {"voltage": 0.0, "unit": "V"},
        "channel_1": {"voltage": 0.0, "unit": "V"},
        "channel_2": {"voltage": 0.0, "unit": "V"},
        "channel_3": {"voltage": 0.0, "unit": "V"},
        "channel_4": {"resistance": 0.0, "unit": "Ω"},
        "channel_5": {"resistance": 0.0, "unit": "Ω"}
    },
    "slave_sensors": {
        "slave_1": {
            "Temperature": 0.0,
            "Humidity": 0.0
        }
    }
}

# Инициализираме буфери за Moving Average (MA)
voltage_buffers = {ch: deque(maxlen=20) for ch in range(4)}       # Channels 0-3: Voltage
resistance_buffers = {ch: deque(maxlen=30) for ch in range(4,6)} # Channels 4-5: Resistance

# Initialize EMA values for each channel
ema_values = {ch: None for ch in range(6)}
