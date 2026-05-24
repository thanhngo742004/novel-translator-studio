from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from nts_core.config import validate_config_files
from nts_core.doctor import build_doctor_report
from nts_core.model_test import run_mock_model_test
from nts_core.projects import create_project, list_projects
from nts_shared.envelopes import error_envelope, success_envelope
from nts_storage.workspace import WorkspaceError, discover_workspace, init_workspace

app = typer.Typer(help="Novel Translator Studio CLI.")
project_app = typer.Typer(help="Project commands.")
config_app = typer.Typer(help="Config commands.")
model_app = typer.Typer(help="Model commands.")
app.add_typer(project_app, name="project")
app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")


class CliState:
    def __init__(self) -> None:
        self.workspace: Optional[Path] = None


state = CliState()


WorkspaceOption = Annotated[
    Optional[Path],
    typer.Option("--workspace", "-w", help="Workspace path. Overrides discovery."),
]


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = payload.get("status", "unknown")
        data = payload.get("data")
        typer.echo(f"{status}: {data if data is not None else payload}")


def _fail(code: str, message: str, exit_code: int, as_json: bool) -> None:
    _print(error_envelope(code=code, message=message), as_json)
    raise typer.Exit(exit_code)


def _workspace_arg(command_workspace: Path | None = None) -> Path | None:
    return command_workspace or state.workspace


@app.callback()
def main(
    workspace: WorkspaceOption = None,
) -> None:
    state.workspace = workspace


@app.command()
def init(
    workspace: Annotated[Path, typer.Option("--workspace", "-w", help="Workspace path.")] = Path(
        "workspace"
    ),
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    ws = init_workspace(workspace)
    _print(success_envelope({"workspace": str(ws.path), "db_path": str(ws.db_path)}), json_output)


@app.command()
def doctor(
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        report = build_doctor_report(ws)
    except WorkspaceError as exc:
        _fail("WORKSPACE_NOT_INITIALIZED", str(exc), 7, json_output)
    _print(success_envelope(report), json_output)


@project_app.command("create")
def project_create(
    slug: Annotated[str, typer.Option("--slug")],
    name: Annotated[str, typer.Option("--name")],
    source_lang: Annotated[str, typer.Option("--source-lang")],
    target_lang: Annotated[str, typer.Option("--target-lang")],
    workspace: WorkspaceOption = None,
    domain: Annotated[str, typer.Option("--domain")] = "novel",
    genre: Annotated[Optional[str], typer.Option("--genre")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        project = create_project(
            ws,
            slug=slug,
            name=name,
            source_lang=source_lang,
            target_lang=target_lang,
            domain=domain,
            genre=genre,
        )
    except WorkspaceError as exc:
        _fail("WORKSPACE_NOT_INITIALIZED", str(exc), 7, json_output)
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(project), json_output)


@project_app.command("list")
def project_list(
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        projects = list_projects(ws)
    except WorkspaceError as exc:
        _fail("WORKSPACE_NOT_INITIALIZED", str(exc), 7, json_output)
    _print(success_envelope({"projects": projects}), json_output)


@config_app.command("validate")
def config_validate(
    workspace: WorkspaceOption = None,
    providers: Annotated[
        Optional[Path],
        typer.Option("--providers", help="Path to providers YAML."),
    ] = None,
    routing: Annotated[
        Optional[Path],
        typer.Option("--routing", help="Path to task routing YAML."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
    except WorkspaceError:
        ws = None
    try:
        result = validate_config_files(providers=providers, routing=routing, workspace=ws)
    except ValueError as exc:
        _fail("CONFIG_ERROR", str(exc), 7, json_output)
    _print(success_envelope(result), json_output)


@model_app.command("test")
def model_test(
    provider: Annotated[str, typer.Option("--provider")],
    workspace: WorkspaceOption = None,
    prompt: Annotated[str, typer.Option("--prompt")] = "ping",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = run_mock_model_test(ws, provider_key=provider, prompt=prompt)
    except WorkspaceError as exc:
        _fail("WORKSPACE_NOT_INITIALIZED", str(exc), 7, json_output)
    except ValueError as exc:
        _fail("PROVIDER_ERROR", str(exc), 5, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


if __name__ == "__main__":
    app()
