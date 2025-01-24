# logger_config.py
# Конфигурация на логването в файл и на конзола.

import logging

logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more verbose output
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),  # Log to file
        logging.StreamHandler()              # Log to console
    ]
)

logger = logging.getLogger('ADC, LIN & MQTT')
logger.info("ADC, LIN & MQTT Add-on started.")
