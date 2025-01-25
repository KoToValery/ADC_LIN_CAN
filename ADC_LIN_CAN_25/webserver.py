# webserver.py

import os
import json
import logging
import asyncio
from quart import Quart, jsonify, send_from_directory, websocket
from hypercorn.asyncio import serve
from hypercorn.config import Config

from logger_config import logger
from shared_data import latest_data

quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Quart(__name__)
clients = set()

@app.route('/data')
async def data_route():
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

@app.route('/')
async def index():
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            await websocket.receive()  # Изчакваме съобщения от клиента
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")

async def broadcast_via_websocket():
    """
    Праща последните данни на всички WS клиенти.
    """
    if clients:
        data_to_send = json.dumps(latest_data)
        await asyncio.gather(*(client.send(data_to_send) for client in clients))
        logger.debug("Sent updated data to WebSocket clients.")

async def run_quart_server(http_port):
    """
    Стартира Quart HTTP сървъра.
    """
    config = Config()
    config.bind = [f"0.0.0.0:{http_port}"]
    logger.info(f"Starting Quart HTTP server on port {http_port}")
    await serve(app, config)
