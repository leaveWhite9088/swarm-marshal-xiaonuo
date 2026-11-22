#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路隔离、物理禁手与盲评脱敏防篡改审计工具（tools/isolation_audit.py）。

核查六项核心纪律：
1. draft/ 临时草稿目录是否已彻底清空；
2. 评测环境承诺 env_commit 与题目契约承诺 task_commit 是否一致；
3. trace.jsonl 链式哈希是否完整无删改；
4. 主干沙盒 workspace/ 是否存在绕过 review_submission(APPROVE) 的非授权直写，以及打包冻结 t1 后 code_sha256 是否被改写；
5. 选手阶段未打包前是否违规预生成 评分.json，已定稿客观分是否混入盲评字段或摘要失配；
6. 匿名盲评包 leaderboard/blind/<pack_id>/ 是否含官方 5 级参照样本，且彻底抹除模型真名、墙钟时间与客观分数。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import verify_workspace_integrity  # noqa: E402
from src.marshal.state_store import verify_trace_chain  # noqa: E402
from src.scoring import objective_cache  # noqa: E402
from tools.start_run import compute_env_commit, compute_task_commit, compute_workspace_sha256  # noqa: E402

FORBIDDEN_BLIND_FILES = {"评分.json", "protocol.json", "trace.jsonl", "state.json", "token_usage.json"}
FORBIDDEN_BLIND_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}
FORBIDDEN_SCORE_KEYWORDS = (
    "A_outcome",
    "B_efficiency",
    "C_quality",
    "total_objective_70",
    "objective_digest",
    "endToEndMin",
)


def audit_isolation(
    *,
    runs_dir: Path | None = None,
    blind_dir: Path | None = None,
    blind_maps_dir: Path | None = None,
    draft_dir: Path | None = None,
    objective_cache_dir: Path | None = None,
) -> dict:
    runs_dir = (runs_dir or (ROOT / "runs")).resolve()
    blind_dir = (blind_dir or (ROOT / "leaderboard" / "blind")).resolve()
    blind_maps_dir = (blind_maps_dir or (ROOT / "leaderboard" / "blind-maps")).resolve()
    draft_dir = (draft_dir or (ROOT / "draft")).resolve()
    objective_cache_dir = (objective_cache_dir or (blind_dir.parent / "objective-cache")).resolve()

    issues: list[str] = []
    cur_env = compute_env_commit(ROOT)
    cur_task = compute_task_commit(ROOT)

    # 1. draft/ 清空检查
    if draft_dir.exists():
        leftovers = [p.relative_to(draft_dir).as_posix() for p in draft_dir.rglob("*") if p.is_file()]
        if leftovers:
            issues.append(f"draft/ 目录未清空，残留文件: {leftovers[:5]}")

    # 2. 逐个 run 核查
    checked_runs = 0
    if runs_dir.exists():
        for rdir in sorted(runs_dir.iterdir()):
            proto_path = rdir / "protocol.json"
            if not rdir.is_dir() or not proto_path.is_file():
                continue
            checked_runs += 1
            proto = json.loads(proto_path.read_text(encoding="utf-8"))
            is_packed = (proto.get("status") == "packed") or bool((proto.get("timing") or {}).get("end_iso"))

            if not is_packed and proto.get("env_commit") and proto["env_commit"] != cur_env:
                issues.append(f"运行 {rdir.name}（进行中）的 env_commit 与当前仓库评分脚本哈希不一致")
            if proto.get("task_commit") and proto["task_commit"] != cur_task:
                issues.append(f"运行 {rdir.name} 的 task_commit 与当前题目契约不一致")

            ok_tr, msg_tr, _ = verify_trace_chain(rdir / "trace.jsonl")
            if not ok_tr:
                issues.append(f"运行 {rdir.name} 轨迹链校验失败: {msg_tr}")

            state_file = rdir / ".marshal_state" / "state.json"
            if state_file.is_file():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if state.get("tamper_detected"):
                    issues.append(f"运行 {rdir.name} 存在已记录的物理禁手篡改标记: {state.get('tamper_anomalies')}")
                ok_ws, ws_anom = verify_workspace_integrity(rdir / "workspace", state.get("trunk_manifest") or {})
                if not ok_ws:
                    issues.append(f"运行 {rdir.name} 主干 workspace 与调度账本不一致: {ws_anom}")

            if proto.get("code_sha256") and (rdir / "workspace").is_dir():
                live_sha = compute_workspace_sha256(rdir / "workspace")
                if live_sha != proto["code_sha256"]:
                    issues.append(f"运行 {rdir.name} 打包冻结 t1 后沙盒源码哈希被篡改")

            score_file = rdir / "评分.json"
            if not is_packed and score_file.exists():
                issues.append(f"运行 {rdir.name} 尚未 judge_pack 冻结却已存在 评分.json（严禁选手阶段私算官方得分）")
            elif is_packed and score_file.exists():
                sc_obj = json.loads(score_file.read_text(encoding="utf-8"))
                ok_sc, err_sc = objective_cache.verify_objective_record(sc_obj, proto=proto)
                if not ok_sc:
                    issues.append(f"运行 {rdir.name}/评分.json 校验失败: {err_sc}")

            cfile = objective_cache_dir / f"{rdir.name}.json"
            if cfile.is_file():
                if not is_packed:
                    issues.append(f"运行 {rdir.name} 尚未打包却已存在客观分缓存 {cfile.name}")
                else:
                    cobj = json.loads(cfile.read_text(encoding="utf-8"))
                    ok_c, err_c = objective_cache.verify_objective_record((cobj or {}).get("scores"), proto=proto)
                    if not ok_c:
                        issues.append(f"客观分缓存 {cfile.name} 校验失败: {err_c}")

    # 3. 盲评包脱敏与 5 级参照样本完备性检查
    checked_packs = 0
    map_by_pack: dict[str, dict] = {}
    if blind_maps_dir.exists():
        for mf in blind_maps_dir.glob("*.blind-map.json"):
            try:
                bm = json.loads(mf.read_text(encoding="utf-8"))
                if bm.get("pack_id"):
                    map_by_pack[bm["pack_id"]] = bm
            except Exception:
                continue

    if blind_dir.exists():
        for pdir in sorted(blind_dir.iterdir()):
            if not pdir.is_dir():
                continue
            files = [f for f in pdir.rglob("*") if f.is_file() and f.name != ".gitkeep"]
            if not files:
                continue
            checked_packs += 1
            pid = pdir.name
            if not (pdir / "REFERENCE_LEVEL5.md").is_file():
                issues.append(f"盲评包 {pid} 缺少法定的官方 5 级参照样本 REFERENCE_LEVEL5.md")
            bm = map_by_pack.get(pid) or {}
            secrets_to_check = set()
            for tok in (bm.get("model"), bm.get("model_id")):
                if tok and len(str(tok).strip()) >= 3:
                    secrets_to_check.add(str(tok).strip())
            for cm in (bm.get("cases") or {}).values():
                rn = (cm or {}).get("run")
                if rn and len(str(rn).strip()) >= 3:
                    secrets_to_check.add(str(rn).strip())

            for f in files:
                if f.name in FORBIDDEN_BLIND_FILES or f.suffix.lower() in FORBIDDEN_BLIND_SUFFIXES:
                    issues.append(f"盲评包 {pid} 混入违禁文件: {f.relative_to(pdir).as_posix()}")
                    continue
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for kw in FORBIDDEN_SCORE_KEYWORDS:
                    if kw in txt:
                        issues.append(f"盲评包 {pid}/{f.name} 疑似泄露客观分字段: {kw}")
                for st in secrets_to_check:
                    if re.search(re.escape(st), txt, flags=re.IGNORECASE):
                        issues.append(f"盲评包 {pid}/{f.relative_to(pdir).as_posix()} 泄露被测统帅模型标识: {st}")

    return {
        "ok": len(issues) == 0,
        "checked_runs": checked_runs,
        "checked_blind_packs": checked_packs,
        "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="执行全链路物理禁手、环境承诺与盲评脱敏审计")
    ap.add_argument("--runs-dir", default=None)
    ap.add_argument("--blind-dir", default=None)
    ap.add_argument("--blind-maps-dir", default=None)
    ap.add_argument("--draft-dir", default=None)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = audit_isolation(
        runs_dir=Path(args.runs_dir) if args.runs_dir else None,
        blind_dir=Path(args.blind_dir) if args.blind_dir else None,
        blind_maps_dir=Path(args.blind_maps_dir) if args.blind_maps_dir else None,
        draft_dir=Path(args.draft_dir) if args.draft_dir else None,
    )
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        if rep["ok"]:
            print(f"[ISOLATION OK] 已核查 {rep['checked_runs']} 个运行目录、{rep['checked_blind_packs']} 个盲评包，零违规、零泄密。")
        else:
            print(f"[ISOLATION VIOLATION] 发现 {len(rep['issues'])} 项隔离违规：")
            for it in rep["issues"]:
                print(f"  - {it}")
    return 0 if rep["ok"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
