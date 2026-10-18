"""Optional SHT 1.2 MQTT input, resolved from the selected HA entities."""

import logging

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.start import async_at_started

from .core.sensor_sample import decode_sample

_LOGGER = logging.getLogger(__name__)


def sample_bindings(bindings, entities, discovery):
    """Use actual discovery state topics; channel numbers do not imply position."""
    result = {}
    for position in ("upper", "lower"):
        sources = {q: bindings.get(f"{position}_{q}") for q in ("temperature", "humidity")}
        configs = [discovery.get(sources[q], {}) for q in sources]
        entries = [entities.get(sources[q]) for q in sources]
        if (any(entry is None or entry.platform != "mqtt" for entry in entries)
                or any(not isinstance(config, dict) for config in configs)):
            continue
        stems = []
        for quantity, entry, config in zip(sources, entries, configs, strict=True):
            uid = config.get("unique_id", "")
            topic = config.get("state_topic", "")
            version = config.get("device", {}).get("sw_version")
            if (uid != entry.unique_id or uid not in (
                    f"sht35_pca_ch3_{quantity}", f"sht35_pca_ch6_{quantity}")
                    or version != "1.2" or not topic.endswith("/" + quantity)
                    or any(char in topic for char in ("+", "#", "\0"))):
                break
            stems.append(topic.rsplit("/", 1)[0])
        if (len(stems) == 2 and stems[0] == stems[1]
                and entries[0].unique_id.rsplit('_', 1)[0] == entries[1].unique_id.rsplit('_', 1)[0]):
            result[position] = stems[0] + "/sample", sources
    return result


async def start_sample_sources(device):
    """HA MQTT owns reconnects; this adapter owns packet/scalar exclusivity."""
    hass, runtime = device.hass, device.runtime
    if "mqtt" not in getattr(hass, "data", {}):
        return
    from homeassistant.components import mqtt
    from homeassistant.components.mqtt import debug_info

    subscribed = set()

    async def connect(_hass=None):
        if runtime.closed:
            return
        registry = er.async_get(hass)
        entities = {source: registry.async_get(source) for source in device.bindings.values()}
        discovery = {}
        for device_id in {entry.device_id for entry in entities.values()
                          if entry is not None and entry.platform == "mqtt" and entry.device_id}:
            for item in debug_info.info_for_device(hass, device_id)["entities"]:
                discovery[item["entity_id"]] = item["discovery_data"]["payload"]
        for position, (topic, sources) in sample_bindings(device.bindings, entities, discovery).items():
            if position in subscribed:
                continue

            async def received(message, *, position=position, sources=sources, topic=topic):
                if runtime.closed or message.retain:
                    return
                try:
                    packet = decode_sample(topic, message.payload, position, sources, runtime._clock())
                except (ValueError, KeyError, TypeError, OverflowError) as error:
                    _LOGGER.warning("Ungültiges SHT-Messpaket für %s: %s", position, error)
                    return
                await runtime.device_input(packet)

            # Claim before subscribe yields: scalar messages precede the JSON
            # in the publisher. Waiting for the first JSON would double count.
            roles = {f"{position}_{q}" for q in sources}
            device.packet_roles.update(roles)
            try:
                unsubscribe = await mqtt.async_subscribe(hass, topic, received, qos=0)
            except Exception:
                device.packet_roles.difference_update(roles)
                raise
            runtime.on_close(unsubscribe)
            subscribed.add(position)

    await connect()
    runtime.on_close(async_at_started(hass, connect))
    return connect
