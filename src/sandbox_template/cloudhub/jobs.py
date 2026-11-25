# -*- coding: utf-8 -*-
"""CloudHub v1.5 legacy job state machine & scheduler (`cloudhub/jobs.py`)."""
from __future__ import annotations

import time
import uuid
from .billing import ServiceError, estimate_cost_cents
from .db import TIER_DEFAULTS, db_session, get_conn, record_ledger


def _job_dict(row) -> dict:
    return {
        "job_id": row["job_id"],
        "tenant_id": row["tenant_id"],
        "status": row["status"],
        "gpu_type": row["gpu_type"],
        "priority": row["priority"],
        "estimated_gpu_sec": int(row["estimated_gpu_sec"]),
        "actual_gpu_sec": int(row["actual_gpu_sec"]) if row["actual_gpu_sec"] is not None else None,
        "reserved_cents": int(row["reserved_cents"]),
        "final_charge_cents": int(row["final_charge_cents"]) if row["final_charge_cents"] is not None else None,
        "penalty_cents": int(row["penalty_cents"] or 0),
        "voucher_code": row["voucher_code"],
        "retry_count": int(row["retry_count"]),
        "max_retries": int(row["max_retries"]),
        "worker_node_id": row["worker_node_id"],
        "lease_until_epoch": int(row["lease_until_epoch"]) if row["lease_until_epoch"] is not None else None,
        "version": int(row["version"]),
    }


def submit_job(db_path: str, tenant_id: str, payload: dict) -> dict:
    gpu_type = str(payload.get("gpu_type") or "")
    priority = str(payload.get("priority") or "")
    est_sec = payload.get("estimated_gpu_sec")
    sub_ep = payload.get("submitted_epoch")
    ttl_sec = int(payload.get("ttl_seconds", 600))
    max_ret = int(payload.get("max_retries", 1))
    job_id = str(payload.get("job_id") or uuid.uuid4().hex[:12]).strip()
    v_code = payload.get("voucher_code")

    if est_sec is None or sub_ep is None or int(est_sec) <= 0 or int(est_sec) > 86400 or ttl_sec < 1 or not (0 <= max_ret <= 5):
        raise ServiceError(400, "INVALID_ARGUMENT", "Invalid job parameters")

    # Legacy race bug: checks balance, concurrency & quota on a separate read connection BEFORE write session!
    rconn = get_conn(db_path)
    try:
        t_row = rconn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        if not t_row:
            raise ServiceError(404, "NOT_FOUND", "Tenant not found")
        act = rconn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(estimated_gpu_sec), 0) AS r_sec FROM jobs WHERE tenant_id = ? AND status IN ('QUEUED', 'RUNNING')",
            (tenant_id,),
        ).fetchone()
    finally:
        rconn.close()

    tier = t_row["tier"]
    max_conc = TIER_DEFAULTS[tier]["max_concurrent"]
    if int(act["cnt"]) >= max_conc:
        raise ServiceError(409, "CONCURRENCY_LIMIT_EXCEEDED", "Too many active jobs")
    if int(t_row["used_gpu_sec"]) + int(act["r_sec"]) + int(est_sec) > int(t_row["quota_gpu_sec"]):
        raise ServiceError(409, "QUOTA_EXCEEDED", "Monthly GPU quota exceeded")

    pre_cents = estimate_cost_cents(gpu_type, int(est_sec), tier, priority, int(sub_ep))
    disc_cents = 0

    with db_session(db_path) as conn:
        dup = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if dup:
            raise ServiceError(409, "ALREADY_EXISTS", "job_id already exists")

        if v_code is not None:
            v = conn.execute("SELECT * FROM vouchers WHERE code = ?", (v_code,)).fetchone()
            if not v:
                raise ServiceError(404, "NOT_FOUND", "Voucher not found")
            if v["tenant_id"] != tenant_id:
                raise ServiceError(403, "TENANT_FORBIDDEN", "Voucher belongs to another tenant")
            if int(v["redeemed"]) != 0 or v["bound_job_id"] is not None:
                raise ServiceError(409, "VOUCHER_ALREADY_USED", "Voucher already used")
            if int(sub_ep) > int(v["expires_at_epoch"]):
                raise ServiceError(400, "VOUCHER_EXPIRED", "Voucher expired")
            if pre_cents < int(v["min_spend_cents"]):
                raise ServiceError(400, "VOUCHER_MIN_SPEND_NOT_MET", "Minimum spend not met")
            disc_cents = min(int(v["discount_cents"]), pre_cents)
            conn.execute("UPDATE vouchers SET bound_job_id = ? WHERE code = ?", (job_id, v_code))

        reserved = pre_cents - disc_cents
        cur_bal = int(t_row["balance_cents"])
        if cur_bal < reserved:
            raise ServiceError(402, "INSUFFICIENT_BALANCE", "Insufficient available balance")

        record_ledger(conn, tenant_id, job_id, "RESERVE", -reserved, reserved, int(sub_ep))
        ttl_until = int(sub_ep) + ttl_sec
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, tenant_id, status, gpu_type, priority, estimated_gpu_sec,
                pre_voucher_cents, discount_cents, reserved_cents, voucher_code,
                submitted_epoch, ttl_until_epoch, max_retries, retry_count, version
            ) VALUES (?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                job_id,
                tenant_id,
                gpu_type,
                priority,
                int(est_sec),
                pre_cents,
                disc_cents,
                reserved,
                v_code,
                int(sub_ep),
                ttl_until,
                max_ret,
            ),
        )

    return {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "status": "QUEUED",
        "gpu_type": gpu_type,
        "priority": priority,
        "estimated_gpu_sec": int(est_sec),
        "reserved_cents": reserved,
        "pre_voucher_cents": pre_cents,
        "discount_cents": disc_cents,
        "submitted_epoch": int(sub_ep),
        "ttl_until_epoch": ttl_until,
        "max_retries": max_ret,
        "retry_count": 0,
    }


def get_job(db_path: str, tenant_id: str, job_id: str) -> dict:
    with db_session(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise ServiceError(404, "NOT_FOUND", "Job not found")
        if row["tenant_id"] != tenant_id:
            raise ServiceError(403, "TENANT_FORBIDDEN", "Forbidden job access")
        return _job_dict(row)


def claim_job(db_path: str, payload: dict) -> dict:
    node_id = str(payload.get("worker_node_id") or "").strip()
    now_ep = payload.get("now_epoch")
    lease_sec = int(payload.get("lease_seconds", 60))
    if not node_id or now_ep is None or lease_sec < 1:
        raise ServiceError(400, "INVALID_ARGUMENT", "Invalid claim payload")

    with db_session(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'QUEUED' AND ttl_until_epoch >= ?
            ORDER BY
                CASE priority WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END ASC,
                submitted_epoch ASC,
                job_id ASC
            LIMIT 1
            """,
            (int(now_ep),),
        ).fetchone()
        if not row:
            return {"job": None}
        lease_until = int(now_ep) + lease_sec
        conn.execute(
            """
            UPDATE jobs
            SET status = 'RUNNING', worker_node_id = ?, lease_until_epoch = ?, version = version + 1
            WHERE job_id = ?
            """,
            (node_id, lease_until, row["job_id"]),
        )
        updated = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
        return {"job": _job_dict(updated)}


def settle_job(db_path: str, tenant_id: str, job_id: str, payload: dict) -> dict:
    actual_sec = payload.get("actual_gpu_sec")
    now_ep = int(payload.get("now_epoch") or time.time())
    if actual_sec is None or int(actual_sec) < 1:
        raise ServiceError(400, "INVALID_ARGUMENT", "actual_gpu_sec must be >= 1")

    with db_session(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not job:
            raise ServiceError(404, "NOT_FOUND", "Job not found")
        if job["tenant_id"] != tenant_id:
            raise ServiceError(403, "TENANT_FORBIDDEN", "Forbidden")
        if job["status"] != "RUNNING":
            raise ServiceError(409, "INVALID_STATE_TRANSITION", f"Cannot settle from {job['status']}")

        t_row = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        est_sec = int(job["estimated_gpu_sec"])
        billable_sec = min(int(actual_sec), int(est_sec * 1.2))
        actual_pre = estimate_cost_cents(job["gpu_type"], billable_sec, t_row["tier"], job["priority"], int(job["submitted_epoch"]))

        # Legacy bug: blindly re-applies stored discount_cents without re-checking voucher min_spend_cents!
        disc = min(int(job["discount_cents"]), actual_pre)
        final_charge = actual_pre - disc
        reserved = int(job["reserved_cents"])

        refunded = 0
        extra = 0
        if final_charge <= reserved:
            refunded = reserved - final_charge
            record_ledger(conn, tenant_id, job_id, "SETTLE", 0, -final_charge, now_ep)
            if refunded > 0:
                record_ledger(conn, tenant_id, job_id, "REFUND_UNUSED", refunded, -refunded, now_ep)
        else:
            extra = final_charge - reserved
            if int(t_row["balance_cents"]) < extra:
                raise ServiceError(402, "INSUFFICIENT_BALANCE_FOR_OVERRUN", "Insufficient balance for overrun")
            record_ledger(conn, tenant_id, job_id, "SETTLE", -extra, -reserved, now_ep)

        if job["voucher_code"]:
            conn.execute("UPDATE vouchers SET redeemed = 1 WHERE code = ?", (job["voucher_code"],))

        conn.execute("UPDATE tenants SET used_gpu_sec = used_gpu_sec + ? WHERE tenant_id = ?", (int(actual_sec), tenant_id))
        conn.execute(
            """
            UPDATE jobs
            SET status = 'SUCCEEDED', actual_gpu_sec = ?, billable_gpu_sec = ?, final_charge_cents = ?, version = version + 1
            WHERE job_id = ?
            """,
            (int(actual_sec), billable_sec, final_charge, job_id),
        )
        after_t = conn.execute("SELECT balance_cents, frozen_cents FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "actual_gpu_sec": int(actual_sec),
            "billable_gpu_sec": billable_sec,
            "reserved_cents": reserved,
            "final_charge_cents": final_charge,
            "refunded_cents": refunded,
            "extra_charged_cents": extra,
            "balance_cents": int(after_t["balance_cents"]),
            "frozen_cents": int(after_t["frozen_cents"]),
        }


def fail_job(db_path: str, tenant_id: str, job_id: str, payload: dict) -> dict:
    reason = str(payload.get("reason") or "worker_error")
    now_ep = int(payload.get("now_epoch") or time.time())
    with db_session(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not job:
            raise ServiceError(404, "NOT_FOUND", "Job not found")
        if job["tenant_id"] != tenant_id:
            raise ServiceError(403, "TENANT_FORBIDDEN", "Forbidden")
        if job["status"] != "RUNNING":
            raise ServiceError(409, "INVALID_STATE_TRANSITION", "Only RUNNING jobs can fail")
        reserved = int(job["reserved_cents"])
        bal, frz = record_ledger(conn, tenant_id, job_id, "CANCEL_RELEASE", reserved, -reserved, now_ep)
        if job["voucher_code"]:
            conn.execute("UPDATE vouchers SET bound_job_id = NULL, redeemed = 0 WHERE code = ?", (job["voucher_code"],))
        conn.execute(
            "UPDATE jobs SET status = 'FAILED', failure_reason = ?, version = version + 1 WHERE job_id = ?",
            (reason, job_id),
        )
        return {
            "job_id": job_id,
            "status": "FAILED",
            "refunded_cents": reserved,
            "balance_cents": bal,
            "frozen_cents": frz,
        }


def cancel_job(db_path: str, tenant_id: str, job_id: str, payload: dict) -> dict:
    now_ep = int(payload.get("now_epoch") or time.time())
    with db_session(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not job:
            raise ServiceError(404, "NOT_FOUND", "Job not found")
        if job["tenant_id"] != tenant_id:
            raise ServiceError(403, "TENANT_FORBIDDEN", "Forbidden")
        if job["status"] not in ("QUEUED", "RUNNING"):
            raise ServiceError(409, "INVALID_STATE_TRANSITION", "Job already terminal")

        reserved = int(job["reserved_cents"])
        if job["status"] == "QUEUED":
            penalty = 0
            refund = reserved
            bal, frz = record_ledger(conn, tenant_id, job_id, "CANCEL_RELEASE", refund, -refund, now_ep)
            if job["voucher_code"]:
                conn.execute("UPDATE vouchers SET bound_job_id = NULL, redeemed = 0 WHERE code = ?", (job["voucher_code"],))
        else:
            # Legacy bug: uses round(), forgets CANCEL_PENALTY ledger (leaving frozen_cents stranded!), and leaks voucher!
            penalty = int(round(reserved * 0.10))
            refund = reserved - penalty
            bal, frz = record_ledger(conn, tenant_id, job_id, "CANCEL_RELEASE", refund, -refund, now_ep)

        conn.execute(
            "UPDATE jobs SET status = 'CANCELLED', penalty_cents = ?, version = version + 1 WHERE job_id = ?",
            (penalty, job_id),
        )
        return {
            "job_id": job_id,
            "status": "CANCELLED",
            "penalty_cents": penalty,
            "refunded_cents": refund,
            "balance_cents": bal,
            "frozen_cents": frz,
        }


def reap_jobs(db_path: str, payload: dict) -> dict:
    now_ep = payload.get("now_epoch")
    if now_ep is None:
        raise ServiceError(400, "INVALID_ARGUMENT", "now_epoch required")
    now_ep = int(now_ep)
    exp_cnt = 0
    req_cnt = 0
    fail_cnt = 0

    with db_session(db_path) as conn:
        q_rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'QUEUED' AND ttl_until_epoch < ?",
            (now_ep,),
        ).fetchall()
        for r in q_rows:
            res = int(r["reserved_cents"])
            record_ledger(conn, r["tenant_id"], r["job_id"], "CANCEL_RELEASE", res, -res, now_ep)
            # Legacy bug: forgets to release voucher on QUEUED -> EXPIRED!
            conn.execute("UPDATE jobs SET status = 'EXPIRED', version = version + 1 WHERE job_id = ?", (r["job_id"],))
            exp_cnt += 1

        r_rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'RUNNING' AND lease_until_epoch < ?",
            (now_ep,),
        ).fetchall()
        for r in r_rows:
            res = int(r["reserved_cents"])
            if int(r["retry_count"]) < int(r["max_retries"]):
                # Legacy bug: mistakenly refunds escrow on requeue!
                record_ledger(conn, r["tenant_id"], r["job_id"], "CANCEL_RELEASE", res, -res, now_ep)
                new_ttl = max(int(r["ttl_until_epoch"]), now_ep + 300)
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'QUEUED', retry_count = retry_count + 1,
                        worker_node_id = NULL, lease_until_epoch = NULL, ttl_until_epoch = ?, version = version + 1
                    WHERE job_id = ?
                    """,
                    (new_ttl, r["job_id"]),
                )
                req_cnt += 1
            else:
                record_ledger(conn, r["tenant_id"], r["job_id"], "CANCEL_RELEASE", res, -res, now_ep)
                if r["voucher_code"]:
                    conn.execute("UPDATE vouchers SET bound_job_id = NULL, redeemed = 0 WHERE code = ?", (r["voucher_code"],))
                conn.execute("UPDATE jobs SET status = 'FAILED', version = version + 1 WHERE job_id = ?", (r["job_id"],))
                fail_cnt += 1

    return {"expired_count": exp_cnt, "requeued_count": req_cnt, "failed_count": fail_cnt}
