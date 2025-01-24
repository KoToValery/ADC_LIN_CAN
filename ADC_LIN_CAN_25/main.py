# main.py
# Главна входна точка на приложението: стартиране на HTTP сървър, асинхронни задачи и т.н.

import asyncio
import logging
from hypercorn.asyncio import serve
from hypercorn.config import Config

from logger_config import logger
from quart_app import app
from tasks import adc_loop, lin_loop, mqtt_loop_task, websocket_loop

async def main():
    """
    Initializes and starts the Quart HTTP server and all asynchronous tasks.
    """
    from py_configuration import HTTP_PORT

    py_configuration = Config()
    py_configuration.bind = [f"0.0.0.0:{HTTP_PORT}"]  # Bind to all interfaces on specified port
    logger.info(f"Starting Quart HTTP server on port {HTTP_PORT}")
    
    # Създаваме асинхронни задачи
    quart_task = asyncio.create_task(serve(app, config))
    adc_task = asyncio.create_task(adc_loop())
    lin_task = asyncio.create_task(lin_loop())
    mqtt_task = asyncio.create_task(mqtt_loop_task())
    ws_task = asyncio.create_task(websocket_loop())

    # Log the start of each task
    logger.info("Quart HTTP server started.")
    logger.info("ADC processing task started.")
    logger.info("LIN communication task started.")
    logger.info("MQTT publishing task started.")
    logger.info("WebSocket broadcasting task started.")

    # Run all tasks concurrently
    await asyncio.gather(quart_task, adc_task, lin_task, mqtt_task, ws_task)

if __name__ == '__main__':
    try:
        # Run the main asynchronous function
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        from spi_adc import spi
        from lin_communication import ser
        from mqtt_integration import mqtt_client

        # Clean up resources on shutdown
        try:
            spi.close()
            ser.close()
            mqtt_client.publish("cis3/status", "offline", retain=True)
            mqtt_client.disconnect()
            logger.info("ADC, LIN & MQTT Add-on has been shut down.")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
