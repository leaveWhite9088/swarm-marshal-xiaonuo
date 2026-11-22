# -*- coding: utf-8 -*-
"""模型身份归一、盲评铁律五锁顶、时间重叠审计与隔离脱敏审计单元测试。"""
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

from src.scoring.model_identity import canonical_model_id  # noqa: E402
from tools.isolation_audit import audit_isolation  # noqa: E402
from tools.timing_audit import build_report, collect  # noqa: E402


class TestScoringAndAuditUnits(unittest.TestCase):
    def test_canonical_model_id(self):
        self.assertEqual(canonical_model_id("gemini-4-carbon-s1"), "gemini-4-carbon")
        self.assertEqual(canonical_model_id("deepseek-r1"), "deepseek-r1")
        self.assertEqual(canonical_model_id("kimi-k3.1-pro（Max）-h2"), "kimi-k3.1-pro（max）")
        self.assertEqual(canonical_model_id("qwen-v2-run3"), "qwen-v2")

    def test_judge_submit_level5_cap_and_realtime_dedupe(self):
        with tempfile.TemporaryDirectory(prefix="sm_judge_") as tmp:
            pdir = Path(tmp) / "pack1"
            pdir.mkdir()
            (pdir / "pack.json").write_text(
                json.dumps({"pack_id": "aa11bb22cc33", "cases": [{"case_id": "case-01"}]}),
                encoding="utf-8",
            )
            rlog = Path(tmp) / "reviews.jsonl"

            # 铁律五测试：勾选 --surpasses-level5 no 但给 18 分（>15）必须拦截失败
            bad = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "scoring" / "judge_submit.py"),
                    "--pack-dir",
                    str(pdir),
                    "--case-id",
                    "case-01",
                    "--judge-id",
                    "j1",
                    "--judge-model",
                    "judge-model",
                    "--D1",
                    "7",
                    "--D2",
                    "6",
                    "--D3",
                    "5",
                    "--surpasses-level5",
                    "no",
                    "--evidence",
                    "[C01] [W01]",
                    "--note",
                    "未超越5级传声筒样本却尝试给高分",
                    "--review-log",
                    str(rlog),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(bad.returncode, 0)

            # 合法提交 14 分（<= 15）通过
            ok1 = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "scoring" / "judge_submit.py"),
                    "--pack-dir",
                    str(pdir),
                    "--case-id",
                    "case-01",
                    "--judge-id",
                    "j1",
                    "--judge-model",
                    "judge-model",
                    "--D1",
                    "5",
                    "--D2",
                    "5",
                    "--D3",
                    "4",
                    "--surpasses-level5",
                    "no",
                    "--evidence",
                    "[C01] [W01]",
                    "--note",
                    "典型5级传声筒样本水平，锁顶在15分以内",
                    "--review-log",
                    str(rlog),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(ok1.returncode, 0)

            # 同一评委重评为 26 分（surpasses-level5 yes）应覆盖生效
            ok2 = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "scoring" / "judge_submit.py"),
                    "--pack-dir",
                    str(pdir),
                    "--case-id",
                    "case-01",
                    "--judge-id",
                    "j1",
                    "--judge-model",
                    "judge-model",
                    "--D1",
                    "10",
                    "--D2",
                    "9",
                    "--D3",
                    "7",
                    "--surpasses-level5",
                    "yes",
                    "--evidence",
                    "[C01] [W02] [R01]",
                    "--note",
                    "复核后确认远超5级样本，但测试覆盖可再加并发场景",
                    "--review-log",
                    str(rlog),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(ok2.returncode, 0)

            merged = subprocess.run(
                [sys.executable, str(ROOT / "src" / "scoring" / "review_merge.py"), "--log", str(rlog)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=True,
            )
            mdata = json.loads(merged.stdout)
            self.assertEqual(mdata["superseded_duplicates"], 1)
            self.assertEqual(mdata["cases"][0]["D"], 26.0)

    def test_timing_and_isolation_audits(self):
        with tempfile.TemporaryDirectory(prefix="sm_audit_") as tmp:
            base = Path(tmp)
            runs = base / "runs"
            for name, s_iso, e_iso in [
                ("demo-model-s1", "2026-11-22T10:00:00+08:00", "2026-11-22T10:10:00+08:00"),
                ("demo-model-s2", "2026-11-22T10:05:00+08:00", "2026-11-22T10:15:00+08:00"),
            ]:
                rd = runs / name
                rd.mkdir(parents=True)
                (rd / "protocol.json").write_text(
                    json.dumps({"timing": {"t0_iso": s_iso, "process_iso": s_iso, "end_iso": e_iso}}),
                    encoding="utf-8",
                )
            rep = build_report(collect(runs))
            self.assertFalse(rep["ok"])
            self.assertEqual(rep["overlapRuns"], 2)

            # 测试 draft/ 未清空被 isolation_audit 捕获
            draft = base / "draft"
            draft.mkdir()
            (draft / "leftover.md").write_text("temp", encoding="utf-8")
            iso = audit_isolation(
                runs_dir=base / "empty_runs",
                blind_dir=base / "blind",
                blind_maps_dir=base / "maps",
                draft_dir=draft,
            )
            self.assertFalse(iso["ok"])
            self.assertTrue(any("draft/" in x for x in iso["issues"]))


if __name__ == "__main__":
    unittest.main()
