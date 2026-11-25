#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CloudHub v1.5 legacy HTTP gateway (`cloudhub/app.py`).

注意：本半成品虽可通过最基础的冒烟连通检查，但网关层与持久层、计费层、状态机层之间仍潜伏着多处鉴权时序、
幂等污染、参数类型校验及并发竞态缺陷，且尚未实现第二阶段演进接口。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .auth import check_admin, check_tenant_signature
from .billing import (
    ServiceError,
    create_tenant,
    create_voucher,
    get_platform_audit,
    get_statement,
    topup_tenant,
)
from .db import get_cached_idempotency, init_db, store_idempotency
from .jobs import cancel_job, claim_job, fail_job, get_job, reap_jobs, settle_job, submit_job

_RE_TOPUP = re.compile(r"^/api/v1/tenants/([^/]+)/topup$")
_RE_STATEMENT = re.compile(r"^/api/v1/tenants/([^/]+)/statement$")
_RE_JOB_GET = re.compile(r"^/api/v1/jobs/([^/]+)$")
_RE_JOB_ACT = re.compile(r"^/api/v1/jobs/([^/]+)/(settle|fail|cancel)$")


def create_server(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    admin_token: str = "marshal-admin-secret",
) -> ThreadingHTTPServer:
    init_db(db_path)

    class LegacyGatewayHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_body(self) -> tuple[bytes, dict]:
            clen = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(clen) if clen > 0 else b""
            if not raw:
                return b"", {}
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                raise ServiceError(400, "INVALID_ARGUMENT", "Malformed JSON request body")
            if not isinstance(parsed, dict):
                raise ServiceError(400, "INVALID_ARGUMENT", "JSON body must be an object")
            return raw, parsed

        def do_GET(self) -> None:
            pure_path = self.path.split("?", 1)[0]
            try:
                if pure_path == "/health":
                    self._send(200, {"ok": True, "data": {"status": "healthy"}})
                    return

                if pure_path == "/api/v1/admin/audit":
                    check_admin(self.headers, admin_token)
                    self._send(200, {"ok": True, "data": get_platform_audit(db_path)})
                    return

                m_st = _RE_STATEMENT.match(pure_path)
                if m_st:
                    path_tid = m_st.group(1)
                    check_tenant_signature(db_path, self.headers, "GET", self.path, b"", expected_path_tenant=path_tid)
                    self._send(200, {"ok": True, "data": get_statement(db_path, path_tid)})
                    return

                m_jg = _RE_JOB_GET.match(pure_path)
                if m_jg:
                    tid = check_tenant_signature(db_path, self.headers, "GET", self.path, b"")
                    self._send(200, {"ok": True, "data": get_job(db_path, tid, m_jg.group(1))})
                    return

                self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown endpoint"}})
            except ServiceError as exc:
                self._send(exc.status_code, {"ok": False, "error": {"code": exc.code, "message": exc.message}})
            except Exception as exc:
                self._send(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}})

        def do_POST(self) -> None:
            pure_path = self.path.split("?", 1)[0]
            idem_key = (self.headers.get("Idempotency-Key") or "").strip() or None
            raw_body = b""
            req_hash = ""
            try:
                raw_body, payload = self._read_body()
                req_hash = hashlib.sha256(raw_body).hexdigest()

                # Legacy security & isolation bug: checks global idempotency BEFORE authentication & tenant ownership!
                if idem_key:
                    state_tag, c_code, c_obj = get_cached_idempotency(db_path, idem_key, req_hash)
                    if state_tag in ("HIT", "CONFLICT"):
                        self._send(int(c_code or 200), c_obj or {})
                        return

                if pure_path == "/api/v1/admin/tenants":
                    check_admin(self.headers, admin_token)
                    data = create_tenant(db_path, payload)
                    self._send(201, {"ok": True, "data": data})
                    return

                if pure_path == "/api/v1/admin/vouchers":
                    check_admin(self.headers, admin_token)
                    data = create_voucher(db_path, payload)
                    self._send(201, {"ok": True, "data": data})
                    return

                if pure_path == "/api/v1/admin/reap":
                    check_admin(self.headers, admin_token)
                    data = reap_jobs(db_path, payload)
                    self._send(200, {"ok": True, "data": data})
                    return

                if pure_path == "/api/v1/workers/claim":
                    check_admin(self.headers, admin_token)
                    data = claim_job(db_path, payload)
                    self._send(200, {"ok": True, "data": data})
                    return

                m_tp = _RE_TOPUP.match(pure_path)
                if m_tp:
                    path_tid = m_tp.group(1)
                    check_tenant_signature(db_path, self.headers, "POST", self.path, raw_body, expected_path_tenant=path_tid)
                    data = topup_tenant(db_path, path_tid, payload)
                    resp = {"ok": True, "data": data}
                    if idem_key:
                        store_idempotency(db_path, idem_key, req_hash, 200, resp)
                    self._send(200, resp)
                    return

                if pure_path == "/api/v1/jobs":
                    tid = check_tenant_signature(db_path, self.headers, "POST", self.path, raw_body)
                    data = submit_job(db_path, tid, payload)
                    resp = {"ok": True, "data": data}
                    if idem_key:
                        store_idempotency(db_path, idem_key, req_hash, 201, resp)
                    self._send(201, resp)
                    return

                m_act = _RE_JOB_ACT.match(pure_path)
                if m_act:
                    jid, act = m_act.group(1), m_act.group(2)
                    tid = check_tenant_signature(db_path, self.headers, "POST", self.path, raw_body)
                    if act == "settle":
                        data = settle_job(db_path, tid, jid, payload)
                    elif act == "fail":
                        data = fail_job(db_path, tid, jid, payload)
                    else:
                        data = cancel_job(db_path, tid, jid, payload)
                    resp = {"ok": True, "data": data}
                    if idem_key:
                        store_idempotency(db_path, idem_key, req_hash, 200, resp)
                    self._send(200, resp)
                    return

                self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown endpoint"}})
            except ServiceError as exc:
                err_resp = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
                # Legacy bug: caches 4xx business errors (e.g. 402 insufficient balance) into idempotency table!
                if idem_key:
                    store_idempotency(db_path, idem_key, req_hash, exc.status_code, err_resp)
                self._send(exc.status_code, err_resp)
            except Exception as exc:
                self._send(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}})

    srv = ThreadingHTTPServer((host, port), LegacyGatewayHandler)
    srv.db_path = db_path  # type: ignore[attr-defined]
    srv.admin_token = admin_token  # type: ignore[attr-defined]
    return srv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--admin-token", default="marshal-admin-secret")
    args = ap.parse_args()
    srv = create_server(args.db, args.host, args.port, args.admin_token)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
