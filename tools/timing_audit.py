#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计 B2 端到端墙钟重叠：检查同模型多批次是否并发执行污染墙钟耗时（timing_audit.py）。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.scoring.model_identity import canonical_model_id  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def first_tool_epoch(trace_path: Path) -> float | None:
    if not trace_path.is_file():
        return None
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("type") == "tool" and isinstance(rec.get("t"), (int, float)):
            return rec["t"] / 1000.0
    return None


def load_run(run_dir: Path) -> dict | None:
    proto_file = run_dir / "protocol.json"
    if not proto_file.is_file():
        return None
    proto = json.loads(proto_file.read_text(encoding="utf-8"))
    timing = proto.get("timing") or {}
    start = epoch(timing.get("t0_iso")) or epoch(timing.get("process_iso"))
    end = epoch(timing.get("end_iso"))
    proc = epoch(timing.get("process_iso"))
    if start is None or end is None:
        return None
    first = first_tool_epoch(run_dir / "trace.jsonl")
    name = run_dir.name
    return {
        "name": name,
        "model": canonical_model_id(name),
        "start": start,
        "end": end,
        "e2e_min": (end - start) / 60.0,
        "pre_first_min": None if (first is None or proc is None) else (first - proc) / 60.0,
        "overlaps": [],
    }


def collect(root: Path) -> list[dict]:
    root = Path(root)
    if not root.exists():
        return []
    if (root / "protocol.json").is_file():
        dirs = [root]
    else:
        dirs = [p for p in sorted(root.iterdir()) if p.is_dir() and (p / "protocol.json").is_file()]
    rows = [r for r in (load_run(d) for d in dirs) if r is not None]
    for a in rows:
        for b in rows:
            if a is b or a["model"] != b["model"]:
                continue
            if max(a["start"], b["start"]) < min(a["end"], b["end"]):
                a["overlaps"].append(b["name"])
    for a in rows:
        a["b2_comparable"] = not a["overlaps"]
    return rows


def build_report(rows: list[dict]) -> dict:
    any_overlap = any(bool(r["overlaps"]) for r in rows)
    return {
        "ok": not any_overlap,
        "overlapRuns": sum(1 for r in rows if r["overlaps"]),
        "runs": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="检查同一统帅模型的多批次 run 是否时间重叠")
    ap.add_argument("runs_dir", nargs="?", default="runs")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = collect(Path(args.runs_dir))
    if not rows:
        if args.json:
            print(json.dumps({"ok": True, "overlapRuns": 0, "runs": []}, ensure_ascii=False, indent=2))
            return 0
        print("没有找到已完成打卡（含 end_iso）的运行目录。")
        return 0

    report = build_report(rows)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] or not args.strict else 1

    print(f"{'run':42} {'e2e':>8} {'首动作等待':>10}  同模型时间重叠")
    print("-" * 86)
    for r in sorted(rows, key=lambda x: (x["start"], x["name"])):
        pre = "-" if r["pre_first_min"] is None else f"{r['pre_first_min']:8.2f}m"
        ov = ("[OVERLAP] " + ", ".join(r["overlaps"])) if r["overlaps"] else "-"
        print(f"{r['name']:42} {r['e2e_min']:7.2f}m {pre:>10}  {ov}")

    if not report["ok"]:
        print("\n警告：检测到同模型多局并行重叠，B2 墙钟耗时已被队列等待污染，正式评测请串行执行！")
        return 1 if args.strict else 0
    print("\nOK：未发现同模型多局时间重叠。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
