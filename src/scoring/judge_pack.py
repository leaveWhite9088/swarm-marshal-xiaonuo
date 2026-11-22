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

RUBRIC_MD = """# D 层盲评量表与反高分通胀铁律（满分 30 分）

## 0. 强制前置动作：先读 `REFERENCE_LEVEL5.md`！

在给任何 `case-*.md` 打分前，**必须先完整通读同目录下的 `REFERENCE_LEVEL5.md`（《官方 5 级传声筒平庸统帅参照样本》）**！
- **反高分通胀铁律五（硬性锁顶）**：凡是在建制清晰度、契约先行完备度、工单拆解防呆颗粒度以及代码审查批注毒辣度上**未明显超越 `REFERENCE_LEVEL5.md`** 的案例，D 层三项之和**最高锁死在 `15.0 / 30`**（提交时须传 `--surpasses-level5 no`）！
- 禁止看结果猜分、禁止访问仓库其他目录或打探模型身份。

---

## 1. 三个评分维度与分档锚点

### D1 · 搭班子与立规矩：建制正交性与契约先行（0 – 11 分）
- **0 – 3 分（混乱失职）**：不写全局契约（`[C01]=0`）或派单后才补形式主义空话；只建 1 个万能工位把所有文件塞进去当传声筒，或工位白名单互相踩踏。
- **4 – 5.5 分（5 级平庸基准）**：有简短契约但只列笼统表名/路由，缺跨模块 Python 函数签名、字段类型与数值取整约定；3 个左右粗放工位，岗位提示词流于口号。
- **6 – 8.5 分（良好架构师）**：派单前确立清晰契约（覆盖主要表结构与跨模块调用接口）；4～5 个模块工位领地正交无重叠，岗位职责明确。
- **9 – 11 分（王牌技术总监）**：开局首动作即确立严密防呆的全局契约（锁死 SQLite 迁移策略、双本位流水字段、半进位整数公式、状态枚举与错误码）；4～5 个正交工位（含独立测试验收工位）白名单聚焦（每工位 1～2 个文件），岗位提示词针对弱模型短板划清边界。

### D2 · 任务降维功力：把复杂大活嚼碎成傻瓜活（0 – 10 分）
- **0 – 3 分（原题直转 / 违规代写）**：直接把总需求大段复制粘贴甩给弱模型，或企图往工单里塞现成源码触发拦截（见 `[C03]`/`[V]`）。
- **4 – 5.0 分（5 级平庸基准）**：按模块粗略概括需求，一张单混杂多组规则却不拆解步骤与易错特例，导致弱模型顾头不顾尾。
- **6 – 8.0 分（精细拆单）**：每张工单聚焦单一模块职责，明确输入输出契约与核心验收条件，弱模型一次成活率较高。
- **8.5 – 10 分（极致降维防呆）**：深谙弱模型“长文必漏边界”的短板，将 18 条耦合规则拆解为每单仅需专注 2～4 个关键点的可执行清单，主动对高危陷阱（如 `SPOT` 高峰期免乘 `1.2`、`>600s` 阶梯 `80%`、`RUNNING` 取消 `10%` 违约金与退券、v1 老库 `ALTER TABLE` 禁删表、`Idempotency-Key` 冲突 `409`）给出自然语言防呆警示，且零源码代写。

### D3 · 审查批注毒辣度、擦屁股指导力与诚实复盘（0 – 9 分）
- **0 – 2.5 分（盲批放水 / 弄虚作假）**：从不审阅暂存代码或预跑测试就盲目 `APPROVE`；或 `RETROSPECTIVE.md`（`[M01]`）公然编造轨迹中不存在的测试与审查成果。
- **3 – 4.5 分（5 级平庸基准）**：偶有检查但流于形式；打回重做（`REJECT`）只会写“报错了请检查修复”之类废话，不指明具体根因；自测仅覆盖皮毛。
- **5 – 7.0 分（有效把关）**：合入前常规执行 `staging` 审阅或冒烟/单元测试，打回批注能指出缺失功能点并推动修平，复盘基本属实。
- **7.5 – 9 分（毒辣把关与诚实复盘）**：严格执行“先审/先测再合入”纪律；面对下属工位首轮提交中的隐蔽逻辑漏洞（对照 `case-*_code/` 与 `[R..]`），能一针见血指出具体函数、错误条件分支与修正方案，一次性指导弱模型修平；驱动测试工位完成多边界回归验证，复盘与真实轨迹严丝合缝。

---

## 2. 提交硬性要求

1. 每案评分必须引用**至少 2 个不同**的档案证据标签（如 `[C01] [W02] [T03] [R02] [M01]`）；
2. `--note` 必须明确写出与 `REFERENCE_LEVEL5.md` 的对比结论及**最强扣分反证**（哪怕给高分也必须挑出至少一处不足或可改进点）。
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
  --evidence "[C01] [W01] [T01] [R01]" \\
  --note "<与5级平庸样本的对比结论及最强扣分反证>"
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
