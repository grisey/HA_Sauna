"""Real HA adapter and runtime packet route, with only MQTT transport replaced."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_device_feedback as fixtures
from test_sensor_sample import packet, SOURCES, TOPIC, records_for, comparison
from test_detector import trace
from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.sensor_sample import ClimatePacket, archive_measurements


@unittest.skipUnless(fixtures.HA_AVAILABLE, 'Home Assistant is required')
class ShtSamplesTests(unittest.TestCase):
    def test_packet_enters_common_runtime_once_and_scalar_reports_cannot_overwrite_it(self):
        async def exercise():
            fixture = fixtures.DeviceFeedbackTests()
            runtime, adapter, clock = fixture.detection_device()
            adapter.packet_roles.update(('upper_temperature', 'upper_humidity'))
            runtime.archive = SimpleNamespace(append=Mock())
            runtime._cycle = AsyncMock()
            runtime._deliver_detection = Mock()
            runtime._sync_detector = Mock()
            runtime.detector = SimpleNamespace(accept=Mock())
            item = packet(received=fixtures.T0 + timedelta(seconds=60))
            # Both fixture sets bind exactly the same HA entity IDs.
            item = ClimatePacket(tuple(replace(m, measured_at=fixtures.T0 + timedelta(seconds=59))
                                       for m in item.measurements))
            clock[0] = item.received_at
            await runtime.device_input(item)
            originals = [call.args[2] for call in runtime.archive.append.call_args_list]
            self.assertEqual(originals, list(item.measurements))
            self.assertEqual(runtime.detector.accept.call_count, 2)
            self.assertEqual(runtime._cycle.await_count, 1)
            self.assertEqual(adapter.measurements['upper_temperature'].sample,
                             adapter.measurements['upper_humidity'].sample)
            before = adapter.last_valid_temperature
            clock[0] += timedelta(seconds=1)
            await runtime.device_input(fixture.detection_edge(runtime, 'upper_temperature', 10))
            await runtime.device_input(ClimatePacket(tuple(replace(m, received_at=clock[0])
                                                           for m in item.measurements)))
            self.assertIs(adapter.last_valid_temperature, before)
            self.assertEqual(runtime.archive.append.call_count, 2)
            self.assertEqual(runtime._cycle.await_count, 1)
            restored = list(archive_measurements(records_for([item]), archive_id='adapter'))
            math = comparison()
            math.accept(restored[0])
            self.assertEqual(math.probabilities()[0].original_count, 2)
        asyncio.run(exercise())

    def test_old_scalar_path_remains_usable_without_optional_mqtt_fields(self):
        async def exercise():
            fixture = fixtures.DeviceFeedbackTests()
            runtime, adapter, clock = fixture.detection_device()
            runtime._cycle = AsyncMock()
            runtime._sync_detector = Mock()
            runtime._deliver_detection = Mock()
            runtime.archive = SimpleNamespace(append=Mock())
            runtime.detector = SimpleNamespace(accept=Mock())
            clock[0] += timedelta(seconds=1)
            await runtime.device_input(fixture.detection_edge(runtime, 'upper_temperature', trace(0)[0]))
            original = adapter.measurements['upper_temperature']
            self.assertIsNone(original.sample)
            self.assertIsNone(original.measured_at)
            self.assertEqual(original.received_at, clock[0])
            self.assertIsNotNone(original.original_id)
            self.assertEqual(runtime.detector.accept.call_count, 1)
            math = comparison()
            math.accept((original,))
            self.assertEqual(math.probabilities()[0].original_count, 1)
        asyncio.run(exercise())

    def test_binding_uses_selected_entities_and_actual_topics_including_swapped_positions(self):
        from custom_components.ha_sauna.sht_samples import sample_bindings
        entries, configs, bindings = {}, {}, {}
        for position, ch in (('upper', 6), ('lower', 3)):
            for quantity in ('temperature', 'humidity'):
                entity = f'sensor.{position}_{quantity}'
                uid = f'sht35_pca_ch{ch}_{quantity}'
                bindings[f'{position}_{quantity}'] = entity
                entries[entity] = SimpleNamespace(platform='mqtt', unique_id=uid)
                configs[entity] = {'unique_id': uid, 'state_topic': f'custom/prefix/ch{ch}/{quantity}',
                                   'device': {'sw_version': '1.2'}}
        result = sample_bindings(bindings, entries, configs)
        self.assertEqual(result['upper'][0], 'custom/prefix/ch6/sample')
        self.assertEqual(result['lower'][0], 'custom/prefix/ch3/sample')
        configs[bindings['upper_humidity']]['state_topic'] = 'custom/prefix/ch3/humidity'
        self.assertNotIn('upper', sample_bindings(bindings, entries, configs))
        configs[bindings['lower_humidity']]['device']['sw_version'] = '1.1'
        self.assertEqual(sample_bindings(bindings, entries, configs), {})

    def test_mqtt_subscription_claims_roles_before_first_message_and_unloads(self):
        async def exercise():
            import json
            from homeassistant.components import mqtt
            from homeassistant.components.mqtt import debug_info
            from custom_components.ha_sauna import sht_samples
            from test_sht35_app import load_app
            fixture = fixtures.DeviceFeedbackTests()
            runtime, adapter, clock = fixture.detection_device()
            adapter.hass.data = {'mqtt': object()}
            entries = {entity: SimpleNamespace(platform='mqtt', unique_id=f'sht35_pca_ch3_{q}',
                                                device_id='selected-sht-device') for q, entity in SOURCES.items()}
            infos = {'entities': [{'entity_id': entity, 'discovery_data': {'payload': {
                'unique_id': f'sht35_pca_ch3_{q}', 'state_topic': TOPIC.removesuffix('sample') + q,
                'device': {'sw_version': '1.2'},
            }}} for q, entity in SOURCES.items()]}
            callbacks = []
            unsubscribe = Mock()
            async def subscribe(hass, topic, callback, **kwargs):
                self.assertEqual(adapter.packet_roles, {'upper_temperature', 'upper_humidity'})
                self.assertEqual(topic, TOPIC)
                callbacks.append(callback)
                return unsubscribe
            closes = []
            runtime.on_close = closes.append
            runtime.device_input = AsyncMock()
            with (patch.object(sht_samples.er, 'async_get', return_value=SimpleNamespace(async_get=entries.get)),
                  patch.object(debug_info, 'info_for_device', return_value=infos),
                  patch.object(mqtt, 'async_subscribe', side_effect=subscribe),
                  patch.object(sht_samples, 'async_at_started', return_value=Mock())):
                await sht_samples.start_sample_sources(adapter)
            self.assertEqual(len(callbacks), 1)
            payload = load_app().sample_payload('sensors/sht35_pca', 3, 'boot', 1,
                                                clock[0].isoformat(), 1000, *trace(0))
            await callbacks[0](SimpleNamespace(payload=json.dumps(payload), retain=False))
            self.assertEqual(runtime.device_input.await_count, 1)
            physical = runtime.device_input.await_args.args[0]
            self.assertIsInstance(physical, ClimatePacket)
            self.assertEqual(physical.measurements[0].sample.sample_id, 1)
            await callbacks[0](SimpleNamespace(payload=json.dumps(payload), retain=True))
            self.assertEqual(runtime.device_input.await_count, 1)
            for close in closes:
                close()
            unsubscribe.assert_called_once()
        asyncio.run(exercise())

    def test_delayed_original_keeps_evidence_but_not_a_fresh_control_temperature(self):
        fixture = fixtures.DeviceFeedbackTests()
        runtime, adapter, clock = fixture.detection_device()
        old = packet().measurements[0]
        old = replace(old, received_at=clock[0], measured_at=clock[0] - timedelta(days=1))
        adapter.ingest_measurement(old)
        self.assertIsNone(adapter.regulation_measurement(clock[0]))
        self.assertEqual(adapter.measurement_status(clock[0])['upper_temperature']['state'], 'stale')
        math = comparison()
        self.assertTrue(math.accept((old,)))
        self.assertEqual(math.probabilities()[0].original_count, 1)
