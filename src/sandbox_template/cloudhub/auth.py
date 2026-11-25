# -*- coding: utf-8 -*-
"""CloudHub v1.5 legacy authentication helper (`cloudhub/auth.py`)."""
from __future__ import annotations

import hashlib
import hmac
import time
from .billing import ServiceError
from .db import get_conn


def check_admin(headers, expected_token: str) -> None:
    tok = headers.get("X-Admin-Token")
    if not tok:
        raise ServiceError(401, "UNAUTHORIZED", "Missing X-Admin-Token")
    if tok != expected_token:
        raise ServiceError(403, "ADMIN_FORBIDDEN", "Invalid admin token")


def check_tenant_signature(
    db_path: str,
    headers,
    method: str,
    raw_path: str,
    body_bytes: bytes,
    expected_path_tenant: str | None = None,
) -> str:
    """Legacy HMAC checker (contains X-Now-Epoch omission, query-path bug & non-constant-time compare)."""
    tid = headers.get("X-Tenant-Id")
    ts_str = headers.get("X-Timestamp")
    sig = headers.get("X-Signature")
    if not tid or not ts_str or not sig:
        raise ServiceError(401, "UNAUTHORIZED", "Missing tenant auth headers")
    try:
        ts_val = int(ts_str)
    except ValueError:
        raise ServiceError(401, "UNAUTHORIZED", "Invalid X-Timestamp")

    # Legacy bug: ignores X-Now-Epoch test clock header!
    now_ref = int(time.time())
    if abs(now_ref - ts_val) > 300:
        raise ServiceError(401, "SIGNATURE_EXPIRED", "Signature timestamp expired")

    conn = get_conn(db_path)
    try:
        row = conn.execute("SELECT api_secret FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ServiceError(401, "UNKNOWN_TENANT", "Unknown tenant")

    # Legacy bug: uses raw_path (including ?query) instead of pure URL path, and `!=` instead of compare_digest
    body_sha = hashlib.sha256(body_bytes).hexdigest()
    canonical = f"{method.upper()}\n{raw_path}\n{ts_str}\n{body_sha}".encode("utf-8")
    expected_sig = hmac.new(str(row["api_secret"]).encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    if sig != expected_sig:
        raise ServiceError(401, "INVALID_SIGNATURE", "HMAC mismatch")

    if expected_path_tenant is not None and expected_path_tenant != tid:
        raise ServiceError(403, "TENANT_FORBIDDEN", "Cross-tenant access denied")
    return tid
