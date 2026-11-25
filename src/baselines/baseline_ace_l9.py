# -*- coding: utf-8 -*-
"""基线三：9 级并行解耦王牌统帅参考基线（baseline_ace_l9.py · xiaonuo-v2.0）。

特征：
- 先侦察后决策：立契约前完整勘探 5 个存量模块摸清全部线上暗雷；
- 高信噪比契约 + 3 工位紧编制正交切分（data_billing、engine_gateway、qa_verifier）；
- 多工位同波次真并行编排：一阶段用 `dispatch_parallel` 双路并发重构数据计费与状态机网关，二阶段三路并发推进持久化演进、抢占/授信路由与黑盒回归套件；
- 100% 实证主义把关（零盲裁）：每次 REJECT 与 APPROVE 前必调阅暂存 Diff 或跑叠加测试，一击修平两处首轮隐蔽边界。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from .runner import MarshalBaselineClient

ACE_CONTRACT_V1 = """# CloudHub 全局架构与跨模块接口契约（Phase 1）

## 1. 存量排雷与三工位模块边界
- `cloudhub/db.py`：唯一 SQLite WAL + 线程锁 + `BEGIN IMMEDIATE` 事务层、v1 脏数据老库在线补列与期初差额基线（`initial_Carry_cents`）、按 `(tenant_id, method, pure_path, idem_key)` 作用域的幂等存储（仅 2xx 落盘）。
  - 导出：`TIER_CONFIG`、`get_conn`、`tx`、`init_db`、`append_ledger`、`check_idempotency`、`save_idempotency`。
- `cloudhub/billing.py`：纯整数精确计费引擎（禁止浮点与 `round`）。
  - 导出：`is_peak_epoch`、`compute_pre_voucher_cents`（0..600s 100%、601..3600s 80%、>3600s 60% 三段阶梯 + SPOT 高峰免上浮 + `(numer + 50_000_000) // 100_000_000` 标准半进位）、`compute_billable_seconds`（120% 封顶）、`apply_voucher_discount`（低于 `min_spend_cents` 归零）。
- `cloudhub/auth.py`：`verify_admin_headers` 与 `verify_tenant_signature`（纯路径剥离 `?` 参数、支持 `X-Now-Epoch` 严格整数校验、`hmac.compare_digest`）。
- `cloudhub/jobs.py`：领域异常 `DomainError`、严格整数校验（拒绝 `bool`/`float`）、状态机、10% 运行取消违约金半进位、超时重入队保冻保券、对账单与全局守恒审计。
- `cloudhub/app.py`：`create_server(db_path, host, port, admin_token)`，先完成身份鉴权与作业 404/403 归属核验，再查幂等键，统一返回 `{"ok": true/false, ...}`。
"""

ACE_CONTRACT_V2 = ACE_CONTRACT_V1 + """
## 2. Phase 2 紧急演进增量契约
- `tenants` 增量字段：`parent_tenant_id`、`credit_limit_cents`、`org_drawn_cents`。
- `cloudhub/jobs.py` 新增：
  - `bind_parent_tenant(conn, tenant_id, payload) -> dict`：校验 `ENTERPRISE` 母账户与 `STARTER/GROWTH` 子账户两级约束、活跃单禁切母账户、`credit_limit_cents >= org_drawn_cents`。
  - `preempt_spot_for_high(conn, payload) -> dict`：原子挑选排首 `HIGH` 排队单与最晚提交 `RUNNING SPOT` 牺牲单，置 `PREEMPTED`，全额退冻（`PREEMPT_RELEASE`）、退代金券、按 `reserved_cents`（若为 0 取 `pre_voucher_cents`）15% 半进位（保底 1 美分）发补贴（`PREEMPT_COMPENSATION`），并拉起 `HIGH` 单。
  - `submit_job` 与 `settle_job` 超支分支：子租户余额不足时在同事务内校验 `org_drawn_cents + shortfall <= credit_limit_cents` 与母余额，记 `ORG_TRANSFER_OUT` / `ORG_TRANSFER_IN` 自动划拨差额。
- `statement` 与 `audit` 同步输出 `total_subsidy_cents`、`net_platform_revenue_cents`、`org_transfer_balanced` 等二阶段扩展字段。
"""


def run_ace_l9_baseline(runs_dir: Path, model_name: str = "baseline-ace-l9-s1") -> Path:
    t0 = "2026-11-22T12:00:00+08:00"
    cli = MarshalBaselineClient(model=model_name, runs_dir=runs_dir, t0_iso=t0)
    cli.start()

    # 0. 先侦察后决策：立契约前完整勘探 5 个存量半成品模块
    for rel in ("cloudhub/db.py", "cloudhub/billing.py", "cloudhub/jobs.py", "cloudhub/auth.py", "cloudhub/app.py"):
        cli.act("inspect_and_test", ["--target", "trunk", "--path", rel])

    # 1. 契约先行
    cli.act("define_contract", ["--stdin"], stdin_text=ACE_CONTRACT_V1)

    # 2. 构建 3 个正交专职工位（零重叠）
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "data_billing",
            "--allowed-paths",
            "cloudhub/db.py",
            "cloudhub/billing.py",
            "--role-prompt",
            "负责持久层事务锁、v1 脏数据老库在线迁表与期初差额校准、幂等存储，以及纯整数三段阶梯算力计费模块。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "engine_gateway",
            "--allowed-paths",
            "cloudhub/jobs.py",
            "cloudhub/auth.py",
            "cloudhub/app.py",
            "--role-prompt",
            "负责作业状态机、代金券回收、抢单与超时扫描、双通道鉴权签名、先鉴权再幂等的 HTTP 路由网关及全局守恒对账。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "qa_verifier",
            "--allowed-paths",
            "tests/test_cloudhub.py",
            "--role-prompt",
            "专职独立黑盒集成测试工程师：覆盖半进位边界、三段阶梯、SPOT 高峰免上浮、退券复用、幂等冲突与守恒压测。",
        ],
    )

    with tempfile.TemporaryDirectory(prefix="ace_wave_tickets_") as tdir:
        tp = Path(tdir)
        t_p1_db = tp / "p1_db.md"
        t_p1_eg = tp / "p1_eg.md"
        t_p1_db.write_text(
            "按全局契约重构存量 `cloudhub/db.py` 与 `cloudhub/billing.py`：\n"
            "1. `db.py` 启用 WAL 与 BEGIN IMMEDIATE 写事务锁；在 init_db 中用 PRAGMA table_info 在线 ALTER TABLE 补齐 v1 老表缺失列并计算期初历史差额基线；实现按作用域隔离的 check_idempotency（异体返 409）与 save_idempotency；\n"
            "2. `billing.py` 清除浮点 round 缺陷，实现 0..600s 100%、601..3600s 80%、>3600s 60% 三段阶梯计费、等级与优先级系数、120% 超支封顶及 min_spend_cents 门槛判断。",
            encoding="utf-8",
        )
        t_p1_eg.write_text(
            "按全局契约重构 `cloudhub/jobs.py`、`cloudhub/auth.py` 与 `cloudhub/app.py`：\n"
            "1. `auth.py` 剥离 URL 查询参数、严格校验整数 `X-Now-Epoch` 并用 `hmac.compare_digest`；\n"
            "2. `app.py` 先完成身份鉴权及目标作业 404/403 归属校验后再查幂等键，且仅 2xx 成功响应写入幂等缓存；\n"
            "3. `jobs.py` 严格整数校验（拒绝 bool/float）、并发写事务内校验配额与余额、实耗跌破代金券 min_spend 归零触发超支 402 回滚、RUNNING 取消 10% 半进位违约金记 CANCEL_PENALTY、reap 重入队保冻不误退，以及 audit 全库资金守恒核验。",
            encoding="utf-8",
        )

        # 3. Wave 1 同波次并行派发 data_billing 与 engine_gateway！
        w1_res = cli.act(
            "dispatch_parallel",
            ["--tasks", f"data_billing:{t_p1_db}", f"engine_gateway:{t_p1_eg}"],
        )
        by_wid_1 = {item["worker_id"]: item for item in w1_res["results"]}
        sub_db_1 = by_wid_1["data_billing"]["submission_id"]
        sub_eg_1 = by_wid_1["engine_gateway"]["submission_id"]

        # 4. 逐一实证审阅暂存 Diff，一击拦截并修平两处首轮隐蔽缺陷
        cli.act("inspect_and_test", ["--target", "staging", "--submission-id", sub_db_1])
        s_db_fix = cli.act(
            "review_submission",
            [
                "--submission-id",
                sub_db_1,
                "--decision",
                "REJECT",
                "--comment",
                "审阅 `cloudhub/billing.py` 暂存 Diff 发现高峰期分支未豁免 SPOT 实例：当 `priority == 'SPOT'` 时在 UTC 9..21 高峰期必须保持 100% 不上浮（仅 NORMAL 与 HIGH 乘 120%）。请精准修正后重提。",
            ],
        )
        cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_db_fix["new_submission_id"]])
        cli.act(
            "review_submission",
            [
                "--submission-id",
                s_db_fix["new_submission_id"],
                "--decision",
                "APPROVE",
                "--comment",
                "已核对 BEGIN IMMEDIATE 事务锁、v1 期初基线迁表、三段阶梯与 SPOT 高峰豁免逻辑，批准合入。",
            ],
        )

        cli.act("inspect_and_test", ["--target", "staging", "--submission-id", sub_eg_1])
        s_eg_fix = cli.act(
            "review_submission",
            [
                "--submission-id",
                sub_eg_1,
                "--decision",
                "REJECT",
                "--comment",
                "审阅 `cloudhub/jobs.py` 发现两处代金券状态泄漏：`cancel_job` 的 RUNNING 分支与 `reap_expired_jobs` 的 QUEUED->EXPIRED 分支均漏调 `_release_voucher` 释放代金券占用。请补齐这两处退券逻辑。",
            ],
        )
        smoke_p1 = cli.act("inspect_and_test", ["--target", "smoke", "--submission-id", s_eg_fix["new_submission_id"]])
        assert smoke_p1["passed"] is True, f"Phase 1 smoke failed: {smoke_p1}"
        cli.act(
            "review_submission",
            [
                "--submission-id",
                s_eg_fix["new_submission_id"],
                "--decision",
                "APPROVE",
                "--comment",
                "退券逻辑、鉴权幂等时序与预合入冒烟自检通过，一阶段主干合入完成。",
            ],
        )

        # 一阶段主干连通确认后解锁 Phase 2
        cli.act("inspect_and_test", ["--target", "smoke"])
        p2_unlock = cli.act("unlock_phase2", [])
        assert p2_unlock["ok"] is True and p2_unlock["phase2_unlocked"] is True

        con_v2 = cli.act("define_contract", ["--stdin"], stdin_text=ACE_CONTRACT_V2)
        assert con_v2["used_phase2_evolution_credit"] is True and con_v2["midflight_revision"] is False

        # 5. Wave 2 三工位同波次全并发推进二阶段增量演进与独立集成测试！
        t_p2_db = tp / "p2_db.md"
        t_p2_eg = tp / "p2_eg.md"
        t_p2_qa = tp / "p2_qa.md"
        t_p2_db.write_text(
            "按二阶段契约升级 `cloudhub/db.py` 与 `cloudhub/billing.py`：\n"
            "在 `tenants` 建表与在线迁表逻辑中增量确保 `parent_tenant_id`、`credit_limit_cents`、`org_drawn_cents` 三列齐备，保持三段阶梯、SPOT 高峰免上浮、120% 封顶与代金券 min_spend 精确计费不变。",
            encoding="utf-8",
        )
        t_p2_eg.write_text(
            "按二阶段契约升级 `cloudhub/jobs.py`、`cloudhub/auth.py` 与 `cloudhub/app.py`（保留全部一阶段违约金、退券释放、reap 重入队与守恒审计）：\n"
            "1. 新增 `/api/v1/workers/preempt` 与 `preempt_spot_for_high`：同一写事务内选排首 HIGH 单并驱逐最晚提交 RUNNING SPOT 单置 PREEMPTED，退冻记 PREEMPT_RELEASE、释放代金券、按 reserved（为 0 取 pre_voucher）15% 半进位（保底 1 美分）发补贴记 PREEMPT_COMPENSATION，拉起 HIGH 单；\n"
            "2. 新增 `/api/v1/admin/tenants/{id}/bind-parent` 与母子授信差额自动划拨（`ORG_TRANSFER_OUT`/`ORG_TRANSFER_IN`）；\n"
            "3. 扩展 `statement` 与 `audit` 输出 `total_subsidy_cents`、`net_platform_revenue_cents` 与 `org_transfer_balanced`。",
            encoding="utf-8",
        )
        t_p2_qa.write_text(
            "在 `tests/test_cloudhub.py` 编写独立黑盒集成回归测试套件：\n"
            "覆盖三段阶梯计费、SPOT 高峰免上浮、RUNNING 取消 10% 违约金与退券复用、幂等冲突 409、非法布尔整数校验 400 以及全局审计 `all_balanced == True` 守恒不变量。",
            encoding="utf-8",
        )

        w2_res = cli.act(
            "dispatch_parallel",
            ["--tasks", f"data_billing:{t_p2_db}", f"engine_gateway:{t_p2_eg}", f"qa_verifier:{t_p2_qa}"],
        )
        by_wid_2 = {item["worker_id"]: item for item in w2_res["results"]}

        cli.act("inspect_and_test", ["--target", "staging", "--submission-id", by_wid_2["data_billing"]["submission_id"]])
        cli.act(
            "review_submission",
            [
                "--submission-id",
                by_wid_2["data_billing"]["submission_id"],
                "--decision",
                "APPROVE",
                "--comment",
                "母子组织授信列在线迁移完备且保持一阶段计费零回退，批准合入。",
            ],
        )

        cli.act("inspect_and_test", ["--target", "staging", "--submission-id", by_wid_2["engine_gateway"]["submission_id"]])
        cli.act(
            "review_submission",
            [
                "--submission-id",
                by_wid_2["engine_gateway"]["submission_id"],
                "--decision",
                "APPROVE",
                "--comment",
                "二阶段高优抢占驱逐补贴、母子共享授信池原子划拨及扩展对账审计核验无误，批准合入。",
            ],
        )

        ut_pre = cli.act("inspect_and_test", ["--target", "unittest", "--submission-id", by_wid_2["qa_verifier"]["submission_id"]])
        assert ut_pre["passed"] is True, f"QA pre-merge unittest failed: {ut_pre}"
        cli.act(
            "review_submission",
            [
                "--submission-id",
                by_wid_2["qa_verifier"]["submission_id"],
                "--decision",
                "APPROVE",
                "--comment",
                "端到端集成测试全部通过，批准合入。",
            ],
        )

    final_ut = cli.act("inspect_and_test", ["--target", "unittest"])
    assert final_ut["passed"] is True

    cli.write_retrospective(
        """# 统领复盘与架构说明

## 1. 存量半成品排雷清单
- 开局完整勘探 5 个存量模块，定位并清除浮点 `round` 偶舍入、缺少三段阶梯与 SPOT 高峰豁免、v1 无流水老库期初差额漂移、鉴权前全局查幂等跨租户泄露、4xx 污染幂等键、RUNNING 取消漏记违约金流水与漏退券、`reap` 重入队误退担保金及读写分离竞态。

## 2. 三工位紧编制切分与全局架构契约设计
- 划分为 `data_billing`（`db.py`, `billing.py`）、`engine_gateway`（`jobs.py`, `auth.py`, `app.py`）与 `qa_verifier`（`tests/test_cloudhub.py`），一阶段双路并行、二阶段三路全并行推进。

## 3. 第二阶段需求变更承接与架构耦合复盘
- 一阶段冒烟通过后调用 `unlock_phase2`，使用 1 次免费契约升级额度平滑补充抢占补贴与母子授信协议，零返工一次合入。

## 4. 代码审查拦截实录与真实测试覆盖边界
- 全程 100% 裁决前实证预检；一阶段拦截 `SPOT` 高峰误乘 1.2 与 `RUNNING` 取消/`EXPIRED` 漏退代金券两处缺陷并一击修平，终局 `unittest` 全绿。
"""
    )
    return cli.run_dir
