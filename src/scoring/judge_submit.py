#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评委盲评提交入口（judge_submit.py）。

强制核验：
1. 必须引用至少 2 条《统帅调度档案》中的结构化证据标签（如 [C01] [W02] [T03] [R01]）；
2. 反高分通胀铁律五：若评委勾选未超越 5 级传声筒样本（--surpasses-level5 no），而总分 > 15.0，脚本直接拦截拒绝！
"""
from __future__ import annotations

import argparse
import json
import re
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

EVIDENCE_TAG_RE = re.compile(r"\[[CWTRMV]\d{2}\]")


def main() -> int:
    ap = argparse.ArgumentParser(description="提交单条 D 层统帅领导力盲评打分（只追加至中心日志，不回显他人评分）")
    ap.add_argument("--pack-dir", required=True, help="盲评包目录 leaderboard/blind/<pack_id>/")
    ap.add_argument("--case-id", required=True, help="例如 case-01")
    ap.add_argument("--judge-id", required=True, help="评委唯一标识（重评时保持同一 ID）")
    ap.add_argument("--judge-model", required=True, help="评委模型或姓名")
    ap.add_argument("--D1", type=float, required=True, help="搭班子与立规矩（0..11）")
    ap.add_argument("--D2", type=float, required=True, help="任务降维功力（0..10）")
    ap.add_argument("--D3", type=float, required=True, help="审查批注毒辣度与诚实复盘（0..9）")
    ap.add_argument(
        "--surpasses-level5",
        choices=["yes", "no"],
        default="yes",
        help="建制、拆单与审查指导是否明确超越 REFERENCE_LEVEL5.md 平庸样本（选 no 时总分锁死 <= 15）",
    )
    ap.add_argument("--evidence", required=True, help="至少引用 2 条档案证据编号，如 '[C01] [W02] [T03] [R01]'")
    ap.add_argument("--note", required=True, help="与 5 级样本的关键对比结论及最强扣分反证")
    ap.add_argument("--review-log", default=str(review_log.DEFAULT_LOG))
    ap.add_argument("--reject-duplicate", action="store_true")
    args = ap.parse_args()

    pack_dir = Path(args.pack_dir).resolve()
    manifest_path = pack_dir / "pack.json"
    if not manifest_path.is_file():
        print(f"找不到盲评包清单：{manifest_path}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_id = manifest.get("pack_id")
    case_ids = [c.get("case_id") for c in (manifest.get("cases") or []) if c.get("case_id")]
    if not pack_id or args.case_id not in case_ids:
        print(f"pack_id 或 case_id 无效：pack={pack_id}, case={args.case_id}, 可选={case_ids}", file=sys.stderr)
        return 1

    tags = EVIDENCE_TAG_RE.findall(args.evidence)
    if len(set(tags)) < 2:
        print("提交失败：--evidence 必须至少引用 2 个不同的结构化证据标签（如 [C01] [W01] [T02] [R01]）。", file=sys.stderr)
        return 1

    if len(args.note.strip()) < 10:
        print("提交失败：--note 必须写明与 5 级参照样本的对比结论及最强反证（不少于 10 字）。", file=sys.stderr)
        return 1

    d_tot = round(args.D1 + args.D2 + args.D3, 2)
    if args.surpasses_level5 == "no" and d_tot > 15.0:
        print(
            f"【铁律五拦截】已判定未明显超越 5 级传声筒平庸样本（--surpasses-level5 no），D 层总分最高锁死在 15.0 分（当前提交 {d_tot} 分）！",
            file=sys.stderr,
        )
        return 1

    rec = {
        "schema_version": review_log.SCHEMA_VERSION,
        "judge_id": args.judge_id.strip(),
        "judge_model": args.judge_model.strip(),
        "pack_id": pack_id,
        "case_id": args.case_id,
        "D1": args.D1,
        "D2": args.D2,
        "D3": args.D3,
        "surpasses_level5": args.surpasses_level5 == "yes",
        "evidence": args.evidence.strip(),
        "note": args.note.strip(),
    }
    try:
        saved, prior_count = review_log.append_review(
            args.review_log,
            rec,
            reject_duplicate=args.reject_duplicate,
            return_prior_count=True,
        )
    except review_log.DuplicateReviewError as exc:
        print(f"提交失败：{exc}", file=sys.stderr)
        return 2
    except review_log.ReviewLogError as exc:
        print(f"提交失败：{exc}", file=sys.stderr)
        return 1

    if prior_count > 0:
        print(f"重评提交成功（已覆盖同一评委此前 {prior_count} 条历史记录，汇总时实时采用最新一条）。")
    else:
        print("盲评打分提交成功。")
    print(f"review_id={saved['review_id']}  pack_id={saved['pack_id']}  case_id={saved['case_id']}  D={saved['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
