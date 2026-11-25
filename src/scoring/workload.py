# -*- coding: utf-8 -*-
"""终局系统四波跨模块端到端黑盒对抗压测（workload.py · xiaonuo-v2.0）。

在独立子进程与隔离临时目录中启动被测终局沙盒的 `cloudhub.app.create_server`：
- Wave 1（A1，满分 6.0）：存量半成品暗雷清剿与一阶段主链路（10 项 × 0.6 分）
- Wave 2（A2，满分 8.0）：历史脏账老库迁移、代金券门槛反转超支与状态机不变量（10 项 × 0.8 分）
- Wave 3（A3，满分 9.0）：二阶段紧急业务演进——高优抢占驱逐补贴与母子组织授信池（10 项 × 0.9 分）
- Wave 4（A4，满分 7.0）：跨租户幂等攻防、并发连击与多子租户共享池混沌风暴（10 项 × 0.7 分）
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
import random
import sqlite3
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
from pathlib import Path

WS = Path(sys.argv[1]).resolve()
OUT_FILE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(WS))

ADMIN_TOKEN = "eval-admin-token-v2-9981"


def sign_req(secret: str, method: str, pure_path: str, ts: str, body: bytes) -> str:
    bh = hashlib.sha256(body).hexdigest()
    canon = f"{method.upper()}\n{pure_path}\n{ts}\n{bh}".encode("utf-8")
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
        ts: int | str | None = None,
        now_hdr: int | str | None = None,
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

        pure_path = path.split("?", 1)[0]
        if tenant is not None:
            use_ts = str(ts if ts is not None else 1700000100)
            headers["X-Tenant-Id"] = tenant
            headers["X-Timestamp"] = use_ts
            if now_hdr is not None:
                headers["X-Now-Epoch"] = str(now_hdr)
            else:
                try:
                    headers["X-Now-Epoch"] = str(int(use_ts))
                except Exception:
                    pass
            headers["X-Signature"] = sign_req(secret, method, pure_path, use_ts, body)
        if idem is not None:
            headers["Idempotency-Key"] = idem

        req = urllib.request.Request(
            self.base_url + path,
            data=(body if method.upper() != "GET" else None),
            headers=headers,
            method=method.upper(),
        )
        try:
            with _OPENER.open(req, timeout=8) as r:
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
            "wave4": [],
        }

    w1: list[dict] = []
    w2: list[dict] = []
    w3: list[dict] = []
    w4: list[dict] = []

    # =========================================================================
    # WAVE 1: Legacy Bug-Hunt & Phase 1 Core E2E (10 checks)
    # =========================================================================
    with tempfile.TemporaryDirectory(prefix="eval_w1_") as tmp1:
        db1 = str(Path(tmp1) / "w1.db")
        try:
            srv1 = create_server(db_path=db1, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
        except Exception as exc:
            return {"boot_ok": False, "boot_error": f"create_server failed: {exc}", "wave1": [], "wave2": [], "wave3": [], "wave4": []}
        th1 = threading.Thread(target=lambda: srv1.serve_forever(poll_interval=0.04), daemon=True)
        th1.start()
        cli = Client(f"http://127.0.0.1:{srv1.server_address[1]}")
        try:
            def c_w1_01():
                c, d = cli.call("GET", "/health")
                assert c == 200 and d.get("ok") is True and d.get("data", {}).get("status") == "healthy"
                c0, _ = cli.call("POST", "/api/v1/admin/tenants", {"tenant_id": "x"})
                c1, d1 = cli.call("POST", "/api/v1/admin/tenants", {"tenant_id": "x"}, admin="wrong-tok")
                assert c0 == 401 and c1 == 403 and "ADMIN_FORBIDDEN" in d1.get("error", {}).get("code", "")
                for tid, tier, q in [("t_s", "STARTER", 3600), ("t_g", "GROWTH", 18000), ("t_e", "ENTERPRISE", 108000)]:
                    cr, dr = cli.call(
                        "POST", "/api/v1/admin/tenants",
                        {"tenant_id": tid, "name": tid, "tier": tier, "api_secret": f"sec_{tid}", "now_epoch": 1700000000},
                        admin=True,
                    )
                    assert cr == 201 and dr["data"]["quota_gpu_sec"] == q
                c_dup, _ = cli.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_s", "name": "dup", "tier": "STARTER", "api_secret": "s"}, admin=True)
                assert c_dup == 409
            run_check(w1, "w1_01_health_and_admin_guard", c_w1_01)

            def c_w1_02():
                # Catches legacy auth bugs: ignoring X-Now-Epoch AND signing raw query string instead of pure path
                cok, dok = cli.call(
                    "GET", "/api/v1/tenants/t_s/statement?format=compact&v=2",
                    tenant="t_s", secret="sec_t_s", ts=1700000100, now_hdr=1700000250,
                )
                assert cok == 200 and dok["data"]["tenant_id"] == "t_s", f"query-stripped HMAC + X-Now-Epoch failed: {cok} {dok}"
                cexp, dexp = cli.call(
                    "GET", "/api/v1/tenants/t_s/statement",
                    tenant="t_s", secret="sec_t_s", ts=1700000100, now_hdr=1700000405,
                )
                assert cexp == 401 and "EXPIRED" in dexp.get("error", {}).get("code", "")
                cbad_now, _ = cli.call(
                    "GET", "/api/v1/tenants/t_s/statement",
                    tenant="t_s", secret="sec_t_s", ts=1700000100, now_hdr="not_an_int",
                )
                assert cbad_now == 401
                cunk, dunk = cli.call("GET", "/api/v1/tenants/ghost/statement", tenant="ghost", secret="sec")
                assert cunk == 401 and "UNKNOWN_TENANT" in dunk.get("error", {}).get("code", "")
            run_check(w1, "w1_02_hmac_now_epoch_and_query_strip", c_w1_02)

            def c_w1_03():
                # Catches legacy bool/float disguised as int acceptance
                for tid in ("t_s", "t_g", "t_e"):
                    ct, dt = cli.call("POST", f"/api/v1/tenants/{tid}/topup", {"amount_cents": 100000, "now_epoch": 1700000010}, tenant=tid, secret=f"sec_{tid}")
                    assert ct == 200 and dt["data"]["balance_cents"] == 100000

                bad_calls = [
                    cli.call("POST", "/api/v1/tenants/t_s/topup", {"amount_cents": True}, tenant="t_s", secret="sec_t_s"),
                    cli.call("POST", "/api/v1/tenants/t_s/topup", {"amount_cents": 50.0}, tenant="t_s", secret="sec_t_s"),
                    cli.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": True, "submitted_epoch": 1700013600}, tenant="t_s", secret="sec_t_s"),
                    cli.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 10.5, "submitted_epoch": 1700013600}, tenant="t_s", secret="sec_t_s"),
                    cli.call("POST", "/api/v1/tenants/t_s/topup", raw_bytes=b"{broken", tenant="t_s", secret="sec_t_s"),
                ]
                for code, body in bad_calls:
                    assert code == 400 and body.get("ok") is False, f"expected 400 on bad type/json, got {code} {body}"
            run_check(w1, "w1_03_strict_type_and_bounds_guard", c_w1_03)

            def c_w1_04():
                # Exact .5 cent half-up vs Python float round() (banker's rounding: round(2.5)==2, round(12.5)==12)
                # 1) A10(2c) * 1s * STARTER(100%) * HIGH(125%) * OffPeak(100%) = 2.50 -> MUST be 3 cents (legacy round(2.5)==2!)
                c1, d1 = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_hu_1", "gpu_type": "A10", "priority": "HIGH", "estimated_gpu_sec": 1, "submitted_epoch": 1700013600},
                    tenant="t_s", secret="sec_t_s",
                )
                assert c1 == 201 and d1["data"]["reserved_cents"] == 3, f"2.50 half-up expected 3, got {d1}"
                cli.call("POST", "/api/v1/jobs/j_hu_1/cancel", {"now_epoch": 1700013602}, tenant="t_s", secret="sec_t_s")

                # 2) L40(5c) * 2s * STARTER(100%) * HIGH(125%) * OffPeak(100%) = 12.50 -> MUST be 13 cents (legacy round(12.5)==12!)
                c2, d2 = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_hu_2", "gpu_type": "L40", "priority": "HIGH", "estimated_gpu_sec": 2, "submitted_epoch": 1700013600},
                    tenant="t_s", secret="sec_t_s",
                )
                assert c2 == 201 and d2["data"]["reserved_cents"] == 13, f"12.50 half-up expected 13, got {d2}"
                cli.call("POST", "/api/v1/jobs/j_hu_2/cancel", {"now_epoch": 1700013602}, tenant="t_s", secret="sec_t_s")
            run_check(w1, "w1_04_half_up_vs_bankers_rounding", c_w1_04)

            def c_w1_05():
                # 3-bracket progressive duration: 0..600 @100%, 601..3600 @80%, >3600 @60%
                # H100(12c), 4000s, ENTERPRISE(80%), NORMAL(100%), OffPeak(100%):
                # (600*12 + 3000*12*0.8 + 400*12*0.6) * 0.8 = (7200 + 28800 + 2880) * 0.8 = 38880 * 0.8 = 31104
                c, d = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_3brk", "gpu_type": "H100", "priority": "NORMAL", "estimated_gpu_sec": 4000, "submitted_epoch": 1700013600},
                    tenant="t_e", secret="sec_t_e",
                )
                assert c == 201 and d["data"]["reserved_cents"] == 31104, f"3-bracket expected 31104, got {d}"
                cli.call("POST", "/api/v1/jobs/j_3brk/cancel", {"now_epoch": 1700013605}, tenant="t_e", secret="sec_t_e")
            run_check(w1, "w1_05_three_bracket_progressive_pricing", c_w1_05)

            def c_w1_06():
                # Peak UTC 12:00 (1700049600): NORMAL/HIGH * 120%, SPOT * 100% (exempt!)
                cp, dp = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_pk_h", "gpu_type": "L40", "priority": "HIGH", "estimated_gpu_sec": 100, "submitted_epoch": 1700049600},
                    tenant="t_g", secret="sec_t_g",
                )
                assert cp == 201 and dp["data"]["reserved_cents"] == 675, f"GROWTH peak HIGH expected 675, got {dp}"
                cli.call("POST", "/api/v1/jobs/j_pk_h/cancel", {"now_epoch": 1700049605}, tenant="t_g", secret="sec_t_g")

                cs, ds = cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_pk_spot", "gpu_type": "H100", "priority": "SPOT", "estimated_gpu_sec": 100, "submitted_epoch": 1700049600},
                    tenant="t_e", secret="sec_t_e",
                )
                assert cs == 201 and ds["data"]["reserved_cents"] == 672, f"SPOT peak exempt expected 672, got {ds}"
                cli.call("POST", "/api/v1/jobs/j_pk_spot/cancel", {"now_epoch": 1700049605}, tenant="t_e", secret="sec_t_e")
            run_check(w1, "w1_06_spot_peak_exemption", c_w1_06)

            def c_w1_07():
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_set_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600},
                    tenant="t_s", secret="sec_t_s",
                )
                cc, dc = cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_a", "now_epoch": 1700013610, "lease_seconds": 60}, admin=True)
                assert cc == 200 and dc["data"]["job"]["job_id"] == "j_set_1" and dc["data"]["job"]["status"] == "RUNNING"
                cs, ds = cli.call("POST", "/api/v1/jobs/j_set_1/settle", {"actual_gpu_sec": 150, "now_epoch": 1700013640}, tenant="t_s", secret="sec_t_s")
                assert cs == 200 and ds["data"]["final_charge_cents"] == 300 and ds["data"]["refunded_cents"] == 300
                assert ds["data"]["frozen_cents"] == 0 and ds["data"]["balance_cents"] == 99700
            run_check(w1, "w1_07_claim_and_partial_settle_refund", c_w1_07)

            def c_w1_08():
                # Running cancel on reserved=25 (L40=5c * 5s) -> 10% = 2.5 -> half-up 3 cents penalty!
                # Catches legacy round(2.5)==2, legacy stranded frozen_cents, AND legacy missing CANCEL_PENALTY in statement!
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_rc_25", "gpu_type": "L40", "priority": "NORMAL", "estimated_gpu_sec": 5, "submitted_epoch": 1700013600},
                    tenant="t_s", secret="sec_t_s",
                )
                cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_a", "now_epoch": 1700013610}, admin=True)
                cc, dc = cli.call("POST", "/api/v1/jobs/j_rc_25/cancel", {"now_epoch": 1700013620}, tenant="t_s", secret="sec_t_s")
                assert cc == 200 and dc["data"]["penalty_cents"] == 3 and dc["data"]["refunded_cents"] == 22, f"got {dc}"
                assert dc["data"]["frozen_cents"] == 0 and dc["data"]["balance_cents"] == 99697
                _, st = cli.call("GET", "/api/v1/tenants/t_s/statement", tenant="t_s", secret="sec_t_s")
                assert st["data"]["total_charged_cents"] == 303, f"expected total_charged 303 (300+3), got {st['data']}"
                assert st["data"]["used_gpu_sec"] == 150
            run_check(w1, "w1_08_running_cancel_penalty_and_statement", c_w1_08)

            def c_w1_09():
                cli.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_fail_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600},
                    tenant="t_s", secret="sec_t_s",
                )
                cli.call("POST", "/api/v1/workers/claim", {"worker_node_id": "node_a", "now_epoch": 1700013610}, admin=True)
                cf, df = cli.call("POST", "/api/v1/jobs/j_fail_1/fail", {"reason": "cuda_ecc", "now_epoch": 1700013620}, tenant="t_s", secret="sec_t_s")
                assert cf == 200 and df["data"]["status"] == "FAILED" and df["data"]["refunded_cents"] == 200
                assert df["data"]["frozen_cents"] == 0 and df["data"]["balance_cents"] == 99697
            run_check(w1, "w1_09_running_fail_full_refund", c_w1_09)

            def c_w1_10():
                ca, da = cli.call("GET", "/api/v1/admin/audit", admin=True)
                assert ca == 200 and da["data"]["all_balanced"] is True and da["data"]["anomalies"] == [], f"audit failed: {da}"
                assert da["data"]["total_frozen_cents"] == da["data"]["active_escrow_cents"] == 0
                assert da["data"]["total_platform_revenue_cents"] == 303
            run_check(w1, "w1_10_wave1_audit_reconciliation", c_w1_10)
        finally:
            srv1.shutdown()
            srv1.server_close()

    # =========================================================================
    # WAVE 2: Dirty Legacy v1 Migration & Deep Invariants (10 checks)
    # =========================================================================
    with tempfile.TemporaryDirectory(prefix="eval_w2_mig_") as tmp2_mig:
        db2_mig = str(Path(tmp2_mig) / "dirty_legacy_v1.db")
        rconn = sqlite3.connect(db2_mig)
        rconn.execute(
            "CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, name TEXT, tier TEXT, balance_cents INTEGER, api_secret TEXT, created_at INTEGER)"
        )
        rconn.execute(
            "CREATE TABLE ledger_entries (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, job_id TEXT, entry_type TEXT, delta_available INTEGER, balance_after INTEGER, created_at INTEGER)"
        )
        # Tenant 1: has matching historical TOPUP
        rconn.execute("INSERT INTO tenants VALUES ('leg_clean', 'Clean Legacy', 'GROWTH', 15000, 'sec_lc', 1690000000)")
        rconn.execute(
            "INSERT INTO ledger_entries (tenant_id, job_id, entry_type, delta_available, balance_after, created_at) VALUES ('leg_clean', NULL, 'TOPUP', 15000, 15000, 1690000000)"
        )
        # Tenant 2: Snapshot-imported v1 tenant whose historical balance (42000) came from a 50000 snapshot minus an 8000 historical SETTLE (no TOPUP row!)
        rconn.execute("INSERT INTO tenants VALUES ('leg_snap', 'Snapshot Legacy', 'ENTERPRISE', 42000, 'sec_ls', 1690000000)")
        rconn.execute(
            "INSERT INTO ledger_entries (tenant_id, job_id, entry_type, delta_available, balance_after, created_at) VALUES ('leg_snap', 'old_j0', 'SETTLE', -8000, 42000, 1690000500)"
        )
        rconn.commit()
        rconn.close()

        def c_w2_01():
            srv_m = create_server(db_path=db2_mig, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            threading.Thread(target=lambda: srv_m.serve_forever(poll_interval=0.04), daemon=True).start()
            try:
                cm = Client(f"http://127.0.0.1:{srv_m.server_address[1]}")
                c1, st1 = cm.call("GET", "/api/v1/tenants/leg_snap/statement", tenant="leg_snap", secret="sec_ls")
                assert c1 == 200 and st1["data"]["balance_cents"] == 42000 and st1["data"]["quota_gpu_sec"] == 18000 * 6, f"st1={st1}"
                # Audit right after migrating dirty v1 DB MUST be balanced!
                ca1, aud1 = cm.call("GET", "/api/v1/admin/audit", admin=True)
                assert ca1 == 200 and aud1["data"]["all_balanced"] is True, f"dirty legacy v1 audit failed: {aud1}"

                # Transact on migrated tenant and re-verify audit
                cj, _ = cm.call(
                    "POST", "/api/v1/jobs",
                    {"job_id": "j_leg_new", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013600},
                    tenant="leg_snap", secret="sec_ls",
                )
                assert cj == 201
                cm.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013610}, admin=True)
                cs, _ = cm.call("POST", "/api/v1/jobs/j_leg_new/settle", {"actual_gpu_sec": 100, "now_epoch": 1700013620}, tenant="leg_snap", secret="sec_ls")
                assert cs == 200
                ca2, aud2 = cm.call("GET", "/api/v1/admin/audit", admin=True)
                assert ca2 == 200 and aud2["data"]["all_balanced"] is True, f"post-settle legacy audit failed: {aud2}"

                # Verify audit is NOT hardcoded True: tamper DB balance by +1 cent and verify audit detects drift!
                tconn = sqlite3.connect(db2_mig)
                tconn.execute("UPDATE tenants SET balance_cents = balance_cents + 1 WHERE tenant_id = 'leg_snap'")
                tconn.commit()
                tconn.close()
                ca3, aud3 = cm.call("GET", "/api/v1/admin/audit", admin=True)
                assert ca3 == 200 and aud3["data"]["all_balanced"] is False and len(aud3["data"]["anomalies"]) >= 1, "audit failed to detect post-migration drift!"
            finally:
                srv_m.shutdown()
                srv_m.server_close()

        run_check(w2, "w2_01_dirty_legacy_v1_migration_and_baseline", c_w2_01)

    with tempfile.TemporaryDirectory(prefix="eval_w2_") as tmp2:
        db2 = str(Path(tmp2) / "w2.db")
        try:
            srv2 = create_server(db_path=db2, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            threading.Thread(target=lambda: srv2.serve_forever(poll_interval=0.04), daemon=True).start()
            cli2 = Client(f"http://127.0.0.1:{srv2.server_address[1]}")
        except Exception as exc:
            srv2 = None
            for cid in [
                "w2_02_voucher_validation_and_multi_path_release",
                "w2_03_below_min_spend_overrun_402_rollback",
                "w2_04_overrun_120pct_cap_vs_actual_quota",
                "w2_05_priority_tiebreak_and_expired_skip",
                "w2_06_reaper_requeue_preserves_escrow_and_voucher",
                "w2_07_reaper_exhaustion_releases_escrow_and_voucher",
                "w2_08_concurrency_and_quota_enforcement",
                "w2_09_illegal_state_transitions_matrix",
                "w2_10_wave2_global_audit_conservation",
            ]:
                w2.append({"id": cid, "passed": False, "detail": f"Wave 2 boot failed: {exc}"})

        if srv2 is not None:
            try:
                cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_a", "name": "A", "tier": "STARTER", "api_secret": "sec_a"}, admin=True)
                cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_b", "name": "B", "tier": "ENTERPRISE", "api_secret": "sec_b"}, admin=True)
                cli2.call("POST", "/api/v1/tenants/t_a/topup", {"amount_cents": 20000}, tenant="t_a", secret="sec_a")
                cli2.call("POST", "/api/v1/tenants/t_b/topup", {"amount_cents": 50000}, tenant="t_b", secret="sec_b")

                def c_w2_02():
                    cli2.call(
                        "POST", "/api/v1/admin/vouchers",
                        {"code": "V300", "tenant_id": "t_a", "discount_cents": 300, "min_spend_cents": 500, "expires_at_epoch": 1700050000},
                        admin=True,
                    )
                    # Wrong tenant -> 403, below min spend -> 400, expired -> 400
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 400, "submitted_epoch": 1700013600, "voucher_code": "V300"}, tenant="t_b", secret="sec_b")[0] == 403
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013600, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")[0] == 400
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700060000, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")[0] == 400

                    # Path 1: QUEUED -> EXPIRED via reap MUST release V300 (catches legacy reap voucher leak!)
                    c1, _ = cli2.call("POST", "/api/v1/jobs", {"job_id": "jv_exp_ttl", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600, "ttl_seconds": 10, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")
                    assert c1 == 201
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013601, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")[0] == 409
                    cli2.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013620}, admin=True)

                    # Path 2: RUNNING -> CANCELLED MUST release V300 (catches legacy running-cancel voucher leak!)
                    c2, _ = cli2.call("POST", "/api/v1/jobs", {"job_id": "jv_rcan", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013630, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")
                    assert c2 == 201
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013635}, admin=True)
                    cli2.call("POST", "/api/v1/jobs/jv_rcan/cancel", {"now_epoch": 1700013640}, tenant="t_a", secret="sec_a")

                    # Reuse V300 and settle SUCCEEDED -> permanently consumed
                    c3, _ = cli2.call("POST", "/api/v1/jobs", {"job_id": "jv_fin", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013645, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")
                    assert c3 == 201
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013650}, admin=True)
                    cli2.call("POST", "/api/v1/jobs/jv_fin/settle", {"actual_gpu_sec": 300, "now_epoch": 1700013660}, tenant="t_a", secret="sec_a")
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013670, "voucher_code": "V300"}, tenant="t_a", secret="sec_a")[0] == 409
                run_check(w2, "w2_02_voucher_validation_and_multi_path_release", c_w2_02)

                def c_w2_03():
                    # Below-min-spend reversal on settle triggers overrun 402 rollback, then topup settles & consumes voucher!
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_vtrap", "name": "VT", "tier": "STARTER", "api_secret": "sec_vt"}, admin=True)
                    cli2.call("POST", "/api/v1/tenants/t_vtrap/topup", {"amount_cents": 250}, tenant="t_vtrap", secret="sec_vt")
                    cli2.call("POST", "/api/v1/admin/vouchers", {"code": "VTRAP", "tenant_id": "t_vtrap", "discount_cents": 400, "min_spend_cents": 600, "expires_at_epoch": 1700090000}, admin=True)
                    # Estimate 300s * 2c = 600 >= 600 -> discount 400 -> reserved = 200, balance left = 50
                    cs, ds = cli2.call("POST", "/api/v1/jobs", {"job_id": "j_vtrap", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013600, "voucher_code": "VTRAP"}, tenant="t_vtrap", secret="sec_vt")
                    assert cs == 201 and ds["data"]["reserved_cents"] == 200
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013610}, admin=True)

                    # Settle at actual=200s -> actual_pre=400 < min_spend(600) -> discount drops to 0!
                    # final_charge=400 > reserved(200) -> needs extra=200 > balance(50) -> MUST 402 & rollback cleanly!
                    c402, _ = cli2.call("POST", "/api/v1/jobs/j_vtrap/settle", {"actual_gpu_sec": 200, "now_epoch": 1700013620}, tenant="t_vtrap", secret="sec_vt")
                    assert c402 == 402
                    _, j_mid = cli2.call("GET", "/api/v1/jobs/j_vtrap", tenant="t_vtrap", secret="sec_vt")
                    _, st_mid = cli2.call("GET", "/api/v1/tenants/t_vtrap/statement", tenant="t_vtrap", secret="sec_vt")
                    assert j_mid["data"]["status"] == "RUNNING" and st_mid["data"]["used_gpu_sec"] == 0 and st_mid["data"]["frozen_cents"] == 200

                    # Topup 200 (balance=250) and settle again -> succeeds with final_charge=400, extra=200, balance=50, voucher consumed!
                    cli2.call("POST", "/api/v1/tenants/t_vtrap/topup", {"amount_cents": 200}, tenant="t_vtrap", secret="sec_vt")
                    cok, dok = cli2.call("POST", "/api/v1/jobs/j_vtrap/settle", {"actual_gpu_sec": 200, "now_epoch": 1700013630}, tenant="t_vtrap", secret="sec_vt")
                    assert cok == 200 and dok["data"]["final_charge_cents"] == 400 and dok["data"]["extra_charged_cents"] == 200 and dok["data"]["balance_cents"] == 50
                    # Voucher VTRAP must NOT be reusable after SUCCEEDED even though discount was 0!
                    cli2.call("POST", "/api/v1/tenants/t_vtrap/topup", {"amount_cents": 1000}, tenant="t_vtrap", secret="sec_vt")
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 300, "submitted_epoch": 1700013640, "voucher_code": "VTRAP"}, tenant="t_vtrap", secret="sec_vt")[0] == 409
                run_check(w2, "w2_03_below_min_spend_overrun_402_rollback", c_w2_03)

                def c_w2_04():
                    # estimated=105 -> 120% floor = 126s; actual=300 -> billable=126, used_gpu_sec increments by 300!
                    cli2.call("POST", "/api/v1/jobs", {"job_id": "j_cap120", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 105, "submitted_epoch": 1700013600}, tenant="t_a", secret="sec_a")
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013610}, admin=True)
                    _, before_st = cli2.call("GET", "/api/v1/tenants/t_a/statement", tenant="t_a", secret="sec_a")
                    u0 = before_st["data"]["used_gpu_sec"]
                    cs, ds = cli2.call("POST", "/api/v1/jobs/j_cap120/settle", {"actual_gpu_sec": 300, "now_epoch": 1700013650}, tenant="t_a", secret="sec_a")
                    assert cs == 200 and ds["data"]["billable_gpu_sec"] == 126 and ds["data"]["final_charge_cents"] == 252
                    _, after_st = cli2.call("GET", "/api/v1/tenants/t_a/statement", tenant="t_a", secret="sec_a")
                    assert after_st["data"]["used_gpu_sec"] - u0 == 300
                run_check(w2, "w2_04_overrun_120pct_cap_vs_actual_quota", c_w2_04)

                def c_w2_05():
                    # Submit an already-soon-to-expire HIGH job + 4 valid jobs
                    cli2.call("POST", "/api/v1/jobs", {"job_id": "jq_exp_h", "gpu_type": "A10", "priority": "HIGH", "estimated_gpu_sec": 10, "submitted_epoch": 1700013700, "ttl_seconds": 5}, tenant="t_b", secret="sec_b")
                    for jid, prio, sub_t in [
                        ("jq_s", "SPOT", 1700013701),
                        ("jq_n", "NORMAL", 1700013701),
                        ("jq_h_b", "HIGH", 1700013702),
                        ("jq_h_a", "HIGH", 1700013702),
                    ]:
                        assert cli2.call("POST", "/api/v1/jobs", {"job_id": jid, "gpu_type": "A10", "priority": prio, "estimated_gpu_sec": 10, "submitted_epoch": sub_t}, tenant="t_b", secret="sec_b")[0] == 201
                    claimed = []
                    for _ in range(4):
                        _, cd = cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013720}, admin=True)
                        claimed.append(cd["data"]["job"]["job_id"])
                    assert claimed == ["jq_h_a", "jq_h_b", "jq_n", "jq_s"], f"got {claimed}"
                    for jid in claimed:
                        cli2.call("POST", f"/api/v1/jobs/{jid}/settle", {"actual_gpu_sec": 10, "now_epoch": 1700013730}, tenant="t_b", secret="sec_b")
                    cli2.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013735}, admin=True)
                run_check(w2, "w2_05_priority_tiebreak_and_expired_skip", c_w2_05)

                def c_w2_06():
                    cli2.call("POST", "/api/v1/admin/vouchers", {"code": "VREAP", "tenant_id": "t_b", "discount_cents": 40, "min_spend_cents": 100, "expires_at_epoch": 1700090000}, admin=True)
                    cli2.call("POST", "/api/v1/jobs", {"job_id": "j_reap_rw", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013800, "ttl_seconds": 50, "max_retries": 1, "voucher_code": "VREAP"}, tenant="t_b", secret="sec_b")
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "dead_node", "now_epoch": 1700013810, "lease_seconds": 20}, admin=True)
                    _, st0 = cli2.call("GET", "/api/v1/tenants/t_b/statement", tenant="t_b", secret="sec_b")
                    frz0 = st0["data"]["frozen_cents"]
                    cr, dr = cli2.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013840}, admin=True)
                    assert cr == 200 and dr["data"]["requeued_count"] == 1
                    _, st1 = cli2.call("GET", "/api/v1/tenants/t_b/statement", tenant="t_b", secret="sec_b")
                    assert st1["data"]["frozen_cents"] == frz0 and frz0 > 0, f"requeue leaked escrow: {frz0} -> {st1['data']['frozen_cents']}"
                    # Voucher must still be locked
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013845, "voucher_code": "VREAP"}, tenant="t_b", secret="sec_b")[0] == 409
                run_check(w2, "w2_06_reaper_requeue_preserves_escrow_and_voucher", c_w2_06)

                def c_w2_07():
                    cli2.call("POST", "/api/v1/workers/claim", {"worker_node_id": "dead_node2", "now_epoch": 1700013850, "lease_seconds": 20}, admin=True)
                    cr, dr = cli2.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013880}, admin=True)
                    assert cr == 200 and dr["data"]["failed_count"] == 1
                    # Now VREAP must be released and reusable!
                    cre, _ = cli2.call("POST", "/api/v1/jobs", {"job_id": "j_vreap_ok", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013890, "voucher_code": "VREAP"}, tenant="t_b", secret="sec_b")
                    assert cre == 201
                    cli2.call("POST", "/api/v1/jobs/j_vreap_ok/cancel", {"now_epoch": 1700013895}, tenant="t_b", secret="sec_b")
                run_check(w2, "w2_07_reaper_exhaustion_releases_escrow_and_voucher", c_w2_07)

                def c_w2_08():
                    cli2.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_q", "name": "Q", "tier": "STARTER", "api_secret": "sec_q"}, admin=True)
                    cli2.call("POST", "/api/v1/tenants/t_q/topup", {"amount_cents": 50000}, tenant="t_q", secret="sec_q")
                    assert cli2.call("POST", "/api/v1/jobs", {"gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 3601, "submitted_epoch": 1700013900}, tenant="t_q", secret="sec_q")[0] == 409
                    assert cli2.call("POST", "/api/v1/jobs", {"job_id": "jq1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 2000, "submitted_epoch": 1700013900}, tenant="t_q", secret="sec_q")[0] == 201
                    # Remaining quota = 1600, so 1700 -> 409 QUOTA_EXCEEDED
                    cq, dq = cli2.call("POST", "/api/v1/jobs", {"job_id": "jq_over", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 1700, "submitted_epoch": 1700013901}, tenant="t_q", secret="sec_q")
                    assert cq == 409 and "QUOTA" in dq.get("error", {}).get("code", "")
                    assert cli2.call("POST", "/api/v1/jobs", {"job_id": "jq2", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 1000, "submitted_epoch": 1700013902}, tenant="t_q", secret="sec_q")[0] == 201
                    # 3rd active job on STARTER -> 409 CONCURRENCY_LIMIT_EXCEEDED
                    cc, dc = cli2.call("POST", "/api/v1/jobs", {"job_id": "jq3", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013903}, tenant="t_q", secret="sec_q")
                    assert cc == 409 and "CONCURRENCY" in dc.get("error", {}).get("code", "")
                run_check(w2, "w2_08_concurrency_and_quota_enforcement", c_w2_08)

                def c_w2_09():
                    # jq1 is QUEUED -> settle/fail must 409
                    assert cli2.call("POST", "/api/v1/jobs/jq1/settle", {"actual_gpu_sec": 10}, tenant="t_q", secret="sec_q")[0] == 409
                    assert cli2.call("POST", "/api/v1/jobs/jq1/fail", {"reason": "x"}, tenant="t_q", secret="sec_q")[0] == 409
                    # jv_fin is SUCCEEDED, j_reap_rw is FAILED, j_vreap_ok is CANCELLED, jv_exp_ttl is EXPIRED
                    for tid, sec, jid in [("t_a", "sec_a", "jv_fin"), ("t_b", "sec_b", "j_reap_rw"), ("t_b", "sec_b", "j_vreap_ok"), ("t_a", "sec_a", "jv_exp_ttl")]:
                        assert cli2.call("POST", f"/api/v1/jobs/{jid}/cancel", {}, tenant=tid, secret=sec)[0] == 409
                        assert cli2.call("POST", f"/api/v1/jobs/{jid}/settle", {"actual_gpu_sec": 10}, tenant=tid, secret=sec)[0] == 409
                run_check(w2, "w2_09_illegal_state_transitions_matrix", c_w2_09)

                def c_w2_10():
                    ca, da = cli2.call("GET", "/api/v1/admin/audit", admin=True)
                    assert ca == 200 and da["data"]["all_balanced"] is True and da["data"]["anomalies"] == [], f"w2 audit failed: {da}"
                    # Active queued jq1(600*2 + 1400*1.6 = 3440) + jq2(600*2 + 400*1.6 = 1840) = 5280 frozen
                    assert da["data"]["total_frozen_cents"] == da["data"]["active_escrow_cents"] == 5280, f"got {da['data']}"
                run_check(w2, "w2_10_wave2_global_audit_conservation", c_w2_10)
            finally:
                srv2.shutdown()
                srv2.server_close()

    # =========================================================================
    # WAVE 3: Phase 2 Evolution — Preemption Subsidy & Org Shared Pool (10 checks)
    # =========================================================================
    with tempfile.TemporaryDirectory(prefix="eval_w3_") as tmp3:
        db3 = str(Path(tmp3) / "w3_phase2.db")
        try:
            srv3 = create_server(db_path=db3, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            threading.Thread(target=lambda: srv3.serve_forever(poll_interval=0.04), daemon=True).start()
            cli3 = Client(f"http://127.0.0.1:{srv3.server_address[1]}")
        except Exception as exc:
            srv3 = None
            for cid in [
                "w3_01_preempt_guard_conditions",
                "w3_02_preempt_latest_spot_subsidy_and_handover",
                "w3_03_preempt_zero_reserved_voucher_subsidy_and_floor",
                "w3_04_org_bind_hierarchy_validation",
                "w3_05_org_child_submit_shortfall_auto_transfer",
                "w3_06_org_child_settle_overrun_auto_transfer",
                "w3_07_org_credit_limit_and_active_rebind_guards",
                "w3_08_org_cancel_refund_stays_in_child",
                "w3_09_phase2_statement_extended_fields",
                "w3_10_phase2_audit_subsidy_and_org_conservation",
            ]:
                w3.append({"id": cid, "passed": False, "detail": f"Wave 3 boot failed: {exc}"})

        if srv3 is not None:
            try:
                cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_spot", "name": "SpotUser", "tier": "GROWTH", "api_secret": "sec_sp"}, admin=True)
                cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_vip", "name": "VipUser", "tier": "ENTERPRISE", "api_secret": "sec_vp"}, admin=True)
                cli3.call("POST", "/api/v1/tenants/t_spot/topup", {"amount_cents": 10000}, tenant="t_spot", secret="sec_sp")
                cli3.call("POST", "/api/v1/tenants/t_vip/topup", {"amount_cents": 20000}, tenant="t_vip", secret="sec_vp")

                def c_w3_01():
                    # Unauthenticated -> 401
                    assert cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n1", "now_epoch": 1700013600})[0] == 401
                    # No HIGH queued -> 409 PREEMPTION_NOT_APPLICABLE
                    c_no_h, d_no_h = cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n1", "now_epoch": 1700013600}, admin=True)
                    assert c_no_h == 409 and "PREEMPT" in d_no_h.get("error", {}).get("code", "")

                    # Start a NORMAL job on node, queue a HIGH job -> still no RUNNING SPOT -> 409
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "j_norm_run", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 50, "submitted_epoch": 1700013600}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013605}, admin=True)
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "j_high_wait1", "gpu_type": "H100", "priority": "HIGH", "estimated_gpu_sec": 100, "submitted_epoch": 1700013610}, tenant="t_vip", secret="sec_vp")

                    assert cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n1", "now_epoch": 1700013615}, admin=True)[0] == 409
                    assert cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n1", "now_epoch": 1700013615, "evict_job_id": "j_norm_run"}, admin=True)[0] == 409
                    assert cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n1", "now_epoch": 1700013615, "evict_job_id": "missing_job"}, admin=True)[0] == 404
                    cli3.call("POST", "/api/v1/jobs/j_norm_run/settle", {"actual_gpu_sec": 50, "now_epoch": 1700013616}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/jobs/j_high_wait1/cancel", {"now_epoch": 1700013616}, tenant="t_vip", secret="sec_vp")
                run_check(w3, "w3_01_preempt_guard_conditions", c_w3_01)

                def c_w3_02():
                    # Issue voucher V_SP to test voucher release on preemption
                    cli3.call("POST", "/api/v1/admin/vouchers", {"code": "V_SP", "tenant_id": "t_spot", "discount_cents": 20, "min_spend_cents": 50, "expires_at_epoch": 1700090000}, admin=True)
                    # Start two RUNNING SPOT jobs: s_early (submitted 1700013700) and s_late (submitted 1700013710, with V_SP)
                    # For s_late: L40(5c) * 20s = 100c * GROWTH(90%) * SPOT(70%) = 63c; minus V_SP(20c) => reserved_cents = 43c!
                    # 15% of 43c = 6.45 -> 6 cents; let's use estimated_gpu_sec=22 -> 110*0.63 = 69.3->69 - 19 = 50 -> 50 * 15% = 7.50 -> half-up 8 cents!
                    cli3.call("POST", "/api/v1/admin/vouchers", {"code": "V_SP19", "tenant_id": "t_spot", "discount_cents": 19, "min_spend_cents": 50, "expires_at_epoch": 1700090000}, admin=True)
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "s_early", "gpu_type": "L40", "priority": "SPOT", "estimated_gpu_sec": 20, "submitted_epoch": 1700013700}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "s_late", "gpu_type": "L40", "priority": "SPOT", "estimated_gpu_sec": 22, "submitted_epoch": 1700013710, "voucher_code": "V_SP19"}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n_s1", "now_epoch": 1700013712}, admin=True)
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n_s2", "now_epoch": 1700013713}, admin=True)

                    # Queue HIGH job h_vip_1
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "h_vip_1", "gpu_type": "H100", "priority": "HIGH", "estimated_gpu_sec": 50, "submitted_epoch": 1700013715}, tenant="t_vip", secret="sec_vp")
                    _, st_before = cli3.call("GET", "/api/v1/tenants/t_spot/statement", tenant="t_spot", secret="sec_sp")
                    bal_before = st_before["data"]["balance_cents"]

                    cp, dp = cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n_s2", "now_epoch": 1700013720, "lease_seconds": 90}, admin=True)
                    assert cp == 200, f"preempt failed: {cp} {dp}"
                    pdata = dp["data"]
                    assert pdata["evicted_job_id"] == "s_late" and pdata["evicted_tenant_id"] == "t_spot", f"wrong victim: {pdata}"
                    assert pdata["refunded_cents"] == 50 and pdata["compensation_cents"] == 8, f"expected refund=50 comp=8 (7.5 half-up), got {pdata}"
                    assert pdata["claimed_job"]["job_id"] == "h_vip_1" and pdata["claimed_job"]["status"] == "RUNNING"

                    _, st_after = cli3.call("GET", "/api/v1/tenants/t_spot/statement", tenant="t_spot", secret="sec_sp")
                    assert st_after["data"]["balance_cents"] == bal_before + 50 + 8
                    # PREEMPTED is terminal -> cancel/settle must 409
                    assert cli3.call("POST", "/api/v1/jobs/s_late/cancel", {}, tenant="t_spot", secret="sec_sp")[0] == 409
                    # V_SP19 must be released and reusable!
                    cre, _ = cli3.call("POST", "/api/v1/jobs", {"job_id": "s_re_v", "gpu_type": "L40", "priority": "SPOT", "estimated_gpu_sec": 22, "submitted_epoch": 1700013725, "voucher_code": "V_SP19"}, tenant="t_spot", secret="sec_sp")
                    assert cre == 201
                    cli3.call("POST", "/api/v1/jobs/s_re_v/cancel", {"now_epoch": 1700013726}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/jobs/s_early/settle", {"actual_gpu_sec": 20, "now_epoch": 1700013727}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/jobs/h_vip_1/settle", {"actual_gpu_sec": 50, "now_epoch": 1700013728}, tenant="t_vip", secret="sec_vp")
                run_check(w3, "w3_02_preempt_latest_spot_subsidy_and_handover", c_w3_02)

                def c_w3_03():
                    # 1) Full-voucher SPOT job where reserved_cents == 0 -> subsidy = 15% of pre_voucher_cents!
                    # A10(2c)*100s=200 * GROWTH(90%) * SPOT(70%) = 126c; voucher 200c -> reserved = 0, pre_voucher = 126c -> 15% = 18.9 -> 19c!
                    cli3.call("POST", "/api/v1/admin/vouchers", {"code": "V_FULL", "tenant_id": "t_spot", "discount_cents": 200, "min_spend_cents": 100, "expires_at_epoch": 1700090000}, admin=True)
                    cs, ds = cli3.call("POST", "/api/v1/jobs", {"job_id": "s_zero_res", "gpu_type": "A10", "priority": "SPOT", "estimated_gpu_sec": 100, "submitted_epoch": 1700013750, "voucher_code": "V_FULL"}, tenant="t_spot", secret="sec_sp")
                    assert cs == 201 and ds["data"]["reserved_cents"] == 0 and ds["data"]["pre_voucher_cents"] == 126
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n_z", "now_epoch": 1700013752}, admin=True)
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "h_vip_2", "gpu_type": "A10", "priority": "HIGH", "estimated_gpu_sec": 10, "submitted_epoch": 1700013755}, tenant="t_vip", secret="sec_vp")
                    cp, dp = cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n_z", "now_epoch": 1700013760}, admin=True)
                    assert cp == 200 and dp["data"]["refunded_cents"] == 0 and dp["data"]["compensation_cents"] == 19, f"got {dp}"
                    cli3.call("POST", "/api/v1/jobs/h_vip_2/settle", {"actual_gpu_sec": 10, "now_epoch": 1700013765}, tenant="t_vip", secret="sec_vp")

                    # 2) Minimum 1-cent floor: A10(2c)*1s * GROWTH(90%)*SPOT(70%) = 1.26->1c; 1c * 15% = 0.15 -> floor 1 cent!
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "s_tiny", "gpu_type": "A10", "priority": "SPOT", "estimated_gpu_sec": 1, "submitted_epoch": 1700013770}, tenant="t_spot", secret="sec_sp")
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n_z", "now_epoch": 1700013772}, admin=True)
                    cli3.call("POST", "/api/v1/jobs", {"job_id": "h_vip_3", "gpu_type": "A10", "priority": "HIGH", "estimated_gpu_sec": 10, "submitted_epoch": 1700013775}, tenant="t_vip", secret="sec_vp")
                    cp2, dp2 = cli3.call("POST", "/api/v1/workers/preempt", {"worker_node_id": "n_z", "now_epoch": 1700013780}, admin=True)
                    assert cp2 == 200 and dp2["data"]["compensation_cents"] == 1, f"floor 1c failed: {dp2}"
                    cli3.call("POST", "/api/v1/jobs/h_vip_3/settle", {"actual_gpu_sec": 10, "now_epoch": 1700013785}, tenant="t_vip", secret="sec_vp")
                run_check(w3, "w3_03_preempt_zero_reserved_voucher_subsidy_and_floor", c_w3_03)

                def c_w3_04():
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "p_corp", "name": "CorpParent", "tier": "ENTERPRISE", "api_secret": "sec_p"}, admin=True)
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "p_corp2", "name": "CorpParent2", "tier": "ENTERPRISE", "api_secret": "sec_p2"}, admin=True)
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "c_sub1", "name": "Sub1", "tier": "STARTER", "api_secret": "sec_c1"}, admin=True)
                    cli3.call("POST", "/api/v1/admin/tenants", {"tenant_id": "c_sub2", "name": "Sub2", "tier": "GROWTH", "api_secret": "sec_c2"}, admin=True)
                    cli3.call("POST", "/api/v1/tenants/p_corp/topup", {"amount_cents": 5000}, tenant="p_corp", secret="sec_p")

                    # Self-bind -> 400; non-ENTERPRISE parent -> 409; ENTERPRISE as child -> 409
                    assert cli3.call("POST", "/api/v1/admin/tenants/p_corp/bind-parent", {"parent_tenant_id": "p_corp", "credit_limit_cents": 500}, admin=True)[0] == 400
                    assert cli3.call("POST", "/api/v1/admin/tenants/c_sub1/bind-parent", {"parent_tenant_id": "c_sub2", "credit_limit_cents": 500}, admin=True)[0] == 409
                    assert cli3.call("POST", "/api/v1/admin/tenants/p_corp2/bind-parent", {"parent_tenant_id": "p_corp", "credit_limit_cents": 500}, admin=True)[0] == 409

                    cb1, db_1 = cli3.call("POST", "/api/v1/admin/tenants/c_sub1/bind-parent", {"parent_tenant_id": "p_corp", "credit_limit_cents": 1000}, admin=True)
                    cb2, _ = cli3.call("POST", "/api/v1/admin/tenants/c_sub2/bind-parent", {"parent_tenant_id": "p_corp", "credit_limit_cents": 800}, admin=True)
                    assert cb1 == 200 and cb2 == 200 and db_1["data"]["parent_tenant_id"] == "p_corp" and db_1["data"]["org_drawn_cents"] == 0
                run_check(w3, "w3_04_org_bind_hierarchy_validation", c_w3_04)

                def c_w3_05():
                    # c_sub1 (STARTER=100% rate!) has 150 own cents; submits 200s A10 = 400c -> draws shortfall 250 from p_corp!
                    cli3.call("POST", "/api/v1/tenants/c_sub1/topup", {"amount_cents": 150}, tenant="c_sub1", secret="sec_c1")
                    cs, ds = cli3.call("POST", "/api/v1/jobs", {"job_id": "j_org_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013800}, tenant="c_sub1", secret="sec_c1")
                    assert cs == 201 and ds["data"]["reserved_cents"] == 400
                    _, st_p = cli3.call("GET", "/api/v1/tenants/p_corp/statement", tenant="p_corp", secret="sec_p")
                    _, st_c = cli3.call("GET", "/api/v1/tenants/c_sub1/statement", tenant="c_sub1", secret="sec_c1")
                    assert st_p["data"]["balance_cents"] == 4750
                    assert st_c["data"]["balance_cents"] == 0 and st_c["data"]["frozen_cents"] == 400 and st_c["data"]["org_drawn_cents"] == 250
                run_check(w3, "w3_05_org_child_submit_shortfall_auto_transfer", c_w3_05)

                def c_w3_06():
                    # Claim j_org_1 and settle with overrun actual=240s -> 480c (extra=80c, c_sub1 own balance=0) -> draws 80c from p_corp!
                    cli3.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n_o1", "now_epoch": 1700013810}, admin=True)
                    cs, ds = cli3.call("POST", "/api/v1/jobs/j_org_1/settle", {"actual_gpu_sec": 240, "now_epoch": 1700013830}, tenant="c_sub1", secret="sec_c1")
                    assert cs == 200 and ds["data"]["final_charge_cents"] == 480 and ds["data"]["extra_charged_cents"] == 80
                    _, st_p = cli3.call("GET", "/api/v1/tenants/p_corp/statement", tenant="p_corp", secret="sec_p")
                    _, st_c = cli3.call("GET", "/api/v1/tenants/c_sub1/statement", tenant="c_sub1", secret="sec_c1")
                    assert st_p["data"]["balance_cents"] == 4670 and st_c["data"]["org_drawn_cents"] == 330
                run_check(w3, "w3_06_org_child_settle_overrun_auto_transfer", c_w3_06)

                def c_w3_07():
                    # Remaining credit on c_sub1 is 1000 - 330 = 670c -> submitting 350s A10 (700c) must fail 402 & rollback!
                    c402, _ = cli3.call("POST", "/api/v1/jobs", {"job_id": "j_org_overlim", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 350, "submitted_epoch": 1700013840}, tenant="c_sub1", secret="sec_c1")
                    assert c402 == 402
                    # Lowering credit_limit_cents below 330 -> 409 INVALID_ORG_BINDING
                    assert cli3.call("POST", "/api/v1/admin/tenants/c_sub1/bind-parent", {"parent_tenant_id": "p_corp", "credit_limit_cents": 200}, admin=True)[0] == 409
                    # Submit active QUEUED job on c_sub1 (100s = 200c -> drawn becomes 530), then try switching parent -> 409!
                    assert cli3.call("POST", "/api/v1/jobs", {"job_id": "j_org_act", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013845}, tenant="c_sub1", secret="sec_c1")[0] == 201
                    assert cli3.call("POST", "/api/v1/admin/tenants/c_sub1/bind-parent", {"parent_tenant_id": "p_corp2", "credit_limit_cents": 2000}, admin=True)[0] == 409
                    cli3.call("POST", "/api/v1/jobs/j_org_act/cancel", {"now_epoch": 1700013848}, tenant="c_sub1", secret="sec_c1")
                run_check(w3, "w3_07_org_credit_limit_and_active_rebind_guards", c_w3_07)

                def c_w3_08():
                    # After cancelling j_org_act above, 200c stays in c_sub1.balance_cents and org_drawn_cents stays 530!
                    _, st_c = cli3.call("GET", "/api/v1/tenants/c_sub1/statement", tenant="c_sub1", secret="sec_c1")
                    assert st_c["data"]["balance_cents"] == 200 and st_c["data"]["org_drawn_cents"] == 530
                run_check(w3, "w3_08_org_cancel_refund_stays_in_child", c_w3_08)

                def c_w3_09():
                    _, st_sp = cli3.call("GET", "/api/v1/tenants/t_spot/statement", tenant="t_spot", secret="sec_sp")
                    _, st_c1 = cli3.call("GET", "/api/v1/tenants/c_sub1/statement", tenant="c_sub1", secret="sec_c1")
                    # t_spot received subsidies: 8 + 19 + 1 = 28 cents!
                    assert st_sp["data"]["total_subsidy_cents"] == 28 and st_sp["data"]["parent_tenant_id"] is None
                    assert st_c1["data"]["parent_tenant_id"] == "p_corp" and st_c1["data"]["credit_limit_cents"] == 1000 and st_c1["data"]["org_drawn_cents"] == 530
                run_check(w3, "w3_09_phase2_statement_extended_fields", c_w3_09)

                def c_w3_10():
                    ca, da = cli3.call("GET", "/api/v1/admin/audit", admin=True)
                    assert ca == 200, f"audit status {ca}: {da}"
                    aud = da["data"]
                    assert aud["all_balanced"] is True and aud["org_transfer_balanced"] is True and aud["anomalies"] == [], f"w3 audit failed: {aud}"
                    assert aud["total_subsidy_cents"] == 28
                    assert aud["net_platform_revenue_cents"] == aud["total_platform_revenue_cents"] - 28
                    # Total topups in w3 = 10000 + 20000 + 5000 + 150 = 35150
                    assert aud["total_available_cents"] + aud["total_frozen_cents"] + aud["net_platform_revenue_cents"] == 35150, f"conservation mismatch: {aud}"
                run_check(w3, "w3_10_phase2_audit_subsidy_and_org_conservation", c_w3_10)
            finally:
                srv3.shutdown()
                srv3.server_close()

    # =========================================================================
    # WAVE 4: Security Order-of-Ops, Idempotency Poisoning & Chaos Storms (10 checks)
    # =========================================================================
    with tempfile.TemporaryDirectory(prefix="eval_w4_") as tmp4:
        db4 = str(Path(tmp4) / "w4_chaos.db")
        try:
            srv4 = create_server(db_path=db4, host="127.0.0.1", port=0, admin_token=ADMIN_TOKEN)
            threading.Thread(target=lambda: srv4.serve_forever(poll_interval=0.03), daemon=True).start()
            cli4 = Client(f"http://127.0.0.1:{srv4.server_address[1]}")
        except Exception as exc:
            srv4 = None
            for cid in [
                "w4_01_cross_tenant_idempotency_sniffing_blocked",
                "w4_02_same_idem_key_scoped_per_tenant_and_route",
                "w4_03_failed_4xx_does_not_poison_idempotency_key",
                "w4_04_unauthenticated_idempotency_probe_rejected",
                "w4_05_concurrent_claim_single_assignment_race",
                "w4_06_concurrent_submit_balance_and_concurrency_race",
                "w4_07_concurrent_multi_child_shared_parent_pool_race",
                "w4_08_concurrent_idempotent_double_click_storm",
                "w4_09_mixed_chaos_preempt_reap_cancel_storm",
                "w4_10_post_chaos_zero_drift_full_audit",
            ]:
                w4.append({"id": cid, "passed": False, "detail": f"Wave 4 boot failed: {exc}"})

        if srv4 is not None:
            try:
                cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_sec_a", "name": "SecA", "tier": "ENTERPRISE", "api_secret": "sec_sa"}, admin=True)
                cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_sec_b", "name": "SecB", "tier": "STARTER", "api_secret": "sec_sb"}, admin=True)
                cli4.call("POST", "/api/v1/tenants/t_sec_a/topup", {"amount_cents": 20000}, tenant="t_sec_a", secret="sec_sa")
                cli4.call("POST", "/api/v1/tenants/t_sec_b/topup", {"amount_cents": 5000}, tenant="t_sec_b", secret="sec_sb")

                def c_w4_01():
                    # Tenant A settles job with Idempotency-Key: k_sniff_1
                    cli4.call("POST", "/api/v1/jobs", {"job_id": "ja_sec1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 50, "submitted_epoch": 1700013600}, tenant="t_sec_a", secret="sec_sa")
                    cli4.call("POST", "/api/v1/workers/claim", {"worker_node_id": "n1", "now_epoch": 1700013605}, admin=True)
                    ca, _ = cli4.call("POST", "/api/v1/jobs/ja_sec1/settle", {"actual_gpu_sec": 50, "now_epoch": 1700013610}, tenant="t_sec_a", secret="sec_sa", idem="k_sniff_1")
                    assert ca == 200
                    # Tenant B tries to sniff ja_sec1 using same Idempotency-Key: k_sniff_1 -> MUST 403 (NOT 200/409!)
                    cb, _ = cli4.call("POST", "/api/v1/jobs/ja_sec1/settle", {"actual_gpu_sec": 50, "now_epoch": 1700013610}, tenant="t_sec_b", secret="sec_sb", idem="k_sniff_1")
                    assert cb == 403, f"cross-tenant idempotency leak! expected 403, got {cb}"
                    # Non-existent job with same Idempotency-Key -> MUST 404
                    cn, _ = cli4.call("POST", "/api/v1/jobs/no_such_job_xyz/settle", {"actual_gpu_sec": 50, "now_epoch": 1700013610}, tenant="t_sec_a", secret="sec_sa", idem="k_sniff_1")
                    assert cn == 404, f"expected 404 before idempotency, got {cn}"
                run_check(w4, "w4_01_cross_tenant_idempotency_sniffing_blocked", c_w4_01)

                def c_w4_02():
                    # Same Idempotency-Key string used by Tenant A and Tenant B on their own routes with different payloads -> both 201!
                    c1, _ = cli4.call("POST", "/api/v1/jobs", {"job_id": "ja_idem_scope", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 40, "submitted_epoch": 1700013620}, tenant="t_sec_a", secret="sec_sa", idem="common_key_01")
                    c2, _ = cli4.call("POST", "/api/v1/jobs", {"job_id": "jb_idem_scope", "gpu_type": "L40", "priority": "HIGH", "estimated_gpu_sec": 10, "submitted_epoch": 1700013620}, tenant="t_sec_b", secret="sec_sb", idem="common_key_01")
                    assert c1 == 201 and c2 == 201
                    cli4.call("POST", "/api/v1/jobs/ja_idem_scope/cancel", {}, tenant="t_sec_a", secret="sec_sa")
                    cli4.call("POST", "/api/v1/jobs/jb_idem_scope/cancel", {}, tenant="t_sec_b", secret="sec_sb")
                run_check(w4, "w4_02_same_idem_key_scoped_per_tenant_and_route", c_w4_02)

                def c_w4_03():
                    # 4xx error MUST NOT poison Idempotency-Key!
                    cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_pois", "name": "Pois", "tier": "STARTER", "api_secret": "sec_po"}, admin=True)
                    jbody = {"job_id": "j_pois_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013630}
                    c_fail, _ = cli4.call("POST", "/api/v1/jobs", jbody, tenant="t_pois", secret="sec_po", idem="idem_retry_402")
                    assert c_fail == 402
                    cli4.call("POST", "/api/v1/tenants/t_pois/topup", {"amount_cents": 500}, tenant="t_pois", secret="sec_po")
                    c_ok, _ = cli4.call("POST", "/api/v1/jobs", jbody, tenant="t_pois", secret="sec_po", idem="idem_retry_402")
                    assert c_ok == 201, f"402 poisoned Idempotency-Key! got {c_ok}"
                    # Exact replay -> 201 without charging twice; mutated body -> 409 IDEMPOTENCY_CONFLICT
                    c_rep, _ = cli4.call("POST", "/api/v1/jobs", jbody, tenant="t_pois", secret="sec_po", idem="idem_retry_402")
                    c_conf, _ = cli4.call("POST", "/api/v1/jobs", {**jbody, "estimated_gpu_sec": 120}, tenant="t_pois", secret="sec_po", idem="idem_retry_402")
                    assert c_rep == 201 and c_conf == 409
                    cli4.call("POST", "/api/v1/jobs/j_pois_1/cancel", {}, tenant="t_pois", secret="sec_po")
                run_check(w4, "w4_03_failed_4xx_does_not_poison_idempotency_key", c_w4_03)

                def c_w4_04():
                    # Unauthenticated or bad-HMAC request carrying an existing cached Idempotency-Key -> MUST 401!
                    jbody = {"job_id": "j_pois_1", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013630}
                    c_noauth, _ = cli4.call("POST", "/api/v1/jobs", jbody, idem="idem_retry_402")
                    c_badsig, _ = cli4.call("POST", "/api/v1/jobs", jbody, tenant="t_pois", secret="wrong_sec", idem="idem_retry_402")
                    assert c_noauth == 401 and c_badsig == 401
                run_check(w4, "w4_04_unauthenticated_idempotency_probe_rejected", c_w4_04)

                def c_w4_05():
                    for i in range(4):
                        assert cli4.call("POST", "/api/v1/jobs", {"job_id": f"jc_race_{i}", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 20, "submitted_epoch": 1700013700 + i}, tenant="t_sec_a", secret="sec_sa")[0] == 201

                    def _claim(i: int):
                        return cli4.call("POST", "/api/v1/workers/claim", {"worker_node_id": f"wn_{i}", "now_epoch": 1700013710}, admin=True)

                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                        outs = list(ex.map(_claim, range(10)))
                    assert all(c == 200 for c, _ in outs), f"non-200 in claim race: {outs}"
                    claimed_ids = [d["data"]["job"]["job_id"] for _, d in outs if d["data"].get("job") is not None]
                    assert len(claimed_ids) == 4 and len(set(claimed_ids)) == 4
                    for jid in claimed_ids:
                        cli4.call("POST", f"/api/v1/jobs/{jid}/settle", {"actual_gpu_sec": 20, "now_epoch": 1700013720}, tenant="t_sec_a", secret="sec_sa")
                run_check(w4, "w4_05_concurrent_claim_single_assignment_race", c_w4_05)

                def c_w4_06():
                    cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_c_lim", "name": "CL", "tier": "STARTER", "api_secret": "sec_cl"}, admin=True)
                    cli4.call("POST", "/api/v1/tenants/t_c_lim/topup", {"amount_cents": 650}, tenant="t_c_lim", secret="sec_cl")

                    def _sub(i: int):
                        return cli4.call("POST", "/api/v1/jobs", {"job_id": f"jcl_{i}", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013750}, tenant="t_c_lim", secret="sec_cl")

                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                        outs = list(ex.map(_sub, range(10)))
                    codes = [c for c, _ in outs]
                    assert 500 not in codes and sum(1 for c in codes if c == 201) == 2, f"got {codes}"
                    _, st = cli4.call("GET", "/api/v1/tenants/t_c_lim/statement", tenant="t_c_lim", secret="sec_cl")
                    assert st["data"]["balance_cents"] == 250 and st["data"]["frozen_cents"] == 400
                run_check(w4, "w4_06_concurrent_submit_balance_and_concurrency_race", c_w4_06)

                def c_w4_07():
                    # Multi-child shared parent pool race: Parent has 600c (enough for ONLY 3 jobs of 200c each); 3 children launch 12 threads!
                    cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "p_pool", "name": "Pool", "tier": "ENTERPRISE", "api_secret": "sec_pp"}, admin=True)
                    cli4.call("POST", "/api/v1/tenants/p_pool/topup", {"amount_cents": 600}, tenant="p_pool", secret="sec_pp")
                    for k in range(3):
                        cid = f"c_pool_{k}"
                        cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": cid, "name": cid, "tier": "STARTER", "api_secret": f"sec_{cid}"}, admin=True)
                        assert cli4.call("POST", f"/api/v1/admin/tenants/{cid}/bind-parent", {"parent_tenant_id": "p_pool", "credit_limit_cents": 1000}, admin=True)[0] == 200

                    def _child_sub(idx: int):
                        cid = f"c_pool_{idx % 3}"
                        return cli4.call("POST", "/api/v1/jobs", {"job_id": f"jpool_{idx}", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 100, "submitted_epoch": 1700013800}, tenant=cid, secret=f"sec_{cid}")

                    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
                        outs = list(ex.map(_child_sub, range(12)))
                    codes = [c for c, _ in outs]
                    assert 500 not in codes, f"500 in shared parent pool race: {outs}"
                    assert sum(1 for c in codes if c == 201) == 3, f"expected exact 3 funded jobs from 600c parent pool, got {codes}"
                    _, st_p = cli4.call("GET", "/api/v1/tenants/p_pool/statement", tenant="p_pool", secret="sec_pp")
                    assert st_p["data"]["balance_cents"] == 0
                    tot_drawn = 0
                    for k in range(3):
                        cid = f"c_pool_{k}"
                        _, st_c = cli4.call("GET", f"/api/v1/tenants/{cid}/statement", tenant=cid, secret=f"sec_{cid}")
                        tot_drawn += st_c["data"]["org_drawn_cents"]
                    assert tot_drawn == 600, f"expected total drawn 600, got {tot_drawn}"
                run_check(w4, "w4_07_concurrent_multi_child_shared_parent_pool_race", c_w4_07)

                def c_w4_08():
                    # Concurrent double-click storm with identical Idempotency-Key -> MUST topup ONCE!
                    cli4.call("POST", "/api/v1/admin/tenants", {"tenant_id": "t_dclick", "name": "DC", "tier": "STARTER", "api_secret": "sec_dc"}, admin=True)

                    def _topup_same(_i: int):
                        return cli4.call(
                            "POST", "/api/v1/tenants/t_dclick/topup",
                            {"amount_cents": 333, "now_epoch": 1700013850},
                            tenant="t_dclick", secret="sec_dc", idem="idem_same_click_8",
                        )

                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                        outs = list(ex.map(_topup_same, range(8)))
                    assert all(c == 200 for c, _ in outs), f"double-click storm failed: {outs}"
                    _, st = cli4.call("GET", "/api/v1/tenants/t_dclick/statement", tenant="t_dclick", secret="sec_dc")
                    assert st["data"]["balance_cents"] == 333 and st["data"]["ledger_count"] == 1, f"double-click charged multiple times: {st['data']}"
                run_check(w4, "w4_08_concurrent_idempotent_double_click_storm", c_w4_08)

                def c_w4_09():
                    # Mixed chaos storm: concurrent SPOT/HIGH submits, claims, preemptions, reaps, settles & cancels
                    def _chaos_op(step: int):
                        op = step % 5
                        if op == 0:
                            prio = "SPOT" if step % 2 == 0 else "HIGH"
                            return cli4.call(
                                "POST", "/api/v1/jobs",
                                {"job_id": f"jchaos_{step}", "gpu_type": "A10", "priority": prio, "estimated_gpu_sec": 15, "submitted_epoch": 1700013900 + step, "ttl_seconds": 30},
                                tenant="t_sec_a", secret="sec_sa",
                            )
                        if op == 1:
                            return cli4.call("POST", "/api/v1/workers/claim", {"worker_node_id": f"cn_{step}", "now_epoch": 1700013905 + step, "lease_seconds": 15}, admin=True)
                        if op == 2:
                            return cli4.call("POST", "/api/v1/workers/preempt", {"worker_node_id": f"pn_{step}", "now_epoch": 1700013906 + step}, admin=True)
                        if op == 3:
                            return cli4.call("POST", "/api/v1/admin/reap", {"now_epoch": 1700013935 + step}, admin=True)
                        target_j = f"jchaos_{(step - 4)}"
                        return cli4.call("POST", f"/api/v1/jobs/{target_j}/cancel", {"now_epoch": 1700013910 + step}, tenant="t_sec_a", secret="sec_sa")

                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                        outs = list(ex.map(_chaos_op, range(25)))
                    codes = [c for c, _ in outs]
                    assert 500 not in codes, f"HTTP 500 during mixed chaos storm: {outs}"
                run_check(w4, "w4_09_mixed_chaos_preempt_reap_cancel_storm", c_w4_09)

                def c_w4_10():
                    ca, da = cli4.call("GET", "/api/v1/admin/audit", admin=True)
                    assert ca == 200, f"post-chaos audit status {ca}: {da}"
                    aud = da["data"]
                    assert aud["all_balanced"] is True and aud.get("org_transfer_balanced", True) is True and aud["anomalies"] == [], f"post-chaos drift! {aud}"
                    assert aud["total_frozen_cents"] == aud["active_escrow_cents"]
                run_check(w4, "w4_10_post_chaos_zero_drift_full_audit", c_w4_10)
            finally:
                srv4.shutdown()
                srv4.server_close()

    return {"boot_ok": True, "boot_error": None, "wave1": w1, "wave2": w2, "wave3": w3, "wave4": w4}


if __name__ == "__main__":
    try:
        report = run_all()
    except Exception:
        report = {"boot_ok": False, "boot_error": traceback.format_exc()[-1000:], "wave1": [], "wave2": [], "wave3": [], "wave4": []}
    OUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
'''


def evaluate_workspace_waves(workspace_dir: Path, scoring_cfg: dict) -> dict:
    """将终局沙盒拷贝至干净临时目录并在独立子进程中执行四波四十项黑盒对抗验收。"""
    a_cfg = scoring_cfg.get("A") or {}
    a1_max = float(a_cfg.get("a1_core_e2e_max", 5.0))
    a2_max = float(a_cfg.get("a2_invariants_max", 7.0))
    a3_max = float(a_cfg.get("a3_phase2_evolution_max", 7.5))
    a4_max = float(a_cfg.get("a4_chaos_security_max", 5.5))

    with tempfile.TemporaryDirectory(prefix="swarm_eval_box_", ignore_cleanup_errors=True) as tmp:
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
                timeout=75,
            )
        except subprocess.TimeoutExpired:
            return {
                "boot_ok": False,
                "boot_error": "四波黑盒压测执行超时（>75s）",
                "A1": 0.0,
                "A2": 0.0,
                "A3": 0.0,
                "A4": 0.0,
                "A_total": 0.0,
                "waves": {"wave1": [], "wave2": [], "wave3": [], "wave4": []},
            }

        if not out_json.is_file():
            return {
                "boot_ok": False,
                "boot_error": "压测探针异常退出，未产出结果文件",
                "A1": 0.0,
                "A2": 0.0,
                "A3": 0.0,
                "A4": 0.0,
                "A_total": 0.0,
                "waves": {"wave1": [], "wave2": [], "wave3": [], "wave4": []},
            }

        raw = json.loads(out_json.read_text(encoding="utf-8"))

    w1 = raw.get("wave1") or []
    w2 = raw.get("wave2") or []
    w3 = raw.get("wave3") or []
    w4 = raw.get("wave4") or []

    w1_passed = sum(1 for x in w1 if x.get("passed"))
    w2_passed = sum(1 for x in w2 if x.get("passed"))
    w3_passed = sum(1 for x in w3 if x.get("passed"))
    w4_passed = sum(1 for x in w4 if x.get("passed"))

    a1 = round(a1_max * (w1_passed / 10.0), 2) if w1 else 0.0
    a2 = round(a2_max * (w2_passed / 10.0), 2) if w2 else 0.0
    a3 = round(a3_max * (w3_passed / 10.0), 2) if w3 else 0.0
    a4 = round(a4_max * (w4_passed / 10.0), 2) if w4 else 0.0
    a_tot = round(a1 + a2 + a3 + a4, 2)

    return {
        "boot_ok": bool(raw.get("boot_ok")),
        "boot_error": raw.get("boot_error"),
        "A1": a1,
        "A2": a2,
        "A3": a3,
        "A4": a4,
        "A_total": a_tot,
        "wave_counts": {
            "w1_passed": w1_passed,
            "w1_total": 10,
            "w2_passed": w2_passed,
            "w2_total": 10,
            "w3_passed": w3_passed,
            "w3_total": 10,
            "w4_passed": w4_passed,
            "w4_total": 10,
        },
        "waves": {"wave1": w1, "wave2": w2, "wave3": w3, "wave4": w4},
    }
