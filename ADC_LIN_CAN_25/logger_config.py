# logger_config.py

import logging

logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more verbose output if needed
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("ADC, LIN & MQTT")
logger.info("Logger initialized.")
