# can_communication.py

import can
import asyncio
from logger_config import logger
from shared_data import latest_data

def init_can_interface(channel='can0', bustype='socketcan'):
    """
    Initializes the CAN interface using python-can.
    """
    try:
        bus = can.interface.Bus(channel=channel, bustype=bustype)
        logger.info(f"CAN interface initialized on channel {channel} with bustype {bustype}.")
        return bus
    except Exception as e:
        logger.error(f"CAN interface initialization error: {e}")
        return None

async def can_listener(bus):
    """
    Asynchronously listens for CAN messages.
    If a message is received, updates latest_data["can_status"] to "ON";
    if no message is received within the timeout, sets it to "OFF".
    """
    while True:
        try:
            # Wait up to 1 second for a message in a separate thread
            msg = await asyncio.to_thread(bus.recv, 1.0)
            if msg:
                logger.debug(f"Received CAN message: ID 0x{msg.arbitration_id:X}, data: {msg.data.hex()}")
                latest_data["can_status"] = "ON"
            else:
                latest_data["can_status"] = "OFF"
        except Exception as e:
            logger.error(f"Error receiving CAN message: {e}")
            latest_data["can_status"] = "OFF"
        await asyncio.sleep(0.1)
