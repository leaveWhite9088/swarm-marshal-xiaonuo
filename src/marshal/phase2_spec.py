# -*- coding: utf-8 -*-
"""第二阶段紧急业务演进需求书（由 `marshal.py unlock_phase2` 动态解锁下发）。"""
from __future__ import annotations

PHASE2_TASK_MD = """# 第二阶段紧急业务演进需求书（`PHASE2_TASK.md`）

> **解锁提示**：恭喜完成第一阶段主干重构！生产业务侧现提出两项高优先级跨模块架构演进需求。
> **契约演进特权**：你在解锁本需求后、派发第一张二阶段工单前，拥有 **1 次免费调用 `define_contract` 升级《全局架构契约》的豁免额度**（不计入“事后乱改契约”折损；但若二阶段开始派单后再次改约仍将照常重扣）。请在不破坏第一阶段全部已有接口与规则的前提下，增量落地以下特性：

---

## 演进特性一：高优算力抢占驱逐与竞价中断补贴（`POST /api/v1/workers/preempt`）

当集群满载而队列中有紧急的 `HIGH` 优先级作业等待时，调度中心需要原子驱逐正在运行的 `SPOT` 竞价作业并给予租户算力中断补贴：

1. **接口定义**：`POST /api/v1/workers/preempt`（Admin 鉴权 `X-Admin-Token`）。
   - 请求入参：`{"worker_node_id": str, "now_epoch": int, "lease_seconds"?: int(默认60, >=1), "evict_job_id"?: str}`（严格校验整数与非空字符串，非法返 `400 INVALID_ARGUMENT`）。
2. **前置条件与目标选择（必须在同一立即写事务内完成）**：
   - 先从当前 `status == 'QUEUED'`、`priority == 'HIGH'` 且 `now_epoch <= ttl_until_epoch` 的待执行队列中，按 `submitted_epoch ASC, job_id ASC` 选出排首的一条高优作业；若当前没有任何符合条件的排队中 `HIGH` 作业，拒绝并返回 `409 PREEMPTION_NOT_APPLICABLE`。
   - 选择被驱逐的牺牲作业：
     - 若入参显式指定了非空 `evict_job_id`：该作业不存在则返 `404 NOT_FOUND`；若该作业当前状态不是 `RUNNING` 或优先级不是 `SPOT`，拒绝并返回 `409 PREEMPTION_NOT_APPLICABLE`；
     - 若未指定 `evict_job_id`：自动从全库当前所有 `status == 'RUNNING'` 且 `priority == 'SPOT'` 的作业中，优先挑选**最晚提交**的一条（按 `submitted_epoch DESC, job_id DESC` 排序，以最小化已投入算力浪费）；若当前没有任何运行中的 `SPOT` 作业，拒绝并返回 `409 PREEMPTION_NOT_APPLICABLE`。
3. **原子驱逐、退券与中断补贴发放**：
   - 将被驱逐的 `SPOT` 作业状态置为新增终态 **`PREEMPTED`**（清空 `worker_node_id` 与 `lease_until_epoch`，`version` 加 1；后续对 `PREEMPTED` 作业调用 `settle`/`fail`/`cancel` 一律返 `409 INVALID_STATE_TRANSITION`）：
     1) **全额解冻退回担保金**：零收费，`used_gpu_sec` 不增加；若原 `reserved_cents > 0`，全额从 `frozen_cents` 退回该租户 `balance_cents`（记流水 `PREEMPT_RELEASE`）；
     2) **释放代金券**：若该作业绑定了代金券，立即解除绑定恢复为未消费可用状态；
     3) **发放 15% 平台中断补贴金**：平台向被驱逐租户的可用余额额外发放一笔不可撤回的补贴金——当原 `reserved_cents > 0` 时按 `reserved_cents` 的 **`15%`** 计算；若原作业因代金券全额抵扣使 `reserved_cents == 0`，则按原 `pre_voucher_cents` 的 **`15%`** 计算；统一执行标准半美分四舍五入，且**最低不少于 `1` 美分**！入账至该租户 `balance_cents` 并写入流水 `PREEMPT_COMPENSATION`（`delta_available` 为正的补贴额，`delta_frozen` 为 `0`，关联该被驱逐 `job_id`）。
   - 紧接着在同一事务内，将前面选出的排首 `HIGH` 作业置为 `RUNNING`，绑定 `worker_node_id`、`lease_until_epoch = now_epoch + lease_seconds`、`version` 加 1。
4. **成功响应（`200`）**：
   返回 `{"evicted_job_id": str, "evicted_tenant_id": str, "refunded_cents": int, "compensation_cents": int, "claimed_job": <新拉起的完整 RUNNING 作业对象>}`。

---

## 演进特性二：企业组织共享授信池与子租户差额自动代付

集团客户要求将旗下 `STARTER` 或 `GROWTH` 子团队账户挂载到一个 `ENTERPRISE` 母账户名下，在授信上限内由母账户资金池自动兜底补足差额：

1. **绑定与调整授信接口**：`POST /api/v1/admin/tenants/{tenant_id}/bind-parent`（Admin 鉴权 `X-Admin-Token`）。
   - 请求入参：`{"parent_tenant_id": str, "credit_limit_cents": int(>=0)}`（严格类型校验，非法或自绑定 `tenant_id == parent_tenant_id` 返 `400 INVALID_ARGUMENT`）。
   - 约束校验（写事务内）：
     - 子租户或母租户不存在返 `404 NOT_FOUND`；
     - **严禁多级嵌套或反向挂载**：母租户等级必须是 `ENTERPRISE`，且子租户等级只能是 `STARTER` 或 `GROWTH`（严禁将 `ENTERPRISE` 设为子租户，从而物理杜绝超过两级的嵌套链），违反返 `409 INVALID_ORG_BINDING`；
     - 若子租户当前存在未完结的活跃作业（`QUEUED` 或 `RUNNING`）且试图切换不同的母租户，或新设定的 `credit_limit_cents` 小于该子租户历史已累计支用额 `org_drawn_cents`，一律拒绝并返回 `409 INVALID_ORG_BINDING`。
   - 成功响应（`200`）：`{"tenant_id": str, "parent_tenant_id": str, "credit_limit_cents": int, "org_drawn_cents": int}`。
2. **提单与超支结算时的原子差额划拨规则**：
   - 子租户自身的并发上限 `max_concurrent`、月度算力配额 `quota_gpu_sec` 与等级计费折扣率仍严格按子租户自身等级独立考核。
   - 当已绑定母账户的子租户在 **提交作业（`POST /api/v1/jobs`）** 需要冻结 `reserved_cents`，或在 **超支结算（`POST /api/v1/jobs/{job_id}/settle`）** 需要从可用余额补扣差额 `extra_cents` 时，若子租户自身当前 `balance_cents` 不足（记缺口 `shortfall = 所需可用金额 - 子租户当前 balance_cents > 0`）：
     - 在**同一立即写事务**内检查两项条件：① `子租户已累计支用 org_drawn_cents + shortfall <= credit_limit_cents`；② `母租户当前可用 balance_cents >= shortfall`；
     - 若任一条件不满足：整单回滚并返回 `402 INSUFFICIENT_BALANCE`（提单时）或 `402 INSUFFICIENT_BALANCE_FOR_OVERRUN`（超支结算时），母子双方余额与流水均不得发生任何变动；
     - 若两项均满足：先在同一事务内从母租户 `balance_cents` 扣减 `shortfall`（记母租户流水 `ORG_TRANSFER_OUT`：`delta_available = -shortfall, delta_frozen = 0`，关联当前 `job_id`），累加子租户 `org_drawn_cents += shortfall`，同时向子租户 `balance_cents` 转入 `+shortfall`（记子租户流水 `ORG_TRANSFER_IN`：`delta_available = +shortfall, delta_frozen = 0`，关联当前 `job_id`），随后无缝执行子租户本单的 `RESERVE` 冻结或 `SETTLE` 补差！（注：后续该作业若发生退款或取消释放，退回款留在子租户自身余额中，不反向冲销 `org_drawn_cents`。）
   - **高并发防击穿要求**：当多个子租户多线程同时提交作业争抢同一母账户有限余额或授信池时，必须确保母账户余额绝不透支为负、子租户支用额绝不突破 `credit_limit_cents`。

---

## 演进特性三：对账单与全局资金守恒审计扩展（向下兼容）

1. **租户对账单 `GET /api/v1/tenants/{tenant_id}/statement` 新增返回字段**（保留一阶段全部原有字段）：
   - `parent_tenant_id`：`str | null`（未绑定时为 `null`）
   - `credit_limit_cents`：`int`（默认 `0`）
   - `org_drawn_cents`：`int`（累计从母账户划拨支用的美分数，默认 `0`）
   - `total_subsidy_cents`：`int`（该租户累计收到的 `PREEMPT_COMPENSATION` 补贴总额，默认 `0`）
2. **平台全局审计 `GET /api/v1/admin/audit` 新增返回字段与守恒校验**（保留一阶段全部原有字段）：
   - `total_subsidy_cents`：`int`（全平台累计发放的 `PREEMPT_COMPENSATION` 补贴总额）
   - `net_platform_revenue_cents`：`int`（平台净收入，即 `total_platform_revenue_cents - total_subsidy_cents`）
   - `org_transfer_balanced`：`bool`（校验全库所有 `ORG_TRANSFER_OUT` 与 `ORG_TRANSFER_IN` 的 `delta_available` 总和是否精确为 `0`；若不为 `0` 必须置 `all_balanced = false` 并追加异常说明至 `anomalies`）
   - **全平台资金守恒总等式**：任意时刻每个租户的可用余额均严格等于其历史期初基线加上该租户全部流水（含充值、预冻、结算、退回、取消释放、抢占释放、抢占补贴及组织划拨进出）的 `delta_available` 之和，且全库 `total_frozen_cents == active_escrow_cents`！
"""
