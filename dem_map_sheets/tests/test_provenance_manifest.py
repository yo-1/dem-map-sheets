"""PR-3 provenance checks; uses only temporary files."""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dem_map_sheets.engine.provenance import build_provenance, provenance_warnings


class TestProvenance(unittest.TestCase):
    def test_original_and_outputs_hash(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.zip"
            source.write_bytes(b"original")
            out = root / "run"
            out.mkdir()
            merged = out / "02_merged_dem.tif"
            merged.write_bytes(b"dem")
            (out / "run.json").write_text("{}")
            work = out / "work"
            work.mkdir()
            (work / "scratch.dat").write_bytes(b"scratch")
            record = build_provenance([str(source)], out)
            self.assertEqual(record["inputs"][0]["sha256"], hashlib.sha256(b"original").hexdigest())
            self.assertEqual(len(record["outputs"]), 1)
            self.assertEqual(record["outputs"][0]["sha256"], hashlib.sha256(b"dem").hexdigest())
            self.assertEqual(record["outputs"][0]["kind"], "merged_dem")
            self.assertEqual(provenance_warnings(record), [])
            source.write_bytes(b"Original")
            changed = build_provenance([str(source)], out)
            self.assertNotEqual(record["inputs"][0]["sha256"], changed["inputs"][0]["sha256"])

    def test_directory_and_remote_unavailable(self):
        with TemporaryDirectory() as td:
            record = build_provenance([td, "https://example.test/file?token=secret"], td)
            self.assertEqual(record["inputs"][0]["hash_status"], "unavailable")
            self.assertEqual(record["inputs"][1]["path"], "remote_or_virtual_input")
            self.assertNotIn("secret", str(record))
            self.assertIn("provenance_hash_unavailable", provenance_warnings(record))

    def test_partial_outputs_are_not_complete(self):
        with TemporaryDirectory() as td:
            out = Path(td)
            (out / "02_merged_dem.tif").write_bytes(b"partial")
            record = build_provenance([], out, complete=False)
            self.assertFalse(record["outputs"][0]["complete"])


if __name__ == "__main__":
    unittest.main()
