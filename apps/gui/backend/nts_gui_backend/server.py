from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from nts_gui_backend.service import GuiService, GuiServiceError


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class NtsGuiRequestHandler(BaseHTTPRequestHandler):
    service: GuiService

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _dispatch(self, method: str) -> None:
        if self.path.startswith("/api/"):
            self._dispatch_api(method)
            return
        self._serve_static()

    def _dispatch_api(self, method: str) -> None:
        try:
            body = self._read_json_body() if method == "POST" else {}
            payload = self.service.handle(method, self.path, body)
            self._write_json(200, {"ok": True, "data": payload})
        except GuiServiceError as exc:
            self._write_json(exc.status, {"ok": False, "error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": {"code": "server_error", "message": str(exc)}})

    def _serve_static(self) -> None:
        rel_path = unquote(self.path.split("?", 1)[0]).lstrip("/") or "index.html"
        if rel_path == "app":
            rel_path = "index.html"
        target = (FRONTEND_DIR / rel_path).resolve()
        if not str(target).startswith(str(FRONTEND_DIR.resolve())) or not target.exists() or not target.is_file():
            target = FRONTEND_DIR / "index.html"
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self._write_no_cache_headers()
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        loaded = json.loads(raw or "{}")
        if not isinstance(loaded, dict):
            raise GuiServiceError("invalid_json", "Request body must be a JSON object.")
        return loaded

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._write_no_cache_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _write_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")


def make_server(host: str, port: int, workspace: Path | None = None) -> ThreadingHTTPServer:
    handler_class = type("ConfiguredNtsGuiRequestHandler", (NtsGuiRequestHandler,), {})
    handler_class.service = GuiService(workspace_path=workspace)
    return ThreadingHTTPServer((host, port), handler_class)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local NTS Studio GUI backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args()
    server = make_server(args.host, args.port, workspace=args.workspace)
    print(f"NTS Studio backend running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
