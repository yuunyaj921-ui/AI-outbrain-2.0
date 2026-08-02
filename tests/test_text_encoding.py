"""Regression tests for maintained-text scan exclusions."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_text_encoding import should_scan


class TextEncodingScannerTests(unittest.TestCase):
    def test_broken_virtual_environment_is_excluded(self) -> None:
        self.assertFalse(
            should_scan(Path(".venv.broken-20260729-022545/Lib/site-packages/bad.py"))
        )

    def test_project_source_is_still_scanned(self) -> None:
        self.assertTrue(should_scan(Path("src/pipeline.py")))


if __name__ == "__main__":
    unittest.main()
