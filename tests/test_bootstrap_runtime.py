"""Regression tests for project Python runtime bootstrapping."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_runtime


class BootstrapRuntimeTests(unittest.TestCase):
    def test_subprocess_environment_removes_python_redirects(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PYTHONHOME": r"C:\LibreOffice\program\python-core",
                "PythonPath": r"C:\LibreOffice\program",
                "AI_OUTBRAIN_TEST_MARKER": "kept",
            },
            clear=True,
        ):
            env = bootstrap_runtime.subprocess_environment()

        self.assertNotIn("PYTHONHOME", {key.upper() for key in env})
        self.assertNotIn("PYTHONPATH", {key.upper() for key in env})
        self.assertEqual(env["AI_OUTBRAIN_TEST_MARKER"], "kept")

    @patch("bootstrap_runtime.subprocess.run")
    def test_python_health_check_uses_clean_environment(self, run) -> None:
        run.return_value.returncode = 0
        with patch.object(Path, "exists", return_value=True), patch.dict(
            os.environ,
            {"PYTHONHOME": "bad-home", "PYTHONPATH": "bad-path"},
            clear=False,
        ):
            healthy = bootstrap_runtime._python_executable_ready(
                Path(r"C:\project\.venv\Scripts\python.exe")
            )

        self.assertTrue(healthy)
        child_env = run.call_args.kwargs["env"]
        self.assertNotIn("PYTHONHOME", {key.upper() for key in child_env})
        self.assertNotIn("PYTHONPATH", {key.upper() for key in child_env})


if __name__ == "__main__":
    unittest.main()
