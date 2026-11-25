# -*- coding: utf-8 -*-
"""确定性本地模拟弱工人（mock_worker.py）。

设计目标（严格对齐《构建与设计说明.md》§五.2.2）：
- 模拟真实轻量弱模型的核心特征：对《全局架构契约》完备度、工位白名单聚焦度、工单拆解颗粒度与审查批注具体度高度敏感。
- 无全局契约时：各工位自行臆造不兼容的函数名与字典键名（如 open_store vs init_db、avail_cents vs balance_cents），合入后联调报错；
- 单工位大包大揽或原题整段转发时：触发上下文注意力涣散（Overload），漏掉迁移、分段阶梯、SPOT 特例、运行取消违约金与并发锁；
- 契约清晰且工单拆解聚焦时：首轮仍会像真实弱模型一样在复杂特例（如 SPOT 高峰期免上浮、RUNNING 取消退还代金券）上出现细微疏漏；
  当统帅通过审查（REJECT）或精准返工指令点明具体错误后，模拟工人即可精准修平，最终拼出 18 条规则严丝合缝的完整系统。
"""
from __future__ import annotations

import re
from pathlib import Path


def _classify_file_role(rel_path: str, role_prompt: str, ticket: str) -> str:
    low = rel_path.lower()
    fname = Path(low).stem
    if low.startswith("tests/") or fname.startswith("test_") or fname.endswith("_test"):
        return "tests"
    if fname == "app" or "server" in fname or "gateway" in fname or "router" in fname:
        return "app"
    if any(k in fname for k in ("auth", "hmac", "sec", "token", "sig")):
        return "auth"
    if any(k in fname for k in ("db", "store", "storage", " persistence", "ledger", "migrat", "repo", "schema")):
        return "db"
    if any(k in fname for k in ("pric", "bill", "quota", "vouch", "calc", "rate", "fee")):
        return "pricing"
    if any(k in fname for k in ("job", "state", "life", "reap", "queue", "sched", "engine", "task", "worker")):
        return "jobs"
    # Fallback by ticket / role prompt keywords
    ctx = (role_prompt + "\n" + ticket).lower()
    if "hmac" in ctx and "http" not in ctx:
        return "auth"
    if "sqlite" in ctx or "ledger" in ctx or "迁表" in ctx:
        return "db"
    if "阶梯" in ctx or "600" in ctx or "voucher" in ctx or "计费" in ctx:
        return "pricing"
    return "jobs"


def _discover_contract_modules(contract_text: str, allowed_paths: list[str]) -> dict[str, str]:
    """从全局契约文本中推断统帅约定的各子模块包导入名（支持统帅自定义文件名）。"""
    found = re.findall(r"cloudhub(?:/|\.)([a-zA-Z_][a-zA-Z0-9_]*)", contract_text or "")
    for rel in allowed_paths:
        if rel.startswith("cloudhub/") and rel.endswith(".py"):
            found.append(Path(rel).stem)

    mapping = {
        "db": "db",
        "pricing": "pricing",
        "jobs": "jobs",
        "auth": "auth",
    }
    for mod in found:
        if mod in ("__init__", "app"):
            continue
        mlow = mod.lower()
        if any(k in mlow for k in ("db", "store", "storage", "ledger", "repo", "schema")):
            mapping["db"] = mod
        elif any(k in mlow for k in ("pric", "bill", "quota", "vouch", "calc", "rate")):
            mapping["pricing"] = mod
        elif any(k in mlow for k in ("job", "state", "life", "reap", "engine", "sched", "queue")):
            mapping["jobs"] = mod
        elif any(k in mlow for k in ("auth", "hmac", "sec", "sig")):
            mapping["auth"] = mod
    return mapping


def _count_rule_clusters(ticket: str) -> int:
    t = ticket.lower()
    clusters = 0
    if any(k in t for k in ("sqlite", "alter table", "ledger", "迁表", "老库", "流水", "idempotency")):
        clusters += 1
    if any(k in t for k in ("600", "阶梯", "spot", "peak", "高峰", "tier", "starter", "growth", "计费")):
        clusters += 1
    if any(k in t for k in ("voucher", "代金券", "min_spend", "120", "1.2", "overrun", "超支")):
        clusters += 1
    if any(k in t for k in ("queued", "running", "claim", "reap", "lease", "违约金", "状态机", "10%")):
        clusters += 1
    if any(k in t for k in ("hmac", "x-signature", "x-admin-token", "http", "create_server", "/api/v1/")):
        clusters += 1
    return clusters


def _analyze_quality_signals(
    *,
    contract_text: str,
    role_prompt: str,
    ticket: str,
    allowed_paths: list[str],
    is_rework: bool,
    review_comment: str,
    mock_cfg: dict,
) -> dict:
    c_len = len((contract_text or "").strip())
    min_contract = int(mock_cfg.get("min_contract_chars_for_alignment", 320))
    overload_clusters = int(mock_cfg.get("overload_rule_clusters", 4))
    overload_chars = int(mock_cfg.get("overload_ticket_chars", 1500))

    combined = f"{ticket}\n{review_comment}"
    comb_low = combined.lower()
    c_low = (contract_text or "").lower()

    has_contract = c_len >= 100
    strong_contract = c_len >= min_contract and sum(
        1
        for kw in ("balance_cents", "frozen_cents", "reserved_cents", "create_server", "error")
        if kw in c_low
    ) >= 3

    clusters = _count_rule_clusters(ticket)
    raw_copy_paste = (
        ("十八条核心业务规则" in ticket)
        or ("完整 http 路由契约速查表" in ticket.lower())
        or ("项目总需求书" in ticket)
        or ("第一阶段 http 路由契约速查表" in ticket.lower())
    )
    overloaded = raw_copy_paste or (clusters >= overload_clusters and len(ticket) >= overload_chars)

    # 判断工单拆解是否精细（明确给出具体边界条件，而不是一句“把模块写一下”或整篇照抄原题）
    focused_ticket = (not overloaded) and (len(ticket.strip()) >= 90) and (len(role_prompt.strip()) >= 25)

    # 具体业务细节信号
    mentions_migration = any(k in comb_low for k in ("alter table", "pragma", "table_info", "v1", "老库", "在线迁", "期初"))
    mentions_idempotency = any(k in comb_low for k in ("idempotency", "幂等", "409", "body_sha256", "sha256"))
    mentions_brackets = any(k in comb_low for k in ("600", "3600", "80%", "60%", "三段", "两段", "阶梯", "bracket"))
    mentions_vouchers = any(k in comb_low for k in ("voucher", "代金券", "min_spend"))
    mentions_overrun = any(k in comb_low for k in ("1.2", "120", "overrun", "超支", "billable_gpu_sec", "402"))
    mentions_reaper_retry = any(k in comb_low for k in ("retry_count", "max_retries", "reap", "requeue", "重入队"))
    mentions_running_penalty = any(k in comb_low for k in ("10%", "penalty", "违约金", "cancel_penalty"))
    mentions_audit = any(k in comb_low for k in ("active_escrow_cents", "all_balanced", "audit", "守恒"))
    mentions_concurrency = any(k in comb_low for k in ("begin immediate", "并发", "lock", "threading"))
    mentions_phase2 = any(
        k in comb_low or k in c_low
        for k in ("preempt", "抢占", "bind-parent", "bind_parent", "org_transfer", "credit_limit", "母账户", "授信")
    )

    # 审查打回（REJECT）是否具体点中要害（而非“报错了重修”）
    rev_low = (review_comment or "").lower()
    specific_rework = is_rework and len((review_comment or "").strip()) >= 25
    fix_spot_peak = specific_rework and any(k in rev_low for k in ("spot", "peak", "高峰", "1.2", "120"))
    fix_voucher_release = specific_rework and any(
        k in rev_low for k in ("voucher", "代金券", "release", "used_by_job_id", "释放", "退券", "cancelled", "expired")
    )
    fix_contract_mismatch = specific_rework and any(
        k in rev_low for k in ("import", "签名", "字段", "函数", "init_db", "contract", "接口", "参数")
    )

    return {
        "has_contract": has_contract,
        "strong_contract": strong_contract or fix_contract_mismatch,
        "overloaded": overloaded,
        "focused_ticket": focused_ticket,
        "mentions_migration": mentions_migration and not overloaded,
        "mentions_idempotency": mentions_idempotency and not overloaded,
        "mentions_brackets": mentions_brackets and not overloaded,
        "mentions_vouchers": mentions_vouchers and not overloaded,
        "mentions_overrun": mentions_overrun and not overloaded,
        "mentions_reaper_retry": mentions_reaper_retry and not overloaded,
        "mentions_running_penalty": mentions_running_penalty and not overloaded,
        "mentions_audit": mentions_audit and not overloaded,
        "mentions_concurrency": (mentions_concurrency or strong_contract) and not overloaded,
        "mentions_phase2": mentions_phase2 and not overloaded,
        "fix_spot_peak": fix_spot_peak,
        "fix_voucher_release": fix_voucher_release,
        "is_rework": is_rework,
    }


def _gen_db_module(sig: dict) -> str:
    if not sig["has_contract"]:
        # 无全局契约时：工人自行臆造不兼容函数名与表列名
        return '''# -*- coding: utf-8 -*-
"""Persistence module written without global contract (drifted names)."""
import sqlite3

def open_tenant_store(path: str):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS org_accounts (
            org_id TEXT PRIMARY KEY,
            title TEXT,
            plan TEXT,
            avail_cents INTEGER DEFAULT 0,
            secret TEXT
        )
    """)
    conn.commit()
    return conn
'''

    do_mig = sig["mentions_migration"] and sig["focused_ticket"]
    do_idem = sig["mentions_idempotency"] and sig["focused_ticket"]
    use_imm = sig["mentions_concurrency"]
    do_phase2 = bool(sig.get("mentions_phase2")) and sig["focused_ticket"]
    p2_cols_sql = (
        "                parent_tenant_id TEXT,\n"
        "                credit_limit_cents INTEGER NOT NULL DEFAULT 0,\n"
        "                org_drawn_cents INTEGER NOT NULL DEFAULT 0,\n"
        if do_phase2
        else ""
    )

    return f'''# -*- coding: utf-8 -*-
"""CloudHub SQLite persistence, schema migration, idempotency, and double-entry ledger."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time

TIER_CONFIG = {{
    "STARTER": {{"max_concurrent": 2, "quota_gpu_sec": 3600, "tier_rate_pct": 100}},
    "GROWTH": {{"max_concurrent": 5, "quota_gpu_sec": 18000, "tier_rate_pct": 90}},
    "ENTERPRISE": {{"max_concurrent": 10, "quota_gpu_sec": 108000, "tier_rate_pct": 80}},
}}

_DB_LOCK = threading.RLock()


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextlib.contextmanager
def tx(db_path: str):
    with _DB_LOCK:
        conn = get_conn(db_path)
        try:
            conn.execute("{"BEGIN IMMEDIATE" if use_imm else "BEGIN"}")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({{table}})").fetchall()
    return {{str(r["name"]) for r in rows}}


def init_db(db_path: str) -> None:
    with tx(db_path) as conn:
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
                initial_Carry_cents INTEGER NOT NULL DEFAULT 0,
{p2_cols_sql}                api_secret TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT 0
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
                created_at INTEGER NOT NULL DEFAULT 0
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
                used_by_job_id TEXT,
                consumed INTEGER NOT NULL DEFAULT 0
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
                final_charge_cents INTEGER NOT NULL DEFAULT 0,
                penalty_cents INTEGER NOT NULL DEFAULT 0,
                voucher_code TEXT,
                submitted_epoch INTEGER NOT NULL,
                ttl_until_epoch INTEGER NOT NULL,
                max_retries INTEGER NOT NULL DEFAULT 1,
                retry_count INTEGER NOT NULL DEFAULT 0,
                worker_node_id TEXT,
                lease_until_epoch INTEGER,
                version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records (
                idem_scope TEXT PRIMARY KEY,
                body_sha256 TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        if {str(bool(do_mig))}:
            tcols = _table_columns(conn, "tenants")
            if "frozen_cents" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN frozen_cents INTEGER NOT NULL DEFAULT 0")
            if "quota_gpu_sec" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN quota_gpu_sec INTEGER NOT NULL DEFAULT 3600")
                for tier_k, cfg in TIER_CONFIG.items():
                    conn.execute(
                        "UPDATE tenants SET quota_gpu_sec = ? WHERE tier = ?",
                        (cfg["quota_gpu_sec"], tier_k),
                    )
            if "used_gpu_sec" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN used_gpu_sec INTEGER NOT NULL DEFAULT 0")
            if "initial_Carry_cents" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN initial_Carry_cents INTEGER NOT NULL DEFAULT 0")
            if {str(bool(do_phase2))}:
                if "parent_tenant_id" not in tcols:
                    conn.execute("ALTER TABLE tenants ADD COLUMN parent_tenant_id TEXT")
                if "credit_limit_cents" not in tcols:
                    conn.execute("ALTER TABLE tenants ADD COLUMN credit_limit_cents INTEGER NOT NULL DEFAULT 0")
                if "org_drawn_cents" not in tcols:
                    conn.execute("ALTER TABLE tenants ADD COLUMN org_drawn_cents INTEGER NOT NULL DEFAULT 0")
            if "api_secret" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN api_secret TEXT NOT NULL DEFAULT ''")
            if "created_at" not in tcols:
                conn.execute("ALTER TABLE tenants ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")

            lcols = _table_columns(conn, "ledger_entries")
            if "delta_frozen" not in lcols:
                conn.execute("ALTER TABLE ledger_entries ADD COLUMN delta_frozen INTEGER NOT NULL DEFAULT 0")
            if "frozen_after" not in lcols:
                conn.execute("ALTER TABLE ledger_entries ADD COLUMN frozen_after INTEGER NOT NULL DEFAULT 0")

            # 对齐 v1 遗留租户若存在无流水的期初余额差值
            for trow in conn.execute("SELECT tenant_id, balance_cents FROM tenants").fetchall():
                tid = trow["tenant_id"]
                srow = conn.execute(
                    "SELECT COALESCE(SUM(delta_available), 0) AS s FROM ledger_entries WHERE tenant_id = ?",
                    (tid,),
                ).fetchone()
                diff = int(trow["balance_cents"]) - int(srow["s"])
                conn.execute("UPDATE tenants SET initial_Carry_cents = ? WHERE tenant_id = ?", (diff, tid))


def append_ledger(
    conn: sqlite3.Connection,
    *,
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
        raise KeyError(f"Unknown tenant: {{tenant_id}}")
    new_bal = int(row["balance_cents"]) + int(delta_available)
    new_frz = int(row["frozen_cents"]) + int(delta_frozen)
    if new_bal < 0 or new_frz < 0:
        raise ValueError("Negative balance or frozen_cents invariant violation")
    ts = int(now_epoch if now_epoch is not None else time.time())
    conn.execute(
        "UPDATE tenants SET balance_cents = ?, frozen_cents = ? WHERE tenant_id = ?",
        (new_bal, new_frz, tenant_id),
    )
    conn.execute(
        """
        INSERT INTO ledger_entries (
            tenant_id, job_id, entry_type, delta_available, delta_frozen, balance_after, frozen_after, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (tenant_id, job_id, entry_type, int(delta_available), int(delta_frozen), new_bal, new_frz, ts),
    )
    return new_bal, new_frz


def check_idempotency(
    conn: sqlite3.Connection,
    scope_key: str | None,
    body_sha256: str,
) -> tuple[str, int | None, dict | None]:
    if not scope_key:
        return "MISS", None, None
    row = conn.execute(
        "SELECT body_sha256, status_code, response_json FROM idempotency_records WHERE idem_scope = ?",
        (scope_key,),
    ).fetchone()
    if row is None:
        return "MISS", None, None
    if {str(bool(do_idem))} and row["body_sha256"] != body_sha256:
        return "CONFLICT", 409, {{
            "ok": False,
            "error": {{"code": "IDEMPOTENCY_CONFLICT", "message": "Idempotency-Key reused with different payload"}},
        }}
    return "HIT", int(row["status_code"]), json.loads(row["response_json"])


def save_idempotency(
    conn: sqlite3.Connection,
    scope_key: str | None,
    body_sha256: str,
    status_code: int,
    response_obj: dict,
    now_epoch: int | None = None,
) -> None:
    if not scope_key:
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO idempotency_records (idem_scope, body_sha256, status_code, response_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (scope_key, body_sha256, int(status_code), json.dumps(response_obj, ensure_ascii=False), int(now_epoch or time.time())),
    )
'''


def _gen_pricing_module(sig: dict) -> str:
    if not sig["has_contract"]:
        return '''# -*- coding: utf-8 -*-
"""Pricing helper written without global contract (incompatible return type)."""
def estimate_fee(gpu_model: str, secs: int) -> dict:
    rates = {"A10": 2, "L40": 5, "H100": 12}
    return {"total_fee": rates.get(gpu_model, 2) * secs}
'''

    use_brackets = sig["mentions_brackets"] and sig["focused_ticket"]
    # 模拟真实弱模型典型首轮疏漏：除非统帅在审查（REJECT）中精准指出 SPOT 高峰期免上浮，首轮会把 SPOT 也乘 1.2
    spot_exempt = bool(sig["fix_spot_peak"])
    use_overrun_cap = sig["mentions_overrun"] and sig["focused_ticket"]

    return f'''# -*- coding: utf-8 -*-
"""CloudHub tiered GPU pricing, peak multiplier, and voucher calculation engine."""
from __future__ import annotations

GPU_UNIT_CENTS = {{"A10": 2, "L40": 5, "H100": 12}}
TIER_RATE_PCT = {{"STARTER": 100, "GROWTH": 90, "ENTERPRISE": 80}}
PRIORITY_PCT = {{"NORMAL": 100, "HIGH": 125, "SPOT": 70}}


def is_peak_epoch(submitted_epoch: int) -> bool:
    utc_hour = (int(submitted_epoch) // 3600) % 24
    return 9 <= utc_hour < 21


def compute_pre_voucher_cents(
    *,
    gpu_type: str,
    seconds: int,
    tier: str,
    priority: str,
    submitted_epoch: int,
) -> int:
    if gpu_type not in GPU_UNIT_CENTS:
        raise ValueError(f"Invalid gpu_type: {{gpu_type}}")
    if tier not in TIER_RATE_PCT:
        raise ValueError(f"Invalid tier: {{tier}}")
    if priority not in PRIORITY_PCT:
        raise ValueError(f"Invalid priority: {{priority}}")
    sec = int(seconds)
    if sec < 0:
        raise ValueError("seconds must be >= 0")

    unit = GPU_UNIT_CENTS[gpu_type]
    if {str(bool(use_brackets))}:
        tier1 = min(sec, 600)
        tier2 = min(max(0, sec - 600), 3000)
        tier3 = max(0, sec - 3600)
        bracket_x100 = tier1 * unit * 100 + tier2 * unit * 80 + tier3 * unit * 60
    else:
        bracket_x100 = sec * unit * 100

    tier_pct = TIER_RATE_PCT[tier]
    prio_pct = PRIORITY_PCT[priority]
    peak = is_peak_epoch(submitted_epoch)
    if {str(bool(spot_exempt))}:
        peak_pct = 120 if (peak and priority != "SPOT") else 100
    else:
        peak_pct = 120 if peak else 100

    numer = bracket_x100 * tier_pct * prio_pct * peak_pct
    return (numer + 50_000_000) // 100_000_000


def compute_billable_seconds(estimated_gpu_sec: int, actual_gpu_sec: int) -> int:
    if {str(bool(use_overrun_cap))}:
        cap = (int(estimated_gpu_sec) * 120) // 100
        return min(int(actual_gpu_sec), cap)
    return int(actual_gpu_sec)


def apply_voucher_discount(pre_voucher_cents: int, discount_cents: int, min_spend_cents: int) -> int:
    if pre_voucher_cents < min_spend_cents:
        return 0
    return min(int(discount_cents), int(pre_voucher_cents))
'''


def _gen_auth_module(sig: dict) -> str:
    return '''# -*- coding: utf-8 -*-
"""CloudHub Admin token & Tenant HMAC-SHA256 authentication guard."""
from __future__ import annotations

import hashlib
import hmac
import time


def compute_hmac_signature(api_secret: str, method: str, path: str, timestamp: str, body_bytes: bytes) -> str:
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    canonical = f"{method.upper()}\\n{path}\\n{timestamp}\\n{body_sha256}"
    return hmac.new(api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_admin_headers(headers, expected_admin_token: str) -> tuple[bool, int, str, str]:
    token = headers.get("X-Admin-Token")
    if not token:
        return False, 401, "UNAUTHORIZED", "Missing X-Admin-Token header"
    if not hmac.compare_digest(str(token), str(expected_admin_token)):
        return False, 403, "ADMIN_FORBIDDEN", "Invalid X-Admin-Token"
    return True, 200, "OK", ""


def verify_tenant_signature(
    headers,
    *,
    method: str,
    path: str,
    body_bytes: bytes,
    lookup_tenant_secret,
) -> tuple[bool, int, str, str, str | None]:
    tenant_id = headers.get("X-Tenant-Id")
    ts_raw = headers.get("X-Timestamp")
    sig = headers.get("X-Signature")
    if not tenant_id or not ts_raw or not sig:
        return False, 401, "UNAUTHORIZED", "Missing tenant HMAC headers", None
    try:
        ts_val = int(ts_raw)
    except ValueError:
        return False, 401, "UNAUTHORIZED", "Invalid X-Timestamp", None

    now_hdr = headers.get("X-Now-Epoch")
    if now_hdr is not None:
        try:
            ref_now = int(now_hdr)
        except ValueError:
            return False, 401, "UNAUTHORIZED", "Invalid X-Now-Epoch", None
    else:
        ref_now = int(time.time())

    if abs(ref_now - ts_val) > 300:
        return False, 401, "SIGNATURE_EXPIRED", "Timestamp outside 300s window", None

    secret = lookup_tenant_secret(tenant_id)
    if secret is None:
        return False, 401, "UNKNOWN_TENANT", f"Unknown tenant: {tenant_id}", None

    expected = compute_hmac_signature(secret, method, path, str(ts_raw), body_bytes)
    if not hmac.compare_digest(expected, str(sig).lower()):
        return False, 401, "INVALID_SIGNATURE", "HMAC signature mismatch", None
    return True, 200, "OK", "", tenant_id
'''


def _gen_jobs_module(sig: dict, mods: dict[str, str]) -> str:
    if not sig["has_contract"]:
        return '''# -*- coding: utf-8 -*-
"""Job state helper written without global contract."""
def push_job(conn, tenant, gpu, secs):
    return {"id": "job_1", "state": "PENDING"}
'''

    db_mod = mods["db"]
    pr_mod = mods["pricing"]
    do_running_penalty = sig["mentions_running_penalty"] and sig["focused_ticket"]
    do_reap_retry = sig["mentions_reaper_retry"] and sig["focused_ticket"]
    do_vouchers = sig["mentions_vouchers"] and sig["focused_ticket"]
    # 模拟弱模型首轮典型遗漏：在 RUNNING 取消或 EXPIRED 回收时漏掉释放代金券，经统帅审查 REJECT 点出后修复
    release_voucher_everywhere = bool(sig["fix_voucher_release"])
    do_full_audit = sig["mentions_audit"] and sig["focused_ticket"]
    do_phase2 = bool(sig.get("mentions_phase2")) and sig["focused_ticket"]

    return f'''# -*- coding: utf-8 -*-
"""CloudHub job lifecycle state machine, priority scheduler, reaper, and audit service."""
from __future__ import annotations

import sqlite3
import uuid

from . import {db_mod} as db_layer
from . import {pr_mod} as pricing_layer

VALID_PRIORITIES = {{"NORMAL", "HIGH", "SPOT"}}
VALID_GPUS = {{"A10", "L40", "H100"}}
VALID_TIERS = set(db_layer.TIER_CONFIG.keys())


class DomainError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _is_strict_int(val) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _job_dict(row: sqlite3.Row) -> dict:
    return {{
        "job_id": row["job_id"],
        "tenant_id": row["tenant_id"],
        "status": row["status"],
        "gpu_type": row["gpu_type"],
        "priority": row["priority"],
        "estimated_gpu_sec": int(row["estimated_gpu_sec"]),
        "actual_gpu_sec": int(row["actual_gpu_sec"]) if row["actual_gpu_sec"] is not None else None,
        "billable_gpu_sec": int(row["billable_gpu_sec"]) if row["billable_gpu_sec"] is not None else None,
        "pre_voucher_cents": int(row["pre_voucher_cents"]),
        "discount_cents": int(row["discount_cents"]),
        "reserved_cents": int(row["reserved_cents"]),
        "final_charge_cents": int(row["final_charge_cents"]),
        "penalty_cents": int(row["penalty_cents"]),
        "voucher_code": row["voucher_code"],
        "submitted_epoch": int(row["submitted_epoch"]),
        "ttl_until_epoch": int(row["ttl_until_epoch"]),
        "max_retries": int(row["max_retries"]),
        "retry_count": int(row["retry_count"]),
        "worker_node_id": row["worker_node_id"],
        "lease_until_epoch": int(row["lease_until_epoch"]) if row["lease_until_epoch"] is not None else None,
        "version": int(row["version"]),
    }}


def _release_voucher(conn: sqlite3.Connection, voucher_code: str | None, job_id: str) -> None:
    if not voucher_code:
        return
    conn.execute(
        "UPDATE vouchers SET used_by_job_id = NULL WHERE code = ? AND used_by_job_id = ? AND consumed = 0",
        (voucher_code, job_id),
    )


def create_tenant(conn: sqlite3.Connection, payload: dict) -> dict:
    tid = str(payload.get("tenant_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    tier = str(payload.get("tier") or "").strip()
    secret = str(payload.get("api_secret") or "").strip()
    now_ep = payload.get("now_epoch", 1700000000)
    if not tid or not name or tier not in VALID_TIERS or not secret or not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid tenant parameters")
    exists = conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tid,)).fetchone()
    if exists:
        raise DomainError(409, "ALREADY_EXISTS", f"Tenant {{tid}} already exists")
    quota = db_layer.TIER_CONFIG[tier]["quota_gpu_sec"]
    conn.execute(
        """
        INSERT INTO tenants (tenant_id, name, tier, balance_cents, frozen_cents, quota_gpu_sec, used_gpu_sec, initial_Carry_cents, api_secret, created_at)
        VALUES (?, ?, ?, 0, 0, ?, 0, 0, ?, ?)
        """,
        (tid, name, tier, quota, secret, int(now_ep)),
    )
    return {{
        "tenant_id": tid,
        "name": name,
        "tier": tier,
        "balance_cents": 0,
        "frozen_cents": 0,
        "quota_gpu_sec": quota,
        "used_gpu_sec": 0,
    }}


def create_voucher(conn: sqlite3.Connection, payload: dict) -> dict:
    code = str(payload.get("code") or "").strip()
    tid = str(payload.get("tenant_id") or "").strip()
    disc = payload.get("discount_cents")
    min_sp = payload.get("min_spend_cents", 0)
    exp = payload.get("expires_at_epoch")
    if not code or not tid or not _is_strict_int(disc) or disc <= 0 or not _is_strict_int(min_sp) or min_sp < 0 or not _is_strict_int(exp):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid voucher fields")
    if not conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tid,)).fetchone():
        raise DomainError(404, "NOT_FOUND", f"Tenant {{tid}} not found")
    if conn.execute("SELECT 1 FROM vouchers WHERE code = ?", (code,)).fetchone():
        raise DomainError(409, "ALREADY_EXISTS", f"Voucher {{code}} already exists")
    conn.execute(
        """
        INSERT INTO vouchers (code, tenant_id, discount_cents, min_spend_cents, expires_at_epoch, used_by_job_id, consumed)
        VALUES (?, ?, ?, ?, ?, NULL, 0)
        """,
        (code, tid, int(disc), int(min_sp), int(exp)),
    )
    return {{
        "code": code,
        "tenant_id": tid,
        "discount_cents": int(disc),
        "min_spend_cents": int(min_sp),
        "expires_at_epoch": int(exp),
        "redeemed": False,
    }}


def topup_tenant(conn: sqlite3.Connection, tenant_id: str, payload: dict) -> dict:
    amt = payload.get("amount_cents")
    now_ep = payload.get("now_epoch", 1700000000)
    if not _is_strict_int(amt) or amt <= 0 or not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "amount_cents must be positive integer")
    bal, frz = db_layer.append_ledger(
        conn,
        tenant_id=tenant_id,
        job_id=None,
        entry_type="TOPUP",
        delta_available=int(amt),
        delta_frozen=0,
        now_epoch=int(now_ep),
    )
    return {{"tenant_id": tenant_id, "balance_cents": bal, "frozen_cents": frz}}


def submit_job(conn: sqlite3.Connection, tenant_id: str, payload: dict) -> dict:
    gpu_type = payload.get("gpu_type")
    priority = payload.get("priority")
    est_sec = payload.get("estimated_gpu_sec")
    sub_ep = payload.get("submitted_epoch")
    ttl_sec = payload.get("ttl_seconds", 600)
    max_ret = payload.get("max_retries", 1)
    voucher_code = payload.get("voucher_code")
    job_id = str(payload.get("job_id") or f"job_{{uuid.uuid4().hex[:10]}}").strip()

    if (
        gpu_type not in VALID_GPUS
        or priority not in VALID_PRIORITIES
        or not _is_strict_int(est_sec)
        or not (1 <= est_sec <= 86400)
        or not _is_strict_int(sub_ep)
        or not _is_strict_int(ttl_sec)
        or ttl_sec < 1
        or not _is_strict_int(max_ret)
        or not (0 <= max_ret <= 5)
        or not job_id
    ):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid job submission arguments")

    if conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone():
        raise DomainError(409, "ALREADY_EXISTS", f"Job {{job_id}} already exists")

    trow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    if trow is None:
        raise DomainError(404, "NOT_FOUND", "Tenant not found")

    tier = trow["tier"]
    max_conc = db_layer.TIER_CONFIG[tier]["max_concurrent"]
    active_stat = conn.execute(
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(estimated_gpu_sec), 0) AS res_sec
        FROM jobs WHERE tenant_id = ? AND status IN ('QUEUED', 'RUNNING')
        """,
        (tenant_id,),
    ).fetchone()
    if int(active_stat["cnt"]) >= max_conc:
        raise DomainError(409, "CONCURRENCY_LIMIT_EXCEEDED", "Active job concurrency limit reached")
    if int(trow["used_gpu_sec"]) + int(active_stat["res_sec"]) + int(est_sec) > int(trow["quota_gpu_sec"]):
        raise DomainError(409, "QUOTA_EXCEEDED", "Monthly GPU quota exceeded")

    pre_cents = pricing_layer.compute_pre_voucher_cents(
        gpu_type=gpu_type,
        seconds=est_sec,
        tier=tier,
        priority=priority,
        submitted_epoch=sub_ep,
    )

    disc_cents = 0
    if voucher_code is not None:
        voucher_code = str(voucher_code).strip()
        if {str(bool(do_vouchers))}:
            vrow = conn.execute("SELECT * FROM vouchers WHERE code = ?", (voucher_code,)).fetchone()
            if vrow is None:
                raise DomainError(404, "NOT_FOUND", "Voucher not found")
            if vrow["tenant_id"] != tenant_id:
                raise DomainError(403, "TENANT_FORBIDDEN", "Voucher belongs to another tenant")
            if vrow["consumed"] or vrow["used_by_job_id"] is not None:
                raise DomainError(409, "VOUCHER_ALREADY_USED", "Voucher already in use or redeemed")
            if int(sub_ep) > int(vrow["expires_at_epoch"]):
                raise DomainError(400, "VOUCHER_EXPIRED", "Voucher has expired")
            if pre_cents < int(vrow["min_spend_cents"]):
                raise DomainError(400, "VOUCHER_MIN_SPEND_NOT_MET", "Order does not meet voucher min_spend_cents")
            disc_cents = pricing_layer.apply_voucher_discount(
                pre_cents, int(vrow["discount_cents"]), int(vrow["min_spend_cents"])
            )
            conn.execute("UPDATE vouchers SET used_by_job_id = ? WHERE code = ?", (job_id, voucher_code))

    reserved_cents = pre_cents - disc_cents
    cur_bal = int(trow["balance_cents"])
    if cur_bal < reserved_cents:
        if {str(bool(do_phase2))} and trow["parent_tenant_id"]:
            shortfall = reserved_cents - cur_bal
            prow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (trow["parent_tenant_id"],)).fetchone()
            if (
                prow is None
                or int(trow["org_drawn_cents"]) + shortfall > int(trow["credit_limit_cents"])
                or int(prow["balance_cents"]) < shortfall
            ):
                raise DomainError(402, "INSUFFICIENT_BALANCE", "Insufficient child & parent shared pool balance")
            db_layer.append_ledger(conn, tenant_id=prow["tenant_id"], job_id=job_id, entry_type="ORG_TRANSFER_OUT", delta_available=-shortfall, delta_frozen=0, now_epoch=sub_ep)
            conn.execute("UPDATE tenants SET org_drawn_cents = org_drawn_cents + ? WHERE tenant_id = ?", (shortfall, tenant_id))
            db_layer.append_ledger(conn, tenant_id=tenant_id, job_id=job_id, entry_type="ORG_TRANSFER_IN", delta_available=shortfall, delta_frozen=0, now_epoch=sub_ep)
        else:
            raise DomainError(402, "INSUFFICIENT_BALANCE", "Insufficient available balance")

    if reserved_cents > 0:
        db_layer.append_ledger(
            conn,
            tenant_id=tenant_id,
            job_id=job_id,
            entry_type="RESERVE",
            delta_available=-reserved_cents,
            delta_frozen=reserved_cents,
            now_epoch=sub_ep,
        )

    ttl_until = int(sub_ep) + int(ttl_sec)
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
            reserved_cents,
            voucher_code,
            int(sub_ep),
            ttl_until,
            int(max_ret),
        ),
    )
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_dict(row)


def get_job_for_tenant(conn: sqlite3.Connection, tenant_id: str, job_id: str) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise DomainError(404, "NOT_FOUND", f"Job {{job_id}} not found")
    if row["tenant_id"] != tenant_id:
        raise DomainError(403, "TENANT_FORBIDDEN", "Cannot access another tenant's job")
    return _job_dict(row)


def claim_next_job(conn: sqlite3.Connection, payload: dict) -> dict:
    node_id = str(payload.get("worker_node_id") or "").strip()
    now_ep = payload.get("now_epoch")
    lease_sec = payload.get("lease_seconds", 60)
    if not node_id or not _is_strict_int(now_ep) or not _is_strict_int(lease_sec) or lease_sec < 1:
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid claim payload")

    row = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'QUEUED' AND ttl_until_epoch >= ?
        ORDER BY
            CASE priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END ASC,
            submitted_epoch ASC,
            job_id ASC
        LIMIT 1
        """,
        (int(now_ep),),
    ).fetchone()
    if row is None:
        return {{"job": None}}

    lease_until = int(now_ep) + int(lease_sec)
    conn.execute(
        """
        UPDATE jobs
        SET status = 'RUNNING', worker_node_id = ?, lease_until_epoch = ?, version = version + 1
        WHERE job_id = ?
        """,
        (node_id, lease_until, row["job_id"]),
    )
    updated = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
    return {{"job": _job_dict(updated)}}


def settle_job(conn: sqlite3.Connection, tenant_id: str, job_id: str, payload: dict) -> dict:
    act_sec = payload.get("actual_gpu_sec")
    now_ep = payload.get("now_epoch", 1700000000)
    if not _is_strict_int(act_sec) or act_sec < 1 or not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "actual_gpu_sec must be integer >= 1")

    jrow = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if jrow is None:
        raise DomainError(404, "NOT_FOUND", "Job not found")
    if jrow["tenant_id"] != tenant_id:
        raise DomainError(403, "TENANT_FORBIDDEN", "Forbidden cross-tenant settle")
    if jrow["status"] != "RUNNING":
        raise DomainError(409, "INVALID_STATE_TRANSITION", f"Cannot settle job in status {{jrow['status']}}")

    trow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    billable_sec = pricing_layer.compute_billable_seconds(int(jrow["estimated_gpu_sec"]), int(act_sec))
    actual_pre = pricing_layer.compute_pre_voucher_cents(
        gpu_type=jrow["gpu_type"],
        seconds=billable_sec,
        tier=trow["tier"],
        priority=jrow["priority"],
        submitted_epoch=int(jrow["submitted_epoch"]),
    )
    actual_disc = 0
    if jrow["voucher_code"]:
        vrow = conn.execute("SELECT * FROM vouchers WHERE code = ?", (jrow["voucher_code"],)).fetchone()
        if vrow is not None:
            actual_disc = pricing_layer.apply_voucher_discount(
                actual_pre, int(vrow["discount_cents"]), int(vrow["min_spend_cents"])
            )
            conn.execute("UPDATE vouchers SET consumed = 1 WHERE code = ?", (jrow["voucher_code"],))

    final_charge = actual_pre - actual_disc
    reserved = int(jrow["reserved_cents"])
    refunded = 0
    extra = 0

    if final_charge <= reserved:
        if final_charge > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=tenant_id,
                job_id=job_id,
                entry_type="SETTLE",
                delta_available=0,
                delta_frozen=-final_charge,
                now_epoch=int(now_ep),
            )
        refunded = reserved - final_charge
        if refunded > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=tenant_id,
                job_id=job_id,
                entry_type="REFUND_UNUSED",
                delta_available=refunded,
                delta_frozen=-refunded,
                now_epoch=int(now_ep),
            )
    else:
        extra = final_charge - reserved
        cur_bal = int(trow["balance_cents"])
        if cur_bal < extra:
            if {str(bool(do_phase2))} and trow["parent_tenant_id"]:
                shortfall = extra - cur_bal
                prow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (trow["parent_tenant_id"],)).fetchone()
                if (
                    prow is None
                    or int(trow["org_drawn_cents"]) + shortfall > int(trow["credit_limit_cents"])
                    or int(prow["balance_cents"]) < shortfall
                ):
                    raise DomainError(402, "INSUFFICIENT_BALANCE_FOR_OVERRUN", "Insufficient child & parent balance to cover overrun")
                db_layer.append_ledger(conn, tenant_id=prow["tenant_id"], job_id=job_id, entry_type="ORG_TRANSFER_OUT", delta_available=-shortfall, delta_frozen=0, now_epoch=int(now_ep))
                conn.execute("UPDATE tenants SET org_drawn_cents = org_drawn_cents + ? WHERE tenant_id = ?", (shortfall, tenant_id))
                db_layer.append_ledger(conn, tenant_id=tenant_id, job_id=job_id, entry_type="ORG_TRANSFER_IN", delta_available=shortfall, delta_frozen=0, now_epoch=int(now_ep))
            else:
                raise DomainError(402, "INSUFFICIENT_BALANCE_FOR_OVERRUN", "Insufficient balance to cover overrun")
        db_layer.append_ledger(
            conn,
            tenant_id=tenant_id,
            job_id=job_id,
            entry_type="SETTLE",
            delta_available=-extra,
            delta_frozen=-reserved,
            now_epoch=int(now_ep),
        )

    conn.execute(
        "UPDATE tenants SET used_gpu_sec = used_gpu_sec + ? WHERE tenant_id = ?",
        (int(act_sec), tenant_id),
    )
    conn.execute(
        """
        UPDATE jobs
        SET status = 'SUCCEEDED', actual_gpu_sec = ?, billable_gpu_sec = ?,
            final_charge_cents = ?, version = version + 1
        WHERE job_id = ?
        """,
        (int(act_sec), billable_sec, final_charge, job_id),
    )
    t_after = conn.execute("SELECT balance_cents, frozen_cents FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    return {{
        "job_id": job_id,
        "status": "SUCCEEDED",
        "actual_gpu_sec": int(act_sec),
        "billable_gpu_sec": billable_sec,
        "reserved_cents": reserved,
        "final_charge_cents": final_charge,
        "refunded_cents": refunded,
        "extra_charged_cents": extra,
        "balance_cents": int(t_after["balance_cents"]),
        "frozen_cents": int(t_after["frozen_cents"]),
    }}


def fail_job(conn: sqlite3.Connection, tenant_id: str, job_id: str, payload: dict) -> dict:
    now_ep = payload.get("now_epoch", 1700000000)
    if not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid now_epoch")
    jrow = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if jrow is None:
        raise DomainError(404, "NOT_FOUND", "Job not found")
    if jrow["tenant_id"] != tenant_id:
        raise DomainError(403, "TENANT_FORBIDDEN", "Forbidden cross-tenant fail")
    if jrow["status"] != "RUNNING":
        raise DomainError(409, "INVALID_STATE_TRANSITION", f"Cannot fail job in status {{jrow['status']}}")

    reserved = int(jrow["reserved_cents"])
    if reserved > 0:
        db_layer.append_ledger(
            conn,
            tenant_id=tenant_id,
            job_id=job_id,
            entry_type="CANCEL_RELEASE",
            delta_available=reserved,
            delta_frozen=-reserved,
            now_epoch=int(now_ep),
        )
    _release_voucher(conn, jrow["voucher_code"], job_id)
    conn.execute("UPDATE jobs SET status = 'FAILED', version = version + 1 WHERE job_id = ?", (job_id,))
    t_after = conn.execute("SELECT balance_cents, frozen_cents FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    return {{
        "job_id": job_id,
        "status": "FAILED",
        "refunded_cents": reserved,
        "balance_cents": int(t_after["balance_cents"]),
        "frozen_cents": int(t_after["frozen_cents"]),
    }}


def cancel_job(conn: sqlite3.Connection, tenant_id: str, job_id: str, payload: dict) -> dict:
    now_ep = payload.get("now_epoch", 1700000000)
    if not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid now_epoch")
    jrow = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if jrow is None:
        raise DomainError(404, "NOT_FOUND", "Job not found")
    if jrow["tenant_id"] != tenant_id:
        raise DomainError(403, "TENANT_FORBIDDEN", "Forbidden cross-tenant cancel")

    cur_status = jrow["status"]
    if cur_status not in ("QUEUED", "RUNNING"):
        raise DomainError(409, "INVALID_STATE_TRANSITION", f"Cannot cancel job in status {{cur_status}}")

    reserved = int(jrow["reserved_cents"])
    penalty = 0
    refund = reserved

    if cur_status == "QUEUED":
        if reserved > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=tenant_id,
                job_id=job_id,
                entry_type="CANCEL_RELEASE",
                delta_available=reserved,
                delta_frozen=-reserved,
                now_epoch=int(now_ep),
            )
        _release_voucher(conn, jrow["voucher_code"], job_id)
    else:
        if {str(bool(do_running_penalty))}:
            penalty = (reserved * 10 + 50) // 100
            refund = reserved - penalty
        if penalty > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=tenant_id,
                job_id=job_id,
                entry_type="CANCEL_PENALTY",
                delta_available=0,
                delta_frozen=-penalty,
                now_epoch=int(now_ep),
            )
        if refund > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=tenant_id,
                job_id=job_id,
                entry_type="CANCEL_RELEASE",
                delta_available=refund,
                delta_frozen=-refund,
                now_epoch=int(now_ep),
            )
        if {str(bool(release_voucher_everywhere))}:
            _release_voucher(conn, jrow["voucher_code"], job_id)

    conn.execute(
        "UPDATE jobs SET status = 'CANCELLED', penalty_cents = ?, version = version + 1 WHERE job_id = ?",
        (penalty, job_id),
    )
    t_after = conn.execute("SELECT balance_cents, frozen_cents FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    return {{
        "job_id": job_id,
        "status": "CANCELLED",
        "penalty_cents": penalty,
        "refunded_cents": refund,
        "balance_cents": int(t_after["balance_cents"]),
        "frozen_cents": int(t_after["frozen_cents"]),
    }}


def reap_expired_jobs(conn: sqlite3.Connection, payload: dict) -> dict:
    now_ep = payload.get("now_epoch")
    if not _is_strict_int(now_ep):
        raise DomainError(400, "INVALID_ARGUMENT", "now_epoch must be integer")

    expired_cnt = 0
    requeued_cnt = 0
    failed_cnt = 0

    q_rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'QUEUED' AND ttl_until_epoch < ? ORDER BY job_id ASC",
        (int(now_ep),),
    ).fetchall()
    for j in q_rows:
        res = int(j["reserved_cents"])
        if res > 0:
            db_layer.append_ledger(
                conn,
                tenant_id=j["tenant_id"],
                job_id=j["job_id"],
                entry_type="CANCEL_RELEASE",
                delta_available=res,
                delta_frozen=-res,
                now_epoch=int(now_ep),
            )
        if {str(bool(release_voucher_everywhere))}:
            _release_voucher(conn, j["voucher_code"], j["job_id"])
        conn.execute("UPDATE jobs SET status = 'EXPIRED', version = version + 1 WHERE job_id = ?", (j["job_id"],))
        expired_cnt += 1

    r_rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'RUNNING' AND lease_until_epoch IS NOT NULL AND lease_until_epoch < ? ORDER BY job_id ASC",
        (int(now_ep),),
    ).fetchall()
    for j in r_rows:
        if {str(bool(do_reap_retry))} and int(j["retry_count"]) < int(j["max_retries"]):
            new_ttl = max(int(j["ttl_until_epoch"]), int(now_ep) + 300)
            conn.execute(
                """
                UPDATE jobs
                SET status = 'QUEUED', retry_count = retry_count + 1,
                    worker_node_id = NULL, lease_until_epoch = NULL,
                    ttl_until_epoch = ?, version = version + 1
                WHERE job_id = ?
                """,
                (new_ttl, j["job_id"]),
            )
            requeued_cnt += 1
        else:
            res = int(j["reserved_cents"])
            if res > 0:
                db_layer.append_ledger(
                    conn,
                    tenant_id=j["tenant_id"],
                    job_id=j["job_id"],
                    entry_type="CANCEL_RELEASE",
                    delta_available=res,
                    delta_frozen=-res,
                    now_epoch=int(now_ep),
                )
            _release_voucher(conn, j["voucher_code"], j["job_id"])
            conn.execute("UPDATE jobs SET status = 'FAILED', version = version + 1 WHERE job_id = ?", (j["job_id"],))
            failed_cnt += 1

    return {{
        "expired_count": expired_cnt,
        "requeued_count": requeued_cnt,
        "failed_count": failed_cnt,
    }}


def get_tenant_statement(conn: sqlite3.Connection, tenant_id: str) -> dict:
    trow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    if trow is None:
        raise DomainError(404, "NOT_FOUND", "Tenant not found")

    active_row = conn.execute(
        """
        SELECT COUNT(*) AS cnt, COALESCE(SUM(estimated_gpu_sec), 0) AS res_sec
        FROM jobs WHERE tenant_id = ? AND status IN ('QUEUED', 'RUNNING')
        """,
        (tenant_id,),
    ).fetchone()

    lrows = conn.execute(
        "SELECT * FROM ledger_entries WHERE tenant_id = ? ORDER BY entry_id ASC",
        (tenant_id,),
    ).fetchall()

    topup_sum = sum(int(r["delta_available"]) for r in lrows if r["entry_type"] == "TOPUP")
    charged_row = conn.execute(
        """
        SELECT COALESCE(SUM(final_charge_cents), 0) AS sc, COALESCE(SUM(penalty_cents), 0) AS pc
        FROM jobs WHERE tenant_id = ?
        """,
        (tenant_id,),
    ).fetchone()

    ledger_list = [
        {{
            "entry_id": int(r["entry_id"]),
            "job_id": r["job_id"],
            "entry_type": r["entry_type"],
            "delta_available": int(r["delta_available"]),
            "delta_frozen": int(r["delta_frozen"]),
            "balance_after": int(r["balance_after"]),
            "frozen_after": int(r["frozen_after"]),
            "created_at": int(r["created_at"]),
        }}
        for r in lrows
    ]

    subsidy_sum = sum(int(r["delta_available"]) for r in lrows if r["entry_type"] == "PREEMPT_COMPENSATION")
    return {{
        "tenant_id": tenant_id,
        "tier": trow["tier"],
        "balance_cents": int(trow["balance_cents"]),
        "frozen_cents": int(trow["frozen_cents"]),
        "quota_gpu_sec": int(trow["quota_gpu_sec"]),
        "used_gpu_sec": int(trow["used_gpu_sec"]),
        "reserved_gpu_sec": int(active_row["res_sec"]),
        "active_jobs": int(active_row["cnt"]),
        "total_topup_cents": int(topup_sum),
        "total_charged_cents": int(charged_row["sc"]) + int(charged_row["pc"]),
        "parent_tenant_id": trow["parent_tenant_id"] if "parent_tenant_id" in trow.keys() else None,
        "credit_limit_cents": int(trow["credit_limit_cents"]) if "credit_limit_cents" in trow.keys() else 0,
        "org_drawn_cents": int(trow["org_drawn_cents"]) if "org_drawn_cents" in trow.keys() else 0,
        "total_subsidy_cents": int(subsidy_sum),
        "ledger_count": len(ledger_list),
        "ledger": ledger_list,
    }}


def bind_parent_tenant(conn: sqlite3.Connection, tenant_id: str, payload: dict) -> dict:
    if not {str(bool(do_phase2))}:
        raise DomainError(404, "NOT_FOUND", "Phase 2 org binding not implemented")
    parent_id = str(payload.get("parent_tenant_id") or "").strip()
    limit_cents = payload.get("credit_limit_cents")
    if not parent_id or parent_id == tenant_id or not _is_strict_int(limit_cents) or limit_cents < 0:
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid bind-parent arguments")
    crow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
    prow = conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (parent_id,)).fetchone()
    if crow is None or prow is None:
        raise DomainError(404, "NOT_FOUND", "Child or parent tenant not found")
    if prow["tier"] != "ENTERPRISE" or crow["tier"] not in ("STARTER", "GROWTH"):
        raise DomainError(409, "INVALID_ORG_BINDING", "Parent must be ENTERPRISE and child must be STARTER/GROWTH")
    drawn = int(crow["org_drawn_cents"] or 0)
    if limit_cents < drawn:
        raise DomainError(409, "INVALID_ORG_BINDING", "credit_limit_cents below already drawn amount")
    cur_parent = crow["parent_tenant_id"]
    if cur_parent is not None and cur_parent != parent_id:
        act = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE tenant_id = ? AND status IN ('QUEUED', 'RUNNING')", (tenant_id,)).fetchone()
        if int(act["c"]) > 0:
            raise DomainError(409, "INVALID_ORG_BINDING", "Cannot switch parent while child has active jobs")
    conn.execute("UPDATE tenants SET parent_tenant_id = ?, credit_limit_cents = ? WHERE tenant_id = ?", (parent_id, int(limit_cents), tenant_id))
    return {{
        "tenant_id": tenant_id,
        "parent_tenant_id": parent_id,
        "credit_limit_cents": int(limit_cents),
        "org_drawn_cents": drawn,
    }}


def preempt_spot_for_high(conn: sqlite3.Connection, payload: dict) -> dict:
    if not {str(bool(do_phase2))}:
        raise DomainError(404, "NOT_FOUND", "Phase 2 preemption not implemented")
    node_id = str(payload.get("worker_node_id") or "").strip()
    now_ep = payload.get("now_epoch")
    lease_sec = payload.get("lease_seconds", 60)
    evict_id = payload.get("evict_job_id")
    if not node_id or not _is_strict_int(now_ep) or not _is_strict_int(lease_sec) or lease_sec < 1:
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid preempt arguments")
    if evict_id is not None and (not isinstance(evict_id, str) or not evict_id.strip()):
        raise DomainError(400, "INVALID_ARGUMENT", "Invalid evict_job_id")

    high_row = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'QUEUED' AND priority = 'HIGH' AND ttl_until_epoch >= ?
        ORDER BY submitted_epoch ASC, job_id ASC
        LIMIT 1
        """,
        (int(now_ep),),
    ).fetchone()
    if high_row is None:
        raise DomainError(409, "PREEMPTION_NOT_APPLICABLE", "No eligible QUEUED HIGH job")

    if evict_id:
        victim = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (evict_id.strip(),)).fetchone()
        if victim is None:
            raise DomainError(404, "NOT_FOUND", "Target evict_job_id not found")
        if victim["status"] != "RUNNING" or victim["priority"] != "SPOT":
            raise DomainError(409, "PREEMPTION_NOT_APPLICABLE", "Target job is not a RUNNING SPOT job")
    else:
        victim = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'RUNNING' AND priority = 'SPOT'
            ORDER BY submitted_epoch DESC, job_id DESC
            LIMIT 1
            """
        ).fetchone()
        if victim is None:
            raise DomainError(409, "PREEMPTION_NOT_APPLICABLE", "No RUNNING SPOT job to evict")

    v_jid = victim["job_id"]
    v_tid = victim["tenant_id"]
    res = int(victim["reserved_cents"])
    pre_c = int(victim["pre_voucher_cents"])
    if res > 0:
        db_layer.append_ledger(conn, tenant_id=v_tid, job_id=v_jid, entry_type="PREEMPT_RELEASE", delta_available=res, delta_frozen=-res, now_epoch=int(now_ep))
    _release_voucher(conn, victim["voucher_code"], v_jid)
    base_for_comp = res if res > 0 else pre_c
    comp = max(1, (base_for_comp * 15 + 50) // 100)
    db_layer.append_ledger(conn, tenant_id=v_tid, job_id=v_jid, entry_type="PREEMPT_COMPENSATION", delta_available=comp, delta_frozen=0, now_epoch=int(now_ep))
    conn.execute(
        "UPDATE jobs SET status = 'PREEMPTED', worker_node_id = NULL, lease_until_epoch = NULL, version = version + 1 WHERE job_id = ?",
        (v_jid,),
    )

    lease_until = int(now_ep) + int(lease_sec)
    conn.execute(
        "UPDATE jobs SET status = 'RUNNING', worker_node_id = ?, lease_until_epoch = ?, version = version + 1 WHERE job_id = ?",
        (node_id, lease_until, high_row["job_id"]),
    )
    claimed = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (high_row["job_id"],)).fetchone()
    return {{
        "evicted_job_id": v_jid,
        "evicted_tenant_id": v_tid,
        "refunded_cents": res,
        "compensation_cents": comp,
        "claimed_job": _job_dict(claimed),
    }}


def get_global_audit(conn: sqlite3.Connection) -> dict:
    if not {str(bool(do_full_audit))}:
        tot = conn.execute(
            "SELECT COALESCE(SUM(balance_cents), 0) AS a, COALESCE(SUM(frozen_cents), 0) AS f FROM tenants"
        ).fetchone()
        return {{
            "all_balanced": True,
            "total_available_cents": int(tot["a"]),
            "total_frozen_cents": int(tot["f"]),
            "active_escrow_cents": int(tot["f"]),
            "total_platform_revenue_cents": 0,
            "anomalies": [],
        }}

    anomalies: list[str] = []
    tenants = conn.execute("SELECT * FROM tenants").fetchall()
    total_avail = 0
    total_frozen = 0

    for t in tenants:
        tid = t["tenant_id"]
        bal = int(t["balance_cents"])
        frz = int(t["frozen_cents"])
        carry = int(t["initial_Carry_cents"]) if "initial_Carry_cents" in t.keys() else 0
        total_avail += bal
        total_frozen += frz
        if bal < 0 or frz < 0:
            anomalies.append(f"Tenant {{tid}} negative balance/frozen")
        l_sum = conn.execute(
            """
            SELECT COALESCE(SUM(delta_available), 0) AS sa, COALESCE(SUM(delta_frozen), 0) AS sf
            FROM ledger_entries WHERE tenant_id = ?
            """,
            (tid,),
        ).fetchone()
        if int(l_sum["sa"]) + carry != bal:
            anomalies.append(f"Tenant {{tid}} available ledger mismatch: {{l_sum['sa']}} + {{carry}} != {{bal}}")
        if int(l_sum["sf"]) != frz:
            anomalies.append(f"Tenant {{tid}} frozen ledger mismatch: {{l_sum['sf']}} != {{frz}}")

    esc_row = conn.execute(
        "SELECT COALESCE(SUM(reserved_cents), 0) AS esc FROM jobs WHERE status IN ('QUEUED', 'RUNNING')"
    ).fetchone()
    active_escrow = int(esc_row["esc"])
    if total_frozen != active_escrow:
        anomalies.append(f"Global frozen ({{total_frozen}}) != active job escrow ({{active_escrow}})")

    rev_row = conn.execute(
        "SELECT COALESCE(SUM(final_charge_cents), 0) AS sc, COALESCE(SUM(penalty_cents), 0) AS pc FROM jobs"
    ).fetchone()
    total_rev = int(rev_row["sc"]) + int(rev_row["pc"])
    sub_row = conn.execute(
        "SELECT COALESCE(SUM(delta_available), 0) AS sub FROM ledger_entries WHERE entry_type = 'PREEMPT_COMPENSATION'"
    ).fetchone()
    total_sub = int(sub_row["sub"])
    org_row = conn.execute(
        "SELECT COALESCE(SUM(delta_available), 0) AS net FROM ledger_entries WHERE entry_type IN ('ORG_TRANSFER_OUT', 'ORG_TRANSFER_IN')"
    ).fetchone()
    org_ok = int(org_row["net"]) == 0
    if not org_ok:
        anomalies.append(f"org_transfer_imbalance:{{org_row['net']}}")

    return {{
        "all_balanced": len(anomalies) == 0,
        "total_available_cents": total_avail,
        "total_frozen_cents": total_frozen,
        "active_escrow_cents": active_escrow,
        "total_platform_revenue_cents": total_rev,
        "total_subsidy_cents": total_sub,
        "net_platform_revenue_cents": total_rev - total_sub,
        "org_transfer_balanced": org_ok,
        "anomalies": anomalies,
    }}
'''


def _gen_overloaded_monolith() -> str:
    """当甩手掌柜统帅只建 1 个工人、不立契约、把整篇 TASK.md 原样塞进来时，弱模型产出的顾头不顾尾残次单文件。"""
    return '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monolith generated under severe weak-model context overload (missing HMAC, brackets, ledger, reaper)."""
from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def create_server(db_path: str, host: str = "127.0.0.1", port: int = 0, admin_token: str = "marshal-admin-secret") -> ThreadingHTTPServer:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, name TEXT, tier TEXT, balance INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, tenant_id TEXT, status TEXT, fee INTEGER DEFAULT 0)")
    conn.commit()

    class WeakHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _json(self, code: int, obj: dict):
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._json(200, {"ok": True, "data": {"status": "healthy"}})
                return
            self._json(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "missing"}})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/v1/admin/tenants":
                tid = body.get("tenant_id", "t1")
                conn.execute("INSERT OR REPLACE INTO tenants VALUES (?, ?, ?, 0)", (tid, body.get("name", ""), body.get("tier", "STARTER")))
                conn.commit()
                # 字段名缺失且无鉴权检查
                self._json(201, {"ok": True, "data": {"tenant_id": tid, "balance_cents": 0}})
                return
            self._json(501, {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "not implemented"}})

    return ThreadingHTTPServer((host, port), WeakHandler)
'''


def _gen_app_module(sig: dict, mods: dict[str, str], has_separate_auth: bool) -> str:
    if sig["overloaded"] and not sig["has_contract"]:
        return _gen_overloaded_monolith()

    db_mod = mods["db"]
    jobs_mod = mods["jobs"]
    auth_mod = mods["auth"]

    inline_auth = ""
    auth_import = f"from . import {auth_mod} as auth_layer"
    if not has_separate_auth:
        auth_import = ""
        inline_auth = _gen_auth_module(sig).split('"""\nfrom __future__ import annotations\n', 1)[-1] + "\nclass _InlineAuth:\n    verify_admin_headers = staticmethod(verify_admin_headers)\n    verify_tenant_signature = staticmethod(verify_tenant_signature)\nauth_layer = _InlineAuth()\n"

    return f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CloudHub HTTP Gateway (`cloudhub/app.py`)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import {db_mod} as db_layer
from . import {jobs_mod} as jobs_layer
{auth_import}
{inline_auth}

_RE_TENANT_TOPUP = re.compile(r"^/api/v1/tenants/([^/]+)/topup$")
_RE_TENANT_STMT = re.compile(r"^/api/v1/tenants/([^/]+)/statement$")
_RE_TENANT_BIND = re.compile(r"^/api/v1/admin/tenants/([^/]+)/bind-parent$")
_RE_JOB_DETAIL = re.compile(r"^/api/v1/jobs/([^/]+)$")
_RE_JOB_ACTION = re.compile(r"^/api/v1/jobs/([^/]+)/(settle|fail|cancel)$")


def create_server(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    admin_token: str = "marshal-admin-secret",
) -> ThreadingHTTPServer:
    db_layer.init_db(db_path)

    def _lookup_secret(tenant_id: str) -> str | None:
        with db_layer.tx(db_path) as conn:
            row = conn.execute("SELECT api_secret FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()
            return str(row["api_secret"]) if row is not None else None

    class CloudHubHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _err(self, code: int, err_code: str, msg: str) -> None:
            self._send(code, {{"ok": False, "error": {{"code": err_code, "message": msg}}}})

        def _read_body(self) -> tuple[bytes, dict | None]:
            raw_len = self.headers.get("Content-Length")
            n = int(raw_len) if raw_len else 0
            raw = self.rfile.read(n) if n > 0 else b""
            if not raw:
                return b"", {{}}
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    return raw, None
                return raw, parsed
            except Exception:
                return raw, None

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._send(200, {{"ok": True, "data": {{"status": "healthy"}}}})
                return

            if path == "/api/v1/admin/audit":
                ok, c, ec, msg = auth_layer.verify_admin_headers(self.headers, admin_token)
                if not ok:
                    self._err(c, ec, msg)
                    return
                with db_layer.tx(db_path) as conn:
                    data = jobs_layer.get_global_audit(conn)
                self._send(200, {{"ok": True, "data": data}})
                return

            m_stmt = _RE_TENANT_STMT.match(path)
            if m_stmt:
                ok, c, ec, msg, auth_tid = auth_layer.verify_tenant_signature(
                    self.headers, method="GET", path=path, body_bytes=b"", lookup_tenant_secret=_lookup_secret
                )
                if not ok:
                    self._err(c, ec, msg)
                    return
                target_tid = m_stmt.group(1)
                if target_tid != auth_tid:
                    self._err(403, "TENANT_FORBIDDEN", "Cross-tenant statement access denied")
                    return
                try:
                    with db_layer.tx(db_path) as conn:
                        data = jobs_layer.get_tenant_statement(conn, target_tid)
                    self._send(200, {{"ok": True, "data": data}})
                except jobs_layer.DomainError as de:
                    self._err(de.status_code, de.code, de.message)
                return

            m_job = _RE_JOB_DETAIL.match(path)
            if m_job:
                ok, c, ec, msg, auth_tid = auth_layer.verify_tenant_signature(
                    self.headers, method="GET", path=path, body_bytes=b"", lookup_tenant_secret=_lookup_secret
                )
                if not ok:
                    self._err(c, ec, msg)
                    return
                try:
                    with db_layer.tx(db_path) as conn:
                        data = jobs_layer.get_job_for_tenant(conn, auth_tid, m_job.group(1))
                    self._send(200, {{"ok": True, "data": data}})
                except jobs_layer.DomainError as de:
                    self._err(de.status_code, de.code, de.message)
                return

            self._err(404, "NOT_FOUND", f"Unknown route: {{path}}")

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            raw_body, body = self._read_body()

            # Admin & Worker endpoints
            m_bind = _RE_TENANT_BIND.match(path)
            if m_bind or path in ("/api/v1/admin/tenants", "/api/v1/admin/vouchers", "/api/v1/admin/reap", "/api/v1/workers/claim", "/api/v1/workers/preempt"):
                ok, c, ec, msg = auth_layer.verify_admin_headers(self.headers, admin_token)
                if not ok:
                    self._err(c, ec, msg)
                    return
                if body is None:
                    self._err(400, "INVALID_ARGUMENT", "Malformed JSON object")
                    return
                try:
                    with db_layer.tx(db_path) as conn:
                        if m_bind:
                            out_code, out_payload = 200, {{"ok": True, "data": jobs_layer.bind_parent_tenant(conn, m_bind.group(1), body)}}
                        elif path == "/api/v1/admin/tenants":
                            out_code, out_payload = 201, {{"ok": True, "data": jobs_layer.create_tenant(conn, body)}}
                        elif path == "/api/v1/admin/vouchers":
                            out_code, out_payload = 201, {{"ok": True, "data": jobs_layer.create_voucher(conn, body)}}
                        elif path == "/api/v1/admin/reap":
                            out_code, out_payload = 200, {{"ok": True, "data": jobs_layer.reap_expired_jobs(conn, body)}}
                        elif path == "/api/v1/workers/preempt":
                            out_code, out_payload = 200, {{"ok": True, "data": jobs_layer.preempt_spot_for_high(conn, body)}}
                        else:
                            out_code, out_payload = 200, {{"ok": True, "data": jobs_layer.claim_next_job(conn, body)}}
                    self._send(out_code, out_payload)
                except jobs_layer.DomainError as de:
                    self._err(de.status_code, de.code, de.message)
                return

            # Tenant HMAC endpoints
            ok, c, ec, msg, auth_tid = auth_layer.verify_tenant_signature(
                self.headers, method="POST", path=path, body_bytes=raw_body, lookup_tenant_secret=_lookup_secret
            )
            if not ok:
                self._err(c, ec, msg)
                return
            if body is None:
                self._err(400, "INVALID_ARGUMENT", "Malformed JSON object")
                return

            idem_hdr = (self.headers.get("Idempotency-Key") or "").strip()
            idem_scope = f"{{auth_tid}}:POST:{{path}}:{{idem_hdr}}" if idem_hdr else None
            body_sha = hashlib.sha256(raw_body).hexdigest()

            try:
                out_code = 200
                out_payload = None
                err_tuple = None
                with db_layer.tx(db_path) as conn:
                    m_top = _RE_TENANT_TOPUP.match(path)
                    m_act = _RE_JOB_ACTION.match(path)
                    if m_top and m_top.group(1) != auth_tid:
                        err_tuple = (403, "TENANT_FORBIDDEN", "Cannot topup another tenant")
                    elif m_act:
                        jchk = conn.execute("SELECT tenant_id FROM jobs WHERE job_id = ?", (m_act.group(1),)).fetchone()
                        if jchk is None:
                            err_tuple = (404, "NOT_FOUND", "Job not found")
                        elif jchk["tenant_id"] != auth_tid:
                            err_tuple = (403, "TENANT_FORBIDDEN", "Forbidden cross-tenant job action")
                    elif not m_top and path != "/api/v1/jobs":
                        err_tuple = (404, "NOT_FOUND", f"Unknown route: {{path}}")

                    if err_tuple is None:
                        state_idem, cached_code, cached_obj = db_layer.check_idempotency(conn, idem_scope, body_sha)
                        if state_idem in ("HIT", "CONFLICT"):
                            out_code, out_payload = int(cached_code), cached_obj
                        elif m_top:
                            data = jobs_layer.topup_tenant(conn, auth_tid, body)
                            out_code, out_payload = 200, {{"ok": True, "data": data}}
                            db_layer.save_idempotency(conn, idem_scope, body_sha, 200, out_payload)
                        elif path == "/api/v1/jobs":
                            data = jobs_layer.submit_job(conn, auth_tid, body)
                            out_code, out_payload = 201, {{"ok": True, "data": data}}
                            db_layer.save_idempotency(conn, idem_scope, body_sha, 201, out_payload)
                        elif m_act:
                            jid, act = m_act.group(1), m_act.group(2)
                            if act == "settle":
                                data = jobs_layer.settle_job(conn, auth_tid, jid, body)
                            elif act == "fail":
                                data = jobs_layer.fail_job(conn, auth_tid, jid, body)
                            else:
                                data = jobs_layer.cancel_job(conn, auth_tid, jid, body)
                            out_code, out_payload = 200, {{"ok": True, "data": data}}
                            db_layer.save_idempotency(conn, idem_scope, body_sha, 200, out_payload)
                if err_tuple is not None:
                    self._err(*err_tuple)
                else:
                    self._send(out_code, out_payload)
            except jobs_layer.DomainError as de:
                self._err(de.status_code, de.code, de.message)

    srv = ThreadingHTTPServer((host, port), CloudHubHandler)
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
'''


def _gen_tests_module(sig: dict) -> str:
    deep = sig["focused_ticket"] and ("spot" in (sig.get("ticket_raw", "")).lower() or sig["strong_contract"])
    if not deep:
        return '''# -*- coding: utf-8 -*-
"""Basic connectivity test suite written by worker."""
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from cloudhub.app import create_server


class TestBasicCloudHub(unittest.TestCase):
    def test_health_and_tenant(self):
        with tempfile.TemporaryDirectory() as tmp:
            srv = create_server(str(Path(tmp) / "t.db"), admin_token="adm")
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                    self.assertEqual(r.status, 200)
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
'''

    return '''# -*- coding: utf-8 -*-
"""Comprehensive contract & edge-case integration tests for CloudHub."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from cloudhub.app import create_server


def _sign(secret: str, method: str, path: str, ts: str, body: bytes) -> str:
    bh = hashlib.sha256(body).hexdigest()
    msg = f"{method}\\n{path}\\n{ts}\\n{bh}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class TestCloudHubIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        self.admin_token = "adm-secret"
        self.srv = create_server(self.db_path, admin_token=self.admin_token)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def _call(self, method: str, path: str, payload=None, *, admin=False, tenant=None, secret="s1", idem=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["X-Admin-Token"] = self.admin_token
        if tenant:
            ts = str(int(time.time()))
            headers["X-Tenant-Id"] = tenant
            headers["X-Timestamp"] = ts
            headers["X-Signature"] = _sign(secret, method, path, ts, body)
        if idem:
            headers["Idempotency-Key"] = idem
        req = urllib.request.Request(self.base + path, data=(body if method != "GET" else None), headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_spot_peak_exemption_and_brackets(self):
        self._call("POST", "/api/v1/admin/tenants", {"tenant_id": "t1", "name": "T1", "tier": "STARTER", "api_secret": "s1"}, admin=True)
        self._call("POST", "/api/v1/tenants/t1/topup", {"amount_cents": 50000}, tenant="t1")
        # UTC 12:00 peak hour: 1700049600 -> (1700049600 // 3600) % 24 == 12 (peak!)
        # SPOT must NOT multiply by 1.2! 100s * 2 cents * 0.7 = 140 cents (if bugged with 1.2x, becomes 168!)
        c, d = self._call(
            "POST",
            "/api/v1/jobs",
            {"job_id": "j_spot", "gpu_type": "A10", "priority": "SPOT", "estimated_gpu_sec": 100, "submitted_epoch": 1700049600},
            tenant="t1",
        )
        self.assertEqual(c, 201)
        self.assertEqual(d["data"]["reserved_cents"], 140, "SPOT priority must NOT be multiplied by 1.2x peak multiplier!")

    def test_running_cancel_penalty_and_voucher_release(self):
        self._call("POST", "/api/v1/admin/tenants", {"tenant_id": "t2", "name": "T2", "tier": "STARTER", "api_secret": "s1"}, admin=True)
        self._call("POST", "/api/v1/tenants/t2/topup", {"amount_cents": 10000}, tenant="t2")
        self._call(
            "POST",
            "/api/v1/admin/vouchers",
            {"code": "V100", "tenant_id": "t2", "discount_cents": 100, "min_spend_cents": 200, "expires_at_epoch": 1800000000},
            admin=True,
        )
        # Off-peak UTC 02:00: 1700013600 -> 200s * 2 = 400 - 100 voucher = 300 reserved
        c, _ = self._call(
            "POST",
            "/api/v1/jobs",
            {"job_id": "j_run_c", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013600, "voucher_code": "V100"},
            tenant="t2",
        )
        self.assertEqual(c, 201)
        self._call("POST", "/api/v1/workers/claim", {"worker_node_id": "w1", "now_epoch": 1700013610}, admin=True)
        c, cancel_res = self._call("POST", "/api/v1/jobs/j_run_c/cancel", {"now_epoch": 1700013620}, tenant="t2")
        self.assertEqual(c, 200)
        self.assertEqual(cancel_res["data"]["penalty_cents"], 30)

        # Voucher V100 must be released after RUNNING -> CANCELLED so a new job can reuse it!
        c2, _ = self._call(
            "POST",
            "/api/v1/jobs",
            {"job_id": "j_reuse", "gpu_type": "A10", "priority": "NORMAL", "estimated_gpu_sec": 200, "submitted_epoch": 1700013630, "voucher_code": "V100"},
            tenant="t2",
        )
        self.assertEqual(c2, 201, "Voucher must be released when RUNNING job is cancelled!")

        _, audit = self._call("GET", "/api/v1/admin/audit", admin=True)
        self.assertTrue(audit["data"]["all_balanced"], f"Audit anomalies: {audit['data']['anomalies']}")
        self.assertEqual(audit["data"]["anomalies"], [])

    def test_three_brackets_half_up_and_idempotency_guards(self):
        self._call("POST", "/api/v1/admin/tenants", {"tenant_id": "te", "name": "TE", "tier": "ENTERPRISE", "api_secret": "s1"}, admin=True)
        self._call("POST", "/api/v1/tenants/te/topup", {"amount_cents": 80000}, tenant="te", idem="k_top_1")
        c_rep, d_rep = self._call("POST", "/api/v1/tenants/te/topup", {"amount_cents": 80000}, tenant="te", idem="k_top_1")
        self.assertEqual(c_rep, 200)
        self.assertEqual(d_rep["data"]["balance_cents"], 80000)
        c_conf, _ = self._call("POST", "/api/v1/tenants/te/topup", {"amount_cents": 999}, tenant="te", idem="k_top_1")
        self.assertEqual(c_conf, 409)

        c3b, d3b = self._call(
            "POST",
            "/api/v1/jobs",
            {"job_id": "j_3b", "gpu_type": "H100", "priority": "NORMAL", "estimated_gpu_sec": 4000, "submitted_epoch": 1700013600},
            tenant="te",
        )
        self.assertEqual(c3b, 201)
        self.assertEqual(d3b["data"]["reserved_cents"], 31104)
        c_bad, _ = self._call("POST", "/api/v1/tenants/te/topup", {"amount_cents": True}, tenant="te")
        self.assertEqual(c_bad, 400)


if __name__ == "__main__":
    unittest.main()
'''


def execute_mock_worker(
    *,
    staging_workspace: Path,
    worker: dict,
    contract_text: str,
    ticket: str,
    is_rework: bool,
    review_comment: str,
    mock_cfg: dict,
) -> dict:
    allowed_paths: list[str] = list(worker["allowed_paths"])
    sig = _analyze_quality_signals(
        contract_text=contract_text,
        role_prompt=worker.get("role_prompt", ""),
        ticket=ticket,
        allowed_paths=allowed_paths,
        is_rework=is_rework,
        review_comment=review_comment,
        mock_cfg=mock_cfg,
    )
    sig["ticket_raw"] = ticket

    # 若工作台中已有上一阶段合入并拷贝进来的重构版模块（如二阶段增量演进），保留一阶段已修复的契约特性
    existing_corpus = ""
    for rel in allowed_paths:
        ef = Path(staging_workspace) / Path(rel)
        if ef.is_file():
            existing_corpus += "\n" + ef.read_text(encoding="utf-8", errors="ignore")
    if 'priority != "SPOT"' in existing_corpus:
        sig["fix_spot_peak"] = True
    if "tier3 = max(0, sec - 3600)" in existing_corpus:
        sig["mentions_brackets"] = True
    if "compute_billable_seconds" in existing_corpus and "* 120) // 100" in existing_corpus:
        sig["mentions_overrun"] = True
    if "initial_Carry_cents" in existing_corpus:
        sig["mentions_migration"] = True
        sig["mentions_idempotency"] = True
        sig["mentions_concurrency"] = True
    if "DomainError" in existing_corpus:
        sig["mentions_vouchers"] = True
        sig["mentions_running_penalty"] = True
        sig["mentions_reaper_retry"] = True
        sig["mentions_audit"] = True
        if "if True:\n            _release_voucher" in existing_corpus:
            sig["fix_voucher_release"] = True

    mods = _discover_contract_modules(contract_text, allowed_paths)
    roles_in_worker = {rel: _classify_file_role(rel, worker.get("role_prompt", ""), ticket) for rel in allowed_paths}

    # 检查全局契约或当前工位是否单独拆出了 auth 模块
    has_separate_auth = ("auth" in roles_in_worker.values()) or ("cloudhub/auth.py" in (contract_text or ""))

    notes: list[str] = []
    written: list[str] = []
    for rel, f_role in roles_in_worker.items():
        dst = Path(staging_workspace) / Path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if f_role == "db":
            code = _gen_db_module(sig)
            notes.append(f"已实现持久层模块 `{rel}`")
        elif f_role == "pricing":
            code = _gen_pricing_module(sig)
            notes.append(f"已实现计费配额模块 `{rel}`")
        elif f_role == "auth":
            code = _gen_auth_module(sig)
            notes.append(f"已实现鉴权签名模块 `{rel}`")
        elif f_role == "jobs":
            code = _gen_jobs_module(sig, mods)
            notes.append(f"已实现作业状态机与对账模块 `{rel}`")
        elif f_role == "app":
            code = _gen_app_module(sig, mods, has_separate_auth=has_separate_auth)
            notes.append(f"已实现网关入口模块 `{rel}`")
        else:
            code = _gen_tests_module(sig)
            notes.append(f"已编写自测套件 `{rel}`")
        dst.write_text(code, encoding="utf-8")
        written.append(rel)

    if sig["overloaded"]:
        notes.append("（工单内容过于庞杂，已尽力填充主流程，部分深层边界暂作简化处理）")
    elif not sig["has_contract"]:
        notes.append("（当前缺少全局架构契约，模块内部签名与表字段已按本工位理解自行命名）")
    elif is_rework:
        notes.append("（已按统帅代码审查批注针对性修正对应逻辑并通过语法校验）")

    est_in = max(250, (len(contract_text) + len(ticket) + len(review_comment)) // 3)
    est_out = max(300, sum(len((Path(staging_workspace) / Path(r)).read_text(encoding="utf-8")) for r in written) // 3)

    return {
        "ok": True,
        "harness": "mock",
        "worker_id": worker["worker_id"],
        "model_active": "mock/weak-flash-lite-v1",
        "thinking_level": "off",
        "elapsed_ms": 45,
        "summary": "；".join(notes) + "。",
        "tokens": {
            "input_tokens": est_in,
            "output_tokens": est_out,
            "total_tokens": est_in + est_out,
        },
    }
