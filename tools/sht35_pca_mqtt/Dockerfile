ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 \
    libgpiod \
    py3-pip

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir smbus2 paho-mqtt

COPY run.sh /run.sh
COPY app.py /app.py
RUN chmod +x /run.sh

CMD [ "/run.sh" ]
