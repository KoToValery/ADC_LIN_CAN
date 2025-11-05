# syntax=docker/dockerfile:1
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

USER root

RUN set -x \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
    python3 python3-pip liblgpio1 libgpiod2 \
 && rm -rf /var/lib/apt/lists/* \
 && pip3 install --no-cache-dir lgpio gpiozero

COPY run.sh /
COPY pwm_HAOS.py /app/
RUN chmod a+x /run.sh
WORKDIR /app
CMD ["/run.sh"]
