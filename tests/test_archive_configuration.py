"""Shared immutable settings preserve full historical archive contracts."""

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from functools import partial
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna.archive import Archive, encoded, plain
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.energy import advance
from test_foundation import T0, bindings, parameters


class ArchiveConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.directory.name) / "current.sqlite", "entry")
        await self.archive.start()
        self.archive.read = partial(self.archive.read, now=T0)
        self.controller = Controller(parameters())
        self.controller.begin_session("settings-session", T0)
        self.configuration = {
            "parameters": parameters().as_dict(),
            "bindings": bindings().as_dict(),
            "historical_extension": {"label": "Kühlung ♨", "unset": None, "enabled": False},
        }

    async def asyncTearDown(self):
        await self.archive.close()
        self.directory.cleanup()

    def snapshot(self, configuration, *, identity="settings-session"):
        snapshot = plain(self.controller.session)
        snapshot["timeline"]["session_id"] = identity
        snapshot["configuration"] = deepcopy(configuration)
        return snapshot

    async def append_snapshot(self, configuration, second=0, *, identity="settings-session"):
        snapshot = self.snapshot(configuration, identity=identity)
        self.archive.append("session", T0 + timedelta(seconds=second), snapshot, identity)
        await self.archive.flush()
        return snapshot

    def counts(self):
        with closing(sqlite3.connect(self.archive.path)) as db:
            return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                         for table in ("records", "sessions", "configuration_versions"))

    async def test_versions_share_canonical_values_and_keep_each_historical_assignment(self):
        first = self.configuration
        reordered = {key: (dict(reversed(tuple(value.items()))) if isinstance(value, dict) else value)
                     for key, value in reversed(tuple(first.items()))}
        changed = deepcopy(first)
        changed["historical_extension"]["enabled"] = True
        for index, configuration in enumerate((first, reordered, changed, first)):
            await self.append_snapshot(configuration, index)
        await self.append_snapshot(first, 5, identity="another-session")
        with closing(sqlite3.connect(self.archive.path)) as db:
            rows = db.execute("SELECT id,configuration_id,payload FROM records ORDER BY id").fetchall()
            versions = db.execute("SELECT id,payload FROM configuration_versions ORDER BY id").fetchall()
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0], "2")
        self.assertEqual(len(versions), 2)
        self.assertEqual([row[1] for row in rows], [versions[0][0], versions[0][0],
                                                  versions[1][0], versions[0][0], versions[0][0]])
        self.assertTrue(all("configuration" not in json.loads(row[2]) for row in rows))
        self.assertEqual(json.loads(versions[0][1]), first)
        result = self.archive.read("settings-session")
        self.assertEqual([record["payload"]["configuration"] for record in result["records"]],
                         [first, first, changed, first])
        self.assertEqual(result["session"]["configuration"], first)
        self.assertEqual([record["id"] for record in result["records"]], [row[0] for row in rows[:4]])
        self.assertTrue(all("configuration_id" not in record for record in result["records"]))
        # No decoded configuration is shared with another record or current row.
        result["records"][0]["payload"]["configuration"]["historical_extension"]["label"] = "changed by reader"
        self.assertEqual(result["records"][1]["payload"]["configuration"], first)
        self.assertEqual(result["session"]["configuration"], first)
        self.assertEqual(self.archive.read("settings-session")["session"]["configuration"], first)

    async def test_missing_empty_and_null_configuration_stay_distinct_in_reads_and_export(self):
        missing = self.snapshot({})
        missing.pop("configuration")
        snapshots = (missing, self.snapshot({}), self.snapshot(None))
        for second, snapshot in enumerate(snapshots):
            self.archive.append("session", T0 + timedelta(seconds=second), snapshot, "settings-session")
        await self.archive.flush()
        result = self.archive.read("settings-session", humidity_display=True)
        self.assertEqual([row["payload"] for row in result["records"]], list(snapshots))
        self.assertIn("configuration", result["session"])
        self.assertIsNone(result["session"]["configuration"])
        self.assertEqual(self.counts(), (3, 1, 2))
        for index, snapshot in enumerate(snapshots):
            page = self.archive.read("settings-session", after=index, limit=1, humidity_display=True)
            self.assertEqual(page["records"][0]["payload"], snapshot)
        path = await self.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                records = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
                sessions = [json.loads(line) for line in archive.read("sessions.jsonl").splitlines()]
            self.assertEqual([row["payload"] for row in records], list(snapshots))
            self.assertIsNone(sessions[0]["configuration"])
        finally:
            path.unlink()

    async def test_configuration_change_mid_session_keeps_energy_deltas_and_paging(self):
        p = parameters()
        step = timedelta(seconds=p.values["sensor_timeout_seconds"])
        rate = p.values["nominal_power_kw"] / 2
        energy = self.controller.session.energy
        changed = deepcopy(self.configuration)
        changed["parameters"]["target_temperature_c"] += 1
        configurations = (self.configuration, changed, changed)
        for number, configuration in enumerate(configurations, 1):
            at = T0 + number * step
            energy = advance(energy, at, power_w=rate * 1000, valid_until=at,
                             heating=True, nominal_kw=p.values["nominal_power_kw"])
            self.archive.save_session(replace(self.controller.session, energy=energy), at, configuration)
        await self.archive.flush()
        records, cursor = [], 0
        while True:
            page = self.archive.read("settings-session", after=cursor, limit=1)
            records.extend(page["records"])
            self.assertEqual(page["session"]["configuration"], changed)
            cursor = page["next_after"]
            if cursor is None:
                break
        self.assertEqual([row["payload"]["configuration"] for row in records], list(configurations))
        self.assertEqual(len({row["id"] for row in records}), 3)
        deltas = [row["payload"]["energy"]["interval_delta"] for row in records]
        self.assertEqual([delta["from"] for delta in deltas],
                         [None, (T0 + step).isoformat(), (T0 + 2 * step).isoformat()])
        self.assertEqual([delta["to"] for delta in deltas],
                         [(T0 + number * step).isoformat() for number in range(1, 4)])
        self.assertEqual([delta["intervals"][0]["started_at"] for delta in deltas],
                         [(T0 + number * step).isoformat() for number in range(3)])
        final = page["session"]["energy"]
        self.assertAlmostEqual(final["measured_kwh"], rate * 3 * step.total_seconds() / 3600)
        self.assertEqual(final["interval_delta"]["intervals"], [{
            "started_at": T0.isoformat(), "ended_at": (T0 + 3 * step).isoformat(),
            "source": "measured", "power_kw": rate,
        }])

    async def test_full_exports_equal_public_records_and_cache_alternating_versions(self):
        changed = deepcopy(self.configuration)
        changed["historical_extension"]["enabled"] = True
        for number in range(100):
            self.archive.append("session", T0 + timedelta(seconds=number),
                                self.snapshot(changed if number % 2 else self.configuration),
                                "settings-session")
        await self.archive.flush()
        connect, queries = sqlite3.connect, []

        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(queries.append)
            return db

        with patch.object(archive_module.sqlite3, "connect", side_effect=traced):
            expected = self.archive.read("settings-session", limit=1000)
        self.assertEqual(sum(query.startswith("SELECT payload FROM configuration_versions")
                             for query in queries), 2)
        path = await self.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json"))["schema"], 1)
                records = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
                sessions = [json.loads(line) for line in archive.read("sessions.jsonl").splitlines()]
            self.assertEqual(records, expected["records"])
            self.assertEqual(sessions, [expected["session"]])
            self.assertTrue(all(set(record) == {"id", "entry_id", "session_id", "kind", "received_at", "payload"}
                                for record in records))
        finally:
            path.unlink()

    async def test_hash_collision_never_reuses_different_json_values(self):
        first = {"value": True, "label": "historical"}
        second = {"value": 1, "label": "historical"}
        with patch.object(archive_module, "sha256", return_value=Mock(hexdigest=lambda: "collision")):
            for number, value in enumerate((first, second, first)):
                await self.append_snapshot(value, number)
        with closing(sqlite3.connect(self.archive.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM configuration_versions").fetchone()[0], 2)
            identities = [row[0] for row in db.execute("SELECT configuration_id FROM records ORDER BY id")]
        self.assertEqual(identities[0], identities[2])
        self.assertNotEqual(identities[0], identities[1])
        values = [row["payload"]["configuration"]["value"]
                  for row in self.archive.read("settings-session")["records"]]
        self.assertIs(values[0], True)
        self.assertIs(type(values[1]), int)
        self.assertIs(values[2], True)

    async def test_append_rolls_back_version_record_and_scalar_before_ordered_retry(self):
        original = await self.append_snapshot(self.configuration)
        before = self.counts()
        changed = deepcopy(self.configuration)
        changed["historical_extension"]["label"] = "new version"
        snapshot = self.snapshot(changed)
        connect = sqlite3.connect

        class FailedScalarConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("INSERT INTO sessions"):
                    raise sqlite3.OperationalError("failure after record insert")
                return super().execute(sql, *args)

        with patch.object(archive_module.sqlite3, "connect", side_effect=lambda *args, **kwargs:
                          connect(*args, **kwargs, factory=FailedScalarConnection)):
            with self.assertRaisesRegex(sqlite3.OperationalError, "after record"):
                self.archive._write(("session", (T0 + timedelta(seconds=1)).isoformat(),
                                     encoded(snapshot), "settings-session"))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.archive.read("settings-session")["session"], original)
        await self.append_snapshot(changed, 1)
        self.assertEqual(self.counts(), (before[0] + 1, before[1], before[2] + 1))
        self.assertEqual([row["payload"]["configuration"]
                          for row in self.archive.read("settings-session")["records"]],
                         [self.configuration, changed])

    async def test_reader_keeps_old_configuration_during_a_concurrent_committed_change(self):
        await self.append_snapshot(self.configuration)
        changed = deepcopy(self.configuration)
        changed["historical_extension"]["label"] = "concurrent change"
        snapshot = self.snapshot(changed)
        connect = sqlite3.connect
        archive = self.archive
        committed = False

        class InterleavedConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                nonlocal committed
                if not committed and sql.startswith("SELECT payload FROM configuration_versions"):
                    # The scalar SELECT established the reader's snapshot before
                    # another connection commits the next full version and record.
                    committed = True
                    archive._write(("session", (T0 + timedelta(seconds=1)).isoformat(),
                                    encoded(snapshot), "settings-session"))
                return super().execute(sql, *args)

        with patch.object(archive_module.sqlite3, "connect", side_effect=lambda *args, **kwargs:
                          connect(*args, **kwargs, factory=InterleavedConnection)):
            old = archive.read("settings-session")
        self.assertTrue(committed)
        self.assertEqual(old["session"]["configuration"], self.configuration)
        self.assertEqual(len(old["records"]), 1)
        self.assertEqual(old["records"][0]["payload"]["configuration"], self.configuration)
        new = archive.read("settings-session")
        self.assertEqual(new["session"]["configuration"], changed)
        self.assertEqual(len(new["records"]), 2)

    async def test_retention_removes_only_unreferenced_configuration_versions(self):
        await self.append_snapshot(self.configuration)
        self.assertEqual(self.counts(), (1, 1, 1))
        await self.archive.close()
        retained = Archive(self.archive.path, "entry", sessions_only=True)
        await retained.start()
        try:
            # This real controller fixture has no confirmed gang and is retired
            # by the existing startup retention rule, including its private config.
            self.assertEqual(retained.read(), [])
            self.assertEqual(self.counts(), (0, 0, 0))
        finally:
            await retained.close()

    async def test_versions_are_immutable_and_deletion_respects_remaining_references(self):
        await self.append_snapshot(self.configuration, identity="first")
        await self.append_snapshot(self.configuration, identity="second")
        other = Archive(self.archive.path, "other-entry")
        await other.start()
        try:
            other.append("session", T0, self.snapshot(self.configuration, identity="other"), "other")
            await other.flush()
            with closing(sqlite3.connect(self.archive.path)) as db, db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    db.execute("UPDATE configuration_versions SET payload='{}'")
            await self.archive.erase("first")
            self.assertEqual(self.archive.read("second")["session"]["configuration"], self.configuration)
            with closing(sqlite3.connect(self.archive.path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM configuration_versions WHERE entry_id='entry'").fetchone()[0], 1)
            await self.archive.erase(reset=True)
            with closing(sqlite3.connect(self.archive.path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM configuration_versions WHERE entry_id='entry'").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM configuration_versions WHERE entry_id='other-entry'").fetchone()[0], 1)
            self.assertEqual(other.read("other")["session"]["configuration"], self.configuration)
        finally:
            await other.close()

    def legacy_database(self, path):
        """Native schema-1 tables and a real controller snapshot, no new writer."""
        snapshot = self.snapshot(self.configuration, identity="legacy")
        raw = encoded(snapshot)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO metadata VALUES ('schema','1');
                CREATE TABLE records (id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL,
                    session_id TEXT, kind TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX records_session ON records(entry_id,session_id,id);
                CREATE INDEX records_measurement_context ON records(entry_id,julianday(received_at))
                    WHERE kind='measurement';
                CREATE TABLE sessions (session_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT, payload TEXT NOT NULL);
            """)
            db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (71, "entry", "legacy", "session", T0.isoformat(), raw))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", ("legacy", "entry", T0.isoformat(), T0.isoformat(), None, raw))
        return raw

    async def test_schema_one_readonly_then_additive_upgrade_preserves_native_record(self):
        path = Path(self.directory.name) / "legacy.sqlite"
        raw = self.legacy_database(path)
        before = path.read_bytes()
        reader = Archive(path, "entry")
        expected = reader.read("legacy", now=T0)
        self.assertEqual(expected["records"][0]["id"], 71)
        self.assertEqual(expected["session"]["configuration"], self.configuration)
        exported = reader._export()
        try:
            with zipfile.ZipFile(exported) as archive:
                self.assertEqual([json.loads(line) for line in archive.read("records.jsonl").splitlines()],
                                 expected["records"])
        finally:
            exported.unlink()
        self.assertEqual(path.read_bytes(), before)
        writer = Archive(path, "entry")
        await writer.start()
        try:
            self.assertEqual(reader.read("legacy", now=T0), expected)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT payload,configuration_id FROM records WHERE id=71").fetchone(), (raw, None))
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0], "2")
            changed = deepcopy(self.configuration)
            changed["historical_extension"]["label"] = "after upgrade"
            writer.append("session", T0 + timedelta(seconds=1), self.snapshot(changed, identity="legacy"), "legacy")
            await writer.flush()
            # The same reader must notice the schema change without restarting.
            actual = reader.read("legacy", now=T0)
            self.assertEqual(actual["records"][0], expected["records"][0])
            self.assertEqual(actual["records"][1]["id"], 72)
            self.assertEqual(actual["records"][1]["payload"]["configuration"], changed)
            self.assertEqual(actual["session"]["configuration"], changed)
        finally:
            await writer.close()

    async def test_failed_additive_migration_leaves_schema_one_and_both_tables_intact(self):
        path = Path(self.directory.name) / "migration-failure.sqlite"
        raw = self.legacy_database(path)
        connect = sqlite3.connect

        class FailedMigrationConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("ALTER TABLE sessions"):
                    raise sqlite3.OperationalError("migration interrupted")
                return super().execute(sql, *args)

        with patch.object(archive_module.sqlite3, "connect", side_effect=lambda *args, **kwargs:
                          connect(*args, **kwargs, factory=FailedMigrationConnection)):
            with self.assertRaisesRegex(sqlite3.OperationalError, "migration interrupted"):
                Archive(path, "entry")._initialize()
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0], "1")
            self.assertNotIn("configuration_id", {row[1] for row in db.execute("PRAGMA table_info(records)")})
            self.assertNotIn("configuration_id", {row[1] for row in db.execute("PRAGMA table_info(sessions)")})
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='configuration_versions'").fetchone())
            self.assertEqual(db.execute("SELECT id,payload FROM records").fetchall(), [(71, raw)])
        self.assertEqual(Archive(path, "entry").read("legacy")["records"][0]["payload"], json.loads(raw))

    async def test_backup_contains_configuration_and_references_at_one_committed_boundary(self):
        await self.append_snapshot(self.configuration)
        expected = self.archive.read("settings-session")
        await self.archive.pre_backup()
        try:
            changed = deepcopy(self.configuration)
            changed["historical_extension"]["label"] = "queued after backup boundary"
            self.archive.append("session", T0 + timedelta(seconds=1), self.snapshot(changed), "settings-session")
            backup = Path(self.directory.name) / "restored.sqlite"
            shutil.copy2(self.archive.path, backup)
            restored = Archive(backup, "entry")
            self.assertEqual(restored.read("settings-session", now=T0), expected)
            with closing(sqlite3.connect(backup)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM configuration_versions").fetchone()[0], 1)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            self.archive.release_backup()
        await self.archive.flush()
        self.assertEqual(self.archive.read("settings-session")["session"]["configuration"], changed)


if __name__ == "__main__":
    unittest.main()
