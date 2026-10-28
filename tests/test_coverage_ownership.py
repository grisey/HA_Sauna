"""Bound scenario evaluations replace, rather than duplicate, old domain checks."""

import ast
import json
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def method_identities(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        (cls.name, method.name)
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for method in cls.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def scenario_evaluations():
    result = {}
    for name, collection, field in (
        ("scenario_manifest.json", "scenarios", "path"),
        ("replay_manifest.json", "sessions", "file"),
    ):
        manifest = json.loads((TESTS / name).read_text(encoding="utf-8"))
        for entry in manifest[collection]:
            scenario = json.loads((TESTS / entry[field]).read_text(encoding="utf-8"))
            if scenario["id"] in result:
                raise ValueError(f"Duplicate scenario identity: {scenario['id']}")
            configuration = scenario.get("configuration", {})
            sources = set(entry.get(
                "presence_sources", [configuration.get("presence_source", "ha_presence")],
            ))
            gestures = {
                variant.get("configuration", {}).get(
                    "button_session_gesture",
                    configuration.get("button_session_gesture", "long"),
                )
                for variant in entry.get("variants", [{}])
            }
            result[scenario["id"]] = {
                expectation["id"]: expectation
                for expectation in scenario["expectations"]
                if sources.intersection(expectation.get("presence_sources", sources))
                and gestures.intersection(expectation.get("gestures", gestures))
            }
    return result


class CoverageOwnershipTests(unittest.TestCase):
    def test_migrated_domain_checks_have_bound_evaluations_and_no_old_copy(self):
        ownership = json.loads(
            (TESTS / "coverage_ownership.json").read_text(encoding="utf-8")
        )
        evaluations = scenario_evaluations()
        sources = {}
        migrations = set()
        self.assertTrue(ownership["scenario_contracts"])
        for contract in ownership["scenario_contracts"]:
            with self.subTest(contract=contract["contract"]):
                for reference in (contract, *contract.get("additional_evaluations", [])):
                    self.assertIn(reference["scenario"], evaluations)
                    bound = evaluations[reference["scenario"]]
                    self.assertTrue(reference["expectation_ids"])
                    for identity in reference["expectation_ids"]:
                        self.assertIn(identity, bound)
                        self.assertTrue(bound[identity]["values"])
                        self.assertTrue(bound[identity]["reason"])
                self.assertTrue(contract["supersedes"])
                for previous in contract["supersedes"]:
                    key = (previous["file"], previous["class"], previous["method"])
                    self.assertNotIn(key, migrations, "Every migrated method has one owner")
                    migrations.add(key)
                    path = ROOT / previous["file"]
                    if "retained_boundary_method" in previous:
                        self.assertTrue(
                            path.is_file(),
                            "A retained boundary contract needs its test module",
                        )
                    if not path.exists():
                        continue
                    if path not in sources:
                        sources[path] = method_identities(path)
                    self.assertNotIn(
                        (previous["class"], previous["method"]), sources[path],
                        "The bound scenario owns this behavioural contract",
                    )
                    if "retained_boundary_method" in previous:
                        self.assertIn(
                            (previous["class"], previous["retained_boundary_method"]),
                            sources[path],
                            "A separately retained boundary check must remain executable",
                        )

    def test_component_duplicates_have_one_existing_owner(self):
        ownership = json.loads(
            (TESTS / "coverage_ownership.json").read_text(encoding="utf-8")
        )
        for contract in ownership["component_contracts"]:
            with self.subTest(contract=contract["contract"]):
                owner = contract["owner"]
                self.assertIn(
                    (owner["class"], owner["method"]),
                    method_identities(ROOT / owner["file"]),
                )
                for previous in contract["supersedes"]:
                    self.assertNotIn(
                        (previous["class"], previous["method"]),
                        method_identities(ROOT / previous["file"]),
                    )

    def test_boundary_contracts_name_existing_suites_and_distinct_purposes(self):
        ownership = json.loads(
            (TESTS / "coverage_ownership.json").read_text(encoding="utf-8")
        )
        for boundary in ownership["boundary_contracts"]:
            self.assertTrue(boundary["scope"])
            for name in boundary["files"]:
                with self.subTest(file=name):
                    self.assertTrue((ROOT / name).is_file())
