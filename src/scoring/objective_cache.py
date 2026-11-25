# -*- coding: utf-8 -*-
"""客观分（A/B/C）双指纹防篡改缓存与核验模块（objective_cache.py）。

严格物理分离客观分与盲评 D 分：
1. scoring_hash：评分配置（configs/benchmark.json）与客观压测/计分脚本整体 SHA-256；
2. deliverables_hash：单局冻结交付物（protocol.json + trace.jsonl + state.json + 终局代码 code_sha256）整体 SHA-256；
3. attestation：签名保护客观分核心载荷，杜绝手工改分。
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from tools.start_run import compute_task_commit, compute_workspace_sha256

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

CACHE_SCHEMA_VERSION = 1

DELIVERABLE_REL_FILES = (
    "protocol.json",
    "trace.jsonl",
    ".marshal_state/state.json",
)

SCORING_FILES = (
    "configs/benchmark.json",
    "src/scoring/workload.py",
    "src/scoring/metrics.py",
    "src/scoring/score_run.py",
    "src/scoring/objective_cache.py",
)

OBJECTIVE_SIGNED_FIELDS = (
    "A1",
    "A2",
    "A3",
    "A4",
    "A_outcome",
    "B1",
    "B2",
    "B_efficiency",
    "B_detail",
    "C1",
    "C2",
    "C3",
    "C_quality",
    "C_detail",
    "total_objective_70",
    "tampered",
    "trace_verified",
    "run_summary",
    "code_sha256",
    "b2_reliable",
)

FORBIDDEN_BLIND_KEYS = frozenset({"D", "D1", "D2", "D3", "D_judged", "judges"})


def check_missing_deliverables(run_dir: Path) -> list[str]:
    rp = Path(run_dir)
    missing = [rel for rel in DELIVERABLE_REL_FILES if not (rp / Path(rel)).is_file()]
    if not (rp / "workspace").is_dir():
        missing.append("workspace/")
    return missing


def _hash_files(named_paths: list[tuple[str, Path]], extra_tag: str = "") -> str:
    h = hashlib.sha256()
    for rel, fp in named_paths:
        raw = fp.read_bytes()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(str(len(raw)).encode("ascii"))
        h.update(b"\0")
        h.update(raw)
        h.update(b"\n")
    if extra_tag:
        h.update(b"extra:")
        h.update(extra_tag.encode("utf-8"))
    return h.hexdigest()


def compute_scoring_hash(root: Path = ROOT) -> str:
    base = Path(root).resolve()
    pairs = [(rel, base / Path(rel)) for rel in sorted(SCORING_FILES)]
    return _hash_files(pairs)


def compute_deliverables_hash(run_dir: Path) -> str:
    rp = Path(run_dir).resolve()
    missing = check_missing_deliverables(rp)
    if missing:
        raise FileNotFoundError(f"运行目录 {rp.name} 缺少交付物: {', '.join(missing)}")
    ws_sha = compute_workspace_sha256(rp / "workspace")
    pairs = [(rel, rp / Path(rel)) for rel in DELIVERABLE_REL_FILES]
    return _hash_files(pairs, extra_tag=f"ws={ws_sha}")


def compute_objective_digest(scoring_hash: str, deliverables_hash: str, score_obj: dict) -> str:
    payload = {k: score_obj.get(k) for k in OBJECTIVE_SIGNED_FIELDS}
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    mat = f"sm-objective-v{CACHE_SCHEMA_VERSION}:{scoring_hash}:{deliverables_hash}:{canon}"
    return hashlib.sha256(mat.encode("utf-8")).hexdigest()


def build_objective_record(
    raw_score: dict,
    *,
    scoring_hash: str,
    deliverables_hash: str,
) -> dict:
    out = {k: v for k, v in raw_score.items() if k not in FORBIDDEN_BLIND_KEYS}
    out["cache_schema_version"] = CACHE_SCHEMA_VERSION
    out["official_finalized"] = True
    out["finalized_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    out["scoring_sha256"] = scoring_hash
    out["deliverable_sha256"] = deliverables_hash
    out["objective_digest"] = compute_objective_digest(scoring_hash, deliverables_hash, out)
    return out


def verify_objective_record(
    score_obj: dict,
    *,
    scoring_hash: str | None = None,
    deliverables_hash: str | None = None,
    proto: dict | None = None,
) -> tuple[bool, str]:
    if not isinstance(score_obj, dict):
        return False, "invalid_json"
    if FORBIDDEN_BLIND_KEYS.intersection(score_obj.keys()):
        return False, "contains_blind_fields"
    if score_obj.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return False, "schema_mismatch"
    if not score_obj.get("official_finalized"):
        return False, "not_official_finalized"

    s_hash = score_obj.get("scoring_sha256")
    d_hash = score_obj.get("deliverable_sha256")
    if not s_hash or not d_hash:
        return False, "missing_hashes"
    if scoring_hash and s_hash != scoring_hash:
        return False, "scoring_changed"
    if deliverables_hash and d_hash != deliverables_hash:
        return False, "deliverables_changed"
    if proto and proto.get("code_sha256") and score_obj.get("code_sha256") != proto.get("code_sha256"):
        return False, "code_sha256_mismatch"

    expected = compute_objective_digest(s_hash, d_hash, score_obj)
    if score_obj.get("objective_digest") != expected:
        return False, "tampered_digest"
    return True, "hit"


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "FORBIDDEN_BLIND_KEYS",
    "build_objective_record",
    "check_missing_deliverables",
    "compute_deliverables_hash",
    "compute_objective_digest",
    "compute_scoring_hash",
    "compute_task_commit",
    "verify_objective_record",
]
