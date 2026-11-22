#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看中心盲评日志聚合结果（review_merge.py）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import review_log  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="按 (pack_id, case_id) 聚合最新盲评打分记录")
    ap.add_argument("--log", default=str(review_log.DEFAULT_LOG))
    ap.add_argument("--out", default=None)
    ap.add_argument("--aggregate", choices=["mean", "median"], default="mean")
    args = ap.parse_args()

    reviews = review_log.read_reviews(args.log)
    latest, dup_cnt = review_log.dedupe_latest(reviews)
    groups = review_log.aggregate_reviews(latest, mode=args.aggregate)
    payload = {
        "total_raw_entries": len(reviews),
        "superseded_duplicates": dup_cnt,
        "aggregate_mode": args.aggregate,
        "cases": groups,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(text + "\n", encoding="utf-8")
        print(f"已导出聚合盲评结果至 {out_p}（共 {len(groups)} 个 case）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
