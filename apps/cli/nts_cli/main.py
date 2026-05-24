from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from nts_core.config import validate_config_files
from nts_core.doctor import build_doctor_report
from nts_core.memory import (
    add_evidence,
    build_bundle,
    create_memory_item,
    list_memory_items,
    parse_json_object,
    show_memory_item,
    update_memory_status,
)
from nts_core.model_test import run_mock_model_test
from nts_core.projects import create_project, get_project_by_slug, list_projects
from nts_core.text_import import get_chapter, import_text_file, list_chapters, list_segments
from nts_core.translation import translate_chapter_mock
from nts_shared.envelopes import error_envelope, success_envelope
from nts_storage.workspace import WorkspaceError, discover_workspace, init_workspace

app = typer.Typer(help="Novel Translator Studio CLI.")
project_app = typer.Typer(help="Project commands.")
config_app = typer.Typer(help="Config commands.")
model_app = typer.Typer(help="Model commands.")
import_app = typer.Typer(help="Import commands.")
text_app = typer.Typer(help="Text commands.")
text_chapters_app = typer.Typer(help="Text chapter commands.")
text_segments_app = typer.Typer(help="Text segment commands.")
memory_app = typer.Typer(help="Memory commands.")
memory_evidence_app = typer.Typer(help="Memory evidence commands.")
memory_status_app = typer.Typer(help="Memory status commands.")
translate_app = typer.Typer(help="Translation commands.")
app.add_typer(project_app, name="project")
app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")
app.add_typer(import_app, name="import")
app.add_typer(text_app, name="text")
app.add_typer(memory_app, name="memory")
app.add_typer(translate_app, name="translate")
text_app.add_typer(text_chapters_app, name="chapters")
text_app.add_typer(text_segments_app, name="segments")
memory_app.add_typer(memory_evidence_app, name="evidence")
memory_app.add_typer(memory_status_app, name="status")


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


def _scope_from_options(ws, project_slug: str | None, scope_json: str | None) -> dict:
    scope = parse_json_object(scope_json, field_name="scope_json")
    if project_slug:
        project = get_project_by_slug(ws, project_slug)
        scope.update(
            {
                "project_id": project["id"],
                "project_slug": project["slug"],
                "domain": project.get("domain"),
                "source_lang": project.get("source_lang"),
                "target_lang": project.get("target_lang"),
                "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
            }
        )
    return scope


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


@import_app.command("text")
def import_text(
    path: Annotated[Path, typer.Argument(help="UTF-8 .txt file to import.")],
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    lang: Annotated[Optional[str], typer.Option("--lang", help="Source language hint.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = import_text_file(ws, path=path, project_slug=project, language=lang)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@text_chapters_app.command("list")
def text_chapters_list(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        chapters = list_chapters(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"chapters": chapters}), json_output)


@text_segments_app.command("list")
def text_segments_list(
    chapter: Annotated[str, typer.Option("--chapter", help="Chapter id.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        segments = list_segments(ws, chapter_id=chapter)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"segments": segments}), json_output)


@memory_app.command("create")
def memory_create(
    memory_type: Annotated[str, typer.Option("--type", help="term/name/pronoun/style/correction")],
    workspace: WorkspaceOption = None,
    status: Annotated[str, typer.Option("--status")] = "pending",
    layer: Annotated[Optional[str], typer.Option("--layer")] = None,
    project: Annotated[Optional[str], typer.Option("--project", help="Optional project slug scope.")] = None,
    scope_json: Annotated[Optional[str], typer.Option("--scope-json")] = None,
    source_key: Annotated[Optional[str], typer.Option("--source-key")] = None,
    target_text: Annotated[Optional[str], typer.Option("--target-text")] = None,
    value_json: Annotated[Optional[str], typer.Option("--value-json")] = None,
    rules_json: Annotated[Optional[str], typer.Option("--rules-json")] = None,
    confidence_score: Annotated[float, typer.Option("--confidence-score")] = 0.0,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        item = create_memory_item(
            ws,
            memory_type=memory_type,
            status=status,
            layer=layer,
            scope=_scope_from_options(ws, project, scope_json),
            source_key=source_key,
            target_text=target_text,
            value=parse_json_object(value_json, field_name="value_json"),
            rules=parse_json_object(rules_json, field_name="rules_json"),
            confidence_score=confidence_score,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"item": item}), json_output)


@memory_app.command("list")
def memory_list(
    workspace: WorkspaceOption = None,
    memory_type: Annotated[Optional[str], typer.Option("--type")] = None,
    status: Annotated[Optional[str], typer.Option("--status")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        items = list_memory_items(ws, memory_type=memory_type, status=status)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"items": items}), json_output)


@memory_app.command("show")
def memory_show(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = show_memory_item(ws, memory_id)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@memory_evidence_app.command("add")
def memory_evidence_add(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    source_kind: Annotated[str, typer.Option("--source-kind")],
    workspace: WorkspaceOption = None,
    artifact_ref: Annotated[Optional[str], typer.Option("--artifact-ref")] = None,
    excerpt_json: Annotated[Optional[str], typer.Option("--excerpt-json")] = None,
    quality_score: Annotated[Optional[float], typer.Option("--quality-score")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        evidence = add_evidence(
            ws,
            memory_item_id=memory_id,
            source_kind=source_kind,
            artifact_ref=artifact_ref,
            excerpt=parse_json_object(excerpt_json, field_name="excerpt_json"),
            quality_score=quality_score,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"evidence": evidence}), json_output)


@memory_status_app.command("set")
def memory_status_set(
    memory_id: Annotated[str, typer.Argument(help="Memory item id.")],
    status: Annotated[str, typer.Option("--status")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        item = update_memory_status(ws, memory_item_id=memory_id, status=status)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"item": item}), json_output)


@memory_app.command("bundle")
def memory_bundle(
    workspace: WorkspaceOption = None,
    project: Annotated[Optional[str], typer.Option("--project", help="Project slug.")] = None,
    text: Annotated[Optional[str], typer.Option("--text", help="Text to retrieve against.")] = None,
    chapter: Annotated[Optional[str], typer.Option("--chapter", help="Chapter id.")] = None,
    top_k: Annotated[int, typer.Option("--top-k")] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        if chapter:
            chapter_row = get_chapter(ws, chapter)
            segments = list_segments(ws, chapter_id=chapter)
            source_text = "\n\n".join(segment["normalized_text"] for segment in segments)
            bundle = build_bundle(
                ws,
                project_id=chapter_row["project_id"],
                text=source_text,
                top_k=top_k,
            )
        else:
            if not project or text is None:
                raise ValueError("Use either --chapter or both --project and --text.")
            bundle = build_bundle(ws, project_slug=project, text=text, top_k=top_k)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(bundle), json_output)


@translate_app.command("text")
def translate_text(
    chapter: Annotated[str, typer.Option("--chapter", help="Chapter id.")],
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = translate_chapter_mock(ws, chapter_id=chapter, provider_key=provider)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


if __name__ == "__main__":
    app()
