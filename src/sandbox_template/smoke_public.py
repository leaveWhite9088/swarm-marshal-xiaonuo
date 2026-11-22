#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开基础连通性冒烟检查（smoke_public.py）。

注意（反高分通胀铁律一）：
本脚本仅验证服务能否正常启动、/health 是否可达、以及最基础的一条建租户+充值+提交作业连通路径。
本脚本【绝不】覆盖 18 条复杂计费阶梯、高峰期 SPOT 特例、代金券退回、v1 老库在线迁移、
租约超时重试、运行期取消违约金、幂等冲突与多线程并发抢单压测！
统帅必须自行建立测试工位并通过代码审查把控完整业务质量。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _sign(secret: str, method: str, path: str, ts: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    msg = f"{method.upper()}\n{path}\n{ts}\n{body_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(base_url: str, method: str, path: str, payload=None, headers=None):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(base_url + path, data=(body if method != "GET" else None), headers=hdrs, method=method)
    try:
        with _OPENER.open(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main() -> int:
    try:
        from cloudhub.app import create_server
    except Exception as exc:
        print(f"[SMOKE FAIL] 无法导入 cloudhub.app.create_server: {exc}")
        return 1

    with tempfile.TemporaryDirectory(prefix="cloudhub_smoke_") as tmp:
        db_path = str(Path(tmp) / "smoke.db")
        admin_token = "smoke-admin-token"
        srv = create_server(db_path=db_path, host="127.0.0.1", port=0, admin_token=admin_token)
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"
        th = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True)
        th.start()
        try:
            code, data = _req(base, "GET", "/health")
            assert code == 200 and data.get("ok") is True, f"/health 异常: {code} {data}"

            code, data = _req(
                base,
                "POST",
                "/api/v1/admin/tenants",
                {
                    "tenant_id": "t_smoke",
                    "name": "Smoke Tenant",
                    "tier": "STARTER",
                    "api_secret": "sec_smoke_123",
                    "now_epoch": 1700000000,
                },
                headers={"X-Admin-Token": admin_token},
            )
            assert code == 201 and data.get("ok") is True, f"创建租户失败: {code} {data}"

            ts = str(int(time.time()))
            topup_body = {"amount_cents": 5000, "now_epoch": 1700000005}
            raw_topup = json.dumps(topup_body).encode("utf-8")
            code, data = _req(
                base,
                "POST",
                "/api/v1/tenants/t_smoke/topup",
                topup_body,
                headers={
                    "X-Tenant-Id": "t_smoke",
                    "X-Timestamp": ts,
                    "X-Signature": _sign("sec_smoke_123", "POST", "/api/v1/tenants/t_smoke/topup", ts, raw_topup),
                },
            )
            assert code == 200 and data.get("data", {}).get("balance_cents") == 5000, f"充值失败: {code} {data}"

            job_body = {
                "job_id": "job_smoke_1",
                "gpu_type": "A10",
                "priority": "NORMAL",
                "estimated_gpu_sec": 100,
                "submitted_epoch": 1700006400,
            }
            raw_job = json.dumps(job_body).encode("utf-8")
            code, data = _req(
                base,
                "POST",
                "/api/v1/jobs",
                job_body,
                headers={
                    "X-Tenant-Id": "t_smoke",
                    "X-Timestamp": ts,
                    "X-Signature": _sign("sec_smoke_123", "POST", "/api/v1/jobs", ts, raw_job),
                },
            )
            assert code == 201 and data.get("data", {}).get("status") == "QUEUED", f"提交作业失败: {code} {data}"

            print("[SMOKE PASS] 基础启动与单条建户/充值/提单连通检查通过！（深层计费、迁移、状态机与并发规则需自行验证）")
            return 0
        except AssertionError as exc:
            print(f"[SMOKE FAIL] {exc}")
            return 1
        except Exception as exc:
            print(f"[SMOKE ERROR] {exc}")
            return 1
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
