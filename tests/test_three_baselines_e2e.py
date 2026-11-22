# -*- coding: utf-8 -*-
"""三套统帅基线端到端全链路自检测试（test_three_baselines_e2e.py，必做核心验收！）。

完整覆盖：
1. 甩手掌柜废片基线（1 工人、无契约、原题直转、盲批） -> 验证客观分 < 20 / 70；
2. 官方 5 级传声筒平庸统帅基线（3 工人、粗略契约、抄原需求、敷衍打回） -> 验证客观分落在 35 – 45 / 70；
3. 9 级王牌统帅参考基线（契约先行、5 正交工位、精细拆单、毒辣审查拦截修平） -> 验证三波压测全绿（A = 30/30）且客观分 >= 65 / 70；
4. 全流程闭环：judge_pack 导出匿名档案包 + isolation_audit 零泄密核验 + judge_submit 盲评提交 + finalize 一键出榜与双指纹缓存命中。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.baselines.baseline_ace_l9 import run_ace_l9_baseline  # noqa: E402
from src.baselines.baseline_lazy import run_lazy_baseline  # noqa: E402
from src.baselines.baseline_mediocre_l5 import run_mediocre_l5_baseline  # noqa: E402
from src.scoring.judge_pack import freeze_and_pack  # noqa: E402
from tools.isolation_audit import audit_isolation  # noqa: E402


class TestThreeBaselinesEndToEnd(unittest.TestCase):
    def test_full_lifecycle_and_three_tier_score_gradient(self):
        with tempfile.TemporaryDirectory(prefix="sm_e2e_all_", ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            runs_dir = base / "runs"
            blind_dir = base / "leaderboard" / "blind"
            maps_dir = base / "leaderboard" / "blind-maps"
            verdicts_dir = base / "leaderboard" / "verdicts"
            cache_dir = base / "leaderboard" / "objective-cache"
            review_log_path = base / "leaderboard" / "reviews.jsonl"
            scores_md = base / "leaderboard" / "scores.md"
            draft_dir = base / "draft"

            # 1. 运行三套统帅基线
            run_lazy_baseline(runs_dir, model_name="marshal-lazy-s1")
            run_mediocre_l5_baseline(runs_dir, model_name="marshal-mediocre-s1")
            run_ace_l9_baseline(runs_dir, model_name="marshal-ace-s1")

            # 2. 分别执行收尾打包冻结 t1（模拟真实合理耗时：lazy 2 分钟，mediocre 10 分钟，ace 5 分钟）
            pack_lazy = freeze_and_pack(
                model="marshal-lazy",
                runs_root=runs_dir,
                blind_root=blind_dir,
                blind_maps_dir=maps_dir,
                t1_iso="2026-11-22T10:02:00+08:00",
            )
            pack_med = freeze_and_pack(
                model="marshal-mediocre",
                runs_root=runs_dir,
                blind_root=blind_dir,
                blind_maps_dir=maps_dir,
                t1_iso="2026-11-22T11:14:00+08:00",
            )
            pack_ace = freeze_and_pack(
                model="marshal-ace",
                runs_root=runs_dir,
                blind_root=blind_dir,
                blind_maps_dir=maps_dir,
                t1_iso="2026-11-22T12:05:00+08:00",
            )

            # 核验打包阶段绝未提前生成 评分.json，且盲评包内必含官方 5 级参照样本
            for rname in ("marshal-lazy-s1", "marshal-mediocre-s1", "marshal-ace-s1"):
                self.assertFalse((runs_dir / rname / "评分.json").exists())
            for pk in (pack_lazy, pack_med, pack_ace):
                self.assertTrue((Path(pk["pack_dir"]) / "REFERENCE_LEVEL5.md").is_file())
                self.assertTrue((Path(pk["pack_dir"]) / "case-01.md").is_file())

            # 3. 打包后执行严格防篡改与零泄密审计
            iso_pre = audit_isolation(
                runs_dir=runs_dir,
                blind_dir=blind_dir,
                blind_maps_dir=maps_dir,
                draft_dir=draft_dir,
                objective_cache_dir=cache_dir,
            )
            self.assertTrue(iso_pre["ok"], f"Isolation issues before finalize: {iso_pre['issues']}")

            # 4. 独立评委提交盲评分数
            judge_inputs = [
                (pack_lazy["pack_dir"], "2.0", "1.5", "1.0", "no", "[C01] [W01] [T01] [R01]", "无全局契约、单工位原题直转盲批，远低于5级样本"),
                (pack_med["pack_dir"], "5.5", "5.0", "4.5", "no", "[C01] [W01] [T01] [R02]", "契约笼统、粗放3工位、打回批注空洞，等同5级参照样本锁顶15分"),
                (pack_ace["pack_dir"], "10.5", "9.5", "8.5", "yes", "[C01] [W02] [R02] [R04] [M01]", "契约先行严密，5工位正交，精准审出SPOT与退券漏洞；反证：极端磁盘满异常未单列"),
            ]
            for pdir, d1, d2, d3, surp, evid, note in judge_inputs:
                sub_res = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "src" / "scoring" / "judge_submit.py"),
                        "--pack-dir",
                        pdir,
                        "--case-id",
                        "case-01",
                        "--judge-id",
                        "judge_alpha",
                        "--judge-model",
                        "independent-judge-v1",
                        "--D1",
                        d1,
                        "--D2",
                        d2,
                        "--D3",
                        d3,
                        "--surpasses-level5",
                        surp,
                        "--evidence",
                        evid,
                        "--note",
                        note,
                        "--review-log",
                        str(review_log_path),
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(sub_res.returncode, 0, f"judge_submit failed: {sub_res.stderr}")

            # 5. 一键运行 finalize.py 执行终局三波集成压测 + 客观分计算 + 合成总榜
            fin1 = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "scoring" / "finalize.py"),
                    "--runs-dir",
                    str(runs_dir),
                    "--blind-maps-dir",
                    str(maps_dir),
                    "--verdicts-dir",
                    str(verdicts_dir),
                    "--objective-cache-dir",
                    str(cache_dir),
                    "--leaderboard",
                    str(scores_md),
                    "--review-log",
                    str(review_log_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(fin1.returncode, 0, f"finalize failed:\n{fin1.stdout}\n{fin1.stderr}")

            v_lazy = json.loads((verdicts_dir / "marshal-lazy.json").read_text(encoding="utf-8"))
            v_med = json.loads((verdicts_dir / "marshal-mediocre.json").read_text(encoding="utf-8"))
            v_ace = json.loads((verdicts_dir / "marshal-ace.json").read_text(encoding="utf-8"))

            obj_lazy = v_lazy["mean"]["objective_70"]
            obj_med = v_med["mean"]["objective_70"]
            obj_ace = v_ace["mean"]["objective_70"]

            print(
                f"\n[三基线实测客观分梯度] 甩手掌柜={obj_lazy:.2f}/70 | 5级传声筒={obj_med:.2f}/70 | 9级王牌统帅={obj_ace:.2f}/70"
            )

            # 验证核心设计梯度断言！
            self.assertLess(obj_lazy, 20.0, f"甩手掌柜基线客观分须 < 20/70，实测 {obj_lazy}")
            self.assertGreaterEqual(obj_med, 35.0, f"5 级平庸基线客观分须 >= 35/70，实测 {obj_med}")
            self.assertLessEqual(obj_med, 45.0, f"5 级平庸基线客观分须 <= 45/70，实测 {obj_med}")
            self.assertEqual(v_ace["mean"]["A"], 30.0, f"9 级王牌统帅三波黑盒压测须全绿满分 30.0，实测 {v_ace['runs'][0]}")
            self.assertGreaterEqual(obj_ace, 65.0, f"9 级王牌统帅客观分须 >= 65/70，实测 {obj_ace}")

            # 6. 再次运行 finalize.py 验证客观分防篡改双指纹缓存命中（无需重跑三波压测）
            fin2 = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "scoring" / "finalize.py"),
                    "--runs-dir",
                    str(runs_dir),
                    "--blind-maps-dir",
                    str(maps_dir),
                    "--verdicts-dir",
                    str(verdicts_dir),
                    "--objective-cache-dir",
                    str(cache_dir),
                    "--leaderboard",
                    str(scores_md),
                    "--review-log",
                    str(review_log_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(fin2.returncode, 0)
            v_ace_cached = json.loads((verdicts_dir / "marshal-ace.json").read_text(encoding="utf-8"))
            self.assertEqual(v_ace_cached["summary"]["objective_cache_hits"], 1)
            self.assertEqual(v_ace_cached["summary"]["objective_recomputed"], 0)

            # 7. 出榜后再跑一次全链路隔离防篡改审计
            iso_post = audit_isolation(
                runs_dir=runs_dir,
                blind_dir=blind_dir,
                blind_maps_dir=maps_dir,
                draft_dir=draft_dir,
                objective_cache_dir=cache_dir,
            )
            self.assertTrue(iso_post["ok"], f"Isolation issues after finalize: {iso_post['issues']}")

            board_txt = scores_md.read_text(encoding="utf-8")
            self.assertLess(board_txt.index("marshal-ace"), board_txt.index("marshal-mediocre"))
            self.assertLess(board_txt.index("marshal-mediocre"), board_txt.index("marshal-lazy"))


if __name__ == "__main__":
    unittest.main()
