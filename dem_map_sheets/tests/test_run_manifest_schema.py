"""PR-2 schema compatibility tests; no QGIS runtime required."""
import unittest
from dem_map_sheets.engine.run_manifest import (
    SCHEMA_VERSION, ensure_manifest_schema, read_manifest_schema,
)

class TestRunManifestSchema(unittest.TestCase):
    def test_new_run_is_not_evaluated(self):
        record = ensure_manifest_schema({"status": "completed", "warnings": []})
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["qa_status"], "not_evaluated")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["warnings"], [])

    def test_failed_and_cancelled_keep_processing_status(self):
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                self.assertEqual(ensure_manifest_schema({"status": status})["status"], status)

    def test_legacy_read_does_not_mutate(self):
        old = {"status": "completed"}
        normalized = read_manifest_schema(old)
        self.assertEqual(old, {"status": "completed"})
        self.assertEqual(normalized["qa_status"], "not_evaluated")

    def test_unknown_qa_is_not_silently_passed(self):
        with self.assertRaises(ValueError):
            read_manifest_schema({"qa_status": "approved"})

    def test_unknown_schema_rejected(self):
        with self.assertRaises(ValueError):
            read_manifest_schema({"schema_version": 2})

    def test_existing_qa_preserved(self):
        self.assertEqual(read_manifest_schema({"qa_status": "fail"})["qa_status"], "fail")

if __name__ == "__main__":
    unittest.main()
