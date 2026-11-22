# -*- coding: utf-8 -*-
"""模型标识归一化：把同一模型的多个批次 run 合并到同一个规范模型 ID（对齐 silicon-rider-xiaonuo）。"""
from __future__ import annotations

import re

_BATCH_SUFFIX_RE = re.compile(
    r"-(?:"
    r"(?:h|s)(?:[-_]?\d+)?"
    r"|(?!(?:v|ver|r)[-_]?\d+$)[a-z](?:[-_]?\d+)"
    r"|(?:batch|run|round|trial|seed|rep|repeat)(?:[-_]?\d+)?"
    r")$",
    re.IGNORECASE,
)


def canonical_model_id(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    stripped = _BATCH_SUFFIX_RE.sub("", text)
    return (stripped or text).lower()


def matches_batch_run(model_name: str, run_name: str) -> bool:
    m = (model_name or "").strip().lower()
    r = (run_name or "").strip().lower()
    if not m or not r:
        return False
    if r == m:
        return True
    if r.startswith(m + "-") or r.startswith(m + "_"):
        return True
    if m[-1].isalpha():
        return re.match(r"^" + re.escape(m) + r"\d+(?:[-_].*)?$", r) is not None
    return False
