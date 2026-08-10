"""Deterministic, review-only knowledge-card draft helpers.

The helpers deliberately create extractive drafts. They never approve or file a
card into a formal Vault category; a human must still review the result through
the persisted review state machine.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SECTION_RE = re.compile(r"(?m)^##\s+")
NUMBERED_ITEM_RE = re.compile(r"(?m)^\d+\.\s*(\S.*)$")
BULLET_ITEM_RE = re.compile(r"(?m)^-\s+(?!\[\s*\])(.+\S)\s*$")

FILLER_LINES = {
    "好的",
    "好",
    "然后",
    "就是",
    "这个",
    "这样",
    "可以",
    "对",
    "嗯",
    "啊",
    "呃",
    "所以",
    "但是",
    "其实",
    "大家好",
}

ACTION_SIGNALS = (
    "推荐",
    "需要",
    "可以",
    "方法",
    "步骤",
    "首先",
    "其次",
    "最后",
    "注意",
    "关键",
    "如果",
    "因为",
    "因此",
    "选择",
    "使用",
    "实现",
    "设计",
    "开发",
    "模型",
    "工具",
    "工作流",
    "智能体",
)

TOOL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:chatgpt|gpt[- ]?\d\w*)\b", "ChatGPT / GPT"),
    (r"\bopenai\b", "OpenAI"),
    (r"\bclaude\b", "Claude"),
    (r"\bcodex\b", "Codex"),
    (r"\bcursor\b", "Cursor"),
    (r"\bfigma\b|费格玛|菲格玛", "Figma"),
    (r"\bgithub\b", "GitHub"),
    (r"\bobsidian\b", "Obsidian"),
    (r"\bmcp\b", "MCP"),
    (r"\bqwen\b|通义千问", "Qwen"),
    (r"\bwhisper\b", "Whisper"),
    (r"\bcomfyui\b", "ComfyUI"),
    (r"\bmidjourney\b", "Midjourney"),
    (r"\bstable diffusion\b", "Stable Diffusion"),
    (r"\bpython\b", "Python"),
    (r"\bnotion\b", "Notion"),
)

CATEGORY_SCENARIOS: dict[str, list[str]] = {
    "01_AI术语库": [
        "理解并解释相关 AI 概念",
        "项目调研与方案讨论",
        "内容选题与知识复习",
    ],
    "02_模型能力库": [
        "模型选型和能力边界判断",
        "提示词与效果验证",
        "把模型接入现有工作流",
    ],
    "03_AI工具库": [
        "工具选型与替代方案比较",
        "快速原型和日常提效",
        "评估集成成本与使用风险",
    ],
    "04_工作流库": ["把重复任务整理成 SOP", "设计自动化执行链路", "团队协作和交付复盘"],
    "05_智能体库": [
        "设计 Agent 的角色与任务边界",
        "拆分多步骤自动化任务",
        "评估智能体协作和审计机制",
    ],
}


@dataclass(frozen=True)
class DraftQuality:
    transcript_chars: int
    knowledge_points: int
    scenarios: int
    tools: int
    complete: bool
    approval_ready: bool
    score: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "transcript_chars": self.transcript_chars,
            "knowledge_points": self.knowledge_points,
            "scenarios": self.scenarios,
            "tools": self.tools,
            "complete": self.complete,
            "approval_ready": self.approval_ready,
            "review_note": "抽取式草稿仅供校对，不能自动批准。",
            "score": self.score,
        }


def extract_transcript_body(note_text: str) -> str:
    """Return the transcript body from an exported Inbox note."""
    marker = "## 文字稿"
    index = note_text.find(marker)
    if index < 0:
        return ""
    return note_text[index + len(marker) :].strip()


def _clean_line(value: str) -> str:
    value = re.sub(r"\s+", "", value.strip())
    value = value.strip("，。！？；：,.!?;:~～-—")
    return value


def _segments(transcript: str) -> list[str]:
    lines = [_clean_line(item) for item in transcript.splitlines()]
    lines = [
        item
        for item in lines
        if item
        and item not in FILLER_LINES
        and not item.startswith(("#", "http://", "https://"))
    ]
    segments: list[str] = []
    buffer = ""
    for line in lines:
        if len(line) < 4 and not buffer:
            continue
        buffer += line
        if len(buffer) >= 28 or line.endswith(("。", "！", "？", "；")):
            segments.append(buffer[:120])
            buffer = ""
    if len(buffer) >= 10:
        segments.append(buffer[:120])
    return _dedupe(segments)


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    fingerprints: list[set[str]] = []
    for item in items:
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", item).lower()
        if len(normalized) < 8:
            continue
        tokens = {
            normalized[index : index + 3]
            for index in range(max(1, len(normalized) - 2))
        }
        if any(
            tokens and len(tokens & known) / len(tokens) >= 0.72
            for known in fingerprints
        ):
            continue
        result.append(item)
        fingerprints.append(tokens)
    return result


def extract_key_points(transcript: str, limit: int = 5) -> list[str]:
    """Select distinct, action-oriented transcript excerpts for human review."""
    segments = _segments(transcript)
    scored: list[tuple[float, int, str]] = []
    for index, segment in enumerate(segments):
        signal_count = sum(signal in segment.lower() for signal in ACTION_SIGNALS)
        length_score = 1.5 if 24 <= len(segment) <= 90 else 0.5
        position_score = max(0.0, 1.0 - index / max(1, len(segments)))
        punctuation_score = 0.4 if re.search(r"[0-9A-Za-z]", segment) else 0.0
        score = signal_count * 1.7 + length_score + position_score + punctuation_score
        scored.append((score, index, segment))
    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    return [item[2] for item in sorted(selected, key=lambda item: item[1])]


def detect_tools(text: str) -> list[str]:
    """Extract explicitly mentioned tools and models without inventing names."""
    found: list[str] = []
    for pattern, display_name in TOOL_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE) and display_name not in found:
            found.append(display_name)
    return found[:8]


def _yaml(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _one_liner(title: str, points: list[str]) -> str:
    if points:
        point = points[0]
        if len(point) > 78:
            point = point[:78].rstrip("，。；：") + "…"
        return f"围绕“{title}”梳理可复用方法，核心信息包括：{point}。"
    return f"围绕“{title}”整理的抽取式草稿，需结合原始文字稿继续审核。"


def _project_mappings(title: str, transcript: str) -> list[str]:
    haystack = f"{title} {transcript}".lower()
    mappings = ["AI 外脑：沉淀为可搜索、可复用的知识卡片"]
    if any(term in haystack for term in ("网站", "开发", "代码", "编程", "工具")):
        mappings.append("当前开发项目：作为工具选型或实现方案的参考")
    if any(term in haystack for term in ("智能体", "agent", "陪伴", "对话", "记忆")):
        mappings.append("AI Companion：作为能力设计、交互或记忆策略的参考")
    return mappings[:3]


def render_card_draft(
    record: dict[str, Any],
    transcript: str,
    *,
    review_id: str,
) -> str:
    """Render a complete extractive draft that remains explicitly unapproved."""
    bucket = str(record.get("suggested_vault_bucket") or "00_Inbox")
    title = str(record.get("title") or record.get("aweme_id") or "未命名内容")
    points = extract_key_points(transcript)
    tools = detect_tools(f"{title}\n{transcript}")
    scenarios = CATEGORY_SCENARIOS.get(
        bucket,
        ["个人知识整理与回顾", "判断是否值得进一步研究", "作为后续内容或项目素材"],
    )
    mappings = _project_mappings(title, transcript)
    related = tools[:4] or ["待审核后补充相关术语或工具"]
    tag = {
        "01_AI术语库": "术语",
        "02_模型能力库": "模型",
        "03_AI工具库": "工具",
        "04_工作流库": "工作流",
        "05_智能体库": "智能体",
    }.get(bucket, "待分类")

    point_lines = (
        "\n".join(f"{index}. {point}" for index, point in enumerate(points, start=1))
        or "1. 原始文字稿信息不足，需人工判断是否保留。"
    )
    scenario_lines = "\n".join(f"- {item}" for item in scenarios)
    tool_lines = (
        "\n".join(f"- {item}" for item in tools) or "- 原视频未明确提及具体工具或模型"
    )
    mapping_lines = "\n".join(f"- [ ] {item}" for item in mappings)
    related_lines = "\n".join(f"- [[{item}]]" for item in related)

    return f"""---
tags:
  - {tag}
  - 待审核
created: {datetime.now(UTC).date().isoformat()}
source: {_yaml(record.get("source_url"))}
douyin_author: {_yaml(record.get("author") or "未知")}
douyin_aweme_id: {_yaml(record.get("aweme_id"))}
douyin_create_time: {_yaml(record.get("source_create_time"))}
source_category: {_yaml(record.get("source_category") or "未知")}
review_id: {_yaml(review_id)}
review_status: pending
curation_method: deterministic-extractive-v1
quality_status: needs_human_review
---

# {title}

## 📌 一句话总结
{_one_liner(title, points)}

## 🏷️ 类型
{tag}

## 🔑 关键知识点
{point_lines}

## 🎯 适用场景
{scenario_lines}

## 🛠️ 可用工具 / 模型
{tool_lines}

## 🔗 可迁移到我的项目
{mapping_lines}

## 💬 我的理解
> 这是基于原始转写自动抽取的审核草稿；批准前应核对事实、术语和 ASR 误差。

## 📤 可输出内容
- 选题方向：{title}
- 讨论话题：原视频方法是否可复用、适用边界是什么
- 商业切入点：仅在人工确认真实需求和可交付价值后评估

## ➡️ 后续行动
- [ ] 值得深入研究
- [ ] 可以做成内容输出
- [ ] 可以做成产品功能
- [ ] 可以做成商业案例
- [ ] 暂时收藏观察

## 🏗️ 关联知识
{related_lines}

---

## 📝 原始文字稿
{transcript.strip()}
"""


def _section(card_text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = card_text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    match = SECTION_RE.search(card_text, body_start)
    end = match.start() if match else len(card_text)
    return card_text[body_start:end].strip()


def assess_card(card_text: str) -> DraftQuality:
    """Measure whether a draft is ready to be shown for approval."""
    transcript = _section(card_text, "📝 原始文字稿")
    points = NUMBERED_ITEM_RE.findall(_section(card_text, "🔑 关键知识点"))
    scenarios = BULLET_ITEM_RE.findall(_section(card_text, "🎯 适用场景"))
    tools = BULLET_ITEM_RE.findall(_section(card_text, "🛠️ 可用工具 / 模型"))
    meaningful_points = [item for item in points if len(_clean_line(item)) >= 8]
    meaningful_scenarios = [item for item in scenarios if len(_clean_line(item)) >= 5]
    score = min(40, int(math.log2(max(1, len(transcript))) * 4))
    score += min(35, len(meaningful_points) * 7)
    score += min(15, len(meaningful_scenarios) * 5)
    score += min(10, len(tools) * 2)
    complete = (
        len(transcript) >= 200
        and len(meaningful_points) >= 3
        and len(meaningful_scenarios) >= 2
    )
    return DraftQuality(
        transcript_chars=len(transcript),
        knowledge_points=len(meaningful_points),
        scenarios=len(meaningful_scenarios),
        tools=len(tools),
        complete=complete,
        approval_ready=False,
        score=min(100, score),
    )
