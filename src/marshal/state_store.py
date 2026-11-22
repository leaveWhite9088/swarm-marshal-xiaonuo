# -*- coding: utf-8 -*-
"""统帅调度状态存储、隔离暂存区编排、Diff 提取与防篡改链式轨迹日志（state_store.py）。"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import shutil
import stat
import os
import time
from pathlib import Path

from .guardrails import (
    compute_manifest_digest,
    compute_workspace_manifest,
    is_ignored_workspace_file,
    set_workspace_writable,
    verify_workspace_integrity,
)


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _force_rmtree(path: Path) -> None:
    if not path.exists():
        return

    def _onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_onerror)


def compute_trace_entry_hash(prev_hash: str, record_without_hash: dict) -> str:
    canon = json.dumps(record_without_hash, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{prev_hash}:{canon}".encode("utf-8")).hexdigest()


def verify_trace_chain(trace_path: Path) -> tuple[bool, str, list[dict]]:
    """校验 trace.jsonl 的哈希链完整性，防止选手手工删改违规记录或伪造轨迹。"""
    if not trace_path.is_file():
        return False, "缺少 trace.jsonl", []
    records: list[dict] = []
    prev_hash = "GENESIS"
    for lineno, line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception as exc:
            return False, f"trace.jsonl 第 {lineno} 行 JSON 损坏: {exc}", records
        saved_prev = rec.get("prev_hash")
        saved_hash = rec.get("entry_hash")
        if saved_prev != prev_hash:
            return False, f"trace.jsonl 第 {lineno} 行 prev_hash 断链", records
        body = {k: v for k, v in rec.items() if k != "entry_hash"}
        expected = compute_trace_entry_hash(prev_hash, body)
        if saved_hash != expected:
            return False, f"trace.jsonl 第 {lineno} 行 entry_hash 校验失败（疑似人工篡改）", records
        prev_hash = saved_hash
        records.append(rec)
    return True, "ok", records


class MarshalStateStore:
    def __init__(self, run_dir: Path, repo_root: Path):
        self.run_dir = Path(run_dir).resolve()
        self.repo_root = Path(repo_root).resolve()
        self.workspace_dir = self.run_dir / "workspace"
        self.briefing_dir = self.run_dir / "briefing"
        self.state_dir = self.run_dir / ".marshal_state"
        self.state_file = self.state_dir / "state.json"
        self.contract_file = self.state_dir / "GLOBAL_CONTRACT.md"
        self.staging_root = self.state_dir / "staging"
        self.submissions_root = self.state_dir / "submissions"
        self.pi_sessions_root = self.state_dir / "pi_sessions"
        self.transcripts_root = self.state_dir / "worker_transcripts"
        self.results_root = self.state_dir / "worker_results"
        self.trace_path = self.run_dir / "trace.jsonl"

    def initialize(self, model_name: str, worker_mode: str, config: dict) -> dict:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.submissions_root.mkdir(parents=True, exist_ok=True)
        self.pi_sessions_root.mkdir(parents=True, exist_ok=True)
        self.transcripts_root.mkdir(parents=True, exist_ok=True)
        self.results_root.mkdir(parents=True, exist_ok=True)

        trunk_manifest = compute_workspace_manifest(self.workspace_dir)
        state = {
            "schema_version": 1,
            "model": model_name,
            "worker_mode": worker_mode,
            "created_at": now_iso(),
            "contract_version": 0,
            "contract_chars": 0,
            "contract_defined_before_first_dispatch": None,
            "contract_revisions_after_dispatch": 0,
            "workers": {},
            "submissions": {},
            "next_submission_seq": 1,
            "dispatches_used": 0,
            "max_dispatches": int((config.get("marshal_limits") or {}).get("max_dispatches", 14)),
            "code_smuggling_count": 0,
            "tool_error_count": 0,
            "tamper_detected": False,
            "tamper_anomalies": [],
            "merge_conflicts_count": 0,
            "syntax_broken_merges": 0,
            "trunk_manifest": trunk_manifest,
            "trunk_digest": compute_manifest_digest(trunk_manifest),
            "last_trace_hash": "GENESIS",
            "trace_seq": 0,
        }
        self.save_state(state)
        set_workspace_writable(self.workspace_dir, False)
        self.append_trace(
            state,
            event_type="initial",
            action="start_run",
            payload={
                "worker_mode": worker_mode,
                "trunk_digest": state["trunk_digest"],
                "max_dispatches": state["max_dispatches"],
            },
        )
        return state

    def load_state(self) -> dict:
        if not self.state_file.is_file():
            raise RuntimeError(f"找不到调度状态文件：{self.state_file}（请先运行 tools/start_run.py）")
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def append_trace(self, state: dict, *, event_type: str, action: str, payload: dict) -> dict:
        seq = int(state.get("trace_seq", 0)) + 1
        prev_hash = str(state.get("last_trace_hash", "GENESIS"))
        rec_body = {
            "seq": seq,
            "t": int(time.time() * 1000),
            "ts": now_iso(),
            "type": event_type,
            "action": action,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        entry_hash = compute_trace_entry_hash(prev_hash, rec_body)
        rec = {**rec_body, "entry_hash": entry_hash}
        with self.trace_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        state["trace_seq"] = seq
        state["last_trace_hash"] = entry_hash
        self.save_state(state)
        return rec

    def check_and_record_tamper(self, state: dict) -> tuple[bool, list[str]]:
        """每次工具动作前后校验主干业务目录是否被统帅绕过调度器手工直写。"""
        ok, anomalies = verify_workspace_integrity(self.workspace_dir, state.get("trunk_manifest") or {})
        if not ok:
            state["tamper_detected"] = True
            existing = set(state.get("tamper_anomalies") or [])
            new_items = [a for a in anomalies if a not in existing]
            if new_items:
                state.setdefault("tamper_anomalies", []).extend(new_items)
                self.append_trace(
                    state,
                    event_type="violation",
                    action="direct_write_tamper_detected",
                    payload={"anomalies": new_items},
                )
            else:
                self.save_state(state)
        return ok, anomalies

    def read_contract(self) -> str:
        if self.contract_file.is_file():
            return self.contract_file.read_text(encoding="utf-8")
        return ""

    def write_contract(self, state: dict, content: str) -> dict:
        self.contract_file.write_text(content.strip() + "\n", encoding="utf-8")
        state["contract_version"] = int(state.get("contract_version", 0)) + 1
        state["contract_chars"] = len(content.strip())
        midflight = int(state.get("dispatches_used", 0)) > 0
        if midflight:
            state["contract_revisions_after_dispatch"] = int(state.get("contract_revisions_after_dispatch", 0)) + 1
        elif state.get("contract_defined_before_first_dispatch") is None:
            state["contract_defined_before_first_dispatch"] = True
        self.save_state(state)
        return {
            "contract_version": state["contract_version"],
            "contract_chars": state["contract_chars"],
            "midflight_revision": midflight,
            "contract_revisions_after_dispatch": int(state.get("contract_revisions_after_dispatch", 0)),
        }

    def prepare_worker_staging(
        self,
        worker: dict,
        *,
        base_submission: dict | None = None,
    ) -> tuple[Path, Path]:
        """构建单工位物理隔离工作台：只放入白名单内的文件 + 只读 GLOBAL_CONTRACT.md。"""
        wid = worker["worker_id"]
        w_root = self.staging_root / wid
        w_ws = w_root / "workspace"
        _force_rmtree(w_ws)
        w_ws.mkdir(parents=True, exist_ok=True)

        allowed: list[str] = worker["allowed_paths"]
        if base_submission is not None:
            # 打回返工时保留该工位上一版暂存文件作为修改起点
            sub_files_dir = self.submissions_root / base_submission["submission_id"] / "files"
            for rel in allowed:
                src_sub = sub_files_dir / Path(rel)
                src_trunk = self.workspace_dir / Path(rel)
                dst = w_ws / Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src_sub.is_file():
                    shutil.copy2(src_sub, dst)
                    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
                elif src_trunk.is_file():
                    shutil.copy2(src_trunk, dst)
                    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
        else:
            for rel in allowed:
                src_trunk = self.workspace_dir / Path(rel)
                dst = w_ws / Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src_trunk.is_file():
                    shutil.copy2(src_trunk, dst)
                    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)

        # 确保所属包目录带有可读的 __init__.py 以便 py_compile/相对导入自检
        if any(r.startswith("cloudhub/") for r in allowed) and not (w_ws / "cloudhub" / "__init__.py").exists():
            init_f = w_ws / "cloudhub" / "__init__.py"
            init_f.parent.mkdir(parents=True, exist_ok=True)
            init_f.write_text('"""CloudHub package."""\n', encoding="utf-8")

        contract_text = self.read_contract() or "（警告：统帅尚未通过 define_contract 定义全局架构契约！）\n"
        contract_dst = w_ws / "GLOBAL_CONTRACT.md"
        contract_dst.write_text(contract_text, encoding="utf-8")
        try:
            os.chmod(contract_dst, stat.S_IREAD)
        except OSError:
            pass

        exec_rules_path = self.repo_root / "src" / "marshal" / "speclock_harness" / "contexts" / "executor.md"
        exec_rules = exec_rules_path.read_text(encoding="utf-8") if exec_rules_path.is_file() else ""
        sys_prompt = (
            f"{exec_rules.strip()}\n\n"
            f"---\n"
            f"## 你的专属工位编制\n"
            f"- 工位代号：`{wid}`\n"
            f"- 允许读写的白名单文件（严禁越界）：`{', '.join(allowed)}`\n"
            f"- 统帅赋予你的岗位职责提示词：\n{worker['role_prompt'].strip()}\n"
        )
        sys_prompt_file = w_root / "worker_system_prompt.md"
        sys_prompt_file.write_text(sys_prompt, encoding="utf-8")
        return w_ws, sys_prompt_file

    def harvest_worker_submission(
        self,
        state: dict,
        worker: dict,
        *,
        ticket: str,
        driver_result: dict,
        rework_of: str | None = None,
    ) -> dict:
        """提取工位执行后的白名单文件快照、过滤越界文件、生成相对主干的 Unified Diff 并检查语法。"""
        wid = worker["worker_id"]
        allowed_set = set(worker["allowed_paths"])
        w_ws = self.staging_root / wid / "workspace"

        sub_seq = int(state.get("next_submission_seq", 1))
        state["next_submission_seq"] = sub_seq + 1
        sub_id = f"sub_{sub_seq:03d}"
        sub_dir = self.submissions_root / sub_id
        sub_files_dir = sub_dir / "files"
        sub_files_dir.mkdir(parents=True, exist_ok=True)

        unauthorized_files: list[str] = []
        changed_files: list[str] = []
        syntax_errors: list[str] = []
        diffs: list[str] = []

        for p in sorted(w_ws.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(w_ws).as_posix()
            if rel == "GLOBAL_CONTRACT.md" or is_ignored_workspace_file(rel):
                continue
            if rel == "cloudhub/__init__.py" and rel not in allowed_set:
                continue
            if rel not in allowed_set:
                unauthorized_files.append(rel)
                continue

        for rel in worker["allowed_paths"]:
            staged_f = w_ws / Path(rel)
            trunk_f = self.workspace_dir / Path(rel)
            old_text = trunk_f.read_text(encoding="utf-8") if trunk_f.is_file() else ""
            if not staged_f.is_file():
                continue
            new_text = staged_f.read_text(encoding="utf-8")
            # 保存当前工位暂存区的全部白名单文件快照
            snap_dst = sub_files_dir / Path(rel)
            snap_dst.parent.mkdir(parents=True, exist_ok=True)
            snap_dst.write_text(new_text, encoding="utf-8")

            if new_text != old_text or not trunk_f.is_file():
                changed_files.append(rel)
                udiff = "".join(
                    difflib.unified_diff(
                        old_text.splitlines(keepends=True),
                        new_text.splitlines(keepends=True),
                        fromfile=f"trunk/{rel}",
                        tofile=f"staging({wid})/{rel}",
                        n=3,
                    )
                )
                diffs.append(udiff)
                try:
                    compile(new_text, rel, "exec")
                except SyntaxError as exc:
                    syntax_errors.append(f"{rel}:{exc.lineno}: {exc.msg}")

        full_diff = "\n".join(diffs).strip()
        (sub_dir / "diff.patch").write_text(full_diff + ("\n" if full_diff else ""), encoding="utf-8")

        trunk_manifest = state.get("trunk_manifest") or {}
        base_hashes = {rel: trunk_manifest.get(rel) for rel in worker["allowed_paths"]}

        parent_sub = (state.get("submissions") or {}).get(rework_of) if rework_of else None
        rework_depth = (int(parent_sub.get("rework_depth", 0)) + 1) if parent_sub else 0

        sub_meta = {
            "submission_id": sub_id,
            "worker_id": wid,
            "created_at": now_iso(),
            "rework_of": rework_of,
            "rework_depth": rework_depth,
            "status": "PENDING",
            "ticket": ticket,
            "ticket_preview": ticket[:300],
            "changed_files": changed_files,
            "unauthorized_files_stripped": unauthorized_files,
            "syntax_ok": len(syntax_errors) == 0,
            "syntax_errors": syntax_errors,
            "base_hashes": base_hashes,
            "inspected_before_review": False,
            "tested_before_review": False,
            "worker_summary": driver_result.get("summary", ""),
            "worker_elapsed_ms": driver_result.get("elapsed_ms", 0),
            "worker_tokens": driver_result.get("tokens") or {},
            "diff_chars": len(full_diff),
        }
        state.setdefault("submissions", {})[sub_id] = sub_meta
        worker["pending_submission_id"] = sub_id
        worker["dispatch_count"] = int(worker.get("dispatch_count", 0)) + 1
        self.save_state(state)
        return {**sub_meta, "diff": full_diff}

    def merge_submission_to_trunk(self, state: dict, sub_id: str, comment: str) -> tuple[bool, dict]:
        """将批准的暂存提交正式合入沙盒主干，检测跨工位文件冲突与语法破坏并刷新只读哈希账本。"""
        subs = state.get("submissions") or {}
        if sub_id not in subs:
            return False, {"error": f"不存在的 submission_id: {sub_id}"}
        sub = subs[sub_id]
        if sub["status"] != "PENDING":
            return False, {"error": f"提交 {sub_id} 当前状态为 {sub['status']}，不可重复合入。"}

        wid = sub["worker_id"]
        worker = (state.get("workers") or {}).get(wid) or {}
        trunk_manifest = state.get("trunk_manifest") or {}
        base_hashes = sub.get("base_hashes") or {}

        # 检测自派单以来主干同路径文件是否已被其他工位合入覆盖（冲突检测）
        conflicts: list[str] = []
        for rel in sub.get("changed_files") or []:
            cur_h = trunk_manifest.get(rel)
            if cur_h != base_hashes.get(rel):
                conflicts.append(rel)

        if conflicts:
            state["merge_conflicts_count"] = int(state.get("merge_conflicts_count", 0)) + 1
            self.save_state(state)
            return (
                False,
                {
                    "error": f"合入冲突：{conflicts} 在派单期间已被其他工位更新，请打回（REJECT）让该工位基于最新主干重做！",
                    "conflicts": conflicts,
                },
            )

        if not sub.get("changed_files"):
            return False, {"error": f"提交 {sub_id} 没有任何有效文件改动，无法合入空提交，请打回（REJECT）重做。"}

        sub_files_dir = self.submissions_root / sub_id / "files"
        set_workspace_writable(self.workspace_dir, True)
        try:
            for rel in sub["changed_files"]:
                src = sub_files_dir / Path(rel)
                dst = self.workspace_dir / Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        finally:
            set_workspace_writable(self.workspace_dir, False)

        if not sub.get("syntax_ok", True):
            state["syntax_broken_merges"] = int(state.get("syntax_broken_merges", 0)) + 1

        new_manifest = compute_workspace_manifest(self.workspace_dir)
        state["trunk_manifest"] = new_manifest
        state["trunk_digest"] = compute_manifest_digest(new_manifest)
        sub["status"] = "APPROVED"
        sub["reviewed_at"] = now_iso()
        sub["review_comment"] = comment
        if worker.get("pending_submission_id") == sub_id:
            worker["pending_submission_id"] = None
        worker["approved_count"] = int(worker.get("approved_count", 0)) + 1
        self.save_state(state)

        return (
            True,
            {
                "submission_id": sub_id,
                "worker_id": wid,
                "merged_files": sub["changed_files"],
                "trunk_digest": state["trunk_digest"],
                "syntax_ok": sub.get("syntax_ok", True),
            },
        )
