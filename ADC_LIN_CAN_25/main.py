# main.py

import asyncio
from hypercorn.asyncio import serve
from hypercorn.config import Config

# Импортираме конфигурации и логер
from config import HTTP_PORT
from logger_config import logger

# Импортираме Quart app и задачите
from quart_app import app
from tasks import adc_loop, lin_loop, mqtt_loop_task, websocket_loop


async def main():
    """
    Стартира Quart HTTP сървъра и всички асинхронни задачи.
    """
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


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
