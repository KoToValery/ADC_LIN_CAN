# logger_config.py
# Конфигурация на логовете

import logging

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("adc_app.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('ADC, LIN & MQTT')
logger.info("ADC, LIN & MQTT Add-on started.")
