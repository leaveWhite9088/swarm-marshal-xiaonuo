# -*- coding: utf-8 -*-
"""物理禁手监控、白名单领地校验与工单代写代码拦截器（guardrails.py）。"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import PurePosixPath, Path

IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".git"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}


def is_ignored_workspace_file(rel_posix: str) -> bool:
    parts = PurePosixPath(rel_posix).parts
    if any(p in IGNORED_DIRS for p in parts):
        return True
    lower = rel_posix.lower()
    return any(lower.endswith(sfx) for sfx in IGNORED_SUFFIXES)


def compute_workspace_manifest(workspace_dir: Path) -> dict[str, str]:
    """计算沙盒主干业务目录的逐文件 SHA-256 清单。"""
    ws = Path(workspace_dir).resolve()
    manifest: dict[str, str] = {}
    if not ws.exists():
        return manifest
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ws).as_posix()
        if is_ignored_workspace_file(rel):
            continue
        manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


def compute_manifest_digest(manifest: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(manifest):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(manifest[rel].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def verify_workspace_integrity(workspace_dir: Path, expected_manifest: dict[str, str]) -> tuple[bool, list[str]]:
    """对比当前 workspace 与最近一次合法合入快照，检测任何绕过调度器的非授权直写。"""
    actual = compute_workspace_manifest(workspace_dir)
    anomalies: list[str] = []
    all_keys = sorted(set(expected_manifest) | set(actual))
    for k in all_keys:
        if k not in expected_manifest:
            anomalies.append(f"非授权新增文件: {k}")
        elif k not in actual:
            anomalies.append(f"非授权删除文件: {k}")
        elif expected_manifest[k] != actual[k]:
            anomalies.append(f"非授权篡改文件: {k}")
    return (len(anomalies) == 0), anomalies


def set_workspace_writable(workspace_dir: Path, writable: bool) -> None:
    """在主干空闲期将业务文件设为只读属性，从操作系统层阻断统帅误用直写工具。"""
    ws = Path(workspace_dir)
    if not ws.exists():
        return
    for p in ws.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ws).as_posix()
        if is_ignored_workspace_file(rel):
            continue
        try:
            mode = p.stat().st_mode
            if writable:
                os.chmod(p, mode | stat.S_IWRITE | stat.S_IREAD)
            else:
                os.chmod(p, (mode & ~stat.S_IWRITE) | stat.S_IREAD)
        except OSError:
            pass


def validate_allowed_paths(raw_paths: list[str], limits_cfg: dict) -> tuple[bool, list[str], str]:
    """校验工位读写白名单：禁止通配符、目录级大包大揽、越界路径，强制单工位文件数上限。"""
    max_files = int(limits_cfg.get("max_files_per_worker", 4))
    allowed_prefixes = tuple(limits_cfg.get("allowed_root_prefixes") or ["cloudhub/", "tests/"])
    protected = set(limits_cfg.get("protected_paths") or ["TASK.md", "smoke_public.py", "GLOBAL_CONTRACT.md"])

    if not isinstance(raw_paths, list) or not raw_paths:
        return False, [], "allowed_paths 不能为空列表，必须明确指定该工位负责的具体文件路径。"

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        s = str(raw or "").strip().replace("\\", "/")
        while s.startswith("./"):
            s = s[2:]
        if not s:
            continue
        if any(ch in s for ch in ("*", "?", "[", "]")):
            return False, [], f"禁止使用通配符白名单（{raw}）：必须逐一指定明确的文件路径。"
        if s.startswith("/") or ":" in s or ".." in s.split("/"):
            return False, [], f"禁止使用绝对路径或上级越界路径（{raw}）。"
        if s.endswith("/"):
            return False, [], f"禁止将整个目录加入白名单（{raw}）：请细化到具体 .py 文件路径。"
        if s in protected:
            return False, [], f"路径 {s} 为沙盒受保护只读文件，不可分配给工位修改。"
        if not s.startswith(allowed_prefixes):
            return False, [], f"路径 {s} 不在允许的业务/测试前缀 {list(allowed_prefixes)} 内。"
        if not s.endswith(".py"):
            return False, [], f"白名单仅允许绑定 Python 模块或测试文件（.py），收到: {s}"
        if s not in seen:
            seen.add(s)
            cleaned.append(s)

    if not cleaned:
        return False, [], "归一化后 allowed_paths 为空。"
    if len(cleaned) > max_files:
        return (
            False,
            [],
            f"单工位绑定文件数超限（收到 {len(cleaned)} 个，物理上限 {max_files} 个）——反通胀铁律要求合理拆分模块工种，严禁设万能工位！",
        )
    return True, cleaned, ""


_IMPL_STMT_RE = re.compile(
    r"^\s*(?:"
    r"if\s+.+:|elif\s+.+:|else\s*:|"
    r"for\s+.+\s+in\s+.+:|while\s+.+:|"
    r"try\s*:|except(?:\s+[^:]+)?\s*:|finally\s*:|"
    r"with\s+.+:|"
    r"return(?:\s+(?!\.\.\.$).+)|"
    r"raise\s+\w+.*|"
    r"yield\s+.+|"
    r"(?:conn|cur|cursor|db|self)\.[a-zA-Z_]\w*\s*\(.*|"
    r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*\s*\+\=\s*.+|"
    r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*\s*-\=\s*.+|"
    r"assert\s+.+"
    r")\s*(?:#.*)?$"
)

_INDENTED_ASSIGN_RE = re.compile(
    r"^(?:    |\t)+(?:[a-zA-Z_]\w*(?:\s*,\s*[a-zA-Z_]\w*)*)\s*=\s*(?!\.\.\.)[^=].*$"
)
_SIGNATURE_ONLY_RE = re.compile(r"^\s*(?:async\s+)?def\s+[a-zA-Z_]\w*\s*\(.*\)\s*(?:->\s*[^:]+)?\s*:\s*(?:\.\.\.)?\s*$")
_CLASS_SIG_RE = re.compile(r"^\s*class\s+[a-zA-Z_]\w*(?:\(.*\))?\s*:\s*(?:\.\.\.)?\s*$")
_IMPORT_RE = re.compile(r"^\s*(?:from\s+[a-zA-Z0-9_.]+\s+import\s+.+|import\s+[a-zA-Z0-9_.,\s]+)\s*$")


def is_python_implementation_line(line: str) -> bool:
    """判定单行是否属于可执行 Python 业务实现代码（排除接口函数签名、表结构说明与普通自然语言）。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
        return False
    if stripped.startswith("|") or stripped.startswith(">") or stripped.startswith("```"):
        return False
    # 包含大量中文说明的自然语言条件句不算源码代写（除非明显是代码语句如 raise/cursor.execute/assert）
    cjk_count = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    if cjk_count >= 6 and not re.match(r"^\s*(?:raise|return|assert|cursor\.|conn\.|self\.)", line):
        return False
    if _SIGNATURE_ONLY_RE.match(line) or _CLASS_SIG_RE.match(line):
        return False
    if _IMPL_STMT_RE.match(line):
        return True
    if _INDENTED_ASSIGN_RE.match(line) and cjk_count <= 2:
        return True
    if _IMPORT_RE.match(line) and cjk_count == 0:
        return True
    return False


def inspect_code_smuggling(text: str, kind: str, limits_cfg: dict) -> tuple[bool, dict]:
    """检测统帅是否在工单、角色提示词、契约或打回意见中夹带大段现成 Python 业务实现源码。

    返回 (is_clean, detail_dict)。当 is_clean=False 时调用被物理拦截并记入违规。
    """
    smug_cfg = limits_cfg.get("code_smuggling") or {}
    max_consec = int(smug_cfg.get("max_consecutive_code_lines", 7))
    max_total = int(
        smug_cfg.get("max_total_impl_lines_contract", 10)
        if kind == "contract"
        else smug_cfg.get("max_total_impl_lines_ticket", 12)
    )
    max_ratio = float(smug_cfg.get("max_impl_line_ratio", 0.28))

    lines = (text or "").splitlines()
    non_empty = [ln for ln in lines if ln.strip() and not ln.strip().startswith("```")]
    impl_flags = [is_python_implementation_line(ln) for ln in lines]

    total_impl = sum(1 for f in impl_flags if f)
    max_run = 0
    cur_run = 0
    worst_snippet: list[str] = []
    cur_snippet: list[str] = []
    for ln, flag in zip(lines, impl_flags):
        if flag:
            cur_run += 1
            cur_snippet.append(ln)
            if cur_run > max_run:
                max_run = cur_run
                worst_snippet = list(cur_snippet)
        else:
            if not ln.strip():
                # 空白行不重置连续源码计数（防止每两行插空行绕过检测）
                continue
            cur_run = 0
            cur_snippet = []

    ratio = (total_impl / len(non_empty)) if len(non_empty) >= 12 else 0.0
    reasons: list[str] = []
    if max_run > max_consec:
        reasons.append(f"检测到连续 {max_run} 行 Python 业务实现代码（上限 {max_consec} 行）")
    if total_impl > max_total:
        reasons.append(f"累计包含 {total_impl} 行 Python 实现语句（{kind} 允许上限 {max_total} 行）")
    if len(non_empty) >= 12 and ratio > max_ratio:
        reasons.append(f"可执行代码行占比达 {ratio:.1%}（上限 {max_ratio:.0%}）")

    detail = {
        "kind": kind,
        "total_non_empty_lines": len(non_empty),
        "total_impl_lines": total_impl,
        "max_consecutive_impl_lines": max_run,
        "impl_ratio": round(ratio, 4),
        "reasons": reasons,
        "sample": "\n".join(worst_snippet[:8]),
    }
    return (len(reasons) == 0), detail
