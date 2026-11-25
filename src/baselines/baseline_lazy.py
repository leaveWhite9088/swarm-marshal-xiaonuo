# -*- coding: utf-8 -*-
"""基线一：甩手掌柜废片基线（baseline_lazy.py）。

特征：
- 不调用 define_contract 建立任何全局契约；
- 只建 1 个万能工位（all_worker），绑定单文件 cloudhub/app.py；
- 把原始 TASK.md 大段直接复制转发给弱模型工人；
- 不查看暂存 Diff、不跑自检，直接盲目 APPROVE 合入收工。
预期客观分：< 20 / 70。
"""
from __future__ import annotations

from pathlib import Path
from .runner import MarshalBaselineClient


def run_lazy_baseline(runs_dir: Path, model_name: str = "baseline-lazy-s1") -> Path:
    t0 = "2026-11-22T10:00:00+08:00"
    cli = MarshalBaselineClient(model=model_name, runs_dir=runs_dir, t0_iso=t0)
    cli.start()

    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "all_worker",
            "--allowed-paths",
            "cloudhub/app.py",
            "--role-prompt",
            "负责写完所有代码。",
        ],
    )

    raw_task = (cli.run_dir / "briefing" / "TASK.md").read_text(encoding="utf-8")[:1700]
    disp = cli.act(
        "dispatch_task",
        ["--worker-id", "all_worker", "--stdin"],
        stdin_text=f"请直接按照下面的完整需求把整个系统全写进 cloudhub/app.py 里：\n\n{raw_task}",
    )

    # 不跑 inspect_and_test，直接盲目合入
    cli.act(
        "review_submission",
        [
            "--submission-id",
            disp["submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "直接合入",
        ],
    )

    cli.write_retrospective("# 复盘\n已派给工人全部完成。")
    return cli.run_dir
