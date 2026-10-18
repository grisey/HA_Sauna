import time
import json
import socket
import subprocess
import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4
from typing import List, Tuple

import paho.mqtt.client as mqtt
from smbus2 import SMBus, i2c_msg


HARD_CODED_CHANNELS: List[int] = [3, 6]

logger = logging.getLogger("sht35_pca")


def sample_payload(base_topic, channel, boot_id, sample_id, measured_at,
                   monotonic_ns, temperature=None, humidity=None):
    """One physical read; identity is (boot_id, sample_id), shared by T/RH.

    measured_at is UTC at completion of the CRC-checked read, not MQTT/HA
    reception time. Error attempts have no measured_at and no measured values.
    monotonic_ns is captured at completion of the attempt; it survives wall
    clock corrections but is meaningful only within the same boot_id.
    """
    successful = measured_at is not None
    return {
        "schema_version": 1,
        "sensor_id": f"{base_topic}/ch{channel}",
        "boot_id": boot_id,
        "sample_id": sample_id,
        "measured_at": measured_at,
        "monotonic_ns": monotonic_ns,
        "status": "ok" if successful else "read_error",
        "temperature": float(f"{temperature:.2f}") if successful else None,
        "humidity": float(f"{humidity:.2f}") if successful else None,
    }


def configure_logging(debug_log: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug_log else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def pca_read_control(bus: SMBus, pca_addr: int) -> int:
    return int(bus.read_byte(pca_addr))



def crc8(data: bytes) -> int:
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) & 0xFF) ^ 0x31
            else:
                crc = (crc << 1) & 0xFF
    return crc


def pca_select_channel(bus: SMBus, pca_addr: int, channel: int) -> None:
    if channel < 0 or channel > 7:
        raise ValueError("PCA9548A channel must be 0..7")
    bus.i2c_rdwr(i2c_msg.write(pca_addr, [1 << channel]))
    time.sleep(0.002)


def sht35_read(bus: SMBus, addr: int) -> Tuple[float, float]:
    bus.i2c_rdwr(i2c_msg.write(addr, [0x2C, 0x06]))
    time.sleep(0.020)

    r = i2c_msg.read(addr, 6)
    bus.i2c_rdwr(r)
    raw = bytes(r)

    t_bytes, t_crc = raw[0:2], raw[2]
    rh_bytes, rh_crc = raw[3:5], raw[5]

    if crc8(t_bytes) != t_crc or crc8(rh_bytes) != rh_crc:
        raise RuntimeError(f"CRC mismatch: {raw.hex()}")

    t_raw = (t_bytes[0] << 8) | t_bytes[1]
    rh_raw = (rh_bytes[0] << 8) | rh_bytes[1]

    temperature = -45.0 + (175.0 * (t_raw / 65535.0))
    humidity = 100.0 * (rh_raw / 65535.0)
    return temperature, humidity


def publish_discovery_sensor(
    client: mqtt.Client,
    disc_prefix: str,
    base_topic: str,
    dev: dict,
    ch: int,
    publish_status: bool,
) -> None:
    uid_temp = f"sht35_pca_ch{ch}_temperature"
    uid_rh = f"sht35_pca_ch{ch}_humidity"
    uid_stat = f"sht35_pca_ch{ch}_status"

    avail = f"{base_topic}/availability"

    def pub(domain: str, object_id: str, payload: dict) -> None:
        topic = f"{disc_prefix}/{domain}/{object_id}/config"
        client.publish(topic, json.dumps(payload), retain=True)

    pub(
        "sensor",
        uid_temp,
        {
            "name": f"SHT35 Kanal {ch} Temperatur",
            "unique_id": uid_temp,
            "state_topic": f"{base_topic}/ch{ch}/temperature",
            "availability_topic": avail,
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            "state_class": "measurement",
            "device": dev,
        },
    )

    pub(
        "sensor",
        uid_rh,
        {
            "name": f"SHT35 Kanal {ch} Luftfeuchte",
            "unique_id": uid_rh,
            "state_topic": f"{base_topic}/ch{ch}/humidity",
            "availability_topic": avail,
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "state_class": "measurement",
            "device": dev,
        },
    )

    if publish_status:
        pub(
            "sensor",
            uid_stat,
            {
                "name": f"SHT35 Kanal {ch} Status",
                "unique_id": uid_stat,
                "state_topic": f"{base_topic}/ch{ch}/status",
                "availability_topic": avail,
                "icon": "mdi:stethoscope",
                "device": dev,
            },
        )


def publish_reset_button(
    client: mqtt.Client,
    disc_prefix: str,
    base_topic: str,
    dev: dict,
) -> None:
    uid_btn = "sht35_pca_pca_reset"
    avail = f"{base_topic}/availability"

    client.publish(
        f"{disc_prefix}/button/{uid_btn}/config",
        json.dumps(
            {
                "name": "PCA9548A Reset",
                "unique_id": uid_btn,
                "command_topic": f"{base_topic}/pca/reset/set",
                "payload_press": "PRESS",
                "availability_topic": avail,
                "icon": "mdi:restart",
                "device": dev,
            }
        ),
        retain=True,
    )


def _run(cmd: List[str], timeout_s: float) -> str:
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)} | rc={r.returncode} | stderr={r.stderr.strip()}"
        )
    return (r.stdout or "").strip()


def do_gpio_reset(gpiochip: str, gpioline: int, pulse_ms: int) -> None:
    ms = max(1, int(pulse_ms))
    logger.info(
        "Triggering PCA reset via %s line %s with pulse %sms",
        gpiochip,
        gpioline,
        ms,
    )
    _run(
        ["gpioset", "-c", str(gpiochip), "--toggle", f"{ms}ms,0", f"{int(gpioline)}=0"],
        timeout_s=max(2.0, (ms / 1000.0) + 1.0),
    )


def main() -> None:
    with open("/data/options.json", "r") as f:
        opt = json.load(f)

    debug_log = bool(opt.get("debug_log", False))
    configure_logging(debug_log)

    mqtt_host = opt.get("mqtt_host", "core-mosquitto")
    mqtt_port = int(opt.get("mqtt_port", 1883))
    mqtt_user = (opt.get("mqtt_user") or "").strip() or None
    mqtt_pass = (opt.get("mqtt_password") or "").strip() or None

    disc_prefix = opt.get("mqtt_discovery_prefix", "homeassistant").strip().strip("/")
    base_topic = opt.get("mqtt_base_topic", "sensors/sht35_pca").strip().strip("/")
    device_name = opt.get("device_name", "Yellow J11 SHT35 PCA")

    interval = max(1, int(opt.get("interval_seconds", 10)))
    i2c_bus = int(opt.get("i2c_bus", 1))
    pca_addr = int(opt.get("pca_address", 0x70))
    sht_addr = int(opt.get("sht_address", 0x44))

    publish_status_sensors = bool(opt.get("publish_status_sensors", True))
    publish_nan_on_error = bool(opt.get("publish_nan_on_error", False))

    publish_reset = bool(opt.get("publish_reset_button", True))
    reset_gpiochip = str(opt.get("reset_gpiochip", "gpiochip0"))
    reset_gpioline = int(opt.get("reset_gpioline", 4))
    reset_pulse_ms = int(opt.get("reset_pulse_ms", 50))

    hostname = socket.gethostname()
    logger.info(
        "Starting app: mqtt=%s:%s base_topic=%s i2c_bus=%s pca=0x%02x sht=0x%02x channels=%s interval=%ss reset_button=%s",
        mqtt_host,
        mqtt_port,
        base_topic,
        i2c_bus,
        pca_addr,
        sht_addr,
        HARD_CODED_CHANNELS,
        interval,
        publish_reset,
    )
    device_ident = f"{hostname}_sht35_pca"

    dev = {
        "identifiers": [device_ident],
        "name": device_name,
        "manufacturer": "Sensirion / NXP",
        "model": "SHT35 via PCA9548A",
        "sw_version": "1.2",
    }

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=device_ident,
        clean_session=True,
    )
    if mqtt_user:
        client.username_pw_set(mqtt_user, mqtt_pass)

    avail_topic = f"{base_topic}/availability"
    client.will_set(avail_topic, "offline", retain=True)

    cmd_reset_topic = f"{base_topic}/pca/reset/set"
    ack_reset_topic = f"{base_topic}/pca/reset/ack"
    i2c_lock = threading.Lock()
    bus_ref: SMBus | None = None

    def on_message(_client, _userdata, msg) -> None:
        if not publish_reset:
            return
        if msg.topic != cmd_reset_topic:
            return

        payload = (msg.payload or b"").decode(errors="ignore").strip().lower()
        allowed = {"press", "1", "on", "reset", "rst", "true"}
        logger.debug("Received MQTT message on %s with payload=%r", msg.topic, payload)

        if payload not in allowed:
            _client.publish(ack_reset_topic, f"ignored:{payload}", retain=False)
            return

        try:
            with i2c_lock:
                do_gpio_reset(reset_gpiochip, reset_gpioline, reset_pulse_ms)
                time.sleep(0.01)
                reg_value = None
                if bus_ref is not None:
                    reg_value = pca_read_control(bus_ref, pca_addr)

            ack_payload = "ok"
            if reg_value is None:
                logger.warning("Reset executed, but PCA control register could not be read because the I2C bus is not ready yet")
                ack_payload = "ok_reg_unavailable"
            elif reg_value == 0x00:
                logger.info("PCA reset verified: control register = 0x00")
                ack_payload = "ok_reg_0x00"
            else:
                logger.warning("PCA reset verification failed: control register = 0x%02x (expected 0x00)", reg_value)
                ack_payload = f"warn_reg_0x{reg_value:02x}"

            _client.publish(ack_reset_topic, ack_payload, retain=False)
        except Exception:
            logger.exception("Reset command failed")
            _client.publish(ack_reset_topic, "error", retain=False)

    client.on_message = on_message
    client.connect(mqtt_host, mqtt_port, keepalive=60)
    logger.info("Connected to MQTT broker %s:%s", mqtt_host, mqtt_port)
    client.loop_start()

    for ch in HARD_CODED_CHANNELS:
        publish_discovery_sensor(
            client,
            disc_prefix,
            base_topic,
            dev,
            ch,
            publish_status_sensors,
        )

    if publish_reset:
        publish_reset_button(client, disc_prefix, base_topic, dev)
        client.subscribe(cmd_reset_topic)

    client.publish(avail_topic, "online", retain=True)

    # New process -> new identity namespace. Reconnects/GPIO resets do not
    # reset this counter. Failed reads also consume one attempt identifier.
    boot_id = uuid4().hex
    sample_id = 0

    with SMBus(i2c_bus) as bus:
        bus_ref = bus
        logger.info("Opened I2C bus %s", i2c_bus)
        while True:
            for ch in HARD_CODED_CHANNELS:
                sample_id += 1
                measured_at = None
                t = rh = None
                try:
                    with i2c_lock:
                        pca_select_channel(bus, pca_addr, ch)
                        t, rh = sht35_read(bus, sht_addr)
                        measured_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                        monotonic_ns = time.monotonic_ns()

                except Exception:
                    measured_at = None
                    monotonic_ns = time.monotonic_ns()
                    logger.exception("Read failed on channel %s", ch)
                    if publish_status_sensors:
                        client.publish(f"{base_topic}/ch{ch}/status", "read_error", retain=False)
                    if publish_nan_on_error:
                        client.publish(f"{base_topic}/ch{ch}/temperature", "nan", retain=False)
                        client.publish(f"{base_topic}/ch{ch}/humidity", "nan", retain=False)

                else:
                    logger.debug("Channel %s read successful: temperature=%.2f humidity=%.2f", ch, t, rh)
                    client.publish(f"{base_topic}/ch{ch}/temperature", f"{t:.2f}", retain=False)
                    client.publish(f"{base_topic}/ch{ch}/humidity", f"{rh:.2f}", retain=False)

                    if publish_status_sensors:
                        client.publish(f"{base_topic}/ch{ch}/status", "ok", retain=False)

                # Dedicated shared packet; legacy scalar topics/discovery
                # retain their values and identities. No changing per-second
                # metadata is attached to every HA scalar entity.
                payload = sample_payload(base_topic, ch, boot_id, sample_id,
                                         measured_at, monotonic_ns, t, rh)
                client.publish(f"{base_topic}/ch{ch}/sample",
                               json.dumps(payload, separators=(",", ":"), allow_nan=False),
                               retain=False)

            time.sleep(interval)


if __name__ == "__main__":
    main()
