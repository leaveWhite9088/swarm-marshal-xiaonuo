# -*- coding: utf-8 -*-
"""B 层（统领效率 15 分）+ C 层（组织管控 25 分）指标计算与匿名《统帅调度档案》提取器（metrics.py）。"""
from __future__ import annotations

import ast
import datetime
import json
import math
import re
from pathlib import Path

from src.marshal.state_store import verify_trace_chain


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
    for mod_name, tree in trees_by_mod.items():
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
        # 单文件无跨模块拆分时跨模块契约对齐得分为 0.3（不鼓励单文件巨石）
        return 0.3 if len(py_files) == 1 else 0.5
    return valid_refs / total_refs


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
    a_max = float((s_cfg.get("A") or {}).get("max", 30.0))
    b_cfg = s_cfg.get("B") or {}
    c_cfg = s_cfg.get("C") or {}

    timing = proto.get("timing") or {}
    t0 = _parse_iso(timing.get("t0_iso")) or _parse_iso(timing.get("process_iso"))
    t1 = _parse_iso(timing.get("end_iso"))
    pause_sec = max(0.0, float(timing.get("infra_pause_sec") or 0.0))
    if t0 is not None and t1 is not None and t1 >= t0:
        e2e_sec = max(0.0, (t1 - t0) - pause_sec)
    else:
        # 若单局由 score_run 单独调试尚未写 end_iso，则取最后一条 trace 时间
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

    # ---------------- B 层：统领效率与经济性（15 分，受 A / 30 封顶） ----------------
    cap_ratio = (a_total / a_max) if b_cfg.get("cap_by_a_ratio", True) else 1.0
    cap_ratio = max(0.0, min(1.0, cap_ratio))

    ideal_d = int(b_cfg.get("b1_ideal_dispatches", 7))
    max_d = int(b_cfg.get("b1_max_dispatches", 14))
    if dispatches_used == 0 or approved_count == 0:
        b1_disp_score = 0.0
    elif dispatches_used <= ideal_d:
        b1_disp_score = 2.0
    elif dispatches_used >= max_d:
        b1_disp_score = 0.0
    else:
        b1_disp_score = 2.0 * (max_d - dispatches_used) / max(1, max_d - ideal_d)

    conv_ratio = approved_count / max(1, dispatches_used)
    floor_c = float(b_cfg.get("b1_floor_first_pass_ratio", 0.35))
    ideal_c = float(b_cfg.get("b1_ideal_first_pass_ratio", 0.70))
    if dispatches_used == 0:
        b1_conv_score = 0.0
    elif conv_ratio >= ideal_c:
        b1_conv_score = 2.0
    elif conv_ratio <= floor_c:
        b1_conv_score = 0.0
    else:
        b1_conv_score = 2.0 * (conv_ratio - floor_c) / max(0.01, ideal_c - floor_c)

    smug_pen = float(b_cfg.get("b1_smuggling_penalty", 0.5)) * smuggling_count
    mid_b_pen = float(b_cfg.get("b1_midflight_contract_penalty", 0.5)) * midflight_contract_revs
    err_pen = float(b_cfg.get("b1_tool_error_penalty", 0.25)) * tool_error_count
    b1_disc_score = max(0.0, 1.0 - smug_pen - mid_b_pen - err_pen) if approved_count > 0 else 0.0

    b1_raw = min(float(b_cfg.get("b1_economy_max", 5.0)), b1_disp_score + b1_conv_score + b1_disc_score)
    b1_final = round(b1_raw * cap_ratio, 2)

    free_min = float(b_cfg.get("b2_free_min", 6.0))
    hl_min = float(b_cfg.get("b2_halflife_min", 8.0))
    b2_max = float(b_cfg.get("b2_timing_max", 10.0))
    if approved_count == 0:
        b2_raw = 0.0
    elif e2e_min <= free_min:
        b2_raw = b2_max
    else:
        b2_raw = b2_max * (0.5 ** ((e2e_min - free_min) / max(0.1, hl_min)))
    b2_final = round(b2_raw * cap_ratio, 2)
    b_total = round(b1_final + b2_final, 2)

    # ---------------- C 层：组织管控与把关（25 分） ----------------
    if approved_count == 0:
        c_gate = 0.0
    else:
        gate_th = float(c_cfg.get("low_delivery_gate_threshold_a", 12.0))
        gate_fl = float(c_cfg.get("low_delivery_gate_floor", 0.25))
        c_gate = 1.0 if a_total >= gate_th else max(gate_fl, a_total / max(1.0, gate_th))

    active_workers = [w for w in workers.values() if int(w.get("approved_count", 0)) > 0]
    ghost_workers_cnt = sum(1 for w in workers.values() if int(w.get("approved_count", 0)) == 0)
    n_active = len(active_workers)

    # C1: 角色领地正交度与跨模块零踩踏冲突（满分 9.0）
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
        focus_factor = 0.92
    else:
        focus_factor = 0.80
    c1_ortho = 3.5 * disjoint_ratio * focus_factor

    merge_conflicts = int(state.get("merge_conflicts_count", 0))
    syntax_broken = int(state.get("syntax_broken_merges", 0))
    c1_clean_merge = max(0.0, 2.5 - 1.25 * merge_conflicts - 1.25 * syntax_broken)

    contract_first = bool(state.get("contract_defined_before_first_dispatch"))
    contract_chars = int(state.get("contract_chars", 0))
    if contract_first and contract_chars >= 320:
        c1_con_pre = 1.5
    elif contract_first and contract_chars >= 100:
        c1_con_pre = 0.7
    elif contract_chars >= 200:
        c1_con_pre = 0.4
    else:
        c1_con_pre = 0.0
    ast_align = _ast_contract_alignment_ratio(run_path / "workspace")
    c1_contract_align = c1_con_pre + 1.5 * ast_align
    mid_c1_pen = float(c_cfg.get("c1_midflight_contract_penalty", 1.5)) * midflight_contract_revs
    c1_raw = max(0.0, c1_ortho + c1_clean_merge + c1_contract_align - mid_c1_pen)
    c1_final = round(min(9.0, c1_raw * c_gate), 2)

    free_rw = int(c_cfg.get("free_reworks_allowance", 2))
    excess_reworks = max(0, rework_churn - free_rw)
    rw_pen_half = 0.5 * float(c_cfg.get("excess_rework_penalty", 1.2)) * excess_reworks
    bloat_th = int(c_cfg.get("dispatch_bloat_threshold", 9))
    bloat_pen = float(c_cfg.get("dispatch_bloat_penalty", 0.5)) * max(0, dispatches_used - bloat_th)

    # C2: 代码审查把关与缺陷拦截率（满分 8.0）
    inspected_approvals = sum(1 for s in approved_subs if s.get("inspected_before_review") or s.get("tested_before_review"))
    c2_pre_inspect = 3.5 * (inspected_approvals / max(1, approved_count))

    reject_events = [
        ev["payload"]
        for ev in trace_events
        if ev.get("action") == "review_submission" and (ev.get("payload") or {}).get("decision") == "REJECT"
    ]
    specific_rejects = sum(
        1
        for r in reject_events
        if len((r.get("comment") or "").strip()) >= 35
    )
    if specific_rejects >= 1 and syntax_broken == 0:
        c2_defect_gate = 2.5
    elif len(reject_events) >= 1:
        # 打回批注敷衍（如“报错了请重修”）
        c2_defect_gate = 1.1
    else:
        if a_total >= 27.0:
            c2_defect_gate = 2.2
        else:
            c2_defect_gate = 1.25 * (a_total / max(1.0, a_max))

    unittest_passed = any(
        ev.get("action") == "inspect_and_test"
        and (ev.get("payload") or {}).get("target") == "unittest"
        and (ev.get("payload") or {}).get("passed") is True
        for ev in trace_events
    )
    smoke_passed = any(
        ev.get("action") == "inspect_and_test"
        and (ev.get("payload") or {}).get("target") == "smoke"
        and (ev.get("payload") or {}).get("passed") is True
        for ev in trace_events
    )
    c2_test_exec = (1.1 if unittest_passed else 0.0) + (0.9 if smoke_passed else 0.0)
    c2_raw = max(0.0, c2_pre_inspect + c2_defect_gate + c2_test_exec - rw_pen_half)
    c2_final = round(min(8.0, c2_raw * c_gate), 2)

    # C3: 团队分工均衡度与拓扑节奏（满分 8.0）
    if n_active <= 1:
        c3_size = 0.0
    elif n_active == 2:
        c3_size = 1.6
    elif n_active == 3:
        c3_size = 2.6
    else:
        c3_size = 3.5
    c3_size = max(0.0, c3_size - 0.8 * ghost_workers_cnt)

    if n_active <= 1:
        c3_balance = 0.0
    else:
        counts = [max(1, int(w.get("dispatch_count", 0))) for w in active_workers]
        tot_c = sum(counts)
        ent = -sum((c / tot_c) * math.log(c / tot_c) for c in counts if c > 0)
        norm_ent = ent / math.log(n_active)
        c3_balance = 2.5 * norm_ent

    # 拓扑秩序：契约先行(0.7) + 底层模块先于网关合入(0.7) + 末次合入后执行终局测试验证(0.6)
    approve_indices = [
        (idx, (ev.get("payload") or {}).get("merged_files") or [])
        for idx, ev in enumerate(trace_events)
        if ev.get("action") == "review_submission" and (ev.get("payload") or {}).get("decision") == "APPROVE"
    ]
    first_app_merge_idx = next((idx for idx, files in approve_indices if "cloudhub/app.py" in files), None)
    first_domain_merge_idx = next(
        (idx for idx, files in approve_indices if any(f.startswith("cloudhub/") and f != "cloudhub/app.py" for f in files)),
        None,
    )
    bottom_up_ok = (
        first_domain_merge_idx is not None
        and (first_app_merge_idx is None or first_domain_merge_idx <= first_app_merge_idx)
    )
    last_approve_idx = approve_indices[-1][0] if approve_indices else -1
    post_merge_verified = any(
        idx > last_approve_idx
        and ev.get("action") == "inspect_and_test"
        and (ev.get("payload") or {}).get("target") in ("smoke", "unittest")
        and (ev.get("payload") or {}).get("passed") is True
        for idx, ev in enumerate(trace_events)
    )
    c3_topo = (0.7 if contract_first else 0.0) + (0.7 if bottom_up_ok else 0.0) + (0.6 if post_merge_verified else 0.0)
    c3_raw = max(0.0, c3_size + c3_balance + c3_topo - rw_pen_half - bloat_pen)
    c3_final = round(min(8.0, c3_raw * c_gate), 2)

    c_total = round(c1_final + c2_final + c3_final, 2)

    total_worker_tokens = sum(
        int((s.get("worker_tokens") or {}).get("total_tokens") or 0)
        for s in subs.values()
    )

    return {
        "tampered": False,
        "trace_verified": trace_ok,
        "trace_message": trace_msg,
        "B1": b1_final,
        "B2": b2_final,
        "B_total": b_total,
        "B_detail": {
            "b1_raw": round(b1_raw, 2),
            "b2_raw": round(b2_raw, 2),
            "cap_ratio": round(cap_ratio, 4),
            "e2e_min": round(e2e_min, 3),
            "b2_reliable": bool(b2_reliable),
            "conversion_ratio": round(conv_ratio, 3),
            "code_smuggling_count": smuggling_count,
            "tool_error_count": tool_error_count,
        },
        "C1": c1_final,
        "C2": c2_final,
        "C3": c3_final,
        "C_total": c_total,
        "C_detail": {
            "c_gate": round(c_gate, 3),
            "c1_ortho": round(c1_ortho, 2),
            "c1_clean_merge": round(c1_clean_merge, 2),
            "c1_contract_align": round(c1_contract_align, 2),
            "c2_pre_inspect": round(c2_pre_inspect, 2),
            "c2_defect_gate": round(c2_defect_gate, 2),
            "c2_test_exec": round(c2_test_exec, 2),
            "c3_size": round(c3_size, 2),
            "c3_balance": round(c3_balance, 2),
            "c3_topo": round(c3_topo, 2),
            "midflight_contract_revisions": midflight_contract_revs,
            "excess_reworks": excess_reworks,
            "rework_and_bloat_penalty": round(2 * rw_pen_half + bloat_pen, 2),
        },
        "run_summary": {
            "workers_total": len(workers),
            "workers_active": n_active,
            "ghost_workers": ghost_workers_cnt,
            "contract_version": int(state.get("contract_version", 0)),
            "contract_chars": contract_chars,
            "contract_before_dispatch": contract_first,
            "midflight_contract_revisions": midflight_contract_revs,
            "dispatches_used": dispatches_used,
            "approved_count": approved_count,
            "rejected_count": len(rejected_subs),
            "rework_churn": rework_churn,
            "specific_rejects": specific_rejects,
            "code_smuggling_count": smuggling_count,
            "worker_tokens_total": total_worker_tokens,
            "e2e_min": round(e2e_min, 2),
        },
    }


def _scrub_secrets(text: str, secret_tokens: set[str]) -> str:
    out = text or ""
    for tok in sorted(secret_tokens, key=len, reverse=True):
        if tok and len(tok) >= 3:
            out = re.sub(re.escape(tok), "anonymous-marshal", out, flags=re.IGNORECASE)
    # 清洗任何残留 runs/<xxx> 路径痕迹
    out = re.sub(r"runs[\\/][^\\/\s\"']+", "runs/anonymous-marshal", out, flags=re.IGNORECASE)
    return out


def generate_anonymized_dossier(run_dir: Path, case_id: str, secret_tokens: set[str]) -> dict[str, str]:
    """从冻结运行产物生成完全脱敏的《统帅调度档案》（绝不含模型名、墙钟时间与 A/B/C 分数）。"""
    run_path = Path(run_dir).resolve()
    state = json.loads((run_path / ".marshal_state" / "state.json").read_text(encoding="utf-8"))
    _, _, trace_events = verify_trace_chain(run_path / "trace.jsonl")

    contract_file = run_path / ".marshal_state" / "GLOBAL_CONTRACT.md"
    contract_raw = contract_file.read_text(encoding="utf-8") if contract_file.is_file() else "（未定义全局架构契约）"
    retro_file = run_path / "briefing" / "RETROSPECTIVE.md"
    retro_raw = retro_file.read_text(encoding="utf-8") if retro_file.is_file() else "（未填写统领复盘说明）"

    workers = state.get("workers") or {}

    # 1. 组装主事实档案卡 case-XX.md（带结构化证据编号 [C..]/[W..]/[T..]/[R..]/[M..]）
    lines: list[str] = [
        f"# 统帅调度档案 · {case_id}",
        "",
        "> **盲评须知**：本档案已彻底抹除被测统帅模型标识、墙钟耗时与客观得分。请先阅读同目录下的 `REFERENCE_LEVEL5.md`（官方 5 级传声筒平庸统帅参照样本）与 `RUBRIC.md`，凡建制、拆单与审查指导未明显超越 5 级样本者，D 层总分严格锁死 `<= 15 / 30`！",
        "",
        "## 一、全局统领概览与契约建立记录（`[C]` 证据组）",
        "",
        f"- `[C01]` 全局架构契约修订次数：`{state.get('contract_version', 0)}` 次（其中派单启动后中途打补丁改约：`{state.get('contract_revisions_after_dispatch', 0)}` 次），终版字符数：`{state.get('contract_chars', 0)}` 字符",
        f"- `[C02]` 契约是否先于首张派单确立：`{'是（契约先行）' if state.get('contract_defined_before_first_dispatch') else '否（未立契约即裸派单）'}`",
        f"- `[C03]` 团队工位总数：`{len(workers)}` 个（上限 5 个）；总派单轮次：`{state.get('dispatches_used', 0)}` 单（上限 {state.get('max_dispatches', 14)} 单，单工单返工熔断上限 2 次）；违规代写代码拦截次数：`{state.get('code_smuggling_count', 0)}` 次",
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
            lines.append(
                f"- **Step #{step_no:02d} · 立规矩 `define_contract`**：更新契约至 v{pl.get('contract_version')}（{pl.get('contract_chars')} 字符）。"
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
            lines.extend(
                [
                    f"### Step #{step_no:02d} · {tag} 拆单派活 `dispatch_task` → `{pl.get('worker_id')}`（生成 `{pl.get('submission_id')}`）",
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
            pre_chk = "是" if (pl.get("inspected_before_review") or pl.get("tested_before_review")) else "否（盲批/未预检）"
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
            "## 四、统帅自撰《统领复盘与架构说明》（`[M01]`，请与上方真实轨迹交叉核验诚实度）",
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
