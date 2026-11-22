# 统帅交互与沙盒协议（docs/protocol.md）

## 1. 统帅五大管理动作协议（`tools/marshal.py`）

所有动作统一输出 UTF-8 JSON，并追加写入带 `prev_hash -> entry_hash` 链式签名的 `runs/<model>/trace.jsonl`：

1. **`define_contract`**：写入或升级 `.marshal_state/GLOBAL_CONTRACT.md`（上限 `15000` 字符）。后续每次唤醒任意工位时，该文件以只读模式置顶挂载至工位工作台根目录。
2. **`manage_worker`**：注册或调整工位（总数上限 `5` 个）。`allowed_paths` 必须为 `cloudhub/` 或 `tests/` 下的具体 `.py` 文件路径（`1..4` 个文件，禁止通配符、禁止目录全开、禁止包含 `smoke_public.py` 或 `TASK.md`）。
3. **`dispatch_task`**：向目标工位派发工单（全局总派单紧预算上限 `14` 次，单工位累计唤醒上限 `5` 次，单张上限 `4000` 字符，且同一工位存在未裁决 `PENDING` 提交时必须先完成 `review_submission`）。完成后返回 `submission_id`、语法检查结果、工人汇报摘要及相对主干的 Unified Diff。
4. **`inspect_and_test`**：
   - `status`：查看契约版本、工位编制、待审提交与剩余预算；
   - `trunk` / `staging`：审阅主干文件或待审提交 Diff（标记 `inspected_before_review=true`）；
   - `smoke` / `unittest`：将主干（可选叠加指定 `--submission-id` 的暂存改动）拷入临时沙盒执行测试，不污染主干（标记 `tested_before_review=true`）。
5. **`review_submission`**：
   - `APPROVE`：校验无跨工位基线冲突后解锁主干写入并立即重加只读锁，刷新 `trunk_manifest`；
   - `REJECT`：要求填写具体审查批注，默认立即在原工位会话与暂存代码基础上触发一次返工并生成新 `submission_id`（**同一工单链最多允许返工 `2` 次，第 3 次打回将直接触发熔断拒绝**；传 `--discard` 则仅作废解锁不自动重跑）。

## 2. 信任域产物文件契约

- `runs/<model>/protocol.json`：记录起点 `t0`、终点 `t1`、`env_commit`、`task_commit`、`worker_config_commit` 与终局 `code_sha256`；
- `runs/<model>/trace.jsonl`：链式防篡改调度事件轨迹；
- `leaderboard/blind/<pack_id>/`：纯脱敏盲评包（含 `REFERENCE_LEVEL5.md`、`RUBRIC.md`、`SUBMIT.md`、`case-*.md`、`case-*_code/`）；
- `leaderboard/reviews.jsonl`：独立评委追加式中心日志。
