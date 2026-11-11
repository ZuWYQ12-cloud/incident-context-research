import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_sources import validate_sources


class ContextSourcesTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "sources": [{
                "id": "api_log",
                "kind": "application_log",
                "component": "api",
                "availability": "provided",
                "representation": "selected_excerpts",
                "completeness": "unknown",
                "coverage_interval": None,
                "coverage_basis": "test_fixture",
                "note": "Selected messages only.",
            }],
            "observations": [{"id": "E1", "source_id": "api_log"}],
        }

    def test_valid_mapping(self):
        result = validate_sources(self.data)
        self.assertEqual(result["api_log"]["observation_ids"], ["E1"])
        self.assertEqual(result["api_log"]["completeness"], "unknown")

    def test_unknown_source(self):
        self.data["observations"][0]["source_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown source"):
            validate_sources(self.data)

    def test_duplicate_source(self):
        self.data["sources"].append(copy.deepcopy(self.data["sources"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate source"):
            validate_sources(self.data)

    def test_duplicate_observation(self):
        self.data["observations"].append(
            copy.deepcopy(self.data["observations"][0])
        )
        with self.assertRaisesRegex(ValueError, "Duplicate observation"):
            validate_sources(self.data)

    def test_absent_source_cannot_have_observations(self):
        source = self.data["sources"][0]
        source["availability"] = "not_provided"
        source["representation"] = "none"
        with self.assertRaisesRegex(ValueError, "marked not_provided"):
            validate_sources(self.data)

    def test_absent_source_without_observations(self):
        source = self.data["sources"][0]
        source["availability"] = "not_provided"
        source["representation"] = "none"
        self.data["observations"] = []
        result = validate_sources(self.data)
        self.assertEqual(result["api_log"]["observation_ids"], [])
        self.assertEqual(result["api_log"]["completeness"], "unknown")

    def test_complete_claim_is_rejected(self):
        self.data["sources"][0]["completeness"] = "complete"
        with self.assertRaisesRegex(ValueError, "completeness claim"):
            validate_sources(self.data)

    def test_provided_source_without_observations(self):
        self.data["observations"] = []
        with self.assertRaisesRegex(ValueError, "has no observations"):
            validate_sources(self.data)


if __name__ == "__main__":
    unittest.main()
