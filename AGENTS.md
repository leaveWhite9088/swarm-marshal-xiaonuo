# AGENTS.md — 角色认领与信任域隔离操作手册（xiaonuo-v2.0）

> 给进入本仓库的 **AI 智能体与人类维护者**的第一入口指南。
> **进门第一件事：在下方第 `0` 节确认你当前的唯一角色，然后只阅读属于你的章节！严禁跨信任域越界阅读或执行禁止命令！**

---

## 0. 一眼认领角色：你是谁？现在该干什么？

本基准在同一仓库内通过**物理禁手只读锁 + 链式调度轨迹哈希 + 环境/代码快照承诺 + 盲评脱敏审计**严格隔离不同角色。**同一智能体绝不允许在同一批次中既当被测统帅选手，又给自己当评委或私自跑评分脚本。**

| 你的角色 | 怎么判断是你 | 你唯一允许阅读的范围 | 核心行动闭环 | 绝对禁止触碰的红线（触碰即成绩作废） |
| --- | --- | --- | --- | --- |
| **① 被测统帅（选手域）** | 用户让你“作为统帅参赛 / 完成本仓库基准任务”，并指定了你的模型名 | **只读本文 `§0–§1`**，开局后**只读 `runs/<你的模型名>/briefing/`**（及通过 `marshal.py` 检视沙盒） | 1. 跑 `tools/start_run.py` 开局打点 `t0`<br>2. 读 `briefing/TASK.md` 并检视主干存量半成品排雷<br>3. 用 `marshal.py` 完成一阶段重构后调 `unlock_phase2` 承接二阶段演进<br>4. 填写 `briefing/RETROSPECTIVE.md`，跑 `judge_pack.py` 冻结 `t1` 生成匿名盲评包 | 严禁读本文 `§2–§4`；严禁读 `README.md`、`docs/`、`configs/`、`src/`、`leaderboard/` 或他人 `runs/*`；**严禁直接修改 `workspace/` 内任何文件**；**严禁在工单/契约中夹带现成业务源码**；严禁运行 `score_run.py` 或 `finalize.py` |
| **② 独立评委（盲评域）** | 用户交给你 `leaderboard/blind/<pack_id>/` 路径让你做 D 层盲评 | **只读交接给你的 `leaderboard/blind/<pack_id>/` 目录**（如先看到本文件，只看 `§0` 与 `§2`） | 1. 先读包内 `REFERENCE_LEVEL5.md`（5 级平庸样本标尺）与 `RUBRIC.md`<br>2. 审阅 `case-*.md` 与 `case-*_code/`<br>3. 调用 `src/scoring/judge_submit.py` 追加 D1/D2/D3 打分 | 严禁访问 `runs/`、`leaderboard/blind-maps/`、`reviews.jsonl`、`scores.md`、`verdicts/`、`configs/` 或 `src/`；严禁打探模型真名或客观分 |
| **③ 汇总与维护者（出榜域）** | 所有评委盲评提交完毕，用户让你执行审计并合成总榜，或维护基准代码 | 本文全部章节及全仓文档 | 1. 跑 `tools/isolation_audit.py --strict` 与 `tools/timing_audit.py runs --strict`<br>2. 跑 `leaderboard/finalize.py` 执行终局三波集成压测并合成 `leaderboard/scores.md` | 不得替在途选手代写代码或手工涂改 `trace.jsonl` / `评分.json` |

### 通用目录卫生纪律（全角色适用）
- 除规定产物路径外，不得在仓库内随意新建、修改文件或残留调试垃圾。
- 如统帅在撰写契约或工单文本时需要临时草稿文件（如通过 `--file draft/ticket.md` 传参避免命令行转义问题），**只允许写入 `draft/` 目录，且在运行 `judge_pack.py` 前必须彻底清空删除 `draft/`**（`isolation_audit.py --strict` 会拦截任何非空 `draft/`）。

---

## 1. 被测统帅（选手域）：三分钟上手与完整参赛规程

> **统帅注意**：读完本节（`§1`）后请**立即停止往下翻阅**！直接运行下方第 1 步开局命令进入你的案头目录。

### 1.1 你的核心使命与三大物理铁律
你扮演一名**技术总监 / 群智统帅**，接手一套预埋了多处跨模块生产缺陷的半成品后端系统，并需经历**“一阶段存量排雷重构 + 二阶段紧急需求演进”**双阶段考验。你的下属执行工位全部由系统统一锁定的**轻量级弱模型**（基于 `pi` Harness，关闭深度思考档位）驱动：它们单次视野有限、上下文一长就漏边界、跨文件一多就把接口字段名写劈叉。
1. **紧编制搭班子与划领地**：系统最多只给你 **`3` 个工位编制**，每个工位最多允许绑定 **`3` 个 `.py` 读写白名单文件**（严禁通配符或单工位全包 5 个业务模块，只建 1 个工位将被重罚且必崩）。工位每次被唤醒时，**物理上只能看见你制定的《全局架构契约》以及它自己白名单内的存量/暂存文件**！
2. **物理禁手（只动嘴、不动手）**：`runs/<名>/workspace/` 已开启只读锁与 SHA-256 账本监控。统帅严禁绕过工位直接修改沙盒代码，否则整局直接判作弊归零！
3. **高信噪比契约、严禁源码代写与预算熔断**：全局契约上限 **`5500` 字符**、单张工单上限 **`2000` 字符**、岗位提示词上限 **`1200` 字符**；只允许写模块职责、函数签名、自然语言逻辑与防呆修改批注，若夹带大段现成 Python 业务实现代码（如连续超过 7 行实现语句）将被直接拦截扣分！**全局最多允许派单 `15` 轮（含返工），单工位最多唤醒 `6` 次，同一工单链最多允许打回返工 `2` 次（超限熔断）**；除解锁二阶段后获赠的 1 次免费契约演进额度外，派单中途回头改契约均将重扣规划折损分！

### 1.2 第一步：一键开局（记录起点时钟 `t0`）

在仓库根目录执行（`<统帅模型名>` 由宿主指令给定，若跑多局请串行并使用 `-s1`/`-s2` 后缀）：

```sh
python tools/start_run.py --model <统帅模型名>
```

开局后，请立即阅读你的专属案头目录：
- `runs/<统帅模型名>/briefing/TASK.md`：项目完整业务需求（18 条耦合规则与 HTTP 路由契约）；
- `runs/<统帅模型名>/briefing/MARSHAL_DESK.md`：命令速查卡；
- `runs/<统帅模型名>/briefing/RETROSPECTIVE.md`：收尾前填写的统领复盘模板。

### 1.3 第二步：全程使用统帅命令统领两阶段开发

所有多行长文本强烈建议先写入 `draft/` 下的临时 `.md` 文件再通过 `--file` 传入（收尾前清空 `draft/`），避免 Shell 引号转义损坏：

```sh
# 动作 0：先勘探主干半成品存量代码排雷（初始半成品默认能过浅层 smoke，但潜伏多处跨模块缺陷！）
python tools/marshal.py --model <统帅模型名> inspect_and_test --target trunk
python tools/marshal.py --model <统帅模型名> inspect_and_test --target trunk --path cloudhub/db.py

# 动作 1：立全局规矩（<= 5500 字符，必须在派发首张工单前率先完成！）
python tools/marshal.py --model <统帅模型名> define_contract --file draft/contract.md

# 动作 2：紧编制搭班子与划领地（最多 3 个工位；每个工位 allowed-paths 最多 3 个 .py 文件，互不重叠）
python tools/marshal.py --model <统帅模型名> manage_worker \
  --worker-id data_billing \
  --allowed-paths cloudhub/db.py cloudhub/billing.py \
  --role-file draft/role_data.md

# 动作 3：拆单派活（<= 2000 字符，支持单工位派单或同波次多工位并行派单，返回各工位 Unified Diff）
python tools/marshal.py --model <统帅模型名> dispatch_task \
  --worker-id data_billing \
  --ticket-file draft/ticket_01.md
python tools/marshal.py --model <统帅模型名> dispatch_parallel \
  --tasks data_billing:draft/ticket_01.md engine_gateway:draft/ticket_02.md

# 动作 4：查代码与跑自检（每次裁决前务必先审阅 staging Diff 或跑叠加测试，严禁盲批！）
python tools/marshal.py --model <统帅模型名> inspect_and_test --target staging --submission-id sub_001
python tools/marshal.py --model <统帅模型名> inspect_and_test --target unittest --submission-id sub_003

# 动作 5：拍板合入或打回重做
python tools/marshal.py --model <统帅模型名> review_submission \
  --submission-id sub_001 --decision APPROVE --comment "迁移基线与事务锁核验无误"
python tools/marshal.py --model <统帅模型名> review_submission \
  --submission-id sub_002 --decision REJECT --comment-file draft/review_fix.md

# 动作 6：解锁第二阶段紧急演进需求（一阶段重构合入且冒烟通过后调用，生成 briefing/PHASE2_TASK.md 并赠送 1 次免费改约额度）
python tools/marshal.py --model <统帅模型名> unlock_phase2
```

> **重要提醒**：初始半成品代码原本就能跑通公开冒烟检查（`--target smoke`），但它**绝不覆盖**半美分四舍五入、三段阶梯、高峰期 `SPOT` 豁免、脏数据老库期初对账、跌破代金券门槛反超支、二阶段抢占补贴、母子共享授信池与跨租户幂等攻防压测！你必须勘探源码排雷、完成二阶段解锁演进，并设立测试工位守住质量底线。

### 1.4 第三步：统帅收尾与生成匿名盲评包（严禁自算得分）

1. 在主干跑通你的全套自测后，如实填写 `runs/<统帅模型名>/briefing/RETROSPECTIVE.md`（切勿夸大未实际执行的测试，评委会与轨迹逐条比对）；
2. （可选成本诊断，不参与评分）将本局统帅自身的词元消耗写入 `runs/<统帅模型名>/token_usage.json`：
   `{"input_tokens": null, "output_tokens": null, "total_tokens": null, "source": "self_report", "note": "统计说明"}`
3. **彻底删除清空 `draft/` 目录**；
4. 运行打包脚本冻结终点时钟 `t1` 并生成纯脱敏盲评包：

```sh
python src/scoring/judge_pack.py --model <统帅模型名>
```

把输出的 `leaderboard/blind/<pack_id>/` 目录路径交给独立评委即可收工。**严禁运行 `score_run.py` 或 `finalize.py`！**

---

## 2. 独立评委（盲评域）：对照 5 级样本盲评规程

> 如果你是选手，严禁阅读本节及以下内容！如果你是独立评委，请**只访问交接给你的 `leaderboard/blind/<pack_id>/` 目录**。

1. **先立标尺**：必先完整阅读盲评包内的 `REFERENCE_LEVEL5.md`（《官方 5 级传声筒平庸统帅参照样本》）与 `RUBRIC.md`；
2. **铁律五硬锁顶**：凡是团队建制、契约深度、工单降维拆解与审查批注指导力**未明显超越 5 级参照样本**的，D 层总分必须锁死在 **`<= 15.0 / 30`**，并在提交命令中传 `--surpasses-level5 no`；
3. **证据锚定提交**：逐案阅读 `case-*.md` 与 `case-*_code/`，引用至少 2 个不同证据编号（如 `[C01] [W02] [T03] [R02]`），写明对比结论与最强反证，追加至中心日志（不查看他人评分，支持同 `--judge-id` 随时覆盖重评）：

```sh
python src/scoring/judge_submit.py \
  --pack-dir leaderboard/blind/<pack_id> \
  --case-id case-01 \
  --judge-id <评委唯一ID> \
  --judge-model <评委模型名> \
  --D1 9.5 --D2 9.0 --D3 8.0 \
  --surpasses-level5 yes \
  --evidence "[C01] [W02] [T03] [R02] [M01]" \
  --note "契约先行锁死跨模块签名，精准审出 SPOT 高峰期与退券两处弱工人漏洞；反证：高并发极端锁超时回退未单独构造故障注入用例"
```

---

## 3. 汇总与出榜维护者（出榜域）

当全部待评盲评包完成评委打分后，按顺序执行审计与一键出榜：

```sh
# 1) 全链路物理禁手、环境承诺与盲评零泄密严格审计
python tools/isolation_audit.py --strict

# 2) 检查同模型多批次是否存在并行墙钟重叠
python tools/timing_audit.py runs --strict

# 3) 查看盲评聚合摘要（可选）
python src/scoring/review_merge.py --log leaderboard/reviews.jsonl --out leaderboard/reviews.merged.json

# 4) 一键执行终局三波集成压测（或复用防篡改缓存）+ 实时合并盲评合成总榜
python leaderboard/finalize.py
# 如需忽略客观分缓存强制全量重跑三波黑盒压测：
python leaderboard/finalize.py --recompute
```

---

## 4. 仓库开发者维护规范

- **单一配置源**：所有工位上限、文件白名单上限、代写拦截阈值、弱模型锁定参数与 A/B/C/D 分值常量均集中在 `configs/benchmark.json`，严禁在业务脚本硬编码魔法数字。
- **底层 Harness 架构**：`src/marshal/speclock_harness/` 源自 `speclock2` 的 `PiRpc` 与进程池隔离底座，任何改动须先通过 `npm run check:js` 与 `pwsh tests/smoke.ps1` 全套自检（含三基线端到端分数梯度验证）。
