FROM python:3.9-slim


RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    build-essential \
    supervisor \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python 
RUN pip3 install --no-cache-dir \
    flask \
    flask-sock \
    websockets \
    websocket-client \
    spidev \
    pyserial 



COPY adc_app.py /
COPY lin_master.py /
COPY lin_slave.py /
COPY flask_app.py /
COPY index.html /


CMD ["python3", "/adc_app.py"]
