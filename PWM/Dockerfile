FROM python:3.11-slim

# Инсталиране на системни зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Инсталиране на pigpio Python библиотека
RUN pip install --no-cache-dir pigpio

# Копиране на скриптовете
COPY pwm_HAOS.py /pwm_HAOS.py
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
