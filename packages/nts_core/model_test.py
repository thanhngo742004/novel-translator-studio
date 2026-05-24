from __future__ import annotations

import hashlib
from typing import Any

from nts_storage.database import connect, insert_task_run, json_dumps, new_id, utc_now
from nts_storage.workspace import Workspace


def _mock_response(prompt: str) -> dict[str, Any]:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {
        "provider": "mock",
        "model": "mock-deterministic-v1",
        "output": f"mock:{digest[:16]}",
        "prompt_hash": digest,
    }


def run_mock_model_test(workspace: Workspace, *, provider_key: str, prompt: str) -> dict[str, Any]:
    if provider_key != "mock":
        raise ValueError("MVP0 only supports provider `mock`.")

    response = _mock_response(prompt)
    now = utc_now()
    with connect(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="model.test",
            status="success",
            stage="completed",
            input_data={"provider": provider_key, "prompt": prompt},
            result_data=response,
        )
        model_run_id = new_id("modelrun")
        conn.execute(
            """
            INSERT INTO model_runs (
                id, task_run_id, provider_key, adapter_type, base_url, model_name,
                prompt_hash, input_tokens, output_tokens, cost_estimate, status,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_run_id,
                task_id,
                provider_key,
                "mock",
                "mock://local",
                response["model"],
                response["prompt_hash"],
                len(prompt.split()),
                len(response["output"].split()),
                0.0,
                "success",
                now,
                now,
            ),
        )
        conn.commit()

    return {
        "task_run_id": task_id,
        "model_run_id": model_run_id,
        "provider": provider_key,
        "response": response,
        "raw_result_json": json_dumps(response),
    }

