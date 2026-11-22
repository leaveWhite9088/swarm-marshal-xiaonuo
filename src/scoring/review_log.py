# -*- coding: utf-8 -*-
"""中心化追加式盲评日志管理（review_log.py，对齐 silicon-rider-xiaonuo）。"""
from __future__ import annotations

from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import statistics
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_LOG = Path(os.environ.get("SM_REVIEW_LOG") or (ROOT / "leaderboard" / "reviews.jsonl"))
SCHEMA_VERSION = 1
REQUIRED = (
    "schema_version",
    "review_id",
    "created_at",
    "judge_id",
    "judge_model",
    "pack_id",
    "case_id",
    "D1",
    "D2",
    "D3",
)
D_RANGES = {"D1": (0, 11), "D2": (0, 10), "D3": (0, 9)}


class ReviewLogError(Exception):
    pass


class DuplicateReviewError(ReviewLogError):
    pass


def validate_review(rec: dict) -> bool:
    if not isinstance(rec, dict):
        raise ReviewLogError("评审记录必须是 JSON object")
    missing = [k for k in REQUIRED if k not in rec]
    if missing:
        raise ReviewLogError("评审记录缺少字段: " + ", ".join(missing))
    if int(rec.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ReviewLogError(f"schema_version 必须是 {SCHEMA_VERSION}")
    for k in ("review_id", "created_at", "judge_id", "judge_model", "pack_id", "case_id"):
        if not isinstance(rec.get(k), str) or not rec[k].strip():
            raise ReviewLogError(f"{k} 必须是非空字符串")
    for k, (lo, hi) in D_RANGES.items():
        v = rec.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
            raise ReviewLogError(f"{k} 必须在 [{lo}, {hi}] 范围内")
    return True


@contextmanager
def _file_lock(log_path: Path, timeout: float = 15.0):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    deadline = time.time() + timeout
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.time() > deadline:
                raise ReviewLogError(f"评审日志被锁定超时：{lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def read_reviews(log_path=None) -> list[dict]:
    path = Path(log_path or DEFAULT_LOG)
    if not path.exists():
        return []
    out: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception as exc:
            raise ReviewLogError(f"{path}:{lineno}: 不是合法 JSON：{exc}")
        validate_review(rec)
        out.append(rec)
    return out


def _parse_ts(ts_str: str) -> float | None:
    if not ts_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _is_newer_or_same(curr: dict, curr_idx: int, prev: dict, prev_idx: int) -> bool:
    t_curr = _parse_ts(str(curr.get("created_at", "")))
    t_prev = _parse_ts(str(prev.get("created_at", "")))
    if t_curr is not None and t_prev is not None and t_curr != t_prev:
        return t_curr > t_prev
    return curr_idx >= prev_idx


def dedupe_latest(reviews: list[dict]) -> tuple[list[dict], int]:
    by_key: dict[tuple[str, str, str], tuple[dict, int]] = {}
    for idx, rec in enumerate(reviews):
        key = (rec["pack_id"], rec["case_id"], rec["judge_id"])
        prev_pair = by_key.get(key)
        if prev_pair is None or _is_newer_or_same(rec, idx, prev_pair[0], prev_pair[1]):
            by_key[key] = (rec, idx)
    return [pair[0] for pair in by_key.values()], len(reviews) - len(by_key)


def aggregate_reviews(reviews: list[dict], mode: str = "mean") -> list[dict]:
    if mode not in ("mean", "median"):
        raise ReviewLogError("mode 只能是 mean 或 median")
    groups: dict[tuple[str, str], list[dict]] = {}
    for rec in reviews:
        groups.setdefault((rec["pack_id"], rec["case_id"]), []).append(rec)
    out: list[dict] = []
    for (pack_id, case_id), items in sorted(groups.items()):
        dims = {}
        for dim in ("D1", "D2", "D3"):
            vals = [float(x[dim]) for x in items]
            dims[dim] = round(statistics.mean(vals) if mode == "mean" else statistics.median(vals), 2)
        out.append(
            {
                "pack_id": pack_id,
                "case_id": case_id,
                "judge_count": len(items),
                "D1": dims["D1"],
                "D2": dims["D2"],
                "D3": dims["D3"],
                "D": round(dims["D1"] + dims["D2"] + dims["D3"], 2),
                "review_ids": [x.get("review_id") for x in items],
                "judges": [
                    {
                        "judge_id": x.get("judge_id"),
                        "judge_model": x.get("judge_model"),
                        "created_at": x.get("created_at"),
                        "D1": x.get("D1"),
                        "D2": x.get("D2"),
                        "D3": x.get("D3"),
                        "surpasses_level5": x.get("surpasses_level5"),
                    }
                    for x in items
                ],
            }
        )
    return out


def append_review(log_path, record: dict, reject_duplicate: bool = False, return_prior_count: bool = False):
    path = Path(log_path or DEFAULT_LOG)
    rec = dict(record)
    rec.setdefault("schema_version", SCHEMA_VERSION)
    rec.setdefault("review_id", str(uuid.uuid4()))
    rec.setdefault("created_at", datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    rec["D1"] = float(rec["D1"])
    rec["D2"] = float(rec["D2"])
    rec["D3"] = float(rec["D3"])
    rec["total"] = round(rec["D1"] + rec["D2"] + rec["D3"], 2)
    validate_review(rec)
    with _file_lock(path):
        existing = read_reviews(path)
        key = (rec["pack_id"], rec["case_id"], rec["judge_id"])
        prior_count = sum(1 for x in existing if (x["pack_id"], x["case_id"], x["judge_id"]) == key)
        if reject_duplicate and prior_count > 0:
            raise DuplicateReviewError(
                f"该评委已提交过 pack={rec['pack_id']} case={rec['case_id']}；如需覆盖重评请去掉 --reject-duplicate。"
            )
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    if return_prior_count:
        return rec, prior_count
    return rec
