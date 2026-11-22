# 官方 5 级（传声筒式平庸统帅）参照样本（`REFERENCE_LEVEL5.md`）

> **本样本的法定标尺作用（反高分通胀铁律五）**：
> 本文件展示了一名典型的 **5 级平庸统帅（对应 D1 ≈ 5.5 / 11、D2 ≈ 5.0 / 10、D3 ≈ 4.5 / 9，D 总分 = 15.0 / 30）** 的完整建制、立约、派单与打回特征。
> **评委硬性红线**：审阅待评 `case-*.md` 时，必须逐项对照本参照样本。**凡是在建制正交性、全局契约精细度、工单降维拆解深度以及代码审查批注具体度上未明显超越本样本的被测统帅，D 层总分必须锁死在 `<= 15.0 / 30`（且提交时传 `--surpasses-level5 no`）！**

---

## 一、5 级平庸统帅的典型病灶画像

1. **契约极度粗略（应付差事）**：虽然调用了 `define_contract`，但只写了几行笼统口号（如“大家统一用 SQLite，返回 `{"ok": true, "data": ...}`，表名叫 `tenants` 和 `jobs`”），**根本没有规定跨模块 Python 函数签名、参数名、异常类、整数半进位公式与 v1 老库迁表字段**，导致工位之间仍靠猜测对接。
2. **建制粗放（大包大揽）**：只建了 3 个宽泛工位（`data_worker`、`api_worker`、`test_worker`），单个工位塞进多个异构模块职责，岗位提示词只有一句“你是后端工程师，负责写好代码”。
3. **传声筒式派单（零任务降维）**：派单时不做逻辑拆解与防呆提示，直接把原需求文档的大段规则概括复述扔给弱模型工人（例如：“请把计费和持久层写出来，注意支持阶梯计价、代金券和账本”），导致弱模型漏掉 `>600s` 两段阶梯、`SPOT` 高峰免上浮、v1 `ALTER TABLE` 在线迁移与 `Idempotency-Key` 冲突 `409`。
4. **盲批合入与敷衍打回**：多次未先跑 `inspect_and_test` 审查暂存区就直接 `APPROVE` 合入；等跑冒烟报错后才匆忙打回，且 `REJECT` 批注只写空洞套话（如“冒烟测试报错了，导入对不上，请检查修复”），不指出具体哪一行、哪个字段或哪条边界漏了。
5. **复盘粉饰太平**：在 `RETROSPECTIVE.md` 中声称“已完成全部计费阶梯、并发与老库迁移自测”，而实际 `tests/` 中只有一条 `/health` 连通测试。

---

## 二、5 级参照样本核心片段实录

### 1. 粗略的《全局架构契约》（仅约 180 字，缺签名与边界）
```markdown
# 全局契约
1. 数据库统一使用 SQLite，核心表为 tenants、ledger_entries、vouchers、jobs。
2. 金额字段统一使用 balance_cents 和 frozen_cents，作业预冻结字段用 reserved_cents。
3. cloudhub/app.py 暴露 create_server(db_path, host, port, admin_token) 供外部启动。
4. 接口统一返回 {"ok": true, "data": ...} 或 {"ok": false, "error": {"code": ..., "message": ...}}。
```

### 2. 粗放的工位设置
- `data_worker`：绑定 `["cloudhub/db.py", "cloudhub/pricing.py"]`，提示词：`负责数据库操作和算力计费逻辑。`
- `api_worker`：绑定 `["cloudhub/jobs.py", "cloudhub/app.py"]`，提示词：`负责作业管理接口、鉴权和 HTTP 服务。`
- `test_worker`：绑定 `["tests/test_basic.py"]`，提示词：`负责写测试验证服务启动。`

### 3. 传声筒式派单示例（未拆解公式与防呆点）
```text
请实现 cloudhub/db.py 和 cloudhub/pricing.py：
建好 tenants、ledger_entries、vouchers、jobs 表，提供 init_db、tx、append_ledger、check_idempotency、save_idempotency，
以及 compute_pre_voucher_cents、compute_billable_seconds、apply_voucher_discount。
按照需求支持 STARTER/GROWTH/ENTERPRISE 折扣与 A10/L40/H100 计价，保证语法正确。
```

### 4. 敷衍的代码审查批注示例
- 批准合入时未先执行 `inspect_and_test(staging)`，批注仅写：`ok 合入`。
- 打回重做时批注仅写：`联调时模块调用和预期不一致，请检查接口重新修改`。

---

## 三、什么才算超越 5 级（迈入 7～10 级优秀/王牌统帅）？

| 对比维度 | ≤ 5 级（传声筒甩手掌柜，锁死 D ≤ 15） | 8～10 级（真正的技术总监/王牌统帅，D = 24～30） |
| --- | --- | --- |
| **D1 建制与契约（11）** | 无契约或契约仅列口号；只建 1～2 个万能工位，或角色职责含糊重叠 | 开工首步即确立详尽契约（精确到表列定义、跨模块函数签名、入参出参类型、整数取整公式与错误码枚举）；4～5 个正交工位各司其职、领地零重叠、岗位提示词针对弱模型短板定好禁令 |
| **D2 任务降维功力（10）** | 原题大段转手抄送，一张单塞满十余条耦合规则，不给弱模型拆步骤 | 把复杂耦合规则嚼碎成每单 2～4 条正交要求，用自然语言把易错特例（如 `SPOT` 高峰期免乘 `1.2`、`>600s` 按 `80%`、v1 `ALTER TABLE` 禁删表、`RUNNING` 取消收 `10%` 违约金并退券）逐条写成防呆清单，且零源码代写 |
| **D3 审查与擦屁股（9）** | 不审 Diff 盲目 `APPROVE`；打回只会说“报错了重修”；复盘夸大未测能力 | 每次合入前必跑 `staging` 审阅或叠加 `unittest`/`smoke`；能一针见血揪出弱模型首轮漏掉的隐蔽边界（指出具体函数、条件分支与修复逻辑）；独立测试工位真测真验，复盘客观诚实 |
