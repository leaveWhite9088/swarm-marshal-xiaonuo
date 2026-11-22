# -*- coding: utf-8 -*-
"""终局系统三波跨模块端到端黑盒集成压测（workload.py）。

在独立子进程与临时目录中启动被测终局沙盒的 `cloudhub.app.create_server`：
- Wave 1（A1，满分 12.0）：跨模块主链路端到端连通与核心业务（12 项 × 1.0 分）
- Wave 2（A2，满分 10.0）：复杂规则咬合、v1 在线迁表与财务/状态不变量（10 项 × 1.0 分）
- Wave 3（A3，满分  8.0）：幂等冲突、租约重试回收与多线程并发抢单压测（8 项 × 1.0 分）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PROBE_SCRIPT = r'''# -*- coding: utf-8 -*-
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

WS = Path(sys.argv[1]).resolve()
OUT_FILE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(WS))

ADMIN_TOKEN = "eval-admin-token-9981"


def sign_req(secret: str, method: str, path: str, ts: str, body: bytes) -> str:
    bh = hashlib.sha256(body).hexdigest()
    canon = f"{method.upper()}\n{path}\n{ts}\n{bh}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canon, hashlib.sha256).hexdigest()


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def call(
        self,
        method: str,
        path: str,
        payload=None,
        *,
        raw_bytes: bytes | None = None,
        admin: str | bool = False,
        tenant: str | None = None,
        secret: str = "sec_default",
        ts: int | None = None,
        now_hdr: int | None = None,
        idem: str | None = None,
    ) -> tuple[int, dict]:
        if raw_bytes is not None:
            body = raw_bytes
        elif payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if admin is True:
            headers["X-Admin-Token"] = ADMIN_TOKEN
        elif isinstance(admin, str):
            headers["X-Admin-Token"] = admin

        if tenant is not None:
            use_ts = str(ts if ts is not None else 1700000100)
            headers["X-Tenant-Id"] = tenant
            headers["X-Timestamp"] = use_ts
            headers["X-Now-Epoch"] = str(now_hdr if now_hdr is not None else int(use_ts))
            headers["X-Signature"] = sign_req(secret, method, path, use_ts, body)
        if idem is not None:
            headers["Idempotency-Key"] = idem

        req = urllib.request.Request(
            self.base_url + path,
            data=(body if method.upper() != "GET" else None),
            headers=headers,
            method=method.upper(),
        )
        try:
            with _OPENER.open(req, timeout=6) as r:
                raw = r.read().decode("utf-8", errors="replace")
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"ok": False, "raw": raw}


def run_check(results: list[dict], check_id: str, fn) -> None:
    try:
        fn()
        results.append({"id": check_id, "passed": True, "detail": "ok"})
    except Exception as exc:
        results.append({"id": check_id, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})


def run_all() -> dict:
    try:
        from cloudhub.app import create_server
    except Exception as exc:
        return {
            "boot_ok": False,
            "boot_error": f"{type(exc).__name__}: {exc}",
            "wave1": [],
            "wave2": [],
            "wave3": [],
        }

    w1: list[dict] = []
    w2: list[dict] = []
    w3: list[dict] = []

    # ---------------- WAVE 1: Core E2E (12 checks) ----------------
    with tempfile.TemporaryDirectory(prefix="eval_w1_") as tmp1:
        db1 = str(Path(tmp1) / "w1.db")
        try:
            srv1 = create_server(db_path=db1, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
        except Exception as exc:
            return {"boot_ok": False, "boot_error": f"create_server failed: {exc}", "wave1": [], "wave2": [], "wave3": []}
        th1 = threading.Thread(target=lambda: srv1.serve_forever(poll_interval=0.05), daemon=True)
        th1.start()
        cli = Client(f"http://127.0.0.1:{srv1.server_address[1]}")
        try:
            def c_w1_01():
                c, d = cli.call("GET", "/health")
                assert c == 200 and d.get("ok") is True and d.get("data", {}).get("status") == "healthy"
            run_check(w1, "w1_01_health", c_w1_01)

            def c_w1_02():
                c0, _ = cli.call("POST", "/api/v1/admin/tenants", {"tenant_id": "x"})
                assert c0 == 401
                c1, _ = cli.call("POST", "/api/v1/admin/tenants", {"tenant_id": "x"}, admin="wrong-tok")
                assert c1 == 403
                for tid, tier, q in [("t_start", "STARTER", 3600), ("t_grow", "GROWTH", 18000), ("t_ent", "ENTERPRISE", 108000)]:
                    c, d = cli.call(
                        "POST",
                        "/api/v1/admin/tenants",
                        {"tenant_id": tid, "name": tid, "tier": tier, "api_secret": f"sec_{tid}", "now_epoch": 1700000000},
                        admin=True,
                    )
                    assert c == 201 and d["ok"] is True and d["data"]["quota_gpu_sec"] == q
                c_dup, _ = cli.call(
                    "POST",
                    "/api/v1/admin/tenants",
                    {"tenant_id": "t_start", "name": "dup", "tier": "STARTER", "api_secret": "s"},
                    admin=True,
                )
                assert c_dup == 409
            run_check(w1, "w1_02_admin_auth_and_tenants", c_w1_02)

            def c_w1_03():
                c_no, _ = cli.call("GET", "/api/v1/tenants/t_start/statement")
                assert c_no == 401
                c_exp, d_exp = cli.call(
                    "GET", "/api/v1/tenants/t_start/statement",
                    tenant="t_start", secret="sec_t_start", ts=1700000000, now_hdr=1700000500,
                )
                assert c_exp == 401 and "EXPIRED" in d_exp.get("error", {}).get("code", "")
                c_bad, _ = cli.call(
                    "GET", "/api/v1/tenants/t_start/statement",
                    tenant="t_start", secret="wrong_secret",
                )
                assert c_bad == 401
            run_check(w1, "w1_03_hmac_signature_guard", c_w1_03)

            def c_w1_04():
                c, d = cli.call(
                    "POST", "/api/v1/tenants/t_start/topup",
                    {"amount_cents": 10000, "now_epoch": 1700000010},
                    tenant="t_start", secret="sec_t_start",
                )
                assert c == 200 and d["data"]["balance_cents"] == 10000 and d["data"]["frozen_cents"] == 0
            run_check(w1, "w1_04_topup_and_balance", c_w1_04)

            def c_w1_05():
                # 1700013600 -> UTC hour 02 (off-peak): 200s * 2 cents = 400 cents
                c, d = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013600},
                    tenant="t_start", secret="sec_t_start",
                )
                assert c == 201 and d["data"]["status"] == "QUEUED" and d["data"]["reserved_cents"] == 400
                _, st = cli.call("GET", "/api/v1/tenants/t_start/statement", tenant="t_start", secret="sec_t_start")
                assert st["data"]["balance_cents"] == 9600 and st["data"]["frozen_cents"] == 400
            run_check(w1, "w1_05_submit_offpeak_escrow", c_w1_05)

            def c_w1_06():
                c, d = cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_1", "now_epoch": 1700013610, "lease_seconds": 60}, admin=True)
                assert c == 200 and d["data"]["job"]["job_id"] == "j1" and d["data"]["job"]["status"] == "RUNNING"
                assert d["data"]["job"]["lease_until_epoch"] == 1700013670
            run_check(w1, "w1_06_worker_claim", c_w1_06)

            def c_w1_07():
                c, d = cli.call(
                    "POST", "/api/v1/jobs/j1/settle",
                    {"actual_gpu_sec": 200, "now_epoch": 1700013630},
                    tenant="t_start", secret="sec_t_start",
                )
                assert c == 200 and d["data"]["status"] == "SUCCEEDED"
                assert d["data"]["final_charge_cents"] == 400 and d["data"]["frozen_cents"] == 0 and d["data"]["balance_cents"] == 9600
            run_check(w1, "w1_07_exact_settle", c_w1_07)

            def c_w1_08():
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j2", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600},
                    tenant="t_start", secret="sec_t_start",
                )
                cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_1", "now_epoch": 1700013610}, admin=True)
                c, d = cli.call(
                    "POST", "/api/v1/jobs/j2/settle",
                    {"actual_gpu_sec": 150, "now_epoch": 1700013650},
                    tenant="t_start", secret="sec_t_start",
                )
                assert c == 200 and d["data"]["final_charge_cents"] == 300 and d["data"]["refunded_cents"] == 300
                assert d["data"]["balance_cents"] == 9300 and d["data"]["frozen_cents"] == 0
            run_check(w1, "w1_08_partial_settle_refund", c_w1_08)

            def c_w1_09():
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j3_qc", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600},
                    tenant="t_start", secret="sec_t_start",
                )
                c, d = cli.call("POST", "/api/v1/jobs/j3_qc/cancel", {"now_epoch": 1700013605}, tenant="t_start", secret="sec_t_start")
                assert c == 200 and d["data"]["penalty_cents"] == 0 and d["data"]["refunded_cents"] == 200
                assert d["data"]["balance_cents"] == 9300 and d["data"]["frozen_cents"] == 0
            run_check(w1, "w1_09_queued_cancel_zero_fee", c_w1_09)

            def c_w1_10():
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j4_fail", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600},
                    tenant="t_start", secret="sec_t_start",
                )
                cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_1", "now_epoch": 1700013610}, admin=True)
                c, d = cli.call("POST", "/api/v1/jobs/j4_fail/fail", {"reason": "oom", "now_epoch": 1700013620}, tenant="t_start", secret="sec_t_start")
                assert c == 200 and d["data"]["status"] == "FAILED" and d["data"]["refunded_cents"] == 200
                assert d["data"]["balance_cents"] == 9300 and d["data"]["frozen_cents"] == 0
            run_check(w1, "w1_10_running_fail_full_refund", c_w1_10)

            def c_w1_11():
                c, d = cli.call("GET", "/api/v1/tenants/t_start/statement", tenant="t_start", secret="sec_t_start")
                assert c == 200
                st = d["data"]
                assert st["balance_cents"] == 9300 and st["frozen_cents"] == 0
                assert st["used_gpu_sec"] == 350 and st["total_topup_cents"] == 10000 and st["total_charged_cents"] == 700
                assert isinstance(st["ledger"], list) and len(st["ledger"]) >= 6
            run_check(w1, "w1_11_tenant_statement", c_w1_11)

            def c_w1_12():
                c1, d1 = cli.call("POST", "/api/v1/tenants/t_start/topup", {"amount_cents": -5}, tenant="t_start", secret="sec_t_start")
                c2, d2 = cli.call("POST", "/api/v1/jobs", {"gpu_type": "V100", "priority": "NORMAL", "estimated_gpu_sec": 10, "submitted_epoch": 1700013600}, tenant="t_start", secret="sec_t_start")
                c3, d3 = cli.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 0, "submitted_epoch": 1700013600}, tenant="t_start", secret="sec_t_start")
                c4, d4 = cli.call("POST", "/api/v1/tenants/t_start/topup", raw_bytes=b"{bad json", tenant="t_start", secret="sec_t_start")
                for code, body in [(c1, d1), (c2, d2), (c3, d3), (c4, d4)]:
                    assert code == 400 and body.get("ok") is False and isinstance(body.get("error", {}).get("code"), str)
            run_check(w1, "w1_12_validation_and_envelope", c_w1_12)
        finally:
            srv1.shutdown()
            srv1.server_close()

    # ---------------- WAVE 2: Complex Interlocking Rules & Migration (10 checks) ----------------
    # Check 2.1: Dedicated legacy v1 SQLite non-destructive online migration check
    with tempfile.TemporaryDirectory(prefix="eval_w2_mig_") as tmp2_mig:
        db2_mig = str(Path(tmp2_mig) / "legacy_v1.db")
        raw_conn = sqlite3.connect(db2_mig)
        raw_conn.execute(
            "CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, name TEXT, tier TEXT, balance_cents INTEGER, api_secret TEXT, created_at INTEGER)"
        )
        raw_conn.execute(
            "CREATE TABLE ledger_entries (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, job_id TEXT, entry_type TEXT, delta_available INTEGER, balance_after INTEGER, created_at INTEGER)"
        )
        raw_conn.execute(
            "INSERT INTO tenants VALUES ('t_legacy', 'Legacy Growth', 'GROWTH', 20000, 'sec_leg', 1690000000)"
        )
        raw_conn.execute(
            "INSERT INTO ledger_entries (tenant_id, job_id, entry_type, delta_available, balance_after, created_at) VALUES ('t_legacy', NULL, 'TOPUP', 20000, 20000, 1690000000)"
        )
        raw_conn.commit()
        raw_conn.close()

        def c_w2_01():
            srv_m = create_server(db_path=db2_mig, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            threading.Thread(target=lambda: srv_m.serve_forever(poll_interval=0.05), daemon=True).start()
            try:
                cli_m = Client(f"http://127.0.0.1:{srv_m.server_address[1]}")
                c, st = cli_m.call("GET", "/api/v1/tenants/t_legacy/statement", tenant="t_legacy", secret="sec_leg")
                assert c == 200, f"legacy statement status {c}: {st}"
                assert st["data"]["balance_cents"] == 20000 and st["data"]["frozen_cents"] == 0
                assert st["data"]["quota_gpu_sec"] == 18000
                cj, _ = cli_m.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_leg_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 50, "submitted_epoch": 1700013600},
                    tenant="t_legacy", secret="sec_leg",
                )
                assert cj == 201
            finally:
                srv_m.shutdown()
                srv_m.server_close()

        run_check(w2, "w2_01_legacy_v1_online_migration", c_w2_01)

    # Checks 2.2 - 2.10: Fresh DB for orthogonal pricing, voucher, penalty, overrun, priority, quota, isolation & audit
    with tempfile.TemporaryDirectory(prefix="eval_w2_") as tmp2:
        db2 = str(Path(tmp2) / "w2_rules.db")
        try:
            srv2 = create_server(db_path=db2, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            th2 = threading.Thread(target=lambda: srv2.serve_forever(poll_interval=0.05), daemon=True)
            th2.start()
            cli2 = Client(f"http://127.0.0.1:{srv2.server_address[1]}")
        except Exception as exc:
            srv2 = None
            for cid in [
                "w2_02_progressive_two_bracket_pricing",
                "w2_03_tier_priority_peak_and_spot_exemption", "w2_04_voucher_rules_and_reuse",
                "w2_05_running_cancel_10pct_penalty", "w2_06_overrun_120pct_cap_and_402_guard",
                "w2_07_priority_scheduling_order", "w2_08_concurrency_and_quota_enforcement",
                "w2_09_cross_tenant_isolation", "w2_10_global_audit_conservation",
            ]:
                w2.append({"id": cid, "passed": False, "detail": f"Wave 2 boot failed: {exc}"})

        if srv2 is not None:
            try:
                try:
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_legacy", "name": "Growth", "tier": "GROWTH", "api_secret": "sec_leg"}, admin=True)
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_s", "name": "S", "tier": "STARTER", "api_secret": "sec_s"}, admin=True)
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_e", "name": "E", "tier": "ENTERPRISE", "api_secret": "sec_e"}, admin=True)
                    cli2.call("POST", "/api/v1/tenants/t_legacy/topup", {"amount_cents": 20000}, tenant="t_legacy", secret="sec_leg")
                    cli2.call("POST", "/api/v1/tenants/t_s/topup", {"amount_cents": 50000}, tenant="t_s", secret="sec_s")
                    cli2.call("POST", "/api/v1/tenants/t_e/topup", {"amount_cents": 50000}, tenant="t_e", secret="sec_e")
                except Exception:
                    pass

                def c_w2_02():
                    # H100 (12c), 1000s, off-peak (1700013600), STARTER (100%): 600*12 + 400*12*0.8 = 7200 + 3840 = 11040
                    c, d = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw2_brk", "gpu_type": "H100", "priority": "NORMAL", "estimated_gpu_sec": 1000, "submitted_epoch": 1700013600},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c == 201 and d["data"]["reserved_cents"] == 11040, f"expected 11040, got {d}"
                    cli2.call("POST", "/api/v1/jobs/jw2_brk/cancel", {"now_epoch": 1700013605}, tenant="t_s", secret="sec_s")
                run_check(w2, "w2_02_progressive_two_bracket_pricing", c_w2_02)

                def c_w2_03():
                    # Peak epoch 1700049600 (UTC 12:00):
                    # 1) GROWTH (90%) * L40 (5c) * 100s * HIGH (125%) * Peak (120%) = 675
                    c1, d1 = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw2_pk_h", "gpu_type": "L40", "priority": "HIGH", "estimated_gpu_sec": 100, "submitted_epoch": 1700049600},
                        tenant="t_legacy", secret="sec_leg",
                    )
                    assert c1 == 201 and d1["data"]["reserved_cents"] == 675, f"expected 675, got {d1}"
                    cli2.call("POST", "/api/v1/jobs/jw2_pk_h/cancel", {}, tenant="t_legacy", secret="sec_leg")

                    # 2) SPOT peak exemption: ENTERPRISE (80%) * H100 (12c) * 100s * SPOT (70%) * NO PEAK (100%) = 672
                    c2, d2 = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw2_spot", "gpu_type": "H100", "priority": "SPOT", "estimated_gpu_sec": 100, "submitted_epoch": 1700049600},
                        tenant="t_e", secret="sec_e",
                    )
                    assert c2 == 201 and d2["data"]["reserved_cents"] == 672, f"SPOT peak expected 672, got {d2}"
                    cli2.call("POST", "/api/v1/jobs/jw2_spot/cancel", {}, tenant="t_e", secret="sec_e")

                    # 3) Half-up rounding: GROWTH (90%) * HIGH (125%) * off-peak * A10 (2c) * 3s = 6.75 -> 7 cents
                    c3, d3 = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw2_rnd", "gpu_type": "A10", "priority": "HIGH", "estimated_gpu_sec": 3, "submitted_epoch": 1700013600},
                        tenant="t_legacy", secret="sec_leg",
                    )
                    assert c3 == 201 and d3["data"]["reserved_cents"] == 7, f"half-up expected 7, got {d3}"
                    cli2.call("POST", "/api/v1/jobs/jw2_rnd/cancel", {}, tenant="t_legacy", secret="sec_leg")
                run_check(w2, "w2_03_tier_priority_peak_and_spot_exemption", c_w2_03)

                def c_w2_04():
                    cv, _ = cli2.call(
                        "POST", "/api/v1/admin/vouchers",
                        {"code": "V250", "tenant_id": "t_s", "discount_cents": 250, "min_spend_cents": 500, "expires_at_epoch": 1700050000},
                        admin=True,
                    )
                    assert cv == 201
                    # Below min_spend (200s * 2c = 400 < 500) -> 400
                    c_low, _ = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jv_low", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013600, "voucher_code": "V250"},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c_low == 400
                    # Expired -> 400
                    c_exp, _ = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jv_exp", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700060000, "voucher_code": "V250"},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c_exp == 400
                    # Valid (300s * 2c = 600 - 250 = 350)
                    c_ok, d_ok = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jv_ok", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600, "voucher_code": "V250"},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c_ok == 201 and d_ok["data"]["reserved_cents"] == 350
                    # Re-use while active -> 409
                    c_use, _ = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jv_dup", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600, "voucher_code": "V250"},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c_use == 409
                    # Claim -> RUNNING -> Cancel -> Voucher must be released and reusable!
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013610}, admin=True)
                    cli2.call("POST", "/api/v1/jobs/jv_ok/cancel", {"now_epoch": 1700013620}, tenant="t_s", secret="sec_s")
                    c_re, d_re = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jv_reused", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013630, "voucher_code": "V250"},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c_re == 201 and d_re["data"]["reserved_cents"] == 350
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013640}, admin=True)
                    cli2.call("POST", "/api/v1/jobs/jv_reused/settle", {"actual_gpu_sec": 300, "now_epoch": 1700013650}, tenant="t_s", secret="sec_s")
                run_check(w2, "w2_04_voucher_rules_and_reuse", c_w2_04)

                def c_w2_05():
                    # Submit job with reserved_cents = 555 (L40=5c, 111s, off-peak, STARTER -> 555)
                    c, d = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "j_pen", "gpu_type": "L40", "priority": "NORMAL", "estimated_gpu_sec": 111, "submitted_epoch": 1700013600},
                        tenant="t_s", secret="sec_s",
                    )
                    assert c == 201 and d["data"]["reserved_cents"] == 555
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013610}, admin=True)
                    cc, dc = cli2.call("POST", "/api/v1/jobs/j_pen/cancel", {"now_epoch": 1700013620}, tenant="t_s", secret="sec_s")
                    assert cc == 200 and dc["data"]["penalty_cents"] == 56 and dc["data"]["refunded_cents"] == 499
                run_check(w2, "w2_05_running_cancel_10pct_penalty", c_w2_05)

                def c_w2_06():
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_tight", "name": "Tight", "tier": "STARTER", "api_secret": "sec_t"}, admin=True)
                    cli2.call("POST", "/api/v1/tenants/t_tight/topup", {"amount_cents": 200}, tenant="t_tight", secret="sec_t")
                    cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "j_ovr", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600},
                        tenant="t_tight", secret="sec_t",
                    )
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013610}, admin=True)
                    # Overrun 115s needs extra 30 cents, available is 0 -> 402
                    c402, _ = cli2.call("POST", "/api/v1/jobs/j_ovr/settle", {"actual_gpu_sec": 115, "now_epoch": 1700013620}, tenant="t_tight", secret="sec_t")
                    assert c402 == 402
                    # Topup 100 cents, settle actual=200s -> capped at 120s (240 cents, extra=40, balance=60)
                    cli2.call("POST", "/api/v1/tenants/t_tight/topup", {"amount_cents": 100}, tenant="t_tight", secret="sec_t")
                    cok, dok = cli2.call("POST", "/api/v1/jobs/j_ovr/settle", {"actual_gpu_sec": 200, "now_epoch": 1700013630}, tenant="t_tight", secret="sec_t")
                    assert cok == 200 and dok["data"]["billable_gpu_sec"] == 120
                    assert dok["data"]["final_charge_cents"] == 240 and dok["data"]["extra_charged_cents"] == 40 and dok["data"]["balance_cents"] == 60
                run_check(w2, "w2_06_overrun_120pct_cap_and_402_guard", c_w2_06)

                def c_w2_07():
                    for jid, prio, sub_t in [
                        ("jq_spot", "SPOT", 1700013610),
                        ("jq_norm", "NORMAL", 1700013611),
                        ("jq_high2", "HIGH", 1700013612),
                        ("jq_high1", "HIGH", 1700013611),
                    ]:
                        c, _ = cli2.call(
                            "POST", "/api/v1/jobs",
                            {"job_id": jid, "gpu_type": "A10", "priority": prio, "estimated_gpu_sec": 10, "submitted_epoch": sub_t},
                            tenant="t_e", secret="sec_e",
                        )
                        assert c == 201
                    claimed = []
                    for _ in range(4):
                        _, cd = cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013620}, admin=True)
                        claimed.append(cd["data"]["job"]["job_id"])
                    assert claimed == ["jq_high1", "jq_high2", "jq_norm", "jq_spot"], f"got order {claimed}"
                    for jid in claimed:
                        cli2.call("POST", f"/api/v1/jobs/{jid}/settle", {"actual_gpu_sec": 10, "now_epoch": 1700013630}, tenant="t_e", secret="sec_e")
                run_check(w2, "w2_07_priority_scheduling_order", c_w2_07)

                def c_w2_08():
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_lim", "name": "Lim", "tier": "STARTER", "api_secret": "sec_l"}, admin=True)
                    cli2.call("POST", "/api/v1/tenants/t_lim/topup", {"amount_cents": 50000}, tenant="t_lim", secret="sec_l")
                    # Quota 3600: try 3700s -> 409 QUOTA_EXCEEDED
                    cq, dq = cli2.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "j_qover", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 3700, "submitted_epoch": 1700013600},
                        tenant="t_lim", secret="sec_l",
                    )
                    assert cq == 409 and "QUOTA" in dq.get("error", {}).get("code", "")
                    # Max concurrent = 2
                    cli2.call("POST", "/api/v1/jobs", {"job_id": "jl1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600}, tenant="t_lim", secret="sec_l")
                    cli2.call("POST", "/api/v1/jobs", {"job_id": "jl2", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600}, tenant="t_lim", secret="sec_l")
                    cc, dc = cli2.call("POST", "/api/v1/jobs", {"job_id": "jl3", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600}, tenant="t_lim", secret="sec_l")
                    assert cc == 409 and "CONCURRENCY" in dc.get("error", {}).get("code", "")
                run_check(w2, "w2_08_concurrency_and_quota_enforcement", c_w2_08)

                def c_w2_09():
                    c1, _ = cli2.call("GET", "/api/v1/tenants/t_lim/statement", tenant="t_e", secret="sec_e")
                    c2, _ = cli2.call("GET", "/api/v1/jobs/jl1", tenant="t_e", secret="sec_e")
                    c3, _ = cli2.call("POST", "/api/v1/jobs/jl1/cancel", {}, tenant="t_e", secret="sec_e")
                    assert c1 == 403 and c2 == 403 and c3 == 403
                run_check(w2, "w2_09_cross_tenant_isolation", c_w2_09)

                def c_w2_10():
                    c, d = cli2.call("GET", "/api/v1/admin/audit", admin=True)
                    assert c == 200 and d["data"]["all_balanced"] is True, f"audit failed: {d}"
                    assert d["data"]["anomalies"] == []
                    # Active queued jl1 + jl2 = 400 cents frozen
                    assert d["data"]["total_frozen_cents"] == d["data"]["active_escrow_cents"] == 400
                    # Expected revenue: jv_ok running-cancel(35) + jv_reused(350) + j_pen penalty(56) + j_ovr(240) + jq(20+20+16+11=67) = 748
                    assert d["data"]["total_platform_revenue_cents"] == 748, f"expected rev 748, got {d['data']}"
                    assert (
                        d["data"]["total_available_cents"]
                        + d["data"]["total_frozen_cents"]
                        + d["data"]["total_platform_revenue_cents"]
                        == 170300
                    )
                run_check(w2, "w2_10_global_audit_conservation", c_w2_10)
            finally:
                srv2.shutdown()
                srv2.server_close()

    # ---------------- WAVE 3: Idempotency, Reaper & Concurrency Stress (8 checks) ----------------
    with tempfile.TemporaryDirectory(prefix="eval_w3_") as tmp3:
        db3 = str(Path(tmp3) / "w3.db")
        try:
            srv3 = create_server(db_path=db3, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            th3 = threading.Thread(target=lambda: srv3.serve_forever(poll_interval=0.05), daemon=True)
            th3.start()
            cli3 = Client(f"http://127.0.0.1:{srv3.server_address[1]}")
        except Exception as exc:
            srv3 = None
            for cid in [
                "w3_01_idempotency_exact_replay", "w3_02_idempotency_conflict_409",
                "w3_03_illegal_state_transitions", "w3_04_reaper_lease_retry_requeue",
                "w3_05_reaper_retry_exhaustion_and_ttl_expiry", "w3_06_concurrent_claim_race",
                "w3_07_concurrent_submit_overdraft_race", "w3_08_post_chaos_audit_invariants",
            ]:
                w3.append({"id": cid, "passed": False, "detail": f"Wave 3 boot failed: {exc}"})

        if srv3 is not None:
            try:
                try:
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_w3", "name": "W3", "tier": "ENTERPRISE", "api_secret": "sec_w3"}, admin=True)
                except Exception:
                    pass

                def c_w3_01():
                    r1_c, r1_d = cli3.call("POST", "/api/v1/tenants/t_w3/topup", {"amount_cents": 20000}, tenant="t_w3", secret="sec_w3", idem="idem_top_1")
                    r2_c, r2_d = cli3.call("POST", "/api/v1/tenants/t_w3/topup", {"amount_cents": 20000}, tenant="t_w3", secret="sec_w3", idem="idem_top_1")
                    assert r1_c == 200 and r2_c == 200 and r2_d["data"]["balance_cents"] == 20000

                    j_body = {"job_id": "jw3_idem", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600}
                    j1_c, j1_d = cli3.call("POST", "/api/v1/jobs", j_body, tenant="t_w3", secret="sec_w3", idem="idem_sub_1")
                    j2_c, j2_d = cli3.call("POST", "/api/v1/jobs", j_body, tenant="t_w3", secret="sec_w3", idem="idem_sub_1")
                    assert j1_c == 201 and j2_c == 201 and j1_d["data"]["job_id"] == j2_d["data"]["job_id"]

                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013605}, admin=True)
                    s1_c, s1_d = cli3.call("POST", "/api/v1/jobs/jw3_idem/settle", {"actual_gpu_sec": 80}, tenant="t_w3", secret="sec_w3", idem="idem_set_1")
                    s2_c, s2_d = cli3.call("POST", "/api/v1/jobs/jw3_idem/settle", {"actual_gpu_sec": 80}, tenant="t_w3", secret="sec_w3", idem="idem_set_1")
                    assert s1_c == 200 and s2_c == 200 and s1_d["data"] == s2_d["data"]
                run_check(w3, "w3_01_idempotency_exact_replay", c_w3_01)

                def c_w3_02():
                    c_conf, d_conf = cli3.call(
                        "POST", "/api/v1/tenants/t_w3/topup", {"amount_cents": 99999},
                        tenant="t_w3", secret="sec_w3", idem="idem_top_1",
                    )
                    assert c_conf == 409 and "IDEMPOTENCY" in d_conf.get("error", {}).get("code", "")
                run_check(w3, "w3_02_idempotency_conflict_409", c_w3_02)

                def c_w3_03():
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "jw3_st", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 50, "submitted_epoch": 1700013600}, tenant="t_w3", secret="sec_w3")
                    # Cannot settle QUEUED job directly
                    c1, _ = cli3.call("POST", "/api/v1/jobs/jw3_st/settle", {"actual_gpu_sec": 50}, tenant="t_w3", secret="sec_w3")
                    assert c1 == 409
                    cli3.call("POST", "/api/v1/jobs/jw3_st/cancel", {}, tenant="t_w3", secret="sec_w3")
                    # Cannot fail or cancel already CANCELLED job
                    c2, _ = cli3.call("POST", "/api/v1/jobs/jw3_st/fail", {}, tenant="t_w3", secret="sec_w3")
                    c3, _ = cli3.call("POST", "/api/v1/jobs/jw3_st/cancel", {}, tenant="t_w3", secret="sec_w3")
                    # Cannot cancel already SUCCEEDED job
                    c4, _ = cli3.call("POST", "/api/v1/jobs/jw3_idem/cancel", {}, tenant="t_w3", secret="sec_w3")
                    assert c2 == 409 and c3 == 409 and c4 == 409
                run_check(w3, "w3_03_illegal_state_transitions", c_w3_03)

                def c_w3_04():
                    cli3.call("POST", "/api/v1/admin/vouchers", {"code": "VW3", "tenant_id": "t_w3", "discount_cents": 50, "min_spend_cents": 100, "expires_at_epoch": 1700050000}, admin=True)
                    cli3.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw3_reap", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600, "max_retries": 1, "voucher_code": "VW3"},
                        tenant="t_w3", secret="sec_w3",
                    )
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w_crash", "now_epoch": 1700013610, "lease_seconds": 30}, admin=True)
                    _, before_st = cli3.call("GET", "/api/v1/tenants/t_w3/statement", tenant="t_w3", secret="sec_w3")
                    frz_before = before_st["data"]["frozen_cents"]

                    cr, dr = cli3.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013650}, admin=True)
                    assert cr == 200 and dr["data"]["requeued_count"] == 1
                    _, jd = cli3.call("GET", "/api/v1/jobs/jw3_reap", tenant="t_w3", secret="sec_w3")
                    assert jd["data"]["status"] == "QUEUED" and jd["data"]["retry_count"] == 1
                    _, after_st = cli3.call("GET", "/api/v1/tenants/t_w3/statement", tenant="t_w3", secret="sec_w3")
                    assert after_st["data"]["frozen_cents"] == frz_before
                run_check(w3, "w3_04_reaper_lease_retry_requeue", c_w3_04)

                def c_w3_05():
                    # Claim jw3_reap again, expire lease -> retry_count(1) >= max_retries(1) -> FAILED + refund + release VW3
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "w_crash2", "now_epoch": 1700013660, "lease_seconds": 20}, admin=True)
                    # Also submit a short-TTL job that stays QUEUED
                    cli3.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw3_ttl", "gpu_type": "A10", "priority": "SPOT", "estimated_gpu_sec": 100, "submitted_epoch": 1700013660, "ttl_seconds": 15},
                        tenant="t_w3", secret="sec_w3",
                    )
                    cr, dr = cli3.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013700}, admin=True)
                    assert cr == 200 and dr["data"]["failed_count"] == 1 and dr["data"]["expired_count"] == 1
                    # Verify voucher VW3 was released and can be reused
                    cv_re, _ = cli3.call(
                        "POST", "/api/v1/jobs",
                        {"job_id": "jw3_vreuse", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013710, "voucher_code": "VW3"},
                        tenant="t_w3", secret="sec_w3",
                    )
                    assert cv_re == 201
                    cli3.call("POST", "/api/v1/jobs/jw3_vreuse/cancel", {}, tenant="t_w3", secret="sec_w3")
                run_check(w3, "w3_05_reaper_retry_exhaustion_and_ttl_expiry", c_w3_05)

                def c_w3_06():
                    for i in range(3):
                        cli3.call(
                            "POST", "/api/v1/jobs",
                            {"job_id": f"jw3_race_{i}", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 20, "submitted_epoch": 1700013800 + i},
                            tenant="t_w3", secret="sec_w3",
                        )

                    def _do_claim(idx: int):
                        return cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": f"wn_{idx}", "now_epoch": 1700013810}, admin=True)

                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                        outs = list(ex.map(_do_claim, range(8)))
                    assert all(code == 200 for code, _ in outs), f"claim race non-200: {outs}"
                    got_ids = [d["data"]["job"]["job_id"] for _, d in outs if d["data"].get("job") is not None]
                    assert len(got_ids) == 3 and len(set(got_ids)) == 3, f"duplicate or lost claim: {got_ids}"
                    for jid in got_ids:
                        cli3.call("POST", f"/api/v1/jobs/{jid}/settle", {"actual_gpu_sec": 20}, tenant="t_w3", secret="sec_w3")
                run_check(w3, "w3_06_concurrent_claim_race", c_w3_06)

                def c_w3_07():
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_race_bal", "name": "RB", "tier": "STARTER", "api_secret": "sec_rb"}, admin=True)
                    # Balance 450 cents; each job needs 200 cents; max_concurrent=2 -> at most 2 can succeed!
                    cli3.call("POST", "/api/v1/tenants/t_race_bal/topup", {"amount_cents": 450}, tenant="t_race_bal", secret="sec_rb")

                    def _do_sub(idx: int):
                        return cli3.call(
                            "POST", "/api/v1/jobs",
                            {"job_id": f"jrb_{idx}", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013900},
                            tenant="t_race_bal", secret="sec_rb",
                        )

                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                        outs = list(ex.map(_do_sub, range(8)))
                    codes = [c for c, _ in outs]
                    assert 500 not in codes, f"500 error under concurrent submit: {outs}"
                    ok_cnt = sum(1 for c in codes if c == 201)
                    assert ok_cnt == 2, f"expected exactly 2 accepted jobs, got {ok_cnt} ({codes})"
                    _, st = cli3.call("GET", "/api/v1/tenants/t_race_bal/statement", tenant="t_race_bal", secret="sec_rb")
                    assert st["data"]["balance_cents"] == 50 and st["data"]["frozen_cents"] == 400
                run_check(w3, "w3_07_concurrent_submit_overdraft_race", c_w3_07)

                def c_w3_08():
                    c, d = cli3.call("GET", "/api/v1/admin/audit", admin=True)
                    assert c == 200 and d["data"]["all_balanced"] is True, f"post-chaos audit failed: {d}"
                    assert d["data"]["anomalies"] == []
                    assert d["data"]["total_frozen_cents"] == d["data"]["active_escrow_cents"] == 400
                run_check(w3, "w3_08_post_chaos_audit_invariants", c_w3_08)
            finally:
                srv3.shutdown()
                srv3.server_close()

    return {"boot_ok": True, "boot_error": None, "wave1": w1, "wave2": w2, "wave3": w3}


if __name__ == "__main__":
    try:
        report = run_all()
    except Exception as exc:
        report = {"boot_ok": False, "boot_error": traceback.format_exc()[-1000:], "wave1": [], "wave2": [], "wave3": []}
    OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
'''


def evaluate_workspace_waves(workspace_dir: Path, scoring_cfg: dict) -> dict:
    """将终局沙盒拷贝至干净临时目录并在独立子进程中执行三波黑盒集成验收。"""
    a_cfg = (scoring_cfg.get("A") or {})
    a1_max = float(a_cfg.get("a1_core_e2e_max", 12.0))
    a2_max = float(a_cfg.get("a2_invariants_max", 10.0))
    a3_max = float(a_cfg.get("a3_resilience_max", 8.0))

    with tempfile.TemporaryDirectory(prefix="swarm_eval_box_") as tmp:
        tmp_path = Path(tmp)
        isolated_ws = tmp_path / "workspace"
        shutil.copytree(workspace_dir, isolated_ws)
        from src.marshal.guardrails import set_workspace_writable
        set_workspace_writable(isolated_ws, True)
        probe_py = tmp_path / "_run_probe.py"
        out_json = tmp_path / "_probe_out.json"
        probe_py.write_text(_PROBE_SCRIPT, encoding="utf-8")

        try:
            subprocess.run(
                [sys.executable, str(probe_py), str(isolated_ws), str(out_json)],
                cwd=str(isolated_ws),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return {
                "boot_ok": False,
                "boot_error": "三波压测执行超时（>60s）",
                "A1": 0.0,
                "A2": 0.0,
                "A3": 0.0,
                "A_total": 0.0,
                "waves": {"wave1": [], "wave2": [], "wave3": []},
            }

        if not out_json.is_file():
            return {
                "boot_ok": False,
                "boot_error": "压测探针异常退出，未产出结果文件",
                "A1": 0.0,
                "A2": 0.0,
                "A3": 0.0,
                "A_total": 0.0,
                "waves": {"wave1": [], "wave2": [], "wave3": []},
            }

        raw = json.loads(out_json.read_text(encoding="utf-8"))

    w1 = raw.get("wave1") or []
    w2 = raw.get("wave2") or []
    w3 = raw.get("wave3") or []

    w1_passed = sum(1 for x in w1 if x.get("passed"))
    w2_passed = sum(1 for x in w2 if x.get("passed"))
    w3_passed = sum(1 for x in w3 if x.get("passed"))

    a1 = round(a1_max * (w1_passed / 12.0), 2) if w1 else 0.0
    a2 = round(a2_max * (w2_passed / 10.0), 2) if w2 else 0.0
    a3 = round(a3_max * (w3_passed / 8.0), 2) if w3 else 0.0
    a_tot = round(a1 + a2 + a3, 2)

    return {
        "boot_ok": bool(raw.get("boot_ok")),
        "boot_error": raw.get("boot_error"),
        "A1": a1,
        "A2": a2,
        "A3": a3,
        "A_total": a_tot,
        "wave_counts": {
            "w1_passed": w1_passed,
            "w1_total": 12,
            "w2_passed": w2_passed,
            "w2_total": 10,
            "w3_passed": w3_passed,
            "w3_total": 8,
        },
        "waves": {"wave1": w1, "wave2": w2, "wave3": w3},
    }
