# main.py
# ============================
# Главна входна точка + Конфигурации
# ============================

import asyncio
import logging
from hypercorn.asyncio import serve
from hypercorn.config import Config

# Импортираме Quart app и асинхронните задачи
from quart_app import app
from tasks import adc_loop, lin_loop, mqtt_loop_task, websocket_loop

# ============================
# Конфигурационни Параметри
# ============================

HTTP_PORT = 8099

SPI_BUS = 1
SPI_DEVICE = 1
SPI_SPEED_HZ = 1_000_000
SPI_MODE = 0

VREF = 3.3
ADC_RESOLUTION = 1023.0
VOLTAGE_MULTIPLIER = 3.31
RESISTANCE_REFERENCE = 10_000  # Ohms

ADC_INTERVAL = 0.1   # период на четене на ADC
LIN_INTERVAL = 2     # период на LIN комуникация
MQTT_INTERVAL = 1    # период на публикуване в MQTT
WS_INTERVAL = 1      # период на WebSocket обновяване

VOLTAGE_THRESHOLD = 0.02

UART_PORT = '/dev/ttyAMA2'
UART_BAUDRATE = 9600

MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = 'mqtt'
MQTT_PASSWORD = 'mqtt_pass'
MQTT_DISCOVERY_PREFIX = 'homeassistant'
MQTT_CLIENT_ID = "cis3_adc_mqtt_client"

SUPERVISOR_TOKEN = None  # или четене с os.getenv("SUPERVISOR_TOKEN")
INGRESS_PATH = ''

# LIN Константи
SYNC_BYTE = 0x55
BREAK_DURATION = 1.35e-3
PID_TEMPERATURE = 0x50
PID_HUMIDITY = 0x51
PID_DICT = {
    PID_TEMPERATURE: 'Temperature',
    PID_HUMIDITY: 'Humidity'
}

# ============================
# Основна async функция
# ============================

async def main():
    """
    Стартира Quart HTTP сървъра и всички асинхронни задачи.
    """
    from logger_config import logger  # Импортираме логера тук
    config = Config()
    config.bind = [f"0.0.0.0:{HTTP_PORT}"]

    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")

    # Създаваме асинхронни задачи
    quart_task = asyncio.create_task(serve(app, config))
    adc_task = asyncio.create_task(adc_loop())
    lin_task = asyncio.create_task(lin_loop())
    mqtt_task = asyncio.create_task(mqtt_loop_task())
    ws_task = asyncio.create_task(websocket_loop())

    logger.info("Quart HTTP server started.")
    logger.info("ADC processing task started.")
    logger.info("LIN communication task started.")
    logger.info("MQTT publishing task started.")
    logger.info("WebSocket broadcasting task started.")

    # Изпълняваме ги едновременно
    await asyncio.gather(quart_task, adc_task, lin_task, mqtt_task, ws_task)

# ============================
# Точка на стартиране
# ============================

if __name__ == '__main__':
    from logger_config import logger
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        try:
            from tasks import spi, ser, mqtt_client
            spi.close()
            ser.close()
            mqtt_client.publish("cis3/status", "offline", retain=True)
            mqtt_client.disconnect()
            logger.info("ADC, LIN & MQTT Add-on has been shut down.")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
