FROM python:3.9-slim

# Install necessary system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install --no-cache-dir \
    quart \
    websockets \
    websocket-client \
    spidev \
    pyserial \
    pyserial-asyncio \
    aiofiles \
    hypercorn    
RUN pip3 install --no-cache-dir asyncio_mqtt

# Copy application files into the container
COPY adc_app.py /
COPY index.html /

# Define the command to run the application
CMD ["python3", "/adc_app.py"]
