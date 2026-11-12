import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from extract_diagnostic_facts import extract, render


def doc(*texts):
    return {"observations": [{"id": f"E{i}", "text": text}
                             for i, text in enumerate(texts, 1)]}


class DiagnosticFactsTests(unittest.TestCase):
    def test_authentication_is_not_config_root_cause(self):
        text = 'FATAL: password authentication failed for user "demo"\nDETAIL: Connection matched file "pg_hba.conf" line 128'
        result = extract(doc(text))
        self.assertEqual([f["signature"] for f in result["facts"]], ["password_authentication_failed"])
        evidence = result["facts"][0]["evidence"][0]
        self.assertEqual(text[evidence["start_char"]:evidence["end_char"]], evidence["quote"])
        self.assertIn("Не установлено:", render(result))

    def test_http_failure_does_not_infer_database_shutdown(self):
        result = extract(doc("GET /health/ready — 503.\nGET /tasks — 500."))
        self.assertEqual(result["facts"], [])

    def test_dns_failure_keeps_specific_error(self):
        result = extract(doc("psycopg.OperationalError: failed to resolve host 'db'"))
        self.assertEqual(result["facts"][0]["signature"], "host_name_resolution_failed")

    def test_missing_relation_and_permissions_are_distinct(self):
        result = extract(doc('ERROR: relation "tasks" does not exist',
                             'psycopg.errors.InsufficientPrivilege: permission denied for table tasks'))
        self.assertEqual({f["signature"] for f in result["facts"]},
                         {"relation_not_found", "table_permission_denied"})

    def test_multiple_attempts_preserve_evidence(self):
        result = extract(doc('FATAL: password authentication failed for user "a"',
                             'FATAL: password authentication failed for user "b"',
                             "failed to resolve host 'db'"))
        self.assertEqual(len(result["facts"]), 2)
        self.assertEqual(len(result["facts"][0]["evidence"]), 2)

    def test_file_name_alone_is_not_an_error(self):
        self.assertEqual(extract(doc('Connection matched file "pg_hba.conf"'))["facts"], [])

    def test_duplicate_ids_rejected(self):
        value = doc("one", "two")
        value["observations"][1]["id"] = "E1"
        with self.assertRaises(ValueError):
            extract(value)

    def test_conflicting_text_rejected(self):
        value = doc("one")
        value["observations"][0]["raw_text"] = "two"
        with self.assertRaises(ValueError):
            extract(value)

    def test_raw_text_supported(self):
        result = extract({"observations": [{"id": "E5", "raw_text":
                         'FATAL: password authentication failed for user "test"'}]})
        self.assertEqual(len(result["facts"]), 1)


if __name__ == "__main__":
    unittest.main()
