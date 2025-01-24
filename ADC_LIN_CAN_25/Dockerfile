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
COPY main.py /
COPY index.html /
COPY py_configuration.py /
COPY data_structures.py /
COPY lin_communication.py /
COPY logger_config.py /
COPY mqtt_integration.py /
COPY quart_app.py /
COPY spi_adc.py /
COPY tasks.py /

# Define the command to run the application
CMD ["python3", "/main.py"]
