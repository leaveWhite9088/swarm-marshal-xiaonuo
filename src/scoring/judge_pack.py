#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成匿名《统帅调度档案》盲评包（judge_pack.py）。

选手收尾唯一合法入口：
1. 冻结每局终点时钟 t1（若尚未冻结），固化终局代码快照哈希 code_sha256；
2. 核验主干业务代码确系经调度器合入（无非授权直写），严禁计算或写入任何官方客观得分（A/B/C）；
3. 提取完全脱敏的《统帅调度档案》（含建制表、全局契约、派单与打回轨迹、复盘说明与脱敏代码），
   连同《官方 5 级平庸统帅参照样本》（REFERENCE_LEVEL5.md）一并打入 leaderboard/blind/<pack_id>/；
4. 将私有模型映射写入 leaderboard/blind-maps/<model_id>.<pack_id>.blind-map.json（评委不可见）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import secrets
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import verify_workspace_integrity  # noqa: E402
from src.scoring import metrics, objective_cache  # noqa: E402
from src.scoring.model_identity import canonical_model_id, matches_batch_run  # noqa: E402
from tools.start_run import compute_workspace_sha256  # noqa: E402

RUBRIC_MD = """# D 层统帅领导力双标尺盲评量表与过程扣分铁律（满分 30 分 · xiaonuo-v2.0）

## 0. 强制前置动作：双标尺锚定（15 分平庸顶 + 25 分宗师门）

在给任何 `case-*.md` 打分前，**必须先完整通读同目录下的 `REFERENCE_LEVEL5.md`（《官方 5 级平庸统帅参照样本》）**，并逐条核对档案首节的 **`[P01]`–`[P06]` 五大统领过程客观取证记录**！
1. **标尺一 · 15 分平庸锁顶线（`--surpasses-level5 no`）**：凡建制清晰度、前期排雷深度、契约完备度、工单降维颗粒度及审查指导力**未明显超越 `REFERENCE_LEVEL5.md`** 的，D 层总分**硬性锁死 `<= 15.0 / 30`**！
2. **标尺二 · 25 分宗师准入门槛（`--grandmaster-tier no`）**：为彻底杜绝高端局扎堆高分，**只有同时满足以下全部四项严苛条件**的统帅才允许进入 `25.5 – 30.0` 宗师区间（传 `--grandmaster-tier yes`）；**缺任意一项必须传 `--grandmaster-tier no` 且总分锁死 `<= 25.0 / 30`**：
   - `[P01]` 开局首单前已调阅至少 `4/5` 个存量核心模块完成排雷诊断；
   - `[P02]` 充分发挥 3 工位协同，存在真实的同波次并行/交替并发推进（`并行率 > 0`），且无单工位连续死磕 `>= 3` 轮或单工位代码占比 `> 70%` 的忙闲失衡；
   - `[P03]` 顺利解锁并完整交付第二阶段演进需求，全程零非豁免中途补丁改约；
   - `[P04]` **全程零盲裁**（每一次 `APPROVE` 与 `REJECT` 裁决前均执行了暂存审阅或叠加测试），且终局代码无跨租户幂等泄密或账本失衡漏洞。

---

## 1. 五大统领过程核心评价维度与 D1/D2/D3 映射

### D1 · 侦察排雷、三工位建制与契约前瞻性（0 – 11 分，对应过程维度 1/2/3）
- **0 – 3.5 分（盲目失职）**：`[P01]` 不看存量半成品源码就拍脑袋派单；无契约或单工位大包大揽；未解锁第二阶段（`[P03]`）。
- **4 – 5.5 分（5 级平庸基准）**：仅扫目录或粗看个别文件，契约只列笼统表名/路由，缺跨模块函数签名、整数半进位算式、v1 期初脏账对账与先鉴权再幂等时序；工位职责含糊。
- **6 – 8.5 分（稳健架构师）**：开局侦察主要存量模块，契约先行覆盖一阶段签名与错误码，3 工位边界无重叠并完成二阶段演进，但缺乏并行波次编排或契约略显臃肿。
- **9 – 11 分（王牌技术总监）**：`[P01]` 开局全面勘探 5 个存量模块精准锁死全部暗雷；在 5500 字上限内立下高信噪比契约，使得 3 个正交工位（如数据计费、调度网关、独立验收）得以同波次并行开工；`[P03]` 二阶段仅靠 1 次免费增量契约平滑落地抢占补贴与母子授信池，零推倒重写。

### D2 · 任务降维功力、协同节奏与指令杠杆率（0 – 10 分，对应过程维度 1/4）
- **0 – 3.0 分（原题直转 / 违规代写）**：复制粘贴原需求甩给弱模型，或夹带实现源码触发拦截（`[C03]`/`[V]`）。
- **4 – 5.0 分（5 级平庸基准）**：工单粗放复述需求，漏拆三段阶梯、`SPOT` 高峰豁免、跌破代金券门槛反超支、超时重入队保冻保券等陷阱；单点串行硬磨。
- **6 – 8.0 分（清晰拆单）**：按模块拆解输入输出与主要边界，但存在纯串行“两个工位干等一个”、单工位负载过重或工单字数堆砌微操现象。
- **8.5 – 10 分（极致降维与并行编排）**：`[P02]`/`[P05]` 用精炼高杠杆工单将去公式化的业务口径转化为弱模型零歧义防呆清单，合理组织多工位并行/紧密交替推进，三工位忙闲均衡，工人一次成活率高且词元消耗克制。

### D3 · 实证主义把关、一击纠偏指导力与诚实复盘（0 – 9 分，对应过程维度 5）
- **0 – 2.5 分（盲裁放水 / 复盘造假）**：`[P04]` 大量未审阅暂存代码即盲目 `APPROVE`/`REJECT`；或 `[M01]` 复盘编造 `[P06]` 中根本不存在的测试断言。
- **3 – 4.5 分（5 级平庸基准）**：偶有检查但多次盲裁；打回批注空洞（如“报错了重修”），导致多轮无效返工；`[P06]` 测试仅测皮毛。
- **5 – 7.0 分（常规把关）**：多数合入前有预检，能指出明显接口偏差，但仍有个别未预检裁决或遗漏深层安全/并发边界（如跨租户同幂等键嗅探、4xx 污染幂等键）。
- **7.5 – 9 分（毒辣实证把关）**：`[P04]` **100% 裁决前实证预检（零盲裁）**；`[P05]` 打回意见直击具体函数与条件分支，实现 **100% 一击修平**；`[P06]` 驱动测试工位构建高密度真实断言覆盖一二阶段耦合与并发，复盘客观诚实。

---

## 2. 六项强制逐条扣分清单（评委打分时必须对照核减！）

在给出最终分前，必须逐项对照以下扣分表硬性核减（并在 `--note` 中写明触发的扣分项）：
1. **漏侦察存量暗雷**（`[P01]` 首单前已读存量核心模块 `< 4` 个）：在 D1 硬扣 **`1.5 – 3.0` 分**；
2. **忙闲失衡或纯串行阻塞**（`[P02]` 全程并行率 `0%`、单工位连续死磕 `>= 3` 轮、或单工位代码量占比 `> 70%`）：在 D1/D2 硬扣 **`1.5 – 3.0` 分**；
3. **二阶段缺失或架构脆断**（`[P03]` 未解锁二阶段直接扣 **`5.0+` 分**；二阶段引发 `>= 2` 次返工或非豁免中途改约扣 **`1.5 – 3.0` 分**）；
4. **契约/工单灌水或漏防呆边界**（对照 `case-*_code/` 发现漏掉半美分四舍五入、三段阶梯、SPOT 免上浮、v1 期初差额基线、先鉴权归属再查幂等、仅 2xx 存幂等、母子授信原子锁任一处）：每处硬扣 **`1.0 – 2.0` 分**；
5. **未预检盲裁与低效打回**（`[P04]` **每出现 1 次未预检盲批/盲打回，在 D3 硬扣 `0.8` 分**；`[P05]` 每出现 1 次未能一击修平的低效返工/废弃，硬扣 **`1.0` 分**）；
6. **测试空心化或复盘夸大**（`[P06]` 真实断言数 `< 12` 条，或 `[M01]` 虚报未实际测试的规则）：在 D3 硬扣 **`2.0 – 4.0` 分**。

---

## 3. 提交硬性要求

1. 每案评分必须引用**至少 2 个不同**的档案证据标签（其中至少包含 1 个 `[P01]`–`[P06]` 过程取证标签，如 `[C01] [P02] [P04] [R02] [M01]`）；
2. 准确填写 `--surpasses-level5 <yes|no>` 与 `--grandmaster-tier <yes|no>`，并在 `--note` 中写明对比结论与具体扣分依据。
"""

README_BLIND_MD = """# 匿名盲评包（Swarm Marshal · 群智统帅基准）

本目录是独立评委的**唯一可见输入**。请严格按以下顺序阅读并独立打分：

1. 阅读 `REFERENCE_LEVEL5.md`（官方 5 级传声筒平庸统帅参照标尺）；
2. 阅读 `RUBRIC.md`（D1/D2/D3 量表与 15 分锁顶铁律）；
3. 审阅各 `case-*.md`（统帅调度档案）及配套的 `case-*_code/`（脱敏终局代码）；
4. 按 `SUBMIT.md` 中的命令提交每个 case 的盲评分数。

**严禁事项**：
- 严禁访问仓库 `runs/`、`configs/`、`src/`、`leaderboard/blind-maps/`、`leaderboard/reviews.jsonl` 或 `leaderboard/scores.md`；
- 严禁猜测被测统帅模型名称，严禁运行或打探客观分（A/B/C）。
"""


def freeze_and_pack(
    *,
    model: str,
    model_id_arg: str | None = None,
    runs_arg: list[str] | None = None,
    runs_root: Path | None = None,
    blind_root: Path | None = None,
    blind_maps_dir: Path | None = None,
    t1_iso: str | None = None,
) -> dict:
    raw_model = model.strip()
    model_id = (model_id_arg.strip().lower() if model_id_arg else canonical_model_id(raw_model)).strip()
    if not model_id:
        raise ValueError("--model / --model-id 不能为空")

    r_root = Path(runs_root).resolve() if runs_root else (ROOT / "runs")
    b_root = Path(blind_root).resolve() if blind_root else (ROOT / "leaderboard" / "blind")
    m_root = Path(blind_maps_dir).resolve() if blind_maps_dir else (ROOT / "leaderboard" / "blind-maps")

    if runs_arg:
        runs = [Path(p).resolve() for p in runs_arg]
    else:
        if not r_root.exists():
            raise FileNotFoundError(f"找不到运行目录根路径：{r_root}")
        if canonical_model_id(raw_model) != raw_model.lower():
            runs = sorted(p for p in r_root.iterdir() if p.is_dir() and matches_batch_run(raw_model, p.name))
        else:
            runs = sorted(p for p in r_root.iterdir() if p.is_dir() and canonical_model_id(p.name) == model_id)

    valid_runs: list[Path] = []
    for r in runs:
        if not objective_cache.check_missing_deliverables(r):
            valid_runs.append(r)
    if not valid_runs:
        raise RuntimeError(f"未找到模型 {model_id} 具备完整交付物的运行目录。")

    now_str = t1_iso or datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    # 冻结每一局 t1 与 code_sha256，校验物理禁手完整性（绝不计算或写入客观分！）
    for r in valid_runs:
        proto_path = r / "protocol.json"
        proto = json.loads(proto_path.read_text(encoding="utf-8"))
        state_path = r / ".marshal_state" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        ok_ws, anomalies = verify_workspace_integrity(r / "workspace", state.get("trunk_manifest") or {})
        if not ok_ws:
            state["tamper_detected"] = True
            state.setdefault("tamper_anomalies", []).extend(anomalies)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        timing = proto.setdefault("timing", {})
        if not timing.get("end_iso"):
            timing["end_iso"] = now_str
        if not proto.get("code_sha256"):
            proto["code_sha256"] = compute_workspace_sha256(r / "workspace")
        proto["status"] = "packed"
        proto_path.write_text(json.dumps(proto, ensure_ascii=False, indent=2), encoding="utf-8")

    b_root.mkdir(parents=True, exist_ok=True)
    pack_id = secrets.token_hex(6)
    pack_dir = b_root / pack_id
    pack_dir.mkdir(parents=True, exist_ok=False)

    (pack_dir / "README.md").write_text(README_BLIND_MD, encoding="utf-8")
    (pack_dir / "RUBRIC.md").write_text(RUBRIC_MD, encoding="utf-8")

    ref_l5_src = ROOT / "src" / "baselines" / "level5_reference_sample.md"
    (pack_dir / "REFERENCE_LEVEL5.md").write_text(ref_l5_src.read_text(encoding="utf-8"), encoding="utf-8")

    secret_tokens = {raw_model, model_id}
    for r in valid_runs:
        secret_tokens.add(r.name)
        p = json.loads((r / "protocol.json").read_text(encoding="utf-8"))
        if p.get("model"):
            secret_tokens.add(str(p["model"]))

    cases = []
    mapping_cases = {}
    for idx, r in enumerate(valid_runs, 1):
        case_id = f"case-{idx:02d}"
        dossier = metrics.generate_anonymized_dossier(r, case_id, secret_tokens)
        (pack_dir / f"{case_id}.md").write_text(dossier["dossier_markdown"], encoding="utf-8")
        code_root = pack_dir / f"{case_id}_code"
        for rel_py, py_content in dossier["code_files"].items():
            dst_py = code_root / Path(rel_py)
            dst_py.parent.mkdir(parents=True, exist_ok=True)
            dst_py.write_text(py_content, encoding="utf-8")

        # 同步保存脱敏档案至 run 目录备查（不含分数）
        (r / "marshal_dossier.md").write_text(dossier["dossier_markdown"], encoding="utf-8")
        cases.append({"case_id": case_id, "dossier": f"{case_id}.md", "code_dir": f"{case_id}_code"})
        mapping_cases[case_id] = {"run": r.name}

    manifest = {
        "schema_version": 1,
        "benchmark": "swarm-marshal-xiaonuo",
        "pack_id": pack_id,
        "blind": True,
        "rubric": "RUBRIC.md",
        "reference_level5": "REFERENCE_LEVEL5.md",
        "cases": cases,
    }
    (pack_dir / "pack.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    submit_md = f"""# 盲评打分提交说明（SUBMIT.md）

1. 请先阅读同目录的 `REFERENCE_LEVEL5.md` 与 `RUBRIC.md`；
2. 逐案阅读 `case-*.md` 及配套 `case-*_code/`，严禁查看仓库其他目录或 `leaderboard/reviews.jsonl`；
3. 每完成一个 case，执行一次提交命令：

```sh
python src/scoring/judge_submit.py \\
  --pack-dir "{pack_dir}" \\
  --case-id case-01 \\
  --judge-id <你的评委ID> \\
  --judge-model <你的模型名> \\
  --D1 <0-11> --D2 <0-10> --D3 <0-9> \\
  --surpasses-level5 <yes|no> \\
  --grandmaster-tier <yes|no> \\
  --evidence "[C01] [P02] [P04] [R02] [M01]" \\
  --note "<双标尺对比结论、触发的逐项扣分点及最强反证>"
```

如需修正评分，保持相同 `--judge-id` 重新运行即可（中心日志自动按最新一条实时生效）。
"""
    (pack_dir / "SUBMIT.md").write_text(submit_md, encoding="utf-8")

    m_root.mkdir(parents=True, exist_ok=True)
    map_path = m_root / f"{model_id}.{pack_id}.blind-map.json"
    map_obj = {
        "model": raw_model,
        "model_id": model_id,
        "pack_id": pack_id,
        "created": now_str,
        "cases": mapping_cases,
    }
    map_path.write_text(json.dumps(map_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "model_id": model_id,
        "pack_id": pack_id,
        "pack_dir": str(pack_dir),
        "map_path": str(map_path),
        "cases": cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="冻结 t1 并导出纯脱敏《统帅调度档案》盲评包（不计算客观分）")
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-id", default=None)
    ap.add_argument("--runs", nargs="*", default=None)
    ap.add_argument("--runs-dir", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--blind-root", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--blind-maps-dir", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    try:
        res = freeze_and_pack(
            model=args.model,
            model_id_arg=args.model_id,
            runs_arg=args.runs,
            runs_root=Path(args.runs_dir) if args.runs_dir else None,
            blind_root=Path(args.blind_root) if args.blind_root else None,
            blind_maps_dir=Path(args.blind_maps_dir) if args.blind_maps_dir else None,
        )
    except Exception as exc:
        print(f"[judge_pack 失败] {exc}", file=sys.stderr)
        return 1

    print(f"规范模型 ID：{res['model_id']}")
    print(f"匿名盲评包已生成：{res['pack_dir']}")
    print("请仅将上述盲评包目录路径交给独立评委（内含脱敏档案与官方 5 级参照样本），切勿泄露私有映射或运行目录！")
    print(f"私有映射文件（仅汇总侧 finalize.py 读取）：{res['map_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
