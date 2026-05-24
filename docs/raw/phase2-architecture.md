# Novel Translator Studio Phase Two Architecture

## Kiến trúc tổng thể và định hướng sản phẩm

### Phase Research Report

**Filename:** `PHASE2_RESEARCH_REPORT.md`

Phase này nên được chốt như một **phase kiến trúc triển khai**, không phải phase nghiên cứu lại memory. Nền tảng cố định là LAMM-T của Phase 1: memory phải là **structured-first**, có **scope**, **confidence**, **evidence**, **provenance**, **conflict handling**, **audit trail**, và luôn được **bundle gọn trước khi dịch**; plugin phụ chỉ nhận **compact exported memory** và **không tự học**. Báo cáo Phase 1 cũng đã khóa lại tám layer, sáu retrieval lanes, và MVP memory gồm `Term`, `Name`, `Pronoun`, `Style`, `Correction` cùng `Evidence`, `Confidence`, `Scope Priority`, `Top-k Retrieval`, và `Compact Export`. fileciteturn0file4

Kiến trúc nên chọn cho Novel Translator Studio là một kiến trúc **backend-first, local-first, app-service-first**: một lõi ứng dụng duy nhất quản lý project, import, alignment, learning, retrieval, translation, QA, export; CLI là bề mặt chuẩn cho automation; GUI là lớp điều phối mỏng chạy trên cùng application services; storage là **SQLite workspace + artifact folder**; memory engine bám LAMM-T bằng **một canonical store có evidence/audit/conflict**, còn vector retrieval nếu có thì chỉ là lớp phụ cho exemplars chứ không phải nguồn chân lý. Quyết định này bám rất sát Phase 1, cũng phù hợp với khả năng full-text/JSON/backup của SQLite cho local-first MVP. fileciteturn0file4 citeturn13view0turn13view1turn24view0turn24view2turn24view3

Về lớp desktop, tôi **không khuyến nghị đặt GUI làm trung tâm ở MVP**. Tauri hiện dùng **OS webview**, hỗ trợ **sidecar binaries** và permission rõ ràng; Electron thì đi theo **mô hình multi-process của Chromium** với main/renderer tương tự browser. Với dự án này, đó là tín hiệu rõ ràng rằng nếu sau này cần một shell desktop gọn, thì **Tauri phù hợp hơn Electron**; nhưng **MVP 0–3 không nên bắt đầu bằng Tauri hay Electron**, mà nên khóa CLI + core services trước. citeturn23view0turn23view1turn23view2

Khuyến nghị stack ở mức chiến lược là **Python-first cho backend**, vì các phần khó nhất của bài toán nằm ở ingestion, alignment, OCR/vision orchestration, local file processing, DB writes, learning pipelines, batch jobs và automation CLI. GUI nên được xem là lớp sau: hoặc **Tauri + web frontend** ở giai đoạn hoàn thiện desktop, hoặc một local web UI mỏng trong quá trình kiểm chứng workflow. Nếu cần đóng gói backend thành binary cho desktop, PyInstaller đã hỗ trợ việc bundle Python app và dependencies thành gói chạy được mà không cần người dùng cài riêng Python. citeturn23view3

Ba lựa chọn đã cân nhắc cho MVP là: **Python-only + PySide**, **TypeScript/Electron-first**, và **Python core + CLI trước, GUI shell sau**. Lựa chọn đầu tiên đơn giản về số ngôn ngữ nhưng dễ hút effort vào GUI sớm. Lựa chọn thứ hai mạnh về frontend nhưng lệch với trọng tâm NLP/OCR/alignment và làm tăng chi phí runtime desktop. Lựa chọn thứ ba giữ được điều quan trọng nhất: logic chỉ viết một lần trong core services, CLI usable ngay cho OpenClaw, còn GUI có thể tới sau mà không làm vỡ backend contracts. Vì mục tiêu của bạn là để Codex/Claude Code scaffold và triển khai được MVP thực tế, **lựa chọn thứ ba là phương án cân bằng nhất**. fileciteturn0file4 citeturn23view0turn23view1turn23view2

Rủi ro lớn nhất của Phase 2 không phải ở model, mà ở **scope creep**. Cụ thể là bốn rủi ro: làm GUI quá sớm, cố tự động hóa manga quá sâu trước Phase 3, dùng vector retrieval như “lõi canon”, và cho plugin phụ “tự học”. Cả bốn đều đi ngược lại kết luận của Phase 1. Vì vậy roadmap nên cứng tay: **CLI + text translation + memory review trước**, **correction learning sau**, **plugin export tiếp theo**, **manga semi-manual sau đó**, và **GUI hoàn chỉnh ở cuối**. fileciteturn0file4

### Product Architecture

**Filename:** `PRODUCT_ARCHITECTURE.md`

Novel Translator Studio nên được tách thành tám module sản phẩm: `Workspace/Projects`, `Import & Normalize`, `Learning`, `Memory`, `Translation`, `Manga`, `Model Settings`, và `Export & Automation`. Trong đó `Learning`, `Memory`, `Translation`, và `Export` là lõi; `Manga` là module con dùng chung memory engine nhưng có thêm visual/layout path; `Model Settings` là control-plane cho provider routing; còn `Workspace/Projects` là tổ chức file, run logs, review queue, bundles và artifacts. Cách tách này phản ánh đúng tinh thần Phase 1: một app chính học và quản lý memory đầy đủ, còn plugin chỉ nhận export đã biên dịch. fileciteturn0file4

Mối quan hệ giữa CLI và GUI nên là **“CLI là surface chuẩn, GUI là orchestration/UI”**. Mọi workflow quan trọng đều phải gọi được từ CLI và trả về JSON ổn định. GUI không được mang business logic riêng; nó chỉ dựng form, bắt đầu task, hiển thị logs, review pending items, và gọi lại cùng application services. Điều này vừa phục vụ OpenClaw sau này, vừa giảm nguy cơ GUI và CLI lệch hành vi. OpenClaw cũng phân biệt rõ **tools** là hành động được gọi, **skills** là instruction packs dạy workflow; vì vậy con đường sẵn sàng nhất là để OpenClaw gọi `nts` như một công cụ/CLI ổn định, rồi thêm skill hướng dẫn workflow, thay vì xây plugin automation riêng ngay từ đầu. citeturn11view0turn11view1

Luồng người dùng của truyện chữ nên là: tạo project → import raw và tài liệu tham chiếu → normalize/segment → nếu có bản dịch mẫu thì learn style/memory → chạy translate chapter → review cảnh báo → sửa tay → import correction → approve/reject memory candidates → export kết quả và bundle plugin. Luồng manga thì đi theo: import pages → detect boxes → user chỉnh box thiếu/sai → OCR/correct OCR → translate theo page/chapter context → preview overlay → export page assets → learn layout/typeset corrections. Cả hai luồng dùng chung projects, task queue, review queue, memory store, model router và export compiler. fileciteturn0file4

Mối quan hệ text/manga/plugin cần được tổ chức theo nguyên tắc: **text và manga chia sẻ canon, pronoun, style, correction, provenance**, nhưng manga có thêm layer visual bằng retrieval lane riêng. Export plugin lại không đọc toàn bộ DB; nó chỉ đọc **approved active memory snapshot** đã được compiler nén xuống bundle read-only. Đó là cách giữ app chính mạnh, plugin nhẹ và deterministic. fileciteturn0file4

Ở mức workflow OpenClaw-ready, product surface nên được nhìn như sau:

```text
User / OpenClaw / Script
        │
   CLI `nts ... --json`
        │
   Application Services
 ┌──────┼──────────────────────────────────┐
 │ Import │ Learn │ Memory │ Translate │ QA │ Export │
 └──────┼──────────────────────────────────┘
        │
 SQLite workspace + artifact files + logs
        │
 Optional GUI / Optional local API wrapper
```

Thiết kế này còn có lợi thế là nếu về sau cần đổi shell desktop, hoặc thêm local API cho GUI, thì **product contracts vẫn giữ nguyên tại service layer**. citeturn23view0turn23view2

## Kiến trúc triển khai và dữ liệu

### Technical Architecture

**Filename:** `TECHNICAL_ARCHITECTURE.md`

Tôi khuyến nghị **monorepo** thay vì tách nhiều repo ngay từ đầu. Lý do không phải vì “thời trang”, mà vì Phase 2 cần một nguồn chân lý duy nhất cho schema LAMM-T, migrations, CLI contracts, task state machine, prompt templates, export bundle schema, fixtures và regression tests. Nếu tách repo sớm, Codex rất dễ sinh ra drift giữa CLI, backend và GUI. Một monorepo với lõi Python và desktop shell tách thư mục là đủ cho MVP. fileciteturn0file4

Repo structure đề xuất:

```text
NovelTranslatorStudio/
├── apps/
│   ├── cli/                    # thin CLI entrypoints
│   ├── desktop/                # Tauri/web shell, defer until MVP5
│   └── api/                    # optional local HTTP wrapper, defer
├── packages/
│   ├── nts_core/               # app services, DI, task orchestration
│   ├── nts_memory/             # writer/retriever/curator/conflicts/export
│   ├── nts_learning/           # style learning, correction learning
│   ├── nts_translation/        # text translation pipelines
│   ├── nts_alignment/          # chapter/segment alignment
│   ├── nts_quality/            # hallucination/style/context checks
│   ├── nts_manga/              # page/box/OCR/translation/typeset placeholders
│   ├── nts_model_router/       # adapters, budgets, fallback
│   ├── nts_storage/            # repositories, migrations, artifact paths
│   └── nts_shared/             # schemas, enums, result envelopes, utils
├── docs/
├── migrations/
├── examples/
├── tests/
├── workspace-template/
└── pyproject.toml
```

Runtime của MVP nên là **library-first** chứ chưa cần daemon-first. `nts` CLI gọi thẳng vào application services. Khi GUI xuất hiện, GUI có thể dùng một trong hai cách: gọi `nts` subprocess với `--json`, hoặc gọi một local API wrapper mỏng quanh cùng application services. Nếu chọn Tauri ở giai đoạn desktop, Tauri có thể chạy sidecar binary và truyền message/args rõ ràng; điều này phù hợp cho việc bọc một backend Python đã ổn định thay vì viết lại logic phía Rust/TypeScript. citeturn23view2turn23view3

Background jobs ở local-first MVP không cần Redis/Celery. Cách hợp lý hơn là dùng **persistent job table** trong SQLite, với worker pool trong cùng process hoặc process phụ cục bộ. Mỗi task chạy theo state machine, checkpoint sau từng stage lớn, và ghi `task_runs`, `model_runs`, `logs`, `artifacts`. Cách này phục vụ tốt resume/retry, journaling, và GUI progress. SQLite WAL cho phép readers tiếp tục đọc trong khi writes được append vào WAL file; vì vậy nó phù hợp cho mô hình GUI xem trạng thái khi worker đang ghi tiến độ. citeturn24view3

Workspace layout nên như sau:

```text
workspace/
├── nts.db
├── config/
│   ├── providers.yaml
│   ├── routing.yaml
│   └── app.yaml
├── artifacts/
│   ├── raw/
│   ├── normalized/
│   ├── translated/
│   ├── manga/
│   ├── exports/
│   └── tmp/
├── logs/
│   └── runs/
├── cache/
└── reviews/
```

`providers.yaml` và `routing.yaml` nên được giữ ở dạng text file để dễ backup, diff, và hand-edit; database chỉ lưu snapshot cần thiết cho audit và validation. Đây là lựa chọn phù hợp với local-first và CLI-oriented workflows, trong khi vẫn cho phép GUI chỉnh lại cấu hình qua form. citeturn17view3turn17view2turn0search24

Đường nâng cấp lên server/cloud sau này nên là **nâng từng tầng, không rewrite**. Tầng service giữ nguyên; `nts_storage` đổi implementation từ SQLite sang PostgreSQL; artifact store đổi từ local path sang object-store URI; worker local đổi sang distributed queue; authentication và multi-user mới được thêm sau cùng. PostgreSQL rất phù hợp cho server mode nhờ `jsonb`, GIN indexing và full-text search; nhưng đó là lý do cho phase sau, không phải lý do để bỏ SQLite trong MVP. citeturn24view4turn24view5turn24view6turn3search13

### Database Schema

**Filename:** `DATABASE_SCHEMA.md`

Khuyến nghị database cho MVP là **SQLite**, không phải PostgreSQL. Lý do chính là dự án hiện là **local-first, single-user, desktop + CLI**, và SQLite đã có đủ bốn thứ bạn cần cho MVP: **một file DB dễ backup**, **WAL cho concurrent read/write cục bộ**, **FTS5 với BM25/highlight/snippet**, và **JSON functions**. Phase 1 cũng đã nói rất rõ là MVP không cần graph DB và chỉ cần structured memory + fuzzy text + optional vector nhẹ. fileciteturn0file4 citeturn13view0turn13view1turn13view2turn24view0turn24view2turn24view3

Câu trả lời cụ thể cho các câu hỏi storage là như sau. **Có cần JSONB không?** Với SQLite MVP: **không cần dựa vào SQLite JSONB như định dạng ứng dụng**, dù SQLite hiện có JSONB nội bộ nhanh hơn text JSON. Hãy lưu canonical payload ở dạng **JSON text dễ debug**, dùng JSON functions khi cần query. Nếu sau này nâng lên PostgreSQL server mode, lúc đó mới chuyển metadata linh hoạt sang `jsonb` và thêm GIN indexes cho query rich filters. citeturn24view0turn24view1turn24view5

**Có cần FTS/BM25 không?** Có, và nên dùng ngay từ MVP. FTS5 có built-in `bm25()`, `highlight()`, `snippet()` và hidden `rank`, rất hợp cho fuzzy textual lane, search UI, review UI, correction signatures và example lookup. Đây là phần đáng dùng nhất ngoài exact lookup trong local-first memory retrieval. citeturn13view0turn13view1turn13view2turn13view3

**Có cần vector index không?** Không cần external vector DB ở MVP. Nếu muốn, chỉ thêm **lightweight example index** cho style exemplars và corrected examples sau khi exact + relation + FTS baseline đã chạy tốt. Điều này đúng với Phase 1: vector retrieval chỉ nên là semantic help layer, không phải canon layer. fileciteturn0file4

**Có cần graph database không?** Không. Entity-relationship của LAMM-T có thể được model bằng relational tables + scoped memory items + relation keys + targeted indexes. Phase 1 đã khuyến nghị để graph DB và temporal relations phức tạp sang phase sau. fileciteturn0file4

Large artifacts như raw files, translated outputs, manga images, preview renders, clean masks, export bundles, OCR dumps nên nằm ở **artifact folder**, không nằm trong DB. DB chỉ lưu checksum, relative path, mime/type, metadata và provenance pointers. Cách này giúp backup nhẹ, GUI/CLI cùng truy cập được, và tránh SQLite phình quá mức do nhúng binary blobs lớn. Backup workspace nên dùng snapshot DB + artifact folder; SQLite Online Backup API hỗ trợ snapshot của live database mà không khóa quá lâu. citeturn24view2

API keys không nên lưu raw trong DB. Hãy lưu **`api_key_env` hoặc keyring reference**, còn secrets lấy từ environment variables hoặc OS credential store. Điều này cũng bám đúng cách mà OpenAI, Anthropic và Gemini hướng dẫn người dùng cấu hình API keys qua environment variables. citeturn17view3turn0search24turn17view5

Schema sơ bộ nên đi theo bốn nhóm bảng sau.

| Bảng | Trường quan trọng | Ghi chú triển khai |
|---|---|---|
| `projects` | `id`, `slug`, `name`, `source_lang`, `target_lang`, `domain`, `genre`, `workspace_path`, `status`, `created_at` | Mỗi project là đơn vị scope vận hành chính |
| `documents` | `id`, `project_id`, `doc_kind`, `source_path`, `checksum`, `language`, `metadata_json`, `imported_at` | `doc_kind`: raw, convert, human_translation, ai_translation, manga_ocr, correction_import |
| `chapters` | `id`, `project_id`, `document_id`, `chapter_no`, `title`, `boundary_start`, `boundary_end`, `alignment_group_id`, `confidence` | Có thể ánh xạ nhiều document vào một logical chapter |
| `segments` | `id`, `project_id`, `chapter_id`, `segment_no`, `source_text`, `normalized_text`, `speaker_hint`, `paragraph_no`, `metadata_json` | Nguồn truth cho translation units |
| `translations` | `id`, `segment_id`, `chapter_id`, `translation_kind`, `text`, `status`, `model_run_id`, `bundle_checksum`, `quality_json`, `is_current`, `created_at` | `translation_kind`: rough, literary, reviewed, human_final, box_translation |
| `style_profiles` | `id`, `project_id`, `profile_key`, `name`, `language_pair`, `domain`, `genre`, `style_summary`, `settings_json`, `status` | Profile được chọn khi dịch |
| `memory_items` | `id`, `memory_type`, `subtype`, `layer`, `status`, `scope_json`, `source_key`, `concept_key`, `entity_key`, `value_json`, `rules_json`, `confidence_score`, `confidence_json`, `conflict_cluster_id`, `current_version`, `created_at`, `updated_at` | Canonical envelope |
| `memory_evidence` | `id`, `memory_item_id`, `source_kind`, `document_id`, `chapter_id`, `segment_id`, `box_id`, `source_span`, `target_span`, `excerpt_json`, `quality_score`, `artifact_ref` | Tách riêng để audit và rollback |
| `memory_conflicts` | `id`, `cluster_key`, `scope_hash`, `status`, `winner_memory_item_id`, `summary_json`, `created_at` | `status`: open, resolved, archived |
| `memory_audit_logs` | `id`, `memory_item_id`, `action`, `actor_type`, `actor_ref`, `before_json`, `after_json`, `task_run_id`, `model_run_id`, `created_at` | Append-only |
| `glossary_terms` | `id`, `memory_item_id`, `source_term`, `target_term`, `term_flags_json`, `forbidden_targets_json`, `project_id`, `active_rank` | Projection/index table cho exact lexical lane |
| `character_entities` | `id`, `project_id`, `entity_key`, `canonical_name`, `aliases_json`, `profile_json`, `first_seen_chapter`, `status` | Projection cho entity lane |
| `manga_pages` | `id`, `project_id`, `chapter_id`, `page_index`, `image_path`, `checksum`, `width`, `height`, `preprocess_json`, `page_fingerprint`, `status` | Mỗi page là một artifact trung tâm |
| `manga_boxes` | `id`, `page_id`, `box_key`, `box_type`, `bbox_json`, `polygon_json`, `origin`, `detector_confidence`, `reading_order`, `speaker_entity_id`, `ocr_text`, `ocr_corrected_text`, `translation_id`, `typeset_json`, `status` | `origin`: auto/manual |
| `model_runs` | `id`, `task_run_id`, `provider_key`, `adapter_type`, `base_url`, `model_name`, `requested_capabilities_json`, `prompt_hash`, `input_tokens`, `output_tokens`, `cost_estimate`, `status`, `started_at`, `finished_at` | Bảng provenance model |
| `task_runs` | `id`, `task_type`, `project_id`, `target_ref_kind`, `target_ref_id`, `status`, `stage`, `input_json`, `state_json`, `result_json`, `error_json`, `retry_of_run_id`, `started_at`, `finished_at` | Job state machine |
| `provider_configs` | `id`, `provider_key`, `provider_type`, `base_url`, `api_key_env`, `options_json`, `last_validated_at`, `status` | Không lưu secret raw |
| `export_bundles` | `id`, `project_id`, `profile_id`, `bundle_kind`, `schema_version`, `bundle_path`, `checksum`, `stats_json`, `created_at` | Dùng cho compact export |

Indexing strategy nên gồm: exact indexes cho `(project_id, memory_type, source_key, status)`, `(entity_key, status)`, `(concept_key, status)`, `(cluster_key)`, `(task_type, status, started_at)`, `(provider_key, model_name, started_at)`; partial indexes cho `is_current = true`; và FTS5 shadow tables cho `segments`, `memory_items` (pattern/correction/style examples), và `translations` để phục vụ search UI, retrieval UI và review UI. PostgreSQL GIN/tsvector là hướng nâng cấp sau; MVP chỉ cần FTS5. citeturn13view0turn13view1turn24view5turn24view6

Backup/export strategy nên là `nts workspace backup` tạo một snapshot gồm DB copy + manifest + artifact references. Với SQLite, hot backup có thể thực hiện theo snapshot semantics qua Online Backup API; do đó đây là thiết kế rất hợp cho môi trường desktop local-first. citeturn24view2

## Memory Engine và Model Routing

### Memory Engine Spec

**Filename:** `MEMORY_ENGINE_SPEC.md`

Implementation của LAMM-T không nên là “mỗi layer một CSDL khác nhau”, mà là **một canonical memory envelope** với `layer`, `memory_type`, `scope`, `confidence`, `evidence`, `conflict`, `provenance`, rồi có thêm vài **projection tables** để exact retrieval chạy nhanh. Đây là cách trung dung nhất giữa structured canon và performance retrieval, đồng thời bám rất sát Phase 1. fileciteturn0file4

Mapping từ LAMM-T sang implementation nên như sau:

| Layer LAMM-T | Memory type chủ đạo | Scope điển hình | Lane chính |
|---|---|---|---|
| Global Norm | style, rule, warning | global + target language | style |
| Language-Pair Heuristic | heuristic, rule, pronoun defaults | language pair | style / relational |
| Domain | term, concept, style, warning | domain | lexical / provenance |
| Genre | style, phrase, pattern | genre | style |
| Project Canon | term, name, heading, exception | project | lexical / correction |
| Entity-Relationship | name, pronoun, relation | project + entity pair | relational |
| Group Style | style profile, phrase exemplars | group/profile | style |
| Correction and Exception | correction, error, exception | project / chapter / entity / page | correction / visual |

Về retrieval lanes, engine nên materialize theo đúng logic Phase 1: `lexical`, `relational`, `style`, `correction`, `visual`, `provenance`. Exact lane và relation lane là lõi; FTS/BM25 lane dùng cho fuzzy textual hits; vector lane chỉ nên optional cho exemplars. Phase 1 còn nhấn mạnh retrieval bundle phải là “bộ nhớ tác chiến”, không dump database. fileciteturn0file4 citeturn13view1

Các service chính của memory engine nên được đặc tả như sau.

| Service | Nhiệm vụ | Input | Output | Bảng chính | Gọi LLM | CLI/API chính | Test bắt buộc |
|---|---|---|---|---|---|---|---|
| Memory Writer | Ghi memory candidates, merge version, tạo audit log | candidate objects + evidence refs | inserted/updated item ids | `memory_items`, `memory_evidence`, `memory_audit_logs` | Không | `nts learn style`, `nts learn correction`, `nts memory import` | idempotency, duplicate merge, rollback-safe |
| Memory Retriever | Build retrieval bundle theo scope và token budget | text/page context + profile + scope | compact bundle JSON | `memory_items`, projections, FTS tables | Không, trừ optional style summarizer | `nts memory bundle`, nội bộ translation pipeline | exact precedence, scope rank, token budget |
| Memory Curator | Gom, deprecate, pin, summarize style | candidate sets / stale sets | curated snapshot | `memory_items`, `memory_conflicts`, `style_profiles` | Optional, chỉ cho summary/explanation | `nts memory curate` | stale handling, no silent overwrite |
| Conflict Resolver | Tạo cluster, chọn winner, giữ loser | same concept/entity competing items | resolved cluster | `memory_conflicts`, `memory_items`, `memory_audit_logs` | Không mặc định | `nts memory resolve` | scope precedence, challenger promotion |
| Confidence Scorer | Chấm confidence có giải thích | evidence, alignment stats, human approvals | score + components | `memory_items`, `memory_evidence` | Không | nội bộ | deterministic scoring |
| Evidence Manager | Lưu chứng cứ, excerpt, refs | source/target spans, artifact refs | evidence rows | `memory_evidence` | Không | nội bộ, `nts evidence show` | traceability, orphan prevention |
| Provenance Tracker | Gắn model/task provenance | task/model run refs | provenance metadata | `model_runs`, `task_runs`, `memory_audit_logs` | Không | nội bộ | full lineage links |
| Export Compiler | Chọn active items và compile compact bundle | profile/project snapshot | bundle JSON + compat assets | `memory_items`, `export_bundles` | Không mặc định | `nts export ...` | deterministic checksum |
| Human Review Queue | Tạo review items cho low-confidence/conflicts | pending writes, low alignment, conflicts | queue rows + review groupings | `task_runs`, `memory_conflicts`, `memory_items` | Optional prioritizer | `nts review ...` | queue ordering, approval state transitions |

Ở mức data flow, engine nên đi theo chuỗi: **extract candidate → score confidence → check conflicts → write pending/active → retrieve by lane → resolve by scope/confidence → compile bundle → audit everything**. Ba điểm không được bỏ là: **không overwrite trực tiếp**, **mọi memory write có evidence/provenance**, và **mọi translation run lưu snapshot bundle checksum** để sau này tái hiện. Đó là cách thực thi đúng tinh thần Phase 1 về provenance, conflict cluster và audit trail. fileciteturn0file4

Human Review Queue không nên là một bảng riêng biệt bắt buộc ngay MVP; có thể triển khai trước bằng `task_runs(status='review_required')` + `memory_items(status='pending')` + `memory_conflicts(status='open')`. Nhưng UI/CLI cần nhìn chúng như một queue logic thống nhất. Review priority nên dựa trên bốn yếu tố: alignment confidence thấp, loại memory có impact lớn, conflict unresolved, và mức độ lặp lại của correction. fileciteturn0file4

### Model Routing Spec

**Filename:** `MODEL_ROUTING_SPEC.md`

Model router nên được thiết kế là **adapter-based capability router**, không phải “switch-case theo provider”. Lý do là bề mặt model hiện nay đã phân hóa rõ: OpenAI khuyến nghị dùng **Responses API** cho dự án mới; Chat Completions vẫn còn nhưng không nên là default cho dự án mới. Anthropic đi theo **Messages API** với vision, tool use và structured outputs. Trong khi đó LM Studio, Ollama, LiteLLM và OpenRouter đều mở ra một lớp **OpenAI-compatible** đủ mạnh để gom local models, gateways và routers vào cùng adapter family. citeturn15view4turn15view6turn15view2turn14view5turn14view7turn16view0turn16view1turn16view2turn16view3turn16view4turn16view5

Provider config schema khuyến nghị:

```yaml
providers:
  openai_main:
    type: openai_responses
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    default_store: false
    timeout_sec: 90
    max_retries: 2

  anthropic_main:
    type: anthropic_messages
    base_url: "https://api.anthropic.com"
    api_key_env: "ANTHROPIC_API_KEY"
    timeout_sec: 90
    max_retries: 2

  lmstudio_local:
    type: openai_compatible
    base_url: "http://localhost:1234/v1"
    api_key_env: "LMSTUDIO_API_KEY"
    api_key_optional: true

  ollama_local:
    type: openai_compatible
    base_url: "http://localhost:11434/v1"
    api_key_env: "OLLAMA_API_KEY"
    api_key_optional: true

  openrouter_main:
    type: openai_compatible
    base_url: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"

  litellm_gateway:
    type: openai_compatible
    base_url: "http://localhost:4000"
    api_key_env: "LITELLM_PROXY_API_KEY"

  gemini_optional:
    type: google_gemini
    base_url: "https://generativelanguage.googleapis.com"
    api_key_env: "GEMINI_API_KEY"
```

Schema này hợp lý vì OpenAI docs, Gemini docs và Anthropic quickstart đều dựa vào **environment variables** cho API key; còn LM Studio và Ollama đều hỗ trợ đổi **base URL** để tái sử dụng OpenAI clients, trong khi LiteLLM Proxy và OpenRouter đóng vai trò gateway/router OpenAI-compatible. citeturn17view3turn0search24turn17view5turn16view2turn16view3turn16view1turn16view0

Task routing config schema khuyến nghị:

```yaml
tasks:
  language_detect:
    primary:
      provider: lmstudio_local
      model_class: cheap_text
    fallbacks:
      - provider: openai_main
        model_class: cheap_text
    policy:
      structured_output: true
      max_cost_usd: 0.002

  style_learning:
    primary:
      provider: openai_main
      model_class: strong_reasoning
    fallbacks:
      - provider: anthropic_main
        model_class: strong_reasoning
    policy:
      structured_output: true
      max_cost_usd: 0.20

  literary_translate:
    primary:
      provider: openai_main
      model_class: strong_writing
    fallbacks:
      - provider: anthropic_main
        model_class: strong_writing
    policy:
      structured_output: false
      max_cost_usd: 0.30

  hallucination_guard:
    primary:
      provider: anthropic_main
      model_class: strong_reviewer
    fallbacks:
      - provider: openai_main
        model_class: strong_reviewer
    policy:
      structured_output: true
      prefer_different_provider_from: literary_translate
      max_cost_usd: 0.08

  ocr_correction:
    primary:
      provider: anthropic_main
      model_class: vision_json
    fallbacks:
      - provider: openai_main
        model_class: vision_json
      - provider: gemini_optional
        model_class: vision_json
```

Thay vì hard-code model names, router nên route theo **capability class**: `cheap_text`, `cheap_json`, `strong_reasoning`, `strong_writing`, `strong_reviewer`, `vision_json`, `vision_multimodal`, `local_text`, `local_vision`. Cách này tránh vỡ config mỗi khi nhà cung cấp đổi model IDs. OpenAI Responses, Anthropic Messages và Gemini đều có structured/multimodal surfaces; nhưng semantic correctness vẫn phải được kiểm bởi pipeline, vì structured output chỉ đảm bảo schema shape, không tự đảm bảo nội dung đúng. citeturn15view7turn14view6turn5search1turn5search2

Adapter interface nên chuẩn hóa về một normalized request/response envelope:

```python
class ProviderAdapter(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def validate(self, cfg: ProviderConfig) -> ValidationResult: ...
    def list_models(self) -> list[ModelInfo]: ...
    def call(self, req: NormalizedRequest) -> NormalizedResponse: ...
```

`NormalizedRequest` nên có các field chính: `task_name`, `messages_or_input`, `images`, `response_schema`, `tools`, `temperature`, `max_output_tokens`, `timeout_sec`, `budget_usd`, `metadata`, `privacy.store_remote`. `NormalizedResponse` nên có: `text`, `json`, `tool_calls`, `usage`, `raw_model_name`, `provider_key`, `base_url`, `request_id`, `warnings`. Điều quan trọng là **luôn log model provenance theo realized call**, không chỉ theo planned route, vì routers như OpenRouter có thể tự động fallback và local gateways có thể đổi model map. fileciteturn0file4 citeturn16view0turn16view1

Fallback policy nên là **task-aware**, không phải global retry ngu ngốc. `429`, timeout, transient `5xx`, quota exceeded, model unavailable, schema-invalid hoặc tool-call-invalid là các lỗi hợp lệ để retry/fallback. Nhưng fallback chỉ diễn ra nếu candidate fallback **đủ capability class**. Ví dụ `ocr_correction` không được fallback sang model không vision; `style_learning` không nên fallback sang local cheap model; `plugin_export` không cần LLM fallback vì nên deterministic by compiler. OpenAI rate-limit docs và error-code docs cho thấy quota/rate-limit/error classes là thực tế phải xử lý ở production. citeturn1search2turn1search17

Budget policy nên có ba lớp: **per-run**, **per-task-type**, và **per-provider**. Nếu dùng LiteLLM Gateway hoặc OpenRouter, bạn có thể tận dụng router capabilities ở ngoài app; nhưng app vẫn phải giữ **budget ledger cục bộ** để provenance và audit không phụ thuộc hoàn toàn vào router ngoài. LiteLLM còn hỗ trợ gateway/OpenAI-compatible routing và OpenRouter mô tả rõ automatic fallback/cost routing; vì vậy trong app hãy xem chúng là **optional infrastructure adapters**, không phải chỗ chứa business policy duy nhất. citeturn16view1turn16view0turn4search17

Khuyến nghị task classes cho các task trọng yếu:

| Task | Nhu cầu | Vision | Structured output | Nên cùng model với translator | Fallback |
|---|---|---:|---:|---|---|
| `language_detect` | rẻ, nhanh | không | có | không cần | nên có |
| `chapter_alignment` | reasoning vừa | không | có | không | nên có |
| `glossary_extract` | reasoning + JSON | không | có | không | nên có |
| `pronoun_extract` | reasoning mạnh | không | có | không | nên có |
| `style_learning` | mạnh, ngữ cảnh dài | không | có | không | nên có |
| `rough_translate` | rẻ-vừa | không | không | có thể | nên có |
| `literary_translate` | mạnh, writing quality cao | không | không | là translator chính | nên có |
| `context_review` | reviewer mạnh | không | có | nên khác khi budget cho phép | nên có |
| `hallucination_guard` | reviewer mạnh, bảo thủ | không | có | nên khác | nên có |
| `memory_curator` | reasoning vừa-mạnh | không | có | không | nên có |
| `ocr_correction` | multimodal | có | có | không | nên có |
| `manga_translate` | text hoặc multimodal | tùy | có lợi | không nhất thiết | nên có |
| `manga_typeset_review` | multimodal + layout sense | có | có | không | có |
| `plugin_export` | deterministic compiler | không | không cần | không | không cần |

Với OpenAI, ưu tiên `Responses` adapter cho dự án mới; với OpenAI-compatible local/gateway, giữ thêm `chat_completions` fallback vì một số server chỉ hỗ trợ tốt đường đó; với Anthropic, dùng `Messages`; với Gemini, để ở trạng thái optional adapter cho multimodal/JSON-heavy tasks nếu sau này cần. OpenAI docs hiện khuyến nghị Responses cho dự án mới; Anthropic Messages hỗ trợ vision/tool use và structured outputs; Gemini hỗ trợ multimodal input và structured outputs subset của JSON Schema. citeturn15view6turn15view2turn14view5turn14view7turn5search0turn5search1turn5search2

## Pipeline dịch văn bản và manga

### Text Translation Pipeline

**Filename:** `TEXT_TRANSLATION_PIPELINE.md`

Text pipeline nên được tổ chức thành **một state machine rõ stage**, thay vì một script “dịch luôn”. Phase 1 đã chốt rằng translation cần bám retrieval bundle nhỏ gọn, confidence/evidence/provenance, human-in-the-loop, và correction memory; nên pipeline phải phản ánh điều đó từ đầu. fileciteturn0file4

Pipeline dịch truyện chữ đầu-cuối nên đi như sau.

| Stage | Module | Input | Output | LLM | Local algorithm | DB | Human review |
|---|---|---|---|---:|---:|---:|---:|
| Import | `ImportService` | raw/convert/draft file | `documents` row + artifact | không | có | có | không |
| Detect language | `LanguageDetectService` | document text | source language + confidence | fallback | có | có | không |
| Normalize | `NormalizeService` | raw text | normalized text | không | có | có | không |
| Chapter boundary detect | `BoundaryService` | normalized doc | chapter candidates | không | có | có | khi low confidence |
| Alignment precheck | `AlignmentService` | raw + human translation | chapter map + confidence | optional | có | có | khi mismatch |
| Segmentation | `SegmentationService` | chapter text | segments | không | có | có | không |
| Candidate extraction | `ContextPrepService` | source segments + profile | term/name/pronoun/style candidates | optional | có | có | không |
| Retrieval bundle | `MemoryRetriever` | candidates + scope | compact memory bundle | optional summary | có | có | không |
| Draft route select | `TranslationPlanner` | source kind + available drafts | translation plan | không | có | có | không |
| Rough translate / polish | `DraftTranslator` | source or convert draft | rough text | có | không | có | không |
| Literary translate | `LiteraryTranslator` | source + draft + bundle | literary translation | có | không | có | không |
| Context review | `ContextReviewer` | source + output + bundle | issues JSON | có | có phụ | có | không |
| Hallucination / omission guard | `HallucinationGuard` | source + output | risk JSON | có | có | có | khi flagged |
| Style review | `StyleReviewer` | output + profile | polished output | có | không | có | optional |
| Export | `ExportService` | approved chapter | `.vi.txt/.md/.json` | không | có | có | không |
| Correction ingest | `CorrectionIngestService` | user edits | diff + correction candidates | optional classify | có | có | có |
| Learn from correction | `CorrectionLearner` | diff set | memory updates + report | optional | có | có | có |

Chi tiết stage-by-stage nên như sau. `ImportService`, `LanguageDetectService`, `NormalizeService`, `BoundaryService`, và `SegmentationService` đều nên **local-first**, không tốn tiền model nếu không cần. LLM chỉ là fallback khi mixed-language, heading bất thường, hoặc alignment quá mơ hồ. `MemoryRetriever` gần như hoàn toàn local; nếu có LLM ở bước này, chỉ dùng để **tóm tắt style rules/examples thành bundle ngắn hơn**, không dùng để quyết định canon. Điều này đúng hẳn logic “retrieval-first, structured-first”. fileciteturn0file4

`TranslationPlanner` cần phân biệt ba đường đi. Nếu input là **raw ngoại ngữ**, chạy `rough_translate` rồi `literary_translate`. Nếu input là **convert tiếng Việt thô/Hán-Việt convert**, bỏ qua rough translate và đi vào `literary_polish` với style profile tương ứng. Nếu input là **machine draft + raw**, thì coi draft như `reference_draft`, nhưng vẫn build bundle từ raw/source context và để final literary translator chịu trách nhiệm đầu ra. Cả ba đường đi đều phải log provenance và bundle checksum. fileciteturn0file4

`ContextReviewer` và `HallucinationGuard` nên là **hai bước tách biệt**. `ContextReviewer` nhìn coherence, relation, xưng hô, chapter continuity và bundle compliance. `HallucinationGuard` nhìn thêm/bớt ý, số lượng, tên riêng, đơn vị, và nghĩa sai. `HallucinationGuard` không nên chỉ dựa vào LLM reviewer; nó phải có local checks trước, như name coverage, number/unit coverage, source-target length ratio, suspicious omissions, repeated phrase anomalies. Chỉ những segment bị flag mới cần pass vào review model mạnh hơn. Cách này vừa tiết kiệm, vừa giảm correlated failure nếu translator và reviewer là cùng một model family. fileciteturn0file4

Pipeline học style từ `raw + translated sample` nên được coi là một pipeline con chính thức, không phải tool rời:

```text
import raw + translated
→ normalize
→ chapter/heading alignment
→ segment alignment
→ candidate extraction
→ terms / names / pronouns / style / phrase patterns
→ evidence objects
→ confidence scoring
→ conflict detection
→ pending writes
→ learn_report
→ human approve/reject
→ activate bundle snapshot
```

Điểm quan trọng nhất ở đây là **gating để tránh học sai vì chapter mismatch**. Cần ít nhất năm kiểm tra trước khi học: `chapter heading/title similarity`, `chapter number heuristics`, `length ratio`, `named-entity overlap`, và `monotonic segment alignment`. Nếu bất kỳ tín hiệu nào tụt dưới threshold, toàn bộ batch phải ra `review_required` thay vì ghi memory bừa. Phase 1 đã nhấn mạnh alignment là pipeline riêng, rất thực dụng, và không được đánh đồng với retrieval/translation. fileciteturn0file4

Tôi khuyến nghị một `alignment_confidence` tổng hợp kiểu:

```text
0.30 chapter-heading match
+ 0.25 length ratio stability
+ 0.20 named-entity overlap
+ 0.15 monotonicity
+ 0.10 punctuation/structure similarity
```

Nếu `< 0.75`, chỉ được trích xuất **report + pending candidates**, không auto-activate. Nếu `< 0.55`, dừng hẳn việc học style cho batch đó. Điều này rất quan trọng để tránh phá hỏng project canon vì sample bị cắt/gộp chap. fileciteturn0file4

Pipeline học từ `AI translation + human correction` phải khác pipeline style learning. Nó là pipeline của **error mining + correction memory**:

```text
raw + ai_translation + human_final
→ segment/paragraph diff
→ classify edit type
→ detect changed terms / names / pronouns / style
→ infer correction rule
→ update existing item confidence OR create challenger
→ create audit log
→ correction_report
→ human approve/reject
```

Chỗ then chốt là **không overwrite memory cũ**. Nếu human chỉnh một term/name/pronoun/style rule, hệ thống phải: thêm evidence mới, tăng confidence cho candidate đúng, giảm confidence cho candidate cũ, mở hoặc cập nhật conflict cluster nếu cần, rồi ghi audit log. Chỉ khi candidate mới thắng lặp lại hoặc được human pin thì mới thành winner. Đây là cách update memory mà không phá memory cũ. fileciteturn0file4

Outputs bắt buộc của text pipeline nên gồm: translated text, quality JSON, warnings, retrieval bundle checksum, model provenance, memory update summary, learn/correction report, và export artifact path. Nếu dùng `--json`, toàn bộ output phải machine-readable; nếu không, CLI in human summary nhưng vẫn lưu JSON đầy đủ vào `task_runs.result_json`. fileciteturn0file4

### Manga Pipeline Overview

**Filename:** `MANGA_PIPELINE_OVERVIEW.md`

Kiến trúc manga trong Phase 2 nên là **đặt chỗ đứng của module**, không “nuốt” nghiên cứu OCR/inpainting từ Phase 3. Điều Phase 2 cần làm là khóa được data model, workflow, điểm nối với memory, và mức độ MVP vs later. Phase 1 đã chỉ ra manga cần visual lane riêng, OCR correction memory, layout memory, speaker hints, reading order, overflow history và typesetting memory. fileciteturn0file4

Data model tối thiểu cho manga nên như sau:

```json
{
  "page_id": "pg_001",
  "chapter_id": "chap_001",
  "image_path": "artifacts/manga/raw/001.png",
  "page_fingerprint": "phash:...",
  "boxes": [
    {
      "box_id": "pg_001_bx_001",
      "type": "speech",
      "bbox": [x, y, w, h],
      "polygon": [[x1,y1],[x2,y2], ...],
      "origin": "auto",
      "detector_confidence": 0.93,
      "reading_order": 1,
      "speaker_entity_id": "char_liu",
      "ocr_text": "原文",
      "ocr_corrected_text": "原文",
      "translation_text": "…",
      "layout_memory_key": "layout:project:page_fingerprint:box_cluster",
      "ocr_memory_key": "ocrsig:...",
      "typeset": {
        "font_preset": "speech-default",
        "overflow_score": 0.12
      },
      "status": "approved"
    }
  ]
}
```

`page_fingerprint` là chìa khóa rất quan trọng: nó cho phép nối manual corrections với các lần re-import hoặc preprocess khác nhau của cùng trang. `box_id` phải ổn định trong phạm vi page; `layout_memory_key` dùng để gắn manual box correction vào Manga Layout Memory; `ocr_memory_key` dùng để gom lỗi OCR lặp lại. Phase 1 đã nhấn mạnh manga memory không phải layer riêng, mà là **lane riêng chạy xuyên qua layers**; dữ liệu ở trên đúng tinh thần đó. fileciteturn0file4

Pipeline manga ở mức kiến trúc nên là:

```text
import image folder / cbz / pdf
→ unpack / rasterize to pages
→ preprocess pages
→ detect text boxes / bubbles
→ user manual correction on boxes
→ OCR each box
→ OCR correction
→ reading order
→ chapter/page context assembly
→ retrieve memory bundle
→ translate by box
→ export box translations
→ clean old text placeholder
→ typeset placeholder / preview
→ final export pages / cbz / pdf
→ learn layout + ocr + typeset corrections
```

Mối nối với memory nên xảy ra ở bốn chỗ. Chỗ thứ nhất là **OCR correction memory** sau OCR. Chỗ thứ hai là **entity/pronoun/style retrieval** trước khi dịch box. Chỗ thứ ba là **layout memory** khi user sửa box hoặc reorder. Chỗ thứ tư là **typeset memory** khi user chỉnh overflow/font fitting. Toàn bộ những sửa này phải đi qua audit/provenance như text pipeline. fileciteturn0file4

CLI cho manga nên bám theo cùng philosophy với text CLI:

```bash
nts import manga ./chapter001.cbz --project truyen-a
nts manga detect-boxes --chapter chapter001 --json
nts manga edit-boxes --chapter chapter001        # optional helper/open editor
nts manga ocr --chapter chapter001 --json
nts manga import-ocr ./ocr_boxes.json --chapter chapter001
nts manga translate --chapter chapter001 --profile nhom_dich_x --json
nts manga preview --chapter chapter001
nts manga export --chapter chapter001 --format cbz
```

Ở GUI, manual box correction là màn hình quan trọng nhất của manga MVP. Workflow nên là: chọn page → xem auto boxes → add/delete/resize/merge/split → sửa OCR text nếu cần → reorder reading order → preview translation → approve page. Đây là phần người dùng thực sự cần ngay; còn full auto inpainting và speaker attribution sâu nên để sang Phase 3. fileciteturn0file4

Phân ranh MVP/V2/V3 nên rất rõ. **MVP manga**: import page set, page registry, box CRUD, OCR text import/sửa tay, reading order, translate box text, export text manifest và preview overlay. **V2**: simple clean/typeset presets, overflow scoring, page export đẹp hơn. **V3 / Phase 3**: OCR nghiên cứu sâu, detector benchmark, inpainting, bubble split, speaker attribution tự động, quality evaluation chuyên sâu. fileciteturn0file4

## Export plugin, giao diện và QA

### Plugin Export Spec

**Filename:** `PLUGIN_EXPORT_SPEC.md`

Plugin export phải là **compiler**, không phải memory sync thô. Canonical source of truth vẫn nằm trong app chính; export chỉ lấy **approved active winners** theo scope/profile rồi compile ra bundle compact để plugin phụ đọc. Phase 1 đã chốt rất rõ nguyên tắc này. fileciteturn0file4

Compact bundle schema đề xuất:

```json
{
  "bundle_id": "bundle_nhom_dich_x_zh_vi",
  "profile_id": "nhom_dich_x",
  "project_id": "truyen_a",
  "language_pair": "zh-vi",
  "domain": "novel",
  "genre": "tien_hiep",
  "style_summary": "...",
  "force_terms": [],
  "force_names": [],
  "pronoun_rules": [],
  "phrase_patterns": [],
  "correction_rules": [],
  "do": [],
  "dont": [],
  "warnings": [],
  "source_snapshot": {
    "memory_item_count": 0,
    "memory_version_set": [],
    "exported_at": "2026-05-24T00:00:00+07:00"
  },
  "schema_version": "lamm_t_compact_v1",
  "checksum": "sha256:..."
}
```

Selection algorithm nên deterministic: chỉ chọn `status=active`, không conflict unresolved, ưu tiên scope hẹp hơn, confidence cao hơn, evidence tốt hơn; `force_terms` và `force_names` lấy theo tổ hợp `confidence × frequency × scope_priority`; `pronoun_rules` chỉ export các pair có support đủ mạnh; `phrase_patterns` và `correction_rules` chỉ export rule có ambiguity thấp; `style_summary` phải là phần distillation ngắn của group/project style chứ không phải dump toàn profile. Token/size cap mặc định nên giữ ở khoảng **bundle compact cỡ một retrieval bundle chiến đấu**, không phải knowledge base. Điều này khớp với tư duy top-k compact bundle của Phase 1. fileciteturn0file4

Versioning nên gồm hai lớp: `schema_version` cho contract, và `bundle_id/checksum` cho snapshot. Checksum nên tính trên **canonical serialization**: UTF-8, newline normalized, sorted keys, sorted arrays theo rule ổn định. Nếu cùng snapshot mà kết quả export khác checksum, đó là bug của compiler. `export_bundles` table phải lưu `stats_json` để bạn dễ audit bundle size, item counts, skipped items, warnings. fileciteturn0file4

Về tương thích với ecosystem hiện tại, các file phụ bạn đã upload cho thấy một hướng interop rất rõ: bạn đang có **line-oriented dictionaries và rule tables**, không phải một database plugin phức tạp. `ChinesePhienAmWords` và `ChinesePhienAmEnglishWords` dùng line mappings kiểu `hanzi=reading`; `LuatNhan` dùng pattern rules với placeholder `{0}`, `{1}`; còn `Pronouns` dùng mappings kiểu `source=choice1/choice2/...`. Vì vậy `export-vbook-profile` nên xuất **cả canonical JSON bundle lẫn compatibility assets dạng text** để dễ nối vào plugin/tool hiện có. fileciteturn0file0 fileciteturn0file1 fileciteturn0file2 fileciteturn0file3

Cụ thể, bộ export compatibility nên gồm:

```text
exports/<bundle_id>/
├── bundle.json
├── manifest.json
├── compat/
│   ├── Pronouns.txt
│   ├── LuatNhan.txt
│   ├── ChinesePhienAmWords.txt
│   ├── ChinesePhienAmEnglishWords.txt
│   └── StyleSummary.txt
└── checksums.txt
```

`Pronouns.txt` được biên dịch từ `pronoun_rules`; `LuatNhan.txt` từ `phrase_patterns` và `correction_rules` có placeholder; hai wordlists chỉ nên chứa **project/profile overrides**, không dump toàn cục wordbase; `StyleSummary.txt` dùng để nhét vào prompt/plugin settings nếu cần. Cách này giữ plugin read-only, deterministic, và tương thích với line-based assets hiện có. fileciteturn0file0 fileciteturn0file1 fileciteturn0file2 fileciteturn0file3

Nếu plugin sau này sinh ra correction mới, đừng “để plugin tự học”. Hãy import ngược về app chính qua một correction import format, ví dụ JSONL:

```json
{
  "source": "vbook",
  "bundle_id": "bundle_nhom_dich_x_zh_vi",
  "project_id": "truyen_a",
  "raw_text": "...",
  "plugin_output": "...",
  "human_final": "...",
  "context": {"chapter": "001", "segment": 42}
}
```

App chính sẽ parse, classify diff, ghi vào `Correction Memory`, `Audit Log`, và chỉ khi review xong mới ảnh hưởng canonical store. Đó là phân vai đúng giữa app chính và plugin phụ. fileciteturn0file4

### CLI Spec

**Filename:** `CLI_SPEC.md`

CLI nên được xem là **contract bậc nhất** của toàn hệ. Mọi thứ GUI làm được, CLI đều phải làm được; mọi thứ OpenClaw cần gọi, CLI đều phải gọi được bằng JSON ổn định. Điều này cũng hợp với mô hình của OpenClaw: tools là action surface, skills dạy workflow; skill tốt nhất cho giai đoạn đầu là skill dạy agent gọi `nts` đúng cách thay vì cấy logic trực tiếp vào agent prompt. citeturn11view0turn11view1

Command tree đề xuất:

```text
nts init
nts doctor

nts project create
nts project list
nts project show

nts import text
nts import translation
nts import manga
nts import glossary
nts import corrections

nts learn style
nts learn correction

nts translate text
nts translate manga

nts memory list
nts memory show
nts memory review
nts memory resolve
nts memory export
nts memory bundle

nts manga detect-boxes
nts manga ocr
nts manga preview
nts manga export

nts model list
nts model test

nts task list
nts task show
nts task retry
nts task resume
nts task cancel

nts config set-provider
nts config validate
nts config show

nts export vbook-profile
nts export bundle
```

Quy tắc output nên như sau. Nếu có `--json`, stdout chỉ in **một JSON object cuối cùng** hoặc **JSONL events** nếu có `--jsonl`; progress logs và human-friendly messages đi sang stderr hoặc file logs. Nếu không có `--json`, CLI in human summary ngắn, nhưng vẫn lưu full result vào `task_runs`. Như vậy OpenClaw/automation có thể parse stdout an toàn mà không phải scrape text. citeturn11view1

Exit codes nên được chuẩn hóa:

| Exit code | Ý nghĩa |
|---|---|
| `0` | success |
| `2` | partial_success |
| `3` | review_required |
| `4` | validation_error |
| `5` | provider_error_retryable |
| `6` | not_found |
| `7` | config_error |
| `8` | budget_exceeded |
| `9` | task_failed_nonretryable |

JSON success envelope nên thống nhất:

```json
{
  "status": "success",
  "task_run_id": "run_01J...",
  "project_id": "truyen_a",
  "output_file": "./artifacts/translated/chapter001.vi.txt",
  "quality": {
    "hallucination_risk": "low",
    "style_match": 0.86,
    "warnings": []
  },
  "memory_updates": {
    "terms_added": 12,
    "pronoun_rules_added": 3,
    "style_rules_added": 5
  }
}
```

JSON error envelope nên là:

```json
{
  "status": "error",
  "task_run_id": "run_01J...",
  "error": {
    "code": "ALIGNMENT_LOW_CONFIDENCE",
    "message": "Sample chapters appear mismatched.",
    "retryable": false,
    "review_required": true,
    "details": {...}
  }
}
```

`Resume/retry` nên dựa trên `task_run_id`. Mỗi task ghi `state_json` theo stage; khi `nts task resume run_xxx` được gọi, pipeline đọc checkpoint cuối và chạy tiếp từ stage kế tiếp, không chạy lại toàn job nếu không cần. `Dry-run` nên dừng trước chỗ có side effects: validate config, load documents, estimate route/budget, build preliminary retrieval plan, nhưng **không gọi model** và **không ghi memory**. Đây là các hành vi rất hợp cho automation agents. fileciteturn0file4

### GUI Workflow Spec

**Filename:** `GUI_WORKFLOW_SPEC.md`

GUI không cần đẹp ở giai đoạn đầu, nhưng phải **bộc lộ workflow đúng**. Bộ tab hợp lý là: `Projects`, `Learn Style`, `Translate Text`, `Manga`, `Memory`, `Model Settings`, `Export Plugin`. Tôi khuyến nghị thêm một **global Runs/Logs drawer** thay vì thêm tab thứ tám, để giữ giao diện đơn giản. GUI chỉ là orchestration layer trên cùng backend services. fileciteturn0file4

Nếu sau này chọn Tauri, frontend có thể là web UI nói chuyện với sidecar/backend bằng command call hoặc event-driven updates; Tauri chính thức hỗ trợ sidecar execution và event bridging giữa backend/frontend. Nhưng lặp lại: **chỉ làm bước này sau khi CLI và services ổn**. citeturn23view2turn0search22

Workflow từng tab nên như sau.

**Projects** là nơi tạo project, chọn source/target language, domain/genre, import files, xem chapter list, và mở recent runs. **Learn Style** là wizard `raw → translated sample → alignment report → extracted candidates → approve/reject`. **Translate Text** là màn hình chọn chapter/profile/model route/risk policy, xem bundle summary, chạy dịch, xem warnings, export file. **Manga** là page list + box editor + OCR/translation preview. **Memory** là review center cho `pending`, `conflicts`, `active`, `deprecated`, và search. **Model Settings** là nơi quản lý providers, routing profiles, `test connection`, `test JSON`, `test vision`, budgets. **Export Plugin** là preview compact bundle, manifest, compatibility assets, checksum và export destination. fileciteturn0file4

State management của GUI nên là **backend-owned state**. Nghĩa là component UI không giữ business truth; nó fetch/poll state từ `task_runs`, `projects`, `memory_items`, `export_bundles` rồi render. Long-running job nên chạy ngoài UI thread; GUI chỉ subscribe progress hoặc poll. Lợi ích của cách này là GUI crash không làm mất task state, và CLI/GUI dùng chung run ids. citeturn23view2turn24view3

Ba màn review bắt buộc nên có là: **Alignment Review**, **Memory Review**, và **Correction Review**. Alignment Review hiển thị raw vs translated sample side-by-side với confidence/gaps. Memory Review hiển thị item, evidence excerpts, scope, confidence components, conflicts và actions approve/reject/pin/deprecate. Correction Review hiển thị diff AI vs human, classification, proposed correction rule, and whether it should update existing memory or create challenger. Đây là các nơi “human-in-the-loop” thực sự xảy ra. fileciteturn0file4

Manga Box Editor nên là màn duy nhất được đầu tư riêng ở GUI manga MVP. Nó cần canvas page, layer box, panel OCR/translation, reorder actions, speaker hint, và preview overflow. Đừng làm inpainting phức tạp ở đây trong Phase 2. Hãy coi editor này là chỗ ghi **layout corrections** vào DB, để Phase 3 sau này mới khai thác chúng cho nghiên cứu sâu hơn. fileciteturn0file4

### Evaluation QA Spec

**Filename:** `EVALUATION_QA_SPEC.md`

QA architecture nên được gắn vào app từ đầu, không đợi đến khi “xong rồi mới test”. Vì LAMM-T là hệ memory có học, bất kỳ thay đổi memory nào cũng có thể làm pipeline tốt hơn hoặc tệ hơn; do đó cần một bộ regression nhỏ nhưng ổn định. Đây cũng là phần tự nhiên nối tiếp yêu cầu evidence/conflict/audit của Phase 1. fileciteturn0file4

Đối với truyện chữ, bộ metric MVP nên gồm: `hallucination_check`, `omission_check`, `terminology_consistency`, `pronoun_consistency`, `style_match`, `segment_length_ratio`, `chapter_boundary_match`, `repeated_name_consistency`, và `machineese_detection`. Không nhất thiết metric nào cũng là benchmark học thuật; nhiều metric có thể là rule-based + reviewer-model hybrid. Quan trọng là chúng phải **so sánh được qua các lần chạy**. fileciteturn0file4

Đối với memory, metric nên gồm: `conflict_rate`, `accepted_rejected_ratio`, `retrieval_hit_rate`, `correction_recurrence_rate`, `stale_memory_rate`, và `bundle_coverage`. Trong đó `retrieval_hit_rate` nên được định nghĩa rất thực dụng: bao nhiêu correction mà sau khi ta đã học xong, item đúng xuất hiện trong pre-translation bundle ở lần chạy sau. Đây là KPI quan trọng nhất để biết memory có thực sự giúp dịch hay không. fileciteturn0file4

Đối với manga, metric MVP nên gồm: `OCR_confidence`, `box_detection_recall`, `manual_correction_rate`, `reading_order_fix_rate`, `translation_overflow_rate`, và `typeset_fit_rate`. Vì Phase 2 chưa nghiên cứu sâu OCR/inpainting, các metric này nên được dùng để **xác định pain points thật** cho Phase 3 chứ chưa phải để tối ưu hóa triệt để. fileciteturn0file4

Test dataset MVP nên nhỏ nhưng có chủ đích: vài chapter truyện chữ có raw + human translation chuẩn, một batch AI draft + human correction, một project có nhiều xưng hô/alias/name conflicts, và một set nhỏ manga pages đã annotate boxes/or OCR text. Kích thước nên đủ để chạy regression trong CI nội bộ nhanh, chứ không phải benchmark lớn. fileciteturn0file4

Evaluation report nên sinh ra dưới dạng JSON + human markdown. Report cần cho thấy: task config, bundle checksum, model route, aggregate metrics, top warnings, segments/pages failed, và delta so với baseline gần nhất. Một memory change chỉ được coi là “an toàn” nếu regression không làm xấu đi một ngưỡng metric cảnh báo đã định trước. fileciteturn0file4

## Lộ trình MVP và hướng dẫn triển khai với Codex

### MVP Implementation Plan

**Filename:** `MVP_IMPLEMENTATION_PLAN.md`

MVP nên chia thật rõ theo các chặng, và mỗi chặng phải có acceptance criteria cụ thể.

**MVP 0** là skeleton kỹ thuật. Mục tiêu của nó là repo skeleton, config loader, migrations, workspace init, task run tracking, provider validation, result envelopes, và test harness tối thiểu. Acceptance criteria: `nts init`, `nts doctor`, `nts project create`, `nts config validate`, và test suite smoke pass. Đây là chặng Codex nên scaffold đầu tiên. fileciteturn0file4

**MVP 1** là text translation + memory lõi. Nó gồm: import raw/translated, normalize/segment, style learning từ aligned sample, memory schema LAMM-T tối giản, retrieval bundle, translate chapter text, context/hallucination review cơ bản, export result, review queue cơ bản. Acceptance criteria: một chapter có thể đi hết pipeline từ import đến translated output và sinh được pending memory items + bundle checksum. fileciteturn0file4

**MVP 2** là correction learning. Nó gồm: import AI translation + human final, diff classifier, correction memory, confidence update, conflict cluster, audit log, correction report, memory review actions. Acceptance criteria: cùng một lỗi lặp lại sau khi được approve phải bắt đầu ảnh hưởng retrieval bundle ở lần dịch sau. fileciteturn0file4

**MVP 3** là compact plugin export. Nó gồm: export compiler, bundle schema versioning, checksum, compatibility text assets, import correction back from plugin ecosystem. Acceptance criteria: `nts export vbook-profile` tạo ra bundle JSON + compat files deterministic; chạy lại không thay data thì checksum không đổi. fileciteturn0file4 fileciteturn0file0 fileciteturn0file2 fileciteturn0file3

**MVP 4** là manga semi-manual. Nó gồm: page registry, box model, manual box editor data path, OCR text import/sửa tay, reading order, translate box manifest, preview/export. Acceptance criteria: một chapter manga nhỏ có thể import → chỉnh box → OCR/correct text → translate boxes → export text/page manifest mà không cần full auto inpainting. fileciteturn0file4

**MVP 5** mới là full GUI desktop. Nó gồm: projects tab, text workflow, memory review, correction review, model settings, export tab, manga page/box editor. Acceptance criteria: người dùng không cần CLI vẫn chạy được end-to-end text flow. Nhưng đây là chặng cuối, không phải mở đầu. fileciteturn0file4

Thứ **nên làm ngay** là CLI, DB schema, task orchestration, memory retrieval/writer, style learning, correction learning, provider routing, export compiler. Thứ **làm sau** là GUI desktop shell, manga preview polish, OpenClaw skill polishing, local API wrapper. Thứ **để Phase 3** là detector/OCR/inpainting research sâu, auto speaker attribution, typeset optimization, visual benchmark corpus. Thứ **cần prototype kiểm chứng** là vector exemplars, local model quality threshold cho cheap tasks, và exact format adapter cho từng plugin ecosystem cụ thể. fileciteturn0file4

### AGENTS Draft

**Filename:** `AGENTS_MD_DRAFT.md`

Bản draft nên như sau:

```md
# AGENTS.md

## Mission
Implement Novel Translator Studio incrementally, using Phase 1 LAMM-T as fixed architecture input.
Do not redesign memory from scratch.

## Mandatory reading order
1. docs/PHASE2_RESEARCH_REPORT.md
2. docs/PRODUCT_ARCHITECTURE.md
3. docs/TECHNICAL_ARCHITECTURE.md
4. docs/DATABASE_SCHEMA.md
5. docs/MEMORY_ENGINE_SPEC.md
6. docs/MODEL_ROUTING_SPEC.md
7. docs/TEXT_TRANSLATION_PIPELINE.md
8. docs/PLUGIN_EXPORT_SPEC.md
9. docs/CLI_SPEC.md
10. docs/MVP_IMPLEMENTATION_PLAN.md

## Scope rules
- Prioritize CLI + memory MVP first.
- Do not implement full manga automation or inpainting now.
- Do not replace LAMM-T with vector-only or CAT-only memory.
- Plugin exports are read-only; plugins do not self-learn.
- Keep the app local-first.
- Avoid introducing server/cloud infra in MVP unless explicitly required.
- Do not hard-code provider/model assumptions into business logic.
- All long-running actions must create task_run records.

## Architecture rules
- Use application services as the single business-logic layer.
- GUI must not duplicate translation or memory logic.
- Store large artifacts on disk, not in the database.
- Every translation task must persist:
  - task_run_id
  - model provenance
  - retrieval bundle checksum
  - quality report
- Every memory update must persist:
  - evidence
  - confidence components
  - provenance
  - audit log
- Never overwrite conflicting memory in place; create challenger/conflict records.

## Testing rules
- Add unit tests for scoring, retrieval precedence, conflict handling, and export determinism.
- Add regression fixtures before changing retrieval behavior.
- Add acceptance tests for CLI JSON outputs and exit codes.
- No feature is complete without at least one acceptance path.

## Delivery order
- MVP 0: skeleton, config, DB, task runs
- MVP 1: text import, learn style, translate text, review queue
- MVP 2: correction learning
- MVP 3: plugin export
- MVP 4: manga semi-manual
- MVP 5: desktop GUI

## When uncertain
Prefer the simpler design that preserves:
structured memory,
auditability,
local-first workflows,
CLI automation compatibility.
```

OpenClaw phía sau nên được tích hợp trước bằng **skill**, không phải plugin tùy biến. Official docs của OpenClaw mô tả skill là một thư mục chứa `SKILL.md` với YAML frontmatter, được load từ workspace hay managed roots; docs cũng nói rõ skill là lớp hướng dẫn workflow, còn plugin mới là nơi có code/capabilities mới. Vì vậy draft `AGENTS.md` và draft OpenClaw skill đều nên dạy agent gọi `nts --json`, không nên dạy agent chạm thẳng DB hay file nội bộ. Đồng thời, OpenClaw cũng khuyến cáo coi third-party skills là untrusted code; vì thế trong giai đoạn đầu, hãy giữ skill ở phạm vi workspace/local thay vì publish rộng. citeturn11view0turn11view1

### Kết luận triển khai

Kiến trúc tổng thể nên chọn là **Python backend core + CLI canonical + SQLite local-first workspace + LAMM-T memory engine canonical + plugin export compiler + GUI shell sau**. Đây là phương án bám chặt Phase 1 nhất, giảm scope creep nhất, và cũng dễ nhất để Codex scaffold repo có thể chạy thật thay vì chỉ đẹp trên giấy. fileciteturn0file4

Tech stack MVP nên khuyến nghị là: **Python cho core/services/CLI**, **SQLite với WAL + FTS5** cho local storage, **artifact folder** cho file lớn, **adapter-based model router** hỗ trợ OpenAI Responses, Chat Completions/OpenAI-compatible, Anthropic Messages, local LM Studio/Ollama, optional OpenRouter/LiteLLM, optional Gemini, và **Tauri chỉ nên vào ở giai đoạn desktop shell sau khi CLI ổn**. OpenAI hiện khuyến nghị Responses cho dự án mới; Anthropic Messages có vision và structured outputs; LM Studio/Ollama/LiteLLM/OpenRouter đều hỗ trợ các mô hình unified/OpenAI-compatible ở các mức khác nhau. citeturn15view6turn14view5turn14view7turn16view0turn16view1turn16view2turn16view3turn16view4turn16view5turn23view0turn23view2

Database nên chọn là **SQLite**, không phải Postgres, cho MVP. Chỉ khi nào có multi-user/server/cloud, lúc đó mới nâng lên PostgreSQL và dùng `jsonb`/GIN/tsvector. Với MVP local-first, SQLite đã đủ JSON functions, FTS5/BM25, WAL và backup snapshot. citeturn24view0turn24view1turn13view1turn24view2turn24view3turn24view5turn24view6

Model routing nên được thiết kế theo **capability classes + provider adapters + task policy + provenance logging**, không theo model names cứng. Khuyến nghị là: translator chính dùng model writing mạnh; reviewer/hallucination guard ưu tiên provider khác khi budget cho phép; extraction/learning tasks dùng structured output; cheap tasks có local fallback; plugin export thì deterministic, không cần model. fileciteturn0file4 citeturn15view7turn14view6

Memory Engine nên implement LAMM-T bằng **một canonical `memory_items` envelope và các bảng phụ cho evidence/conflicts/audit/projections**, với retrieval theo thứ tự **exact → relation → FTS/BM25 → optional exemplar retrieval → conflict filter → token budgeting → compact bundle**. Tuyệt đối không overwrite trực tiếp candidate cũ; dùng conflict cluster và audit trail. fileciteturn0file4

CLI nên làm trước các lệnh: `nts init`, `nts project create`, `nts import text`, `nts learn style`, `nts translate text`, `nts review`, `nts learn correction`, `nts memory export`, và `nts model test`. GUI nên làm sau, bắt đầu từ `Projects`, `Translate Text`, `Learn Style`, `Memory Review`, `Model Settings`, và `Export Plugin`; `Manga` chỉ cần vào khi MVP text đã đứng vững. fileciteturn0file4

Plugin export nên làm ở **MVP 3**. Manga nên ở **MVP 4 semi-manual**, còn nghiên cứu OCR/inpainting sâu để sang **Phase 3**. Task đầu tiên Codex nên làm là: **repo skeleton + workspace init + migrations + core schemas + task_runs + provider config loader + project/text import happy path + unit tests**. Nếu làm đúng thứ tự đó, Novel Translator Studio sẽ có một lõi đủ chắc để sau này mới thêm GUI, automation, local models và pipeline manga mà không phải đập đi làm lại. fileciteturn0file4