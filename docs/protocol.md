# 统帅交互与沙盒协议（docs/protocol.md）

## 1. 统帅管理动作协议（`tools/marshal.py`）

所有动作在跨进程状态锁保护下统一输出 UTF-8 JSON，并追加写入带 `prev_hash -> entry_hash` 链式签名的 `runs/<model>/trace.jsonl`：

1. **`define_contract`**：写入或升级 `.marshal_state/GLOBAL_CONTRACT.md`（上限 `5500` 字符）。后续每次唤醒任意工位时，该文件以只读模式置顶挂载至工位独立隔离沙盒。除二阶段解锁赠送的 1 次免费改约额度外，派单中途改约将触发规划折损扣分。
2. **`manage_worker`**：注册或调整工位（总数上限 `3` 个）。`allowed_paths` 必须为 `cloudhub/` 或 `tests/` 下的具体 `.py` 文件路径（`1..3` 个文件，工位间互不重叠，禁止通配符、禁止目录全开、禁止包含 `smoke_public.py` 或 `TASK.md`）。
3. **`dispatch_task` / `dispatch_parallel`**：向单个或多个无待审冲突的工位派发工单（全局总派单紧预算上限 `15` 次，单工位累计唤醒上限 `6` 次，单张上限 `2000` 字符）。支持多进程或 `dispatch_parallel` 同波次真实并发唤醒 `pi` 工人，并在各自独立临时沙盒内编码返回 `submission_id`、语法检查结果与相对主干的 Unified Diff。
4. **`inspect_and_test`**：
   - `status`：查看契约版本、阶段状态、工位编制、待审提交与剩余预算；
   - `trunk` / `staging`：勘探存量主干模块或审阅待审提交 Diff（标记 `inspected_before_review=true`）；
   - `smoke` / `unittest`：将主干（可选叠加指定 `--submission-id` 的暂存改动）拷入临时沙盒执行测试，绝不污染主干（标记 `tested_before_review=true`）。
5. **`review_submission`**：
   - `APPROVE`：校验无跨工位基线冲突后解锁主干写入并立即重加只读锁，刷新 `trunk_manifest` 与历史快照备份；
   - `REJECT`：要求填写具体审查批注，默认在原工位会话与暂存代码基础上触发返工并生成新 `submission_id`（**同一工单链最多允许返工 `2` 次，第 3 次打回直接触发熔断拒绝**；传 `--discard` 则仅作废解锁不重跑）。
6. **`unlock_phase2`**：一阶段核心修改合入且公开冒烟通过后调用，生成 `briefing/PHASE2_TASK.md` 并解锁 1 次免费契约演进额度。

## 2. 信任域产物文件契约

- `runs/<model>/protocol.json`：记录起点 `t0`、终点 `t1`、`env_commit`、`task_commit`、`worker_config_commit` 与终局 `code_sha256`；
- `runs/<model>/trace.jsonl`：链式防篡改调度事件轨迹（含每次工位调用的起止毫秒时间戳与阶段标记）；
- `leaderboard/blind/<pack_id>/`：纯脱敏盲评包（含 `REFERENCE_LEVEL5.md`、`RUBRIC.md`、`SUBMIT.md`、带 `[P01]–[P06]` 过程证据摘要的 `case-*.md`、`case-*_code/`）；
- `leaderboard/reviews.jsonl`：独立评委追加式中心日志（强制校验 `--surpasses-level5` 与 `--grandmaster-tier` 双锚点门禁）。
