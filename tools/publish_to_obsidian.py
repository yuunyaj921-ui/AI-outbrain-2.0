#!/usr/bin/env python3
"""Publish classification candidates to Obsidian 审核门 via Local REST API.

Usage:
    # Dry-run: preview 3 knowledge-type candidates without writing
    python tools/publish_to_obsidian.py --dry-run --limit 3

    # Write all knowledge-type candidates (not 00_Inbox/抖音链接)
    python tools/publish_to_obsidian.py

    # Write only specific bucket
    python tools/publish_to_obsidian.py --bucket "01_AI术语库"

    # Skip candidates with short transcripts
    python tools/publish_to_obsidian.py --min-transcript-chars 100
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = PROJECT_ROOT / "runtime" / "batch_transcription" / "classification_candidates.jsonl"
DEFAULT_BASE = os.environ.get("OBSIDIAN_API_URL", "https://127.0.0.1:27124")

API_KEY = os.environ.get(
    "OBSIDIAN_API_KEY",
    "7d5fae81891539d8d79eaf97d3891fc42bb5389204af980447eb49564ccb8e4b",
)

# ── template ──────────────────────────────────────────────────────────────

CARD_TEMPLATE = """\
---
tags:
  - {bucket_tag}
  - 待审核
created: {created_date}
source: {source_url}
douyin_author: {author}
douyin_aweme_id: {aweme_id}
douyin_create_time: {douyin_create_time}
source_category: {source_category}
review_status: pending
---

# {title}

## 📌 一句话总结
{one_liner}

## 🏷️ 类型
{card_type}

## 🔑 关键知识点
1. 
2. 
3. 
4. 
5. 

## 🎯 适用场景
- 
- 
- 

## 🛠️ 可用工具 / 模型
- 
- 
- 

## 🔗 可迁移到我的项目
- [ ] 
- [ ] 
- [ ] 

## 💬 我的理解
> 待审核后补充

## 📤 可输出内容
- 选题方向：
- 讨论话题：
- 商业切入点：

## ➡️ 后续行动
- [ ] 值得深入研究
- [ ] 可以做成内容输出
- [ ] 可以做成产品功能
- [ ] 可以做成商业案例
- [ ] 暂时收藏观察

## 🏗️ 关联知识
- [[相关术语]]
- [[相关模型]]
- [[相关工具]]
- [[相关工作流]]
- [[相关案例]]

---

## 📝 原始文字稿
{transcript_body}
"""

BUCKET_TAG_MAP = {
    "01_AI术语库": "术语",
    "02_模型能力库": "模型",
    "03_AI工具库": "工具",
    "04_工作流库": "工作流",
    "05_智能体库": "智能体",
}


# ── API helpers ───────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def encode_vault_path(path: str) -> str:
    """Percent-encode a vault path, preserving slashes."""
    parts = path.split("/")
    return "/vault/" + "/".join(urllib.parse.quote(p, safe="") for p in parts)


def api_call(method: str, path: str, *, data: Any = None, content_type: str | None = None) -> tuple[int, str]:
    """Make an HTTP request to the Obsidian Local REST API."""
    url = DEFAULT_BASE + path
    headers = {"Authorization": f"Bearer {API_KEY}"}
    body = None

    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = content_type or "application/vnd.olrapi.note+json"
        elif isinstance(data, str):
            body = data.encode("utf-8")
            headers["Content-Type"] = content_type or "text/markdown"
        elif isinstance(data, bytes):
            body = data
            headers["Content-Type"] = content_type or "text/markdown"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


# ── card builder ───────────────────────────────────────────────────────────

def extract_transcript_body(note_path: Path) -> str:
    """Extract the 文字稿 section from an existing Inbox note."""
    try:
        text = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    idx = text.find("## 文字稿")
    if idx == -1:
        return ""
    body = text[idx + len("## 文字稿"):].strip()
    return body


def build_card(record: dict, transcript_body: str) -> str:
    """Build a knowledge card from a candidate record."""
    bucket = record.get("suggested_vault_bucket", "00_Inbox")
    tag = BUCKET_TAG_MAP.get(bucket, "待分类")
    card_type = BUCKET_TAG_MAP.get(bucket, "待分类")

    title = record.get("title", record.get("aweme_id", ""))
    author = record.get("author", "未知")
    aweme_id = record.get("aweme_id", "")
    source_url = record.get("source_url", "")
    source_category = record.get("source_category", "未知")
    douyin_create_time = record.get("source_create_time", "")
    created_date = datetime.now().strftime("%Y-%m-%d")

    one_liner = f"抖音视频「{title}」的转写内容，来自 {author}，分类建议：{card_type}"

    return CARD_TEMPLATE.format(
        bucket_tag=tag,
        created_date=created_date,
        source_url=source_url,
        author=author,
        aweme_id=aweme_id,
        douyin_create_time=douyin_create_time,
        source_category=source_category,
        title=title,
        one_liner=one_liner,
        card_type=card_type,
        transcript_body=transcript_body,
    )


def safe_filename(title: str) -> str:
    """Sanitize title for use as filename."""
    for ch in '/\\:*?"<>|':
        title = title.replace(ch, " ")
    return title.strip()[:80]


# ── main ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of candidates (0 = all)")
    parser.add_argument("--bucket", type=str, help="Filter by specific vault bucket")
    parser.add_argument("--min-transcript-chars", type=int, default=0, help="Skip candidates with transcript body < N chars")
    parser.add_argument("--jsonl", type=Path, default=CANDIDATES_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.jsonl.exists():
        print(f"ERROR: candidates file not found: {args.jsonl}")
        return 1

    records = [json.loads(l) for l in args.jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    knowledge = [r for r in records if r.get("suggested_vault_bucket") != "00_Inbox/抖音链接"]

    if args.bucket:
        knowledge = [r for r in knowledge if r.get("suggested_vault_bucket") == args.bucket]
    if args.limit > 0:
        knowledge = knowledge[:args.limit]

    print(f"Total candidates: {len(records)}")
    print(f"Knowledge-type: {len(knowledge)}")
    if args.min_transcript_chars:
        print(f"Min transcript chars filter: {args.min_transcript_chars}")
    if args.dry_run:
        print("MODE: DRY RUN (no writes)")
    print()

    written = 0
    skipped_low_quality = 0
    skipped_no_transcript = 0
    errors = 0

    for i, record in enumerate(knowledge):
        aweme_id = record["aweme_id"]
        bucket = record["suggested_vault_bucket"]
        title = record.get("title", "")
        note_filename = f"{aweme_id} {safe_filename(title)}.md"

        source_path = Path(record.get("source_transcript_path", ""))
        transcript_body = ""
        if source_path.exists():
            transcript_body = extract_transcript_body(source_path)

        if args.min_transcript_chars > 0 and len(transcript_body) < args.min_transcript_chars:
            skipped_low_quality += 1
            if args.dry_run or i < 3:
                print(f"  SKIP [{bucket}] {title[:50]} (transcript={len(transcript_body)} chars, min={args.min_transcript_chars})")
            continue

        if not transcript_body.strip():
            skipped_no_transcript += 1
            if args.dry_run or i < 3:
                print(f"  SKIP [{bucket}] {title[:50]} (no transcript body)")
            continue

        card = build_card(record, transcript_body)
        vault_path = f"00_Inbox/_待审核/{bucket}/{note_filename}"

        if args.dry_run:
            print(f"  DRY-RUN [{i+1}/{len(knowledge)}] -> {vault_path}")
            print(f"    Title: {title[:60]}")
            print(f"    Transcript chars: {len(transcript_body)}")
            print(f"    Card size: {len(card)} bytes")
            # Show first 300 chars of card
            print(f"    Card preview:\n{card[:300]}...")
            written += 1
            continue

        # Write via REST API
        encoded_path = encode_vault_path(vault_path)
        status, body = api_call("PUT", encoded_path, data=card)

        if status in (200, 204):
            written += 1
            if i < 3 or written % 20 == 0:
                print(f"  [{written}/{len(knowledge)}] OK {bucket}/{note_filename[:60]}")
        else:
            errors += 1
            print(f"  ERROR [{i+1}] {bucket}/{note_filename[:60]}: status={status} {body[:200]}")

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total candidates:         {len(records)}")
    print(f"  Knowledge-type:           {len(knowledge)}")
    print(f"  Written (or dry-run):     {written}")
    print(f"  Skipped (low quality):    {skipped_low_quality}")
    print(f"  Skipped (no transcript):  {skipped_no_transcript}")
    print(f"  Errors:                   {errors}")
    if args.dry_run:
        print("  MODE: DRY RUN - no actual writes performed")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
