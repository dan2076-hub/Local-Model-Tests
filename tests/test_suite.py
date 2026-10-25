"""Test packaging and preparation without calling a model or running answers."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_model_tests as suite


class SuiteTests(unittest.TestCase):
    def test_original_core_is_complete_and_verified(self):
        result = suite.verify_repository()
        self.assertEqual(result, {"status": "VERIFIED", "cases": 10, "original_files": 46})

    def test_all_tasks_are_listed_without_executing_a_candidate(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(suite.main(["list"]), 0)
        self.assertEqual([row["case_id"] for row in json.loads(output.getvalue())], list(suite.CASE_IDS))

    def test_prepare_copies_only_the_selected_starter(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "F05"
            result = suite.prepare_case("F05", destination)
            expected = {p.relative_to(suite.ROOT / "seeds/F05").as_posix(): p.read_bytes()
                        for p in (suite.ROOT / "seeds/F05").rglob("*") if p.is_file()}
            actual = {p.relative_to(destination).as_posix(): p.read_bytes()
                      for p in destination.rglob("*") if p.is_file()}
            self.assertEqual(actual, expected)
            self.assertIn("BRIEF.md", result["files"])
            self.assertFalse((destination / "criteria").exists())

    def test_prepare_preserves_an_existing_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            sentinel = destination / "keep.txt"
            sentinel.write_bytes(b"existing work")
            with self.assertRaisesRegex(ValueError, "already exist"):
                suite.prepare_case("F01", destination)
            self.assertEqual(sentinel.read_bytes(), b"existing work")

    def test_altered_oracle_fails_integrity_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("cases", "criteria", "seeds"):
                shutil.copytree(suite.ROOT / name, base / name)
            shutil.copyfile(suite.ROOT / "provenance.json", base / "provenance.json")
            oracle = base / "criteria/F01.py"
            oracle.write_bytes(oracle.read_bytes() + b"\n# changed\n")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                suite.verify_repository(base)


if __name__ == "__main__":
    unittest.main()
