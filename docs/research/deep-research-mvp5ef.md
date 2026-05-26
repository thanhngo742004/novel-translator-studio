# Deep Research cho Novel Translator Studio MVP5E và MVP5F

## Executive summary

Dựa trên context zip bạn cung cấp, hướng đi đúng cho lần này không phải là thiết kế lại toàn bộ Novel Translator Studio, mà là gắn thêm một lớp **Chinese NLP Analysis Layer** mỏng, cache-first, artifact-first và một **project dictionary subsystem** tách riêng khỏi LAMM-T memory. Với ba repo tham chiếu hiện có, khuyến nghị mạnh nhất là: **MVP5E dùng `ltp-server` như một local sidecar service tùy chọn**, không port nguyên code vào Python; **MVP5F chỉ mượn workflow chunk/range/skip-existing/resume của `build-dict`**, nhưng triển khai builder bằng Python theo mô hình project dictionary có evidence/confidence/scope/provenance; còn `hanlp_mt` nên được dùng như nguồn ý tưởng về phrase/entity semantics và dependency-aware prioritization, **không** dùng làm dependency trực tiếp cho phase này. Lý do là `ltp-server` hiện chỉ cung cấp CWS/POS/NER qua HTTP, trong khi `hanlp_mt` đòi đầu vào giàu hơn nhiều gồm constituency (`con`) và dependency (`dep`) bên cạnh `cws`, `pos`, `ner`. citeturn3view2turn9view0turn12view0turn2view1turn12view1turn13view2

Về mặt kỹ thuật, `ltp-server` đã khá khớp với mục tiêu MVP5E: đây là server Rust dùng `axum`/`tokio`, load ba model legacy `cws_model.bin`, `pos_model.bin`, `ner_model.bin`, nhận `POST /analyze` với `text/plain`, xử lý batch theo từng dòng, rồi trả về JSON gồm `cws`, `pos`, `ner`. Tài liệu chính thức của LTP cũng phân biệt rõ legacy model nhanh chỉ hỗ trợ ba tác vụ CWS/POS/NER, trong khi full Python/PyTorch pipeline mới hỗ trợ sáu tác vụ như dependency parsing và semantic parsing. Điều này khiến phương án “sidecar local service + Python adapter + cache chapter-level + heuristic derivation” rẻ rủi ro hơn đáng kể so với phương án port code hoặc chuyển thẳng sang full Python LTP ngay ở MVP5E. citeturn3view2turn12view0turn18view1turn21view0

Về dictionary builder, `build-dict` có một số ý tưởng đáng mượn gần như nguyên xi ở tầng workflow: chia input thành chunk nhỏ, hỗ trợ concurrent calls, hỗ trợ range `-f/-u`, skip các output đã tồn tại và do đó có tính resume/idempotent tự nhiên. Nhưng nó **không** phải drop-in design cho NTS, vì tool này gọi endpoint OpenAI-compatible `/v1/chat/completions`, lưu raw response cạnh file input, và system prompt của nó yêu cầu model trả về **bảng Markdown** với CWS/POS theo PKU + contextual translation, chứ không phải một project canon dictionary có evidence/provenance/review lifecycle. MVP5F vì vậy nên mượn cơ chế batch/chunk/resume của repo đó, chứ không mượn nguyên shape dữ liệu hay prompt. citeturn9view1turn9view2turn9view3turn11view0turn11view2turn22view2

Có hai rủi ro cần đưa lên mức “gating” ngay từ đầu. Thứ nhất, tài liệu chính thức của LTP nêu rõ việc dùng platform cho mục đích thương mại hoặc trong bối cảnh doanh nghiệp cần xử lý license/fee riêng; nếu Novel Translator Studio được dùng cho production translation thương mại, đây là điểm cần xác minh pháp lý trước khi bundle model hoặc phân phối workflow dựa trên `LTP/legacy`. Thứ hai, trong những gì tôi duyệt được, `build-dict` có MIT license hiển thị rõ, nhưng tôi **không xác minh được license tương tự** trên các trang GitHub của `ltp-server` và `hanlp_mt`; điều này càng củng cố quyết định “integrate, do not port wholesale”. citeturn21view0turn22view2turn1view0turn1view1

Kết luận ngắn gọn là: **MVP5E nên là một NLP cache layer chuẩn hóa và suy diễn nhẹ trên đầu ra CWS/POS/NER của `ltp-server`; MVP5F nên là một deterministic project dictionary builder tách biệt khỏi memory, có human approval bắt buộc, retrieval cap nghiêm ngặt, và tích hợp vào production translation/learning loop theo kiểu additive, không làm drift stable prompt**. citeturn3view2turn11view0turn16search1turn18view1

## Recommended architecture

### Quyết định kiến trúc cốt lõi

Khuyến nghị kiến trúc là thêm một seam mới trong NTS, không thay đổi lõi app hiện có:

```text
raw chapter / segment
    -> sentence splitter
    -> AnalyzerProvider
         -> LtpServerAnalyzer
         -> FallbackSimpleAnalyzer
    -> normalized NLP result
    -> derived entity/phrase/term candidates
    -> chapter-level artifact cache
    -> consumers
         -> alignment helper
         -> memory extraction helper
         -> dictionary builder
         -> verification
         -> prompt hint retrieval
```

Mấu chốt là **provider-neutral schema**. Lý do là ba nguồn tham chiếu đang không cùng một I/O contract. `ltp-server` trả CWS/POS/NER với tag kiểu LTP legacy như `r`, `v`, `nh`, `ns`. `build-dict` lại bắt model trả POS theo **PKU standard** với các tag như `n`, `nr`, `ns`, `nt`. Trong khi đó `hanlp_mt` làm việc với HanLP MTL output gồm `con`, `cws`, `pos`, `ner`, `dep`, dùng ví dụ POS kiểu `PN`, `VV`, `NN` và phrase labels như `NP`, `VP`. Nếu NTS khóa chặt schema vào một tagset/provider ngay từ đầu, MVP5E sẽ tự tạo technical debt cho chính MVP5F và các phase sau. citeturn3view2turn9view4turn16search1turn12view1

Vì NTS hiện là Python-first/CLI-first/local-first, còn ba repo tham chiếu lần lượt là Rust và Crystal, chi phí bảo trì dài hạn của việc port code vào trong app chính sẽ cao hơn rõ rệt so với việc dựng một adapter Python mỏng. `ltp-server` là Rust; `build-dict` và `hanlp_mt` đều là Crystal, chứ không phải Python libraries sẵn để import trực tiếp. Do đó, giải pháp implementable nhất cho Codex là **adapter + artifact contracts + SQLite metadata**, không phải cross-language port. citeturn1view0turn1view1turn22view2

### Khuyến nghị cho MVP5E

Ở MVP5E, `ltp-server` nên được xem là **optional local sidecar service**, không phải in-process dependency. NTS nên có một `AnalyzerProvider` interface với hai implementation trong phạm vi phase này:

- `LtpServerAnalyzer`
- `FallbackSimpleAnalyzer`

`LtpServerAnalyzer` sẽ lo health check, batch request theo câu, parse JSON `cws/pos/ner`, normalize tagset và sinh ra một `NormalizedSentenceAnalysis`. `FallbackSimpleAnalyzer` chỉ cần đảm bảo hệ thống **vẫn chạy được** khi server không có mặt: sentence split, bracket/panel pattern detection, exact-match dictionary anchors, simple Chinese span grouping, repeated n-gram discovery và warning flags. Cách làm này đáp ứng đúng yêu cầu “nếu không có LTP server thì tool vẫn phải chạy được”, đồng thời không kéo full PyTorch LTP vào trong core process của NTS. citeturn3view2turn12view0turn18view1turn21view0

### Khuyến nghị cho MVP5F

Ở MVP5F, kiến trúc nên tách làm ba tầng:

- **candidate extraction**
- **human review**
- **approved project dictionary**

Không được đổ candidate vào active memory ngay. `build-dict` cho thấy workflow batch theo chunk/range/resume rất tiện cho CLI cục bộ: input được chia file `*.zh.txt`, có concurrency mặc định 3, có `-f/-u` để xử lý khoảng file, và skip output nếu file đích đã tồn tại. NTS nên sao chép pattern workflow này, nhưng chunk theo **segment/chapter boundary** thay vì cắt cứng 1000 ký tự như repo gốc, vì NTS cần provenance, evidence và review dễ đọc. citeturn9view1turn9view2turn11view0

`hanlp_mt` vẫn rất hữu ích, nhưng chủ yếu ở mức ý tưởng. Repo này khẳng định một hướng dịch phrase-first/dependency-aware: khóa entity như atomic node, phrase matching trước, nếu không khớp mới dùng dependency rules và DRT-style disambiguation. NTS chưa có `con`/`dep`, nên chưa nên bắt chước pipeline đó ngay. Nhưng bạn hoàn toàn có thể mượn triết lý xếp hạng retrieval của nó cho dictionary builder và verification: **entity exact > fixed phrase > generic term**. citeturn16search1turn12view1turn14view0turn14view2

## MVP5E implementation spec

### Tích hợp `ltp-server` như local sidecar

Khuyến nghị dứt khoát là **external local service**, không port code. Lý do thực tế:

- Repo đã có sẵn HTTP contract đúng kiểu sidecar: `POST /analyze`, body là `text/plain`, mỗi dòng là một câu, trả JSON array cùng thứ tự dòng vào.  
- Nó dùng mô hình legacy đúng ba tác vụ CWS/POS/NER, tức là “đủ dùng” cho analysis layer nhẹ.  
- Mã nguồn hiện set worker threads bằng một nửa số CPU, phù hợp cho máy cá nhân, và batch theo request nên rất thuận tiện cho chapter-level cache build.  
- Porting sang Python không giúp bạn có thêm DEP/CON; nó chỉ khiến bạn gánh thêm maintenance cost cho Rust/Crystal semantics trong codebase Python. citeturn3view2turn9view0turn12view0

NTS nên thêm config kiểu:

```yaml
nlp:
  provider: ltp_server
  ltp_server:
    base_url: "http://127.0.0.1:3003"
    timeout_seconds: 10
    max_sentences_per_request: 512
    enabled: true
  fallback:
    enabled: true
```

Không nên hardcode port theo repo script. Có một bất nhất nhỏ trong repo: README và `main.rs` dùng cổng `3003`, nhưng `verify.sh` lại poll cổng `3000`. Vì thế `nts nlp status` cần dựa vào config/runtime health check của NTS chứ không clone logic từ script xác minh đó. citeturn3view2turn12view0turn23view0

### Output schema nên chuẩn hóa ra sao

Điểm quan trọng nhất của MVP5E là **không trả nguyên shape `ltp-server` thô cho các feature khác**. Thay vào đó, NTS nên định nghĩa một schema trung gian ổn định, trong đó `tokens`, `provider_pos`, `normalized_pos`, `ner_tags`, `entity_spans`, `phrase_candidates`, `term_candidates` là các lớp riêng.

Khuyến nghị schema chapter-level:

```json
{
  "meta": {
    "project_slug": "han-jue",
    "chapter_id": "chap_000123",
    "source_sha256": "…",
    "provider": "ltp_server",
    "provider_version": "chi-vi/ltp-server + LTP/legacy",
    "heuristics_version": "mvp5e-v1",
    "degraded": false,
    "created_at": "2026-05-25T…"
  },
  "segments": [
    {
      "segment_id": "seg_001",
      "sentence_ids": ["s1", "s2"]
    }
  ],
  "sentences": [
    {
      "sentence_id": "s1",
      "segment_id": "seg_001",
      "text": "他叫汤姆去拿外衣。",
      "tokens": [
        {"text": "他", "start": 0, "end": 1, "provider_pos": "r", "norm_pos": "pron"},
        {"text": "汤姆", "start": 2, "end": 4, "provider_pos": "nh", "norm_pos": "name"}
      ],
      "ner_tags": ["O", "O", "S-Nh", "O", "O", "O", "O"],
      "entity_spans": [
        {"text": "汤姆", "type": "person", "start_token": 2, "end_token": 3, "confidence": 0.82}
      ],
      "phrase_candidates": [],
      "term_candidates": [],
      "warnings": []
    }
  ],
  "chapter_candidates": {
    "entity_candidates": [],
    "term_candidates": [],
    "phrase_candidates": []
  }
}
```

Lý do phải có lớp chuẩn hóa này là vì `ltp-server`/LTP legacy và `build-dict` đang ở không gian tag PKU-ish/lowercase, còn `hanlp_mt` lại ở contract HanLP/CTB/UTT + `con`/`dep`. Một neutral schema sẽ cho phép NTS dùng `ltp-server` bây giờ nhưng vẫn không khóa cửa cho analyzer khác sau này. citeturn3view2turn9view4turn16search1turn12view1

### Những gì nên là server-native và những gì nên là derived

`ltp-server` hiện chỉ cấp ba lớp gốc: `cws`, `pos`, `ner`. Vì vậy trong MVP5E, các trường sau phải được coi là **derived inside NTS**, không phải kỳ vọng từ server:

- `entity_spans`
- `entity_candidates`
- `term_candidates`
- `phrase_candidates`

Cách làm đề xuất:

- **entity_spans**: convert từ tag BIO/S của `ner`.  
- **entity_candidates**: gộp entity span lặp lại trong chapter/project, phân loại `person/place/org/other`, cộng thêm heuristic cho tên tông môn/phái/hệ thống nếu NER không tốt.  
- **term_candidates**: lấy từ n-gram 2–6 token có POS pattern ổn định, lặp lại nhiều lần, hoặc khớp regex domain như `【…】`, `第…重`, `…宗`, `…门`, `…诀`, `…功`, `…丹`, `…器`.  
- **phrase_candidates**: lấy từ cụm nhiều token lặp lại, cụm bracketed/system-panel, hoặc span có consistency cao giữa raw/reference.  

Như vậy MVP5E vẫn đạt được yêu cầu “tokens, POS, NER, phrase candidates, entity candidates, term candidates” mà không phải nâng scope lên DEP/CON hoặc full neural parser. Điều này phù hợp với thực tế là official LTP legacy chỉ hỗ trợ CWS/POS/NER, còn `hanlp_mt` chỉ có thể chạy đầy đủ với `con` + `dep`. citeturn18view1turn21view0turn12view1turn13view2

### Cache theo project/chapter/segment

Khuyến nghị cache **chapter-level artifact**, không cache payload nặng trong SQLite. Chapter là đơn vị phù hợp nhất với `ltp-server` vì API xử lý batch theo nhiều dòng trong một request, trong khi segment-level cache sẽ sinh quá nhiều file nhỏ và tăng overhead HTTP/manifest. citeturn3view2turn9view0turn12view0

Thiết kế artifact đúng yêu cầu của bạn:

- `artifacts/nlp/<project>/<chapter_id>.ltp.json`
- `artifacts/nlp/<project>/nlp_cache_manifest.json`
- `artifacts/nlp/<project>/nlp_analysis_report.md`

Khuyến nghị cache key:

```text
cache_key =
  sha256(
    chapter_source_text
    + provider_kind
    + provider_version
    + heuristics_version
    + normalization_version
  )
```

`segment_id` không cần file riêng trong MVP đầu; chỉ cần map `segment_id -> sentence_ids` bên trong file chapter JSON. SQLite chỉ nên giữ metadata mỏng: chapter nào đã phân tích, file artifact ở đâu, source hash là gì, degraded hay không, provider nào đã dùng.

### NLP cache được dùng để cải thiện gì

**Alignment raw/reference** nên là consumer đầu tiên. Context hiện tại của NTS đã đi khá xa về stable prompt/eval/replay/human review, nên giá trị thực của NLP layer nằm ở chỗ làm alignment deterministic tốt hơn, không phải tạo thêm một subsystem nặng. Chapter cache có thể cung cấp AI-friendly anchor cho paragraph alignment: repeated names, sects, panel labels, entities, fixed multi-token terms, punctuation windows. Khi raw và reference cùng chia paragraph không đều, việc so overlap anchor thay vì so thuần index sẽ giúp giảm ghép sai ở các đoạn có tên riêng dày đặc hoặc panel/system text. citeturn3view2turn9view0turn16search1

**Memory extraction** cũng sẽ hưởng lợi ngay. Candidate extraction hiện tại của project đã có nền deterministic và human review workflow; NLP cache sẽ giúp tách rõ hơn candidate nào là canon term/name và candidate nào là style/correction. Ví dụ: `玉清宗` nên đi vào dictionary/name-first flow; `【修为：无】` nên được gắn thêm nhãn panel/style anchor; `游戏人生` có thể là phrase canon. Điều này làm cho memory store đỡ nhiễm “term inventory” và giữ LAMM-T đúng vai trò decision/evidence/style.

**Dictionary builder** là consumer trực tiếp của cache. Một cache chapter-level tốt sẽ giúp builder không phải re-NLP mọi lần review/build/resume, đồng thời cho phép clustering candidate theo source span, entity type, repeated occurrence và chapter spread.

**Verification** nên dùng cache để kiểm tra sau dịch: nếu source chunk chứa một approved dict entry hoặc high-priority entity span, output phải chứa canonical target tương ứng và không chứa forbidden variant. Đây là deterministic gate rất hợp với NTS vì không động vào stable prompt logic lõi.

**Prompt hint retrieval** chỉ nên lấy một lớp rất nhỏ từ NLP cache: exact hits, high-confidence local entities và 1–3 soft phrase hints. Không nên đẩy “toàn bộ phân tích NLP” vào prompt.

### Fallback khi `ltp-server` không chạy

Fallback nên được xem là **degraded analysis mode**, không phải hard error. Hành vi đề xuất:

- sentence split bằng regex dấu câu tiếng Trung
- detect span đặc biệt như `【...】`, chuỗi chữ Hán lặp lại, pattern `第X`, suffix domain
- exact-match với approved dictionary hiện có
- exact-match với approved memory nguồn Trung
- character-span grouping đơn giản để hỗ trợ repeated term mining
- đánh dấu `degraded=true` trong artifact/meta/report

Nếu `ltp-server` không reachable, `nts nlp analyze*` vẫn trả success nhưng có warning rõ ràng. Các consumer downstream cần đọc cờ `degraded`:

- alignment: giảm trọng số NLP anchor
- dictionary builder: chỉ sinh candidate conservative
- verification: chỉ áp strict gate với approved exact-match entries
- prompt hint: chỉ inject exact hits

Phương án này đáp ứng yêu cầu “tool vẫn phải chạy được” mà không tạo cảm giác vỡ pipeline.

### Ước tính thời gian xử lý và RAM trên i5-13450HX, RAM 12 GB

Intel công bố i5-13450HX có 10 core, 16 thread. `ltp-server` hiện set worker threads bằng **một nửa** số CPU, tức trên máy này sidecar sẽ chạy khoảng 8 worker threads. Official LTP legacy benchmark nhấn mạnh đây là nhánh tối ưu cho tốc độ và đưa ra tốc độ công bố rất cao ở 16 thread; hơn nữa `ltp-server` chỉ làm ba tác vụ CWS/POS/NER, không có dependency/semantic parsing. Từ các dữ kiện đó, planning estimate hợp lý cho laptop 12 GB RAM là:

- **cold start** sidecar: khoảng 5–20 giây  
- **steady-state** một chapter 5k–15k ký tự Trung: khoảng 0.5–3 giây  
- **`cache-build` 100 chapter**: khoảng 2–10 phút, chủ yếu phụ thuộc I/O artifact và số câu  
- **RAM cộng thêm**: nên budget khoảng dưới 1 GB đến khoảng 1.2 GB cho sidecar + wrapper + serialization overhead  

Đây là **ước tính triển khai bảo thủ**, không phải benchmark đo trực tiếp trên máy của bạn. Nếu thay bằng full Python LTP (`torch` + `transformers`) chạy sáu tác vụ, footprint và độ nặng dependency sẽ cao hơn đáng kể, không phù hợp bằng legacy sidecar ở phase này trong bối cảnh NTS còn phải chạy translation/learning loop song song. citeturn17search1turn18view1turn21view0turn12view0

### Test plan và acceptance criteria cho MVP5E

MVP5E nên có test plan theo bốn lớp.

**Unit tests** phải cover: sentence splitting, offset rebuilding từ token list, NER BIO/S -> span conversion, POS normalization, candidate generation heuristics, cache key invalidation và degraded fallback.

**Integration tests** phải cover: `ltp-server` healthy path, server down path, chapter artifact write/read, manifest update, cache hit khi source hash không đổi, cache miss khi source_hash/provider_version/heuristics_version đổi.

**Workflow tests** phải cover: alignment helper đọc được NLP cache; memory extraction reclassify được name/term/panel candidates; verification bắt được forbidden variant; prompt hint retrieval không vượt cap.

**Acceptance criteria** nên chốt như sau:

- `nts nlp analyze-chapter` ghi được file `artifacts/nlp/<project>/<chapter_id>.ltp.json`.
- `nts nlp cache-build` bỏ qua chapter đã có cache hợp lệ nếu không `--force`.
- `nts nlp status` hiển thị được health của provider, cache coverage, degraded chapters.
- Khi `ltp-server` down, command vẫn chạy và sinh artifact `degraded=true`.
- Translation/learning commands có thể đọc cache mà không yêu cầu sửa stable prompt cốt lõi.
- Verification bắt được ít nhất một forbidden variant known-bad từ sample Han Jue.
- Toàn bộ test suite mới phải deterministic và không gọi network ngoài localhost.

## MVP5F implementation spec

### Nên học gì từ `build-dict`, và nên bỏ gì

Điểm nên học từ `build-dict` là **mechanics**, không phải **logic từ điển**. Repo này chia input ra file `0.zh.txt`, `1.zh.txt`… bằng chunk khoảng 1000 ký tự; tool `call-gemini` có concurrency mặc định 3, cho phép giới hạn file range bằng `-f/-u`, và skip file output đã tồn tại, nhờ đó resume rất rẻ theo kiểu artifact-based. Đó là đúng tinh thần của một CLI local-first cần chạy được lâu, dừng giữa chừng rồi resume tiếp. citeturn9view1turn9view2turn11view0

Điểm **không** nên học nguyên xi là nhiệm vụ của tool. `build-dict` gửi text tới chat completion endpoint và prompt model làm ba việc một lượt: CWS, POS theo PKU, contextual translation; output được yêu cầu là **một bảng Markdown duy nhất**, rồi raw response được ghi thẳng ra file model-specific. Đó không phải là shape dữ liệu phù hợp cho project dictionary của NTS, nơi bạn cần candidate có `evidence`, `confidence`, `scope`, `provenance`, review lifecycle và integration với translation/learning loop. Nói cách khác, NTS nên mượn “máy chạy chunked range/resume”, chứ không mượn “linh hồn lexicographic prompt của build-dict”. citeturn11view0turn11view2turn22view2

### Project-level dictionary nên được mô hình hóa thế nào

Khuyến nghị tách rất rõ hai khái niệm:

- **dictionary** = canon terms / names / phrases dùng để cưỡng bức nhất quán dịch
- **memory** = decisions / evidence / style / provenance / corrective behavior

Dictionary vì vậy nên trở thành một subsystem riêng với **approved active entries** và **candidate store** riêng. Một entry approved của dictionary có thể được production translation dùng trực tiếp, **không cần** biến thành active memory. Nếu sau này bạn muốn một số entry được promotion sang memory bundle thì đó phải là một thao tác review riêng, không tự động. Cách tách này đúng với yêu cầu tránh phình active memory và cũng khớp với vai trò hiện tại của LAMM-T trong NTS.  

Các loại entry nên hỗ trợ ngay từ MVP5F:

- `name`
- `sect_org`
- `realm`
- `system_label`
- `item_artifact`
- `fixed_phrase`
- `forbidden_variant`

Tôi khuyến nghị `forbidden_variant` không phải là một “dictionary entry độc lập” trong hầu hết trường hợp, mà là **thuộc tính** của một approved entry chính. Ví dụ:

```json
{
  "entry_type": "sect_org",
  "source_text": "玉清宗",
  "target_text": "Ngọc Thanh Tông",
  "forbidden_variants": ["Ngọc Thanh tông"]
}
```

Cách này giúp verification và prompt retrieval đơn giản hơn.

### Candidate store để không làm phình active memory

Candidate store của MVP5F nên là một vùng đệm riêng, gồm:

- candidate metadata trong SQLite
- evidence chi tiết trong artifact JSONL
- review artifact dạng Markdown/CSV cho human approval

Một candidate chưa approve **không** được đi vào:

- `memory_items`
- active dictionary export
- production prompt block

Candidate schema tối thiểu nên có:

```json
{
  "candidate_id": "dictcand_...",
  "dict_run_id": "dict_run_...",
  "entry_type": "realm",
  "source_text": "筑基境",
  "target_text": "Trúc Cơ cảnh",
  "normalized_source": "筑基境",
  "normalized_target": "trúc cơ cảnh",
  "scope": {"project": "han-jue"},
  "confidence": 0.86,
  "confidence_json": {
    "occurrence_count": 5,
    "chapter_spread": 3,
    "reference_consistency": 1.0,
    "nlp_support": "term_pattern+repeat",
    "learning_support": true
  },
  "provenance": [
    "human_reference_alignment",
    "learning_candidate",
    "nlp_term_repeat"
  ],
  "evidence": [
    {
      "chapter_id": "chap_12",
      "segment_id": "seg_88",
      "source_excerpt": "……筑基境……",
      "target_excerpt": "……Trúc Cơ cảnh……"
    }
  ],
  "status": "pending_review"
}
```

Điểm quan trọng là `scope`, `evidence`, `confidence`, `provenance` phải có ngay từ lần đầu build. Đây không chỉ là yêu cầu quản trị; nó còn là cách để review artifacts hữu ích thật sự.

### Cách build dictionary từ NLP cache, raw, reference và learning candidates

MVP5F nên theo pipeline này:

**Pha prepare**

- đọc chapter/segment đã chọn
- bảo đảm NLP cache tồn tại hoặc marked degraded
- nạp raw source, human reference nếu có, learning candidates hiện có
- tạo chunk plan theo chapter/segment boundary
- khóa source fingerprint vào manifest

**Pha build**

Với mỗi chunk, chạy bốn nguồn candidate song song:

- **NLP-derived spans**: entity spans, repeated term spans, bracket/system labels
- **raw/reference alignment spans**: span nguồn Trung có target Việt lặp và ổn định
- **learning candidates**: pending hoặc approved candidate hiện có của vòng học
- **format anchors**: dấu panel, danh hiệu, realm markers, sect suffix, item suffix

Sau đó cluster theo `normalized_source`, group target variants, tính confidence và gán type.

**Pha review**

- sinh `candidates.jsonl`
- sinh `dictionary_review.md`
- sinh `dictionary_review.csv`
- highlight conflict groups: một source có nhiều target, hoặc một target map nhiều source

**Pha approval**

- operator approve/reject candidate
- approved entries đi vào active project dictionary
- rejected entries được audit lại để lần build sau không spam reviewer

### Retrieval cap để không đưa cả dictionary vào prompt

Đây là điểm sống còn. Approved dictionary không có nghĩa là đưa toàn bộ nó vào prompt. Khuyến nghị retrieval theo ba tầng:

- **hard hits**: exact hoặc normalized source match trong chunk hiện tại  
- **soft local hints**: high-confidence phrase/entity nằm trong cùng chapter window  
- **verification-only rules**: forbidden variants, conflict alarms, warnings — không nhất thiết đưa vào prompt

Cap đề xuất cho production translation một chunk:

- tối đa **8 hard entries**
- tối đa **4 soft hints**
- tổng support text từ dictionary không quá **350–500 ký tự**
- nếu đã vượt cap, ưu tiên theo thứ tự: `name` > `sect_org` > `realm` > `system_label` > `item_artifact` > `fixed_phrase`

Khi source chunk không chứa exact hit, dictionary block có thể rỗng. Không có lý do gì phải “nhồi dictionary cho chắc”.

### Tích hợp approved dictionary vào production translation và learning loop

Production translation hiện tại của NTS đã có stable prompt, memory bundle, controlled batch translation và strict gates theo context zip bạn cung cấp. Vì vậy integration đúng là **additive**:

- Trước khi render prompt chunk, chạy dictionary retrieval exact-hit trên source chunk.
- Render một block ngắn, riêng, tách khỏi memory block.
- Dictionary được đọc độc lập với memory bundle.
- Verification sau dịch dùng approved dictionary + forbidden variants.

Khuyến nghị prompt support block kiểu:

```text
Approved project dictionary for this chunk:
- 玉清宗 => Ngọc Thanh Tông
- 灵根资质 => Linh căn tư chất
- 【修为：无】 => 【 Tu vi: Không 】

Forbidden variants:
- 玉清宗 != Ngọc Thanh tông
```

Trong learning loop:

- approved dictionary đi vào baseline context như nguồn canon ổn định
- candidate extraction mới không được tạo duplicate vô ích nếu entry đã có trong active dictionary
- learning loop có thể đề xuất **dictionary candidate mới** hoặc **forbidden variant mới**
- dictionary không tự chuyển thành memory; promotion sang memory là explicit review flow riêng

### Test plan và acceptance criteria cho MVP5F

**Unit tests**: normalization, clustering, conflict detection, confidence scoring, forbidden variant rule, export serialization.

**Integration tests**: prepare/build/review/approve/reject/export command chain; resume từ chunk giữa chừng; skip-existing artifact chunks; dictionary retrieval exact-hit; post-translation verification.

**Workflow tests**: approved dictionary được inject vào production translation; learning loop nhìn thấy active dictionary nhưng không duplicate vào active memory; rejected entries không tái spam reviewer nếu source fingerprint không đổi.

**Acceptance criteria**:

- `nts dict prepare` tạo được `dict_build_manifest.json`.
- `nts dict build` sinh được `candidates.jsonl`, `dictionary_review.md`, `dictionary_review.csv`.
- `nts dict approve` chỉ activate entries được chọn vào project dictionary.
- Không có candidate nào auto-activate vào active memory.
- `nts dict export` sinh được `approved_entries.json`.
- Production translation exact-hit được approved dictionary nhưng prompt block vẫn nằm trong cap.
- Verification bắt được ít nhất một forbidden variant known-bad trên sample project.

## Database and artifact schema proposal

### SQLite tables nên thêm

Để giữ app nhẹ và gần với tinh thần artifact-first của NTS, tôi đề xuất chỉ thêm metadata mỏng vào SQLite, còn payload nặng nằm ở artifact files.

| Table | Mục đích | Cột chính đề xuất |
|---|---|---|
| `nlp_analysis_runs` | Theo dõi cache NLP theo chapter | `id`, `project_id`, `chapter_id`, `provider_kind`, `provider_version`, `heuristics_version`, `source_sha256`, `artifact_path`, `manifest_path`, `status`, `degraded`, `sentence_count`, `token_count`, `created_at`, `updated_at` |
| `dictionary_runs` | Theo dõi một lần build dictionary | `id`, `project_id`, `scope_json`, `source_snapshot_json`, `artifact_dir`, `status`, `created_at`, `updated_at` |
| `dictionary_candidates` | Candidate metadata để review/search/filter | `id`, `dict_run_id`, `project_id`, `entry_type`, `source_text`, `target_text`, `normalized_source`, `normalized_target`, `scope_json`, `confidence_score`, `confidence_json`, `status`, `evidence_count`, `provenance_json`, `artifact_ref_json`, `created_at`, `updated_at`, `reviewed_at` |
| `dictionary_candidate_evidence` | Bằng chứng ngắn, query được | `id`, `candidate_id`, `chapter_id`, `segment_id`, `source_excerpt`, `target_excerpt`, `evidence_kind`, `artifact_ref_json`, `created_at` |
| `project_dictionary_entries` | Active approved dictionary cấp project | `id`, `project_id`, `entry_type`, `source_text`, `target_text`, `normalized_source`, `normalized_target`, `forbidden_variants_json`, `scope_json`, `confidence_score`, `provenance_json`, `status`, `approved_by`, `approved_at`, `created_at`, `updated_at` |
| `dictionary_audit_logs` | Lịch sử approve/reject/deprecate | `id`, `dictionary_entry_id`, `action`, `payload_json`, `created_at` |

### Artifact files nên thêm

| Artifact | Vai trò |
|---|---|
| `artifacts/nlp/<project>/<chapter_id>.ltp.json` | Chapter-level normalized NLP cache |
| `artifacts/nlp/<project>/nlp_cache_manifest.json` | Index/cache coverage/status |
| `artifacts/nlp/<project>/nlp_analysis_report.md` | Report tổng hợp cho operator |
| `artifacts/dictionaries/<dict_run_id>/dict_build_manifest.json` | Fingerprint, chunk plan, resume state |
| `artifacts/dictionaries/<dict_run_id>/candidates.jsonl` | Candidate store đầy đủ |
| `artifacts/dictionaries/<dict_run_id>/dictionary_review.md` | Human-readable review |
| `artifacts/dictionaries/<dict_run_id>/dictionary_review.csv` | Spreadsheet review |
| `artifacts/dictionaries/<dict_run_id>/approved_entries.json` | Export active dictionary |
| `artifacts/dictionaries/<dict_run_id>/chunks/*.json` | Optional chunk-state để resume deterministic |

### Đề xuất schema cho `nlp_cache_manifest.json`

```json
{
  "project_slug": "han-jue",
  "provider": "ltp_server",
  "provider_version": "chi-vi/ltp-server",
  "heuristics_version": "mvp5e-v1",
  "generated_at": "2026-05-25T22:00:00+07:00",
  "chapters": [
    {
      "chapter_id": "chap_001",
      "source_sha256": "…",
      "artifact_path": "artifacts/nlp/han-jue/chap_001.ltp.json",
      "status": "ready",
      "degraded": false,
      "sentence_count": 182,
      "token_count": 1344
    }
  ]
}
```

### Đề xuất schema cho `approved_entries.json`

```json
{
  "project_slug": "han-jue",
  "exported_at": "2026-05-25T22:30:00+07:00",
  "entries": [
    {
      "entry_id": "dict_001",
      "entry_type": "sect_org",
      "source_text": "玉清宗",
      "target_text": "Ngọc Thanh Tông",
      "forbidden_variants": ["Ngọc Thanh tông"],
      "scope": {"project": "han-jue"},
      "confidence": 0.93,
      "provenance": ["human_reference_alignment", "learning_candidate"]
    }
  ]
}
```

## CLI command spec and prompt budget rules

### CLI command spec cho MVP5E

| Command | Mục đích | Ghi chú triển khai |
|---|---|---|
| `nts nlp analyze` | Phân tích một segment hoặc text ad-hoc | Hữu ích cho debug/inspection |
| `nts nlp analyze-chapter` | Phân tích toàn chapter và ghi artifact | Command chính của MVP5E |
| `nts nlp cache-build` | Build cache cho nhiều chapter | Có `--chapters`, `--missing-only`, `--force`, `--resume` |
| `nts nlp status` | Xem health, coverage, degraded chapters | Nên có `--json` |

Khuyến nghị option thực dụng:

```bash
nts nlp analyze-chapter --workspace ./workspace --project han-jue --chapter <chapter_id> --provider ltp-server --json
nts nlp cache-build --workspace ./workspace --project han-jue --chapters 1-50 --missing-only --json
nts nlp status --workspace ./workspace --project han-jue --json
```

### CLI command spec cho MVP5F

| Command | Mục đích | Ghi chú triển khai |
|---|---|---|
| `nts dict prepare` | Tạo run + chunk plan + manifest | Không gọi LLM |
| `nts dict build` | Build candidate theo chunk/range/resume | Default deterministic |
| `nts dict review` | Sinh review markdown/csv hoặc filter candidates | Có `--min-confidence`, `--type` |
| `nts dict approve` | Approve candidate vào active project dictionary | Không tạo memory tự động |
| `nts dict reject` | Reject candidate có audit trail | Có reason |
| `nts dict export` | Export approved dictionary | JSON first |

Khuyến nghị option thực dụng:

```bash
nts dict prepare --workspace ./workspace --project han-jue --chapters 1-100 --json
nts dict build --workspace ./workspace --run <dict_run_id> --from-chunk 0 --to-chunk 20 --resume --json
nts dict review --workspace ./workspace --run <dict_run_id> --min-confidence 0.75
nts dict approve --workspace ./workspace --run <dict_run_id> --candidate-ids id1,id2,id3 --json
nts dict export --workspace ./workspace --project han-jue --json
```

### Prompt và retrieval budget rules

Khuyến nghị retrieval budget cho production translation:

| Bucket | Nguồn | Max entries | Max chars | Có vào prompt không |
|---|---|---:|---:|---|
| Hard canon | Approved dictionary exact hits | 8 | 300 | Có |
| Soft hints | High-confidence local phrase/entity hints | 4 | 150–200 | Có |
| Memory bundle | Existing active memory | Giữ current top-k của app, nhưng nên cap 8 | ~300 | Có |
| Verification-only | Forbidden variants, unresolved conflicts | Không cap cứng | 0 | Không |

Các rule nên khóa cứng:

- Không inject candidate chưa approve.
- Không inject cả dictionary project-wide.
- Không inject NLP raw cache.
- Không inject phrase hint nếu exact canon hit đã đủ mạnh và prompt block sắp vượt cap.
- Nếu không có exact hit trong chunk, dictionary block có thể bỏ trống.
- Forbidden variants nên ưu tiên ở **verification layer**, không ưu tiên nhồi vào prompt nếu không cần.

Rule xếp hạng hit nên là:

1. exact source match dài nhất  
2. type priority: `name` > `sect_org` > `realm` > `system_label` > `item_artifact` > `fixed_phrase`  
3. confidence cao hơn  
4. chapter-local evidence nhiều hơn  
5. entry approved gần đây hơn chỉ để tie-break, không phải weight chính

## Risks and mitigations

Rủi ro lớn nhất là **contract mismatch** giữa các repo tham chiếu. `ltp-server`/LTP legacy đang ở thế giới CWS/POS/NER, còn `hanlp_mt` cần hẳn `con` và `dep` mới phát huy được parser, reordering và dep-dictionary lookup. Nếu cố nối trực tiếp hai thứ này trong MVP5E/F, bạn sẽ hoặc phải bắc cầu bằng heuristic nặng, hoặc kéo scope sang dự án khác. Cách giảm rủi ro là dùng `hanlp_mt` như design reference cho ranking/atomic entity/phrase-first semantics, còn implementation thực tế vẫn xoay quanh neutral schema + deterministic candidate derivation. citeturn3view2turn16search1turn12view1turn13view2turn14view0

Rủi ro pháp lý đứng ngay sau đó. Official LTP materials nêu điều kiện dùng cho mục đích nghiên cứu và nói rõ doanh nghiệp/commercial use cần trao đổi/chi trả riêng. Vì `ltp-server` tải trực tiếp `LTP/legacy` models từ Hugging Face, pipeline của bạn thừa hưởng rủi ro license này. Mitigation đúng là: tách provider thành optional dependency, không bundle model vào repo NTS khi chưa clear pháp lý, và thêm một item “license review” như blocker trước khi formalize production packaging. citeturn19view0turn21view0

Rủi ro kỹ thuật nhỏ nhưng đáng lưu ý là **repo inconsistency**. `ltp-server` README và mã chính bind cổng `3003`, nhưng `verify.sh` lại chờ `3000`. Đây là kiểu footgun rất dễ khiến integration test sai giả. Mitigation là để `nts nlp status` và test harness của NTS dùng config-driven base URL, tuyệt đối không hardcode theo script repo. citeturn3view2turn12view0turn23view0

Rủi ro prompt drift cũng rất thực. `build-dict` bản chất là một prompt-driven lexicographic tool, còn NTS hiện đã có stable prompt được validate. Nếu bạn dùng dictionary builder theo kiểu “có gì cũng nhét vào prompt”, bạn sẽ mất tính ổn định mà dự án đã tốn công xây. Mitigation là cap retrieval nhỏ, chỉ inject approved exact hits và soft hints tối thiểu; phần còn lại dùng cho verification, review và deterministic gates.

Rủi ro false positive/false negative từ NLP là điều chắc chắn xảy ra với domain xianxia. Legacy LTP nhanh nhưng không phải domain-tuned cho cultivation fiction; NER có thể không bắt được tên sect, realm hay system label đúng như mong muốn. Mitigation là để **human reference evidence và review** có trọng số cao hơn NLP; NLP chỉ là extractor/anchor, không phải truth oracle.

Rủi ro pháp lý thứ hai là chuyện **license visibility của repo tham chiếu**. Tôi xác minh được `build-dict` có MIT license, nhưng trên các trang repo tôi đã duyệt cho `ltp-server` và `hanlp_mt`, tôi không xác minh được một license file tương tự. Điều này chưa chứng minh repo không có license; nó chỉ có nghĩa là nếu bạn định port/code-copy trực tiếp, bạn đang bước vào vùng chưa được verify. Mitigation rất rõ: integrate by interface, do not port wholesale. citeturn22view2turn1view0turn1view1

## Implementation roadmap and Do / Don’t

### Roadmap từng bước cho Codex

1. **Thêm `AnalyzerProvider` interface** trong Python, với hai implementation rỗng: `LtpServerAnalyzer` và `FallbackSimpleAnalyzer`.  
2. **Thêm sentence splitter + offset rebuilder** để biến chapter text thành danh sách câu mà vẫn map ngược được về `segment_id`.  
3. **Implement `LtpServerAnalyzer`**: health check, newline-batched request, parse JSON, normalize errors, timeout, degraded fallback.  
4. **Định nghĩa normalized NLP schema** và writer cho `artifacts/nlp/<project>/<chapter_id>.ltp.json`.  
5. **Thêm `nts nlp analyze-chapter`, `nts nlp cache-build`, `nts nlp status`** cùng `nlp_analysis_runs` migration.  
6. **Implement candidate generators**: entity spans, repeated term spans, panel/system label spans, phrase candidates nhẹ.  
7. **Hook NLP cache vào alignment helper, verification helper và memory extraction helper**; giữ integration read-only trước, chưa động vào core translation flow.  
8. **Thêm `dictionary_runs`, `dictionary_candidates`, `project_dictionary_entries`** và artifact layout cho MVP5F.  
9. **Implement `nts dict prepare`** để tạo manifest/chunk plan theo chapter/segment boundary.  
10. **Implement `nts dict build`** theo kiểu deterministic, skip-existing, range/resume; nguồn input là NLP cache + raw/reference + learning candidates.  
11. **Implement `nts dict review`, `approve`, `reject`, `export`** và active dictionary retrieval.  
12. **Hook active dictionary vào production translation** như một support block additive, cap nhỏ, exact-hit only.  
13. **Hook active dictionary vào learning loop** như baseline canon source, nhưng không duplicate vào memory.  
14. **Viết integration tests + real smoke tests** trên project Han Jue trước khi mở rộng ra truyện khác.  
15. **Chốt release gate**: license review cho LTP, prompt budget regression pass, fallback pass, cache determinism pass.

### Do

- **Dùng `ltp-server` như local external sidecar**, không kéo scope sang port code.  
- **Chuẩn hóa output sang neutral schema** để không bị khóa vào PKU hay CTB tagset.  
- **Cache theo chapter**, map ngược về segment bên trong artifact.  
- **Giữ payload nặng ở artifact files, metadata mỏng ở SQLite**.  
- **Tách dictionary khỏi active memory** ngay từ migration đầu tiên.  
- **Bắt buộc human approval** trước khi dictionary entry thành active dictionary, và thêm một bước review riêng nếu sau này muốn promote sang memory.  
- **Chỉ inject exact/local approved hits vào prompt**, còn verification/conflict xử lý ngoài prompt.  
- **Mượn chunk/range/resume/skip-existing từ `build-dict`**, nhưng chunk theo segment/chapter boundary của NTS.  
- **Dùng NLP cache để hỗ trợ alignment, verification và candidate scoring**, không coi nó là truth source tuyệt đối.  
- **Đặt `degraded=true` rõ ràng** khi fallback chạy, để downstream behavior còn kiểm soát được.

### Don’t

- **Đừng port nguyên `ltp-server`, `hanlp_mt` hay `build-dict` vào codebase Python** nếu chưa có một lý do rất mạnh.  
- **Đừng nối trực tiếp `ltp-server` vào `hanlp_mt`** rồi kỳ vọng phrase/dependency semantics tự xuất hiện; contract đầu vào hiện không khớp.  
- **Đừng chuyển MVP5E sang full Python LTP/PyTorch ngay** nếu mục tiêu hiện tại là nhẹ, local-first và Codex-implementable.  
- **Đừng lưu toàn bộ NLP payload trong SQLite**.  
- **Đừng nhét cả dictionary hoặc cả NLP cache vào prompt**.  
- **Đừng biến mọi dictionary candidate thành memory candidate**.  
- **Đừng để approved dictionary tự động thành active memory**.  
- **Đừng để build dictionary phụ thuộc bắt buộc vào LLM/API online** ở MVP đầu.  
- **Đừng hardcode cổng/health check theo script của repo tham chiếu**.  
- **Đừng bỏ qua review license của LTP** nếu production của bạn có yếu tố thương mại.