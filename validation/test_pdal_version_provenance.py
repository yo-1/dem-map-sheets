"""Regression tests for Windows and Linux PDAL version output (Issue #22)."""
import unittest
from dem_map_sheets.engine.provenance import _extract_pdal_version, collect_environment


class PdalVersionTests(unittest.TestCase):
    def test_windows_multiline(self):
        output = "----------------------\\npdal 2.10.0 (git-version: eb9126)\\n----------------------"
        self.assertEqual(_extract_pdal_version(output), "2.10.0")
        self.assertEqual(collect_environment(input_report={"pdal_version": output})["pdal"], "2.10.0")

    def test_linux_single_line(self):
        self.assertEqual(_extract_pdal_version("PDAL 2.9.1"), "2.9.1")

    def test_unavailable(self):
        for value in ("-----", "PDAL", "command not found", "", "pdal /home/user/secret"):
            with self.subTest(value=value):
                self.assertIsNone(_extract_pdal_version(value))
                self.assertNotIn("pdal", collect_environment(input_report={"pdal_version": value}))

    def test_no_leakage(self):
        value = "-----\\npdal 2.10.0 (git-version: abc) /Users/alice/token\\n"
        self.assertEqual(_extract_pdal_version(value), "2.10.0")
        self.assertNotIn("alice", collect_environment(input_report={"pdal_version": value})["pdal"])
        self.assertIsNone(_extract_pdal_version("pdal 2.10.0evil /tmp/private"))


if __name__ == "__main__":
    unittest.main()
