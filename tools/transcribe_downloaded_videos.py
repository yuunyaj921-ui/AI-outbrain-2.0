"""Batch-transcribe already downloaded Douyin videos without network access.

The project ASR command accepts audio files, while OpenSquilla stores MP4
videos.  This runner uses the project's FFmpeg extractor and transcript
exporter, loads faster-whisper once, and keeps an append-only manifest so an
interrupted run can resume safely.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from runtime_paths import portable_model_path, resolve_ffmpeg_path  # noqa: E402
from text_normalizer import to_simplified  # noqa: E402
from transcript_exporter import export_transcript  # noqa: E402
from pipeline import load_config  # noqa: E402


DEFAULT_DB = (
    Path.home()
    / "AppData"
    / "Roaming"
    / "@opensquilla"
    / "desktop-electron"
    / "opensquilla"
    / "workspace"
    / "douku"
    / "data"
    / "douku_v4.db"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "Obsidian" / "AI外脑知识库" / "00_Inbox" / "抖音链接"
DEFAULT_MANIFEST = PROJECT_ROOT / "runtime" / "batch_transcription" / "douyin_downloads.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--model-size", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def load_rows(db_path: Path, start: int, limit: int) -> list[sqlite3.Row]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        rows = connection.execute(
            """
            SELECT
                vb.aweme_id,
                vb.title,
                vb.desc,
                vb.create_time,
                vb.duration_sec,
                vb.share_url,
                vb.video_tags,
                ab.nickname,
                vc.content_category,
                vd.download_path
            FROM videos_base vb
            JOIN videos_download vd ON vd.aweme_id = vb.aweme_id
            LEFT JOIN authors_base ab ON ab.sec_uid = vb.author_sec_uid
            LEFT JOIN videos_classification vc ON vc.aweme_id = vb.aweme_id
            WHERE vd.status = 1 AND vd.download_path != ''
            ORDER BY vb.create_time DESC, vb.aweme_id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    selected = rows[start:]
    return selected if limit <= 0 else selected[:limit]


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        aweme_id = str(record.get("aweme_id") or "")
        if aweme_id:
            latest[aweme_id] = record
    return latest


def append_manifest(handle, record: dict[str, Any]) -> None:
    handle.write(json_line(record) + "\n")
    handle.flush()


def duration_label(value: Any) -> str:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return "N/A"
    if seconds <= 0:
        return "N/A"
    return f"{seconds // 60}:{seconds % 60:02d}"


def transcript_metadata(row: sqlite3.Row) -> dict[str, Any]:
    aweme_id = str(row["aweme_id"])
    title = (row["title"] or row["desc"] or "untitled").replace("\n", " ").strip()
    return {
        "title": f"{aweme_id} {title}",
        "author": row["nickname"] or "N/A",
        "original_url": row["share_url"] or row["download_path"],
        "duration": duration_label(row["duration_sec"]),
        "cover_url": "N/A",
        "source_id": aweme_id,
        "source_path": row["download_path"],
        "create_time": row["create_time"] or "N/A",
        "content_category": row["content_category"] or "未分类",
        "video_tags": row["video_tags"] or "[]",
    }


def existing_output(output_dir: Path, aweme_id: str) -> Path | None:
    candidates = sorted(output_dir.glob(f"{aweme_id} *.md"))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def extract_audio_compatible(ffmpeg_path: str, source: Path, target: Path) -> tuple[bool, str]:
    """Extract the existing AAC stream into an MP4/M4A audio container.

    The bundled FFmpeg is intentionally minimal and has no WAV muxer or AAC
    encoder.  Douyin files contain AAC audio, so stream-copying into an M4A
    container avoids re-encoding and works with faster-whisper's decoder.
    """
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-f",
            "mp4",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
        return True, ""
    if target.exists():
        target.unlink()
    return False, (result.stderr or result.stdout or "FFmpeg extraction failed").strip()[-1000:]


def transcribe_one(
    row: sqlite3.Row,
    model: Any,
    ffmpeg_path: str,
    output_dir: Path,
    temp_dir: Path,
    language: str | None,
) -> dict[str, Any]:
    started = time.monotonic()
    aweme_id = str(row["aweme_id"])
    source = Path(str(row["download_path"]))
    if not source.is_file():
        return {
            "aweme_id": aweme_id,
            "status": "failed",
            "stage": "acquisition",
            "error_code": "source_file_missing",
            "error": str(source),
        }

    temp_audio = temp_dir / f"{aweme_id}.m4a"
    try:
        extracted_ok, extract_error = extract_audio_compatible(ffmpeg_path, source, temp_audio)
        if not extracted_ok:
            return {
                "aweme_id": aweme_id,
                "status": "failed",
                "stage": "acquisition",
                "error_code": "audio_extract_failed",
                "error": extract_error,
            }

        segments_iter, _info = model.transcribe(str(temp_audio), language=language)
        text_parts: list[str] = []
        segment_count = 0
        for segment in segments_iter:
            segment_text = (getattr(segment, "text", "") or "").strip()
            if segment_text:
                text_parts.append(segment_text)
            segment_count += 1
        transcript = to_simplified("\n".join(text_parts))
        paths = export_transcript(
            transcript_metadata(row),
            transcript,
            "faster_whisper",
            "md",
            str(output_dir),
        )
        return {
            "aweme_id": aweme_id,
            "status": "success",
            "stage": "processing",
            "md_path": paths[0] if paths else "",
            "transcript_chars": len(transcript),
            "segment_count": segment_count,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    except Exception as exc:  # keep the batch moving after one bad file
        return {
            "aweme_id": aweme_id,
            "status": "failed",
            "stage": "processing",
            "error_code": "transcription_failed",
            "error": str(exc),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    finally:
        try:
            if temp_audio.is_file():
                temp_audio.unlink()
        except OSError:
            pass


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        print(json_line({"success": False, "error": f"database not found: {args.db}"}))
        return 1

    rows = load_rows(args.db, max(0, args.start), max(0, args.limit))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = PROJECT_ROOT / "runtime" / "batch_transcription" / "audio_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    manifest = {} if args.no_resume else load_manifest(args.manifest)

    config = load_config(PROJECT_ROOT)
    asr_config = config.get("faster_whisper", {})
    model_size = args.model_size or asr_config.get("model_size", "base")
    device = args.device or asr_config.get("device", "cpu")
    compute_type = args.compute_type or asr_config.get("compute_type", "int8")
    language = args.language if args.language is not None else asr_config.get("language", "zh")

    try:
        from faster_whisper import WhisperModel

        model_path = portable_model_path(model_size)
        model_source = str(model_path) if model_path.is_dir() else model_size
        print(json_line({
            "event": "model_loading",
            "model": model_source,
            "device": device,
            "compute_type": compute_type,
            "items": len(rows),
        }), flush=True)
        model = WhisperModel(model_source, device=device, compute_type=compute_type)
    except Exception as exc:
        print(json_line({"success": False, "stage": "processing", "error_code": "model_load_failed", "error": str(exc)}))
        return 1

    ffmpeg_path = resolve_ffmpeg_path()
    succeeded = 0
    failed = 0
    skipped = 0
    started = time.monotonic()

    with args.manifest.open("a", encoding="utf-8") as handle:
        for index, row in enumerate(rows, start=1):
            aweme_id = str(row["aweme_id"])
            prior = manifest.get(aweme_id)
            if prior and prior.get("status") in {"success", "skipped_existing"}:
                skipped += 1
                continue
            prior_path = existing_output(args.output_dir, aweme_id)
            if prior_path is not None:
                record = {
                    "aweme_id": aweme_id,
                    "status": "skipped_existing",
                    "md_path": str(prior_path),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                append_manifest(handle, record)
                manifest[aweme_id] = record
                skipped += 1
                continue

            record = transcribe_one(
                row,
                model,
                ffmpeg_path,
                args.output_dir,
                temp_dir,
                language.strip() or None if isinstance(language, str) else None,
            )
            record["timestamp"] = datetime.now().isoformat(timespec="seconds")
            append_manifest(handle, record)
            manifest[aweme_id] = record
            if record["status"] == "success":
                succeeded += 1
            else:
                failed += 1
            if index == 1 or index % 10 == 0 or record["status"] == "failed":
                print(json_line({
                    "event": "progress",
                    "index": index,
                    "total": len(rows),
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                    "last": record,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }), flush=True)

    print(json_line({
        "success": failed == 0,
        "event": "complete",
        "total_selected": len(rows),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "manifest": str(args.manifest),
        "output_dir": str(args.output_dir),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }), flush=True)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
