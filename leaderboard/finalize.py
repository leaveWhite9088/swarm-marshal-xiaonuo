#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维护者一键出榜入口（leaderboard/finalize.py）。

用法：
    python leaderboard/finalize.py                 # 核验/复用防篡改客观分缓存 + 实时合并最新盲评合成总榜
    python leaderboard/finalize.py --recompute     # 强制忽略缓存，重新跑全量三波黑盒集成压测
    python leaderboard/finalize.py --model Demo    # 仅刷新单个规范模型并重排大榜
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

cmd = [
    sys.executable,
    str(ROOT / "src" / "scoring" / "finalize.py"),
    "--review-log",
    str(ROOT / "leaderboard" / "reviews.jsonl"),
    "--d-aggregate",
    "mean",
    "--min-judges",
    "1",
    *sys.argv[1:],
]
raise SystemExit(subprocess.run(cmd, cwd=str(ROOT)).returncode)
