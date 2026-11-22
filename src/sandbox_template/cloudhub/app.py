#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CloudHub HTTP Gateway skeleton (`cloudhub/app.py`).

初始骨架仅提供 `/health` 占位。全部业务路由、鉴权、账本、计费与作业状态机均须由统帅派单给下属工位实现并合入。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def create_server(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 0,
    admin_token: str = "marshal-admin-secret",
) -> ThreadingHTTPServer:
    class SkeletonHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/health":
                self._send(200, {"ok": True, "data": {"status": "healthy", "skeleton": True}})
                return
            self._send(501, {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "Route not implemented yet"}})

        def do_POST(self) -> None:
            self._send(501, {"ok": False, "error": {"code": "NOT_IMPLEMENTED", "message": "Route not implemented yet"}})

    srv = ThreadingHTTPServer((host, port), SkeletonHandler)
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
