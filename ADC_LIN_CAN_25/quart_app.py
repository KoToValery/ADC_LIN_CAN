# quart_app.py
# Инициализация на Quart приложението и HTTP/WebSocket пътища.

import os
import json
import logging
from quart import Quart, jsonify, send_from_directory, websocket
from data_structures import latest_data
from logger_config import logger

# Създаваме Quart app
app = Quart(__name__)

# Подтискаме Quart логовете, ако искаме по-малко шум.
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

# Base directory за статични файлове
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Множество за WebSocket клиенти
clients = set()

@app.route('/data')
async def data_route():
    """
    Returns the latest sensor data in JSON format.
    """
    return jsonify(latest_data)

@app.route('/health')
async def health():
    """
    Health check endpoint. Returns HTTP 200 if the service is running.
    """
    return '', 200

@app.route('/')
async def index():
    """
    Serves the index.html file from the base directory.
    """
    try:
        return await send_from_directory(BASE_DIR, 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return jsonify({"error": "Index file not found."}), 404

@app.websocket('/ws')
async def ws_route():
    """
    WebSocket endpoint for real-time data updates.
    """
    logger.info("New WebSocket connection established.")
    clients.add(websocket._get_current_object())
    try:
        while True:
            # Keep the connection open by waiting for incoming messages
            await websocket.receive()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")
