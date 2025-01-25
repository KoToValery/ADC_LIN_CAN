# adc_app.py
# Това е главният "стартов" файл, който обединява всичко.

import asyncio
import time
from logger_config import logger
from config import (HTTP_PORT, ADC_INTERVAL, LIN_INTERVAL, MQTT_INTERVAL, WS_INTERVAL)
from adc_manager import ADCManager
from lin_communication import LinCommunication
from mqtt_manager import MqttManager
from webserver import run_quart_server, broadcast_via_websocket
from shared_data import latest_data

# ============================
# Инициализация на Мениджърите
# ============================
adc_manager = ADCManager()
lin_comm = LinCommunication()
mqtt_manager = MqttManager()

# Стартираме MQTT в отделна нишка
mqtt_manager.start()

# ============================
# Асинхронни функции за периодични задачи
# ============================

async def adc_loop():
    """
    Периодично четене/филтриране на ADC канали.
    """
    while True:
        adc_manager.process_all_adc_channels()
        await asyncio.sleep(ADC_INTERVAL)

async def lin_loop():
    """
    Периодично извикване на LIN комуникацията (темп, влажност).
    """
    while True:
        await lin_comm.process_lin_communication()
        await asyncio.sleep(LIN_INTERVAL)

async def mqtt_loop_task():
    """
    Периодично публикуване към MQTT (ADC данни, данни от слейва).
    """
    while True:
        mqtt_manager.publish_to_mqtt()
        await asyncio.sleep(MQTT_INTERVAL)

async def websocket_loop():
    """
    Периодично пращане на данни към WebSocket клиенти.
    """
    while True:
        await broadcast_via_websocket()
        await asyncio.sleep(WS_INTERVAL)

# ============================
# Главна функция
# ============================

async def main():
    # Създаваме асинхронни задачи
    quart_task = asyncio.create_task(run_quart_server(HTTP_PORT))
    adc_task = asyncio.create_task(adc_loop())
    lin_task = asyncio.create_task(lin_loop())
    mqtt_task = asyncio.create_task(mqtt_loop_task())
    ws_task = asyncio.create_task(websocket_loop())

    logger.info("All tasks have been started. (ADC, LIN, MQTT, WebServer, WebSocket)")

    # Изпълняваме ги успоредно
    await asyncio.gather(quart_task, adc_task, lin_task, mqtt_task, ws_task)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down ADC, LIN & MQTT Add-on...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        # Clean-up
        try:
            adc_manager.close()
            lin_comm.close()
            mqtt_manager.client.publish("cis3/status", "offline", retain=True)
            mqtt_manager.client.disconnect()
            logger.info("ADC, LIN & MQTT Add-on has been shut down.")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
