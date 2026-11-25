#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统帅五大管理动作唯一命令行入口（tools/marshal.py）。

支持的五大动作：
  1. define_contract   — 建立或更新全局架构与跨模块接口契约（只读置顶注入给全体工人）
  2. manage_worker     — 招募或调整下属工位、岗位提示词与读写文件白名单（上限 5 工位，单工位 <= 4 文件）
  3. dispatch_task     — 向指定工位拆单派活，驱动底层锁定的弱模型（pi / mock）在隔离暂存区编码并返回 Diff
  4. inspect_and_test  — 查看团队状态、主干代码、暂存 Diff，或运行冒烟自检与单元/集成测试套件（支持暂存叠加预检）
  5. review_submission — 拍板批准合入主干（APPROVE）或附审查意见打回原工位返工重做（REJECT）
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import inspect_code_smuggling, set_workspace_writable, validate_allowed_paths  # noqa: E402
from src.marshal.phase2_spec import PHASE2_TASK_MD  # noqa: E402
from src.marshal.state_store import MarshalStateStore, now_iso  # noqa: E402
from src.marshal.worker_driver import dispatch_to_worker  # noqa: E402

WORKER_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,31}$")


def _read_text_input(direct: str | None, file_path: str | None, use_stdin: bool, label: str) -> str:
    if direct is not None:
        return direct
    if file_path is not None:
        p = Path(file_path)
        if not p.is_file():
            raise ValueError(f"找不到指定的 {label} 文件: {file_path}")
        return p.read_text(encoding="utf-8")
    if use_stdin:
        return sys.stdin.read()
    raise ValueError(f"必须提供 {label} 内容（直接传参、文件路径或 --stdin）。")


def _emit(obj: dict, exit_code: int = 0) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return exit_code


def _record_tool_error(store: MarshalStateStore, state: dict, action: str, msg: str, extra: dict | None = None) -> int:
    state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
    store.append_trace(
        state,
        event_type="tool_error",
        action=action,
        payload={"error": msg, **(extra or {})},
    )
    return _emit({"ok": False, "action": action, "error": msg, **(extra or {})}, exit_code=1)


def _record_infra_error(store: MarshalStateStore, state: dict, action: str, msg: str) -> int:
    """基础设施/远端网络瞬断：仅如实留痕，绝不消耗派单预算，也不计入统帅操作失误扣分。"""
    store.append_trace(
        state,
        event_type="infra_error",
        action=action,
        payload={"error": msg},
    )
    return _emit(
        {
            "ok": False,
            "action": action,
            "infra_error": True,
            "dispatches_used": state.get("dispatches_used", 0),
            "error": f"{msg}（基础设施异常未扣除派单预算，也未计入违规，请直接重试）",
        },
        exit_code=1,
    )


def _record_smuggling_block(store: MarshalStateStore, state: dict, action: str, detail: dict) -> int:
    state["code_smuggling_count"] = int(state.get("code_smuggling_count", 0)) + 1
    store.append_trace(
        state,
        event_type="violation",
        action="code_smuggling_intercepted",
        payload={"source_action": action, **detail},
    )
    return _emit(
        {
            "ok": False,
            "action": action,
            "error": "【防代写护栏拦截】检测到输入中包含大段现成 Python 业务实现代码！统帅只能写职责边界、函数签名契约、逻辑步骤、防呆约束或具体问题行批注，严禁替工人代写源码（本次已计入违规调度）。",
            "smuggling_detail": detail,
        },
        exit_code=2,
    )


def handle_define_contract(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    limits = config.get("marshal_limits") or {}
    try:
        content = _read_text_input(args.content, args.file, args.stdin, "全局架构契约").strip()
    except ValueError as exc:
        return _record_tool_error(store, state, "define_contract", str(exc))

    if not content:
        return _record_tool_error(store, state, "define_contract", "全局契约内容不能为空。")
    max_chars = int(limits.get("max_contract_chars", 15000))
    if len(content) > max_chars:
        return _record_tool_error(store, state, "define_contract", f"契约长度超出上限（{len(content)} > {max_chars} 字符）。")

    clean, detail = inspect_code_smuggling(content, "contract", limits)
    if not clean:
        return _record_smuggling_block(store, state, "define_contract", detail)

    res = store.write_contract(state, content)
    store.append_trace(
        state,
        event_type="tool",
        action="define_contract",
        payload={**res, "content": content},
    )
    if res.get("used_phase2_evolution_credit"):
        msg = "【二阶段契约平滑演进】已消耗 1 次二阶段免费契约升级额度（未计入事后改约折损），后续派单将同步新版契约。"
    elif res.get("midflight_revision"):
        msg = "【规划折损警告】全局契约已在派单中途被修改（已计入事后改约折损），后续派单与返工将同步新契约。"
    else:
        msg = "全局架构契约已先行确立，将在后续所有派单与返工中自动置顶只读注入。"
    return _emit(
        {
            "ok": True,
            "action": "define_contract",
            **res,
            "message": msg,
        }
    )


def handle_manage_worker(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    limits = config.get("marshal_limits") or {}
    wid = (args.worker_id or "").strip()
    if not WORKER_ID_RE.match(wid):
        return _record_tool_error(store, state, "manage_worker", f"非法的工位代号 {wid!r}（仅限字母开头、字母数字下划线短横线，2..32 字符）。")

    workers = state.setdefault("workers", {})
    max_workers = int(limits.get("max_workers", 5))
    is_new = wid not in workers
    if is_new and len(workers) >= max_workers:
        return _record_tool_error(
            store,
            state,
            "manage_worker",
            f"编制池已满（当前已有 {len(workers)} 个工位，上限 {max_workers} 个），请复用或调整既有工位。",
        )

    try:
        role_prompt = _read_text_input(args.role_prompt, args.role_file, args.stdin, "role_prompt").strip()
    except ValueError as exc:
        return _record_tool_error(store, state, "manage_worker", str(exc))

    if not role_prompt:
        return _record_tool_error(store, state, "manage_worker", "role_prompt 不能为空。")
    max_rp = int(limits.get("max_role_prompt_chars", 2500))
    if len(role_prompt) > max_rp:
        return _record_tool_error(store, state, "manage_worker", f"role_prompt 超出上限（{len(role_prompt)} > {max_rp} 字符）。")

    ok_paths, cleaned_paths, path_err = validate_allowed_paths(args.allowed_paths, limits)
    if not ok_paths:
        return _record_tool_error(store, state, "manage_worker", path_err)

    clean, detail = inspect_code_smuggling(role_prompt, "role_prompt", limits)
    if not clean:
        return _record_smuggling_block(store, state, "manage_worker", detail)

    overlaps: list[str] = []
    for other_id, other_w in workers.items():
        if other_id == wid:
            continue
        shared = sorted(set(cleaned_paths) & set(other_w.get("allowed_paths") or []))
        if shared:
            overlaps.append(f"{other_id}: {shared}")

    prev = workers.get(wid) or {}
    workers[wid] = {
        "worker_id": wid,
        "role_prompt": role_prompt,
        "allowed_paths": cleaned_paths,
        "dispatch_count": prev.get("dispatch_count", 0),
        "approved_count": prev.get("approved_count", 0),
        "rejected_count": prev.get("rejected_count", 0),
        "pending_submission_id": prev.get("pending_submission_id"),
    }
    store.save_state(state)
    store.append_trace(
        state,
        event_type="tool",
        action="manage_worker",
        payload={
            "worker_id": wid,
            "created": is_new,
            "allowed_paths": cleaned_paths,
            "role_prompt": role_prompt,
            "overlaps": overlaps,
        },
    )
    return _emit(
        {
            "ok": True,
            "action": "manage_worker",
            "worker_id": wid,
            "created": is_new,
            "allowed_paths": cleaned_paths,
            "active_workers_count": len(workers),
            "territory_overlap_warnings": overlaps,
        }
    )


def _execute_worker_dispatch_unit(
    store: MarshalStateStore,
    wid: str,
    ticket: str,
    config: dict,
    *,
    wave_id: str | None = None,
) -> tuple[int, dict]:
    """线程/多进程安全的单工位派单执行单元：短锁抢占工位 -> 无锁并发运行底层工人 -> 短锁原子收割 Diff。"""
    limits = config.get("marshal_limits") or {}
    max_disp = int(limits.get("max_dispatches", 15))
    max_per_worker = int(limits.get("max_dispatches_per_worker", 6))
    max_t_chars = int(limits.get("max_ticket_chars", 2000))

    with store.state_lock():
        state = store.load_state()
        workers = state.get("workers") or {}
        if wid not in workers:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"未知的工位 {wid!r}，请先调用 manage_worker 创建。"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        worker = workers[wid]
        if worker.get("pending_submission_id"):
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"工位 {wid} 仍有待审提交 {worker['pending_submission_id']} 尚未裁决！请先调用 review_submission（APPROVE 或 REJECT）后再派新单。"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        now_ms_check = int(time.time() * 1000)
        if worker.get("in_flight_job_id") and (now_ms_check - int(worker.get("in_flight_started_ms") or 0)) < 300_000:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"工位 {wid} 当前正在执行在途任务 {worker['in_flight_job_id']}，请勿对同一工位重复并发派单！"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        in_flight_cnt = sum(
            1
            for w in workers.values()
            if w.get("in_flight_job_id") and (now_ms_check - int(w.get("in_flight_started_ms") or 0)) < 300_000
        )
        used = int(state.get("dispatches_used", 0))
        if used + in_flight_cnt >= max_disp:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"全局派单预算已耗尽（已用 {used} + 在途 {in_flight_cnt} >= {max_disp} 单），严禁靠无限试错硬磨！"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        w_used = int(worker.get("dispatch_count", 0))
        if w_used >= max_per_worker:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"工位 {wid} 累计唤醒已达单工位上限（{w_used}/{max_per_worker} 次），禁止向单一工位过度倾倒工单！"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        if not ticket:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = "工单内容不能为空。"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        if len(ticket) > max_t_chars:
            state["tool_error_count"] = int(state.get("tool_error_count", 0)) + 1
            msg = f"单张工单长度超限（{len(ticket)} > {max_t_chars} 字符），请精简拆单。"
            store.append_trace(state, event_type="tool_error", action="dispatch_task", payload={"error": msg, "worker_id": wid})
            return 1, {"ok": False, "action": "dispatch_task", "worker_id": wid, "error": msg}

        clean, detail = inspect_code_smuggling(ticket, "ticket", limits)
        if not clean:
            state["code_smuggling_count"] = int(state.get("code_smuggling_count", 0)) + 1
            store.append_trace(
                state,
                event_type="violation",
                action="code_smuggling_intercepted",
                payload={"source_action": "dispatch_task", "worker_id": wid, **detail},
            )
            return (
                2,
                {
                    "ok": False,
                    "action": "dispatch_task",
                    "worker_id": wid,
                    "error": "【防代写护栏拦截】检测到输入中包含大段现成 Python 业务实现代码！统帅只能写职责边界、函数签名契约、逻辑步骤、防呆约束或具体问题行批注，严禁替工人代写源码（本次已计入违规调度）。",
                    "smuggling_detail": detail,
                },
            )

        if state.get("contract_defined_before_first_dispatch") is None:
            state["contract_defined_before_first_dispatch"] = bool(int(state.get("contract_version", 0)) > 0)

        started_ms = int(time.time() * 1000)
        job_id = f"disp_{used + in_flight_cnt + 1:03d}_{wid}"
        trunk_manifest_snap = dict(state.get("trunk_manifest") or {})
        base_hashes_snap = {rel: trunk_manifest_snap.get(rel) for rel in worker["allowed_paths"]}
        worker["in_flight_job_id"] = job_id
        worker["in_flight_started_ms"] = started_ms
        worker_snap = dict(worker)
        worker_mode = state["worker_mode"]
        staging_ws, sys_prompt_file = store.prepare_worker_staging(worker_snap, base_submission=None)
        w_sess_dir = store.pi_sessions_root / wid
        if int(worker.get("dispatch_count", 0)) == 0 and w_sess_dir.exists():
            shutil.rmtree(w_sess_dir, ignore_errors=True)
        contract_text = store.read_contract()
        store.save_state(state)

    try:
        drv_res = dispatch_to_worker(
            mode=worker_mode,
            repo_root=ROOT,
            staging_workspace=staging_ws,
            system_prompt_file=sys_prompt_file,
            session_dir=store.pi_sessions_root / wid,
            transcripts_dir=store.transcripts_root,
            results_dir=store.results_root,
            worker=worker_snap,
            contract_text=contract_text,
            ticket=ticket,
            job_id=job_id,
            config=config,
            is_rework=False,
        )
    except Exception as exc:
        with store.state_lock():
            state = store.load_state()
            w_cur = (state.get("workers") or {}).get(wid) or {}
            w_cur["in_flight_job_id"] = None
            w_cur["in_flight_started_ms"] = None
            store.restore_trunk_from_history(state)
            msg = f"底层工人执行异常: {exc}"
            store.append_trace(state, event_type="infra_error", action="dispatch_task", payload={"worker_id": wid, "error": msg})
            return (
                1,
                {
                    "ok": False,
                    "action": "dispatch_task",
                    "worker_id": wid,
                    "infra_error": True,
                    "dispatches_used": state.get("dispatches_used", 0),
                    "error": f"{msg}（基础设施异常未扣除派单预算，也未计入违规，请直接重试）",
                },
            )

    finished_ms = int(time.time() * 1000)
    with store.state_lock():
        state = store.load_state()
        store.restore_trunk_from_history(state)
        worker = (state.get("workers") or {})[wid]
        worker["in_flight_job_id"] = None
        worker["in_flight_started_ms"] = None
        if state.get("phase2_unlocked") and int(state.get("phase2_free_contract_credit", 0)) > 0:
            state["phase2_free_contract_credit"] = 0
        state["dispatches_used"] = int(state.get("dispatches_used", 0)) + 1

        harvested = store.harvest_worker_submission(
            state,
            worker,
            ticket=ticket,
            driver_result=drv_res,
            rework_of=None,
            started_ms=started_ms,
            finished_ms=finished_ms,
            base_hashes_override=base_hashes_snap,
        )
        store.append_trace(
            state,
            event_type="tool",
            action="dispatch_task",
            payload={
                "worker_id": wid,
                "wave_id": wave_id,
                "dispatch_index": state["dispatches_used"],
                "started_ms": started_ms,
                "finished_ms": finished_ms,
                "ticket": ticket,
                "submission_id": harvested["submission_id"],
                "changed_files": harvested["changed_files"],
                "syntax_ok": harvested["syntax_ok"],
                "syntax_errors": harvested["syntax_errors"],
                "unauthorized_files_stripped": harvested["unauthorized_files_stripped"],
                "worker_summary": harvested["worker_summary"],
                "worker_tokens": harvested["worker_tokens"],
                "diff_chars": harvested["diff_chars"],
            },
        )
        return (
            0,
            {
                "ok": True,
                "action": "dispatch_task",
                "wave_id": wave_id,
                "submission_id": harvested["submission_id"],
                "worker_id": wid,
                "dispatches_used": state["dispatches_used"],
                "dispatches_remaining": max_disp - state["dispatches_used"],
                "changed_files": harvested["changed_files"],
                "syntax_ok": harvested["syntax_ok"],
                "syntax_errors": harvested["syntax_errors"],
                "unauthorized_files_stripped": harvested["unauthorized_files_stripped"],
                "worker_summary": harvested["worker_summary"],
                "diff": harvested["diff"],
            },
        )


def handle_dispatch_task(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    wid = (args.worker_id or "").strip()
    try:
        ticket = _read_text_input(args.ticket, args.ticket_file, args.stdin, "工单指令").strip()
    except ValueError as exc:
        with store.state_lock():
            fresh = store.load_state()
            return _record_tool_error(store, fresh, "dispatch_task", str(exc))
    rc, resp = _execute_worker_dispatch_unit(store, wid, ticket, config, wave_id=None)
    return _emit(resp, exit_code=rc)


def handle_dispatch_parallel(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    """同波次向多个互不重叠的空闲工位并发派单（基于线程池同时驱动多个隔离沙箱工人）。"""
    raw_specs: list[str] = list(args.tasks or [])
    if not raw_specs:
        with store.state_lock():
            fresh = store.load_state()
            return _record_tool_error(store, fresh, "dispatch_parallel", "必须通过 --tasks 至少指定一项 <worker_id>:<ticket_file>。")

    parsed_pairs: list[tuple[str, str]] = []
    seen_wids: set[str] = set()
    for item in raw_specs:
        if ":" not in item:
            with store.state_lock():
                fresh = store.load_state()
                return _record_tool_error(
                    store,
                    fresh,
                    "dispatch_parallel",
                    f"参数格式错误 {item!r}，应为 <worker_id>:<ticket_file_path>。",
                )
        wid, tpath_str = item.split(":", 1)
        wid = wid.strip()
        fp = Path(tpath_str.strip())
        if not wid or not fp.is_file():
            with store.state_lock():
                fresh = store.load_state()
                return _record_tool_error(
                    store,
                    fresh,
                    "dispatch_parallel",
                    f"找不到工位 {wid!r} 对应的工单文件: {tpath_str!r}",
                )
        if wid in seen_wids:
            with store.state_lock():
                fresh = store.load_state()
                return _record_tool_error(
                    store,
                    fresh,
                    "dispatch_parallel",
                    f"同波次并行派单中工位 {wid!r} 重复出现，每个工位每波最多派发一张工单。",
                )
        seen_wids.add(wid)
        parsed_pairs.append((wid, fp.read_text(encoding="utf-8").strip()))

    wave_id = f"wave_{int(time.time() * 1000)}"
    results: list[dict] = []
    codes: list[int] = []

    def _run_one(pair: tuple[str, str]) -> tuple[int, dict]:
        return _execute_worker_dispatch_unit(store, pair[0], pair[1], config, wave_id=wave_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(parsed_pairs)) as pool:
        for rc, obj in pool.map(_run_one, parsed_pairs):
            codes.append(rc)
            results.append(obj)

    fresh_after = store.load_state()
    max_disp = int((config.get("marshal_limits") or {}).get("max_dispatches", 15))
    all_ok = all(c == 0 for c in codes)
    return _emit(
        {
            "ok": all_ok,
            "action": "dispatch_parallel",
            "wave_id": wave_id,
            "parallel_count": len(parsed_pairs),
            "dispatches_used": fresh_after.get("dispatches_used", 0),
            "dispatches_remaining": max_disp - int(fresh_after.get("dispatches_used", 0)),
            "results": results,
        },
        exit_code=(0 if all_ok else max(codes)),
    )


def _run_sandbox_command(base_ws: Path, cmd_args: list[str], timeout_sec: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd_args,
        cwd=str(base_ws),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    return proc.returncode, proc.stdout[-4000:], proc.stderr[-4000:]


def handle_inspect_and_test(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    target = args.target
    sub_id = (args.submission_id or "").strip() or None
    subs = state.get("submissions") or {}

    if target == "status":
        pending = [s["submission_id"] for s in subs.values() if s.get("status") == "PENDING"]
        payload = {
            "phase": state.get("phase", 1),
            "phase2_unlocked": bool(state.get("phase2_unlocked", False)),
            "phase2_free_contract_credit": int(state.get("phase2_free_contract_credit", 0)),
            "contract_version": state.get("contract_version", 0),
            "contract_chars": state.get("contract_chars", 0),
            "dispatches_used": state.get("dispatches_used", 0),
            "max_dispatches": state.get("max_dispatches", 15),
            "workers": {
                k: {
                    "allowed_paths": v["allowed_paths"],
                    "dispatch_count": v["dispatch_count"],
                    "approved_count": v["approved_count"],
                    "rejected_count": v.get("rejected_count", 0),
                    "pending_submission_id": v.get("pending_submission_id"),
                }
                for k, v in (state.get("workers") or {}).items()
            },
            "pending_submissions": pending,
            "trunk_files": sorted((state.get("trunk_manifest") or {}).keys()),
            "tamper_detected": state.get("tamper_detected", False),
        }
        store.append_trace(state, event_type="tool", action="inspect_and_test", payload={"target": "status"})
        return _emit({"ok": True, "action": "inspect_and_test", "target": "status", "data": payload})

    if target == "trunk":
        manifest = state.get("trunk_manifest") or {}
        if args.path:
            rel = args.path.strip().replace("\\", "/")
            fp = store.workspace_dir / Path(rel)
            if not fp.is_file() or rel not in manifest:
                return _record_tool_error(store, state, "inspect_and_test", f"主干中不存在文件: {rel}")
            content = fp.read_text(encoding="utf-8")
            store.append_trace(
                state,
                event_type="tool",
                action="inspect_and_test",
                payload={"target": "trunk", "path": rel, "chars": len(content)},
            )
            return _emit({"ok": True, "action": "inspect_and_test", "target": "trunk", "path": rel, "content": content})
        store.append_trace(state, event_type="tool", action="inspect_and_test", payload={"target": "trunk", "list_only": True})
        return _emit({"ok": True, "action": "inspect_and_test", "target": "trunk", "files": sorted(manifest.keys())})

    if target == "staging":
        if not sub_id:
            pending_list = [k for k, v in subs.items() if v.get("status") == "PENDING"]
            if len(pending_list) == 1:
                sub_id = pending_list[0]
            else:
                return _record_tool_error(store, state, "inspect_and_test", f"请通过 --submission-id 指定待查看的提交（当前待审: {pending_list}）。")
        if sub_id not in subs:
            return _record_tool_error(store, state, "inspect_and_test", f"不存在的 submission_id: {sub_id}")

        sub = subs[sub_id]
        sub["inspected_before_review"] = True
        store.save_state(state)

        sub_dir = store.submissions_root / sub_id
        diff_text = (sub_dir / "diff.patch").read_text(encoding="utf-8") if (sub_dir / "diff.patch").is_file() else ""
        file_content = None
        if args.path:
            rel = args.path.strip().replace("\\", "/")
            sf = sub_dir / "files" / Path(rel)
            if not sf.is_file():
                return _record_tool_error(store, state, "inspect_and_test", f"暂存提交 {sub_id} 中不含文件: {rel}")
            file_content = sf.read_text(encoding="utf-8")

        store.append_trace(
            state,
            event_type="tool",
            action="inspect_and_test",
            payload={"target": "staging", "submission_id": sub_id, "path": args.path},
        )
        return _emit(
            {
                "ok": True,
                "action": "inspect_and_test",
                "target": "staging",
                "submission_id": sub_id,
                "status": sub["status"],
                "changed_files": sub["changed_files"],
                "syntax_ok": sub["syntax_ok"],
                "syntax_errors": sub["syntax_errors"],
                "diff": diff_text,
                **({"path": args.path, "content": file_content} if file_content is not None else {}),
            }
        )

    if target in ("smoke", "unittest"):
        overlay_meta = None
        if sub_id:
            if sub_id not in subs:
                return _record_tool_error(store, state, "inspect_and_test", f"不存在的 submission_id: {sub_id}")
            sub = subs[sub_id]
            sub["inspected_before_review"] = True
            sub["tested_before_review"] = True
            store.save_state(state)
            overlay_meta = sub

        with tempfile.TemporaryDirectory(prefix=f"marshal_{target}_") as tmp:
            temp_ws = Path(tmp) / "workspace"
            shutil.copytree(store.workspace_dir, temp_ws)
            set_workspace_writable(temp_ws, True)
            if overlay_meta is not None:
                sfiles = store.submissions_root / sub_id / "files"
                for rel in overlay_meta.get("changed_files") or []:
                    src = sfiles / Path(rel)
                    dst = temp_ws / Path(rel)
                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)

            if target == "smoke":
                cmd = [sys.executable, "smoke_public.py"]
            else:
                tpath = (args.test_path or "tests").strip().replace("\\", "/")
                if tpath.endswith(".py"):
                    cmd = [sys.executable, "-m", "unittest", tpath, "-v"]
                else:
                    cmd = [sys.executable, "-m", "unittest", "discover", "-s", tpath, "-p", "test*.py", "-v"]

            try:
                rc, out, err = _run_sandbox_command(temp_ws, cmd, timeout_sec=35)
            except subprocess.TimeoutExpired:
                rc, out, err = 124, "", "测试命令执行超时（>35s，请检查是否存在死循环或阻塞监听）"

        passed = rc == 0
        # 检验 unittest 是否真正跑了至少 1 条用例（排除 0 tests）
        ran_real_tests = True
        if target == "unittest":
            combined_out = f"{out}\n{err}"
            if "Ran 0 tests" in combined_out or "NO TESTS RAN" in combined_out.upper():
                ran_real_tests = False

        store.append_trace(
            state,
            event_type="tool",
            action="inspect_and_test",
            payload={
                "target": target,
                "submission_id": sub_id,
                "passed": passed and ran_real_tests,
                "returncode": rc,
                "ran_real_tests": ran_real_tests,
                "stdout_tail": out[-1200:],
                "stderr_tail": err[-1200:],
            },
        )
        return _emit(
            {
                "ok": True,
                "action": "inspect_and_test",
                "target": target,
                "overlay_submission_id": sub_id,
                "passed": passed and ran_real_tests,
                "returncode": rc,
                "ran_real_tests": ran_real_tests,
                "stdout": out,
                "stderr": err,
            }
        )

    return _record_tool_error(store, state, "inspect_and_test", f"未知的 target: {target}")


def handle_review_submission(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    limits = config.get("marshal_limits") or {}
    sub_id = (args.submission_id or "").strip()
    decision = (args.decision or "").strip().upper()
    subs = state.get("submissions") or {}

    if sub_id not in subs:
        return _record_tool_error(store, state, "review_submission", f"不存在的 submission_id: {sub_id}")
    sub = subs[sub_id]
    if sub.get("status") != "PENDING":
        return _record_tool_error(store, state, "review_submission", f"提交 {sub_id} 状态已是 {sub.get('status')}，不可重复裁决。")

    try:
        comment = _read_text_input(
            args.comment if args.comment is not None else ("" if decision == "APPROVE" else None),
            args.comment_file,
            args.stdin,
            "审查批注",
        ).strip()
    except ValueError as exc:
        return _record_tool_error(store, state, "review_submission", str(exc))

    max_rc = int(limits.get("max_review_comment_chars", 2500))
    if len(comment) > max_rc:
        return _record_tool_error(store, state, "review_submission", f"审查批注长度超限（{len(comment)} > {max_rc} 字符）。")

    if decision == "APPROVE":
        inspected = bool(sub.get("inspected_before_review"))
        tested = bool(sub.get("tested_before_review"))
        ok_m, m_res = store.merge_submission_to_trunk(state, sub_id, comment)
        if not ok_m:
            return _record_tool_error(store, state, "review_submission", m_res.get("error", "合入失败"), m_res)
        store.append_trace(
            state,
            event_type="tool",
            action="review_submission",
            payload={
                "submission_id": sub_id,
                "worker_id": sub["worker_id"],
                "decision": "APPROVE",
                "comment": comment,
                "inspected_before_review": inspected,
                "tested_before_review": tested,
                "syntax_ok": sub.get("syntax_ok", True),
                "merged_files": m_res["merged_files"],
                "trunk_digest": m_res["trunk_digest"],
            },
        )
        return _emit(
            {
                "ok": True,
                "action": "review_submission",
                "decision": "APPROVE",
                **m_res,
            }
        )

    if decision == "REJECT":
        if not comment:
            return _record_tool_error(store, state, "review_submission", "打回重做（REJECT）必须附上具体的代码审查批注（--comment）！")
        clean, detail = inspect_code_smuggling(comment, "review_comment", limits)
        if not clean:
            return _record_smuggling_block(store, state, "review_submission", detail)

        wid = sub["worker_id"]
        worker = (state.get("workers") or {})[wid]
        max_disp = int(limits.get("max_dispatches", 14))
        max_reworks = int(limits.get("max_reworks_per_ticket", 2))
        max_per_worker = int(limits.get("max_dispatches_per_worker", 5))
        cur_depth = int(sub.get("rework_depth", 0))

        if not args.discard:
            if cur_depth >= max_reworks:
                return _record_tool_error(
                    store,
                    state,
                    "review_submission",
                    f"【单工单返工熔断】该工单链已累计返工 {cur_depth} 次（硬上限 {max_reworks} 次），禁止继续盲目打回抽卡！请评估后 APPROVE，或传 --discard 废弃本单重新规划。",
                )
            if int(state.get("dispatches_used", 0)) >= max_disp:
                return _record_tool_error(
                    store,
                    state,
                    "review_submission",
                    f"全局派单预算已耗尽（{state.get('dispatches_used')}/{max_disp}），无法继续触发返工。",
                )
            if int(worker.get("dispatch_count", 0)) >= max_per_worker:
                return _record_tool_error(
                    store,
                    state,
                    "review_submission",
                    f"工位 {wid} 累计唤醒已达单工位上限（{worker.get('dispatch_count')}/{max_per_worker} 次），无法继续返工。",
                )

        if args.discard:
            sub["status"] = "REJECTED"
            sub["reviewed_at"] = now_iso()
            sub["review_comment"] = comment
            worker["rejected_count"] = int(worker.get("rejected_count", 0)) + 1
            if worker.get("pending_submission_id") == sub_id:
                worker["pending_submission_id"] = None
            store.save_state(state)
            store.append_trace(
                state,
                event_type="tool",
                action="review_submission",
                payload={
                    "submission_id": sub_id,
                    "worker_id": wid,
                    "decision": "REJECT",
                    "rework_depth_next": cur_depth,
                    "comment": comment,
                    "inspected_before_review": bool(sub.get("inspected_before_review")),
                    "tested_before_review": bool(sub.get("tested_before_review")),
                    "discard_only": True,
                },
            )
            return _emit(
                {
                    "ok": True,
                    "action": "review_submission",
                    "decision": "REJECT",
                    "submission_id": sub_id,
                    "worker_id": wid,
                    "reworked": False,
                    "message": f"已丢弃提交 {sub_id}，工位 {wid} 已解锁，可重新 dispatch_task。",
                }
            )

        used = int(state.get("dispatches_used", 0))
        staging_ws, sys_prompt_file = store.prepare_worker_staging(worker, base_submission=sub)
        contract_text = store.read_contract()
        orig_ticket = sub.get("ticket") or sub.get("ticket_preview", "")
        job_id = f"rework_{used + 1:03d}_{wid}"

        started_ms = int(time.time() * 1000)
        trunk_manifest_snap = dict(state.get("trunk_manifest") or {})
        base_hashes_snap = {rel: trunk_manifest_snap.get(rel) for rel in worker["allowed_paths"]}
        try:
            drv_res = dispatch_to_worker(
                mode=state["worker_mode"],
                repo_root=ROOT,
                staging_workspace=staging_ws,
                system_prompt_file=sys_prompt_file,
                session_dir=store.pi_sessions_root / wid,
                transcripts_dir=store.transcripts_root,
                results_dir=store.results_root,
                worker=worker,
                contract_text=contract_text,
                ticket=orig_ticket,
                job_id=job_id,
                config=config,
                is_rework=True,
                review_comment=comment,
            )
        except Exception as exc:
            with store.state_lock():
                state = store.load_state()
                store.restore_trunk_from_history(state)
                return _record_infra_error(store, state, "review_submission", f"工人返工执行异常: {exc}")

        finished_ms = int(time.time() * 1000)
        with store.state_lock():
            state = store.load_state()
            store.restore_trunk_from_history(state)
            sub = (state.get("submissions") or {})[sub_id]
            worker = (state.get("workers") or {})[wid]
            sub["status"] = "REJECTED"
            sub["reviewed_at"] = now_iso()
            sub["review_comment"] = comment
            worker["rejected_count"] = int(worker.get("rejected_count", 0)) + 1
            if worker.get("pending_submission_id") == sub_id:
                worker["pending_submission_id"] = None
            state["dispatches_used"] = int(state.get("dispatches_used", 0)) + 1
            store.save_state(state)

            store.append_trace(
                state,
                event_type="tool",
                action="review_submission",
                payload={
                    "submission_id": sub_id,
                    "worker_id": wid,
                    "decision": "REJECT",
                    "rework_depth_next": cur_depth + 1,
                    "comment": comment,
                    "inspected_before_review": bool(sub.get("inspected_before_review")),
                    "tested_before_review": bool(sub.get("tested_before_review")),
                    "discard_only": False,
                },
            )

            harvested = store.harvest_worker_submission(
                state,
                worker,
                ticket=orig_ticket,
                driver_result=drv_res,
                rework_of=sub_id,
                started_ms=started_ms,
                finished_ms=finished_ms,
                base_hashes_override=base_hashes_snap,
            )
            store.append_trace(
                state,
                event_type="tool",
                action="rework_completed",
                payload={
                    "rework_of": sub_id,
                    "worker_id": wid,
                    "dispatch_index": state["dispatches_used"],
                    "started_ms": started_ms,
                    "finished_ms": finished_ms,
                    "new_submission_id": harvested["submission_id"],
                    "changed_files": harvested["changed_files"],
                    "syntax_ok": harvested["syntax_ok"],
                    "worker_summary": harvested["worker_summary"],
                    "worker_tokens": harvested["worker_tokens"],
                },
            )
        return _emit(
            {
                "ok": True,
                "action": "review_submission",
                "decision": "REJECT",
                "rejected_submission_id": sub_id,
                "reworked": True,
                "new_submission_id": harvested["submission_id"],
                "worker_id": wid,
                "dispatches_used": state["dispatches_used"],
                "dispatches_remaining": max_disp - state["dispatches_used"],
                "changed_files": harvested["changed_files"],
                "syntax_ok": harvested["syntax_ok"],
                "syntax_errors": harvested["syntax_errors"],
                "worker_summary": harvested["worker_summary"],
                "diff": harvested["diff"],
            }
        )

    return _record_tool_error(store, state, "review_submission", f"非法的 decision: {decision}（只能是 APPROVE 或 REJECT）")


def handle_unlock_phase2(args, store: MarshalStateStore, state: dict, config: dict) -> int:
    if state.get("phase2_unlocked"):
        p2_path = store.briefing_dir / "PHASE2_TASK.md"
        return _emit(
            {
                "ok": True,
                "action": "unlock_phase2",
                "already_unlocked": True,
                "phase": 2,
                "briefing_phase2_path": str(p2_path),
                "phase2_free_contract_credit": int(state.get("phase2_free_contract_credit", 0)),
                "message": "第二阶段已处于解锁状态，请直接阅读 briefing/PHASE2_TASK.md 继续推进。",
            }
        )

    subs = state.get("submissions") or {}
    pending = [k for k, v in subs.items() if v.get("status") == "PENDING"]
    if pending:
        return _record_tool_error(
            store,
            state,
            "unlock_phase2",
            f"当前仍有待审提交 {pending} 尚未裁决，请先完成第一阶段提交审查（APPROVE 或 REJECT）后再解锁第二阶段。",
        )

    approved_cnt = sum(1 for v in subs.values() if v.get("status") == "APPROVED")
    if approved_cnt < 1:
        return _record_tool_error(
            store,
            state,
            "unlock_phase2",
            "尚未合入任何第一阶段重构提交！严禁在完成第一阶段排雷与合入前提前窥探第二阶段需求。",
        )

    with tempfile.TemporaryDirectory(prefix="marshal_p2_gate_") as tmp:
        temp_ws = Path(tmp) / "workspace"
        shutil.copytree(store.workspace_dir, temp_ws)
        set_workspace_writable(temp_ws, True)
        try:
            rc, out, err = _run_sandbox_command(temp_ws, [sys.executable, "smoke_public.py"], timeout_sec=30)
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", "第一阶段连通冒烟检查超时"

    if rc != 0:
        return _record_tool_error(
            store,
            state,
            "unlock_phase2",
            f"第一阶段主干尚未通过基础冒烟连通检查（returncode={rc}），请先修复第一阶段主干后再解锁第二阶段。{(out + err)[-400:]}",
        )

    res = store.unlock_phase2_in_store(state, PHASE2_TASK_MD)
    store.append_trace(
        state,
        event_type="phase",
        action="unlock_phase2",
        payload=res,
    )
    return _emit(
        {
            "ok": True,
            "action": "unlock_phase2",
            **res,
            "message": (
                "第二阶段已成功解锁！请立即阅读案头目录 `briefing/PHASE2_TASK.md`，"
                "你在派发首张二阶段工单前拥有 1 次免费调用 `define_contract` 升级全局契约的豁免额度。"
            ),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="群智统帅团队管理与双阶段演进命令入口")
    ap.add_argument("--model", required=True, help="被测统帅模型标识")
    ap.add_argument("--runs-dir", default=None, help="运行目录根路径（默认 runs/）")
    sub = ap.add_subparsers(dest="action", required=True)

    p_con = sub.add_parser("define_contract", help="撰写或更新全局架构与跨模块接口契约")
    p_con.add_argument("--content", default=None, help="契约 Markdown 文本")
    p_con.add_argument("--file", default=None, help="从文件读取契约内容")
    p_con.add_argument("--stdin", action="store_true", help="从标准输入读取契约内容")

    p_wrk = sub.add_parser("manage_worker", help="创建或更新下属工位、岗位提示词与白名单领地")
    p_wrk.add_argument("--worker-id", required=True, help="工位代号，如 core_data")
    p_wrk.add_argument("--allowed-paths", nargs="+", required=True, help="允许读写的相对文件路径列表（上限 3 个 .py 文件）")
    p_wrk.add_argument("--role-prompt", default=None, help="岗位系统提示词")
    p_wrk.add_argument("--role-file", default=None, help="从文件读取岗位提示词")
    p_wrk.add_argument("--stdin", action="store_true", help="从标准输入读取岗位提示词")

    p_dsp = sub.add_parser("dispatch_task", help="向指定下属工位拆单派活")
    p_dsp.add_argument("--worker-id", required=True, help="目标工位代号")
    p_dsp.add_argument("--ticket", default=None, help="具体任务工单文本（严禁夹带现成业务源码）")
    p_dsp.add_argument("--ticket-file", default=None, help="从文件读取工单文本")
    p_dsp.add_argument("--stdin", action="store_true", help="从标准输入读取工单文本")

    p_par = sub.add_parser("dispatch_parallel", help="同波次向多个空闲下属工位并发派活")
    p_par.add_argument(
        "--tasks",
        nargs="+",
        required=True,
        help="多项 <worker_id>:<ticket_file> 列表，如 data_billing:draft/t1.md engine_gateway:draft/t2.md",
    )

    p_ins = sub.add_parser("inspect_and_test", help="查看状态/代码/Diff 或运行冒烟及单元测试")
    p_ins.add_argument("--target", choices=["status", "trunk", "staging", "smoke", "unittest"], required=True)
    p_ins.add_argument("--submission-id", default=None, help="暂存提交号（如 sub_001；用于查看 Diff 或叠加预跑测试）")
    p_ins.add_argument("--path", default=None, help="读取特定相对路径文件内容")
    p_ins.add_argument("--test-path", default="tests", help="unittest 测试目录或文件路径（默认 tests）")

    p_rev = sub.add_parser("review_submission", help="对下属暂存提交拍板合入（APPROVE）或打回返工（REJECT）")
    p_rev.add_argument("--submission-id", required=True, help="待裁决的暂存提交号，如 sub_001")
    p_rev.add_argument("--decision", choices=["APPROVE", "REJECT", "approve", "reject"], required=True)
    p_rev.add_argument("--comment", default=None, help="审查批注（REJECT 时必填，指出具体问题行与缺失边界）")
    p_rev.add_argument("--comment-file", default=None, help="从文件读取审查批注")
    p_rev.add_argument("--stdin", action="store_true", help="从标准输入读取审查批注")
    p_rev.add_argument("--discard", action="store_true", help="仅在 REJECT 时生效：只废弃该提交并解锁工位，不立即唤醒工人返工")

    sub.add_parser("unlock_phase2", help="在一阶段主干重构合入且冒烟通过后，解锁第二阶段紧急业务演进需求")

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    runs_base = Path(args.runs_dir).resolve() if args.runs_dir else (ROOT / "runs")
    run_dir = runs_base / args.model.strip()
    proto_path = run_dir / "protocol.json"
    if not proto_path.is_file():
        return _emit({"ok": False, "error": f"运行目录不存在或未初始化：{run_dir}（请先运行 tools/start_run.py）"}, exit_code=1)

    proto = json.loads(proto_path.read_text(encoding="utf-8"))
    if proto.get("status") == "packed" or (proto.get("timing") or {}).get("end_iso"):
        return _emit({"ok": False, "error": f"运行 {args.model} 已打包冻结 t1，严禁继续执行任何调度操作！"}, exit_code=1)

    config = json.loads((ROOT / "configs" / "benchmark.json").read_text(encoding="utf-8"))
    store = MarshalStateStore(run_dir=run_dir, repo_root=ROOT)
    state = store.load_state()

    tamper_ok, anomalies = store.check_and_record_tamper(state)
    if not tamper_ok:
        return _emit(
            {
                "ok": False,
                "error": "【严重违规：物理禁手破环】检测到 workspace/ 主干目录存在未经 review_submission(APPROVE) 的非授权直写改动，本局已被标记为作弊（tamper_detected=true）！",
                "anomalies": anomalies,
            },
            exit_code=3,
        )

    dispatch_map = {
        "define_contract": handle_define_contract,
        "manage_worker": handle_manage_worker,
        "dispatch_task": handle_dispatch_task,
        "dispatch_parallel": handle_dispatch_parallel,
        "inspect_and_test": handle_inspect_and_test,
        "review_submission": handle_review_submission,
        "unlock_phase2": handle_unlock_phase2,
    }
    return dispatch_map[args.action](args, store, state, config)


if __name__ == "__main__":
    raise SystemExit(main())
