# Използваме образ, базиран на Raspberry Pi OS (с поддръжка за lgpio)
FROM lsiobase/raspbian:bookworm

# Инсталираме зависимости
RUN apt-get update && \
    apt-get install -y python3 python3-pip liblgpio1 && \
    pip3 install lgpio gpiozero && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Копираме скрипта
COPY pwm_HAOS.py /app/
WORKDIR /app

# Стартираме скрипта
CMD ["python3", "pwm_HAOS.py"]
