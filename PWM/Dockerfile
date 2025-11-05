FROM python:3.11-slim

# Инсталиране на системни зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-dev \
    build-essential \
    libgpiod3 \
    python3-libgpiod \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Инсталиране на Python библиотеки
RUN pip install --no-cache-dir lgpio gpiozero

# Копиране на скриптовете
COPY pwm_HAOS.py /pwm_HAOS.py
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
