# -*- coding: utf-8 -*-
"""基线三：9 级王牌统帅参考基线（baseline_ace_l9.py）。

特征：
- 契约先行：首步调用 define_contract 锁死 SQLite 在线迁移列、双本位流水签名、两段阶梯与半进位公式、状态转移表、HMAC 鉴权及错误信封；
- 5 个正交专职下属工位（db_ledger、pricing_engine、job_lifecycle、http_gateway、qa_verifier），白名单文件零重叠（每工位 1～2 文件）；
- 精细任务降维：每张工单聚焦 3～4 条核心不变量，纯自然语言 + 接口签名，零源码代写；
- 毒辣审查把关：每次合入前必审暂存 Diff 或叠加跑测；精准揪出弱模型首轮在 SPOT 高峰免上浮、RUNNING 取消退还代金券上的两处隐蔽漏洞，通过具体批注指导一次修平；
- 全量回归收官：驱动独立测试工位跑通端到端边界断言与冒烟检查，如实撰写复盘报告。
预期客观分：逼近满分（65+ / 70）。
"""
from __future__ import annotations

from pathlib import Path
from .runner import MarshalBaselineClient

ACE_CONTRACT = """# CloudHub 全局架构与跨模块接口契约（v1）

## 1. 模块边界与导入拓扑
- `cloudhub/db.py`：唯一持久层与事务模块（无业务层反向依赖）。
  - 常量：`TIER_CONFIG`（映射 `STARTER`/`GROWTH`/`ENTERPRISE` 的 `max_concurrent`、`quota_gpu_sec`、`tier_rate_pct`）
  - 签名：
    `get_conn(db_path: str) -> sqlite3.Connection`
    `tx(db_path: str)`（基于线程锁 + `BEGIN IMMEDIATE` 的事务上下文管理器）
    `init_db(db_path: str) -> None`（幂等建表 + `PRAGMA table_info` 在线增量 `ALTER TABLE` 兼容 v1 老库）
    `append_ledger(conn, *, tenant_id: str, job_id: str | None, entry_type: str, delta_available: int, delta_frozen: int, now_epoch: int | None) -> tuple[int, int]`
    `check_idempotency(conn, scope_key: str | None, body_sha256: str) -> tuple[str, int | None, dict | None]`
    `save_idempotency(conn, scope_key: str | None, body_sha256: str, status_code: int, response_obj: dict, now_epoch: int | None) -> None`
- `cloudhub/pricing.py`：纯函数算力计费与代金券计算模块。
  - 签名：
    `is_peak_epoch(submitted_epoch: int) -> bool`
    `compute_pre_voucher_cents(*, gpu_type: str, seconds: int, tier: str, priority: str, submitted_epoch: int) -> int`
    `compute_billable_seconds(estimated_gpu_sec: int, actual_gpu_sec: int) -> int`
    `apply_voucher_discount(pre_voucher_cents: int, discount_cents: int, min_spend_cents: int) -> int`
- `cloudhub/auth.py`：Admin 令牌与租户 HMAC-SHA256 签名核验模块。
  - 签名：
    `compute_hmac_signature(api_secret: str, method: str, path: str, timestamp: str, body_bytes: bytes) -> str`
    `verify_admin_headers(headers, expected_admin_token: str) -> tuple[bool, int, str, str]`
    `verify_tenant_signature(headers, *, method: str, path: str, body_bytes: bytes, lookup_tenant_secret) -> tuple[bool, int, str, str, str | None]`
- `cloudhub/jobs.py`：作业状态机、代金券生命周期、优先级抢单、超时回收与对账审计服务。
  - 异常类：`DomainError(status_code: int, code: str, message: str)`
  - 签名：`create_tenant`, `create_voucher`, `topup_tenant`, `submit_job`, `get_job_for_tenant`, `claim_next_job`, `settle_job`, `fail_job`, `cancel_job`, `reap_expired_jobs`, `get_tenant_statement`, `get_global_audit`。
- `cloudhub/app.py`：对外唯一 HTTP 网关入口 `create_server(db_path, host, port, admin_token)`。

## 2. 数据库核心字段与资金守恒不变量
- 全部金额使用整数美分 `int`：`tenants.balance_cents`（可用）、`tenants.frozen_cents`（冻结担保）、`jobs.reserved_cents`（单作业预冻额）。
- 任意时刻全库 `SUM(tenants.frozen_cents) == SUM(jobs.reserved_cents WHERE status IN ('QUEUED', 'RUNNING'))`。
- 错误信封固定为 `{"ok": false, "error": {"code": "<UPPER_SNAKE>", "message": "..."}}`。
"""


def run_ace_l9_baseline(runs_dir: Path, model_name: str = "baseline-ace-l9-s1") -> Path:
    t0 = "2026-11-22T12:00:00+08:00"
    cli = MarshalBaselineClient(model=model_name, runs_dir=runs_dir, t0_iso=t0)
    cli.start()

    # 1. 契约先行
    cli.act("define_contract", ["--stdin"], stdin_text=ACE_CONTRACT)

    # 2. 搭建 5 个职责正交、领地零重叠的专职工位
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "db_ledger",
            "--allowed-paths",
            "cloudhub/db.py",
            "--role-prompt",
            "专职负责 SQLite WAL 连接、BEGIN IMMEDIATE 并发写事务锁、v1 老库在线增量迁表、双本位不可变账本流水与请求幂等存储。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "pricing_engine",
            "--allowed-paths",
            "cloudhub/pricing.py",
            "--role-prompt",
            "专职负责纯整数算力计费引擎：600 秒两段阶梯单价、租户等级折扣、优先级与高峰时段系数、半进位取整、超支 120% 封顶及代金券门槛抵扣。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "job_lifecycle",
            "--allowed-paths",
            "cloudhub/jobs.py",
            "--role-prompt",
            "专职负责异步作业有限状态机、配额并发数门禁、优先级抢单、运行取消违约金、租约超时重入队回收以及租户对账单与全局资金守恒审计。",
        ],
    )
    cli.act(
        "manage_worker",
        [
            "--worker-id",
            "http_gateway",
            "--allowed-paths",
            "cloudhub/auth.py",
            "cloudhub/app.py",
            "--role-prompt",
            "专职负责 Admin Token 校验、租户 HMAC-SHA256 签名与时间窗防重放、跨租户越权拦截以及 13 个 HTTP 路由分发。",
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
            "专职独立测试验收工程师：对照全局契约与业务规则编写端到端回归测试，重点覆盖高峰期 SPOT 特例、代金券释放复用与全局审计守恒。",
        ],
    )

    # 3. 推进底层持久层 db_ledger
    s_db = cli.act(
        "dispatch_task",
        ["--worker-id", "db_ledger", "--stdin"],
        stdin_text=(
            "严格按全局契约实现 `cloudhub/db.py`：\n"
            "1. 开启 WAL 与 busy_timeout=5000，事务上下文使用线程锁配合 BEGIN IMMEDIATE 防止并发抢单写锁冲突；\n"
            "2. 在 init_db 中用 PRAGMA table_info 检测 tenants 与 ledger_entries 老表列，对缺失的 frozen_cents、quota_gpu_sec、used_gpu_sec、delta_frozen、frozen_after 执行 ALTER TABLE 在线补列，严禁删表，并根据老租户 tier 回填标准配额；\n"
            "3. 实现 append_ledger 保证可用余额与冻结金非负且同步写流水；\n"
            "4. 实现 check_idempotency 与 save_idempotency：同键同 body_sha256 返回缓存响应，同键不同 body_sha256 返回 409 IDEMPOTENCY_CONFLICT。"
        ),
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_db["submission_id"]])
    cli.act(
        "review_submission",
        [
            "--submission-id",
            s_db["submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "已核对 PRAGMA table_info 在线迁表、BEGIN IMMEDIATE 锁与 409 幂等冲突逻辑，批准合入。",
        ],
    )

    # 4. 推进计费模块 pricing_engine，并在审查 Diff 时拦截 SPOT 高峰期误乘 1.2 的首轮漏洞
    s_pr = cli.act(
        "dispatch_task",
        ["--worker-id", "pricing_engine", "--stdin"],
        stdin_text=(
            "按全局契约实现 `cloudhub/pricing.py`：\n"
            "1. 两段阶梯计费：前 600 秒按 100% 机型单价（A10=2, L40=5, H100=12），超出 600 秒部分按 80% 计价；\n"
            "2. 叠加租户等级比例（100/90/80）、优先级比例（NORMAL=100, HIGH=125, SPOT=70）与 UTC 9..21 高峰系数，采用 (numer + 50_000_000) // 100_000_000 半进位取整；\n"
            "3. compute_billable_seconds 将结算计费秒数封顶在 estimated_gpu_sec 的 120%（整数整除）；\n"
            "4. apply_voucher_discount 校验达标 min_spend_cents 后抵扣并不超过折后总价。"
        ),
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_pr["submission_id"]])
    # 统帅审查 Diff 发现首轮代码将 SPOT 在高峰期也乘了 120%，精准打回修正！
    s_pr_fix = cli.act(
        "review_submission",
        [
            "--submission-id",
            s_pr["submission_id"],
            "--decision",
            "REJECT",
            "--comment",
            "审查发现 compute_pre_voucher_cents 高峰期分支漏掉了规则 R06 的关键特例：当 priority == 'SPOT' 时不受高峰期 1.2 倍上浮影响，peak_pct 必须保持 100（仅 NORMAL 与 HIGH 在高峰期取 120）。请精准修正该条件判断。",
        ],
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_pr_fix["new_submission_id"]])
    cli.act(
        "review_submission",
        [
            "--submission-id",
            s_pr_fix["new_submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "复核确认 SPOT 高峰期免上浮条件 `peak and priority != 'SPOT'` 已修正，批准合入。",
        ],
    )

    # 5. 推进状态机与对账服务 job_lifecycle，并在审查时拦截 RUNNING 取消及 EXPIRED 漏退代金券漏洞
    s_jb = cli.act(
        "dispatch_task",
        ["--worker-id", "job_lifecycle", "--stdin"],
        stdin_text=(
            "按全局契约实现 `cloudhub/jobs.py`：\n"
            "1. 实现活跃并发上限与配额余量校验（409 CONCURRENCY_LIMIT_EXCEEDED / QUOTA_EXCEEDED）及代金券核销校验；\n"
            "2. 严格实现状态转移：claim 按 HIGH>NORMAL>SPOT、submitted_epoch ASC、job_id ASC 出队；settle 处理多退与超支补扣（可用余额不足返 402 INSUFFICIENT_BALANCE_FOR_OVERRUN 并回滚）；\n"
            "3. cancel 区分 QUEUED（0 违约金全退）与 RUNNING（收 10% 违约金 `(reserved_cents * 10 + 50) // 100` 记 CANCEL_PENALTY，90% 退回记 CANCEL_RELEASE）；\n"
            "4. reap_expired_jobs 扫描 QUEUED 超 TTL 置 EXPIRED 退款，RUNNING 超租约且 retry_count < max_retries 则置回 QUEUED 且保留原冻结担保金不重扣，否则置 FAILED 全额退款；\n"
            "5. 实现 get_tenant_statement 与 get_global_audit 全库守恒核验（核对 total_frozen_cents == active_escrow_cents 及平台总收入）。"
        ),
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_jb["submission_id"]])
    s_jb_fix = cli.act(
        "review_submission",
        [
            "--submission-id",
            s_jb["submission_id"],
            "--decision",
            "REJECT",
            "--comment",
            "审查发现两处代金券状态泄漏：在 cancel_job 的 RUNNING 分支以及 reap_expired_jobs 的 QUEUED->EXPIRED 分支中漏掉了调用 `_release_voucher` 释放 `used_by_job_id`，这会导致运行期取消或排队过期作业绑定的代金券无法再次复用（违反规则 R07/R09/R11）。请在这两个分支补齐代金券释放。",
        ],
    )
    cli.act("inspect_and_test", ["--target", "staging", "--submission-id", s_jb_fix["new_submission_id"]])
    cli.act(
        "review_submission",
        [
            "--submission-id",
            s_jb_fix["new_submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "已确认 RUNNING 取消与 EXPIRED 过期分支均已正确释放未消费代金券，批准合入。",
        ],
    )

    # 6. 推进鉴权与 HTTP 网关层 http_gateway，合入前先叠加跑冒烟自检
    s_gw = cli.act(
        "dispatch_task",
        ["--worker-id", "http_gateway", "--stdin"],
        stdin_text=(
            "按全局契约实现 `cloudhub/auth.py` 与 `cloudhub/app.py`：\n"
            "1. Admin 与 `/api/v1/workers/claim` 校验 `X-Admin-Token`（缺头 401，错令 403 ADMIN_FORBIDDEN）；\n"
            "2. 租户接口校验 `X-Tenant-Id`、`X-Timestamp`（参考 `X-Now-Epoch` 或当前时间，偏差 >300s 返 401 SIGNATURE_EXPIRED）与小写 hex HMAC-SHA256 签名；跨租户路径越权返 403 TENANT_FORBIDDEN；\n"
            "3. 写接口接入 `Idempotency-Key` 幂等检查与落盘，异常统一转换为标准错误信封。"
        ),
    )
    smoke_pre = cli.act("inspect_and_test", ["--target", "smoke", "--submission-id", s_gw["submission_id"]])
    assert smoke_pre["passed"] is True, f"Gateway pre-merge smoke failed: {smoke_pre}"
    cli.act(
        "review_submission",
        [
            "--submission-id",
            s_gw["submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "网关路由、HMAC 签名校验与预合入冒烟测试通过，批准合入主干。",
        ],
    )

    # 7. 推进独立测试验收工位 qa_verifier，叠加运行 unittest 全绿后合入并执行终局验证
    s_qa = cli.act(
        "dispatch_task",
        ["--worker-id", "qa_verifier", "--stdin"],
        stdin_text=(
            "在 `tests/test_cloudhub.py` 编写独立黑盒集成测试：\n"
            "1. 验证高峰期（UTC 12:00）SPOT 作业不乘 1.2 倍高峰系数；\n"
            "2. 验证作业进入 RUNNING 后取消精确扣除 10% 违约金，且原绑定的代金券已释放可被新作业复用；\n"
            "3. 调用 `/api/v1/admin/audit` 验证 `all_balanced == True` 且 `anomalies == []`。"
        ),
    )
    ut_pre = cli.act("inspect_and_test", ["--target", "unittest", "--submission-id", s_qa["submission_id"]])
    assert ut_pre["passed"] is True, f"QA pre-merge unittest failed: {ut_pre}"
    cli.act(
        "review_submission",
        [
            "--submission-id",
            s_qa["submission_id"],
            "--decision",
            "APPROVE",
            "--comment",
            "独立集成测试覆盖 SPOT 高峰免上浮、运行取消违约金、退券复用与全局守恒断言，全部通过，批准合入。",
        ],
    )

    # 终局主干回归确认
    final_ut = cli.act("inspect_and_test", ["--target", "unittest"])
    assert final_ut["passed"] is True

    cli.write_retrospective(
        """# 统领复盘与架构说明

## 1. 团队建制与领地切分逻辑
- 共设立 5 个职责正交的下属工位：`db_ledger`（`cloudhub/db.py`）、`pricing_engine`（`cloudhub/pricing.py`）、`job_lifecycle`（`cloudhub/jobs.py`）、`http_gateway`（`cloudhub/auth.py`, `cloudhub/app.py`）与 `qa_verifier`（`tests/test_cloudhub.py`）。每个工位仅绑定 1～2 个文件，跨工位读写白名单零重叠。

## 2. 全局架构契约的关键防呆设计
- 在派发首张工单前先行发布 `GLOBAL_CONTRACT.md`，统一了各模块导入签名、`DomainError` 异常传递协议、整数美分双本位账本字段与 `SUM(frozen_cents) == active_escrow_cents` 全局不变量。

## 3. 代码审查与缺陷拦截实录
- 在 `pricing_engine` 首轮提交 `sub_002` 中，通过审阅暂存 Diff 发现其在 UTC 高峰期将 `SPOT` 实例也误乘了 `1.2` 倍系数，当即 `REJECT` 指明 `priority != 'SPOT'` 边界并在 `sub_003` 修平；
- 在 `job_lifecycle` 首轮提交 `sub_004` 中，审出 `RUNNING -> CANCELLED` 与 `QUEUED -> EXPIRED` 分支遗漏释放代金券占用，通过 `REJECT` 指导其在 `sub_005` 补齐 `_release_voucher`；
- 网关层与测试层均执行了预合入叠加测试（`smoke` 与 `unittest`）后再批准合入。

## 4. 终局自检覆盖面与已知遗留风险
- 已通过 `qa_verifier` 集成测试与公开冒烟脚本验证了计费特例、违约金、退券复用与全局审计守恒；高并发极端竞争主要依赖 SQLite WAL + `BEGIN IMMEDIATE` 串行化事务锁保障。
"""
    )
    return cli.run_dir
