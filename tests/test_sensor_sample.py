"""Publisher → originals → archive/replay → input of the recognition."""

from dataclasses import replace
from datetime import timedelta
import csv
import io
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from climate_fixtures import trace
from test_foundation import T0
from test_sht35_app import load_app
from custom_components.ha_sauna.archive import Archive, plain
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.sensor_sample import (
    ClimatePacket, SampleStream, archive_measurements, decode_sample,
)
from custom_components.ha_sauna.core.recognition import ClimateRecognizer
from custom_components.ha_sauna.recognition_parameters import shipped_parameters
from custom_components.ha_sauna.core.moisture import current_absolute_humidity
from custom_components.ha_sauna.history_humidity import project_absolute_humidity


SOURCES = {'temperature': 'sensor.top_t', 'humidity': 'sensor.top_h'}
TOPIC = 'sensors/sht35_pca/ch3/sample'


def packet(second=60, *, sample_id=None, boot='fixture-boot', received=None, changes=None):
    module = load_app()
    at = T0 + timedelta(seconds=second)
    t, h = trace(second)
    payload = module.sample_payload('sensors/sht35_pca', 3, boot,
                                    sample_id if sample_id is not None else second + 1,
                                    at.isoformat(), int(second * 1e9), t, h)
    payload.update(changes or {})
    return decode_sample(TOPIC, json.dumps(payload), Position.UPPER, SOURCES, received or at)


def comparison():
    """Recognition input only: the measurement grid of the upper position, without evaluation."""
    return ClimateRecognizer(shipped_parameters(), T0, positions=(Position.UPPER,), evaluate=False)


def accept(recognizer, measurements):
    return [recognizer.accept(m) for m in measurements]


def course(recognizer, through=T0 + timedelta(seconds=600)):
    """Evaluated grid: (seconds since start, channel values) per step of the clock channel."""
    recognizer.advance(through)
    record = recognizer.record
    return list(zip(record.times, map(tuple, record.values), strict=True))


def records_for(packets):
    return [{'id': index, 'kind': 'measurement', 'payload': plain(m)}
            for index, m in enumerate((m for p in packets for m in p.measurements), 1)]


class SensorSampleTests(unittest.TestCase):
    def test_actual_publisher_packet_preserves_physical_pair_and_entity_bindings(self):
        a, b = packet().measurements
        self.assertEqual(a.sample, b.sample)
        self.assertEqual(a.sample.sample_id, 61)
        self.assertEqual(a.measured_at, T0 + timedelta(seconds=60))
        self.assertEqual((a.value, b.value), trace(60))
        self.assertEqual((a.source, b.source), tuple(SOURCES.values()))
        self.assertEqual(Measurement.from_payload(plain(a)), a)

    def test_malformed_second_quantity_does_not_produce_a_partial_packet(self):
        for changes in ({'humidity': 101}, {'humidity': float('nan')},
                        {'schema_version': True}, {'sample_id': True},
                        {'sensor_id': 'wrong/channel'}, {'monotonic_ns': -1},
                        {'measured_at': None}, {'measured_at': '2026-01-01T00:00:00'}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                packet(changes=changes)

    def test_new_equal_values_are_originals_duplicates_do_not_renew_freshness(self):
        stream = SampleStream()
        first = packet()
        self.assertTrue(stream.accept(first))
        repeated = ClimatePacket(tuple(replace(m, received_at=m.received_at + timedelta(days=1))
                                       for m in first.measurements))
        self.assertFalse(stream.accept(repeated))
        self.assertIs(stream.last[first.measurements[0].sample.sensor_id], first)
        self.assertTrue(stream.accept(packet(61)))
        self.assertEqual(packet(60).measurements[0].value, packet(61).measurements[0].value)
        self.assertFalse(stream.accept(first))
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            stream.accept(packet(61, changes={'humidity': trace(150)[1]}))

    def test_boot_change_id_gaps_and_clock_correction_are_distinct(self):
        stream = SampleStream()
        self.assertTrue(stream.accept(packet()))
        self.assertTrue(stream.accept(packet(61, sample_id=90,
                                             changes={'measured_at': (T0 - timedelta(days=1)).isoformat()})))
        self.assertTrue(stream.accept(packet(62, sample_id=1, boot='restarted')))
        self.assertFalse(stream.accept(packet(63, sample_id=91)))
        with self.assertRaisesRegex(ValueError, 'clock'):
            stream.accept(packet(63, sample_id=2, boot='restarted', changes={'monotonic_ns': 0}))

    def test_read_error_is_unknown_without_evidence_or_invented_measurement_time(self):
        stream = comparison()
        accept(stream, packet().measurements)
        before = course(stream)
        failed = packet(61, changes={'status': 'read_error', 'temperature': None,
                                     'humidity': None, 'measured_at': None})
        self.assertEqual(accept(stream, failed.measurements), [False, False])
        self.assertEqual(course(stream), before)
        self.assertIsNone(failed.measurements[0].measured_at)
        self.assertEqual(accept(stream, packet(62).measurements), [True, True])
        self.assertEqual([at for at, _ in course(stream)], [60.0, 62.0])

    def test_archive_roundtrip_has_the_same_course_and_no_snapshot_votes(self):
        packets = [packet(second) for second in range(60, 76)]
        live, replay = comparison(), comparison()
        for item in packets:
            accept(live, item.measurements)
        rows = records_for(packets)
        rows.insert(2, {'id': 1000, 'kind': 'source_snapshot', 'payload': rows[0]['payload']})
        for originals in archive_measurements(rows, archive_id='fixture-archive'):
            accept(replay, originals)
        self.assertEqual(course(replay), course(live))
        self.assertEqual(len(course(live)), len(packets))
        repeated = tuple(replace(m, received_at=T0 + timedelta(seconds=80)) for m in packets[-1].measurements)
        self.assertEqual(accept(replay, repeated), [False, False])
        self.assertEqual(course(replay), course(live))

    def test_legacy_archive_and_current_archive_share_the_course_without_fabricated_metadata(self):
        packets = [packet(second) for second in range(60, 76)]
        modern, legacy = comparison(), comparison()
        rows = records_for(packets)
        for row in rows:
            for key in ('sample', 'measured_at', 'original_id'):
                row['payload'].pop(key, None)
        for item in packets:
            accept(modern, item.measurements)
        for originals in archive_measurements(rows, archive_id='legacy-fixture'):
            self.assertEqual(len(originals), 1)
            self.assertIsNone(originals[0].sample)
            self.assertIsNone(originals[0].measured_at)
            self.assertTrue(originals[0].original_id.startswith('legacy-fixture:'))
            accept(legacy, originals)
        self.assertEqual(course(legacy), course(modern))
        before = course(legacy)
        for originals in archive_measurements(rows, archive_id='legacy-fixture'):
            accept(legacy, tuple(replace(m, received_at=T0 + timedelta(seconds=80)) for m in originals))
        # Receipt-only originals retain their original curve time on redelivery.
        self.assertEqual(course(legacy), before)

    def test_distinct_legacy_records_at_same_time_remain_distinct_originals(self):
        first = replace(packet().measurements[0], sample=None, measured_at=None)
        rows = [{'id': i, 'kind': 'measurement', 'payload': plain(first)} for i in (1, 2)]
        stream = comparison()
        accepted = [stream.accept(m) for originals in archive_measurements(rows, archive_id='old')
                    for m in originals]
        self.assertEqual(accepted, [True, True])

    def test_existing_r03_rows_without_new_fields_enter_the_recognition(self):
        path = Path(__file__).parent / 'fixtures/replay/R03.samples.jsonl.gz'
        with gzip.open(path, 'rt') as source:
            rows = [(index, json.loads(line)) for index, line in enumerate(source)]
        stream = comparison()
        originals = [(index, row) for index, row in rows
                     if row['role'] in ('upper_temperature', 'upper_humidity')][:32]
        self.assertEqual(len(originals), 32)
        temperatures = []
        for index, row in originals:
            self.assertNotIn('sample', row)
            self.assertNotIn('measured_at', row)
            quantity = row['role'].removeprefix('upper_')
            at = T0 + timedelta(seconds=row['t'])
            value = row['value'] if type(row['value']) in (float, int) else None
            m = Measurement(Position.UPPER, Quantity(quantity), value, str(row['value']),
                            SOURCES[quantity], at, original_id=f'fixture:R03:{index}')
            self.assertEqual(stream.accept(m), value is not None)
            if quantity == 'temperature' and value is not None:
                temperatures.append(row['t'])
        times = [at for at, _ in course(stream, T0 + timedelta(seconds=max(r['t'] for _, r in originals)))]
        self.assertTrue(times)
        self.assertTrue(set(times) <= {round(float(t), 6) for t in temperatures} | set(map(float, temperatures)))

    def test_initial_legacy_snapshot_preserves_original_when_no_measurement_row_exists(self):
        old = replace(packet().measurements[0], sample=None, measured_at=None)
        rows = [
            {'id': 1, 'kind': 'source_snapshot', 'payload': plain(old)},
            {'id': 2, 'kind': 'source_snapshot', 'payload': plain(old)},
            {'id': 3, 'kind': 'measurement', 'payload': plain(old)},
        ]
        originals = list(archive_measurements(rows, archive_id='old-start'))
        self.assertEqual(len(originals), 2)
        self.assertEqual(originals[0][0].original_id, originals[1][0].original_id)
        self.assertEqual(originals[0][0].received_at, old.received_at)
        stream = comparison()
        self.assertEqual(accept(stream, originals[0]), [True])
        self.assertEqual(accept(stream, originals[1]), [False])

    def test_later_snapshot_keeps_older_source_time_and_later_knowledge_boundary(self):
        early = replace(packet(60).measurements[1], sample=None, measured_at=None)
        later = replace(packet(61).measurements[0], sample=None, measured_at=None)
        subsequent = replace(packet(62).measurements[0], sample=None, measured_at=None)
        at = T0 + timedelta(seconds=70)
        rows = [
            {'id': 1, 'kind': 'measurement', 'payload': plain(later)},
            {'id': 2, 'kind': 'source_state', 'received_at': at.isoformat()},
            {'id': 3, 'kind': 'source_snapshot', 'payload': plain(early),
             'received_at': (at - timedelta(seconds=1)).isoformat()},
            {'id': 4, 'kind': 'measurement', 'payload': plain(subsequent)},
        ]
        batches = list(archive_measurements(rows, archive_id='late-snapshot'))
        self.assertEqual(batches[1][0].received_at, early.received_at)
        self.assertIsNone(batches[1][0].measured_at)
        self.assertEqual(batches[1].observed_at, at)
        self.assertEqual(batches[2].observed_at, at)
        stream = comparison()
        self.assertEqual([stream.accept(m) for batch in batches for m in batch], [True, True, True])
        # the snapshot keeps its own (older) curve time; both temperatures are evaluated with it
        self.assertEqual([at for at, _ in course(stream)], [61.0, 62.0])

    def test_snapshot_pairs_follow_identity_not_binding_dictionary_order(self):
        upper = packet()
        lower = ClimatePacket(tuple(replace(
            m, position=Position.LOWER, source=m.source.replace('top', 'bottom'),
            sample=replace(m.sample, sensor_id='sensors/sht35_pca/ch6', sample_id=62),
        ) for m in upper.measurements))
        rows = [{'id': index, 'kind': 'source_snapshot', 'payload': plain(m)}
                for index, m in enumerate((upper.measurements[1], lower.measurements[1],
                                           upper.measurements[0], lower.measurements[0]), 1)]
        batches = list(archive_measurements(rows, archive_id='reordered-snapshot'))
        self.assertEqual(tuple(batches[0]), upper.measurements)
        self.assertEqual(tuple(batches[1]), lower.measurements)

    def test_monotonic_clock_prevents_utc_correction_from_warping_curves(self):
        regular, corrected = comparison(), comparison()
        for second in range(60, 76):
            accept(regular, packet(second).measurements)
            shift = timedelta(days=-1 if second > 65 else 0)
            changed = packet(second, changes={'measured_at': (T0 + timedelta(seconds=second) + shift).isoformat()})
            accept(corrected, changed.measurements)
        self.assertEqual(course(corrected), course(regular))
        self.assertEqual([at for at, _ in course(regular)], [float(s) for s in range(60, 76)])

    def test_active_recognition_preserves_new_original_after_source_utc_correction(self):
        recognizer = comparison()
        first = packet(60)
        second = packet(61, changes={'measured_at': (T0 - timedelta(days=1)).isoformat()})
        for item in (first, second):
            self.assertEqual(accept(recognizer, item.measurements), [True, True])
            recognizer.advance(item.received_at)
        self.assertEqual([at for at, _ in course(recognizer)], [60.0, 61.0])

    def test_absolute_humidity_never_mixes_two_different_physical_samples(self):
        old, new = packet(60), packet(61)
        at = new.received_at
        mixed = {'upper_temperature': new.measurements[0], 'upper_humidity': old.measurements[1]}
        self.assertIsNone(current_absolute_humidity(mixed, Position.UPPER, at, 60))
        rows = records_for([old, new])
        result = project_absolute_humidity(rows, bindings={f'upper_{q}': s for q, s in SOURCES.items()},
                                           timeout_seconds=60)
        self.assertIsNone(result[2]['display']['absolute_humidity']['value'])
        self.assertIsNotNone(result[3]['display']['absolute_humidity']['value'])


class SampleArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_jsonl_and_csv_keep_new_metadata_and_read_old_rows(self):
        with TemporaryDirectory() as temporary:
            archive = Archive(Path(temporary) / 'samples.sqlite', 'fixture-entry')
            await archive.start()
            try:
                physical = packet()
                for measurement in physical.measurements:
                    archive.append('measurement', measurement.received_at, measurement, None)
                old = plain(replace(physical.measurements[0], sample=None, measured_at=None))
                old.pop('sample'); old.pop('original_id'); old.pop('measured_at')
                # Existing archives always had nullable measured_at.
                old['measured_at'] = None
                archive.append('measurement', physical.received_at, old, None)
                await archive.flush()
                path = await archive.export()
                try:
                    with zipfile.ZipFile(path) as output:
                        rows = [json.loads(line) for line in output.read('records.jsonl').splitlines()]
                        rows = [row for row in rows if row['kind'] == 'measurement']
                        self.assertEqual(Measurement.from_payload(rows[0]['payload']), physical.measurements[0])
                        self.assertIsNone(Measurement.from_payload(rows[-1]['payload']).sample)
                        csv_rows = list(csv.DictReader(io.StringIO(output.read('measurements.csv').decode())))
                        self.assertEqual(csv_rows[0]['boot_id'], physical.measurements[0].sample.boot_id)
                        self.assertEqual(csv_rows[0]['sample_id'], '61')
                        self.assertEqual(csv_rows[-1]['sample_id'], '')
                finally:
                    path.unlink()
            finally:
                await archive.close()
