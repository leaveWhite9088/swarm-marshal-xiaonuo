# 项目总需求书：云算力多租户配额计费与异步作业网关（`cloudhub`）

## 1. 背景与工程边界

你需要统领下属执行工位，在沙盒内基于 **Python 3.12 标准库 + SQLite3**（严禁引入任何第三方依赖包）完整设计并实现一套企业级后端系统 **`cloudhub`**：支持多租户账户双本位账本、v1 老库平滑在线迁移、多档位阶梯算力计费与代金券核销、异步 GPU 作业有限状态机与租约超时回收、HMAC 请求签名鉴权以及全局资金守恒对账。

- **唯一外部黑盒入口契约**：`cloudhub/app.py` 必须提供工厂函数：
  `create_server(db_path: str, host: str = "127.0.0.1", port: int = 0, admin_token: str = "marshal-admin-secret") -> http.server.ThreadingHTTPServer`
  调用方通过 `srv = create_server(db_path)` 获取实例并置于后台线程 `srv.serve_forever()`，停止时调用 `srv.shutdown(); srv.server_close()`。同时支持命令行启动 `python -m cloudhub.app --db <db_path> --port <port> [--admin-token <token>]`。
- **内部架构与团队分工完全开放**：`cloudhub/` 内部如何划分子模块、`tests/` 如何编写集成自测、招募哪几个工种、每个工位绑定哪几个文件（单工位最多允许绑定 4 个读写文件），全部由你（统帅）自主决策并通过《全局架构契约》约束。
- **金额与时间单位**：所有金额一律使用**整数美分（`int`，单位 cents）**，严禁使用浮点数存储金额；所有时间戳一律使用**整数秒级 Unix Epoch（`int`）**。

---

## 2. 统一响应信封与鉴权协议

### 2.1 JSON 响应信封
- 所有成功响应（HTTP `200` / `201`）统一返回：
  `{"ok": true, "data": <object>}`
- 所有失败响应（HTTP `400` / `401` / `402` / `403` / `404` / `409` / `501`）统一返回：
  `{"ok": false, "error": {"code": "<UPPER_SNAKE_CODE>", "message": "<可读错误说明>"}}`

### 2.2 双通道鉴权协议（规则 `R12`）
1. **公开健康检查**：`GET /health` 免鉴权，返回 `200` 及 `{"ok": true, "data": {"status": "healthy"}}`。
2. **平台管理员接口**（所有 `/api/v1/admin/*` 与工作节点抢单 `/api/v1/workers/*`）：
   - 必须携带请求头 `X-Admin-Token`；缺失返回 `401 UNAUTHORIZED`，令牌不匹配返回 `403 ADMIN_FORBIDDEN`。
3. **租户签名接口**（所有 `/api/v1/tenants/*` 与 `/api/v1/jobs/*`）：
   - 必须携带三个请求头：`X-Tenant-Id`、`X-Timestamp`（整数秒字符串）、`X-Signature`；缺失或格式非法返回 `401 UNAUTHORIZED`，租户不存在返回 `401 UNKNOWN_TENANT`。
   - **时间窗校验**：记请求体中若显式传入 `now_epoch` 则仅作业务时钟，签名校验基准时间取请求头 `X-Now-Epoch`（若测试端提供该头则以该整数秒为准，否则取 `int(time.time())`）。当 `abs(ref_now - int(X-Timestamp)) > 300` 时返回 `401 SIGNATURE_EXPIRED`。
   - **HMAC-SHA256 签名算法**：
     记 `body_bytes` 为原始请求体字节串（GET 空体为 `b""`），`body_sha256 = hashlib.sha256(body_bytes).hexdigest()`，`path` 为不含查询串的纯路径（如 `/api/v1/jobs`），`method` 为大写 HTTP 方法（如 `POST`）。
     待签字符串为：`f"{method}\n{path}\n{timestamp}\n{body_sha256}"`（UTF-8 编码），密钥为租户创建时配置的明文 `api_secret`。计算小写十六进制 `hmac.new(api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()`，与 `X-Signature` 常量时间比对（`hmac.compare_digest`），不匹配返回 `401 INVALID_SIGNATURE`。
   - **多租户越权隔离**：租户访问 `/api/v1/tenants/{tenant_id}/*` 时若路径 `tenant_id != X-Tenant-Id`，或访问/操作 `/api/v1/jobs/{job_id}*` 时该作业不属于 `X-Tenant-Id`，一律返回 `403 TENANT_FORBIDDEN`。

---

## 3. 十八条核心业务规则与不变量（`R01`–`R18`）

### 领域一：持久层、v1 老库平滑迁移与幂等账本
- **`R01`（幂等建表与非破坏性 v1→v2 在线迁表）**：服务启动初始化 SQLite 时须开启 `PRAGMA journal_mode=WAL` 与 `busy_timeout=5000`。若传入的数据库文件已是历史 **v1 老库**——其中已存在旧版表：
  - `tenants(tenant_id TEXT PRIMARY KEY, name TEXT, tier TEXT, balance_cents INTEGER, api_secret TEXT, created_at INTEGER)`（缺少 `frozen_cents`、`quota_gpu_sec`、`used_gpu_sec` 等新列）
  - `ledger_entries(entry_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, job_id TEXT, entry_type TEXT, delta_available INTEGER, balance_after INTEGER, created_at INTEGER)`（缺少 `delta_frozen`、`frozen_after` 等新列）
  系统必须通过 `PRAGMA table_info(...)` 检测并执行 `ALTER TABLE ... ADD COLUMN` 在线增量补齐缺失列（老租户 `frozen_cents` 补 `0`、`used_gpu_sec` 补 `0`、`quota_gpu_sec` 按其 `tier` 补标准配额；老流水 `delta_frozen` 与 `frozen_after` 补 `0`），**严禁 `DROP TABLE` 清空老租户余额与历史流水**！
- **`R02`（双本位资金守恒不变量）**：每个租户维护可用余额 `balance_cents >= 0` 与冻结担保金 `frozen_cents >= 0`。任何改变 `balance_cents` 或 `frozen_cents` 的操作，必须在同一数据库事务内写入一条不可变 `ledger_entries` 流水（`entry_type` 取值：`TOPUP`、`RESERVE`、`SETTLE`、`REFUND_UNUSED`、`CANCEL_RELEASE`、`CANCEL_PENALTY`）。任意时刻对任意租户：所有流水 `SUM(delta_available)`（加上 v1 迁入且无初始流水的期初差额，若 v1 老库已有对应 `TOPUP` 流水则直接相等）严格等于当前 `balance_cents`，`SUM(delta_frozen)` 严格等于当前 `frozen_cents`！
- **`R03`（请求级幂等键 `Idempotency-Key`）**：对写接口（充值 `topup`、提交作业 `POST /api/v1/jobs`、结算 `settle`、失败回报 `fail`、取消 `cancel`），若请求头携带非空 `Idempotency-Key`：
  - 同一 `(tenant_id, method, path, idempotency_key)` 且请求体 SHA-256 完全相同时，不重复执行扣费/入账/变态，原样返回首次处理时的 HTTP 状态码与 JSON 响应；
  - 同一幂等键但请求体 SHA-256 不同时，拒绝执行并返回 `409 IDEMPOTENCY_CONFLICT`。

### 领域二：多档位阶梯计费、时段倍率与代金券引擎
- **`R04`（三档租户等级与配额上限）**：
  | `tier` | 最大活跃并发作业数 `max_concurrent` | 月度总配额 `quota_gpu_sec` | 等级折扣付费比例 `tier_rate_pct` |
  | --- | --- | --- | --- |
  | `STARTER` | `2` | `3600` | `100`（原价） |
  | `GROWTH` | `5` | `18000` | `90`（九折） |
  | `ENTERPRISE` | `10` | `108000` | `80`（八折） |
- **`R05`（机型单价与两段阶梯计费）**：支持三种机型 `gpu_type`：`A10`（基础单价 `2` cents/gpu_sec）、`L40`（`5` cents/gpu_sec）、`H100`（`12` cents/gpu_sec）。给定算力秒数 `S`（`S >= 0`）与机型单价 `unit_cents`：
  - 前 `600` 秒按 `100%` 计价：`tier1_sec = min(S, 600)`；
  - 超出 `600` 秒的部分按 `80%` 阶梯优惠计价：`tier2_sec = max(0, S - 600)`；
  - 阶梯折前基准分（放大 100 倍以保精度）：`bracket_x100 = tier1_sec * unit_cents * 100 + tier2_sec * unit_cents * 80`。
- **`R06`（优先级与峰谷乘数及整数半进位取整）**：
  - 作业优先级 `priority`：`NORMAL`（`priority_pct = 100`）、`HIGH`（`priority_pct = 125`）、`SPOT`（`priority_pct = 70`）。
  - 高峰期判定：提交作业时的 `submitted_epoch` 转换为 UTC 小时 `utc_hour = (submitted_epoch // 3600) % 24`；当 `9 <= utc_hour < 21` 时为高峰期。
  - **关键特例**：高峰期下 `NORMAL` 与 `HIGH` 的 `peak_pct = 120`（上浮 20%），但 **`SPOT` 竞价实例不受高峰期上浮影响，高峰期 `peak_pct` 仍固定为 `100`**！非高峰期所有优先级的 `peak_pct = 100`。
  - 折后券前总费用（整数精确半进位）：
    `numer = bracket_x100 * tier_rate_pct * priority_pct * peak_pct`
    `pre_voucher_cents = (numer + 50_000_000) // 100_000_000`（注：分母为 `100 * 100 * 100 * 100 = 100_000_000`）。
- **`R07`（代金券核销与回退规则）**：管理员可为指定租户发放代金券（`code`、`tenant_id`、`discount_cents > 0`、`min_spend_cents >= 0`、`expires_at_epoch`）。
  - 提交作业传入可选 `voucher_code` 时：校验该券存在、归属当前租户（否则 `403`/`404`）、未被其他未释放作业占用（否则 `409 VOUCHER_ALREADY_USED`）、`submitted_epoch <= expires_at_epoch`（否则 `400 VOUCHER_EXPIRED`）、且预估折后券前费用 `pre_voucher_cents >= min_spend_cents`（否则 `400 VOUCHER_MIN_SPEND_NOT_MET`）。
  - 抵扣额 `applied_discount = min(discount_cents, pre_voucher_cents)`，实冻金额 `reserved_cents = pre_voucher_cents - applied_discount`，并将该券绑定至当前 `job_id`。
  - **退券规则**：若绑定了代金券的作业最终进入 `CANCELLED`、`FAILED` 或 `EXPIRED`（未成功完成结算），必须立即解除该代金券的占用（恢复为可再次被后续新作业使用的状态）！若作业 `SUCCEEDED` 结算，实结时若实算券前金额仍 `>= min_spend_cents` 则继续抵扣 `min(discount_cents, actual_pre_voucher_cents)`，否则不予抵扣（且已成功结算的作业代金券视为已消费，不再退回）。
- **`R08`（预冻结担保与超支 1.2 倍封顶结算）**：
  - 提交作业时，要求租户当前 `balance_cents >= reserved_cents`（否则返回 `402 INSUFFICIENT_BALANCE`）；通过后原子执行 `balance_cents -= reserved_cents`、`frozen_cents += reserved_cents` 并记 `RESERVE` 流水。
  - 作业 `SUCCEEDED` 结算传入 `actual_gpu_sec`（整数 `>= 1`）时：
    - **计费秒数封顶**：`billable_gpu_sec = min(actual_gpu_sec, (estimated_gpu_sec * 120) // 100)`（即超出预估时长 1.2 倍以上的部分免收超支费，由平台兜底）。
    - 用同一作业的 `(gpu_type, tier, priority, submitted_epoch, voucher)` 对 `billable_gpu_sec` 重新计算实结应收 `final_charge_cents`。
    - 若 `final_charge_cents <= reserved_cents`：从 `frozen_cents` 中核销 `final_charge_cents`（记 `SETTLE`：`delta_available=0, delta_frozen=-final_charge_cents`），剩余未用冻结金 `refund = reserved_cents - final_charge_cents`（若 `refund > 0`）退回 `balance_cents`（记 `REFUND_UNUSED`：`delta_available=+refund, delta_frozen=-refund`）。
    - 若 `final_charge_cents > reserved_cents`（实际耗时超预估或实耗跌破代金券门槛导致补差）：差额 `extra = final_charge_cents - reserved_cents` 必须从租户当前可用余额 `balance_cents` 补扣！若此时 `balance_cents < extra`，**拒绝本次结算并返回 `402 INSUFFICIENT_BALANCE_FOR_OVERRUN`**（事务回滚，作业保持 `RUNNING` 状态不变，冻结金不动）；若余额充足，则核销全部 `reserved_cents` 冻结金并从 `balance_cents` 扣除 `extra`（记 `SETTLE`：`delta_available=-extra, delta_frozen=-reserved_cents`）。

### 领域三：异步作业状态机、抢单调度与租约超时回收
- **`R09`（严格有限状态机与运行中取消违约金）**：作业状态集合为 `QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`、`EXPIRED`。仅允许以下合法转移：
  1. `QUEUED -> RUNNING`：工作节点抢单成功。
  2. `QUEUED -> CANCELLED`：排队期用户主动取消——**零违约金**，全额解冻退回 `reserved_cents` 至 `balance_cents`（记 `CANCEL_RELEASE`：`delta_available=+reserved_cents, delta_frozen=-reserved_cents`，若 `reserved_cents == 0` 可不记零额流水或记 0），释放配额与代金券。
  3. `QUEUED -> EXPIRED`：排队期超过 `ttl_until_epoch` 被扫描回收——全额解冻退回 `reserved_cents`（记 `CANCEL_RELEASE`），释放配额与代金券。
  4. `RUNNING -> SUCCEEDED`：正常结算（`actual_gpu_sec >= 1`），累加租户 `used_gpu_sec += actual_gpu_sec`（注意：`used_gpu_sec` 按真实物理消耗 `actual_gpu_sec` 累加）。
  5. `RUNNING -> FAILED`：算力节点故障上报失败——**零收费**，全额解冻退回 `reserved_cents`（记 `CANCEL_RELEASE`），释放代金券，`used_gpu_sec` 不增加。
  6. `RUNNING -> CANCELLED`：运行期用户强行取消——**收取 `reserved_cents` 的 `10%` 作为算力违约金** `penalty_cents = (reserved_cents * 10 + 50) // 100`，其余 `refund_cents = reserved_cents - penalty_cents` 退回可用余额！在同一事务内：`frozen_cents` 减少 `reserved_cents`，`balance_cents` 增加 `refund_cents`（记 `CANCEL_PENALTY`：`delta_available=0, delta_frozen=-penalty_cents` 若 `penalty_cents > 0`；记 `CANCEL_RELEASE`：`delta_available=+refund_cents, delta_frozen=-refund_cents` 若 `refund_cents > 0`），同时释放代金券，`used_gpu_sec` 不增加。
  7. `RUNNING -> QUEUED`：仅限租约超时扫描器在 `retry_count < max_retries` 时触发重入队（见 `R11`）。
  - 任何其他状态转移（如对已 `SUCCEEDED`/`CANCELLED`/`FAILED`/`EXPIRED` 的作业再次结算、取消、报错，或对 `QUEUED` 作业直接 `settle`/`fail`）一律返回 `409 INVALID_STATE_TRANSITION`。
- **`R10`（优先级出队调度）**：`POST /api/v1/workers/claim`（传入 `worker_node_id`、`now_epoch`、`lease_seconds`，默认 `lease_seconds=60`）在当前 `status == 'QUEUED'` 且 `now_epoch <= ttl_until_epoch` 的作业中，严格按以下优先级选出唯一一条作业：
  1. `priority` 等级：`HIGH` 最优先，其次 `NORMAL`，最后 `SPOT`；
  2. 同优先级按 `submitted_epoch ASC`（先提交先服务）；
  3. 再相同按 `job_id ASC` 字典序。
  选中后在同一写事务内将其置为 `RUNNING`，设置 `worker_node_id`、`lease_until_epoch = now_epoch + lease_seconds`、`version = version + 1` 并返回该作业详情；若无符合条件的待抢作业，返回 `200` 及 `{"ok": true, "data": {"job": null}}`。
- **`R11`（租约与排队超时扫描器 `reap`）**：`POST /api/v1/admin/reap`（传入 `{"now_epoch": <int>}`）在事务内完成两类清理并返回统计计数：
  1. 所有 `status == 'QUEUED'` 且 `now_epoch > ttl_until_epoch` 的作业 → 转为 `EXPIRED`，全额解冻退回 `reserved_cents` 并释放代金券（计入 `expired_count`）；
  2. 所有 `status == 'RUNNING'` 且 `now_epoch > lease_until_epoch` 的作业：
     - 若 `retry_count < max_retries`：转为 `QUEUED`，`retry_count += 1`，清空 `worker_node_id` 与 `lease_until_epoch`，顺延 `ttl_until_epoch = max(ttl_until_epoch, now_epoch + 300)`，**原已冻结的 `reserved_cents` 与代金券保持锁定不变（严禁重复扣冻或误退）**（计入 `requeued_count`）；
     - 若 `retry_count >= max_retries`：转为 `FAILED`，全额解冻退回 `reserved_cents` 并释放代金券（计入 `failed_count`）。

### 领域四 & 五：配额控制、并发安全、对账单与全局审计
- **`R13`（统一错误码与状态码规范）**：参数格式/范围错误一律 `400 INVALID_ARGUMENT`；鉴权/签名失败 `401`；余额不足 `402`；越权 `403`；资源不存在 `404 NOT_FOUND`；状态/幂等/并发数/配额冲突一律 `409`。
- **`R14`（多线程并发写事务隔离）**：并发抢单（`claim`）、并发提交（`submit`）、并发充值与取消时，必须使用事务锁（如 `BEGIN IMMEDIATE`）防止两个线程同时抢到同一个 `QUEUED` 作业，防止并发提交击穿余额或并发上限，且不得抛出未捕获的 `sqlite3.OperationalError: database is locked` 导致 `500`。
- **`R15`（活跃并发数与配额预占实时核算）**：某租户当前的活跃作业定义为 `status IN ('QUEUED', 'RUNNING')` 的作业集合：
  - 提交新作业前，若当前活跃作业数 `>= max_concurrent`，拒绝并返回 `409 CONCURRENCY_LIMIT_EXCEEDED`；
  - 记当前活跃作业的 `SUM(estimated_gpu_sec)` 为 `reserved_gpu_sec`。若 `used_gpu_sec + reserved_gpu_sec + new_estimated_gpu_sec > quota_gpu_sec`，拒绝并返回 `409 QUOTA_EXCEEDED`。
- **`R16`（租户财务与配额对账单接口）**：`GET /api/v1/tenants/{tenant_id}/statement` 返回该租户实时快照：
  `tenant_id`, `tier`, `balance_cents`, `frozen_cents`, `quota_gpu_sec`, `used_gpu_sec`, `reserved_gpu_sec`, `active_jobs`, `total_topup_cents`（累计充值额）, `total_charged_cents`（累计已结算实付 + 运行取消违约金）, `ledger_count`, 以及按 `entry_id ASC` 排列的 `ledger` 列表。
- **`R17`（平台全局资金与担保守恒审计接口）**：`GET /api/v1/admin/audit` 跨全库核验并返回：
  - `total_available_cents`：全库租户 `SUM(balance_cents)`；
  - `total_frozen_cents`：全库租户 `SUM(frozen_cents)`；
  - `active_escrow_cents`：全库处于 `('QUEUED', 'RUNNING')` 状态作业的 `SUM(reserved_cents)`；
  - `total_platform_revenue_cents`：全平台已确认收入（所有 `SETTLE` 实收总额 + 所有 `CANCEL_PENALTY` 违约金总额）；
  - `all_balanced`：布尔值，当且仅当 `total_frozen_cents == active_escrow_cents`、所有租户余额与冻结金非负、且每个租户的流水增量和与账户快照一致时为 `true`；
  - `anomalies`：异常描述字符串列表（正常时为空列表 `[]`）。
- **`R18`（输入参数严格边界检验）**：
  - 充值 `amount_cents` 必须为正整数（`> 0`）；
  - 提交作业：`estimated_gpu_sec` 必须为 `1..86400` 之间的整数；`gpu_type` 必须在 `{"A10", "L40", "H100"}`；`priority` 必须在 `{"NORMAL", "HIGH", "SPOT"}`；`ttl_seconds` 必须 `>= 1`（默认 `600`）；`max_retries` 必须在 `0..5`（默认 `1`）；
  - 结算作业：`actual_gpu_sec` 必须为正整数（`>= 1`）；
  - 任何非法 JSON、类型不符（如传入布尔值或浮点数冒充整数）或越界参数均返回 `400 INVALID_ARGUMENT`。

---

## 4. 完整 HTTP 路由契约速查表

| 序号 | 方法与路径 | 鉴权 | 请求 JSON 入参 | 成功状态码与 `data` 关键返回字段 |
| --- | --- | --- | --- | --- |
| 1 | `GET /health` | 无 | 无 | `200`：`{"status": "healthy"}` |
| 2 | `POST /api/v1/admin/tenants` | Admin | `{"tenant_id": str, "name": str, "tier": "STARTER\|GROWTH\|ENTERPRISE", "api_secret": str, "now_epoch"?: int}` | `201`：`{"tenant_id", "name", "tier", "balance_cents": 0, "frozen_cents": 0, "quota_gpu_sec", "used_gpu_sec": 0}`（已存在同 ID 返回 `409 ALREADY_EXISTS`） |
| 3 | `POST /api/v1/admin/vouchers` | Admin | `{"code": str, "tenant_id": str, "discount_cents": int, "min_spend_cents": int, "expires_at_epoch": int}` | `201`：`{"code", "tenant_id", "discount_cents", "min_spend_cents", "expires_at_epoch", "redeemed": false}` |
| 4 | `POST /api/v1/admin/reap` | Admin | `{"now_epoch": int}` | `200`：`{"expired_count": int, "requeued_count": int, "failed_count": int}` |
| 5 | `GET /api/v1/admin/audit` | Admin | 无 | `200`：`{"all_balanced": bool, "total_available_cents": int, "total_frozen_cents": int, "active_escrow_cents": int, "total_platform_revenue_cents": int, "anomalies": list}` |
| 6 | `POST /api/v1/tenants/{tenant_id}/topup` | Tenant HMAC（支持 `Idempotency-Key`） | `{"amount_cents": int, "now_epoch"?: int}` | `200`：`{"tenant_id", "balance_cents": int, "frozen_cents": int}` |
| 7 | `GET /api/v1/tenants/{tenant_id}/statement` | Tenant HMAC | 无 | `200`：`{"tenant_id", "tier", "balance_cents", "frozen_cents", "quota_gpu_sec", "used_gpu_sec", "reserved_gpu_sec", "active_jobs", "total_topup_cents", "total_charged_cents", "ledger_count", "ledger": [...]}` |
| 8 | `POST /api/v1/jobs` | Tenant HMAC（支持 `Idempotency-Key`） | `{"job_id"?: str, "gpu_type": str, "priority": str, "estimated_gpu_sec": int, "submitted_epoch": int, "ttl_seconds"?: int, "max_retries"?: int, "voucher_code"?: str}` | `201`：`{"job_id", "tenant_id", "status": "QUEUED", "gpu_type", "priority", "estimated_gpu_sec", "reserved_cents": int, "pre_voucher_cents": int, "discount_cents": int, "submitted_epoch", "ttl_until_epoch", "max_retries", "retry_count": 0}` |
| 9 | `GET /api/v1/jobs/{job_id}` | Tenant HMAC | 无 | `200`：完整作业对象 `{"job_id", "tenant_id", "status", "gpu_type", "priority", "estimated_gpu_sec", "actual_gpu_sec", "reserved_cents", "final_charge_cents", "penalty_cents", "voucher_code", "retry_count", "max_retries", "worker_node_id", "lease_until_epoch", "version"}` |
| 10 | `POST /api/v1/workers/claim` | Admin | `{"worker_node_id": str, "now_epoch": int, "lease_seconds"?: int}` | `200`：`{"job": <作业对象或 null>}` |
| 11 | `POST /api/v1/jobs/{job_id}/settle` | Tenant HMAC（支持 `Idempotency-Key`） | `{"actual_gpu_sec": int, "now_epoch"?: int}` | `200`：`{"job_id", "status": "SUCCEEDED", "actual_gpu_sec", "billable_gpu_sec", "reserved_cents", "final_charge_cents", "refunded_cents", "extra_charged_cents", "balance_cents", "frozen_cents"}` |
| 12 | `POST /api/v1/jobs/{job_id}/fail` | Tenant HMAC（支持 `Idempotency-Key`） | `{"reason"?: str, "now_epoch"?: int}` | `200`：`{"job_id", "status": "FAILED", "refunded_cents": int, "balance_cents": int, "frozen_cents": int}` |
| 13 | `POST /api/v1/jobs/{job_id}/cancel` | Tenant HMAC（支持 `Idempotency-Key`） | `{"now_epoch"?: int}` | `200`：`{"job_id", "status": "CANCELLED", "penalty_cents": int, "refunded_cents": int, "balance_cents": int, "frozen_cents": int}` |
