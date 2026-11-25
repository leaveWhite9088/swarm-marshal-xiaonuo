# 群智统帅 · Swarm Marshal（`swarm-marshal-xiaonuo` · `xiaonuo-v2.0`）

> **一句话定位**：剥夺被测大模型的直接写代码权限，将所有底层执行工位统一锁定为同一个关闭深度思考的轻量级弱模型（基于 `pi` Harness），面对一套**预埋多处线上缺陷的近千行半成品系统**与**中途突发的第二阶段紧急业务演进**，在**最多 3 个子代理工位**的紧编制下，考核谁才是真正懂侦察排雷、立契约解耦、多工位并行统筹与毒辣实证把关的技术统帅。

当前版本为 `xiaonuo-v2.0`，调度与并行工位底座融合自 `speclock2`，信任域物理隔离与双指纹盲评体系对齐同系列基准。最新成绩见 [`leaderboard/scores.md`](leaderboard/scores.md)。

---

## 核心设计亮点（`xiaonuo-v2.0`）

1. **存量半成品排雷 + 二阶段紧急需求冲击**：不再是从零写玩具空壳，开局沙盒即为默认能过浅层冒烟、实则暗藏浮点偶舍入、脏账期初失衡、鉴权前查幂等跨租户泄密、运行取消漏退券与并发竞态的五模块遗留系统；一阶段重构合入后解锁《第二阶段紧急演进需求》（高优算力抢占补贴 + 母子组织授信池原子代付），直接检验一阶段架构耦合度。
2. **三工位紧编制与真并行波次调度**：最多仅允许招募 `3` 个工位（单工位最多绑定 `3` 个 `.py` 文件，契约 `<= 5500` 字，工单 `<= 2000` 字），原生支持多工位同波次并行派发。契约解耦严密的统帅可驱动三工位齐头并进大幅压缩关键路径耗时，分工混乱者则会陷入“两个干等一个”的单点串行泥潭。
3. **重过程统领质量的四维百分体系（`25 + 20 + 25 + 30`）**：
   - **A 终局系统交付（25 分）**：四波 40 项跨模块黑盒对抗与多线程混沌风暴压测（兼作 B/C 封顶乘数防空架子刷分）；
   - **B 统筹效率与并行杠杆（20 分）**：关键路径墙钟耗时（10）+ 同波次并行推进度、工人词元能效比与指令信噪比（10）；
   - **C 五大统领过程管控（25 分）**：先侦察后决策情报纪律与二阶段抗冲击弹性（8）+ 零容忍盲裁实证把关与打回一击修平率（9）+ 三工位频次/代码量双均衡与防单点死磕流转（8）；
   - **D 双标尺领导力盲评（30 分）**：内置 `[P01]`–`[P06]` 过程取证卡、15 分平庸锁顶线与 25 分宗师准入门槛及六项逐条硬扣分表。

---

## 标准全流程命令速查

```sh
# 1) 统帅开局（打下起点 t0，锁定环境与弱模型配置哈希承诺）
python tools/start_run.py --model Demo-s1

# 2) 统帅勘探存量排雷、立契约、建三工位、并行派单、实证预检合入并解锁二阶段
python tools/marshal.py --model Demo-s1 inspect_and_test --target trunk --path cloudhub/db.py
python tools/marshal.py --model Demo-s1 define_contract --file draft/contract.md
python tools/marshal.py --model Demo-s1 manage_worker --worker-id data_billing --allowed-paths cloudhub/db.py cloudhub/billing.py --role-file draft/r1.md
python tools/marshal.py --model Demo-s1 dispatch_parallel --tasks data_billing:draft/t1.md engine_gateway:draft/t2.md
python tools/marshal.py --model Demo-s1 inspect_and_test --target staging --submission-id sub_001
python tools/marshal.py --model Demo-s1 review_submission --submission-id sub_001 --decision APPROVE --comment "..."
python tools/marshal.py --model Demo-s1 unlock_phase2

# 3) 统帅收尾：填写 briefing/RETROSPECTIVE.md 并清空 draft/ 后，冻结 t1 生成匿名盲评包
python src/scoring/judge_pack.py --model Demo

# 4) 独立评委双标尺盲评（只读 leaderboard/blind/<pack_id>/）
python src/scoring/judge_submit.py --pack-dir leaderboard/blind/<pack_id> --case-id case-01 \
  --judge-id judge_01 --judge-model gpt-5.4 \
  --D1 9.5 --D2 9.0 --D3 8.0 --surpasses-level5 yes --grandmaster-tier yes \
  --evidence "[C01] [P02] [P04] [R02] [M01]" --note "双标尺对比结论与逐项扣分核验"

# 5) 维护者审计并一键汇总出榜
python tools/isolation_audit.py --strict
python tools/timing_audit.py runs --strict
python leaderboard/finalize.py

# 6) 开发者全量回归与三基线梯度自检
pwsh tests/smoke.ps1
```
