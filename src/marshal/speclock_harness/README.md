# SpecLock2 → Swarm Marshal 执行底座映射说明

本目录直接复用并针对评测隔离需求裁剪自 `speclock2`（`packages/agentd` 与 `packages/drivers`）：

- `core/pi/rpc.mjs` & `core/pi/models.mjs`：`pi --mode rpc` 标准双向 JSONL 客户端与私有 `models.json` 编排器；
- `core/base/process.mjs` & `core/base/leak.mjs`：Windows/POSIX 跨平台子进程树强杀（`killTree`）与弱模型伪工具调用文本泄漏检测（`TOOL_CALL_AS_TEXT`）；
- `core/records/reply.mjs` & `transcripts.mjs`：执行者超长输出头尾智能裁剪与按工位持久化的会话轨迹记录；
- `drivers/pi.mjs` & `pi-catalog.mjs` & `pi-adapter/acp-adapter.mjs`：`pi` CLI 驱动探测、推理档位目录与 ACP 协议适配器；
- `contexts/executor.md` & `project.md`：下属工位运行纪律提示词（强制白名单领地隔离、只读全局契约、禁起常驻进程）；
- `pi_worker_runner.mjs`：面向 `tools/marshal.py` 派单与打回返工的工位级执行入口，锁定弱模型与 `thinking=off`，保持单工位跨轮会话记忆与超限自动 `compact`。
