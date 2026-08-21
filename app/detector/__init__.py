"""
信号识别层 - 关键词匹配 + 置信度评分 + 否定词过滤
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ─────────────────────────────────────────────
# 关键词库
# ─────────────────────────────────────────────

# 强信号（直接命中 → high 置信度）
STRONG_SIGNALS_EN = [
    r"\bfully\s+reset\b",
    r"\bhard\s+reset\b",
    r"\bbanked\s+reset\b",
    r"\breplenish(?:ed|ing)?\b",
    r"\breset(?:ting)?\s+(your\s+)?(?:usage\s+)?limits?\b",
    r"\breset(?:ting)?\s+(?:all|every(?:one|body)|the)\b",
    r"\blimits?\s+(?:have\s+been\s+)?reset\b",
    r"\b100\s*%\s+(?:usage\s+)?limits?\b",
    r"\busage\s+limits?\s+reset\b",
    r"\bgive\s+(?:everyone|you|all)\s+(?:a\s+)?(?:full\s+)?reset\b",
    r"\bcleared\s+(?:all\s+)?(?:usage\s+)?limits?\b",
    r"\bnice\s+reset\b",
    r"\benjoy\s+a\s+reset\b",
]

STRONG_SIGNALS_ZH = [
    r"重置(?:了|完|啦)?额度",
    r"额度(?:已|已经|重新)?重置",
    r"重置用量",
    r"用量重置",
    r"全员重置",
    r"额度清零",
    r"回满(?:了)?",
]

# 弱信号（需加权累计 → medium 置信度）
WEAK_SIGNALS_EN = [
    r"\breset\b",
    r"\bmilestone\b",
    r"\busage\s+limits?\b",
    r"\brate\s+limits?\b",
    r"\bactive\s+users?\b",
    r"🔄",
    r"🎁",
    r"🎉",
    r"\bgift\b",
]

WEAK_SIGNALS_ZH = [
    r"重置",
    r"里程碑",
    r"用量",
    r"限制",
    r"活跃用户",
]

# 否定排除词（命中任一 → 强制低置信）
NEGATION_PATTERNS = [
    r"\bnot\s+resetting\b",
    r"\bno\s+reset\b",
    r"\bwon'?t\s+reset\b",
    r"\bwill\s+not\s+reset\b",
    r"\bno\s+replenish\b",
    r"\bwithout\s+reset\b",
    r"不重置",
    r"没有重置",
    r"不会重置",
]

# ─────────────────────────────────────────────

Confidence = Literal["high", "medium", "low"]


@dataclass
class DetectResult:
    is_reset: bool
    confidence: Confidence
    matched_patterns: list[str]
    score: float


def _match_patterns(text: str, patterns: list[str]) -> list[str]:
    matched = []
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            matched.append(pat)
    return matched


def classify(text: str) -> DetectResult:
    """
    对一条推文文本进行重置信号分类。
    返回 DetectResult，is_reset=True 表示判定为重置事件。
    """
    # 1. 否定词检测 → 直接排除
    neg_hits = _match_patterns(text, NEGATION_PATTERNS)
    if neg_hits:
        return DetectResult(
            is_reset=False,
            confidence="low",
            matched_patterns=neg_hits,
            score=0.0,
        )

    # 2. 强信号
    strong_hits = _match_patterns(text, STRONG_SIGNALS_EN + STRONG_SIGNALS_ZH)
    if strong_hits:
        return DetectResult(
            is_reset=True,
            confidence="high",
            matched_patterns=strong_hits,
            score=1.0,
        )

    # 3. 弱信号加权（阈值 ≥ 2 条弱命中 → medium）
    weak_hits = _match_patterns(text, WEAK_SIGNALS_EN + WEAK_SIGNALS_ZH)
    score = len(weak_hits) / max(len(WEAK_SIGNALS_EN + WEAK_SIGNALS_ZH), 1)

    if len(weak_hits) >= 3:
        return DetectResult(
            is_reset=True,
            confidence="medium",
            matched_patterns=weak_hits,
            score=score,
        )

    return DetectResult(
        is_reset=False,
        confidence="low",
        matched_patterns=weak_hits,
        score=score,
    )
