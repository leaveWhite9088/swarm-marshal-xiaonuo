# -*- coding: utf-8 -*-
"""基线统帅执行辅助器：严格通过 start_run 与 tools/marshal.py 五大入口驱动沙盒（runner.py）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class MarshalBaselineClient:
    def __init__(self, model: str, runs_dir: Path, t0_iso: str):
        self.model = model
        self.runs_dir = Path(runs_dir).resolve()
        self.t0_iso = t0_iso
        self.run_dir = self.runs_dir / model

    def start(self) -> None:
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "start_run.py"),
            "--model",
            self.model,
            "--runs-dir",
            str(self.runs_dir),
            "--t0",
            self.t0_iso,
            "--worker-mode",
            "mock",
            "--force",
        ]
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            raise RuntimeError(f"start_run failed: {res.stderr}\n{res.stdout}")

    def act(self, action: str, extra_args: list[str], stdin_text: str | None = None) -> dict:
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "marshal.py"),
            "--model",
            self.model,
            "--runs-dir",
            str(self.runs_dir),
            action,
            *extra_args,
        ]
        res = subprocess.run(
            cmd,
            cwd=str(ROOT),
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        try:
            parsed = json.loads(res.stdout)
        except Exception as exc:
            raise RuntimeError(f"marshal.py non-JSON output ({res.returncode}): {res.stdout}\n{res.stderr}") from exc
        if res.returncode != 0 or not parsed.get("ok"):
            raise RuntimeError(f"marshal.py {action} failed: {parsed}")
        return parsed

    def write_retrospective(self, content: str) -> None:
        retro = self.run_dir / "briefing" / "RETROSPECTIVE.md"
        retro.write_text(content.strip() + "\n", encoding="utf-8")
