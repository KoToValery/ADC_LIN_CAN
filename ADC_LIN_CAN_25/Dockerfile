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
    hypercorn \
    spidev \
    pyserial \
    aiomqtt \
    aiofiles

# Copy application files into the container
COPY adc_app.py /
COPY index.html /
COPY config.py /
COPY shared_data.py /
COPY lin_communication.py /
COPY logger_config.py /
COPY mqtt_manager.py /
COPY webserver.py /
COPY adc_manager.py /
COPY tasks.py /

# Define the command to run the application
CMD ["python3", "/adc_app.py"]
