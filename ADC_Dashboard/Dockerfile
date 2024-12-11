FROM python:3.9-slim

# 
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    build-essential \
    supervisor \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 
RUN pip3 install --no-cache-dir \
    flask \
    flask-sock \
    websockets \
    websocket-client \
    spidev \
    pyserial \
    paho-mqtt
 
# 
COPY data_reader.py /

CMD ["python3", "/data_reader.py"]
