FROM python:3.9-slim

# Инсталирайте системни зависимости преди pip пакетите
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-dev \
    build-essential \
    libgpiod2 \
    python3-libgpiod \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python пакети - със същия pattern като вашия работещ addon
RUN pip3 install --no-cache-dir \
    lgpio \
    gpiozero \
    rpi-lgpio

# Копиране на файловете
COPY pwm_HAOS.py /
COPY run.sh /

# Даване на права за стартиране
RUN chmod a+x /run.sh

CMD ["/run.sh"]
