#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单局官方客观分（A30 + B15 + C25 = 70 分）定稿脚本（score_run.py）。

注意：选手阶段严禁调用本脚本！本脚本仅由汇总出榜侧（finalize.py）或维护者自检时调用。
"""
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
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import verify_workspace_integrity  # noqa: E402
from src.scoring import metrics, objective_cache, workload  # noqa: E402
from tools.start_run import compute_workspace_sha256  # noqa: E402


def score_single_run(
    run_dir: Path,
    *,
    root: Path = ROOT,
    b2_reliable: bool = True,
    write_files: bool = True,
    objective_cache_dir: Path | None = None,
) -> dict:
    rp = Path(run_dir).resolve()
    config = json.loads((Path(root) / "configs" / "benchmark.json").read_text(encoding="utf-8"))
    proto = json.loads((rp / "protocol.json").read_text(encoding="utf-8"))
    state = json.loads((rp / ".marshal_state" / "state.json").read_text(encoding="utf-8"))

    # 校验主干业务目录是否与调度状态账本一致（防绕过调度器手工直写）
    ok_ws, ws_anomalies = verify_workspace_integrity(rp / "workspace", state.get("trunk_manifest") or {})
    code_sha = compute_workspace_sha256(rp / "workspace")
    if proto.get("code_sha256") and proto["code_sha256"] != code_sha:
        ok_ws = False
        ws_anomalies.append("打包冻结 t1 后 workspace 代码哈希发生变动")

    if not ok_ws or state.get("tamper_detected"):
        state["tamper_detected"] = True
        a_rep = {
            "boot_ok": False,
            "boot_error": f"检测到物理禁手违规/篡改：{ws_anomalies or state.get('tamper_anomalies')}",
            "A1": 0.0,
            "A2": 0.0,
            "A3": 0.0,
            "A_total": 0.0,
            "wave_counts": {"w1_passed": 0, "w1_total": 12, "w2_passed": 0, "w2_total": 10, "w3_passed": 0, "w3_total": 8},
            "waves": {"wave1": [], "wave2": [], "wave3": []},
        }
    else:
        a_rep = workload.evaluate_workspace_waves(rp / "workspace", config.get("scoring") or {})

    bc_rep = metrics.compute_bc_scores(rp, a_rep, config, b2_reliable=b2_reliable)
    if not ok_ws:
        bc_rep["tampered"] = True
        bc_rep["B1"] = bc_rep["B2"] = bc_rep["B_total"] = 0.0
        bc_rep["C1"] = bc_rep["C2"] = bc_rep["C3"] = bc_rep["C_total"] = 0.0

    a_out = 0.0 if bc_rep["tampered"] else float(a_rep["A_total"])
    tot_70 = round(a_out + float(bc_rep["B_total"]) + float(bc_rep["C_total"]), 2)

    raw_record = {
        "model": proto.get("model", rp.name),
        "run_name": rp.name,
        "worker_mode": proto.get("worker_mode"),
        "worker_model_locked": proto.get("worker_model_locked"),
        "A1": 0.0 if bc_rep["tampered"] else a_rep["A1"],
        "A2": 0.0 if bc_rep["tampered"] else a_rep["A2"],
        "A3": 0.0 if bc_rep["tampered"] else a_rep["A3"],
        "A_outcome": a_out,
        "B1": bc_rep["B1"],
        "B2": bc_rep["B2"],
        "B_efficiency": bc_rep["B_total"],
        "B_detail": bc_rep["B_detail"],
        "C1": bc_rep["C1"],
        "C2": bc_rep["C2"],
        "C3": bc_rep["C3"],
        "C_quality": bc_rep["C_total"],
        "C_detail": bc_rep["C_detail"],
        "total_objective_70": tot_70,
        "tampered": bc_rep["tampered"],
        "trace_verified": bc_rep["trace_verified"],
        "run_summary": bc_rep["run_summary"],
        "wave_counts": a_rep.get("wave_counts"),
        "waves": a_rep.get("waves"),
        "boot_ok": a_rep.get("boot_ok"),
        "boot_error": a_rep.get("boot_error"),
        "code_sha256": code_sha,
        "b2_reliable": bool(b2_reliable),
    }

    scoring_hash = objective_cache.compute_scoring_hash(root)
    deliv_hash = objective_cache.compute_deliverables_hash(rp)
    final_rec = objective_cache.build_objective_record(
        raw_record,
        scoring_hash=scoring_hash,
        deliverables_hash=deliv_hash,
    )

    if write_files:
        (rp / "评分.json").write_text(json.dumps(final_rec, ensure_ascii=False, indent=2), encoding="utf-8")
        cdir = objective_cache_dir or (Path(root) / "leaderboard" / "objective-cache")
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{rp.name}.json").write_text(
            json.dumps({"run": rp.name, "scores": final_rec}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return final_rec


def main() -> int:
    ap = argparse.ArgumentParser(description="对已打包冻结的单局运行执行三波集成压测并定稿客观分（仅汇总侧使用）")
    ap.add_argument("run_dir", help="例如 runs/gemini-4-carbon-s1")
    ap.add_argument("--no-write", action="store_true", help="只打印结果，不落盘写入 评分.json 与缓存")
    args = ap.parse_args()

    rec = score_single_run(Path(args.run_dir), write_files=not args.no_write)
    summary_out = {k: v for k, v in rec.items() if k != "waves"}
    print(json.dumps(summary_out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
