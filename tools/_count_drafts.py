#!/usr/bin/env python3
"""Quick draft counter for review audit."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PENDING = ROOT / "Obsidian" / "AI外脑知识库" / "00_Inbox" / "_待审核"
INBOX = ROOT / "Obsidian" / "AI外脑知识库" / "00_Inbox" / "抖音链接"
CANDS = ROOT / "runtime" / "batch_transcription" / "classification_candidates.jsonl"
REVS = ROOT / ".workflow" / "reviews"

total = 0
for d in sorted(PENDING.iterdir()):
    if d.is_dir():
        md = [f for f in d.iterdir() if f.suffix == ".md" and f.name != ".gitkeep"]
        total += len(md)
        print(f"  {d.name}: {len(md)}")

print(f"\n待审核草稿总计: {total}")
print(f"Inbox 抖音链接转写稿: {len(list(INBOX.glob('*.md')))}")
lines = [l for l in CANDS.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"分类候选 JSONL 行数: {len(lines)}")
if REVS.exists():
    print(f"审核记录数: {len(list(REVS.glob('*.json')))}")
else:
    print("审核记录目录不存在")
