import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_time import audit_observation, classify, timestamp_interval


class ContextTimeTests(unittest.TestCase):
    cutoff = "2026-11-02T18:33:03Z"

    def test_event_and_collection_before(self):
        result = audit_observation(
            "2026-11-02T18:32:58.910617163Z",
            "2026-11-02T18:33:01Z",
            self.cutoff,
        )
        self.assertTrue(result["strict_eligible"])

    def test_future_event(self):
        self.assertEqual(
            classify("2026-11-02T18:33:04Z", self.cutoff),
            "at_or_after",
        )

    def test_late_collection_is_not_eligible(self):
        result = audit_observation(
            "2026-11-02T18:32:58Z",
            "2026-11-02T18:33:04Z",
            self.cutoff,
        )
        self.assertEqual(result["event_time_status"], "before")
        self.assertFalse(result["strict_eligible"])

    def test_interval_overlaps_boundary(self):
        self.assertEqual(
            classify(
                "2026-11-02T18:33:02Z",
                "2026-11-02T18:33:02.500Z",
            ),
            "overlaps",
        )

    def test_exact_boundary_is_excluded(self):
        self.assertEqual(classify(self.cutoff, self.cutoff), "at_or_after")

    def test_unknown_collection_is_not_eligible(self):
        result = audit_observation(
            "2026-11-02T18:32:58Z", None, self.cutoff
        )
        self.assertEqual(result["collection_time_status"], "missing")
        self.assertFalse(result["strict_eligible"])

    def test_invalid_timestamps(self):
        for value in (
            "2026-11-02T18:32:58",
            "2026-02-30T18:32:58Z",
            "2026-11-02T18:32:58+03:99",
            "not-a-date",
        ):
            with self.subTest(value=value):
                self.assertEqual(classify(value, self.cutoff), "invalid")

    def test_nanosecond_difference_is_preserved(self):
        first, _ = timestamp_interval("2026-11-02T18:32:58.910617163Z")
        second, _ = timestamp_interval("2026-11-02T18:32:58.910617164Z")
        self.assertEqual(second - first, 1)
        self.assertEqual(
            classify(
                "2026-11-02T18:32:58.910617163Z",
                "2026-11-02T18:32:58.910617164Z",
            ),
            "before",
        )

    def test_equivalent_timezones(self):
        self.assertEqual(
            timestamp_interval("2026-11-02T21:32:58.123+03:00"),
            timestamp_interval("2026-11-02T18:32:58.123Z"),
        )

    def test_invalid_cutoff_stops_audit(self):
        with self.assertRaises(ValueError):
            audit_observation(
                "2026-11-02T18:32:58Z", None, "invalid"
            )


if __name__ == "__main__":
    unittest.main()
