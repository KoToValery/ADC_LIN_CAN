# quart_app.py
# Quart приложение: HTTP, WebSocket, endpoints

import os
import json
import logging
from quart import Quart, jsonify, send_from_directory, websocket
from logger_config import logger

# Създаваме Quart app
app = Quart(__name__)

# Подтискаме подробните логове на Quart
quart_log = logging.getLogger('quart.app')
quart_log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Множество за WebSocket клиенти
clients = set()

@app.route('/data')
async def data_route():
    from tasks import latest_data  # Импорт на данни от tasks
    return jsonify(latest_data)

@app.route('/health')
async def health():
    return '', 200

@app.route('/')
async def index():
    """
    Сервира index.html, ако съществува в директорията.
    """
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
            # Държим връзката отворена - чакаме съобщения
            await websocket.receive()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        clients.remove(websocket._get_current_object())
        logger.info("WebSocket connection closed.")
