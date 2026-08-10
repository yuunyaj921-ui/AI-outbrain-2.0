"""Regression checks preventing credentials from entering Python source."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRS = {".git", ".venv", ".ruff_cache", "__pycache__", "runtime"}
SENSITIVE_NAMES = {"api_key", "access_token", "secret", "secret_key", "token"}


def _python_sources() -> list[Path]:
    return [
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not any(
            part in SKIPPED_DIRS or part.startswith(".venv") for part in path.parts
        )
    ]


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id.lower() for target in targets if isinstance(target, ast.Name)]


def test_python_source_has_no_literal_credentials() -> None:
    findings: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            names = _assigned_names(node)
            if not any(
                name in SENSITIVE_NAMES
                or any(name.endswith(f"_{suffix}") for suffix in SENSITIVE_NAMES)
                for name in names
            ):
                continue
            literal = value.value.strip()
            if len(literal) >= 16 and not literal.startswith(("<", "${")):
                findings.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
    assert findings == [], "literal credentials found in: " + ", ".join(findings)
