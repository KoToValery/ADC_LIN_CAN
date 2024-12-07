FROM python:3.9-slim

# Инсталиране на необходимите системни зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Инсталиране на Python пакетите
RUN pip3 install --no-cache-dir \
    quart \
    websockets \
    websocket-client \
    spidev \
    pyserial \
    hypercorn 

# Копиране на приложните файлове в контейнера
COPY adc_app.py /
COPY index.html /

# Определяне на командата за стартиране на приложението
CMD ["python3", "/adc_app.py"]
