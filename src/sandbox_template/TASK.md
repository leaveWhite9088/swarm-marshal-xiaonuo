# 项目总需求书（第一阶段）：云算力网关存量排雷与核心架构重构（`cloudhub`）

## 1. 工程背景、接手现状与双阶段演进赛制

你接手了一套由前任外包团队留下的企业级算力调度与多租户计费半成品系统 **`cloudhub`**（位于 `workspace/cloudhub/`，含 `db.py`、`billing.py`、`jobs.py`、`auth.py`、`app.py` 共五个存量模块，仅允许使用 **Python 3.12 标准库 + SQLite3**）。
**高度警惕**：这套存量代码虽然表面上能跑通最浅层的公开冒烟脚本（`smoke_public.py`），但在财务整数精度、阶梯计费规则、历史脏数据老库在线迁移、跨租户鉴权与幂等时序、代金券状态回收以及多线程并发事务隔离上潜伏着多处严重线上缺陷！

### 1.1 双阶段实战赛制（务必合理规划模块解耦！）
本项目考核真实研发中的**“一阶段存量排雷重构 + 二阶段紧急需求演进”**全周期统领能力：
1. **第一阶段（当前阶段）**：先通过 `inspect_and_test --target trunk` 勘探存量代码中的跨模块隐患，建立精炼高信噪比的《全局架构契约》（硬上限 **5500 字符**），在**最多 3 个下属工位**（单工位最多允许绑定 **3 个 `.py` 文件**，单张工单上限 **2000 字符**）的紧编制约束下，完成一阶段全部业务规则排雷与重构，并通过充分自测。
2. **解锁第二阶段（`unlock_phase2`）**：当你确认一阶段主干已稳定合入并通过连通检验后，必须主动执行：
   `python tools/marshal.py --model <你的模型名> unlock_phase2`
   系统将解锁并在 `briefing/PHASE2_TASK.md` 下发**《第二阶段紧急业务演进需求书》**（涉及跨租户资金池与高优算力抢占调度），同时赋予你**唯一一次免费升级《全局架构契约》的额度**（仅限解锁二阶段后、派发首张二阶段工单前的一次 `define_contract` 免扣规划折损分）。若一阶段模块边界切分清晰，二阶段只需小幅增量演进即可平滑落地！

### 1.2 外部黑盒入口与基本铁律
- **唯一外部工厂入口**：`cloudhub/app.py` 必须保留工厂函数 `create_server(db_path: str, host: str = "127.0.0.1", port: int = 0, admin_token: str = "marshal-admin-secret") -> http.server.ThreadingHTTPServer` 及命令行启动能力。
- **金额与时钟铁律**：一切金额单位均为**整数美分（`int`）**，一切时间戳均为**整数秒级 Unix Epoch（`int`）**。严禁使用浮点数参与金额乘除或使用编程语言默认的银行家偶舍入！凡涉及折扣、时段上浮或比例违约金产生小数美分的，必须在最终合成金额上采用精确有理数运算并执行**标准四舍五入（小数部分满半美分即向上进一美分，不足半美分舍去）**。任何布尔值、浮点数、字符串或越界值冒充整数参数时，一律拒绝并返回 `400 INVALID_ARGUMENT`。

---

## 2. 响应信封、安全鉴权与幂等时序规范

### 2.1 统一 JSON 响应信封
- 成功响应（`200` / `201`）：`{"ok": true, "data": <object>}`
- 失败响应（`400` / `401` / `402` / `403` / `404` / `409`）：`{"ok": false, "error": {"code": "<UPPER_SNAKE_CODE>", "message": "<说明>"}}`

### 2.2 双通道鉴权与防重放
1. **公开健康检查**：`GET /health` 免鉴权，返回 `200` 及 `{"status": "healthy"}`。
2. **管理员与节点通道**（`/api/v1/admin/*` 与 `/api/v1/workers/*`）：必须携带 `X-Admin-Token`；缺失或空串返回 `401 UNAUTHORIZED`，常量时间比对不匹配返回 `403 ADMIN_FORBIDDEN`。
3. **租户 HMAC 签名通道**（`/api/v1/tenants/*` 与 `/api/v1/jobs/*`）：
   - 必须携带 `X-Tenant-Id`、`X-Timestamp`（合法整数秒字符串）、`X-Signature`；任一缺失或格式非法返回 `401 UNAUTHORIZED`；租户不存在返回 `401 UNKNOWN_TENANT`。
   - **时间窗防重放**：以请求头 `X-Now-Epoch`（若测试端提供则须为合法整数秒，非法返 `401 UNAUTHORIZED`；未提供则取系统当前整数秒）为基准时钟，与 `X-Timestamp` 绝对偏差超过 `300` 秒返回 `401 SIGNATURE_EXPIRED`。（注：请求体内的 `now_epoch` 仅作为业务事件时钟，不用于签名过期判定。）
   - **签名原文与比对**：取大写 HTTP 方法、**剥离 `?` 查询参数后的纯 URL 路径**、`X-Timestamp` 原串、以及原始请求体字节串（空体为 `b""`）的小写十六进制 SHA-256 摘要，四项以换行符 `\n` 拼接后使用租户 `api_secret` 计算小写十六进制 HMAC-SHA256，并通过常量时间函数与 `X-Signature` 比对，不匹配返回 `401 INVALID_SIGNATURE`。
   - **多租户越权隔离**：访问 `/api/v1/tenants/{tenant_id}/*` 时路径租户与 `X-Tenant-Id` 不一致，或访问/操作 `/api/v1/jobs/{job_id}*` 时目标作业不存在（先返 `404 NOT_FOUND`）或不属于 `X-Tenant-Id`，一律返回 `403 TENANT_FORBIDDEN`。

### 2.3 请求级幂等键（`Idempotency-Key`）与安全先后顺序
对所有租户写接口（充值 `topup`、提交作业 `POST /api/v1/jobs`、结算 `settle`、失败 `fail`、取消 `cancel`），若请求头携带非空 `Idempotency-Key`：
1. **严禁鉴权前短路泄露**：必须**先通过完整的身份签名校验、目标资源存在性检查（`404`）以及租户归属权校验（`403`）**后，才允许按 `(tenant_id, method, pure_path, idempotency_key)` 作用域查询幂等记录，杜绝跨租户利用相同幂等键嗅探他人响应或触发伪冲突！
2. **仅成功结果落盘锁定**：只有业务成功完成（`2xx`）的请求才与原始请求体 SHA-256 一并写入幂等表，后续同作用域同请求体重放时原样返回首次状态码与响应体，同作用域不同请求体返回 `409 IDEMPOTENCY_CONFLICT`；若请求因余额不足（`402`）、并发或配额受限（`409`）、参数错误（`400`）等业务失败被回滚拒绝，**严禁写入幂等表锁死该键**，以便租户充值或释放配额后使用原幂等键重试成功。

---

## 3. 第一阶段核心业务规则与财务不变量

### 3.1 历史 v1 老库非破坏性迁移与期初差额对账
- 服务启动打开现有 SQLite 数据库时须启用 WAL 日志与合理锁等待，且当面对旧版 **v1 历史库**——其中 `tenants` 仅有 `(tenant_id, name, tier, balance_cents, api_secret, created_at)`、`ledger_entries` 仅有 `(entry_id, tenant_id, job_id, entry_type, delta_available, balance_after, created_at)`——必须在线无损补齐缺失表与字段，保留全部老租户余额与历史流水，老租户 `frozen_cents` 与 `used_gpu_sec` 置 `0`，`quota_gpu_sec` 按其所属等级补齐标准月度配额。
- **历史脏账期初基线校准**：部分 v1 老租户系早期从快照导入，其迁入时的 `balance_cents` 与历史 `ledger_entries` 中已有的 `delta_available` 之和可能存在期初历史差额（例如无初始充值流水或仅有部分流水）。迁表时必须妥善记录或校准该历史期初基线，确保刚迁移完时全局审计 `all_balanced` 为 `true`，且迁表后新增的一切资金变动仍严格受双本位账本守恒约束！

### 3.2 租户等级、三段累进阶梯计费与时段特例
1. **三档租户等级**：
   - `STARTER`：活跃并发上限 `2` 单，月度配额 `3600` GPU 秒，享受原价（`100%`）；
   - `GROWTH`：活跃并发上限 `5` 单，月度配额 `18000` GPU 秒，享受九折（`90%`）；
   - `ENTERPRISE`：活跃并发上限 `10` 单，月度配额 `108000` GPU 秒，享受八折（`80%`）。
2. **机型基础单价与三段累进阶梯时长计价**：
   - 机型基础单价：`A10` 为 `2` 美分/秒，`L40` 为 `5` 美分/秒，`H100` 为 `12` 美分/秒。
   - 对任意算力时长（秒）：**前 `600` 秒**按基础单价的 **`100%`** 计价；**第 `601` 至 `3600` 秒的部分**（最多 3000 秒）按基础单价的 **`80%`** 计价；**超出 `3600` 秒以上的部分**按基础单价的 **`60%`** 计价。
3. **优先级系数与 UTC 高峰期特例**：
   - 优先级系数：`NORMAL` 为 `100%`，`HIGH` 为 `125%`（加收 25%），`SPOT` 为 `70%`（七折）。
   - 高峰期判定：按作业 `submitted_epoch` 对应的 UTC 小时计算，落在 **`09:00` 至 `20:59`（即 UTC 小时 `9 <= hour < 21`）** 内为高峰期。
   - 高峰期上浮与豁免：高峰期内 `NORMAL` 与 `HIGH` 额外乘以 **`120%`**（上浮 20%），而 **`SPOT` 竞价作业在高峰期免收上浮费（固定按 `100%` 计）**；非高峰期所有优先级均不加收高峰费。
   - 将三段阶梯基础金额依次叠加租户等级折扣、优先级系数与高峰系数后，做一次标准半美分四舍五入取整，得到券前应付 `pre_voucher_cents`。

### 3.3 代金券核销、预冻结担保、超支 120% 封顶与跌破门槛反转
- **代金券规则**：管理员可为已存在租户（不存在返 `404`）创建唯一 `code`（重复返 `409 ALREADY_EXISTS`）的代金券，含正整数 `discount_cents`、非负整数 `min_spend_cents` 及整数过期时间 `expires_at_epoch`。
- **提单校验与冻结**：提单校验活跃作业（`QUEUED` 或 `RUNNING`）数量未达 `max_concurrent`（否则 `409 CONCURRENCY_LIMIT_EXCEEDED`），且 `已耗配额 + 活跃作业预估秒数之和 + 本单预估秒数 <= quota_gpu_sec`（否则 `409 QUOTA_EXCEEDED`）。若传 `voucher_code`：不存在返 `404`，非本租户返 `403 TENANT_FORBIDDEN`，已被其他未释放作业绑定或已消费返 `409 VOUCHER_ALREADY_USED`，`submitted_epoch > expires_at_epoch` 返 `400 VOUCHER_EXPIRED`，`pre_voucher_cents < min_spend_cents` 返 `400 VOUCHER_MIN_SPEND_NOT_MET`。抵扣额不超过 `pre_voucher_cents`，差额 `reserved_cents` 从可用余额原子转入冻结担保金并记 `RESERVE` 流水（可用余额不足返 `402 INSUFFICIENT_BALANCE`）。
- **正常结算（`SUCCEEDED`）**：传入正整数 `actual_gpu_sec`（仅允许 `RUNNING` 状态结算，否则 `409 INVALID_STATE_TRANSITION`）。租户 `used_gpu_sec` 按真实 `actual_gpu_sec` 累加，但**计费时长 `billable_gpu_sec` 封顶在 `estimated_gpu_sec` 的 `120%`（向下取整）**。按 `billable_gpu_sec` 重算实结券前金额：若绑定了代金券且实结券前金额仍不低于 `min_spend_cents` 则抵扣，**若因提前结束导致实结券前金额跌破 `min_spend_cents`，本单不再享受代金券抵扣（抵扣置 0）**，且无论是否触发抵扣，一旦作业 `SUCCEEDED` 结算成功，该券即永久核销不可再退！
  - 若实收 `final_charge_cents <= reserved_cents`：从冻结金核销实收（记 `SETTLE`），多余冻结金退回可用余额（记 `REFUND_UNUSED`）；
  - 若实收 `final_charge_cents > reserved_cents`（因实际超耗时或跌破代金券门槛引发补差）：差额从当前可用余额补扣（合并记一笔 `SETTLE`）；若当前可用余额不足补差，**必须整单事务回滚并返回 `402 INSUFFICIENT_BALANCE_FOR_OVERRUN`**，作业保持 `RUNNING`、冻结金与代金券状态丝毫不动！

### 3.4 作业状态机、运行取消违约金、抢单与超时回收（`reap`）
- **失败回报（`fail`）**：仅 `RUNNING` 可调用，零收费，全额退回 `reserved_cents` 至可用余额（记 `CANCEL_RELEASE`），**释放绑定的代金券供后续新单复用**，置 `FAILED`。
- **用户取消（`cancel`）**：仅 `QUEUED` 或 `RUNNING` 可调用：
  - `QUEUED` 取消：零违约金，全额解冻退回 `reserved_cents`（记 `CANCEL_RELEASE`），释放代金券；
  - `RUNNING` 取消：按 `reserved_cents` 的 **`10%`（标准半美分四舍五入）** 收取违约金 `penalty_cents`（若 `> 0` 记 `CANCEL_PENALTY` 从冻结金核销归入平台收入），其余部分退回可用余额（若 `> 0` 记 `CANCEL_RELEASE`），**同时释放绑定的代金券**，`used_gpu_sec` 不变。
- **节点抢单（`claim`）**：在 `status == 'QUEUED'` 且 `now_epoch <= ttl_until_epoch` 的作业中，严格按 `HIGH > NORMAL > SPOT`、`submitted_epoch ASC`、`job_id ASC` 选出唯一一条置为 `RUNNING`，写入 `worker_node_id`、`lease_until_epoch = now_epoch + lease_seconds`（默认 60）并将 `version` 加 1；无单可抢时返回 `{"job": null}`。
- **超时回收（`reap`）**：同一写事务内扫描：
  1. `QUEUED` 且 `now_epoch > ttl_until_epoch` → 置 `EXPIRED`，全额解冻退款（`CANCEL_RELEASE`）并**释放代金券**（计入 `expired_count`）；
  2. `RUNNING` 且 `now_epoch > lease_until_epoch`：若 `retry_count < max_retries`，置回 `QUEUED`、`retry_count += 1`、清空节点与租约、将 `ttl_until_epoch` 顺延至 `max(原 ttl_until_epoch, now_epoch + 300)`，**原冻结担保金与绑定的代金券必须原封保留锁定（严禁误退或重扣）**（计入 `requeued_count`）；若已达重试上限，则置 `FAILED`，全额解冻退款并**释放代金券**（计入 `failed_count`）。
- **并发隔离**：多线程同时抢单、同时提单或同时充值结算时，必须通过数据库立即写锁保证零重复发单、零透支、零并发超限且不返回 `500` 锁崩溃。

---

## 4. 第一阶段 HTTP 路由契约速查表

| 序号 | 方法与路径 | 鉴权 | 请求 JSON 入参 | 成功状态码与 `data` 必需返回字段 |
| --- | --- | --- | --- | --- |
| 1 | `GET /health` | 无 | 无 | `200`：`{"status": "healthy"}` |
| 2 | `POST /api/v1/admin/tenants` | Admin | `{"tenant_id": str, "name": str, "tier": "STARTER\|GROWTH\|ENTERPRISE", "api_secret": str, "now_epoch"?: int}` | `201`：`{"tenant_id", "name", "tier", "balance_cents": 0, "frozen_cents": 0, "quota_gpu_sec", "used_gpu_sec": 0}` |
| 3 | `POST /api/v1/admin/vouchers` | Admin | `{"code": str, "tenant_id": str, "discount_cents": int, "min_spend_cents": int, "expires_at_epoch": int}` | `201`：`{"code", "tenant_id", "discount_cents", "min_spend_cents", "expires_at_epoch", "redeemed": false}` |
| 4 | `POST /api/v1/admin/reap` | Admin | `{"now_epoch": int}` | `200`：`{"expired_count": int, "requeued_count": int, "failed_count": int}` |
| 5 | `GET /api/v1/admin/audit` | Admin | 无 | `200`：`{"all_balanced": bool, "total_available_cents": int, "total_frozen_cents": int, "active_escrow_cents": int, "total_platform_revenue_cents": int, "anomalies": list}` |
| 6 | `POST /api/v1/tenants/{tenant_id}/topup` | Tenant HMAC（幂等） | `{"amount_cents": int, "now_epoch"?: int}` | `200`：`{"tenant_id", "balance_cents": int, "frozen_cents": int}` |
| 7 | `GET /api/v1/tenants/{tenant_id}/statement` | Tenant HMAC | 无 | `200`：`{"tenant_id", "tier", "balance_cents", "frozen_cents", "quota_gpu_sec", "used_gpu_sec", "reserved_gpu_sec", "active_jobs", "total_topup_cents", "total_charged_cents", "ledger_count", "ledger": [...]}`（注：`total_charged_cents` 为实结费用与运行取消违约金之和） |
| 8 | `POST /api/v1/jobs` | Tenant HMAC（幂等） | `{"job_id"?: str, "gpu_type": "A10\|L40\|H100", "priority": "NORMAL\|HIGH\|SPOT", "estimated_gpu_sec": 1..86400, "submitted_epoch": int, "ttl_seconds"?: int(默认600), "max_retries"?: 0..5(默认1), "voucher_code"?: str}` | `201`：`{"job_id", "tenant_id", "status": "QUEUED", "gpu_type", "priority", "estimated_gpu_sec", "reserved_cents": int, "pre_voucher_cents": int, "discount_cents": int, "submitted_epoch", "ttl_until_epoch", "max_retries", "retry_count": 0}` |
| 9 | `GET /api/v1/jobs/{job_id}` | Tenant HMAC | 无 | `200`：`{"job_id", "tenant_id", "status", "gpu_type", "priority", "estimated_gpu_sec", "actual_gpu_sec", "reserved_cents", "final_charge_cents", "penalty_cents", "voucher_code", "retry_count", "max_retries", "worker_node_id", "lease_until_epoch", "version"}` |
| 10 | `POST /api/v1/workers/claim` | Admin | `{"worker_node_id": str, "now_epoch": int, "lease_seconds"?: int(默认60)}` | `200`：`{"job": <作业对象或 null>}` |
| 11 | `POST /api/v1/jobs/{job_id}/settle` | Tenant HMAC（幂等） | `{"actual_gpu_sec": int(>=1), "now_epoch"?: int}` | `200`：`{"job_id", "status": "SUCCEEDED", "actual_gpu_sec", "billable_gpu_sec", "reserved_cents", "final_charge_cents", "refunded_cents", "extra_charged_cents", "balance_cents", "frozen_cents"}` |
| 12 | `POST /api/v1/jobs/{job_id}/fail` | Tenant HMAC（幂等） | `{"reason"?: str, "now_epoch"?: int}` | `200`：`{"job_id", "status": "FAILED", "refunded_cents": int, "balance_cents", "frozen_cents"}` |
| 13 | `POST /api/v1/jobs/{job_id}/cancel` | Tenant HMAC（幂等） | `{"now_epoch"?: int}` | `200`：`{"job_id", "status": "CANCELLED", "penalty_cents": int, "refunded_cents": int, "balance_cents", "frozen_cents"}` |
