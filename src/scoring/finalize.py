#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总出榜核心脚本（src/scoring/finalize.py）。

职责：
1. 自动扫描 leaderboard/blind-maps/（或指定 --model），定位冻结交付物；
2. 校验客观分防篡改双指纹缓存（scoring_hash + deliverables_hash + objective_digest）：
   缓存命中则直接复用，未命中或传 --recompute 则现场拉起 score_run.py 跑三波跨模块黑盒压测定稿 A/B/C；
3. 实时读取中心盲评日志 leaderboard/reviews.jsonl（绝不缓存盲评分，同一评委重评自动取最新一条）；
4. 合成总分（A30 + B15 + C25 + D30 = 100），按多局算术平均排名生成 leaderboard/scores.md 与 verdicts/<model_id>.json。
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics
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

from src.scoring import objective_cache, review_log, score_run  # noqa: E402
from src.scoring.model_identity import canonical_model_id  # noqa: E402
from tools.timing_audit import collect as collect_timing_rows  # noqa: E402

DEFAULT_LEADERBOARD = ROOT / "leaderboard" / "scores.md"
DEFAULT_VERDICTS = ROOT / "leaderboard" / "verdicts"
DEFAULT_BLIND_MAPS = ROOT / "leaderboard" / "blind-maps"
DEFAULT_OBJ_CACHE = ROOT / "leaderboard" / "objective-cache"


def _load_realtime_reviews(review_log_path: Path, mode: str) -> tuple[dict[tuple[str, str], dict], int]:
    reviews = review_log.read_reviews(review_log_path)
    if not reviews:
        return {}, 0
    used, dups = review_log.dedupe_latest(reviews)
    groups = review_log.aggregate_reviews(used, mode=mode)
    return {(g["pack_id"], g["case_id"]): g for g in groups}, dups


def finalize_single_model(
    target_model: str,
    *,
    runs_dir: Path,
    blind_maps_dir: Path,
    verdicts_dir: Path,
    objective_cache_dir: Path,
    scoring_hash: str,
    review_groups: dict[tuple[str, str], dict],
    overlap_map: dict[str, list[str]],
    recompute: bool = False,
    allow_partial: bool = False,
    min_judges: int = 1,
    d_aggregate: str = "mean",
    dry_run: bool = False,
) -> tuple[dict | None, str | None]:
    model_id = canonical_model_id(target_model)
    map_files = []
    if blind_maps_dir.exists():
        for mf in sorted(blind_maps_dir.glob("*.blind-map.json")):
            try:
                bm = json.loads(mf.read_text(encoding="utf-8"))
                if canonical_model_id(bm.get("model_id") or bm.get("model", "")) == model_id:
                    map_files.append((mf, bm))
            except Exception:
                continue

    if not map_files:
        raise RuntimeError(f"找不到模型 {model_id} 的任何盲评映射文件（请先运行 judge_pack.py）")

    run_to_pack_case: dict[str, tuple[str, str]] = {}
    for _, bm in map_files:
        pid = bm["pack_id"]
        for cid, meta in (bm.get("cases") or {}).items():
            rname = Path(meta["run"]).name
            run_to_pack_case[rname] = (pid, cid)

    run_records: list[dict] = []
    cache_hits = 0
    recomputed_cnt = 0
    total_judge_reviews = 0

    for rname in sorted(run_to_pack_case):
        rdir = runs_dir / rname
        if not rdir.is_dir():
            if allow_partial:
                continue
            raise FileNotFoundError(f"找不到映射指向的运行目录：{rdir}")

        pid, cid = run_to_pack_case[rname]
        d_grp = review_groups.get((pid, cid))
        if not d_grp or int(d_grp["judge_count"]) < min_judges:
            if allow_partial:
                print(f"!! 跳过尚未完成盲评的运行：{rname} (pack={pid}, case={cid})")
                continue
            raise RuntimeError(f"运行 {rname} (pack={pid}, case={cid}) 尚无足够评委打分（需 >= {min_judges} 位评委）")

        b2_rel = not bool(overlap_map.get(rname))
        deliv_hash = objective_cache.compute_deliverables_hash(rdir)
        proto = json.loads((rdir / "protocol.json").read_text(encoding="utf-8"))

        cached_obj = None
        score_file = rdir / "评分.json"
        if not recompute and score_file.is_file():
            try:
                cand = json.loads(score_file.read_text(encoding="utf-8"))
                ok_c, _ = objective_cache.verify_objective_record(
                    cand,
                    scoring_hash=scoring_hash,
                    deliverables_hash=deliv_hash,
                    proto=proto,
                )
                if ok_c and cand.get("b2_reliable") == b2_rel:
                    cached_obj = cand
                    cache_hits += 1
            except Exception:
                cached_obj = None

        if cached_obj is None:
            cached_obj = score_run.score_single_run(
                rdir,
                root=ROOT,
                b2_reliable=b2_rel,
                write_files=not dry_run,
                objective_cache_dir=objective_cache_dir,
            )
            recomputed_cnt += 1

        d1, d2, d3, d_tot = float(d_grp["D1"]), float(d_grp["D2"]), float(d_grp["D3"]), float(d_grp["D"])
        obj_70 = float(cached_obj["total_objective_70"])
        grand_total = 0.0 if cached_obj.get("tampered") else round(obj_70 + d_tot, 2)
        total_judge_reviews += int(d_grp["judge_count"])

        run_records.append(
            {
                "run": rname,
                "pack_id": pid,
                "case_id": cid,
                "worker_mode": cached_obj.get("worker_mode"),
                "tampered": bool(cached_obj.get("tampered")),
                "A": float(cached_obj["A_outcome"]),
                "A1": float(cached_obj["A1"]),
                "A2": float(cached_obj["A2"]),
                "A3": float(cached_obj["A3"]),
                "A4": float(cached_obj.get("A4", 0.0)),
                "B": float(cached_obj["B_efficiency"]),
                "B1": float(cached_obj["B1"]),
                "B2": float(cached_obj["B2"]),
                "C": float(cached_obj["C_quality"]),
                "C1": float(cached_obj["C1"]),
                "C2": float(cached_obj["C2"]),
                "C3": float(cached_obj["C3"]),
                "objective_70": obj_70,
                "D": 0.0 if cached_obj.get("tampered") else d_tot,
                "D1": 0.0 if cached_obj.get("tampered") else d1,
                "D2": 0.0 if cached_obj.get("tampered") else d2,
                "D3": 0.0 if cached_obj.get("tampered") else d3,
                "total": grand_total,
                "judge_count": int(d_grp["judge_count"]),
                "dispatches_used": (cached_obj.get("run_summary") or {}).get("dispatches_used", 0),
                "workers_active": (cached_obj.get("run_summary") or {}).get("workers_active", 0),
                "endToEndMin": (cached_obj.get("B_detail") or {}).get("e2e_min", 0.0),
                "b2_reliable": b2_rel,
            }
        )

    if not run_records:
        return None, None

    def _avg(key: str) -> float:
        return round(statistics.mean(r[key] for r in run_records), 2)

    mean_obj = {
        k: _avg(k)
        for k in ("total", "objective_70", "A", "A1", "A2", "A3", "A4", "B", "B1", "B2", "C", "C1", "C2", "C3", "D", "D1", "D2", "D3", "endToEndMin")
    }
    mean_obj["dispatches_used"] = round(statistics.mean(r["dispatches_used"] for r in run_records), 1)

    verdict = {
        "benchmark": "swarm-marshal-xiaonuo",
        "version": "xiaonuo-v2.0",
        "model": model_id,
        "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "d_aggregate": d_aggregate,
        "mean": mean_obj,
        "summary": {
            "runs_total": len(run_to_pack_case),
            "runs_scored": len(run_records),
            "judge_reviews": total_judge_reviews,
            "objective_cache_hits": cache_hits,
            "objective_recomputed": recomputed_cnt,
        },
        "runs": run_records,
    }

    if not dry_run:
        verdicts_dir.mkdir(parents=True, exist_ok=True)
        (verdicts_dir / f"{model_id}.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")

    sec_lines = [
        f"### 最终成绩 · {model_id} — 总分均值 **{mean_obj['total']:.2f}** / 100（客观 **{mean_obj['objective_70']:.2f}**/70 + 盲评 **{mean_obj['D']:.2f}**/30）",
        "",
        "| Run | 总分(100) | A交付(25) | B1经济并行(10) | B2耗时(10) | C1侦察演进(8) | C2实证把关(9) | C3协同均衡(8) | D1建制(11) | D2拆单(10) | D3审查(9) | 活跃工位 | 派单数 | 耗时(分) | 评委数 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in run_records:
        sec_lines.append(
            f"| `{r['run']}` | **{r['total']:.2f}** | {r['A']:.2f} ({r['A1']:.1f}/{r['A2']:.1f}/{r['A3']:.1f}/{r['A4']:.1f}) "
            f"| {r['B1']:.2f} | {r['B2']:.2f} | {r['C1']:.2f} | {r['C2']:.2f} | {r['C3']:.2f} "
            f"| {r['D1']:.2f} | {r['D2']:.2f} | {r['D3']:.2f} | {r['workers_active']} | {r['dispatches_used']} "
            f"| {r['endToEndMin']:.1f} | {r['judge_count']} |"
        )
    return verdict, "\n".join(sec_lines)


def write_leaderboard_markdown(
    leaderboard_path: Path,
    verdicts_dir: Path,
    sections_by_model: dict[str, str],
) -> None:
    board: list[dict] = []
    for vf in sorted(verdicts_dir.glob("*.json")):
        try:
            board.append(json.loads(vf.read_text(encoding="utf-8")))
        except Exception:
            continue
    board.sort(key=lambda v: (-(v.get("mean", {}).get("total") or 0.0), v.get("model", "")))

    lines = [
        "# 排行榜 · 群智统帅基准（swarm-marshal-xiaonuo · xiaonuo-v2.0）",
        "",
        "> 总分 = **A 终局系统交付（25）+ B 统筹效率与并行杠杆（20，受 A/25 封顶）+ C 五大统领过程管控（25）+ D 双标尺领导力盲评（30）**。",
        "",
        "## 大榜（按各统帅模型多局算术平均总分排名）",
        "",
        "| 排名 | 统帅模型 | 总分(100) | A交付(25) | B1经济并行(10) | B2耗时(10) | C1侦察演进(8) | C2实证把关(9) | C3协同均衡(8) | D1建制(11) | D2拆单(10) | D3审查(9) | 平均派单 | 耗时(分) | 已评Run |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for idx, v in enumerate(board, 1):
        m = v.get("mean") or {}
        s = v.get("summary") or {}
        lines.append(
            f"| {idx} | {v['model']} | **{m.get('total', 0.0):.2f}** | {m.get('A', 0.0):.2f} "
            f"| {m.get('B1', 0.0):.2f} | {m.get('B2', 0.0):.2f} | {m.get('C1', 0.0):.2f} "
            f"| {m.get('C2', 0.0):.2f} | {m.get('C3', 0.0):.2f} | {m.get('D1', 0.0):.2f} "
            f"| {m.get('D2', 0.0):.2f} | {m.get('D3', 0.0):.2f} | {m.get('dispatches_used', 0.0):.1f} "
            f"| {m.get('endToEndMin', 0.0):.1f} | {s.get('runs_scored', 0)} |"
        )

    lines.extend(["", "---", ""])
    for v in board:
        mid = v["model"]
        if mid in sections_by_model:
            lines.append(sections_by_model[mid])
            lines.append("")

    leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
    leaderboard_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="核验/复用防篡改客观分并实时合并中心盲评日志合成排行榜")
    ap.add_argument("--model", default=None, help="仅更新指定模型；省略则汇总 blind-maps 中的全部模型")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--blind-maps-dir", default=str(DEFAULT_BLIND_MAPS))
    ap.add_argument("--verdicts-dir", default=str(DEFAULT_VERDICTS))
    ap.add_argument("--objective-cache-dir", default=str(DEFAULT_OBJ_CACHE))
    ap.add_argument("--leaderboard", default=str(DEFAULT_LEADERBOARD))
    ap.add_argument("--review-log", default=str(review_log.DEFAULT_LOG))
    ap.add_argument("--d-aggregate", choices=["mean", "median"], default="mean")
    ap.add_argument("--min-judges", type=int, default=1)
    ap.add_argument("--recompute", "--force-recompute", dest="recompute", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    runs_dir = Path(args.runs_dir).resolve()
    blind_maps_dir = Path(args.blind_maps_dir).resolve()
    verdicts_dir = Path(args.verdicts_dir).resolve()
    obj_cache_dir = Path(args.objective_cache_dir).resolve()
    leaderboard_path = Path(args.leaderboard).resolve()
    review_log_path = Path(args.review_log).resolve()

    review_groups, dups = _load_realtime_reviews(review_log_path, args.d_aggregate)
    if dups:
        print(f"[信息] 评审日志含 {dups} 条历史重评覆盖记录，已实时采用最新打分。")

    timing_rows = collect_timing_rows(runs_dir)
    overlap_map = {r["name"]: r["overlaps"] for r in timing_rows}
    scoring_hash = objective_cache.compute_scoring_hash(ROOT)

    if args.model:
        target_models = [canonical_model_id(args.model)]
    else:
        discovered = set()
        if blind_maps_dir.exists():
            for mf in sorted(blind_maps_dir.glob("*.blind-map.json")):
                try:
                    bm = json.loads(mf.read_text(encoding="utf-8"))
                    mid = canonical_model_id(bm.get("model_id") or bm.get("model", ""))
                    if mid:
                        discovered.add(mid)
                except Exception:
                    continue
        target_models = sorted(discovered)

    if not target_models:
        print("没有发现待汇总的盲评映射（leaderboard/blind-maps/*.blind-map.json）。", file=sys.stderr)
        return 1

    sections: dict[str, str] = {}
    verdicts_out: list[dict] = []
    for mid in target_models:
        v, sec = finalize_single_model(
            mid,
            runs_dir=runs_dir,
            blind_maps_dir=blind_maps_dir,
            verdicts_dir=verdicts_dir,
            objective_cache_dir=obj_cache_dir,
            scoring_hash=scoring_hash,
            review_groups=review_groups,
            overlap_map=overlap_map,
            recompute=args.recompute,
            allow_partial=args.allow_partial,
            min_judges=args.min_judges,
            d_aggregate=args.d_aggregate,
            dry_run=args.dry_run,
        )
        if v is not None and sec is not None:
            verdicts_out.append(v)
            sections[v["model"]] = sec
            print(
                f"[已定稿] {v['model']}: 总分={v['mean']['total']:.2f} "
                f"(A={v['mean']['A']:.2f}, B={v['mean']['B']:.2f}, C={v['mean']['C']:.2f}, D={v['mean']['D']:.2f}) "
                f"[cache_hits={v['summary']['objective_cache_hits']}, recomputed={v['summary']['objective_recomputed']}]"
            )

    if not verdicts_out:
        print("没有任何模型满足汇总条件。", file=sys.stderr)
        return 1

    if not args.dry_run:
        # 若只更新单个模型，同时补齐已有其他模型的明细表段落
        for vf in sorted(verdicts_dir.glob("*.json")):
            v_other = json.loads(vf.read_text(encoding="utf-8"))
            omid = v_other.get("model")
            if omid and omid not in sections:
                m = v_other["mean"]
                slines = [
                    f"### 最终成绩 · {omid} — 总分均值 **{m['total']:.2f}** / 100（客观 **{m['objective_70']:.2f}**/70 + 盲评 **{m['D']:.2f}**/30）",
                    "",
                    "| Run | 总分(100) | A交付(30) | B1经济(5) | B2耗时(10) | C1正交(9) | C2把关(8) | C3拓扑(8) | D1建制(11) | D2拆单(10) | D3审查(9) | 活跃工位 | 派单数 | 耗时(分) | 评委数 |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                ]
                for r in v_other.get("runs") or []:
                    slines.append(
                        f"| `{r['run']}` | **{r['total']:.2f}** | {r['A']:.2f} ({r['A1']:.1f}/{r['A2']:.1f}/{r['A3']:.1f}) "
                        f"| {r['B1']:.2f} | {r['B2']:.2f} | {r['C1']:.2f} | {r['C2']:.2f} | {r['C3']:.2f} "
                        f"| {r['D1']:.2f} | {r['D2']:.2f} | {r['D3']:.2f} | {r['workers_active']} | {r['dispatches_used']} "
                        f"| {r['endToEndMin']:.1f} | {r['judge_count']} |"
                    )
                sections[omid] = "\n".join(slines)
        write_leaderboard_markdown(leaderboard_path, verdicts_dir, sections)
        print(f"排行榜已更新：{leaderboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
