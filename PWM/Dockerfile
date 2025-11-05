ARG BUILD_FROM
FROM $BUILD_FROM

USER root

# Обновяване на системата и инсталация на зависимости
RUN \
    set -x \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    liblgpio1 \
    libgpiod2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir lgpio gpiozero

# Копиране на скрипта
COPY pwm_HAOS.py /app/
WORKDIR /app

# Право за стартиране
RUN chmod a+x /app/pwm_HAOS.py

# Стартиране
CMD ["python3", "/app/pwm_HAOS.py"]
