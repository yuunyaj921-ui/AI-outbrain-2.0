"""Wait for the four transcription shards, then build review-only candidates."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "batch_transcription"
LOG = RUNTIME / "classification_watcher.log"
EXPECTED = 1734


def write_log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def manifest_lines() -> tuple[int, list[Path]]:
    paths = sorted(RUNTIME.glob("douyin_downloads_shard*.jsonl"))
    count = 0
    for path in paths:
        try:
            count += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError as exc:
            write_log(f"read failed path={path} error={exc}")
    return count, paths


def main() -> int:
    write_log("watcher started")
    while True:
        count, paths = manifest_lines()
        if count >= EXPECTED and len(paths) == 4:
            break
        time.sleep(30)
    command = [
        str(ROOT / ".venv" / "Scripts" / "python.exe"),
        "-X",
        "utf8",
        "tools/build_classification_candidates.py",
    ]
    for path in paths:
        command.extend(["--manifest", str(path)])
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    write_log(f"classification exit={result.returncode} stdout={result.stdout.strip()} stderr={result.stderr.strip()}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
