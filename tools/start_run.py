#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选手（统帅）开局脚本（tools/start_run.py）。

职责：
1. 记录起点墙钟时间戳 t0；
2. 计算评测环境、题目契约与底层弱模型配置的 SHA-256 防篡改承诺；
3. 在 runs/<统帅模型名>/ 下初始化只读保护的主干沙盒 workspace/、统帅案头 briefing/ 与空白团队调度库 .marshal_state/。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import compute_manifest_digest, compute_workspace_manifest, set_workspace_writable  # noqa: E402
from src.marshal.state_store import MarshalStateStore  # noqa: E402

ENV_COMMIT_FILES = (
    "configs/benchmark.json",
    "src/marshal/guardrails.py",
    "src/marshal/state_store.py",
    "src/marshal/worker_driver.py",
    "src/scoring/workload.py",
    "src/scoring/metrics.py",
    "src/scoring/score_run.py",
    "src/scoring/objective_cache.py",
)

TASK_COMMIT_FILES = (
    "src/sandbox_template/TASK.md",
    "src/sandbox_template/smoke_public.py",
    "src/sandbox_template/cloudhub/app.py",
)


def _hash_rel_files(root: Path, rel_paths: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for rel in sorted(rel_paths):
        fp = root / Path(rel)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if fp.is_file():
            raw = fp.read_bytes()
            h.update(str(len(raw)).encode("ascii"))
            h.update(b"\0")
            h.update(raw)
        else:
            h.update(b"MISSING")
        h.update(b"\n")
    return h.hexdigest()


def compute_env_commit(root: Path = ROOT) -> str:
    return _hash_rel_files(Path(root).resolve(), ENV_COMMIT_FILES)


def compute_task_commit(root: Path = ROOT) -> str:
    return _hash_rel_files(Path(root).resolve(), TASK_COMMIT_FILES)


def compute_workspace_sha256(workspace_dir: Path) -> str:
    return compute_manifest_digest(compute_workspace_manifest(workspace_dir))


def compute_worker_config_commit(config: dict, worker_mode: str) -> str:
    wh = dict(config.get("worker_harness") or {})
    payload = {"active_mode": worker_mode, "worker_harness": wh}
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _build_marshal_desk_guide(model_name: str) -> str:
    return f"""# 统帅案头作战速查卡（`{model_name}`）

- **项目需求说明书**：同目录下的 `TASK.md`（18 条耦合规则与 HTTP 契约）
- **收尾统领复盘表**：同目录下的 `RETROSPECTIVE.md`（收工打包前必须如实填写）
- **主干业务沙盒**：`../workspace/`（**物理禁手保护**：统帅严禁直接写 `workspace/` 内任何文件，一切代码改动必须经下属工位提交并通过 `APPROVE` 合入！）

## 五大统帅命令速查（在仓库根目录执行）

1. **立全局规矩（`define_contract`）**——必须在首张派单前一次立稳（派单中途改契约将触发重罚！）：
   `python tools/marshal.py --model "{model_name}" define_contract --file draft/contract.md`
2. **搭班子与划领地（`manage_worker`）**——最多 5 个工位，单工位最多绑定 4 个 `.py` 文件：
   `python tools/marshal.py --model "{model_name}" manage_worker --worker-id db_worker --allowed-paths cloudhub/db.py --role-prompt "专注 SQLite 持久层、v1 在线迁表、幂等表与双本位流水"`
3. **拆单派活（`dispatch_task`）**——全局紧预算上限 14 单（单工位最多 5 次唤醒，单工单最多允许打回返工 2 次即熔断），严禁在工单内夹带现成 Python 实现代码块：
   `python tools/marshal.py --model "{model_name}" dispatch_task --worker-id db_worker --ticket-file draft/ticket.md`
4. **查代码与跑自检（`inspect_and_test`）**——支持对暂存提交做预合入叠加测试：
   `python tools/marshal.py --model "{model_name}" inspect_and_test --target staging --submission-id sub_001`
   `python tools/marshal.py --model "{model_name}" inspect_and_test --target smoke --submission-id sub_001`
   `python tools/marshal.py --model "{model_name}" inspect_and_test --target unittest --submission-id sub_005`
5. **拍板合入或打回重做（`review_submission`）**：
   `python tools/marshal.py --model "{model_name}" review_submission --submission-id sub_001 --decision APPROVE --comment "签名与事务锁符合全局契约"`
   `python tools/marshal.py --model "{model_name}" review_submission --submission-id sub_002 --decision REJECT --comment "指出具体错误行与缺失边界，触发原工位返工"`

## 收尾打包（严禁运行 `score_run.py` 或 `finalize.py`）
1. 填写 `runs/{model_name}/briefing/RETROSPECTIVE.md`（并按需填写 `runs/{model_name}/token_usage.json`），清空 `draft/`；
2. 执行 `python src/scoring/judge_pack.py --model "{model_name}"` 冻结 `t1` 并导出匿名盲评包。
"""


def start_run(
    *,
    model: str,
    runs_dir: Path | None = None,
    t0_iso: str | None = None,
    worker_mode: str | None = None,
    force: bool = False,
) -> dict:
    model_clean = (model or "").strip()
    if not model_clean or re.search(r'[<>:"/\\|?*]', model_clean):
        raise ValueError(f"非法的统帅模型标识: {model!r}")

    cfg_path = ROOT / "configs" / "benchmark.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    mode = (
        worker_mode
        or os.environ.get("SWARM_MARSHAL_WORKER_MODE")
        or (config.get("worker_harness") or {}).get("mode", "pi")
    ).strip()
    if mode not in ("pi", "mock", "openai"):
        raise ValueError(f"不支持的 worker_mode: {mode}")

    runs_base = Path(runs_dir).resolve() if runs_dir else (ROOT / "runs")
    run_dir = runs_base / model_clean
    if run_dir.exists():
        if not force:
            raise FileExistsError(f"运行目录已存在：{run_dir}（如需重置请更换批次后缀如 -s1，或测试时传入 --force）")
        set_workspace_writable(run_dir / "workspace", True)
        shutil.rmtree(run_dir, ignore_errors=True)

    ws_dir = run_dir / "workspace"
    brief_dir = run_dir / "briefing"
    ws_dir.mkdir(parents=True, exist_ok=True)
    brief_dir.mkdir(parents=True, exist_ok=True)

    tpl_dir = ROOT / "src" / "sandbox_template"
    shutil.copy2(tpl_dir / "TASK.md", ws_dir / "TASK.md")
    shutil.copy2(tpl_dir / "smoke_public.py", ws_dir / "smoke_public.py")
    shutil.copytree(tpl_dir / "cloudhub", ws_dir / "cloudhub", dirs_exist_ok=True)
    shutil.copytree(tpl_dir / "tests", ws_dir / "tests", dirs_exist_ok=True)

    shutil.copy2(tpl_dir / "TASK.md", brief_dir / "TASK.md")
    shutil.copy2(tpl_dir / "RETROSPECTIVE.md", brief_dir / "RETROSPECTIVE.md")
    (brief_dir / "MARSHAL_DESK.md").write_text(_build_marshal_desk_guide(model_clean), encoding="utf-8")

    proc_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    effective_t0 = t0_iso.strip() if t0_iso else proc_iso
    t0_source = "arg" if t0_iso else "process"

    wh = config.get("worker_harness") or {}
    locked_desc = (
        f"pi:{(wh.get('pi') or {}).get('provider')}/{(wh.get('pi') or {}).get('model')}(thinking={(wh.get('pi') or {}).get('thinking', 'off')})"
        if mode == "pi"
        else ("mock:weak-flash-lite-v1" if mode == "mock" else f"openai:{(wh.get('openai_fallback') or {}).get('model')}")
    )

    store = MarshalStateStore(run_dir=run_dir, repo_root=ROOT)
    state = store.initialize(model_name=model_clean, worker_mode=mode, config=config)

    protocol = {
        "benchmark_id": config.get("benchmark_id", "swarm-marshal-xiaonuo"),
        "version": config.get("version", "xiaonuo-v1.0"),
        "model": model_clean,
        "status": "running",
        "worker_mode": mode,
        "worker_model_locked": locked_desc,
        "env_commit": compute_env_commit(ROOT),
        "task_commit": compute_task_commit(ROOT),
        "worker_config_commit": compute_worker_config_commit(config, mode),
        "initial_workspace_sha256": state["trunk_digest"],
        "code_sha256": None,
        "timing": {
            "t0_iso": effective_t0,
            "process_iso": proc_iso,
            "t0_source": t0_source,
            "end_iso": None,
        },
    }
    (run_dir / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_dir": str(run_dir), "protocol": protocol}


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化统帅评测沙盒并打下起点时钟 t0")
    ap.add_argument("--model", required=True, help="被测统帅模型标识（如 gemini-4-carbon-s1）")
    ap.add_argument("--t0", default=None, help="可选起点 ISO 时间戳；默认取当前进程启动时间")
    ap.add_argument("--runs-dir", default=None, help="运行根目录（默认 runs/）")
    ap.add_argument("--worker-mode", choices=["pi", "mock", "openai"], default=None, help=argparse.SUPPRESS)
    ap.add_argument("--force", action="store_true", help="强制重建同名运行目录")
    args = ap.parse_args()

    try:
        res = start_run(
            model=args.model,
            runs_dir=Path(args.runs_dir) if args.runs_dir else None,
            t0_iso=args.t0,
            worker_mode=args.worker_mode,
            force=args.force,
        )
    except Exception as exc:
        print(f"[start_run 失败] {exc}", file=sys.stderr)
        return 1

    proto = res["protocol"]
    print(f"=== 群智统帅沙盒已就绪：{args.model} ===")
    print(f"- 运行目录：{res['run_dir']}")
    print(f"- 统帅案头：{Path(res['run_dir']) / 'briefing' / 'TASK.md'}（请先阅读项目需求）")
    print(f"- 速查手册：{Path(res['run_dir']) / 'briefing' / 'MARSHAL_DESK.md'}")
    print(f"- 起点时钟 t0：{proto['timing']['t0_iso']}")
    print(f"- 锁定工人底座：{proto['worker_model_locked']}")
    print("提示：主干 workspace/ 已启用物理禁手保护，请全程使用 `python tools/marshal.py` 五大动作统领团队。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
