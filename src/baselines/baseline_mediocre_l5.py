# -*- coding: utf-8 -*-
"""基线二：官方 5 级传声筒平庸统帅基线（baseline_mediocre_l5.py）。

特征：
- 仅写一份极粗略的口号式全局契约（~210 字，缺迁移、分段阶梯、SPOT 免上浮、10% 违约金、幂等冲突等关键契约）；
- 设 3 个粗放工位（data_worker、api_worker、test_worker），单工位混装多个模块；
- 工单大包大揽概述原需求，未拆解精细边界；
- 审查流于形式（部分盲批、一次含糊打回“接口联调有点问题请检查重修”），终局跑通基础连通与 Wave 1 主链路，但深层边界大量失守。
预期客观分：落在中等区间（35 – 45 / 70）。
"""
from __future__ import annotations

from pathlib import Path
from .runner import MarshalBaselineClient

COARSE_CONTRACT = """# 全局架构契约（简版）
1. 数据库统一使用 SQLite，表名包括 tenants、ledger_entries、vouchers、jobs、idempotency_records。
2. 租户资金字段使用 balance_cents 和 frozen_cents，作业冻结字段使用 reserved_cents。
3. cloudhub/db.py 负责数据库与流水，cloudhub/pricing.py 负责算力价格，cloudhub/jobs.py 负责作业接口，cloudhub/app.py 暴露 create_server。
4. 统一返回 {"ok": true, "data": ...} 与 {"ok": false, "error": {"code": ..., "message": ...}}。
"""


def run_mediocre_l5_baseline(runs_dir: Path, model_name: str = "baseline-mediocre-l5-s1") -> Path:
    t0 = "2026-11-22T11:00:00+08:00"
    cli = MarshalBaselineClient(model=model_name, runs_dir=runs_dir, t0_iso=t0)
    cli.start()

    cli.act("define_contract", ["--stdin"], stdin_text=COARSE_CONTRACT)

    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "data_worker",
            "--allowed-paths",
            "cloudhub/db.py",
            "cloudhub/pricing.py",
            "--role-prompt",
            "你是后端开发，负责数据库存储和计费函数。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "api_worker",
            "--allowed-paths",
            "cloudhub/jobs.py",
            "cloudhub/app.py",
            "--role-prompt",
            "你是接口开发，负责作业逻辑和 HTTP 路由服务。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "test_worker",
            "--allowed-paths",
            "tests/test_basic.py",
            "--role-prompt",
            "负责编写基础测试。",
        ],
    )

    # 粗放派单 1（漏掉老库迁表、幂等冲突、两段阶梯、SPOT 高峰特例等精细说明）
    d1 = cli.act(
        "dispatch_task",
        ["--worker-id", "data_worker", "--stdin"],
        stdin_text=(
            "请实现 cloudhub/db.py 和 cloudhub/pricing.py，提供基础表创建、流水记录与基础单价计算函数，"
            "按租户等级 STARTER/GROWTH/ENTERPRISE 和机型 A10/L40/H100 算费用即可。"
        ),
    )
    # 敷衍打回一次，再盲批合入（未跑测试或审阅）
    rej1 = cli.act(
        "review_submission",
        ["--submission-id", d1["submission_id"], "--decision", "REJECT", "--comment", "再检查下字段名重交一次"],
    )
    cli.act("review_submission", ["--submission-id", rej1["new_submission_id"], "--decision", "APPROVE", "--comment", "ok"])

    # 派单中途才想起来补改一次全局契约（典型平庸规划事后打补丁）
    cli.act("define_contract", ["--stdin"], stdin_text=COARSE_CONTRACT + "\n5. 补充：异常码用大写字符串。\n")

    # 粗放派单 2
    d2 = cli.act(
        "dispatch_task",
        ["--worker-id", "api_worker", "--stdin"],
        stdin_text=(
            "请实现 cloudhub/jobs.py 和 cloudhub/app.py，支持建租户、充值、提单、抢单、结算、失败、取消、对账单和 HMAC 签名检查。"
        ),
    )
    # 敷衍打回一次（评语空洞，未指出任何深层规则缺失）
    rej2 = cli.act(
        "review_submission",
        [
            "--submission-id",
            d2["submission_id"],
            "--decision",
            "REJECT",
            "--comment",
            "报错了请检查接口细节重修一下",
        ],
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", rej2["new_submission_id"]])
    cli.act(
        "review_submission",
        ["--submission-id", rej2["new_submission_id"], "--decision", "APPROVE", "--comment", "合入主干"],
    )

    # 粗略测试单
    d3 = cli.act(
        "dispatch_task",
        ["--worker-id", "test_worker", "--stdin"],
        stdin_text="写一个测试检查 /health 可以正常访问。",
    )
    cli.act("review_submission", ["--submission-id", d3["submission_id"], "--decision", "APPROVE", "--comment", "ok"])
    cli.act("inspect_and_test", ["--target", "smoke"])

    cli.write_retrospective(
        """# 统领复盘与架构说明
1. 建立了 3 个工位分别负责数据计费、接口服务和基础测试。
2. 跑通了公开冒烟检查，主链路创建租户与提交结算可用，但未深入拆解历史老库迁移与复杂计费特例。
"""
    )
    return cli.run_dir
