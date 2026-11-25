# -*- coding: utf-8 -*-
"""CloudHub v1.5 legacy persistence module (`cloudhub/db.py`).

遗留半成品说明：提供了基础建表、连接与简单幂等缓存，但尚未经历生产级并发压测与老库脏数据迁移演练。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time

TIER_DEFAULTS = {
    "STARTER": {"max_concurrent": 2, "quota_gpu_sec": 3600, "discount": 1.0},
    "GROWTH": {"max_concurrent": 5, "quota_gpu_sec": 18000, "discount": 0.9},
    "ENTERPRISE": {"max_concurrent": 10, "quota_gpu_sec": 108000, "discount": 0.8},
}


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def db_session(db_path: str):
    """Legacy transaction wrapper (uses default deferred sqlite transaction)."""
    conn = get_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Initialize schema if tables do not exist yet."""
    with db_session(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tier TEXT NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,
                frozen_cents INTEGER NOT NULL DEFAULT 0,
                quota_gpu_sec INTEGER NOT NULL DEFAULT 3600,
                used_gpu_sec INTEGER NOT NULL DEFAULT 0,
                api_secret TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                job_id TEXT,
                entry_type TEXT NOT NULL,
                delta_available INTEGER NOT NULL DEFAULT 0,
                delta_frozen INTEGER NOT NULL DEFAULT 0,
                balance_after INTEGER NOT NULL DEFAULT 0,
                frozen_after INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vouchers (
                code TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                discount_cents INTEGER NOT NULL,
                min_spend_cents INTEGER NOT NULL DEFAULT 0,
                expires_at_epoch INTEGER NOT NULL,
                redeemed INTEGER NOT NULL DEFAULT 0,
                bound_job_id TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                gpu_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                estimated_gpu_sec INTEGER NOT NULL,
                actual_gpu_sec INTEGER,
                billable_gpu_sec INTEGER,
                pre_voucher_cents INTEGER NOT NULL DEFAULT 0,
                discount_cents INTEGER NOT NULL DEFAULT 0,
                reserved_cents INTEGER NOT NULL DEFAULT 0,
                final_charge_cents INTEGER,
                penalty_cents INTEGER NOT NULL DEFAULT 0,
                voucher_code TEXT,
                submitted_epoch INTEGER NOT NULL,
                ttl_until_epoch INTEGER NOT NULL,
                max_retries INTEGER NOT NULL DEFAULT 1,
                retry_count INTEGER NOT NULL DEFAULT 0,
                worker_node_id TEXT,
                lease_until_epoch INTEGER,
                failure_reason TEXT,
                version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_cache (
                idem_key TEXT PRIMARY KEY,
                req_hash TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )


def record_ledger(
    conn: sqlite3.Connection,
    tenant_id: str,
    job_id: str | None,
    entry_type: str,
    delta_available: int,
    delta_frozen: int,
    now_epoch: int | None = None,
) -> tuple[int, int]:
    row = conn.execute(
        "SELECT balance_cents, frozen_cents FROM tenants WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown tenant: {tenant_id}")
    new_bal = int(row["balance_cents"]) + int(delta_available)
    new_frz = int(row["frozen_cents"]) + int(delta_frozen)
    conn.execute(
        "UPDATE tenants SET balance_cents = ?, frozen_cents = ? WHERE tenant_id = ?",
        (new_bal, new_frz, tenant_id),
    )
    conn.execute(
        """
        INSERT INTO ledger_entries
            (tenant_id, job_id, entry_type, delta_available, delta_frozen, balance_after, frozen_after, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tenant_id,
            job_id,
            entry_type,
            int(delta_available),
            int(delta_frozen),
            new_bal,
            new_frz,
            int(now_epoch if now_epoch is not None else time.time()),
        ),
    )
    return new_bal, new_frz


def get_cached_idempotency(db_path: str, idem_key: str, req_hash: str) -> tuple[str, int | None, dict | None]:
    """Legacy global lookup by idem_key only."""
    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT req_hash, status_code, response_json FROM idempotency_cache WHERE idem_key = ?",
            (idem_key,),
        ).fetchone()
        if row is None:
            return "MISS", None, None
        if row["req_hash"] != req_hash:
            return "CONFLICT", 409, {"ok": False, "error": {"code": "IDEMPOTENCY_CONFLICT", "message": "Payload mismatch"}}
        return "HIT", int(row["status_code"]), json.loads(row["response_json"])


def store_idempotency(db_path: str, idem_key: str, req_hash: str, status_code: int, resp_obj: dict) -> None:
    with db_session(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO idempotency_cache (idem_key, req_hash, status_code, response_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (idem_key, req_hash, int(status_code), json.dumps(resp_obj, ensure_ascii=False), int(time.time())),
        )
