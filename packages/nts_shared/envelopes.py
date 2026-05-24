from __future__ import annotations

from typing import Any


def success_envelope(data: Any, task_run_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "success", "data": data}
    if task_run_id:
        payload["task_run_id"] = task_run_id
    return payload


def error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"status": "error", "error": error}

