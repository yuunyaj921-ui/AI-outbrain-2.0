"""Build review-only classification candidates for downloaded transcripts.

This file deliberately writes outside the formal Obsidian category folders.
Final cards and index updates require the project's Obsidian MCP review gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
DEFAULT_RUNTIME = PROJECT_ROOT / "runtime" / "batch_transcription"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--manifest",
        type=Path,
        action="append",
        default=None,
        help="Manifest JSONL; may be repeated.",
    )
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_RUNTIME / "classification_candidates.jsonl")
    parser.add_argument("--output-report", type=Path, default=DEFAULT_RUNTIME / "classification_candidates.md")
    return parser.parse_args()


def load_latest_records(paths: list[Path]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            aweme_id = str(record.get("aweme_id") or "")
            if aweme_id:
                records[aweme_id] = record
    return records


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f#]', "_", value or "untitled")
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:80] or "untitled"


def suggest_vault_bucket(title: str, text: str, source_category: str) -> str:
    haystack = f"{title} {text}".lower()
    if any(term in haystack for term in ("智能体", "agent", "工作助手")):
        return "05_智能体库"
    if any(term in haystack for term in ("工作流", "自动化", "流程", "workflow")):
        return "04_工作流库"
    if any(term in haystack for term in ("工具", "软件", "codex", "github", "插件", "app")):
        return "03_AI工具库"
    if any(term in haystack for term in ("模型", "gpt", "claude", "qwen", "llm", "whisper")):
        return "02_模型能力库"
    if source_category == "知识" or any(
        term in haystack for term in ("提示词", "prompt", "人工智能", "ai", "编程", "python")
    ):
        return "01_AI术语库"
    return "00_Inbox/抖音链接"


def main() -> int:
    args = parse_args()
    manifest_paths = args.manifest or [DEFAULT_RUNTIME / "douyin_downloads.jsonl", DEFAULT_RUNTIME / "douyin_downloads_part2.jsonl"]
    latest = load_latest_records(manifest_paths)
    connection = sqlite3.connect(str(args.db))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT vb.aweme_id, vb.title, vb.desc, vb.share_url, vb.create_time,
                   ab.nickname, vc.content_category
            FROM videos_base vb
            LEFT JOIN authors_base ab ON ab.sec_uid = vb.author_sec_uid
            LEFT JOIN videos_classification vc ON vc.aweme_id = vb.aweme_id
            """
        ).fetchall()
    finally:
        connection.close()
    db_rows = {str(row["aweme_id"]): row for row in rows}

    candidates: list[dict[str, Any]] = []
    for aweme_id, record in sorted(latest.items()):
        if record.get("status") not in {"success", "skipped_existing"}:
            continue
        row = db_rows.get(aweme_id)
        if row is None:
            continue
        title = str(row["title"] or row["desc"] or aweme_id).replace("\n", " ").strip()
        source_category = str(row["content_category"] or "未分类")
        transcript_path = str(record.get("md_path") or "")
        bucket = suggest_vault_bucket(title, str(row["desc"] or ""), source_category)
        target_dir = "00_Inbox/_待审核" if bucket.startswith("00_Inbox") else f"00_Inbox/_待审核/{bucket}"
        candidates.append(
            {
                "aweme_id": aweme_id,
                "title": title,
                "author": row["nickname"] or "N/A",
                "source_category": source_category,
                "suggested_vault_bucket": bucket,
                "suggested_target_path": f"{target_dir}/{aweme_id} {safe_name(title)}.md",
                "source_transcript_path": transcript_path,
                "source_url": row["share_url"] or "",
                "source_create_time": row["create_time"] or "",
                "review_status": "pending_mcp_review",
                "classification_source": "DouKu content_category + title/description keyword suggestion",
                "transcript_chars": record.get("transcript_chars", 0),
            }
        )

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output_jsonl.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates),
        encoding="utf-8",
    )
    counts = Counter(item["suggested_vault_bucket"] for item in candidates)
    source_counts = Counter(item["source_category"] for item in candidates)
    report_lines = [
        "# 抖音转写分类候选（待 MCP 审核）",
        "",
        f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "> 状态：仅为候选，不代表已创建知识卡或完成正式归档。",
        "> 正式写入分类目录、移动文件和更新索引必须经过 Obsidian MCP 与用户审核。",
        "",
        f"- 成功转写候选：{len(candidates)}",
        f"- JSONL 明细：`{args.output_jsonl}`",
        "",
        "## 建议目标桶统计",
        "",
    ]
    report_lines.extend(f"- {bucket}：{count}" for bucket, count in counts.most_common())
    report_lines.extend(["", "## 原始 DouKu 分类统计", ""])
    report_lines.extend(f"- {category}：{count}" for category, count in source_counts.most_common())
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "success": True,
                "candidates": len(candidates),
                "output_jsonl": str(args.output_jsonl),
                "output_report": str(args.output_report),
                "review_status": "pending_mcp_review",
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
