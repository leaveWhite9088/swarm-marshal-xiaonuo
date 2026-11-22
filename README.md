# 群智统帅 · Swarm Marshal（`swarm-marshal-xiaonuo`）

> **一句话定位**：剥夺被测大模型的直接写代码权限，将所有底层执行工位统一锁定为同一个关闭深度思考的轻量级弱模型（基于 `pi` Harness），看谁能像真正的技术总监一样：**立全局契约、从零搭班子划领地、把复杂耦合需求拆成傻瓜式工单、毒辣审代码擦屁股**，带着一群“笨工人”拼出一套严丝合缝的多模块企业级系统。

当前赛制版本为 `xiaonuo-v1.0`，工程规范与盲评流转体系完全对齐同系列基准（`silicon-rider-xiaonuo`、`global-settle-xiaonuo`），调度与工位会话底座融合自 `speclock2`。最新成绩见 [`leaderboard/scores.md`](leaderboard/scores.md)。

---

## 为什么传统多智能体评测测不出真正的“统帅能力”？

1. **拒绝假剧本，直面真实弱模型的天然短板**：不靠编写虚假的“刺头人设”，而是直接将所有工位锁定在同一个轻量级弱模型（默认 `pi` RPC 驱动 `DeepSeek-V4.1-Flash` 且强制 `thinking=off`，同时内置零成本确定性模拟弱工人供离线冒烟）。真实弱模型一旦遇到长上下文或跨文件耦合，天然就会顾头不顾尾、漏掉特例、写错字段名。
2. **空白编制池，建制与规划占一半**：开局不预设任何角色。统帅在最多 `5` 个工位、单工位最多绑定 `4` 个 `.py` 文件、**全局最多 `14` 次唤醒、单工单最多 `2` 次返工熔断**的紧预算约束下，必须靠前期契约与拆单一次把活讲透，严防后期暴力抽卡硬磨；派单中途回头改全局契约或超额返工均会在客观分中被重扣。每个工位干活时物理上只能看见《全局架构契约》与自己白名单内的文件。
3. **物理禁手与反代写护栏**：沙盒主干开启操作系统只读锁与逐文件 SHA-256 账本核验，统帅绕过工人改一行代码即判作弊归零；工单或契约中若夹带连续超过 7 行的现成 Python 实现代码，护栏当场拦截并扣分。

---

## 四维评分体系（满分 100 分）

总分 = **A 终局系统集成交付（30）+ B 统领效率与经济性（15）+ C 组织管控与把关（25）+ D 统帅领导力盲评（30）**。

| 维度 | 满分 | 算分时机与方式 | 评什么 |
| --- | --- | --- | --- |
| **A 终局系统集成交付** | **30** | 出榜时跑三波黑盒压测 | 对合入主干的 `cloudhub`（18 条耦合规则的云算力多租户计费与异步作业网关）执行三波跨模块验收：**A1 主链路连通（12）**、**A2 v1 老库在线迁表与阶梯/SPOT/退券/守恒不变量（10）**、**A3 幂等冲突、租约重试回收与多线程并发抢单（8）**。 |
| **B 统领效率与经济性** | **15** | 出榜时从轨迹计算（**受 `A/30` 封顶**） | **B1 调度经济性与一次成活率（5）** + **B2 端到端统领墙钟耗时（10，6 分钟内满分、8 分钟半衰期）**。终局系统交付不及格时，草率收工拿不到任何速度分。 |
| **C 组织管控与把关** | **25** | 出榜时从轨迹与 AST 计算 | **C1 角色领地正交度与跨模块 AST 接口对齐率（9）**、**C2 合入前预审/预测纪律与有效缺陷拦截（8）**、**C3 团队分工均衡度与自底向上拓扑节奏（8，严惩单工位传声筒）**。 |
| **D 统帅领导力与思想** | **30** | 独立评委对照 5 级样本盲评 | 评委审阅隐去模型名与客观分的《统帅调度档案》，对照内置《官方 5 级平庸统帅参照样本》打分：**D1 搭班子与契约先行（11）**、**D2 任务降维防呆功力（10）**、**D3 审查批注毒辣度与诚实复盘（9）**。未明显超越 5 级样本者硬性锁死 `D <= 15`！ |

---

## 标准全流程命令速查

```sh
# 1) 统帅开局（打下起点 t0，锁定环境与弱模型配置哈希承诺）
python tools/start_run.py --model Demo-s1

# 2) 统帅逐轮调用五大管理动作统领全局
python tools/marshal.py --model Demo-s1 define_contract --file draft/contract.md
python tools/marshal.py --model Demo-s1 manage_worker --worker-id db_worker --allowed-paths cloudhub/db.py --role-prompt "..."
python tools/marshal.py --model Demo-s1 dispatch_task --worker-id db_worker --ticket-file draft/t1.md
python tools/marshal.py --model Demo-s1 inspect_and_test --target staging --submission-id sub_001
python tools/marshal.py --model Demo-s1 review_submission --submission-id sub_001 --decision APPROVE --comment "..."

# 3) 统帅收尾：填写 briefing/RETROSPECTIVE.md 并清空 draft/ 后，冻结 t1 生成匿名盲评包（绝不生成客观分）
python src/scoring/judge_pack.py --model Demo

# 4) 独立评委盲评（只读 leaderboard/blind/<pack_id>/，对照 REFERENCE_LEVEL5.md 提交打分）
python src/scoring/judge_submit.py --pack-dir leaderboard/blind/<pack_id> --case-id case-01 \
  --judge-id judge_01 --judge-model gpt-5.4 \
  --D1 9.5 --D2 9.0 --D3 8.0 --surpasses-level5 yes \
  --evidence "[C01] [W02] [T03] [R02]" --note "对比 5 级样本结论与最强反证"

# 5) 维护者审计并一键汇总出榜
python tools/isolation_audit.py --strict
python tools/timing_audit.py runs --strict
python leaderboard/finalize.py

# 6) 开发者全量回归与三基线梯度自检
pwsh tests/smoke.ps1
```

---

## 仓库目录结构

```text
AGENTS.md                  进门必读：三角色信任域分流与防越界操作规程
configs/benchmark.json     全局唯一配置源（弱模型 pi 锁定参数、护栏上限、A/B/C/D 计分常量）
docs/                      设计思想（design.md）、动作与业务协议（protocol.md）、计分公式（scoring.md）
src/sandbox_template/      选手开局沙盒模板（含 18 条规则 TASK.md 与基础连通冒烟 smoke_public.py）
src/marshal/               调度核心引擎、物理禁手与反代写护栏、模拟弱工人及 speclock2 pi Harness 底座
src/baselines/             甩手掌柜 / 官方 5 级传声筒 / 9 级王牌统帅三套基线与 5 级参照样本
src/scoring/               三波黑盒集成压测、B/C 轨迹分析、盲评档案打包、双指纹缓存与出榜合成
tools/                     start_run.py、marshal.py、isolation_audit.py、timing_audit.py
leaderboard/               官方总榜（scores.md）、匿名盲评包（blind/）、私有映射与裁决留档
tests/                     护栏/计分单元测试、三基线端到端全链路测试与一键自检脚本 smoke.ps1
```
