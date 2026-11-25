# -*- coding: utf-8 -*-
"""统一弱模型工人驱动器（worker_driver.py）。

支持三种底层执行后端（在 configs/benchmark.json 的 worker_harness.mode 中一键切换，或由开局参数锁定）：
1. `pi`（默认官方实战模式）：调用复用自 SpecLock2 的 `speclock_harness/pi_worker_runner.mjs`，
   通过 `pi --mode rpc` 驱动真实轻量弱模型（锁定 provider/model 与 thinking=off），具备工位级持久会话、
   上下文超限自动压缩、伪工具文本自愈及跨平台进程树回收能力；
2. `mock`（确定性本地模拟弱工人模式）：对全局契约完备度、工位领地聚焦度、工单颗粒度与打回审查具体度敏感，
   用于零 API 成本端到端测试与三基线梯度校准；
3. `openai`（纯标准库 OpenAI 兼容直连兜底模式）：无 Node/pi 环境时直接对接任意 OpenAI 兼容接口。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .mock_worker import execute_mock_worker


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


def build_worker_prompt(
    *,
    worker: dict,
    contract_text: str,
    ticket: str,
    is_rework: bool = False,
    review_comment: str = "",
) -> str:
    allowed_str = ", ".join(f"`{p}`" for p in worker["allowed_paths"])
    contract_hint = (
        "当前工作目录根目录下的 `GLOBAL_CONTRACT.md` 已同步最新《全局架构契约》，请先阅读并严格对齐其表结构、函数签名与错误码。"
        if contract_text.strip()
        else "注意：统帅当前尚未建立《全局架构契约》（`GLOBAL_CONTRACT.md` 为空占位），请按工单要求在白名单文件内实现。"
    )
    if is_rework:
        return (
            f"【代码审查打回重做】\n"
            f"- 工位代号：`{worker['worker_id']}`\n"
            f"- 允许读写的白名单文件：{allowed_str}\n"
            f"- 契约状态：{contract_hint}\n\n"
            f"### 统帅审查打回批注（必须逐条落实修复）\n"
            f"{review_comment.strip()}\n\n"
            f"### 原工单参考背景\n"
            f"{ticket.strip()}\n\n"
            f"请直接在当前工作目录中修改白名单内的目标文件，运行 `python -m py_compile` 确认无语法错误后简要汇报修改点。"
        )
    return (
        f"【统帅派单指令】\n"
        f"- 工位代号：`{worker['worker_id']}`\n"
        f"- 允许读写的白名单文件：{allowed_str}\n"
        f"- 契约状态：{contract_hint}\n\n"
        f"### 本单任务要求与验收约束\n"
        f"{ticket.strip()}\n\n"
        f"请只在白名单允许的文件路径内完成实现，运行 `python -m py_compile` 自检语法无误后简要汇报产出文件与验证情况。"
    )


def _run_pi_harness(
    *,
    repo_root: Path,
    staging_workspace: Path,
    system_prompt_file: Path,
    session_dir: Path,
    transcripts_dir: Path,
    results_dir: Path,
    worker: dict,
    prompt_text: str,
    job_id: str,
    pi_cfg: dict,
) -> dict:
    runner_mjs = repo_root / "src" / "marshal" / "speclock_harness" / "pi_worker_runner.mjs"
    node_bin = os.environ.get("PI_NODE_BIN") or shutil.which("node")
    if not node_bin:
        raise RuntimeError("未找到 node 可执行文件，无法启动 pi harness。")

    timeout_sec = int(pi_cfg.get("timeout_sec", 240))
    wid = worker["worker_id"]
    stage_key = hashlib.sha256(str(staging_workspace.resolve()).encode("utf-8")).hexdigest()[:16]
    box_root = Path(tempfile.gettempdir()) / "swarm_marshal_pi_boxes" / stage_key
    box_ws = box_root / "workspace"
    box_sess = box_root / "session"
    box_transcripts = box_root / "transcripts"
    box_results = box_root / "results"
    box_prompt = box_root / "worker_system_prompt.md"

    _force_rmtree(box_root)
    box_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging_workspace, box_ws)
    for p in box_ws.rglob("*"):
        if p.is_file():
            try:
                if p.name == "GLOBAL_CONTRACT.md":
                    os.chmod(p, stat.S_IREAD)
                else:
                    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass

    box_sess.mkdir(parents=True, exist_ok=True)
    if session_dir.is_dir():
        for sf in session_dir.glob("*.jsonl"):
            if sf.is_file():
                shutil.copy2(sf, box_sess / sf.name)

    box_transcripts.mkdir(parents=True, exist_ok=True)
    box_results.mkdir(parents=True, exist_ok=True)
    shutil.copy2(system_prompt_file, box_prompt)

    spec = {
        "workerId": wid,
        "jobId": job_id,
        "cwd": str(box_ws),
        "sessionDir": str(box_sess),
        "transcriptsDir": str(box_transcripts),
        "resultsDir": str(box_results),
        "systemPromptFile": str(box_prompt),
        "provider": pi_cfg.get("provider", "deepseek"),
        "model": pi_cfg.get("model", "DeepSeek-V4.1-Flash-1017"),
        "thinking": pi_cfg.get("thinking", "off"),
        "maxContextTokens": int(pi_cfg.get("max_context_tokens", 32000)),
        "compactAtTokens": int(pi_cfg.get("compact_at_tokens", 24000)),
        "replyCharLimit": int(pi_cfg.get("reply_char_limit", 6000)),
        "timeoutSec": timeout_sec,
        "customEndpoint": {
            "enabled": bool((pi_cfg.get("custom_endpoint") or {}).get("enabled", False)),
            "baseUrl": (pi_cfg.get("custom_endpoint") or {}).get("base_url", ""),
            "apiKeyEnv": (pi_cfg.get("custom_endpoint") or {}).get("api_key_env", "SWARM_WORKER_API_KEY"),
            "api": (pi_cfg.get("custom_endpoint") or {}).get("api", "openai-completions"),
        },
        "prompt": prompt_text,
    }

    try:
        with tempfile.TemporaryDirectory(prefix="swarm_pi_io_", ignore_cleanup_errors=True) as io_tmp:
            in_path = Path(io_tmp) / "input.json"
            out_path = Path(io_tmp) / "output.json"
            in_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

            proc = subprocess.run(
                [node_bin, str(runner_mjs), "--input", str(in_path), "--output", str(out_path)],
                cwd=str(box_ws),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec + 30,
            )
            if out_path.is_file():
                res = json.loads(out_path.read_text(encoding="utf-8"))
                if not res.get("ok"):
                    raise RuntimeError(f"pi 工人执行失败: {res.get('error') or proc.stderr[-500:]}")
                _force_rmtree(staging_workspace)
                shutil.copytree(box_ws, staging_workspace)
                return res
            raise RuntimeError(f"pi_worker_runner 未生成结果文件 (code={proc.returncode}): {proc.stderr[-600:]}")
    finally:
        try:
            if box_sess.is_dir():
                session_dir.mkdir(parents=True, exist_ok=True)
                for sf in box_sess.glob("*.jsonl"):
                    if sf.is_file():
                        shutil.copy2(sf, session_dir / sf.name)
            t_src = box_transcripts / f"{wid}.jsonl"
            if t_src.is_file():
                transcripts_dir.mkdir(parents=True, exist_ok=True)
                with (transcripts_dir / f"{wid}.jsonl").open("a", encoding="utf-8", newline="\n") as tf:
                    tf.write(t_src.read_text(encoding="utf-8"))
            if box_results.is_dir():
                results_dir.mkdir(parents=True, exist_ok=True)
                for rf in box_results.glob("*.json"):
                    if rf.is_file():
                        shutil.copy2(rf, results_dir / rf.name)
        finally:
            _force_rmtree(box_root)


_FILE_BLOCK_RE = re.compile(r"```python\s+path=([^\s\n]+)\n(.*?)```", re.DOTALL)


def _run_openai_fallback(
    *,
    staging_workspace: Path,
    system_prompt_file: Path,
    worker: dict,
    contract_text: str,
    prompt_text: str,
    oa_cfg: dict,
) -> dict:
    base_url = os.environ.get(oa_cfg.get("base_url_env", "SWARM_WORKER_BASE_URL"), "").rstrip("/")
    api_key = os.environ.get(oa_cfg.get("api_key_env", "SWARM_WORKER_API_KEY"), "")
    if not base_url or not api_key:
        raise RuntimeError("openai 模式需要设置环境变量 SWARM_WORKER_BASE_URL 与 SWARM_WORKER_API_KEY。")

    existing_blocks = []
    for rel in worker["allowed_paths"]:
        fp = staging_workspace / Path(rel)
        if fp.is_file():
            existing_blocks.append(f"### 当前文件 `{rel}`\n```python\n{fp.read_text(encoding='utf-8')}\n```")

    sys_text = system_prompt_file.read_text(encoding="utf-8") + (
        "\n\n输出规范：对于你要创建或修改的每个白名单文件，必须严格使用代码块格式：\n"
        "```python path=cloudhub/xxx.py\n# 完整文件内容\n```\n随后用两三句话总结改动。"
    )
    user_full = (
        f"### GLOBAL_CONTRACT.md\n{contract_text or '(空)'}\n\n"
        + "\n\n".join(existing_blocks)
        + f"\n\n{prompt_text}"
    )
    body = {
        "model": oa_cfg.get("model", "DeepSeek-V4.1-Flash-1017"),
        "temperature": float(oa_cfg.get("temperature", 0.1)),
        "max_tokens": int(oa_cfg.get("max_tokens", 4096)),
        "messages": [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": user_full},
        ],
    }
    t0 = time.time()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=int(oa_cfg.get("timeout_sec", 180))) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    reply = data["choices"][0]["message"]["content"]
    allowed_set = set(worker["allowed_paths"])
    for m in _FILE_BLOCK_RE.finditer(reply):
        rel = m.group(1).strip().replace("\\", "/")
        if rel in allowed_set:
            dst = staging_workspace / Path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(m.group(2), encoding="utf-8")
    usage = data.get("usage") or {}
    summary = _FILE_BLOCK_RE.sub("[已写入文件代码块]", reply).strip()
    return {
        "ok": True,
        "harness": "openai",
        "worker_id": worker["worker_id"],
        "model_active": oa_cfg.get("model"),
        "thinking_level": "off",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "summary": summary[:2000],
        "tokens": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def dispatch_to_worker(
    *,
    mode: str,
    repo_root: Path,
    staging_workspace: Path,
    system_prompt_file: Path,
    session_dir: Path,
    transcripts_dir: Path,
    results_dir: Path,
    worker: dict,
    contract_text: str,
    ticket: str,
    job_id: str,
    config: dict,
    is_rework: bool = False,
    review_comment: str = "",
) -> dict:
    wh_cfg = config.get("worker_harness") or {}
    prompt_text = build_worker_prompt(
        worker=worker,
        contract_text=contract_text,
        ticket=ticket,
        is_rework=is_rework,
        review_comment=review_comment,
    )
    if mode == "mock":
        return execute_mock_worker(
            staging_workspace=staging_workspace,
            worker=worker,
            contract_text=contract_text,
            ticket=ticket,
            is_rework=is_rework,
            review_comment=review_comment,
            mock_cfg=wh_cfg.get("mock") or {},
        )
    if mode == "pi":
        return _run_pi_harness(
            repo_root=repo_root,
            staging_workspace=staging_workspace,
            system_prompt_file=system_prompt_file,
            session_dir=session_dir,
            transcripts_dir=transcripts_dir,
            results_dir=results_dir,
            worker=worker,
            prompt_text=prompt_text,
            job_id=job_id,
            pi_cfg=wh_cfg.get("pi") or {},
        )
    if mode == "openai":
        return _run_openai_fallback(
            staging_workspace=staging_workspace,
            system_prompt_file=system_prompt_file,
            worker=worker,
            contract_text=contract_text,
            prompt_text=prompt_text,
            oa_cfg=wh_cfg.get("openai_fallback") or {},
        )
    raise ValueError(f"未知的 worker_harness.mode: {mode}")
