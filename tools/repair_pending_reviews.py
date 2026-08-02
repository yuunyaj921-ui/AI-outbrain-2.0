#!/usr/bin/env python3
"""Repair legacy pending cards and register them in the review state machine.

By default this command is a dry run. ``--apply`` registers all matching legacy
cards as ``pending_draft`` and enriches only the highest-priority batch. A card
is moved to ``awaiting_approval`` only after the generated draft passes the
local completeness check. No formal Vault category or index is ever written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agent.card_drafts import (
    assess_card,
    extract_transcript_body,
    render_card_draft,
)
from agent.reviews import (
    create_review_record,
    handle_review_draft_ready,
    load_review_record,
    save_review_record,
)

CANDIDATES_PATH = (
    PROJECT_ROOT / "runtime" / "batch_transcription" / "classification_candidates.jsonl"
)
VAULT_ROOT = PROJECT_ROOT / "Obsidian" / "AI外脑知识库"
PENDING_ROOT = VAULT_ROOT / "00_Inbox" / "_待审核"
TRANSCRIPT_ROOT = VAULT_ROOT / "00_Inbox" / "抖音链接"
REVIEW_DIR = PROJECT_ROOT / ".workflow" / "reviews"

CATEGORY_INDEX = {
    "01_AI术语库": "_术语库索引.md",
    "02_模型能力库": "_模型库索引.md",
    "03_AI工具库": "_工具库索引.md",
    "04_工作流库": "_工作流库索引.md",
    "05_智能体库": "_智能体库索引.md",
}

CATEGORY_WEIGHT = {
    "05_智能体库": 50,
    "04_工作流库": 45,
    "03_AI工具库": 40,
    "02_模型能力库": 35,
    "01_AI术语库": 30,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist repair results")
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=20,
        help="Highest-priority drafts to enrich (default: 20)",
    )
    parser.add_argument(
        "--min-transcript-chars",
        type=int,
        default=300,
        help="Minimum transcript length before a draft can enter approval",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def load_candidates(path: Path = CANDIDATES_PATH) -> list[dict[str, Any]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            source_id = str(item.get("aweme_id") or "")
            if source_id:
                records_by_id[source_id] = item

    for draft_path in PENDING_ROOT.rglob("*.md"):
        match = re.match(r"(\d{12,})\s+", draft_path.name)
        if not match:
            continue
        source_id = match.group(1)
        text = draft_path.read_text(encoding="utf-8")
        record = dict(records_by_id.get(source_id) or {})
        record["aweme_id"] = source_id
        record["_draft_path"] = str(draft_path)
        record["suggested_vault_bucket"] = draft_path.parent.name
        record["suggested_target_path"] = str(
            draft_path.relative_to(VAULT_ROOT)
        ).replace(os.sep, "/")
        heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        record.setdefault("title", heading.group(1) if heading else draft_path.stem)
        for key in ("source", "douyin_author", "source_category", "douyin_create_time"):
            value_match = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", text)
            if not value_match:
                continue
            raw = value_match.group(1).strip()
            try:
                value = json.loads(raw) if raw.startswith(('"', "'")) else raw
            except json.JSONDecodeError:
                value = raw.strip("\"'")
            field = {
                "source": "source_url",
                "douyin_author": "author",
                "source_category": "source_category",
                "douyin_create_time": "source_create_time",
            }[key]
            record.setdefault(field, value)
        source_matches = list(TRANSCRIPT_ROOT.glob(f"{source_id} *.md"))
        if source_matches:
            record["source_transcript_path"] = str(source_matches[0])
        records_by_id[source_id] = record
    return list(records_by_id.values())


def _draft_path(record: dict[str, Any]) -> Path:
    if record.get("_draft_path"):
        return Path(str(record["_draft_path"]))
    relative = str(record.get("suggested_target_path") or "").replace("/", os.sep)
    return VAULT_ROOT / relative


def _source_path(record: dict[str, Any]) -> Path:
    return Path(str(record.get("source_transcript_path") or ""))


def _priority(record: dict[str, Any]) -> int:
    bucket = str(record.get("suggested_vault_bucket") or "")
    transcript_chars = int(record.get("transcript_chars") or 0)
    keyword_bonus = 0
    title = str(record.get("title") or "").lower()
    for term in (
        "agent",
        "智能体",
        "工作流",
        "自动化",
        "codex",
        "github",
        "网站",
        "工具",
    ):
        if term in title:
            keyword_bonus += 4
    return (
        CATEGORY_WEIGHT.get(bucket, 0)
        + min(30, transcript_chars // 300)
        + keyword_bonus
    )


def _existing_reviews() -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    if not REVIEW_DIR.exists():
        return existing
    for path in REVIEW_DIR.glob("*.json"):
        record = load_review_record(REVIEW_DIR, path.stem)
        if not record:
            continue
        source_id = str(record.get("source_id") or "")
        draft_path = str(record.get("draft_path") or "")
        if source_id:
            existing[f"id:{source_id}"] = record
        if draft_path:
            existing[f"path:{Path(draft_path)}"] = record
    return existing


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip() + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _register(
    candidate: dict[str, Any],
    existing: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    draft_path = _draft_path(candidate)
    source_id = str(candidate.get("aweme_id") or "")
    record = existing.get(f"id:{source_id}") or existing.get(f"path:{draft_path}")
    if record:
        return record, False

    record = create_review_record(
        REVIEW_DIR,
        PENDING_ROOT,
        {
            "md_path": str(_source_path(candidate)),
            "title": candidate.get("title") or "",
            "author": candidate.get("author") or "",
        },
        content_mode="card",
        interaction_channel="terminal",
    )
    record.update(
        {
            "source_id": source_id,
            "source_url": candidate.get("source_url") or "",
            "source_category": candidate.get("source_category") or "",
            "draft_path": str(draft_path),
            "suggested_category": candidate.get("suggested_vault_bucket") or "",
            "priority_score": _priority(candidate),
            "next_action": "complete_review_draft",
            "migration_source": "legacy-classification-candidate",
        }
    )
    save_review_record(REVIEW_DIR, record, expected_revision=0)
    existing[f"id:{source_id}"] = record
    existing[f"path:{draft_path}"] = record
    return record, True


def run(args: argparse.Namespace) -> dict[str, Any]:
    candidates = [
        item
        for item in load_candidates()
        if str(item.get("suggested_vault_bucket") or "") in CATEGORY_INDEX
        and _draft_path(item).exists()
        and _source_path(item).exists()
    ]
    transcripts: dict[str, str] = {}
    for candidate in candidates:
        source_id = str(candidate.get("aweme_id") or "")
        source_text = _source_path(candidate).read_text(encoding="utf-8")
        transcript = extract_transcript_body(source_text)
        transcripts[source_id] = transcript
        candidate["transcript_chars"] = len(transcript)
    candidates.sort(
        key=lambda item: (-_priority(item), str(item.get("aweme_id") or ""))
    )
    eligible = [
        item
        for item in candidates
        if len(transcripts[str(item.get("aweme_id") or "")])
        >= args.min_transcript_chars
    ]
    enrich_ids = {
        str(item.get("aweme_id") or "")
        for item in eligible[: max(0, args.enrich_limit)]
    }
    summary: dict[str, Any] = {
        "success": True,
        "mode": "apply" if args.apply else "dry-run",
        "matched_cards": len(candidates),
        "registered": 0,
        "existing": 0,
        "enriched": 0,
        "awaiting_approval": 0,
        "kept_pending": 0,
        "skipped_short": 0,
        "errors": [],
        "selected": [],
    }
    existing = _existing_reviews()

    for candidate in candidates:
        source_id = str(candidate.get("aweme_id") or "")
        draft_path = _draft_path(candidate)
        transcript = transcripts[source_id]
        selected = source_id in enrich_ids
        preview = {
            "source_id": source_id,
            "title": candidate.get("title") or "",
            "category": candidate.get("suggested_vault_bucket") or "",
            "priority_score": _priority(candidate),
            "transcript_chars": len(transcript),
            "selected_for_enrichment": selected,
        }
        if selected:
            summary["selected"].append(preview)
        if not args.apply:
            continue

        try:
            review, created = _register(candidate, existing)
            summary["registered" if created else "existing"] += 1
            if not selected or review.get("status") != "pending_draft":
                summary["kept_pending"] += int(review.get("status") == "pending_draft")
                continue
            if len(transcript) < args.min_transcript_chars:
                summary["skipped_short"] += 1
                summary["kept_pending"] += 1
                continue

            content = render_card_draft(
                candidate, transcript, review_id=review["review_id"]
            )
            quality = assess_card(content)
            review["quality"] = quality.as_dict()
            save_review_record(
                REVIEW_DIR,
                review,
                expected_revision=int(review.get("revision", 0)),
            )
            _atomic_write(draft_path, content)
            summary["enriched"] += 1
            if not quality.complete:
                summary["kept_pending"] += 1
                continue

            bucket = str(candidate.get("suggested_vault_bucket") or "")
            target_path = f"{bucket}/{draft_path.name}"
            index_path = f"{bucket}/{CATEGORY_INDEX[bucket]}"
            result = handle_review_draft_ready(
                REVIEW_DIR,
                review["review_id"],
                suggested_category=bucket,
                target_path=target_path,
                index_path=index_path,
                draft_path=str(draft_path),
            )
            if result.get("success"):
                summary["awaiting_approval"] += 1
            else:
                summary["errors"].append(
                    {"source_id": source_id, "error": result.get("error")}
                )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            summary["errors"].append(
                {"source_id": source_id, "error": f"{type(exc).__name__}: {exc}"}
            )

    summary["success"] = not summary["errors"]
    return summary


def main() -> int:
    args = parse_args()
    result = run(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
