# -*- coding: utf-8 -*-
"""CloudHub v1.5 legacy billing, voucher & audit module (`cloudhub/billing.py`)."""
from __future__ import annotations

import time
from .db import TIER_DEFAULTS, db_session, record_ledger

GPU_BASE_CENTS = {
    "A10": 2,
    "L40": 5,
    "H100": 12,
}

PRIORITY_MULT = {
    "NORMAL": 1.0,
    "HIGH": 1.25,
    "SPOT": 0.70,
}


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def estimate_cost_cents(
    gpu_type: str,
    seconds: int,
    tier: str,
    priority: str,
    submitted_epoch: int,
) -> int:
    """Legacy cost estimator (contains float rounding, flat duration & peak bugs)."""
    if gpu_type not in GPU_BASE_CENTS:
        raise ServiceError(400, "INVALID_ARGUMENT", f"Unsupported gpu_type: {gpu_type}")
    if priority not in PRIORITY_MULT:
        raise ServiceError(400, "INVALID_ARGUMENT", f"Unsupported priority: {priority}")
    if tier not in TIER_DEFAULTS:
        raise ServiceError(400, "INVALID_ARGUMENT", f"Unsupported tier: {tier}")
    if int(seconds) < 0:
        raise ServiceError(400, "INVALID_ARGUMENT", "seconds must be non-negative")

    unit = GPU_BASE_CENTS[gpu_type]
    base = float(seconds) * float(unit)
    tier_disc = TIER_DEFAULTS[tier]["discount"]
    prio_mult = PRIORITY_MULT[priority]

    utc_hour = (int(submitted_epoch) // 3600) % 24
    peak_mult = 1.2 if (9 <= utc_hour < 21) else 1.0

    # Legacy uses Python float round() (banker's rounding + binary float drift)
    return int(round(base * tier_disc * prio_mult * peak_mult))


def create_tenant(db_path: str, payload: dict) -> dict:
    tid = str(payload.get("tenant_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    tier = str(payload.get("tier") or "").strip()
    secret = str(payload.get("api_secret") or "").strip()
    now_ep = int(payload.get("now_epoch") or time.time())
    if not tid or not name or not secret or tier not in TIER_DEFAULTS:
        raise ServiceError(400, "INVALID_ARGUMENT", "Invalid tenant fields")

    quota = TIER_DEFAULTS[tier]["quota_gpu_sec"]
    with db_session(db_path) as conn:
        exists = conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
        if exists:
            raise ServiceError(409, "ALREADY_EXISTS", f"Tenant {tid} already exists")
        conn.execute(
            """
            INSERT INTO tenants (tenant_id, name, tier, balance_cents, frozen_cents, quota_gpu_sec, used_gpu_sec, api_secret, created_at)
            VALUES (?, ?, ?, 0, 0, ?, 0, ?, ?)
            """,
            (tid, name, tier, quota, secret, now_ep),
        )
    return {
        "tenant_id": tid,
        "name": name,
        "tier": tier,
        "balance_cents": 0,
        "frozen_cents": 0,
        "quota_gpu_sec": quota,
        "used_gpu_sec": 0,
    }


def create_voucher(db_path: str, payload: dict) -> dict:
    code = str(payload.get("code") or "").strip()
    tid = str(payload.get("tenant_id") or "").strip()
    disc = payload.get("discount_cents")
    min_sp = payload.get("min_spend_cents", 0)
    exp = payload.get("expires_at_epoch")
    if not code or not tid or disc is None or exp is None or int(disc) <= 0 or int(min_sp) < 0:
        raise ServiceError(400, "INVALID_ARGUMENT", "Invalid voucher arguments")

    with db_session(db_path) as conn:
        t_row = conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
        if not t_row:
            raise ServiceError(404, "NOT_FOUND", "Target tenant not found")
        v_row = conn.execute("SELECT 1 FROM vouchers WHERE code = ?", (code,)).fetchone()
        if v_row:
            raise ServiceError(409, "ALREADY_EXISTS", "Voucher code already exists")
        conn.execute(
            """
            INSERT INTO vouchers (code, tenant_id, discount_cents, min_spend_cents, expires_at_epoch, redeemed, bound_job_id)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
            """,
            (code, tid, int(disc), int(min_sp), int(exp)),
        )
    return {
        "code": code,
        "tenant_id": tid,
        "discount_cents": int(disc),
        "min_spend_cents": int(min_sp),
        "expires_at_epoch": int(exp),
        "redeemed": False,
    }


def topup_tenant(db_path: str, tenant_id: str, payload: dict) -> dict:
    amt = payload.get("amount_cents")
    if amt is None or int(amt) <= 0:
        raise ServiceError(400, "INVALID_ARGUMENT", "amount_cents must be > 0")
    now_ep = int(payload.get("now_epoch") or time.time())

    with db_session(db_path) as conn:
        t_row = conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        if not t_row:
            raise ServiceError(404, "NOT_FOUND", "Tenant not found")
        bal, frz = record_ledger(conn, tenant_id, None, "TOPUP", int(amt), 0, now_ep)
    return {
        "tenant_id": tenant_id,
        "balance_cents": bal,
        "frozen_cents": frz,
    }


def get_statement(db_path: str, tenant_id: str) -> dict:
    with db_session(db_path) as conn:
        t = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
        if not t:
            raise ServiceError(404, "NOT_FOUND", "Tenant not found")
        act = conn.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(estimated_gpu_sec), 0) AS r_sec
            FROM jobs WHERE tenant_id = ? AND status IN ('QUEUED', 'RUNNING')
            """,
            (tenant_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM ledger_entries WHERE tenant_id = ? ORDER BY entry_id ASC",
            (tenant_id,),
        ).fetchall()
        ledger = [dict(r) for r in rows]
        topup_sum = sum(r["delta_available"] for r in ledger if r["entry_type"] == "TOPUP")
        # Legacy bug: forgets CANCEL_PENALTY in total_charged_cents
        charged_sum = sum(
            -(r["delta_available"] + r["delta_frozen"])
            for r in ledger
            if r["entry_type"] == "SETTLE"
        )
        return {
            "tenant_id": t["tenant_id"],
            "tier": t["tier"],
            "balance_cents": int(t["balance_cents"]),
            "frozen_cents": int(t["frozen_cents"]),
            "quota_gpu_sec": int(t["quota_gpu_sec"]),
            "used_gpu_sec": int(t["used_gpu_sec"]),
            "reserved_gpu_sec": int(act["r_sec"]),
            "active_jobs": int(act["cnt"]),
            "total_topup_cents": int(topup_sum),
            "total_charged_cents": int(charged_sum),
            "ledger_count": len(ledger),
            "ledger": ledger,
        }


def get_platform_audit(db_path: str) -> dict:
    with db_session(db_path) as conn:
        tenants = conn.execute("SELECT * FROM tenants").fetchall()
        tot_avail = sum(int(t["balance_cents"]) for t in tenants)
        tot_frz = sum(int(t["frozen_cents"]) for t in tenants)
        escrow_row = conn.execute(
            "SELECT COALESCE(SUM(reserved_cents), 0) AS esc FROM jobs WHERE status IN ('QUEUED', 'RUNNING')"
        ).fetchone()
        active_escrow = int(escrow_row["esc"])

        rev_rows = conn.execute(
            "SELECT entry_type, delta_available, delta_frozen FROM ledger_entries WHERE entry_type IN ('SETTLE', 'CANCEL_PENALTY')"
        ).fetchall()
        tot_rev = 0
        for r in rev_rows:
            tot_rev += -(int(r["delta_available"]) + int(r["delta_frozen"]))

        anomalies: list[str] = []
        if tot_frz != active_escrow:
            anomalies.append(f"frozen_mismatch:{tot_frz}!={active_escrow}")

        for t in tenants:
            tid = t["tenant_id"]
            if int(t["balance_cents"]) < 0 or int(t["frozen_cents"]) < 0:
                anomalies.append(f"negative_balance:{tid}")
            sums = conn.execute(
                """
                SELECT COALESCE(SUM(delta_available), 0) AS da, COALESCE(SUM(delta_frozen), 0) AS df
                FROM ledger_entries WHERE tenant_id = ?
                """,
                (tid,),
            ).fetchone()
            # Legacy bug: fails on migrated v1 tenants whose historical balance had no matching initial TOPUP row
            if int(sums["da"]) != int(t["balance_cents"]) or int(sums["df"]) != int(t["frozen_cents"]):
                anomalies.append(f"ledger_drift:{tid}")

        return {
            "all_balanced": len(anomalies) == 0,
            "total_available_cents": tot_avail,
            "total_frozen_cents": tot_frz,
            "active_escrow_cents": active_escrow,
            "total_platform_revenue_cents": tot_rev,
            "anomalies": anomalies,
        }
