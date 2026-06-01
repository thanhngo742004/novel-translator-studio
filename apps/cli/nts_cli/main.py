from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

from nts_core.approved_memory_validation import (
    DEFAULT_APPROVED_MEMORY_MAX_CHAPTERS,
    DEFAULT_APPROVED_MEMORY_VALIDATION_CHAPTERS,
    DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS,
    approved_memory_validation_status,
    diagnose_chapter_alignment,
    replay_approved_memory_validation,
    resume_approved_memory_validation,
    start_approved_memory_validation,
)
from nts_core.config import validate_config_files
from nts_core.chinese_nlp import (
    analyze_chapter as nlp_analyze_chapter,
    analyze_text as nlp_analyze_text,
    cache_build as nlp_cache_build,
    create_final_human_review_package as nlp_create_final_human_review_package,
    nlp_status as nlp_status_report,
    quality_check as nlp_quality_check,
    show_cache as nlp_show_cache,
)
from nts_core.corrections import (
    learn_corrections,
    records_from_jsonl,
    records_from_parallel_files,
)
from nts_core.doctor import build_doctor_report
from nts_core.dictionary import (
    approve_dictionary_candidates,
    build_dictionary_run,
    dictionary_status,
    export_project_dictionary,
    inspect_dictionary_hits,
    prepare_dictionary_run,
    reject_dictionary_candidates,
    review_dictionary_run,
)
from nts_core.hybrid_prompt import inspect_hybrid_prompt
from nts_core.eval_harness import (
    DEFAULT_LIMITS,
    DEFAULT_PROVIDER_RETRY_ATTEMPTS,
    DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS,
    DEFAULT_PROVIDER_RUN_RETRY_ATTEMPTS,
    TINY_PARAGRAPH_THRESHOLD,
    UNIT_TARGET_MIN_CHARS,
    compare_translation,
    compact_stable_validation_result,
    learn_style,
    prepare_parallel,
    replay_cached_eval,
    run_full,
    stable_prompt_review,
    translate_sample,
    validate_stable_prompt,
)
from nts_core.export_compiler import compile_export_bundle
from nts_core.learning_loop import (
    DEFAULT_GLOBAL_CYCLES,
    DEFAULT_ITERATIONS,
    DEFAULT_LEARNING_MAX_SOURCE_CHARS,
    DEFAULT_LEARNING_MAX_TARGET_CHARS,
    DEFAULT_REPAIR_ITERATIONS,
    apply_test_memory,
    approve_learning_memory,
    ablate_learning_candidates,
    extract_learning_memory,
    learning_loop,
    learning_job_status,
    list_learning_jobs,
    memory_review,
    prepare_learning_dataset,
    reject_learning_memory,
    resume_learning_job,
    run_resumable_learning_loop,
    run_learning_evaluation,
)
from nts_core.manga import (
    export_manga_boxes,
    export_manga_manifest,
    import_manga_boxes,
    import_manga_pages,
    list_manga_pages,
)
from nts_core.memory import (
    add_evidence,
    build_bundle,
    create_memory_item,
    list_memory_items,
    parse_json_object,
    show_memory_item,
    update_memory_status,
)
from nts_core.memory_impact import (
    ablate_approved_memory,
    ablate_memory_regression,
    ablate_original_memory_regression,
    diagnose_memory_regression,
    diagnose_original_memory_regression,
    mine_memory_candidates,
    review_active_memory_risk,
    rollback_approved_memory,
    scope_approved_memory,
    auto_review_memory_candidates,
    simulate_memory_bundle,
)
from nts_core.model_test import run_mock_model_test
from nts_core.projects import create_project, get_project_by_slug, list_projects
from nts_core.production_translation import (
    DEFAULT_BATCH_MAX_CHAPTERS,
    DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    DEFAULT_CHUNK_SIZE_CHARS,
    DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
    translate_batch_stable,
    translate_chapter_stable,
)
from nts_core.production_rollout import diagnose_production_qa, diagnose_unit_safety, run_controlled_production_rollout, write_provider_preflight
from nts_core.rules import (
    ablate_rule_prompt_impact,
    approve_rule_candidates,
    diagnose_rule_prompt_impact,
    export_project_rules,
    extract_rule_candidates,
    reject_rule_candidates,
    review_rule_run,
    rule_status,
    scope_approved_rules,
    test_project_rules,
)
from nts_core.stable_prompts import StablePromptBlocker
from nts_core.text_import import get_chapter, import_text_file, list_chapters, list_segments
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
learn_app = typer.Typer(help="Learning commands.")
export_app = typer.Typer(help="Export commands.")
manga_app = typer.Typer(help="Manga commands.")
manga_pages_app = typer.Typer(help="Manga page commands.")
manga_boxes_app = typer.Typer(help="Manga box commands.")
manga_manifest_app = typer.Typer(help="Manga manifest commands.")
eval_app = typer.Typer(help="Evaluation harness commands.")
nlp_app = typer.Typer(help="Chinese NLP analysis commands.")
dict_app = typer.Typer(help="Project dictionary commands.")
prompt_app = typer.Typer(help="Prompt support inspection commands.")
rule_app = typer.Typer(help="Rule candidate commands.")
production_app = typer.Typer(help="Controlled production rollout commands.")
app.add_typer(project_app, name="project")
app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")
app.add_typer(import_app, name="import")
app.add_typer(text_app, name="text")
app.add_typer(memory_app, name="memory")
app.add_typer(translate_app, name="translate")
app.add_typer(learn_app, name="learn")
app.add_typer(export_app, name="export")
app.add_typer(manga_app, name="manga")
app.add_typer(eval_app, name="eval")
app.add_typer(nlp_app, name="nlp")
app.add_typer(dict_app, name="dict")
app.add_typer(prompt_app, name="prompt")
app.add_typer(rule_app, name="rule")
app.add_typer(production_app, name="production")
text_app.add_typer(text_chapters_app, name="chapters")
text_app.add_typer(text_segments_app, name="segments")
memory_app.add_typer(memory_evidence_app, name="evidence")
memory_app.add_typer(memory_status_app, name="status")
manga_app.add_typer(manga_pages_app, name="pages")
manga_app.add_typer(manga_boxes_app, name="boxes")
manga_app.add_typer(manga_manifest_app, name="manifest")


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
        typer.echo(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        status = payload.get("status", "unknown")
        data = payload.get("data")
        if data is not None:
            rendered = json.dumps(data, ensure_ascii=True, sort_keys=True)
        else:
            rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        typer.echo(f"{status}: {rendered}")


def _fail(code: str, message: str, exit_code: int, as_json: bool) -> None:
    _print(error_envelope(code=code, message=message), as_json)
    raise typer.Exit(exit_code)


def _workspace_arg(command_workspace: Path | None = None) -> Path | None:
    return command_workspace or state.workspace


AutoStartOption = Annotated[
    Optional[bool],
    typer.Option(
        "--auto-start/--no-auto-start",
        help="Override NLP sidecar auto-start for this command.",
    ),
]


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
    model: Annotated[Optional[str], typer.Option("--model", help="Provider model name.")] = None,
    workspace: WorkspaceOption = None,
    use_stable_prompt: Annotated[
        bool,
        typer.Option(
            "--use-stable-prompt",
            help="Use a human-approved stable prompt for production translation.",
        ),
    ] = False,
    prompt_id: Annotated[Optional[str], typer.Option("--prompt-id")] = None,
    max_source_chars: Annotated[
        Optional[int],
        typer.Option("--max-source-chars", help="Limit source chars for smoke tests."),
    ] = None,
    enable_paragraph_alignment: Annotated[
        bool,
        typer.Option("--enable-paragraph-alignment/--disable-paragraph-alignment"),
    ] = True,
    enable_compression_pass: Annotated[
        bool,
        typer.Option("--enable-compression-pass/--disable-compression-pass"),
    ] = True,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option("--merge-tiny-paragraphs/--no-merge-tiny-paragraphs"),
    ] = True,
    evaluate_after: Annotated[bool, typer.Option("--evaluate-after")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    use_approved_dictionary: Annotated[bool, typer.Option("--use-approved-dictionary")] = False,
    use_hybrid_prompt: Annotated[bool, typer.Option("--use-hybrid-prompt")] = False,
    use_approved_rules: Annotated[bool, typer.Option("--use-approved-rules")] = False,
    dictionary_max_entries: Annotated[int, typer.Option("--dictionary-max-entries")] = 8,
    memory_max_items: Annotated[int, typer.Option("--memory-max-items")] = 6,
    rule_max_hints: Annotated[int, typer.Option("--rule-max-hints")] = 4,
    support_max_chars: Annotated[int, typer.Option("--support-max-chars")] = 1200,
    max_unit_repair_attempts: Annotated[int, typer.Option("--max-unit-repair-attempts")] = 2,
    emit_prompt_artifacts: Annotated[bool, typer.Option("--emit-prompt-artifacts")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = translate_chapter_stable(
            ws,
            chapter_id=chapter,
            provider_key=provider,
            model=model or ("mock-eval" if provider == "mock" else ""),
            use_stable_prompt=use_stable_prompt,
            prompt_id=prompt_id,
            max_source_chars=max_source_chars,
            enable_paragraph_alignment=enable_paragraph_alignment,
            enable_compression_pass=enable_compression_pass,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            evaluate_after=evaluate_after,
            dry_run=dry_run,
            output_dir=output_dir,
            force=force,
            use_approved_dictionary=use_approved_dictionary,
            use_hybrid_prompt=use_hybrid_prompt,
            use_approved_rules=use_approved_rules,
            dictionary_max_entries=dictionary_max_entries,
            memory_max_items=memory_max_items,
            rule_max_hints=rule_max_hints,
            support_max_chars=support_max_chars,
            max_unit_repair_attempts=max_unit_repair_attempts,
            emit_prompt_artifacts=emit_prompt_artifacts,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@translate_app.command("batch")
def translate_batch(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    provider: Annotated[str, typer.Option("--provider")],
    model: Annotated[str, typer.Option("--model")],
    fallback_model: Annotated[Optional[str], typer.Option("--fallback-model")] = None,
    workspace: WorkspaceOption = None,
    use_stable_prompt: Annotated[
        bool,
        typer.Option(
            "--use-stable-prompt",
            help="Use a human-approved stable prompt for production translation.",
        ),
    ] = False,
    prompt_id: Annotated[Optional[str], typer.Option("--prompt-id")] = None,
    chapters: Annotated[Optional[str], typer.Option("--chapters", help="Chapter numbers, e.g. 1-3.")] = None,
    chapter_ids: Annotated[
        Optional[str],
        typer.Option("--chapter-ids", help="Comma-separated chapter ids."),
    ] = None,
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_BATCH_MAX_CHAPTERS,
    max_source_chars_per_chapter: Annotated[
        int,
        typer.Option("--max-source-chars-per-chapter"),
    ] = DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
    chunk_size_chars: Annotated[int, typer.Option("--chunk-size-chars")] = DEFAULT_CHUNK_SIZE_CHARS,
    chunk_overlap_paragraphs: Annotated[
        int,
        typer.Option("--chunk-overlap-paragraphs"),
    ] = DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    skip_existing: Annotated[bool, typer.Option("--skip-existing/--no-skip-existing")] = True,
    force: Annotated[bool, typer.Option("--force")] = False,
    enable_paragraph_alignment: Annotated[
        bool,
        typer.Option("--enable-paragraph-alignment/--disable-paragraph-alignment"),
    ] = True,
    enable_compression_pass: Annotated[
        bool,
        typer.Option("--enable-compression-pass/--disable-compression-pass"),
    ] = True,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option("--merge-tiny-paragraphs/--no-merge-tiny-paragraphs"),
    ] = True,
    evaluate_after: Annotated[bool, typer.Option("--evaluate-after")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir")] = None,
    export_combined: Annotated[bool, typer.Option("--export-combined")] = False,
    stop_on_error: Annotated[bool, typer.Option("--stop-on-error")] = False,
    use_approved_dictionary: Annotated[bool, typer.Option("--use-approved-dictionary")] = False,
    use_hybrid_prompt: Annotated[bool, typer.Option("--use-hybrid-prompt")] = False,
    use_approved_rules: Annotated[bool, typer.Option("--use-approved-rules")] = False,
    dictionary_max_entries: Annotated[int, typer.Option("--dictionary-max-entries")] = 8,
    memory_max_items: Annotated[int, typer.Option("--memory-max-items")] = 6,
    rule_max_hints: Annotated[int, typer.Option("--rule-max-hints")] = 4,
    support_max_chars: Annotated[int, typer.Option("--support-max-chars")] = 1200,
    max_unit_repair_attempts: Annotated[int, typer.Option("--max-unit-repair-attempts")] = 2,
    emit_prompt_artifacts: Annotated[bool, typer.Option("--emit-prompt-artifacts")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = translate_batch_stable(
            ws,
            project_slug=project,
            provider_key=provider,
            model=model,
            use_stable_prompt=use_stable_prompt,
            prompt_id=prompt_id,
            chapters=chapters,
            chapter_ids=chapter_ids,
            max_chapters=max_chapters,
            max_source_chars_per_chapter=max_source_chars_per_chapter,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_paragraphs=chunk_overlap_paragraphs,
            resume=resume,
            skip_existing=skip_existing,
            force=force,
            enable_paragraph_alignment=enable_paragraph_alignment,
            enable_compression_pass=enable_compression_pass,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            evaluate_after=evaluate_after,
            dry_run=dry_run,
            output_dir=output_dir,
            export_combined=export_combined,
            stop_on_error=stop_on_error,
            use_approved_dictionary=use_approved_dictionary,
            use_hybrid_prompt=use_hybrid_prompt,
            use_approved_rules=use_approved_rules,
            dictionary_max_entries=dictionary_max_entries,
            memory_max_items=memory_max_items,
            rule_max_hints=rule_max_hints,
            support_max_chars=support_max_chars,
            max_unit_repair_attempts=max_unit_repair_attempts,
            emit_prompt_artifacts=emit_prompt_artifacts,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@production_app.command("rollout")
def production_rollout_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    provider: Annotated[str, typer.Option("--provider")],
    model: Annotated[str, typer.Option("--model")],
    fallback_model: Annotated[Optional[str], typer.Option("--fallback-model")] = None,
    workspace: WorkspaceOption = None,
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")] = "1-10",
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = 10,
    max_real_calls: Annotated[int, typer.Option("--max-real-calls")] = 24,
    use_stable_prompt: Annotated[bool, typer.Option("--use-stable-prompt/--no-use-stable-prompt")] = True,
    use_hybrid_prompt: Annotated[bool, typer.Option("--use-hybrid-prompt/--no-use-hybrid-prompt")] = True,
    use_approved_dictionary: Annotated[bool, typer.Option("--use-approved-dictionary/--no-use-approved-dictionary")] = True,
    use_approved_rules: Annotated[bool, typer.Option("--use-approved-rules/--no-use-approved-rules")] = False,
    dictionary_max_entries: Annotated[int, typer.Option("--dictionary-max-entries")] = 8,
    memory_max_items: Annotated[int, typer.Option("--memory-max-items")] = 6,
    support_max_chars: Annotated[int, typer.Option("--support-max-chars")] = 1200,
    max_unit_repair_attempts: Annotated[int, typer.Option("--max-unit-repair-attempts")] = 2,
    emit_prompt_artifacts: Annotated[bool, typer.Option("--emit-prompt-artifacts/--no-emit-prompt-artifacts")] = True,
    resumable: Annotated[bool, typer.Option("--resumable/--no-resumable")] = True,
    canary: Annotated[bool, typer.Option("--canary/--no-canary")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        if not use_stable_prompt or not use_hybrid_prompt or not use_approved_dictionary:
            raise ValueError("MVP5I production rollout requires --use-stable-prompt, --use-hybrid-prompt, and --use-approved-dictionary.")
        if use_approved_rules:
            raise ValueError("--use-approved-rules is not part of the MVP5I safe production profile; rules remain verifier-only.")
        ws = discover_workspace(_workspace_arg(workspace))
        result = run_controlled_production_rollout(
            ws,
            project_slug=project,
            provider_key=provider,
            model=model,
            fallback_model=fallback_model,
            chapters=chapters,
            max_chapters=max_chapters,
            max_real_calls=max_real_calls,
            dictionary_max_entries=dictionary_max_entries,
            memory_max_items=memory_max_items,
            support_max_chars=support_max_chars,
            max_unit_repair_attempts=max_unit_repair_attempts,
            emit_prompt_artifacts=emit_prompt_artifacts,
            resumable=resumable,
            dry_run=dry_run,
            canary=canary,
            output_dir=output_dir,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("PRODUCTION_ROLLOUT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@production_app.command("preflight")
def production_preflight_command(
    provider: Annotated[str, typer.Option("--provider")],
    model: Annotated[str, typer.Option("--model")],
    workspace: WorkspaceOption = None,
    fallback_model: Annotated[Optional[str], typer.Option("--fallback-model")] = None,
    project: Annotated[str, typer.Option("--project")] = "preflight",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        run_dir = ws.path / "artifacts" / "production_rollout" / f"{project}_preflight_{int(time.time() * 1000)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = write_provider_preflight(ws, run_dir=run_dir, provider_key=provider, primary_model=model, fallback_model=fallback_model)
        result["run_dir"] = str(run_dir)
        result["provider_preflight_path"] = str(run_dir / "provider_preflight.json")
    except (WorkspaceError, ValueError) as exc:
        _fail("PROVIDER_PREFLIGHT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@production_app.command("diagnose-qa")
def production_diagnose_qa_command(
    run: Annotated[str, typer.Option("--run", help="Rollout run path or id.")],
    chapter: Annotated[int, typer.Option("--chapter")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_production_qa(ws, rollout_run_path=run, chapter=chapter)
    except (WorkspaceError, ValueError) as exc:
        _fail("PRODUCTION_QA_DIAGNOSTIC_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@production_app.command("diagnose-unit-safety")
def production_diagnose_unit_safety_command(
    run: Annotated[str, typer.Option("--run", help="Rollout run path or id.")],
    chapter: Annotated[int, typer.Option("--chapter")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_unit_safety(ws, rollout_run_path=run, chapter=chapter)
    except (WorkspaceError, ValueError) as exc:
        _fail("PRODUCTION_UNIT_SAFETY_DIAGNOSTIC_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("correction")
def learn_correction(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    raw: Annotated[Optional[Path], typer.Option("--raw", help="Raw source text file.")] = None,
    ai: Annotated[Optional[Path], typer.Option("--ai", help="AI translation file.")] = None,
    human: Annotated[
        Optional[Path], typer.Option("--human", help="Human-corrected translation file.")
    ] = None,
    file: Annotated[Optional[Path], typer.Option("--file", help="Corrections JSONL file.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        if file is not None:
            if any(path is not None for path in (raw, ai, human)):
                raise ValueError("Use either --file or --raw/--ai/--human, not both.")
            records = records_from_jsonl(file)
            input_ref = str(file.resolve())
        else:
            if raw is None or ai is None or human is None:
                raise ValueError("Use --file or provide all of --raw, --ai, and --human.")
            records = records_from_parallel_files(raw, ai, human)
            input_ref = json.dumps(
                {
                    "raw": str(raw.resolve()),
                    "ai": str(ai.resolve()),
                    "human": str(human.resolve()),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        result = learn_corrections(ws, project_slug=project, records=records, input_ref=input_ref)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@learn_app.command("prepare-parallel")
def learn_prepare_parallel(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Human translated EPUB.")],
    workspace: WorkspaceOption = None,
    chapters: Annotated[str, typer.Option("--chapters")] = "1-3",
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LEARNING_MAX_SOURCE_CHARS,
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LEARNING_MAX_TARGET_CHARS,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = prepare_learning_dataset(
            ws,
            project_slug=project,
            raw_path=raw,
            translated_path=translated,
            chapters=chapters,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@learn_app.command("eval-production")
def learn_eval_production(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    chapters: Annotated[str, typer.Option("--chapters")] = "1-3",
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    model: Annotated[str, typer.Option("--model")] = "mock-eval",
    workspace: WorkspaceOption = None,
    use_stable_prompt: Annotated[bool, typer.Option("--use-stable-prompt")] = False,
    run: Annotated[Optional[str], typer.Option("--run", help="Learning run id or path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = run_learning_evaluation(
            ws,
            project_slug=project,
            chapters=chapters,
            provider_key=provider,
            model=model,
            use_stable_prompt=use_stable_prompt,
            run=run,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@learn_app.command("extract-memory")
def learn_extract_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    from_run: Annotated[str, typer.Option("--from-run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = extract_learning_memory(ws, project_slug=project, from_run=from_run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("memory-review")
def learn_memory_review(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = memory_review(ws, project_slug=project, run=run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("apply-test-memory")
def learn_apply_test_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    mode: Annotated[str, typer.Option("--mode")] = "test-only",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = apply_test_memory(ws, project_slug=project, run=run, mode=mode)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("loop")
def learn_loop_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Human translated EPUB.")],
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    model: Annotated[str, typer.Option("--model")] = "mock-eval",
    workspace: WorkspaceOption = None,
    fallback_model: Annotated[Optional[str], typer.Option("--fallback-model")] = None,
    chapters: Annotated[str, typer.Option("--chapters")] = "1-3",
    global_cycles: Annotated[int, typer.Option("--global-cycles")] = DEFAULT_GLOBAL_CYCLES,
    iterations: Annotated[int, typer.Option("--iterations")] = DEFAULT_ITERATIONS,
    repair_iterations: Annotated[int, typer.Option("--repair-iterations")] = DEFAULT_REPAIR_ITERATIONS,
    min_improvement: Annotated[float, typer.Option("--min-improvement")] = 1.0,
    target_improvement: Annotated[float, typer.Option("--target-improvement")] = 3.0,
    allow_fallback_model: Annotated[bool, typer.Option("--allow-fallback-model/--no-fallback-model")] = True,
    rollback_harmful_memory: Annotated[bool, typer.Option("--rollback-harmful-memory")] = False,
    stop_if_baseline_high: Annotated[float, typer.Option("--stop-if-baseline-high")] = 94.0,
    max_real_calls: Annotated[Optional[int], typer.Option("--max-real-calls")] = None,
    use_stable_prompt: Annotated[bool, typer.Option("--use-stable-prompt")] = False,
    resumable: Annotated[bool, typer.Option("--resumable", help="Run as a checkpointed resumable learning job.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        if resumable:
            result = run_resumable_learning_loop(
                ws,
                project_slug=project,
                raw_path=raw,
                translated_path=translated,
                provider_key=provider,
                model=model,
                fallback_model=fallback_model,
                chapters=chapters,
                global_cycles=global_cycles,
                iterations=iterations,
                repair_iterations=repair_iterations,
                min_improvement=min_improvement,
                target_improvement=target_improvement,
                allow_fallback_model=allow_fallback_model,
                rollback_harmful_memory=rollback_harmful_memory,
                stop_if_baseline_high=stop_if_baseline_high,
                max_real_calls=max_real_calls,
                use_stable_prompt=use_stable_prompt,
            )
        else:
            result = learning_loop(
                ws,
                project_slug=project,
                raw_path=raw,
                translated_path=translated,
                provider_key=provider,
                model=model,
                fallback_model=fallback_model,
                chapters=chapters,
                global_cycles=global_cycles,
                iterations=iterations,
                repair_iterations=repair_iterations,
                min_improvement=min_improvement,
                target_improvement=target_improvement,
                allow_fallback_model=allow_fallback_model,
                rollback_harmful_memory=rollback_harmful_memory,
                stop_if_baseline_high=stop_if_baseline_high,
                max_real_calls=max_real_calls,
                use_stable_prompt=use_stable_prompt,
            )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@learn_app.command("resume")
def learn_resume(
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    max_real_calls: Annotated[Optional[int], typer.Option("--max-real-calls")] = None,
    force_stage: Annotated[Optional[str], typer.Option("--force-stage")] = None,
    from_stage: Annotated[Optional[str], typer.Option("--from-stage")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = resume_learning_job(
            ws,
            run=run,
            max_real_calls=max_real_calls,
            force_stage=force_stage,
            from_stage=from_stage,
            dry_run=dry_run,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@learn_app.command("status")
def learn_status(
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = learning_job_status(ws, run=run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("jobs")
def learn_jobs(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = list_learning_jobs(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("ablate-candidates")
def learn_ablate_candidates(
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = ablate_learning_candidates(ws, run=run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("diagnose-chapter-alignment")
def learn_diagnose_chapter_alignment(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Human translated EPUB.")],
    workspace: WorkspaceOption = None,
    chapters: Annotated[str, typer.Option("--chapters")] = "1-10",
    match_window: Annotated[int, typer.Option("--match-window")] = 3,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_chapter_alignment(
            ws,
            project_slug=project,
            raw_path=raw,
            translated_path=translated,
            chapters=chapters,
            match_window=match_window,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("validate-approved-memory")
def learn_validate_approved_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Human translated EPUB.")],
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    model: Annotated[str, typer.Option("--model")] = "gpt-5.4",
    workspace: WorkspaceOption = None,
    fallback_model: Annotated[Optional[str], typer.Option("--fallback-model")] = "gpt-5.4-mini",
    chapters: Annotated[str, typer.Option("--chapters")] = DEFAULT_APPROVED_MEMORY_VALIDATION_CHAPTERS,
    rounds: Annotated[int, typer.Option("--rounds")] = DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS,
    require_consecutive_improvement: Annotated[
        bool,
        typer.Option("--require-consecutive-improvement/--no-require-consecutive-improvement"),
    ] = True,
    min_improvement: Annotated[float, typer.Option("--min-improvement")] = 1.0,
    target_improvement: Annotated[float, typer.Option("--target-improvement")] = 3.0,
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_APPROVED_MEMORY_MAX_CHAPTERS,
    max_real_calls: Annotated[Optional[int], typer.Option("--max-real-calls")] = None,
    use_stable_prompt: Annotated[bool, typer.Option("--use-stable-prompt")] = False,
    resumable: Annotated[bool, typer.Option("--resumable")] = False,
    rollback_on_regression: Annotated[bool, typer.Option("--rollback-on-regression")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output_dir: Annotated[Optional[Path], typer.Option("--output-dir")] = None,
    exclude_candidate_ids: Annotated[Optional[str], typer.Option("--exclude-candidate-ids")] = None,
    candidate_ablation_top_n: Annotated[int, typer.Option("--candidate-ablation-top-n")] = 5,
    prefer_no_compression_window: Annotated[
        bool,
        typer.Option("--prefer-no-compression-window/--no-prefer-no-compression-window"),
    ] = True,
    allow_skip_unsafe_chapter_sample: Annotated[
        bool,
        typer.Option("--allow-skip-unsafe-chapter-sample/--no-allow-skip-unsafe-chapter-sample"),
    ] = False,
    use_approved_dictionary: Annotated[bool, typer.Option("--use-approved-dictionary")] = False,
    use_hybrid_prompt: Annotated[bool, typer.Option("--use-hybrid-prompt")] = False,
    use_approved_rules: Annotated[bool, typer.Option("--use-approved-rules")] = False,
    dictionary_max_entries: Annotated[int, typer.Option("--dictionary-max-entries")] = 8,
    memory_max_items: Annotated[int, typer.Option("--memory-max-items")] = 6,
    rule_max_hints: Annotated[int, typer.Option("--rule-max-hints")] = 4,
    support_max_chars: Annotated[int, typer.Option("--support-max-chars")] = 1200,
    emit_prompt_artifacts: Annotated[bool, typer.Option("--emit-prompt-artifacts")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = start_approved_memory_validation(
            ws,
            project_slug=project,
            raw_path=raw,
            translated_path=translated,
            provider_key=provider,
            model=model,
            fallback_model=fallback_model,
            chapters=chapters,
            rounds=rounds,
            require_consecutive_improvement=require_consecutive_improvement,
            min_improvement=min_improvement,
            target_improvement=target_improvement,
            max_chapters=max_chapters,
            max_real_calls=max_real_calls,
            use_stable_prompt=use_stable_prompt,
            resumable=resumable,
            rollback_on_regression=rollback_on_regression,
            dry_run=dry_run,
            output_dir=output_dir,
            exclude_candidate_ids=exclude_candidate_ids,
            candidate_ablation_top_n=candidate_ablation_top_n,
            prefer_no_compression_window=prefer_no_compression_window,
            allow_skip_unsafe_chapter_sample=allow_skip_unsafe_chapter_sample,
            use_approved_dictionary=use_approved_dictionary,
            use_hybrid_prompt=use_hybrid_prompt,
            use_approved_rules=use_approved_rules,
            dictionary_max_entries=dictionary_max_entries,
            memory_max_items=memory_max_items,
            rule_max_hints=rule_max_hints,
            support_max_chars=support_max_chars,
            emit_prompt_artifacts=emit_prompt_artifacts,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@learn_app.command("resume-approved-memory-validation")
def learn_resume_approved_memory_validation(
    run: Annotated[str, typer.Option("--run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    max_real_calls: Annotated[Optional[int], typer.Option("--max-real-calls")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = resume_approved_memory_validation(
            ws,
            run=run,
            max_real_calls=max_real_calls,
            dry_run=dry_run,
        )
    except StablePromptBlocker as exc:
        _fail("STABLE_PROMPT_BLOCKED", str(exc), 4, json_output)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@learn_app.command("approved-memory-validation-status")
def learn_approved_memory_validation_status(
    run: Annotated[str, typer.Option("--run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = approved_memory_validation_status(ws, run=run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("replay-approved-memory-validation")
def learn_replay_approved_memory_validation(
    run: Annotated[str, typer.Option("--run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = replay_approved_memory_validation(ws, run=run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("ablate-approved-memory")
def learn_ablate_approved_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = ablate_approved_memory(ws, project_slug=project, validation_run=validation_run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("mine-memory-candidates")
def learn_mine_memory_candidates(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = mine_memory_candidates(ws, project_slug=project, validation_run=validation_run)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("simulate-memory-bundle")
def learn_simulate_memory_bundle(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    candidate_run: Annotated[str, typer.Option("--candidate-run", help="Mining run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = simulate_memory_bundle(
            ws,
            project_slug=project,
            validation_run=validation_run,
            candidate_run=candidate_run,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("auto-review-memory-candidates")
def learn_auto_review_memory_candidates(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    candidate_run: Annotated[str, typer.Option("--candidate-run", help="Mining run id or path.")],
    workspace: WorkspaceOption = None,
    validation_run: Annotated[Optional[str], typer.Option("--validation-run")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = auto_review_memory_candidates(
            ws, project_slug=project, candidate_run=candidate_run, validation_run=validation_run
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)

@learn_app.command("diagnose-memory-regression")
def learn_diagnose_memory_regression(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    chapter: Annotated[int, typer.Option("--chapter", help="Chapter number to inspect.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_memory_regression(
            ws,
            project_slug=project,
            validation_run=validation_run,
            chapter=chapter,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("ablate-memory-regression")
def learn_ablate_memory_regression(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    chapter: Annotated[int, typer.Option("--chapter", help="Chapter number to ablate.")],
    candidate_ids: Annotated[str, typer.Option("--candidate-ids", help="Comma-separated candidate ids.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = ablate_memory_regression(
            ws,
            project_slug=project,
            validation_run=validation_run,
            chapter=chapter,
            candidate_ids=candidate_ids,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("rollback-approved-memory")
def learn_rollback_approved_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    candidate_ids: Annotated[str, typer.Option("--candidate-ids", help="Comma-separated candidate ids.")],
    reason: Annotated[str, typer.Option("--reason", help="Rollback reason.")],
    workspace: WorkspaceOption = None,
    validation_run: Annotated[Optional[str], typer.Option("--validation-run")] = None,
    chapter: Annotated[Optional[int], typer.Option("--chapter")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = rollback_approved_memory(
            ws,
            project_slug=project,
            candidate_ids=candidate_ids,
            reason=reason,
            validation_run=validation_run,
            chapter=chapter,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("review-active-memory-risk")
def learn_review_active_memory_risk(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = review_active_memory_risk(
            ws,
            project_slug=project,
            validation_run=validation_run,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("diagnose-original-memory-regression")
def learn_diagnose_original_memory_regression(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    chapter: Annotated[int, typer.Option("--chapter", help="Chapter number to inspect.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_original_memory_regression(
            ws,
            project_slug=project,
            validation_run=validation_run,
            chapter=chapter,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("ablate-original-memory-regression")
def learn_ablate_original_memory_regression(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run id or path.")],
    chapter: Annotated[int, typer.Option("--chapter", help="Chapter number to ablate.")],
    memory_ids: Annotated[str, typer.Option("--memory-ids", help="Comma-separated original memory ids.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = ablate_original_memory_regression(
            ws,
            project_slug=project,
            validation_run=validation_run,
            chapter=chapter,
            memory_ids=memory_ids,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("scope-approved-memory")
def learn_scope_approved_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    memory_ids: Annotated[str, typer.Option("--memory-ids", help="Comma-separated memory ids.")],
    reason: Annotated[str, typer.Option("--reason", help="Scope/deprecation reason.")],
    workspace: WorkspaceOption = None,
    validation_run: Annotated[Optional[str], typer.Option("--validation-run")] = None,
    chapter: Annotated[Optional[int], typer.Option("--chapter")] = None,
    exclude_chapters: Annotated[Optional[str], typer.Option("--exclude-chapters")] = None,
    context_required: Annotated[Optional[str], typer.Option("--context-required")] = None,
    deprecated_for_validation: Annotated[
        bool,
        typer.Option("--deprecated-for-validation/--scoped-only"),
    ] = True,
    exact_source_required: Annotated[
        bool,
        typer.Option("--exact-source-required/--no-exact-source-required"),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = scope_approved_memory(
            ws,
            project_slug=project,
            memory_ids=memory_ids,
            reason=reason,
            validation_run=validation_run,
            chapter=chapter,
            exclude_chapters=exclude_chapters,
            context_required=context_required,
            deprecated_for_validation=deprecated_for_validation,
            exact_source_required=exact_source_required,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("approve-memory")
def learn_approve_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    candidate_ids: Annotated[Optional[str], typer.Option("--candidate-ids")] = None,
    all_candidates: Annotated[bool, typer.Option("--all")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = approve_learning_memory(
            ws,
            project_slug=project,
            run=run,
            candidate_ids=candidate_ids,
            approve_all=all_candidates,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@learn_app.command("reject-memory")
def learn_reject_memory(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Learning run id or path.")],
    workspace: WorkspaceOption = None,
    candidate_ids: Annotated[Optional[str], typer.Option("--candidate-ids")] = None,
    all_candidates: Annotated[bool, typer.Option("--all")] = False,
    reason: Annotated[Optional[str], typer.Option("--reason")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = reject_learning_memory(
            ws,
            project_slug=project,
            run=run,
            candidate_ids=candidate_ids,
            reject_all=all_candidates,
            reason=reason,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@export_app.command("bundle")
def export_bundle(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = compile_export_bundle(ws, project_slug=project, bundle_kind="bundle")
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@export_app.command("vbook-profile")
def export_vbook_profile(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = compile_export_bundle(ws, project_slug=project, bundle_kind="vbook-profile")
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@manga_app.command("import")
def manga_import(
    path: Annotated[Path, typer.Argument(help="Image folder or .cbz archive.")],
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = import_manga_pages(ws, path=path, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@manga_pages_app.command("list")
def manga_pages_list(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        pages = list_manga_pages(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope({"pages": pages}), json_output)


@manga_boxes_app.command("import")
def manga_boxes_import(
    boxes_json: Annotated[Path, typer.Argument(help="Boxes JSON file.")],
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = import_manga_boxes(ws, boxes_path=boxes_json, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result["task_run_id"]), json_output)


@manga_boxes_app.command("export")
def manga_boxes_export(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = export_manga_boxes(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@manga_manifest_app.command("export")
def manga_manifest_export(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = export_manga_manifest(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("status")
def nlp_status_command(
    workspace: WorkspaceOption = None,
    project: Annotated[Optional[str], typer.Option("--project", help="Project slug.")] = None,
    provider: Annotated[
        str,
        typer.Option("--provider", help="ltp_server or fallback_simple."),
    ] = "ltp_server",
    auto_start: AutoStartOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = None
        if workspace is not None or project is not None:
            ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_status_report(
            ws,
            project_slug=project,
            provider_kind=provider,
            auto_start=auto_start,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("analyze")
def nlp_analyze_command(
    workspace: WorkspaceOption = None,
    text: Annotated[Optional[str], typer.Option("--text", help="Chinese text to analyze.")] = None,
    file: Annotated[Optional[Path], typer.Option("--file", help="UTF-8 text file to analyze.")] = None,
    provider: Annotated[
        str,
        typer.Option("--provider", help="ltp_server or fallback_simple."),
    ] = "ltp_server",
    auto_start: AutoStartOption = None,
    output: Annotated[Optional[Path], typer.Option("--output", help="Optional artifact path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        if text is None and file is None:
            raise ValueError("Use --text or --file.")
        if text is not None and file is not None:
            raise ValueError("Use only one of --text or --file.")
        source_text = text if text is not None else file.read_text(encoding="utf-8")  # type: ignore[union-attr]
        try:
            ws = discover_workspace(_workspace_arg(workspace))
        except WorkspaceError:
            ws = None
        result = nlp_analyze_text(
            ws,
            text=source_text,
            provider_kind=provider,
            auto_start=auto_start,
        )
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result = {"analysis": result, "output_path": str(output)}
    except (OSError, WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("analyze-chapter")
def nlp_analyze_chapter_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    chapter: Annotated[str, typer.Option("--chapter", help="Chapter id or chapter number.")],
    workspace: WorkspaceOption = None,
    provider: Annotated[
        str,
        typer.Option("--provider", help="ltp_server or fallback_simple."),
    ] = "ltp_server",
    auto_start: AutoStartOption = None,
    force: Annotated[bool, typer.Option("--force", help="Rebuild even if cache is valid.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_analyze_chapter(
            ws,
            project_slug=project,
            chapter_ref=chapter,
            provider_kind=provider,
            auto_start=auto_start,
            force=force,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("cache-build")
def nlp_cache_build_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")],
    workspace: WorkspaceOption = None,
    missing_only: Annotated[
        bool,
        typer.Option("--missing-only", help="Skip valid existing cache entries."),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Force cache rebuild.")] = False,
    provider: Annotated[
        str,
        typer.Option("--provider", help="ltp_server or fallback_simple."),
    ] = "ltp_server",
    auto_start: AutoStartOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_cache_build(
            ws,
            project_slug=project,
            chapters=chapters,
            missing_only=missing_only,
            force=force,
            provider_kind=provider,
            auto_start=auto_start,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@nlp_app.command("show-cache")
def nlp_show_cache_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    chapter: Annotated[
        Optional[str],
        typer.Option("--chapter", help="Optional chapter id or number."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_show_cache(ws, project_slug=project, chapter_ref=chapter)
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("quality-check")
def nlp_quality_check_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_quality_check(ws, project_slug=project, chapters=chapters)
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@nlp_app.command("human-review-final")
def nlp_human_review_final_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Approved-memory validation run path.")],
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = nlp_create_final_human_review_package(
            ws,
            project_slug=project,
            validation_run=validation_run,
            chapters=chapters,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("NLP_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@dict_app.command("prepare")
def dict_prepare_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")],
    workspace: WorkspaceOption = None,
    source_nlp_cache: Annotated[
        Optional[Path],
        typer.Option("--source-nlp-cache", help="Optional NLP cache directory or manifest."),
    ] = None,
    raw: Annotated[Optional[Path], typer.Option("--raw", help="Optional raw source file path.")] = None,
    translated: Annotated[
        Optional[Path],
        typer.Option("--translated", help="Optional translated EPUB/reference path."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = prepare_dictionary_run(
            ws,
            project_slug=project,
            chapters=chapters,
            source_nlp_cache=source_nlp_cache,
            raw_path=raw,
            translated_path=translated,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@dict_app.command("build")
def dict_build_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Dictionary run id or path.")],
    workspace: WorkspaceOption = None,
    from_chunk: Annotated[Optional[int], typer.Option("--from-chunk")] = None,
    to_chunk: Annotated[Optional[int], typer.Option("--to-chunk")] = None,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    skip_existing: Annotated[bool, typer.Option("--skip-existing")] = False,
    max_candidates: Annotated[Optional[int], typer.Option("--max-candidates")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = build_dictionary_run(
            ws,
            project_slug=project,
            run=run,
            from_chunk=from_chunk,
            to_chunk=to_chunk,
            resume=resume,
            skip_existing=skip_existing,
            max_candidates=max_candidates,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@dict_app.command("review")
def dict_review_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Dictionary run id or path.")],
    workspace: WorkspaceOption = None,
    min_confidence: Annotated[Optional[float], typer.Option("--min-confidence")] = None,
    entry_type: Annotated[
        Optional[str],
        typer.Option("--type", help="Filter by dictionary entry type."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = review_dictionary_run(
            ws,
            project_slug=project,
            run=run,
            min_confidence=min_confidence,
            entry_type=entry_type,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@dict_app.command("approve")
def dict_approve_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Dictionary run id or path.")],
    workspace: WorkspaceOption = None,
    candidate_ids: Annotated[Optional[str], typer.Option("--candidate-ids")] = None,
    all_high_confidence: Annotated[bool, typer.Option("--all-high-confidence")] = False,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "human",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = approve_dictionary_candidates(
            ws,
            project_slug=project,
            run=run,
            candidate_ids=candidate_ids,
            all_high_confidence=all_high_confidence,
            reviewer=reviewer,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@dict_app.command("reject")
def dict_reject_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Dictionary run id or path.")],
    candidate_ids: Annotated[str, typer.Option("--candidate-ids")],
    reason: Annotated[str, typer.Option("--reason")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = reject_dictionary_candidates(
            ws,
            project_slug=project,
            run=run,
            candidate_ids=candidate_ids,
            reason=reason,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@dict_app.command("export")
def dict_export_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    out: Annotated[Optional[Path], typer.Option("--out", help="Optional output JSON path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = export_project_dictionary(ws, project_slug=project, out=out)
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@dict_app.command("status")
def dict_status_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = dictionary_status(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@dict_app.command("inspect")
def dict_inspect_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    source_text: Annotated[str, typer.Option("--source-text", help="Chinese source chunk.")],
    workspace: WorkspaceOption = None,
    chapter: Annotated[Optional[str], typer.Option("--chapter")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = inspect_dictionary_hits(
            ws,
            project_slug=project,
            source_text=source_text,
            chapter=chapter,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("DICT_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@prompt_app.command("inspect")
def prompt_inspect_command(
    project: Annotated[str, typer.Option("--project")],
    source_text: Annotated[str, typer.Option("--source-text")],
    workspace: WorkspaceOption = None,
    mode: Annotated[str, typer.Option("--mode")] = "production",
    use_hybrid_prompt: Annotated[bool, typer.Option("--use-hybrid-prompt")] = False,
    use_approved_rules: Annotated[bool, typer.Option("--use-approved-rules")] = False,
    dictionary_max_entries: Annotated[int, typer.Option("--dictionary-max-entries")] = 8,
    memory_max_items: Annotated[int, typer.Option("--memory-max-items")] = 6,
    rule_max_hints: Annotated[int, typer.Option("--rule-max-hints")] = 4,
    support_max_chars: Annotated[int, typer.Option("--support-max-chars")] = 1200,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        if use_approved_rules:
            use_hybrid_prompt = True
        if not use_hybrid_prompt:
            raise ValueError("--use-hybrid-prompt is required for MVP5H prompt inspection.")
        ws = discover_workspace(_workspace_arg(workspace))
        result = inspect_hybrid_prompt(
            ws,
            project_slug=project,
            source_text=source_text,
            mode=mode,
            max_dictionary_entries=dictionary_max_entries,
            max_memory_items=memory_max_items,
            use_approved_rules=use_approved_rules,
            max_rule_hints=rule_max_hints,
            max_support_chars=support_max_chars,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("extract")
def rule_extract_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    from_hybrid_run: Annotated[
        Optional[str],
        typer.Option("--from-hybrid-run", help="Hybrid prompt artifact or review path."),
    ] = None,
    from_dictionary_run: Annotated[
        Optional[str],
        typer.Option("--from-dictionary-run", help="Dictionary run id or path."),
    ] = None,
    from_learning_run: Annotated[
        Optional[str],
        typer.Option("--from-learning-run", help="Learning run id or path."),
    ] = None,
    from_validation_run: Annotated[
        Optional[str],
        typer.Option("--from-validation-run", help="Approved-memory validation run path."),
    ] = None,
    from_nlp_cache: Annotated[bool, typer.Option("--from-nlp-cache", help="Use read-only NLP cache signals.")] = False,
    chapters: Annotated[str, typer.Option("--chapters", help="Chapter range, e.g. 1-10.")] = "1-10",
    max_candidates: Annotated[Optional[int], typer.Option("--max-candidates")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = extract_rule_candidates(
            ws,
            project_slug=project,
            from_hybrid_run=from_hybrid_run,
            from_dictionary_run=from_dictionary_run,
            from_learning_run=from_learning_run,
            from_validation_run=from_validation_run,
            from_nlp_cache=from_nlp_cache,
            chapters=chapters,
            max_candidates=max_candidates,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@rule_app.command("review")
def rule_review_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Rule run id or path.")],
    workspace: WorkspaceOption = None,
    min_confidence: Annotated[Optional[float], typer.Option("--min-confidence")] = None,
    rule_type: Annotated[Optional[str], typer.Option("--type", help="Filter by rule type.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = review_rule_run(
            ws,
            project_slug=project,
            run=run,
            min_confidence=min_confidence,
            rule_type=rule_type,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("approve")
def rule_approve_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Rule run id or path.")],
    workspace: WorkspaceOption = None,
    rule_ids: Annotated[Optional[str], typer.Option("--rule-ids", help="Comma-separated rule candidate ids.")] = None,
    all_high_confidence: Annotated[bool, typer.Option("--all-high-confidence")] = False,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "human",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = approve_rule_candidates(
            ws,
            project_slug=project,
            run=run,
            rule_ids=rule_ids,
            all_high_confidence=all_high_confidence,
            reviewer=reviewer,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@rule_app.command("reject")
def rule_reject_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    run: Annotated[str, typer.Option("--run", help="Rule run id or path.")],
    rule_ids: Annotated[str, typer.Option("--rule-ids", help="Comma-separated rule candidate ids.")],
    reason: Annotated[str, typer.Option("--reason")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = reject_rule_candidates(
            ws,
            project_slug=project,
            run=run,
            rule_ids=rule_ids,
            reason=reason,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@rule_app.command("export")
def rule_export_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    out: Annotated[Optional[Path], typer.Option("--out", help="Optional output JSON path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = export_project_rules(ws, project_slug=project, out=out)
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("status")
def rule_status_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = rule_status(ws, project_slug=project)
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("test")
def rule_test_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    source_text: Annotated[str, typer.Option("--source-text", help="Chinese source chunk.")],
    workspace: WorkspaceOption = None,
    mode: Annotated[str, typer.Option("--mode")] = "production",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = test_project_rules(
            ws,
            project_slug=project,
            source_text=source_text,
            mode=mode,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("diagnose-prompt-impact")
def rule_diagnose_prompt_impact_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run artifact path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = diagnose_rule_prompt_impact(
            ws,
            project_slug=project,
            validation_run=validation_run,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("ablate-prompt-impact")
def rule_ablate_prompt_impact_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    validation_run: Annotated[str, typer.Option("--validation-run", help="Validation run artifact path.")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = ablate_rule_prompt_impact(
            ws,
            project_slug=project,
            validation_run=validation_run,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@rule_app.command("scope-approved")
def rule_scope_approved_command(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    rule_ids: Annotated[str, typer.Option("--rule-ids", help="Comma-separated approved rule ids.")],
    action: Annotated[
        str,
        typer.Option("--action", help="scope, verifier_only, disable_prompt, or reject_after_validation."),
    ],
    reason: Annotated[str, typer.Option("--reason")],
    workspace: WorkspaceOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        ws = discover_workspace(_workspace_arg(workspace))
        result = scope_approved_rules(
            ws,
            project_slug=project,
            rule_ids=rule_ids,
            action=action,
            reason=reason,
        )
    except (WorkspaceError, ValueError) as exc:
        _fail("RULE_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result, task_run_id=result.get("task_run_id")), json_output)


@eval_app.command("prepare-parallel")
def eval_prepare_parallel(
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Vietnamese translated EPUB.")],
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "translation_sample_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "evaluation_max_target_chars"
    ],
    sample_start_ratio: Annotated[float, typer.Option("--sample-start-ratio")] = 0.0,
    sample_count: Annotated[int, typer.Option("--sample-count")] = 1,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option(
            "--merge-tiny-paragraphs/--no-merge-tiny-paragraphs",
            help="Merge tiny aligned paragraphs into safer eval translation units.",
        ),
    ] = True,
    tiny_paragraph_threshold: Annotated[int, typer.Option("--tiny-paragraph-threshold")] = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: Annotated[int, typer.Option("--unit-target-min-chars")] = UNIT_TARGET_MIN_CHARS,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        result = prepare_parallel(
            project=project,
            raw_path=raw,
            translated_path=translated,
            max_chapters=max_chapters,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
            sample_start_ratio=sample_start_ratio,
            sample_count=sample_count,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("learn-style")
def eval_learn_style(
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    chapters: Annotated[int, typer.Option("--chapters")] = 1,
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    model: Annotated[str, typer.Option("--model")] = "mock-eval",
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "style_learning_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "style_learning_max_target_chars"
    ],
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    sample_start_ratio: Annotated[float, typer.Option("--sample-start-ratio")] = 0.0,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    _ = (max_chapters, sample_start_ratio)
    try:
        result = learn_style(
            project=project,
            chapters=chapters,
            provider_key=provider,
            model=model,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("translate-sample")
def eval_translate_sample(
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    chapter: Annotated[int, typer.Option("--chapter")] = 1,
    models: Annotated[str, typer.Option("--models")] = "mock-eval",
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "translation_sample_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "evaluation_max_target_chars"
    ],
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    sample_start_ratio: Annotated[float, typer.Option("--sample-start-ratio")] = 0.0,
    enable_length_retry: Annotated[
        bool, typer.Option("--enable-length-retry", help="Retry once if output is too long.")
    ] = False,
    target_length_tolerance: Annotated[float, typer.Option("--target-length-tolerance")] = 0.2,
    enable_paragraph_alignment: Annotated[
        bool,
        typer.Option(
            "--enable-paragraph-alignment/--disable-paragraph-alignment",
            help="Use paragraph-level eval alignment and structured output.",
        ),
    ] = True,
    enable_compression_pass: Annotated[
        bool,
        typer.Option(
            "--enable-compression-pass/--disable-compression-pass",
            help="Compress overlong paragraph outputs with the safe two-attempt protocol.",
        ),
    ] = True,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option(
            "--merge-tiny-paragraphs/--no-merge-tiny-paragraphs",
            help="Merge tiny aligned paragraphs into safer eval translation units.",
        ),
    ] = True,
    tiny_paragraph_threshold: Annotated[int, typer.Option("--tiny-paragraph-threshold")] = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: Annotated[int, typer.Option("--unit-target-min-chars")] = UNIT_TARGET_MIN_CHARS,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    _ = (chapter, max_target_chars, max_chapters, sample_start_ratio)
    try:
        result = translate_sample(
            project=project,
            provider_key=provider,
            models=[part.strip() for part in models.split(",") if part.strip()],
            max_source_chars=max_source_chars,
            enable_length_retry=enable_length_retry,
            target_length_tolerance=target_length_tolerance,
            enable_paragraph_alignment=enable_paragraph_alignment,
            enable_compression_pass=enable_compression_pass,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("compare-translation")
def eval_compare_translation(
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    chapter: Annotated[int, typer.Option("--chapter")] = 1,
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "evaluation_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "evaluation_max_target_chars"
    ],
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    sample_start_ratio: Annotated[float, typer.Option("--sample-start-ratio")] = 0.0,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    _ = (max_chapters, sample_start_ratio)
    try:
        result = compare_translation(
            project=project,
            chapter=chapter,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("run-full")
def eval_run_full(
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Vietnamese translated EPUB.")],
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    models: Annotated[str, typer.Option("--models")] = "mock-eval",
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "translation_sample_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "evaluation_max_target_chars"
    ],
    sample_start_ratio: Annotated[float, typer.Option("--sample-start-ratio")] = 0.0,
    sample_count: Annotated[int, typer.Option("--sample-count")] = 1,
    enable_length_retry: Annotated[
        bool, typer.Option("--enable-length-retry", help="Retry once if output is too long.")
    ] = False,
    target_length_tolerance: Annotated[float, typer.Option("--target-length-tolerance")] = 0.2,
    enable_paragraph_alignment: Annotated[
        bool,
        typer.Option(
            "--enable-paragraph-alignment/--disable-paragraph-alignment",
            help="Use paragraph-level eval alignment and structured output.",
        ),
    ] = True,
    enable_compression_pass: Annotated[
        bool,
        typer.Option(
            "--enable-compression-pass/--disable-compression-pass",
            help="Compress overlong paragraph outputs with the safe two-attempt protocol.",
        ),
    ] = True,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option(
            "--merge-tiny-paragraphs/--no-merge-tiny-paragraphs",
            help="Merge tiny aligned paragraphs into safer eval translation units.",
        ),
    ] = True,
    tiny_paragraph_threshold: Annotated[int, typer.Option("--tiny-paragraph-threshold")] = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: Annotated[int, typer.Option("--unit-target-min-chars")] = UNIT_TARGET_MIN_CHARS,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        result = run_full(
            project=project,
            raw_path=raw,
            translated_path=translated,
            provider_key=provider,
            models=[part.strip() for part in models.split(",") if part.strip()],
            max_chapters=max_chapters,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
            sample_start_ratio=sample_start_ratio,
            sample_count=sample_count,
            enable_length_retry=enable_length_retry,
            target_length_tolerance=target_length_tolerance,
            enable_paragraph_alignment=enable_paragraph_alignment,
            enable_compression_pass=enable_compression_pass,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("validate-stable-prompt")
def eval_validate_stable_prompt(
    project: Annotated[str, typer.Option("--project", help="Eval project key.")],
    raw: Annotated[Path, typer.Option("--raw", help="Chinese raw text file.")],
    translated: Annotated[Path, typer.Option("--translated", help="Vietnamese translated EPUB.")],
    provider: Annotated[str, typer.Option("--provider")] = "mock",
    model: Annotated[str, typer.Option("--model")] = "mock-eval",
    max_chapters: Annotated[int, typer.Option("--max-chapters")] = DEFAULT_LIMITS[
        "alignment_max_chapters"
    ],
    sample_count: Annotated[int, typer.Option("--sample-count")] = 3,
    max_source_chars: Annotated[int, typer.Option("--max-source-chars")] = DEFAULT_LIMITS[
        "translation_sample_max_source_chars"
    ],
    max_target_chars: Annotated[int, typer.Option("--max-target-chars")] = DEFAULT_LIMITS[
        "evaluation_max_target_chars"
    ],
    enable_paragraph_alignment: Annotated[
        bool,
        typer.Option(
            "--enable-paragraph-alignment/--disable-paragraph-alignment",
            help="Use paragraph-level eval alignment and structured output.",
        ),
    ] = True,
    enable_compression_pass: Annotated[
        bool,
        typer.Option(
            "--enable-compression-pass/--disable-compression-pass",
            help="Compress overlong paragraph outputs with the safe two-attempt protocol.",
        ),
    ] = True,
    merge_tiny_paragraphs: Annotated[
        bool,
        typer.Option(
            "--merge-tiny-paragraphs/--no-merge-tiny-paragraphs",
            help="Merge tiny aligned paragraphs into safer eval translation units.",
        ),
    ] = True,
    tiny_paragraph_threshold: Annotated[int, typer.Option("--tiny-paragraph-threshold")] = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: Annotated[int, typer.Option("--unit-target-min-chars")] = UNIT_TARGET_MIN_CHARS,
    stable_run_count: Annotated[int, typer.Option("--stable-run-count")] = 3,
    provider_retry_attempts: Annotated[
        int,
        typer.Option(
            "--provider-retry-attempts",
            help="Retry attempts for retryable provider failures within each sample.",
        ),
    ] = DEFAULT_PROVIDER_RETRY_ATTEMPTS,
    provider_run_retry_attempts: Annotated[
        int,
        typer.Option(
            "--provider-run-retry-attempts",
            help="Run-level retries when a validation run fails only from retryable provider errors.",
        ),
    ] = DEFAULT_PROVIDER_RUN_RETRY_ATTEMPTS,
    provider_retry_backoff_seconds: Annotated[
        float,
        typer.Option(
            "--provider-retry-backoff-seconds",
            help="Base exponential backoff in seconds for provider retries.",
        ),
    ] = DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
    verbose_json: Annotated[
        bool,
        typer.Option(
            "--verbose-json",
            help="Include full stable-validation diagnostics in JSON output.",
        ),
    ] = False,
) -> None:
    try:
        result = validate_stable_prompt(
            project=project,
            raw_path=raw,
            translated_path=translated,
            provider_key=provider,
            model=model,
            max_chapters=max_chapters,
            sample_count=sample_count,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
            enable_paragraph_alignment=enable_paragraph_alignment,
            enable_compression_pass=enable_compression_pass,
            stable_run_count=stable_run_count,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
            provider_retry_attempts=provider_retry_attempts,
            provider_run_retry_attempts=provider_run_retry_attempts,
            provider_retry_backoff_seconds=provider_retry_backoff_seconds,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    output = result if verbose_json else compact_stable_validation_result(result)
    _print(success_envelope(output), json_output)


@eval_app.command("replay")
def eval_replay(
    run: Annotated[str, typer.Option("--run", help="Evaluation run id or path.")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        result = replay_cached_eval(run)
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


@eval_app.command("review-stable")
def eval_review_stable(
    run: Annotated[str, typer.Option("--run", help="Evaluation run id or path.")],
    approve: Annotated[bool, typer.Option("--approve", help="Approve the stable prompt.")] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the stable prompt.")] = False,
    reason: Annotated[
        Optional[str],
        typer.Option("--reason", help="Required rejection reason when using --reject."),
    ] = None,
    reviewer: Annotated[
        Optional[str],
        typer.Option("--reviewer", help="Reviewer name. Defaults to environment user."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    try:
        result = stable_prompt_review(
            run=run,
            approve=approve,
            reject=reject,
            reason=reason,
            reviewer=reviewer,
        )
    except ValueError as exc:
        _fail("VALIDATION_ERROR", str(exc), 4, json_output)
    _print(success_envelope(result), json_output)


if __name__ == "__main__":
    app()
