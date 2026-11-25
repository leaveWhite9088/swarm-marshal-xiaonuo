# -*- coding: utf-8 -*-
"""B 层（统筹效率与并行杠杆 20 分）+ C 层（五大统领过程管控维度 25 分）计算与脱敏档案提取器（metrics.py）。"""
from __future__ import annotations

import ast
import datetime
import json
import math
import re
from pathlib import Path

from src.marshal.state_store import verify_trace_chain

LEGACY_CORE_MODULES = (
    "cloudhub/db.py",
    "cloudhub/billing.py",
    "cloudhub/jobs.py",
    "cloudhub/auth.py",
    "cloudhub/app.py",
)


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _ast_contract_alignment_ratio(workspace_dir: Path) -> float:
    """静态 AST 分析：核查 cloudhub/ 下跨模块相对导入及属性调用是否在目标模块真实存在。"""
    pkg_dir = workspace_dir / "cloudhub"
    if not pkg_dir.is_dir():
        return 0.0
    py_files = [p for p in sorted(pkg_dir.glob("*.py")) if p.name != "__init__.py"]
    if not py_files:
        return 0.0

    exports_by_mod: dict[str, set[str]] = {}
    trees_by_mod: dict[str, ast.AST] = {}
    for fp in py_files:
        mod_name = fp.stem
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8"), filename=fp.name)
        except SyntaxError:
            return 0.0
        trees_by_mod[mod_name] = tree
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
        exports_by_mod[mod_name] = names

    total_refs = 0
    valid_refs = 0
    for _, tree in trees_by_mod.items():
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module is None:
                    for alias in node.names:
                        target_mod = alias.name
                        as_name = alias.asname or alias.name
                        total_refs += 1
                        if target_mod in exports_by_mod:
                            valid_refs += 1
                            aliases[as_name] = target_mod
                else:
                    target_mod = node.module
                    for alias in node.names:
                        total_refs += 1
                        if target_mod in exports_by_mod and alias.name in exports_by_mod[target_mod]:
                            valid_refs += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in aliases:
                    tmod = aliases[node.value.id]
                    total_refs += 1
                    if node.attr in exports_by_mod.get(tmod, set()):
                        valid_refs += 1

    if total_refs == 0:
        return 0.25 if len(py_files) == 1 else 0.5
    return valid_refs / total_refs


def _count_test_assertions(workspace_dir: Path) -> tuple[int, int]:
    """静态统计 workspace/tests/*.py 中的测试文件数与真实断言语句数（供 C2 与盲评交叉核验）。"""
    tests_dir = workspace_dir / "tests"
    if not tests_dir.is_dir():
        return 0, 0
    test_files = [p for p in sorted(tests_dir.glob("*.py")) if p.name != "__init__.py"]
    assert_cnt = 0
    for fp in test_files:
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="ignore"), filename=fp.name)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                assert_cnt += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr.startswith("assert"):
                    assert_cnt += 1
    return len(test_files), assert_cnt


def extract_process_observables(run_path: Path, state: dict, trace_events: list[dict]) -> dict:
    """提取五大统领过程核心维度的客观事实指标（供 B/C 算分与盲评档案 `[P]` 证据组共用）。"""
    workers: dict = state.get("workers") or {}
    subs: dict = state.get("submissions") or {}

    # 1. 前期侦察纪律（首张派单前、首次立契约前实际阅读了几个存量核心模块）
    first_dispatch_idx = next((i for i, e in enumerate(trace_events) if e.get("action") == "dispatch_task" and e.get("type") == "tool"), len(trace_events))
    first_contract_idx = next((i for i, e in enumerate(trace_events) if e.get("action") == "define_contract" and e.get("type") == "tool"), len(trace_events))
    unlock_p2_idx = next((i for i, e in enumerate(trace_events) if e.get("action") == "unlock_phase2"), None)

    recon_before_dispatch: set[str] = set()
    recon_before_contract: set[str] = set()
    for idx, ev in enumerate(trace_events):
        if ev.get("action") == "inspect_and_test" and (ev.get("payload") or {}).get("target") == "trunk":
            p = ((ev.get("payload") or {}).get("path") or "").strip()
            if p in LEGACY_CORE_MODULES:
                if idx < first_dispatch_idx:
                    recon_before_dispatch.add(p)
                if idx < first_contract_idx:
                    recon_before_contract.add(p)

    # 2. 并行波次、工位交替流转与单点死磕阻塞检测
    # 扫描轨迹：统计同波次并行（wave_id 共享 / 执行区间重叠 / 他人工位已有 PENDING 待审时并发派发其他工位）
    pending_by_worker: dict[str, str | None] = {}
    co_scheduled_subs: set[str] = set()
    wave_Buckets: dict[str, list[str]] = {}
    intervals: list[tuple[str, str, int, int]] = []
    exec_worker_seq: list[str] = []

    for ev in trace_events:
        if ev.get("type") != "tool":
            continue
        act = ev.get("action")
        pl = ev.get("payload") or {}
        if act == "dispatch_task":
            wid = str(pl.get("worker_id") or "")
            sid = str(pl.get("submission_id") or "")
            exec_worker_seq.append(wid)
            other_pending = any(v is not None for k, v in pending_by_worker.items() if k != wid)
            if other_pending and sid:
                co_scheduled_subs.add(sid)
            pending_by_worker[wid] = sid
            w_id = pl.get("wave_id")
            if w_id and sid:
                wave_Buckets.setdefault(str(w_id), []).append(sid)
            s_ms = pl.get("started_ms")
            f_ms = pl.get("finished_ms")
            if isinstance(s_ms, int) and isinstance(f_ms, int) and sid:
                intervals.append((sid, wid, s_ms, f_ms))
        elif act == "review_submission":
            wid = str(pl.get("worker_id") or "")
            dec = str(pl.get("decision") or "")
            if dec == "APPROVE" or (dec == "REJECT" and pl.get("discard_only")):
                pending_by_worker[wid] = None
        elif act == "rework_completed":
            wid = str(pl.get("worker_id") or "")
            nsid = str(pl.get("new_submission_id") or "")
            exec_worker_seq.append(wid)
            pending_by_worker[wid] = nsid

    for sids in wave_Buckets.values():
        if len(sids) >= 2:
            co_scheduled_subs.update(sids)

    for i in range(len(intervals)):
        sid_i, wid_i, s_i, f_i = intervals[i]
        for j in range(i + 1, len(intervals)):
            sid_j, wid_j, s_j, f_j = intervals[j]
            if wid_i != wid_j and max(s_i, s_j) <= min(f_i, f_j):
                co_scheduled_subs.add(sid_i)
                co_scheduled_subs.add(sid_j)

    initial_subs = [s for s in subs.values() if not s.get("rework_of")]
    parallel_ratio = len(co_scheduled_subs) / max(1, len(initial_subs))

    max_same_worker_streak = 0
    cur_streak = 0
    prev_w = None
    for wid in exec_worker_seq:
        if wid == prev_w:
            cur_streak += 1
        else:
            cur_streak = 1
            prev_w = wid
        if cur_streak > max_same_worker_streak:
            max_same_worker_streak = cur_streak

    # 3. 工位合入代码量分布（防“一工位包干九成代码，两工位陪跑”）
    merged_chars_by_worker: dict[str, int] = {wid: 0 for wid in workers}
    for s in subs.values():
        if s.get("status") == "APPROVED":
            wid = s.get("worker_id")
            if wid in merged_chars_by_worker:
                merged_chars_by_worker[wid] += max(1, int(s.get("diff_chars", 0)))
    total_merged_chars = sum(merged_chars_by_worker.values())
    active_wids = [wid for wid, w in workers.items() if int(w.get("approved_count", 0)) > 0]
    max_worker_code_share = (
        max((merged_chars_by_worker[wid] / total_merged_chars for wid in active_wids), default=1.0)
        if total_merged_chars > 0 and active_wids
        else 1.0
    )

    # 4. 实证审查把关（核查每一次 APPROVE 和 REJECT 前是否做过代码审阅或叠加测试）与打回一击修复率
    verdict_events = [
        ev["payload"]
        for ev in trace_events
        if ev.get("type") == "tool" and ev.get("action") == "review_submission"
    ]
    blind_verdict_subs: list[str] = []
    verified_verdicts = 0
    for vp in verdict_events:
        if vp.get("inspected_before_review") or vp.get("tested_before_review"):
            verified_verdicts += 1
        else:
            blind_verdict_subs.append(f"{vp.get('submission_id')}({vp.get('decision')})")

    reject_events = [vp for vp in verdict_events if vp.get("decision") == "REJECT"]
    one_shot_fixes = 0
    ineffective_reworks = 0
    for r_vp in reject_events:
        target_sid = r_vp.get("submission_id")
        comment_txt = (r_vp.get("comment") or "").strip()
        is_specific = len(comment_txt) >= 45
        if r_vp.get("discard_only"):
            ineffective_reworks += 1
            continue
        child_sub = next((s for s in subs.values() if s.get("rework_of") == target_sid), None)
        if is_specific and child_sub and child_sub.get("status") == "APPROVED" and int(child_sub.get("rework_depth", 1)) == 1:
            one_shot_fixes += 1
        else:
            ineffective_reworks += 1

    # 5. 二阶段抗冲击弹性与阶段测试节奏
    phase2_reworks = sum(
        1
        for s in subs.values()
        if int(s.get("phase", 1)) == 2 and s.get("rework_of") is not None
    )
    pre_p2_tested = False
    post_p2_regression_fail = False
    if unlock_p2_idx is not None:
        pre_p2_tested = any(
            idx < unlock_p2_idx
            and ev.get("action") == "inspect_and_test"
            and (ev.get("payload") or {}).get("target") in ("smoke", "unittest")
            and (ev.get("payload") or {}).get("passed") is True
            for idx, ev in enumerate(trace_events)
        )
        # 检查二阶段最后一次合入后是否残留未修平的测试失败
        last_test_after_p2 = [
            (ev.get("payload") or {}).get("passed")
            for idx, ev in enumerate(trace_events)
            if idx > unlock_p2_idx
            and ev.get("action") == "inspect_and_test"
            and (ev.get("payload") or {}).get("target") in ("smoke", "unittest")
            and not (ev.get("payload") or {}).get("submission_id")
        ]
        if last_test_after_p2 and (last_test_after_p2[-1] is False):
            post_p2_regression_fail = True

    test_file_cnt, test_assert_cnt = _count_test_assertions(run_path / "workspace")

    ticket_lengths = [len((s.get("ticket") or "").strip()) for s in initial_subs if s.get("ticket")]
    avg_ticket_chars = (sum(ticket_lengths) / len(ticket_lengths)) if ticket_lengths else 0.0

    return {
        "recon_before_dispatch": sorted(recon_before_dispatch),
        "recon_before_contract": sorted(recon_before_contract),
        "co_scheduled_count": len(co_scheduled_subs),
        "initial_dispatches_count": len(initial_subs),
        "parallel_ratio": round(parallel_ratio, 3),
        "max_same_worker_streak": max_same_worker_streak,
        "merged_chars_by_worker": merged_chars_by_worker,
        "max_worker_code_share": round(max_worker_code_share, 3),
        "total_verdicts": len(verdict_events),
        "verified_verdicts": verified_verdicts,
        "blind_verdict_subs": blind_verdict_subs,
        "reject_count": len(reject_events),
        "one_shot_fixes": one_shot_fixes,
        "ineffective_reworks": ineffective_reworks,
        "phase2_unlocked": bool(state.get("phase2_unlocked", False)),
        "phase2_reworks": phase2_reworks,
        "pre_p2_tested": pre_p2_tested,
        "post_p2_regression_fail": post_p2_regression_fail,
        "test_file_cnt": test_file_cnt,
        "test_assert_cnt": test_assert_cnt,
        "avg_ticket_chars": round(avg_ticket_chars, 1),
    }


def compute_bc_scores(
    run_dir: Path,
    a_report: dict,
    config: dict,
    *,
    b2_reliable: bool = True,
) -> dict:
    run_path = Path(run_dir).resolve()
    proto = json.loads((run_path / "protocol.json").read_text(encoding="utf-8"))
    state = json.loads((run_path / ".marshal_state" / "state.json").read_text(encoding="utf-8"))
    trace_ok, trace_msg, trace_events = verify_trace_chain(run_path / "trace.jsonl")

    tampered = bool(state.get("tamper_detected")) or (not trace_ok)
    a_total = 0.0 if tampered else float(a_report.get("A_total", 0.0))

    s_cfg = config.get("scoring") or {}
    a_max = float((s_cfg.get("A") or {}).get("max", 25.0))
    b_cfg = s_cfg.get("B") or {}
    c_cfg = s_cfg.get("C") or {}

    timing = proto.get("timing") or {}
    t0 = _parse_iso(timing.get("t0_iso")) or _parse_iso(timing.get("process_iso"))
    t1 = _parse_iso(timing.get("end_iso"))
    pause_sec = max(0.0, float(timing.get("infra_pause_sec") or 0.0))
    if t0 is not None and t1 is not None and t1 >= t0:
        e2e_sec = max(0.0, (t1 - t0) - pause_sec)
    else:
        last_ms = trace_events[-1]["t"] if trace_events else ((t0 or 0) * 1000)
        e2e_sec = max(0.0, (last_ms / 1000.0) - (t0 or (last_ms / 1000.0)) - pause_sec)
    e2e_min = e2e_sec / 60.0

    workers: dict = state.get("workers") or {}
    subs: dict = state.get("submissions") or {}
    dispatches_used = int(state.get("dispatches_used", 0))
    approved_subs = [s for s in subs.values() if s.get("status") == "APPROVED"]
    rejected_subs = [s for s in subs.values() if s.get("status") == "REJECTED"]
    approved_count = len(approved_subs)
    smuggling_count = int(state.get("code_smuggling_count", 0))
    tool_error_count = int(state.get("tool_error_count", 0))
    midflight_contract_revs = int(state.get("contract_revisions_after_dispatch", 0))
    rework_churn = max(0, dispatches_used - approved_count)

    if tampered:
        return {
            "tampered": True,
            "trace_verified": trace_ok,
            "trace_message": trace_msg,
            "B1": 0.0,
            "B2": 0.0,
            "B_total": 0.0,
            "B_detail": {"e2e_min": round(e2e_min, 3), "cap_ratio": 0.0, "b2_reliable": b2_reliable},
            "C1": 0.0,
            "C2": 0.0,
            "C3": 0.0,
            "C_total": 0.0,
            "C_detail": {},
            "run_summary": {"dispatches_used": dispatches_used, "workers_total": len(workers), "approved_count": approved_count, "e2e_min": round(e2e_min, 2)},
        }

    obs = extract_process_observables(run_path, state, trace_events)
    total_worker_tokens = sum(int((s.get("worker_tokens") or {}).get("total_tokens") or 0) for s in subs.values())

    # ---------------- B 层：统筹效率、并行度与指令经济性（20 分 = B1 10 + B2 10，受 A / 25 封顶） ----------------
    cap_ratio = (a_total / max(1.0, a_max)) if b_cfg.get("cap_by_a_ratio", True) else 1.0
    cap_ratio = max(0.0, min(1.0, cap_ratio))

    # B1.1 同波次并行推进度 (1.8) + 轮次转化效率 (1.2) = 3.0
    target_par = float(b_cfg.get("b1_target_parallel_ratio", 0.45))
    b1_parallel_score = 1.8 * min(1.0, obs["parallel_ratio"] / max(0.05, target_par)) if approved_count > 0 else 0.0

    ideal_d = int(b_cfg.get("b1_ideal_dispatches", 8))
    max_d = int(b_cfg.get("b1_max_dispatches", 15))
    if dispatches_used == 0 or approved_count == 0:
        b1_disp_score = 0.0
    elif dispatches_used <= ideal_d:
        b1_disp_score = 0.6
    elif dispatches_used >= max_d:
        b1_disp_score = 0.0
    else:
        b1_disp_score = 0.6 * (max_d - dispatches_used) / max(1, max_d - ideal_d)

    conv_ratio = approved_count / max(1, dispatches_used)
    floor_c = float(b_cfg.get("b1_floor_first_pass_ratio", 0.35))
    ideal_c = float(b_cfg.get("b1_ideal_first_pass_ratio", 0.72))
    if dispatches_used == 0 or conv_ratio <= floor_c:
        b1_conv_score = 0.0
    elif conv_ratio >= ideal_c:
        b1_conv_score = 0.6
    else:
        b1_conv_score = 0.6 * (conv_ratio - floor_c) / max(0.01, ideal_c - floor_c)

    # B1.2 工人词元能效比 (3.0) + 统帅指令信噪比 (1.5) = 4.5
    w_mode = str(state.get("worker_mode", "pi"))
    if w_mode == "mock" and total_worker_tokens < 200_000:
        tok_free = float(b_cfg.get("b1_mock_token_free", 16000))
        tok_hl = float(b_cfg.get("b1_mock_token_halflife", 14000))
    else:
        tok_free = float(b_cfg.get("b1_pi_token_free", 4800000))
        tok_hl = float(b_cfg.get("b1_pi_token_halflife", 4500000))

    if approved_count == 0:
        b1_token_score = 0.0
    elif total_worker_tokens <= tok_free:
        b1_token_score = 3.0
    else:
        b1_token_score = 3.0 * (0.5 ** ((total_worker_tokens - tok_free) / max(1.0, tok_hl)))

    contract_chars = int(state.get("contract_chars", 0))
    if contract_chars < 220:
        snr_con = 0.1 if contract_chars > 0 else 0.0
    elif contract_chars <= 4200:
        snr_con = 0.8
    else:
        snr_con = max(0.25, 0.8 - 0.55 * ((contract_chars - 4200) / 1300.0))

    avg_t = obs["avg_ticket_chars"]
    if avg_t < 80 or approved_count == 0:
        snr_tkt = 0.1 if approved_count > 0 else 0.0
    elif avg_t <= 1250:
        snr_tkt = 0.7
    else:
        snr_tkt = max(0.2, 0.7 - 0.5 * ((avg_t - 1250) / 750.0))
    b1_snr_score = snr_con + snr_tkt

    # B1.3 零违规与操作合规度 (2.5)
    smug_pen = float(b_cfg.get("b1_smuggling_penalty", 0.8)) * smuggling_count
    mid_b_pen = float(b_cfg.get("b1_midflight_contract_penalty", 0.8)) * midflight_contract_revs
    err_pen = float(b_cfg.get("b1_tool_error_penalty", 0.35)) * tool_error_count
    b1_disc_score = max(0.0, 2.5 - smug_pen - mid_b_pen - err_pen) if approved_count > 0 else 0.0

    b1_raw = min(
        float(b_cfg.get("b1_economy_max", 10.0)),
        b1_parallel_score + b1_disp_score + b1_conv_score + b1_token_score + b1_snr_score + b1_disc_score,
    )
    b1_final = round(b1_raw * cap_ratio, 2)

    # B2 关键路径墙钟耗时 (10.0)
    free_min = float(b_cfg.get("b2_free_min", 20.0))
    hl_min = float(b_cfg.get("b2_halflife_min", 18.0))
    b2_max = float(b_cfg.get("b2_timing_max", 10.0))
    if approved_count == 0:
        b2_raw = 0.0
    elif e2e_min <= free_min:
        b2_raw = b2_max
    else:
        b2_raw = b2_max * (0.5 ** ((e2e_min - free_min) / max(0.1, hl_min)))
    b2_final = round(b2_raw * cap_ratio, 2)
    b_total = round(b1_final + b2_final, 2)

    # ---------------- C 层：五大统领过程管控维度（25 分 = C1 8 + C2 9 + C3 8） ----------------
    if approved_count == 0:
        c_gate = 0.0
    else:
        gate_th = float(c_cfg.get("low_delivery_gate_threshold_a", 15.0))
        gate_fl = float(c_cfg.get("low_delivery_gate_floor", 0.20))
        c_gate = 1.0 if a_total >= gate_th else max(gate_fl, a_total / max(1.0, gate_th))

    active_workers = [w for w in workers.values() if int(w.get("approved_count", 0)) > 0]
    ghost_workers_cnt = sum(1 for w in workers.values() if int(w.get("approved_count", 0)) == 0)
    n_active = len(active_workers)

    # C1 (满分 8.0)：先侦察后决策情报纪律 (2.5) + 领地正交零冲突 (2.5) + 契约前瞻与二阶段抗冲击弹性 (3.0)
    c1_recon = (
        1.75 * (len(obs["recon_before_dispatch"]) / 5.0)
        + 0.75 * (len(obs["recon_before_contract"]) / 5.0)
    )

    all_paths_list = [p for w in workers.values() for p in (w.get("allowed_paths") or [])]
    unique_paths = set(all_paths_list)
    overlap_extra = max(0, len(all_paths_list) - len(unique_paths))
    disjoint_ratio = 1.0 - (overlap_extra / max(1, len(all_paths_list)))
    max_files_bound = max((len(w.get("allowed_paths") or []) for w in workers.values()), default=0)
    if n_active <= 1:
        focus_factor = 0.25
    elif max_files_bound <= 2:
        focus_factor = 1.0
    elif max_files_bound == 3:
        focus_factor = 0.98
    else:
        focus_factor = 0.75
    merge_conflicts = int(state.get("merge_conflicts_count", 0))
    syntax_broken = int(state.get("syntax_broken_merges", 0))
    c1_ortho_clean = 1.5 * disjoint_ratio * focus_factor + max(0.0, 1.0 - 0.6 * merge_conflicts - 0.6 * syntax_broken)

    contract_first = bool(state.get("contract_defined_before_first_dispatch"))
    c1_con_pre = 0.8 if (contract_first and contract_chars >= 320) else (0.35 if contract_first and contract_chars >= 120 else 0.0)
    ast_align = _ast_contract_alignment_ratio(run_path / "workspace")
    if obs["phase2_unlocked"]:
        p2_score = max(
            0.0,
            1.5 - 0.45 * obs["phase2_reworks"] - (0.6 if obs["post_p2_regression_fail"] else 0.0),
        )
    else:
        p2_score = 0.0
    mid_c1_pen = float(c_cfg.get("c1_midflight_contract_penalty", 1.5)) * midflight_contract_revs
    c1_raw = max(0.0, c1_recon + c1_ortho_clean + c1_con_pre + 0.7 * ast_align + p2_score - mid_c1_pen)
    c1_final = round(min(8.0, c1_raw * c_gate), 2)

    # C2 (满分 9.0)：全裁决零盲裁实证预检 (3.5) + 打回一击修平率/零免费返工 (3.0) + 阶段回归与断言密度 (2.5)
    blind_cnt = len(obs["blind_verdict_subs"])
    blind_pen = float(c_cfg.get("blind_verdict_penalty", 0.65)) * blind_cnt
    c2_zero_blind = max(
        0.0,
        3.5 * (obs["verified_verdicts"] / max(1, obs["total_verdicts"])) - blind_pen,
    )

    rw_decay = float(c_cfg.get("rework_step_decay", 0.30))
    ineff_pen = float(c_cfg.get("ineffective_rework_penalty", 0.90))
    if rework_churn == 0 and obs["reject_count"] == 0:
        c2_rework_prec = 3.0 * min(1.0, a_total / max(1.0, 0.9 * a_max))
    else:
        one_shot_rate = obs["one_shot_fixes"] / max(1, obs["reject_count"])
        c2_rework_prec = max(
            0.0,
            2.85 * one_shot_rate - rw_decay * max(0, rework_churn - 1) - ineff_pen * obs["ineffective_reworks"],
        )

    approve_indices = [
        (idx, (ev.get("payload") or {}).get("merged_files") or [])
        for idx, ev in enumerate(trace_events)
        if ev.get("action") == "review_submission" and (ev.get("payload") or {}).get("decision") == "APPROVE"
    ]
    last_approve_idx = approve_indices[-1][0] if approve_indices else -1
    post_merge_ut = any(
        idx > last_approve_idx
        and ev.get("action") == "inspect_and_test"
        and (ev.get("payload") or {}).get("target") == "unittest"
        and (ev.get("payload") or {}).get("passed") is True
        for idx, ev in enumerate(trace_events)
    )
    c2_test_depth = (
        (0.6 if obs["pre_p2_tested"] else 0.0)
        + (0.8 if post_merge_ut else 0.0)
        + 1.1 * min(1.0, obs["test_assert_cnt"] / 12.0)
    )
    c2_raw = max(0.0, c2_zero_blind + c2_rework_prec + c2_test_depth)
    c2_final = round(min(9.0, c2_raw * c_gate), 2)

    # C3 (满分 8.0)：三工位满编激活 (2.5) + 频次与代码量双均衡 (3.0) + 并行/交替流转防单点死磕 (2.5)
    if n_active <= 1:
        c3_size = 0.0
    elif n_active == 2:
        c3_size = 1.3
    else:
        c3_size = 2.5
    c3_size = max(0.0, c3_size - 0.8 * ghost_workers_cnt)

    if n_active <= 1:
        c3_disp_ent = 0.0
        c3_code_bal = 0.0
    else:
        counts = [max(1, int(w.get("dispatch_count", 0))) for w in active_workers]
        tot_c = sum(counts)
        ent = -sum((c / tot_c) * math.log(c / tot_c) for c in counts if c > 0)
        c3_disp_ent = 1.5 * (ent / math.log(n_active))
        max_sh = obs["max_worker_code_share"]
        if max_sh <= 0.58:
            c3_code_bal = 1.5
        elif max_sh >= 0.90:
            c3_code_bal = 0.2
        else:
            c3_code_bal = 1.5 - 1.3 * ((max_sh - 0.58) / 0.32)

    first_app_merge_idx = next((idx for idx, files in approve_indices if "cloudhub/app.py" in files), None)
    first_domain_merge_idx = next(
        (idx for idx, files in approve_indices if any(f.startswith("cloudhub/") and f != "cloudhub/app.py" for f in files)),
        None,
    )
    bottom_up_ok = first_domain_merge_idx is not None and (first_app_merge_idx is None or first_domain_merge_idx <= first_app_merge_idx)

    streak_excess = max(0, obs["max_same_worker_streak"] - 2)
    streak_pen = float(c_cfg.get("same_worker_streak_penalty", 0.85)) * streak_excess
    bloat_th = int(c_cfg.get("dispatch_bloat_threshold", 10))
    bloat_pen = float(c_cfg.get("dispatch_bloat_penalty", 0.4)) * max(0, dispatches_used - bloat_th)

    c3_flow = (
        (0.4 if contract_first else 0.0)
        + (0.4 if bottom_up_ok else 0.0)
        + 1.7 * min(1.0, 0.35 + 0.65 * min(1.0, obs["parallel_ratio"] / 0.35))
        - streak_pen
        - bloat_pen
    )
    c3_raw = max(0.0, c3_size + c3_disp_ent + c3_code_bal + max(0.0, c3_flow))
    c3_final = round(min(8.0, c3_raw * c_gate), 2)

    c_total = round(c1_final + c2_final + c3_final, 2)

    return {
        "tampered": False,
        "trace_verified": trace_ok,
        "trace_message": trace_msg,
        "B1": b1_final,
        "B2": b2_final,
        "B_total": b_total,
        "B_detail": {
            "b1_raw": round(b1_raw, 2),
            "b1_parallel": round(b1_parallel_score, 2),
            "b1_token_efficiency": round(b1_token_score, 2),
            "b1_snr": round(b1_snr_score, 2),
            "b1_discipline": round(b1_disc_score, 2),
            "b2_raw": round(b2_raw, 2),
            "cap_ratio": round(cap_ratio, 4),
            "e2e_min": round(e2e_min, 3),
            "b2_reliable": bool(b2_reliable),
            "parallel_ratio": obs["parallel_ratio"],
            "conversion_ratio": round(conv_ratio, 3),
            "worker_tokens_total": total_worker_tokens,
            "code_smuggling_count": smuggling_count,
            "tool_error_count": tool_error_count,
        },
        "C1": c1_final,
        "C2": c2_final,
        "C3": c3_final,
        "C_total": c_total,
        "C_detail": {
            "c_gate": round(c_gate, 3),
            "c1_recon": round(c1_recon, 2),
            "c1_ortho_clean": round(c1_ortho_clean, 2),
            "c1_phase2_evolution": round(p2_score, 2),
            "c2_zero_blind_verdict": round(c2_zero_blind, 2),
            "c2_rework_precision": round(c2_rework_prec, 2),
            "c2_test_depth": round(c2_test_depth, 2),
            "c3_size": round(c3_size, 2),
            "c3_dual_balance": round(c3_disp_ent + c3_code_bal, 2),
            "c3_flow": round(max(0.0, c3_flow), 2),
            "blind_verdicts_count": blind_cnt,
            "max_same_worker_streak": obs["max_same_worker_streak"],
            "max_worker_code_share": obs["max_worker_code_share"],
            "test_assert_count": obs["test_assert_cnt"],
            "midflight_contract_revisions": midflight_contract_revs,
        },
        "run_summary": {
            "workers_total": len(workers),
            "workers_active": n_active,
            "ghost_workers": ghost_workers_cnt,
            "phase2_unlocked": obs["phase2_unlocked"],
            "recon_modules_before_dispatch": len(obs["recon_before_dispatch"]),
            "parallel_ratio": obs["parallel_ratio"],
            "max_same_worker_streak": obs["max_same_worker_streak"],
            "blind_verdicts_count": blind_cnt,
            "contract_version": int(state.get("contract_version", 0)),
            "contract_chars": contract_chars,
            "contract_before_dispatch": contract_first,
            "midflight_contract_revisions": midflight_contract_revs,
            "dispatches_used": dispatches_used,
            "approved_count": approved_count,
            "rejected_count": len(rejected_subs),
            "rework_churn": rework_churn,
            "one_shot_fixes": obs["one_shot_fixes"],
            "code_smuggling_count": smuggling_count,
            "worker_tokens_total": total_worker_tokens,
            "test_assert_count": obs["test_assert_cnt"],
            "e2e_min": round(e2e_min, 2),
        },
    }


def _scrub_secrets(text: str, secret_tokens: set[str]) -> str:
    out = text or ""
    for tok in sorted(secret_tokens, key=len, reverse=True):
        if tok and len(tok) >= 3:
            out = re.sub(re.escape(tok), "anonymous-marshal", out, flags=re.IGNORECASE)
    out = re.sub(r"runs[\\/][^\\/\s\"']+", "runs/anonymous-marshal", out, flags=re.IGNORECASE)
    return out


def generate_anonymized_dossier(run_dir: Path, case_id: str, secret_tokens: set[str]) -> dict[str, str]:
    """从冻结运行产物生成完全脱敏的《统帅调度档案》（含五大统领过程行为客观取证 `[P]` 组，绝不含模型名、耗时与客观分）。"""
    run_path = Path(run_dir).resolve()
    state = json.loads((run_path / ".marshal_state" / "state.json").read_text(encoding="utf-8"))
    _, _, trace_events = verify_trace_chain(run_path / "trace.jsonl")
    obs = extract_process_observables(run_path, state, trace_events)

    contract_file = run_path / ".marshal_state" / "GLOBAL_CONTRACT.md"
    contract_raw = contract_file.read_text(encoding="utf-8") if contract_file.is_file() else "（未定义全局架构契约）"
    retro_file = run_path / "briefing" / "RETROSPECTIVE.md"
    retro_raw = retro_file.read_text(encoding="utf-8") if retro_file.is_file() else "（未填写统领复盘说明）"

    workers = state.get("workers") or {}
    subs = state.get("submissions") or {}
    total_worker_tokens = sum(int((s.get("worker_tokens") or {}).get("total_tokens") or 0) for s in subs.values())
    missed_legacy = [m for m in LEGACY_CORE_MODULES if m not in obs["recon_before_dispatch"]]

    lines: list[str] = [
        f"# 统帅调度档案 · {case_id}",
        "",
        "> **盲评须知**：本档案已彻底抹除被测统帅模型标识、墙钟绝对耗时与客观得分。请先阅读同目录下的 `REFERENCE_LEVEL5.md` 与 `RUBRIC.md`（含 15 分平庸硬锁顶、25 分宗师准入门槛及六项过程硬扣分清单），严格依据下方证据编号打分！",
        "",
        "## 一、全局统领概览与五大过程维度客观取证（`[C]` / `[P]` 证据组）",
        "",
        f"- `[C01]` 全局架构契约修订次数：`{state.get('contract_version', 0)}` 次（其中非豁免中途补丁改约：`{state.get('contract_revisions_after_dispatch', 0)}` 次），终版字符数：`{state.get('contract_chars', 0)}` 字符（硬上限 5500 字符）",
        f"- `[C02]` 契约是否先于首张派单确立：`{'是（契约先行）' if state.get('contract_defined_before_first_dispatch') else '否（未立契约即裸派单）'}`",
        f"- `[C03]` 团队工位总数：`{len(workers)}` 个（硬上限 3 个，单工位上限 3 文件）；总派单轮次：`{state.get('dispatches_used', 0)}` 单（上限 {state.get('max_dispatches', 15)} 单）；违规代写代码拦截次数：`{state.get('code_smuggling_count', 0)}` 次；工具调用报错次数：`{state.get('tool_error_count', 0)}` 次",
        f"- `[P01]` **开局存量侦察纪律**：首张工单派发前实际调阅存量核心模块 `{len(obs['recon_before_dispatch'])} / 5` 个（已读：`{obs['recon_before_dispatch']}`；漏读：`{missed_legacy}`；其中立契约前已读 `{len(obs['recon_before_contract'])} / 5` 个）",
        f"- `[P02]` **三工位协同节奏与忙闲均衡**：同波次并行/交替并发派单 `{obs['co_scheduled_count']} / {obs['initial_dispatches_count']}` 单（并行率 `{obs['parallel_ratio'] * 100:.1f}%`）；单工位连续死磕最大轮次：`{obs['max_same_worker_streak']}` 轮；各工位合入代码字符分布：`{obs['merged_chars_by_worker']}`（最大单工位吞吐占比 `{obs['max_worker_code_share'] * 100:.1f}%`）",
        f"- `[P03]` **二阶段需求演进与抗冲击弹性**：是否解锁承接第二阶段需求：`{'是（已解锁 PHASE2_TASK.md）' if obs['phase2_unlocked'] else '否（未解锁第二阶段！）'}`；二阶段返工次数：`{obs['phase2_reworks']}` 次；解锁二阶段前是否跑过连通/回归测试：`{'是' if obs['pre_p2_tested'] else '否'}`",
        f"- `[P04]` **实证主义审查纪律（盲裁检测）**：累计裁决 `{obs['total_verdicts']}` 次，裁决前已执行代码预检或叠加测试 `{obs['verified_verdicts']}` 次；**未预检直接盲批/盲打回次数：`{len(obs['blind_verdict_subs'])}` 次**（涉及提交：`{obs['blind_verdict_subs'] or '无'}`）",
        f"- `[P05]` **打回纠偏精准度与指令杠杆率**：累计打回 `{obs['reject_count']}` 次，一击修平（1 次返工即合入）`{obs['one_shot_fixes']}` 次，低效多轮返工/废弃 `{obs['ineffective_reworks']}` 次；平均单张工单字数：`{obs['avg_ticket_chars']}` 字符；下属工人累计消耗词元：`{total_worker_tokens:,}`",
        f"- `[P06]` **终局测试工程真实度**：`tests/` 下测试文件 `{obs['test_file_cnt']}` 个，静态解析真实 `assert` 断言总数：`{obs['test_assert_cnt']}` 条（请与 `[M01]` 复盘自述交叉核对是否存在夸大虚报）",
        "",
        "### 终版《全局架构与跨模块接口契约》全文（`[C04]`）",
        "",
        "```markdown",
        _scrub_secrets(contract_raw.strip(), secret_tokens),
        "```",
        "",
        "---",
        "",
        "## 二、团队建制、岗位提示词与白名单领地配置表（`[W]` 证据组）",
        "",
    ]

    if not workers:
        lines.append("- `[W00]` 未创建任何下属工位。")
    else:
        for idx, (wid, w) in enumerate(workers.items(), 1):
            allowed = ", ".join(f"`{p}`" for p in (w.get("allowed_paths") or []))
            lines.extend(
                [
                    f"### `[W{idx:02d}]` 工位 `{wid}`",
                    f"- **绑定读写白名单（共 {len(w.get('allowed_paths') or [])} 个文件）**：{allowed}",
                    f"- **累计派单 / 批准合入 / 打回重做**：`{w.get('dispatch_count', 0)}` / `{w.get('approved_count', 0)}` / `{w.get('rejected_count', 0)}`",
                    f"- **岗位系统提示词（`role_prompt`）**：",
                    "```text",
                    _scrub_secrets((w.get("role_prompt") or "").strip(), secret_tokens),
                    "```",
                    "",
                ]
            )

    lines.extend(
        [
            "---",
            "",
            "## 三、完整派单、测试自检与代码审查打回轨迹（Mission Transcript，`[T]`/`[R]` 证据组）",
            "",
        ]
    )

    t_idx = 0
    r_idx = 0
    for step_no, ev in enumerate(trace_events, 1):
        act = ev.get("action")
        pl = ev.get("payload") or {}
        if act == "start_run":
            continue
        if act == "define_contract":
            p2_tag = "（使用二阶段免费契约演进额度）" if pl.get("used_phase2_evolution_credit") else ""
            lines.append(
                f"- **Step #{step_no:02d} · 立规矩 `define_contract`**{p2_tag}：更新契约至 v{pl.get('contract_version')}（{pl.get('contract_chars')} 字符）。"
            )
        elif act == "unlock_phase2":
            lines.append(
                f"### Step #{step_no:02d} · `[P03]` 解锁第二阶段紧急演进需求 `unlock_phase2`（已完成 {pl.get('phase2_unlocked_at_dispatch')} 单一阶段派单）\n"
            )
        elif act == "manage_worker":
            lines.append(
                f"- **Step #{step_no:02d} · 建工位 `manage_worker`**：配置 `{pl.get('worker_id')}`，白名单 `{pl.get('allowed_paths')}`"
                + (f"，领地重叠警告 `{pl.get('overlaps')}`" if pl.get("overlaps") else "（零领地重叠）")
                + "。"
            )
        elif act == "dispatch_task":
            t_idx += 1
            tag = f"[T{t_idx:02d}]"
            w_tag = f"（并行波次 `{pl.get('wave_id')}`）" if pl.get("wave_id") else ""
            lines.extend(
                [
                    f"### Step #{step_no:02d} · {tag} 拆单派活 `dispatch_task` → `{pl.get('worker_id')}`（生成 `{pl.get('submission_id')}`）{w_tag}",
                    f"- **统帅下发工单原文**：",
                    "```text",
                    _scrub_secrets((pl.get("ticket") or "").strip(), secret_tokens),
                    "```",
                    f"- **下属工人执行回报**：改动文件 `{pl.get('changed_files')}`，语法自检 `{'通过' if pl.get('syntax_ok') else '报错'}`"
                    + (f"，越界被剥离文件 `{pl.get('unauthorized_files_stripped')}`" if pl.get("unauthorized_files_stripped") else "")
                    + f"。摘要：{_scrub_secrets(str(pl.get('worker_summary') or ''), secret_tokens)}",
                    "",
                ]
            )
        elif act == "inspect_and_test":
            tgt = pl.get("target")
            sub_ref = f"（叠加 `{pl.get('submission_id')}`）" if pl.get("submission_id") else ""
            if tgt in ("smoke", "unittest"):
                lines.append(
                    f"- **Step #{step_no:02d} · 跑自检 `inspect_and_test({tgt})`{sub_ref}**：结果 `{'PASS' if pl.get('passed') else 'FAIL'}`（退出码 `{pl.get('returncode')}`）。"
                )
            else:
                lines.append(
                    f"- **Step #{step_no:02d} · 查代码 `inspect_and_test({tgt})`{sub_ref}**：审阅代码或状态 `{pl.get('path') or ''}`。"
                )
        elif act == "review_submission":
            r_idx += 1
            rtag = f"[R{r_idx:02d}]"
            dec = pl.get("decision")
            pre_chk = "是" if (pl.get("inspected_before_review") or pl.get("tested_before_review")) else "否（盲裁/未预检！）"
            lines.extend(
                [
                    f"### Step #{step_no:02d} · {rtag} 审查裁决 `review_submission({dec})` · 目标 `{pl.get('submission_id')}`（工位 `{pl.get('worker_id')}`）",
                    f"- **裁决前是否执行代码审阅或叠加测试**：`{pre_chk}`",
                    f"- **统帅审查批注原文**：",
                    "```text",
                    _scrub_secrets((pl.get("comment") or "（无批注）").strip(), secret_tokens),
                    "```",
                    "",
                ]
            )
        elif act == "rework_completed":
            lines.append(
                f"- **Step #{step_no:02d} · 返工交付 `rework_completed`**：工位 `{pl.get('worker_id')}` 针对 `{pl.get('rework_of')}` 生成新提交 `{pl.get('new_submission_id')}`，改动 `{pl.get('changed_files')}`，语法 `{'通过' if pl.get('syntax_ok') else '报错'}`。"
            )
        elif act in ("code_smuggling_intercepted", "direct_write_tamper_detected", "tool_error"):
            lines.append(
                f"- **Step #{step_no:02d} · `[V]` 调度异常/护栏记录 `{act}`**：`{_scrub_secrets(json.dumps(pl, ensure_ascii=False)[:300], secret_tokens)}`"
            )

    lines.extend(
        [
            "---",
            "",
            "## 四、统帅自撰《统领复盘与架构说明》（`[M01]`，请与上方真实轨迹及 `[P01]-[P06]` 交叉核验诚实度）",
            "",
            _scrub_secrets(retro_raw.strip(), secret_tokens),
            "",
            "---",
            "",
            f"## 五、脱敏终局主干代码索引（详见同目录 `{case_id}_code/`）",
            "",
        ]
    )

    ws_dir = run_path / "workspace"
    code_files: dict[str, str] = {}
    for fp in sorted(ws_dir.rglob("*.py")):
        rel = fp.relative_to(ws_dir).as_posix()
        if rel == "smoke_public.py":
            continue
        scrubbed_code = _scrub_secrets(fp.read_text(encoding="utf-8"), secret_tokens)
        code_files[rel] = scrubbed_code
        line_cnt = len(scrubbed_code.splitlines())
        lines.append(f"- `{case_id}_code/{rel}`（`{line_cnt}` 行）")

    return {
        "dossier_markdown": "\n".join(lines) + "\n",
        "code_files": code_files,
    }
