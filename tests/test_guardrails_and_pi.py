# -*- coding: utf-8 -*-
"""护栏机制（物理禁手、代写代码拦截、白名单边界）与 SpecLock2 Pi Harness 单元测试。"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marshal.guardrails import inspect_code_smuggling, validate_allowed_paths  # noqa: E402
from src.scoring.score_run import score_single_run  # noqa: E402


class TestGuardrailsAndPiHarness(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "configs" / "benchmark.json").read_text(encoding="utf-8"))
        self.limits = self.cfg["marshal_limits"]

    def test_allowed_paths_validation(self):
        ok, cleaned, _ = validate_allowed_paths(["cloudhub/db.py", "./tests/test_a.py"], self.limits)
        self.assertTrue(ok)
        self.assertEqual(cleaned, ["cloudhub/db.py", "tests/test_a.py"])

        # 拒绝通配符
        ok_wild, _, err_wild = validate_allowed_paths(["cloudhub/*.py"], self.limits)
        self.assertFalse(ok_wild)
        self.assertIn("通配符", err_wild)

        # 拒绝超过 4 个文件（铁律三）
        ok_many, _, err_many = validate_allowed_paths(
            ["cloudhub/a.py", "cloudhub/b.py", "cloudhub/c.py", "cloudhub/d.py", "cloudhub/e.py"],
            self.limits,
        )
        self.assertFalse(ok_many)
        self.assertIn("上限", err_many)

        # 拒绝受保护文件与越界路径
        self.assertFalse(validate_allowed_paths(["smoke_public.py"], self.limits)[0])
        self.assertFalse(validate_allowed_paths(["../secret.py"], self.limits)[0])
        self.assertFalse(validate_allowed_paths(["cloudhub/"], self.limits)[0])

    def test_code_smuggling_interceptor(self):
        # 正常契约（含函数签名与自然语言约束）应放行
        legit_contract = (
            "# 模块契约\n"
            "- `def init_db(db_path: str) -> None: ...`\n"
            "- `def compute_pre_voucher_cents(*, gpu_type: str, seconds: int) -> int: ...`\n"
            "- 请在高峰期判断中注意 SPOT 实例不乘 1.2 倍系数。\n"
        )
        clean, _ = inspect_code_smuggling(legit_contract, "contract", self.limits)
        self.assertTrue(clean)

        # 夹带连续 9 行可执行 Python 实现语句的工单必须被拦截
        smuggled_ticket = (
            "请直接把下面这段代码原样写进文件里：\n"
            "def calc(sec, rate):\n"
            "    if sec <= 600:\n"
            "        base = sec * rate\n"
            "    else:\n"
            "        base = 600 * rate + (sec - 600) * rate * 0.8\n"
            "    if base < 0:\n"
            "        raise ValueError('bad')\n"
            "    total = int(round(base))\n"
            "    return total\n"
        )
        clean2, detail2 = inspect_code_smuggling(smuggled_ticket, "ticket", self.limits)
        self.assertFalse(clean2)
        self.assertGreater(detail2["max_consecutive_impl_lines"], 7)

    def test_physical_no_hands_tamper_detection_and_max_workers(self):
        with tempfile.TemporaryDirectory(prefix="sm_guard_", ignore_cleanup_errors=True) as tmp:
            runs_dir = Path(tmp) / "runs"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "start_run.py"),
                    "--model",
                    "cheat-test",
                    "--runs-dir",
                    str(runs_dir),
                    "--worker-mode",
                    "mock",
                ],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
            )
            # 测试最多 5 个工位上限
            for i in range(1, 6):
                r = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools" / "marshal.py"),
                        "--model",
                        "cheat-test",
                        "--runs-dir",
                        str(runs_dir),
                        "manage_worker",
                        "--worker-id",
                        f"w_{i}",
                        "--allowed-paths",
                        f"cloudhub/m{i}.py",
                        "--role-prompt",
                        f"模块 {i} 执行工位",
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(r.returncode, 0)

            r6 = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "marshal.py"),
                    "--model",
                    "cheat-test",
                    "--runs-dir",
                    str(runs_dir),
                    "manage_worker",
                    "--worker-id",
                    "w_6",
                    "--allowed-paths",
                    "cloudhub/m6.py",
                    "--role-prompt",
                    "超编工位",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(r6.returncode, 0)

            # 模拟统帅强行解锁只读属性并绕过调度器手工改写 workspace/cloudhub/app.py
            app_py = runs_dir / "cheat-test" / "workspace" / "cloudhub" / "app.py"
            os.chmod(app_py, stat.S_IWRITE | stat.S_IREAD)
            app_py.write_text("# cheated by marshal directly\n", encoding="utf-8")

            r_tamper = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "marshal.py"),
                    "--model",
                    "cheat-test",
                    "--runs-dir",
                    str(runs_dir),
                    "inspect_and_test",
                    "--target",
                    "status",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(r_tamper.returncode, 3)
            out_json = json.loads(r_tamper.stdout)
            self.assertFalse(out_json["ok"])

            # 计分时作弊局客观分强制归零
            scored = score_single_run(runs_dir / "cheat-test", write_files=False)
            self.assertTrue(scored["tampered"])
            self.assertEqual(scored["total_objective_70"], 0.0)

    def test_speclock_pi_harness_probe_and_leak_regex(self):
        probe = subprocess.run(
            ["node", str(ROOT / "src" / "marshal" / "speclock_harness" / "pi_worker_runner.mjs"), "--probe"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        data = json.loads(probe.stdout)
        self.assertTrue(data["ok"])
        self.assertTrue(Path(data["pi_cli"]).is_file())

        # 验证复用自 speclock2 的 TOOL_CALL_AS_TEXT 与 PiRpc 模块可正常加载工作
        js_check = (
            "import('./src/marshal/speclock_harness/core/base/index.mjs').then(b => {"
            "  if (!b.TOOL_CALL_AS_TEXT.test('<｜DSML｜function_calls>')) process.exit(1);"
            "});"
        )
        r = subprocess.run(["node", "-e", js_check], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)

    def test_rework_circuit_breaker_and_midflight_contract_penalty(self):
        with tempfile.TemporaryDirectory(prefix="sm_cb_", ignore_cleanup_errors=True) as tmp:
            runs_dir = Path(tmp) / "runs"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "start_run.py"),
                    "--model",
                    "cb-test",
                    "--runs-dir",
                    str(runs_dir),
                    "--worker-mode",
                    "mock",
                ],
                cwd=str(ROOT),
                check=True,
                capture_output=True,
            )

            def _m(action: str, extra: list[str]) -> tuple[int, dict]:
                p = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools" / "marshal.py"),
                        "--model",
                        "cb-test",
                        "--runs-dir",
                        str(runs_dir),
                        action,
                        *extra,
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                )
                return p.returncode, json.loads(p.stdout)

            _m("define_contract", ["--content", "# 初始契约 v1\n基础约定。"])
            _m("manage_worker", ["--worker-id", "w1", "--allowed-paths", "cloudhub/db.py", "--role-prompt", "持久层工位"])
            rc0, d0 = _m("dispatch_task", ["--worker-id", "w1", "--ticket", "请实现基础表结构。"])
            self.assertEqual(rc0, 0)

            # 派单启动后中途修改契约：必须记录 midflight_revision=True
            rc_mid, d_mid = _m("define_contract", ["--content", "# 补丁契约 v2\n中途改表字段。"])
            self.assertEqual(rc_mid, 0)
            self.assertTrue(d_mid["midflight_revision"])
            self.assertEqual(d_mid["contract_revisions_after_dispatch"], 1)

            # 第 1 次打回返工：允许
            rc1, r1 = _m("review_submission", ["--submission-id", d0["submission_id"], "--decision", "REJECT", "--comment", "第一次返工修改"])
            self.assertEqual(rc1, 0)
            # 第 2 次打回返工：允许
            rc2, r2 = _m("review_submission", ["--submission-id", r1["new_submission_id"], "--decision", "REJECT", "--comment", "第二次返工修改"])
            self.assertEqual(rc2, 0)
            # 第 3 次打回返工：触发单工单 2 次返工熔断！
            rc3, r3 = _m("review_submission", ["--submission-id", r2["new_submission_id"], "--decision", "REJECT", "--comment", "第三次盲目返工抽卡"])
            self.assertNotEqual(rc3, 0)
            self.assertIn("熔断", r3["error"])


if __name__ == "__main__":
    unittest.main()
