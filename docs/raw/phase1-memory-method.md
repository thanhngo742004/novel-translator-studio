# Thiết kế phương pháp memory cho hệ thống dịch đa miền bằng LAMM-T

## Tóm tắt điều hành

Kết luận chính của phiên Deep Research này là: hệ thống bạn cần **không nên dùng một “translation memory” kiểu CAT tool thuần túy**, cũng không nên dùng **vector memory kiểu agent** làm lõi duy nhất. Phù hợp nhất là một kiến trúc **hybrid**: giữ **memory chuẩn hoá, có cấu trúc, có scope, có confidence, có evidence, có provenance** làm nguồn chân lý; giữ **corpus song song, examples, OCR/layout samples, correction episodes** ở kho phụ; rồi **retrieve theo từng “lane” chuyên trách** để tạo ra một **memory bundle nhỏ gọn** trước khi dịch. Cách này lấy phần tốt nhất của CAT tools như TM, glossary, segmentation, QA, scope/project sharing, đồng thời mượn từ hệ memory của agent các ý tưởng về semantic–episodic–procedural memory, memory tiers, on-demand retrieval, reflection, provenance và pruning. citeturn25view0turn17view1turn21view0turn25view3turn17view6turn24view0turn24view1turn18view1

Tên nên dùng là **LAMM-T — Layered Adaptive Memory Method for Translation**. Tôi giữ lõi “LAMM” vì đúng tinh thần phân tầng, và thêm “-T” để nhấn mạnh đây là phương pháp cho **dịch thuật**, không phải memory chung cho chat agent. LAMM-T nên có **tám layer**, **mười ba memory types**, và một **MVP rất rõ ràng**: `Term`, `Name`, `Pronoun`, `Style`, `Correction`, cộng với `Evidence`, `Confidence`, `Scope Priority`, `Top-k Retrieval`, và `Compact Export`. Phần graph database, fine-tuning, manga auto-layout learning hoàn toàn tự động, cloud sync, hay multi-user collaboration nên để sau. Mức trưởng thành phù hợp hiện tại là: **structured-first, retrieval-first, human-in-the-loop**. citeturn24view0turn24view1turn22view4turn23view2

Điểm khác biệt quan trọng nhất của LAMM-T so với TM truyền thống là: **memory không chỉ lưu source–target**. Nó còn phải lưu **scope**, **độ tin cậy**, **evidence**, **trạng thái hiệu lực**, **xung đột**, **audit trail**, và **mối quan hệ với model/agent đã tạo hoặc kiểm chứng nó**. Trong manga, memory còn phải nhớ **OCR lỗi hay gặp, box corrections, speaker hints, bubble type, overflow history**. Trong y học/dược, memory phải có **source-aware evidence**, **controlled terminology anchors** như UMLS/RxNorm/SNOMED CT, **unit consistency**, và **cảnh báo bất định** trước khi “force inject” vào prompt. citeturn23view0turn23view1turn17view24turn17view18turn17view19turn29view1turn17view20turn17view21

Nếu phải chốt một câu cho toàn bộ nghiên cứu này, thì đó là: **hãy coi memory như một hệ tri thức có kiểm chứng và có phạm vi áp dụng, không phải một bãi ngữ liệu được nhét vào prompt**. RAG gốc cho thấy giá trị của việc kết hợp kiến thức tham số và bộ nhớ ngoài; MemGPT, LangGraph, Letta, CrewAI, LlamaIndex và các hệ tương tự cho thấy value của memory tiering, namespace, JSON documents, blocks, composite scoring và long-term persistence; nhưng với dịch thuật, nhất là truyện và tài liệu chuyên ngành, muốn bền thì phải ưu tiên **structured canon first**, rồi mới đến vector retrieval và reflective learning. citeturn22view0turn22view1turn17view6turn17view7turn24view2turn22view4turn23view5

## Điều rút ra từ nguồn mở và tài liệu

Từ nhánh **CAT / Translation Memory**, có vài nguyên lý rất đáng lấy. OmegaT cho thấy bộ công cụ CAT thực dụng cần **fuzzy matching**, **match propagation**, **nhiều translation memories cùng lúc**, **glossary nhận dạng biến thể hình thái**, và khả năng tương thích với các format trao đổi như TMX, XLIFF, SDLXLIFF. Weblate cho thấy memory phải có **scope** rõ ràng như personal / project / shared, có **trạng thái** như `active` và `pending`, và cần cơ chế **autoclean** để thay thế memory tự động cũ khi bản dịch chuẩn hơn trở thành active. Okapi cho thấy giá trị của chuỗi pipeline gồm **filters**, **segmentation bằng SRX**, **QA checks**, **term extraction**, **TM connectors** và xử lý trên file bilingual như XLIFF/TMX/PO. Translate Toolkit và `poterminology` cho thấy bilingual term extraction thực dụng thường dựa vào **tần suất**, **stop words**, và **alignment tối thiểu đủ dùng**, chứ không nhất thiết phải khởi đầu bằng mô hình nặng. Hunalign, LF Aligner và Bitextor chỉ ra rằng pipeline học memory tốt phải tách rõ **document alignment** và **segment alignment**, và có thể trộn luật, từ điển song ngữ, MT hỗ trợ hoặc scoring theo đặc trưng HTML/tài liệu. TMX, XLIFF và SRX cũng rất đáng học vì chúng đặt ra chuẩn cho **translation unit**, **interoperability**, **segmentation rules**, và xử lý **inline markup**. citeturn25view0turn17view1turn22view6turn22view7turn21view0turn21view1turn21view3turn17view3turn17view4turn25view4turn17view5turn26view0turn26view1turn21view2

Từ đây, phần **nên áp dụng** cho LAMM-T là: tách memory theo project/scope; coi thuật ngữ là first-class objects; lưu bilingual units có metadata; chuẩn hoá segmentation; có QA về độ dài, khoảng trắng, “same as source”, và glossary compliance; cho phép import/export TMX/XLIFF/TBX/CSV để không khóa hệ thống vào một app duy nhất. Phần **không nên bê nguyên** là tư duy “mọi thứ đều là segment pair”. Với truyện và manga, nhiều thông tin quan trọng không nằm ở segment level: xưng hô phụ thuộc quan hệ nhân vật, văn phong phụ thuộc nhóm dịch, speaker phụ thuộc frame, và medicine phụ thuộc concept + evidence chứ không chỉ câu tương tự. Nói cách khác, CAT TM rất tốt cho **đòn bẩy tái sử dụng**, nhưng chưa đủ cho **canon, style, correction, provenance và multimodal page memory**. citeturn21view3turn31view2turn31view0turn31view1turn26view0

Từ nhánh **LLM memory / Adaptive memory**, bức tranh nhất quán hơn nhiều so với vẻ ngoài hỗn loạn của thị trường framework. LangChain/LangGraph phân biệt rõ **semantic memory** là facts và concepts, **episodic memory** là past experiences / few-shot examples, còn **procedural memory** là rules/instructions; đồng thời lưu long-term memories trong custom **namespaces**. Letta biến memory thành **blocks** có thể gắn/tháo khỏi context và dùng chung giữa nhiều agents. LlamaIndex cho phép kết hợp **short-term FIFO** và **long-term extraction over time**. MemGPT đưa ra logic **memory tiers** để làm việc với context window hữu hạn. Reflexion cho thấy learning không nhất thiết phải fine-tune: có thể dùng **reflective text trong episodic buffer** để tránh lặp lại lỗi. Self-RAG cho thấy retrieval nên là **on-demand** và có bước **critique/self-reflection** chứ không phải cứ lấy top-k cố định. CrewAI và AutoGen nhấn mạnh composite scoring, metadata filtering, custom retrieval, và persistence. Zep đẩy thêm ý tưởng **temporal knowledge graph** và assembled context, nhưng chính repo/documentation của họ cũng ngầm cho thấy đó là giải pháp mạnh hơn mức MVP. citeturn24view0turn24view1turn24view2turn22view4turn17view6turn18view0turn18view1turn23view5turn21view5turn23view3turn23view4

Với dịch thuật, phần **nên lấy** từ thế giới agent là: tách memory theo **semantic / episodic / procedural**; lưu memory dưới dạng **JSON documents có schema**; dùng **few-shot episodic examples** để steering style; có **reflection loop** để tạo correction rules từ human edits; có **retrieval theo score tổng hợp** gồm similarity + recency + importance/confidence; và có **tiering** giữa memory “pinned” bắt buộc, memory “retrieved” mềm, và evidence archive ngoài prompt. Phần **không nên lấy nguyên** là thói quen để LLM tự do “viết” memory dài, mơ hồ, thiếu kiểm chứng, rồi tin vào vector similarity. Với dịch thuật thực chiến, memory không giải thích được thì sẽ khó sửa, khó rollback, khó chứng minh đúng/sai. Vì vậy, vector retrieval chỉ nên là **lớp phụ** cho example selection, không phải hệ chân lý. citeturn17view6turn24view0turn24view1turn18view0turn18view1turn23view5

Từ nhánh **style learning và literary translation**, có ba phát hiện rất đáng chú ý. Thứ nhất, nghiên cứu về English–Turkish literary MT cho thấy **style của dịch giả người thật có thể tái tạo được** nếu có corpus aligned đủ tốt và mô hình được adapt theo đúng translator style. Thứ hai, nghiên cứu style-learning prompting cho thấy khoảng cách giữa zero-shot và few-shot MT có thể được đóng lại khoảng **70%** bằng cách **match writing style của target corpus**, kể cả khi chỉ dùng retrieval từ target corpora. Thứ ba, AFSP cho thấy chọn ví dụ few-shot không thể làm bừa: họ retrieve **top-k semantic-similar demonstrations** từ aligned corpus và trong thiết lập thí nghiệm đó, **3 demonstrations** là điểm ngọt. Nhưng một nghiên cứu đánh giá literary translation gần đây vẫn chỉ ra rằng đầu ra LLM **thường literal hơn và ít đa dạng hơn** dịch giả người, dù model mới đã tiến gần hơn. Nghĩa là: style learning có hiệu quả, nhưng **không nên nhầm “style” với một prompt văn vẻ duy nhất**. Style phải học từ song song văn bản, ví dụ tiêu biểu, lựa chọn từ, nhịp câu, và nhất là từ các lần người sửa “văn máy”. citeturn18view3turn18view2turn19view2turn18view4turn18view5turn19view1

Từ nhánh **manga/comic translation**, nguồn mở hiện có cho thấy bài toán này đòi hỏi một nhánh memory riêng. `manga-ocr` xử lý tốt nhiều tình huống điển hình của manga như **text dọc/ngang, furigana, text đè lên ảnh, nhiều font**, và hỗ trợ **multi-line text trong một lần forward pass**; nhưng README của chính dự án cũng cảnh báo OCR có thể **nhận ra chữ dù ảnh không có chữ** và thậm chí “dream up” câu hợp lý-looking, nghĩa là OCR hallucination là rủi ro có thật. `manga-image-translator` và `BallonsTranslator` cho thấy một pipeline tích hợp thường bao gồm **text detection, OCR, text removal/inpainting, translation, typesetting**. `comic-text-detector` cho thấy text-detection tốt trong comics thường cần **nhiều ảnh synthetic/weakly supervised** vì dữ liệu annotate sạch không nhiều. Bài báo “Towards Fully Automated Manga Translation” cho thấy pipeline chuẩn cần đi qua **page pairing, bubble detection, pixel-level masks, split bubble, text recognition, context grouping và alignment**. `Manga109Dialog` cung cấp bằng chứng rằng **speaker attribution** và **frame/reading order** có thể cải thiện đáng kể xử lý lời thoại, với dataset speaker-to-text rất lớn cho comics. `Koharu` lại đáng chú ý vì chứng minh một workflow **local-first** với OCR/vision/LLM chạy trên máy là hoàn toàn khả thi. citeturn32view0turn33view0turn33view1turn28view3turn28view4turn33view2turn33view3turn27view2turn28view0turn28view2turn28view5turn28view6

Từ nhánh **textbook / medicine / pharmacy**, kết luận quan trọng nhất là domain memory phải đi theo **controlled terminology + evidence + uncertainty**, không đi theo “dịch hay là đủ”. UMLS là hệ tập hợp nhiều biomedical vocabularies; RxNorm chuẩn hoá tên thuốc lâm sàng; SNOMED CT là terminology chuẩn cho trao đổi thông tin lâm sàng và do SNOMED International/NLM duy trì. EMA QRD templates cho product information của thuốc cho thấy trong regulatory/pharma có những **wording templates chuẩn** đáng được coi như procedural/domain memory. NIST SI rules cho thấy document kỹ thuật và khoa học cần **quy tắc biểu diễn đơn vị đo** rõ ràng. IMIA guide nhấn mạnh glossary và style guide phải được tạo **trước khi dịch** trong project lớn để giữ consistency. Nghiên cứu HPO cho thấy GPT-3.5 và DeepL có thể cho chất lượng tương đương nhau trên nhiều medical terms, nhưng GPT đôi khi **làm rơi synonyms**. Một nghiên cứu RAG y khoa cho thấy khi grounding trên guideline chuyên ngành, mô hình có thể tăng độ chính xác, độ nhất quán và giảm hallucination. Vì thế, domain memory cho text học thuật/y dược phải ưu tiên **concept anchor**, **source provenance**, **synonym handling**, **unit policy**, và **cảnh báo khi chưa chắc**, thay vì tự tin ép một bản dịch duy nhất vào mọi ngữ cảnh. citeturn17view18turn17view19turn29view1turn29view0turn17view20turn17view21turn29view2turn20view4turn20view5

## Phương pháp LAMM-T

LAMM-T nên được xây theo bốn nguyên tắc cứng.

Thứ nhất, **structured canon trước, vector sau**. Canonical memories như tên riêng, thuật ngữ, xưng hô, rule style, exception, OCR correction, layout preferences phải là object có schema rõ; vector search chỉ phục vụ truy ví dụ gần nghĩa, style exemplars, hay correction episodes tương tự. Điều này phù hợp với cách CAT tools dùng TM/glossary và cũng phù hợp với thực tế rằng semantic search rất tốt để tìm “gần giống”, nhưng không đủ tốt để quyết định “bắt buộc dùng bản dịch nào”. citeturn24view0turn17view1turn21view0

Thứ hai, **scope là logic trung tâm**. LAMM-T không dùng “một bộ nhớ chung cho tất cả”. Mọi memory phải khai báo ít nhất một hoặc nhiều scope: `global`, `language_pair`, `domain`, `genre`, `project_id`, `group_id`, `character_set`, `chapter_range`, `page_range`, `document_section`. Weblate cho thấy scopes và lifecycle management là cực kỳ hữu ích; LangGraph cũng cho thấy long-term memory nên nằm trong custom namespaces thay vì một kho phẳng. citeturn17view1turn24view0

Thứ ba, **memory phải mang evidence và provenance**. Mỗi memory entry cần biết nó đến từ đâu, agent/model nào tạo, model nào validate, con người có duyệt chưa, dựa trên raw nào, translated nào, edit nào. W3C PROV định nghĩa provenance là thông tin về entities, activities và agents tham gia tạo ra dữ liệu; OpenLineage và MLflow bổ sung tư duy rất hữu ích về `job`, `run`, versioning, aliasing, metadata tagging và traceability. Đây là nền tốt để thiết kế `created_by`, `validated_by`, `updated_by`, `audit_log`. citeturn23view0turn23view1turn23view2

Thứ tư, **memory không được phình vô hạn**. Weblate’s autoclean là ví dụ tốt: entry tự sinh cũ có thể bị thay bằng entry active mới tốt hơn, còn imported memory thì giữ lại. LAMM-T nên kế thừa đúng tinh thần này: memory machine-generated, confidence thấp, không còn được retrieve, hoặc bị human sửa ngược nhiều lần thì phải tự hạ cấp, gộp, tóm lược, archive, hoặc deprecate; nhưng raw evidence và audit trail phải còn để rollback. citeturn22view5turn22view6turn22view7

Về **layers**, tôi đề xuất tám layer sau.

- **Global Norm Memory**: quy tắc chung cho mọi bản dịch tiếng Việt, như ưu tiên tự nhiên, tránh bê nguyên cấu trúc máy, không thêm ý lớn, giữ nhất quán khi đã có canon.
- **Language-Pair Heuristic Memory**: heuristic riêng cho `zh-vi`, `ko-vi`, `ja-vi`, `en-vi`, ví dụ cách suy chủ ngữ, ellipsis, honorifics, furigana, loanwords.
- **Domain Memory**: `novel`, `manga`, `textbook`, `medicine`, `pharmacy`, `traditional_medicine`, `technical`, `legal`.
- **Genre Memory**: `tiên hiệp`, `huyền huyễn`, `kiếm hiệp`, `ngôn tình`, `học thuật`, `y dược`, v.v.
- **Project Canon Memory**: canon riêng của từng truyện hoặc tài liệu, gồm tên, thuật ngữ, cảnh giới, địa danh, chương, heading conventions.
- **Entity-Relationship Memory**: tên nhân vật, alias, xưng hô cặp nhân vật, speaker tendencies, quan hệ xã hội, title-to-person mapping.
- **Group Style Memory**: mức Hán-Việt, mức thuần Việt, nhịp câu, thoại/nội tâm, cách dùng đại từ, cách xử lý hài–hành động–tình cảm.
- **Correction and Exception Memory**: tất cả những gì người sửa đã sửa, rule “đừng lặp lại lỗi này”, trường hợp ngoại lệ, memory override tạm thời hoặc vĩnh viễn.

Với manga, tôi **không tách thêm một layer “manga-only”**, mà tách thành **một lane memory riêng** chạy xuyên qua các layer trên: layout memory có thể mang scope project/page/chapter, speaker memory có thể nằm trong entity-relationship layer, typesetting preference nằm ở style/project layer. Cách này giúp một kiến trúc chung vẫn dùng được cho novel, manga, textbook, medicine mà không phải clone hai hệ memory. Ý tưởng phân layer theo namespace và block cũng tương thích với LangGraph và Letta. citeturn24view0turn24view2

Ngoài layer, LAMM-T nên có **sáu lane retrieval** để tránh lẫn vai trò.

- **Lexical lane**: `Term`, `Name`, `Domain Concept`.
- **Relational lane**: `Pronoun`, `Speaker`, `Character Relation`.
- **Style lane**: `Style`, `Phrase/Pattern`, `Few-shot Example`.
- **Correction lane**: `Error`, `Correction`, `Exception`.
- **Visual lane**: `Manga Layout`, `OCR Correction`, `Typesetting`.
- **Provenance lane**: `Evidence`, `Model Provenance`, warnings.

Về **priority**, LAMM-T không dùng một thứ tự cứng cho mọi loại memory, mà dùng **priority theo lane**. Ví dụ: với thuật ngữ kỹ thuật, `Project > Domain > Language > Global`; với xưng hô, `Entity-Relationship > Project > Genre/Style > Language`; với style, `Project Style Override > Group Style > Genre > Global`; với correction, **Correction/Exception** luôn có quyền chèn override trong đúng scope nếu memory còn active và có evidence đủ mạnh. Đây là chỗ TM truyền thống thường thiếu, và là chỗ memory agent thô thường không minh bạch. citeturn24view0turn24view1turn18view0

Về **confidence**, tôi khuyến nghị dùng điểm tổng hợp có giải thích được, không dùng “một con số thần bí”. Mỗi entry có thể có:

```json
{
  "confidence": {
    "score": 0.91,
    "level": "high",
    "components": {
      "human_validation": 1.0,
      "evidence_quality": 0.9,
      "cross_model_agreement": 0.7,
      "frequency": 0.8,
      "recency": 0.6,
      "conflict_penalty": -0.1
    },
    "reason": "derived-from-human-translation-and-confirmed-by-editor"
  }
}
```

Công thức gợi ý:

`score = 0.30*human_validation + 0.20*evidence_quality + 0.15*agreement + 0.15*frequency + 0.10*recency + 0.10*retrieval_success - conflict_penalty - rollback_penalty`

Trong đó `human_validation` là tín hiệu mạnh nhất cho các mục canon; còn với medical/pharma thì thêm điều kiện **hard gate**: nếu `evidence_quality < threshold` hoặc chưa map được sang nguồn thuật ngữ chuẩn thì không được vào `force_terms`, chỉ được vào `warnings`. Cách này phù hợp với tinh thần medical terminology management và provenance-first. citeturn29view2turn17view18turn17view19turn29view1

Về **conflict**, thay vì overwrite, LAMM-T nên tạo **conflict cluster**. Mỗi cluster gom các candidate cạnh tranh cho cùng một `concept_key` hoặc `entity_key`. Một cluster có thể có `winner`, `losers`, `requires_review`, `deprecated_by`, `superseded_reason`. Khi người dùng sửa ngược lại memory cũ, hệ thống không xoá memory cũ ngay; nó:
- tăng evidence cho candidate mới,
- giảm confidence của candidate cũ,
- tạo event trong `audit_log`,
- nếu candidate mới thắng liên tiếp nhiều lần trong cùng scope thì candidat cũ thành `deprecated`,
- nếu memory cũ sai nghiêm trọng do model hallucination thì đánh `rejected`.

Cách làm này gần logic versioning và lineage trong MLflow/OpenLineage hơn là logic “replace in place”, và phù hợp hơn với bài toán dịch nơi cùng một từ có thể đúng–sai theo context. citeturn23view1turn23view2

## Schema và loại memory

Phần này tương ứng với `MEMORY_SCHEMA.json`, `MEMORY_TYPES.md`, và `MEMORY_EXAMPLES.json`.

Đề xuất tốt nhất là dùng một **general envelope** chung cho mọi memory object, rồi để `payload` thay đổi theo từng loại. Như vậy code sau này dễ hơn nhiều so với việc tạo mười ba schema hoàn toàn tách biệt.

```json
{
  "id": "mem_01J...",
  "type": "term|name|pronoun|style|phrase|error|correction|concept|manga_layout|ocr_correction|evidence|model_provenance|plugin_export",
  "subtype": "optional",
  "status": "draft|pending|active|deprecated|rejected",
  "scope": {
    "global": false,
    "source_language": "zh",
    "target_language": "vi",
    "language_pair": "zh-vi",
    "domain": "novel",
    "genre": "tien_hiep",
    "project_id": "pj_xxx",
    "group_id": "grp_xxx",
    "character_ids": ["char_a", "char_b"],
    "chapter_range": [1, 200],
    "page_range": null,
    "document_section": null
  },
  "keys": {
    "source_key": "丹田",
    "concept_key": "concept.dantian",
    "entity_key": null,
    "pattern_key": null
  },
  "value": {},
  "rules": {
    "do": [],
    "dont": []
  },
  "examples": [],
  "evidence": [],
  "confidence": {
    "score": 0.0,
    "level": "low",
    "components": {}
  },
  "stats": {
    "frequency": 0,
    "last_hit_at": null,
    "last_confirmed_at": null
  },
  "conflicts": {
    "cluster_id": null,
    "winner": null,
    "alternatives": []
  },
  "provenance": {
    "created_by": {},
    "validated_by": [],
    "updated_by": [],
    "model_history": []
  },
  "lifecycle": {
    "created_at": "2026-05-24T00:00:00+07:00",
    "updated_at": "2026-05-24T00:00:00+07:00",
    "version": 1,
    "checksum": "sha256:..."
  },
  "notes": ""
}
```

Mười ba loại memory cụ thể nên tối ưu như sau.

**Term Memory** dùng cho thuật ngữ và equivalence chính tắc. Payload nên có `source`, `target`, `pos`, `concept_key`, `term_flags`, `forbidden_targets`, `unit_policy`, `force_injection`. Tạo khi có glossary import, term extraction, human translation ổn định, hoặc domain lexicon chuẩn. Update khi được human xác nhận, có evidence mới, hoặc domain/project đổi chuẩn. Bỏ qua nếu chỉ xuất hiện một lần trong văn cảnh quá mơ hồ. Weblate’s glossary flags như `untranslatable`, `forbidden`, `terminology` là cảm hứng rất tốt cho phần `term_flags`. citeturn31view0turn31view1

**Name Memory** dùng cho tên người, môn phái, địa danh, công pháp, item names, alias và romanization/Hán-Việt lựa chọn. Payload nên có `canonical_target`, `aliases`, `script_variants`, `naming_strategy` như `han_viet|phonetic|keep_original|localized`, và `first_seen_context`. Tạo từ NER + alignment + canon list. Update khi xuất hiện alias mới hoặc project chốt lại cách dịch.

**Pronoun Memory** không nên chỉ lưu “source pronoun -> target pronoun”. Nó phải lưu theo **quan hệ**. Payload nên có `speaker_id`, `listener_id`, `relationship`, `social_rank`, `scene_mood`, `pronoun_target`, `vocative_target`, `fallback_options`. Tạo từ parallel text có speaker info, chapter context, hoặc human corrections. Update rất thường xuyên khi quan hệ nhân vật đổi giai đoạn.

**Style Memory** là procedural memory của hệ dịch. Payload nên có `han_viet_ratio_target`, `naturalness_bias`, `narration_voice`, `dialogue_voice`, `sentence_length_preference`, `split_merge_tendency`, `humor_mode`, `action_mode`, `romance_mode`, `anti_machine_patterns`, `preferred_connectors`. Style này nên lưu cả `positive_examples` lẫn `negative_examples`. Điều này ăn khớp với idea procedural memory/refined instructions. citeturn24view1

**Phrase or Pattern Memory** dùng cho fixed phrases, construction patterns, catchphrases, cultivation idioms, legal/technical boilerplates. Payload gồm `source_pattern`, `target_pattern`, `variables`, `regex_or_template`, `context_guard`. Tạo khi pattern lặp đủ nhiều hoặc có human đánh dấu “luôn dịch thế này”.

**Error Memory** lưu lỗi mô hình/tác vụ. Payload gồm `error_signature`, `error_type`, `trigger`, `bad_output_pattern`, `impact`, `avoidance_rule`. Ví dụ: “对他说” bị dịch thành “đối với hắn nói”. Tạo từ AI translation vs human correction diff.

**Correction Memory** giống Error Memory nhưng theo hướng actionable. Payload gồm `before`, `after`, `fix_rule`, `applicable_context`, `override_priority`. Correction Memory là layer cuối, có quyền override trong đúng scope.

**Domain Concept Memory** cực kỳ quan trọng cho textbook/medicine/pharmacy. Payload gồm `concept_key`, `definition_vi`, `accepted_terms`, `forbidden_terms`, `source_vocab_refs`, `notes_on_usage`, `citation_required`. Với y dược, `source_vocab_refs` nên trỏ tới UMLS/RxNorm/SNOMED/HPO/EMA QRD hoặc nguồn project-approved. citeturn17view18turn17view19turn29view1turn17view20turn20view4

**Manga Layout Memory** lưu visual canon. Payload gồm `page_fingerprint`, `region_id`, `corrected_boxes`, `missed_regions`, `false_positive_regions`, `reading_order_edges`, `bubble_type`, `speaker_hint`, `overflow_tolerance`, `typesetting_preset`. Tạo khi user sửa box, kéo vùng, đổi thứ tự đọc, sửa tràn ô, sửa font/layout.

**OCR Correction Memory** lưu lỗi OCR lặp lại. Payload gồm `ocr_source`, `ocr_bad`, `ocr_good`, `visual_features`, `language_hint`, `confidence_adjustment`, `post_ocr_rule`. Vì manga-ocr tự thừa nhận khả năng “dream up” text, OCR correction memory phải luôn mang cảnh báo nguồn vision/OCR và không được auto-promote quá nhanh. citeturn33view0turn33view1

**Evidence Memory** không phải memory để retrieve trực tiếp vào prompt nhiều, mà là object lưu chứng cứ. Payload gồm `evidence_id`, `source_kind`, `artifact_ref`, `span`, `alignment_ref`, `quote_or_excerpt`, `quality`, `license_or_rights_note`. Evidence là nền để giải thích và rollback.

**Model Provenance Memory** lưu lịch sử sinh ra/kiểm chứng. Payload gồm `created_by`, `validated_by`, `run_ids`, `provider`, `model`, `prompt_hash`, `toolchain`, `input_artifacts`, `output_artifacts`, `verification_result`. Cấu trúc này nên bám tư duy PROV/OpenLineage/MLflow. citeturn23view0turn23view1turn23view2

**Exported Plugin Memory** là bundle nén, read-only, không mang toàn bộ evidence. Payload gồm `bundle_scope`, `top_terms`, `top_names`, `pronoun_rules`, `style_summary`, `fixed_phrases`, `do_dont`, `warnings`, `version`, `checksum`, `exported_at`. Đây là object compile-time, không phải object học trực tiếp.

Dưới đây là một **mẫu rút gọn** cho `MEMORY_EXAMPLES.json` để bạn thấy phương pháp hoạt động ra sao trên năm case tiêu biểu:

```json
[
  {
    "id": "term.zhvi.novel.dantian",
    "type": "term",
    "status": "active",
    "scope": {"language_pair": "zh-vi", "domain": "novel", "genre": "tien_hiep", "project_id": "pj_tkh_001"},
    "keys": {"source_key": "丹田", "concept_key": "concept.dantian"},
    "value": {"source": "丹田", "target": "đan điền", "term_flags": ["terminology", "force_injection"]},
    "confidence": {"score": 0.95, "level": "high"},
    "evidence": [{"evidence_id": "ev_seg_001"}, {"evidence_id": "ev_glossary_004"}]
  },
  {
    "id": "name.kovi.manhwa.mc",
    "type": "name",
    "status": "active",
    "scope": {"language_pair": "ko-vi", "domain": "manga", "project_id": "pj_manhwa_017"},
    "keys": {"source_key": "서준", "entity_key": "char.seojun"},
    "value": {"canonical_target": "Seo Joon", "aliases": ["Seojun"], "naming_strategy": "romanized"},
    "confidence": {"score": 0.89, "level": "high"}
  },
  {
    "id": "pronoun.zhvi.rel.master_disciple",
    "type": "pronoun",
    "status": "active",
    "scope": {"language_pair": "zh-vi", "project_id": "pj_xuanhuan_003", "character_ids": ["char_master", "char_disciple"]},
    "value": {
      "relationship": "master-disciple",
      "speaker_id": "char_disciple",
      "listener_id": "char_master",
      "pronoun_target": "đệ tử",
      "vocative_target": "sư tôn",
      "fallback_options": ["con", "ta"]
    },
    "confidence": {"score": 0.93, "level": "high"}
  },
  {
    "id": "style.zhvi.group_a",
    "type": "style",
    "status": "active",
    "scope": {"language_pair": "zh-vi", "group_id": "grp_a"},
    "value": {
      "han_viet_ratio_target": 0.72,
      "dialogue_voice": "co_dien",
      "narration_voice": "mượt, tiết chế",
      "split_merge_tendency": "prefer_split_long_cn_sentences",
      "anti_machine_patterns": ["đối với hắn nói", "tiến hành", "một loại cảm giác"]
    },
    "confidence": {"score": 0.84, "level": "medium"}
  },
  {
    "id": "concept.envi.med.rxnorm.acetaminophen",
    "type": "concept",
    "status": "active",
    "scope": {"language_pair": "en-vi", "domain": "medicine", "project_id": "pj_med_009"},
    "keys": {"concept_key": "rxnorm.acetaminophen"},
    "value": {
      "accepted_terms": ["acetaminophen", "paracetamol"],
      "preferred_target": "paracetamol",
      "source_vocab_refs": ["RxNorm", "project_formulary"],
      "citation_required": true,
      "warning_if_uncertain": true
    },
    "confidence": {"score": 0.97, "level": "high"}
  }
]
```

## Đặc tả truy xuất memory

Phần này tương ứng với `MEMORY_RETRIEVAL_SPEC.md`.

Bài toán retrieval của bạn không phải “semantic search một phát là xong”. Nó là một bài toán **context assembly** nhiều bước, gần với Zep/GraphRAG ở ý tưởng assemble-context, nhưng nên nhẹ hơn rất nhiều trong MVP. Bundle đưa vào prompt phải là **bộ nhớ tác chiến**, không phải dump database. Zep nhấn mạnh context assembly và relationship-aware retrieval; CrewAI nhấn mạnh semantic + recency + importance; AutoGen nhấn mạnh metadata filtering; còn AFSP chứng minh ví dụ in-context phải được retrieve thích nghi. LAMM-T nên lấy tinh thần đó nhưng áp lên translation-specific lanes. citeturn23view3turn23view4turn23view5turn21view5turn18view4

**Input** của retrieval:

```json
{
  "raw_text": "...",
  "ocr_text": null,
  "page_image_ref": null,
  "source_language": "zh",
  "target_language": "vi",
  "domain": "novel",
  "genre": "tien_hiep",
  "project_id": "pj_xxx",
  "group_style_id": "grp_xxx",
  "character_context": {"speaker_id": "char_a", "listener_id": "char_b"},
  "chapter_context": {"chapter": 128, "scene": "argument"},
  "user_settings": {"han_viet_bias": "auto", "strict_terminology": true},
  "token_budget": 1200
}
```

**Output** của retrieval:

```json
{
  "force_terms": [],
  "force_names": [],
  "pronoun_rules": [],
  "style_rules": [],
  "correction_rules": [],
  "phrase_patterns": [],
  "visual_hints": [],
  "warnings": [],
  "few_shot_examples": [],
  "evidence_refs": [],
  "bundle_summary": ""
}
```

Truy xuất nên chạy theo thứ tự sau.

Đầu tiên là **candidate extraction**. Từ `raw_text`, hệ thống chạy normalizer, tokenizer, script detector, NER/term spotter, phrase miner và optional speech-role parser. Với manga, thêm OCR artifacts, page fingerprint, detected boxes, current region IDs. Với medicine/technical, thêm số đo, đơn vị, abbreviations, thuốc, hoạt chất, section title. Mục tiêu là tạo `candidate_keys`: terms, names, pronouns, patterns, concepts, visual keys.

Tiếp theo là **lane retrieval**.

- **Exact lane**: hash/trie lookup cho term, name, concept, hard project canon. Đây là lane quan trọng nhất.
- **Relational lane**: lookup theo `speaker_id`, `listener_id`, `relationship`, hoặc nearest available relation state.
- **Fuzzy textual lane**: BM25/token-set/fuzzy ratio trên source patterns, segment memories, correction signatures. Ý tưởng này gần fuzzy matching của CAT tools và BM25 retrieval cho few-shot MT. citeturn25view0turn19view2
- **Vector lane**: style examples, corrected examples, phrase-level exemplars, chapter-similar episodes. Đây là semantic help layer, không phải canon layer.
- **Visual lane**: page/project-specific layout/OCR memories, reading-order hints, overflow statistics.
- **Evidence lane**: chỉ kéo evidence refs/warnings khi task là medical/pharma/textbook hoặc confidence thấp.

Mỗi candidate sau đó được **scoring** theo công thức chuẩn hoá:

`final_score = 0.28*scope_score + 0.22*match_score + 0.18*confidence_score + 0.10*evidence_score + 0.08*recency_score + 0.07*frequency_score + 0.07*preference_fit - 0.15*conflict_penalty - 0.10*staleness_penalty`

Trong đó:
- `scope_score`: project exact = 1.00; project family = 0.92; group style = 0.82; genre = 0.72; domain = 0.64; language-pair = 0.52; global = 0.40.
- `match_score`: exact literal/concept hit > entity relation hit > fuzzy phrase match > vector similarity.
- `evidence_score`: human-translated aligned evidence > approved glossary > user correction > AI-only extraction.
- `preference_fit`: user settings hiện tại có khớp style/domain không.
- `conflict_penalty`: cluster đang tranh chấp mạnh, chưa reviewer resolve.
- `staleness_penalty`: memory quá cũ hoặc bị sửa ngược nhiều lần.

Sau scoring là **type-specific hard rules**. Một vài hard rules cần áp dụng:

- `force_terms`: chỉ nhận `active`, confidence đủ ngưỡng, không conflict unresolved.
- Với `medicine/pharmacy`, nếu term chưa có evidence hoặc chưa được approved trong domain/project, term chuyển sang `warnings` thay vì `force_terms`.
- `pronoun_rules`: chỉ inject tối đa khi speaker/listener context đủ gần.
- `style_rules`: ưu tiên ít mà sắc; không đưa cả profile dài.
- `few_shot_examples`: mặc định 2–3 ví dụ. AFSP cho thấy 3 là mức thường hiệu quả trong một thiết lập MT cụ thể; trong thực tế plugin/App sau này bạn nên benchmark 2/3/4. citeturn18view5

**Conflict filtering** nên theo cluster. Nếu cùng `concept_key` có nhiều target:
- cùng scope: chọn confidence cao hơn;
- khác scope: chọn scope hẹp hơn;
- nếu domain safety-critical: chọn mục có evidence chuẩn hơn;
- nếu vẫn hoà: đẩy thành warning và không force inject.

**Token budgeting** nên làm theo bucket thay vì cut ngẫu nhiên. Gợi ý cho `token_budget = 1200`:
- force terms + names: 250–350 tokens;
- pronouns + relation hints: 80–150;
- style rules: 120–180;
- correction rules: 120–180;
- phrase patterns: 80–120;
- 2–3 few-shot examples: 350–500;
- warnings/evidence refs: phần còn lại.

Nếu budget thấp hơn, ưu tiên theo thứ tự:
`force_terms > force_names > pronouns > critical corrections > style_summary > examples > optional warnings`.

Pseudo-code gợi ý:

```python
def build_memory_bundle(ctx):
    candidates = extract_candidates(ctx)

    exact_hits = lookup_exact(candidates, ctx.scope)
    relation_hits = lookup_relations(candidates, ctx.character_context, ctx.scope)
    fuzzy_hits = lookup_fuzzy(candidates, ctx.scope)
    vector_hits = lookup_vector_examples(ctx.raw_text, ctx.scope)
    visual_hits = lookup_visual(ctx.page_image_ref, ctx.ocr_text, ctx.scope)
    evidence_hits = lookup_evidence(candidates, ctx.scope)

    all_hits = merge_hits(
        exact_hits, relation_hits, fuzzy_hits, vector_hits, visual_hits, evidence_hits
    )

    ranked = []
    for hit in all_hits:
        score = score_hit(hit, ctx)
        if pass_hard_rules(hit, ctx):
            ranked.append((score, hit))

    ranked.sort(reverse=True, key=lambda x: x[0])
    resolved = resolve_conflicts(ranked, ctx)

    bundle = allocate_budget_by_bucket(resolved, ctx.token_budget)
    bundle = summarize_style_and_examples(bundle, ctx.token_budget)
    bundle = attach_warnings(bundle, ctx)

    return bundle
```

Thực thi thực tế cho MVP không cần graph DB. Bạn có thể bắt đầu với:
- `SQLite/Postgres` cho structured memory,
- `FTS/BM25` cho fuzzy text,
- một vector index nhẹ cho examples,
- file JSON export cho plugin.  
Lên phase 2 hoặc phase 3 mới cân nhắc graph/temporal relations phức tạp hơn. citeturn21view7turn23view3

## Quy trình học, pruning và provenance

Phần này tương ứng với `MEMORY_LEARNING_PIPELINES.md`.

**Pipeline học từ raw + human translation** nên là pipeline gốc vì đây là nguồn tín hiệu sạch nhất. Quy trình đề xuất:
- normalize source/target;
- segment theo SRX-like rules tùy ngôn ngữ và domain;
- align paragraph rồi align sentence bằng hunalign/LF Aligner hoặc hybrid alignment;
- extract names, terms, pronouns, patterns;
- đo style features: Hán-Việt bias, nhịp câu, tỉ lệ split/merge, thoại/nội tâm, preferred vocatives;
- tạo examples đã align;
- chấm confidence theo evidence quality, alignment quality, repetition, scope consistency;
- tạo conflict clusters nếu khác canon cũ;
- xuất `learn_report.md` với phần “đã học chắc”, “đã học nhưng cần review”, “mâu thuẫn”.  
Các aligner và chuẩn SRX/TMX/XLIFF cho thấy đây là một pipeline rất thực dụng, không phải ý tưởng viển vông. citeturn17view4turn25view4turn21view2turn26view0turn26view1

**Pipeline học từ AI translation + human correction** là nơi tạo ra `Error Memory` và `Correction Memory`. Ở đây đừng chỉ lưu diff raw text. Hãy lưu:
- loại lỗi (`literal`, `term_wrong`, `pronoun_wrong`, `speaker_mismatch`, `machineese`, `overflow`, `OCR_carryover`);
- pattern gây lỗi;
- fix cuối cùng;
- scope áp dụng;
- model/source đã sinh lỗi;
- có lặp lại bao nhiêu lần.  
Đây chính là biến thể translation-specific của tư duy Reflexion: học từ linguistic feedback chứ không cần fine-tune model ngay. citeturn18view0

**Pipeline học từ manga manual box correction** phải xử lý ít nhất bảy biến thành quả:
- `missed_region`;
- `false_positive_region`;
- `box_resize_history`;
- `bubble_type`;
- `reading_order_override`;
- `speaker_hint`;
- `typesetting_overflow_history`.  
Bài báo về automated manga translation cho thấy detection, mask estimation, split bubble và context grouping là những bước khác nhau; vì vậy user correction nên được gắn về đúng bước lỗi, không ghi chung một “visual note”. Manga109Dialog cũng cho thấy speaker prediction tốt hơn khi xét frame/reading order; do đó manual correction về thứ tự đọc và speaker nên được coi là memory lane hạng nặng, không phải comment bên lề. citeturn27view2turn28view1turn28view2

**Pipeline import glossary** phải phân biệt ba loại nguồn:
- **authoritative glossary**: project canon, termbase cũ, domain lexicon chuẩn;
- **legacy noisy glossary**: glossaries nhóm dịch cũ nhưng chưa chuẩn hoá;
- **machine-extracted glossary**: term extraction từ corpus.  
Authoritative vào `active` nhanh hơn; noisy vào `pending`; machine-extracted vào `draft` cho đến khi có repeat evidence hoặc human confirm. Logic `active/pending` lấy cảm hứng trực tiếp từ Weblate là rất đáng dùng. citeturn22view6

**Pipeline export plugin memory** nên là bước compile chứ không phải dump database. Plugin export chỉ nên lấy:
- top terms bắt buộc,
- top names/aliases,
- pronoun rules đang active,
- 5–8 style rules,
- 5–10 correction rules hay gặp,
- 3–8 phrase patterns,
- warnings cực quan trọng,
- version + checksum + exported scope.  
Không export raw audit log dài, không export toàn bộ evidence list, không export mọi conflict alternatives. Nếu plugin cần gọn hơn nữa, hãy export thêm một `style_summary` 120–200 từ và `do/dont` 8–12 dòng.

Về **pruning**, tôi đề xuất bảy quy tắc.

- **Autoclean machine-generated duplicates**: nếu cùng source/concept/scope mà có bản active mới tốt hơn, bản auto-generated cũ bị deprecate.
- **Archive, don’t erase**: raw evidence và audit log chuyển kho lạnh, không xoá hẳn.
- **Promote by repeat confirmation**: machine memory chỉ lên `active` khi có đủ repeated hits hoặc human confirm.
- **Decay by non-use**: memory không được hit trong thời gian dài và confidence thấp thì giảm điểm retrieve.
- **Split overloaded objects**: style profile quá to thì tách thành narration/dialogue/pronoun/tone modules.
- **Merge near-duplicates**: alias/term variants gộp dưới `concept_key`.
- **Rollback ready**: mọi update đều tạo version mới, không destructively overwrite.

Về **provenance schema**, block tối thiểu nên như sau:

```json
{
  "provenance": {
    "created_by": {
      "agent": "memory_writer",
      "provider": "openai",
      "model": "gpt-4.1-mini",
      "run_id": "run_...",
      "timestamp": "2026-05-24T09:00:00+07:00"
    },
    "validated_by": [
      {
        "agent": "hallucination_checker",
        "provider": "anthropic",
        "model": "claude-3.7-sonnet",
        "result": "pass-with-warning",
        "timestamp": "2026-05-24T09:01:00+07:00"
      },
      {
        "agent": "human_editor",
        "user_id": "u_001",
        "result": "approved",
        "timestamp": "2026-05-24T09:05:00+07:00"
      }
    ],
    "updated_by": [],
    "audit_log": [
      {
        "event": "created",
        "from_version": 0,
        "to_version": 1,
        "reason": "learned_from_raw_human_parallel"
      }
    ]
  }
}
```

Khối này bám khá sát ba ý tưởng đã được chứng minh hữu ích: **entity/activity/agent** của PROV, **dataset/job/run/facets** của OpenLineage, và **lineage/versioning/tagging** của MLflow. citeturn23view0turn23view1turn23view2

**MVP đầu tiên** nên cực kỳ thực dụng:
- học từ `raw + human translation`,
- học từ `AI + human correction`,
- lưu `Term`, `Name`, `Pronoun`, `Style`, `Correction`,
- có `Evidence`,
- có `Confidence`,
- có `Scope Priority`,
- retrieve bằng `exact + fuzzy + top-k examples`,
- export `compact plugin memory`.  
Không cần graph DB, không cần fine-tuning, không cần manga fully automatic learning ngay. Nếu muốn chạy local một phần, manga/OCR pipeline hoàn toàn có thể dựa vào local-first tooling như Koharu hoặc local OCR/detectors, còn memory core có thể chạy bằng SQLite + local vector index. citeturn28view5turn28view6

## Kết luận và gói file đầu ra

Nếu tách nội dung báo cáo này thành các file bạn yêu cầu, tôi khuyến nghị chia như sau:  
`MEMORY_RESEARCH_REPORT.md` = phần **Tóm tắt điều hành** + **Điều rút ra từ nguồn mở và tài liệu**.  
`MEMORY_METHOD.md` = phần **Phương pháp LAMM-T**.  
`MEMORY_SCHEMA.json`, `MEMORY_TYPES.md`, `MEMORY_EXAMPLES.json` = phần **Schema và loại memory**.  
`MEMORY_RETRIEVAL_SPEC.md` = phần **Đặc tả truy xuất memory**.  
`MEMORY_LEARNING_PIPELINES.md` = phần **Quy trình học, pruning và provenance**.  
`DEEP_RESEARCH_SOURCES.md` = danh sách nguồn trọng yếu ngay bên dưới.  

Chốt lại các câu hỏi quyết định của phiên nghiên cứu này:

- **Tên phương pháp nên dùng**: **LAMM-T — Layered Adaptive Memory Method for Translation**.
- **Số layer nên dùng**: **8 layers**.
- **Số loại memory nên hỗ trợ**: **13 types** như bạn yêu cầu.
- **MVP nên gồm gì**: `Term`, `Name`, `Pronoun`, `Style`, `Correction`, cộng `Evidence`, `Confidence`, `Scope Priority`, `Top-k Retrieval`, `Compact Export`.
- **Nên bắt đầu học memory từ đâu**: từ **raw + human translation** trước, sau đó mới thêm **AI + human correction**, rồi mới tới manga visual corrections và domain terminology import.
- **Cách retrieve khi dịch**: `exact lookup -> relation lookup -> fuzzy/BM25 -> vector examples -> conflict filter -> token budgeting -> bundle summary`.
- **Cách xử lý conflict**: không overwrite trực tiếp; dùng `conflict cluster`, `winner`, `alternatives`, `deprecated/rejected`, có rollback.
- **Cách export cho plugin**: compile ra bundle read-only rất gọn, có `style_summary`, `hard terms`, `hard names`, `pronoun rules`, `do/dont`, `warnings`, `checksum`.
- **Repo/công cụ đáng học nhất**: OmegaT, Weblate, Okapi Framework, Translate Toolkit, Hunalign/LF Aligner, Bitextor, Letta/MemGPT, LangGraph memory, LlamaIndex memory, Reflexion, Self-RAG, manga-ocr, manga-image-translator, BallonsTranslator, Manga109Dialog, Koharu, cùng các tài nguyên chuẩn như TMX/XLIFF/SRX, UMLS, RxNorm, SNOMED CT, EMA QRD, NIST SI. citeturn25view0turn17view1turn21view0turn25view3turn17view4turn25view4turn17view5turn17view6turn24view2turn22view4turn18view0turn18view1turn32view0turn28view3turn28view4turn28view1turn28view5turn26view0turn26view1turn17view18turn17view19turn29view1turn17view20turn17view21

Danh sách nguồn đáng học nhất và **áp dụng / không áp dụng** ngắn gọn:

- **OmegaT**: học cách tổ chức TM nhiều nguồn, fuzzy matching, glossary inflection, interoperability; không dùng làm model memory trực tiếp. citeturn25view0
- **Weblate**: học scopes, `active/pending`, autoclean, glossary flags, history/revert, length checks; rất nên mượn cho lifecycle và QA. citeturn17view1turn31view0turn31view1turn31view2
- **Okapi Framework**: học filters, pipelines, SRX, QA, file bilingual; rất hợp cho ingestion/normalization. citeturn21view0turn21view1turn21view3turn21view4
- **Translate Toolkit / poterminology**: học term extraction nhẹ, format conversions; hợp cho MVP. citeturn17view3turn25view3
- **Hunalign / LF Aligner / Bitextor**: học alignment nhiều tầng; rất đáng dùng trước khi nghĩ tới model alignment phức tạp. citeturn17view4turn25view4turn17view5
- **LangGraph / Letta / LlamaIndex / MemGPT**: học semantic–episodic–procedural memory, namespaces, blocks, memory tiers; không nên bê nguyên framework vào translation app nếu chưa cần. citeturn24view0turn24view1turn24view2turn22view4turn17view6
- **Reflexion / Self-RAG**: học reflective correction loop và retrieval on-demand; rất hợp cho correction memory và evidence-aware generation. citeturn18view0turn18view1
- **manga-ocr / manga-image-translator / BallonsTranslator / Manga109Dialog / Koharu**: học OCR, auto-translation pipeline, typesetting, speaker linking, local-first deployment; nhưng không được tin mù quáng vì OCR/vision có thể hallucinate hoặc miss boxes. citeturn32view0turn33view0turn28view3turn28view4turn28view1turn28view5
- **UMLS / RxNorm / SNOMED CT / EMA QRD / NIST SI**: đây là phần bắt buộc nếu sau này dịch y dược/tài liệu kỹ thuật nghiêm túc. citeturn17view18turn17view19turn29view1turn17view20turn17view21

Những gì nên đưa vào **Deep Research phiên 2 về kiến trúc app**:
- kiến trúc storage: SQLite/Postgres + JSONB + FTS + optional vector index;
- physical tables / collections cho từng memory lane;
- alignment service;
- memory writer / validator / retriever agents;
- prompt assembly service;
- evaluation harness theo domain;
- CLI/Desktop dataflow;
- import/export TMX/XLIFF/TBX/JSON;
- human review workflow;
- benchmark datasets cho `zh-vi`, `ko-vi`, `ja-vi`, `en-vi`;
- plugin export compiler và cache invalidation.

**Prompt ngắn đề xuất cho Deep Research phiên 2**:

```text
Hãy thực hiện Deep Research phiên 2 để thiết kế kiến trúc app cho LAMM-T memory system.
Tập trung vào:
- storage architecture
- database schema
- indexing strategy
- retrieval service
- learning service
- provenance/audit service
- plugin export service
- desktop/CLI-ready architecture
- local-first options
- evaluation harness
Không code app ngay. Chỉ nghiên cứu và thiết kế technical architecture đủ rõ để triển khai sau.
```

**Open questions / limitations**: báo cáo này đã đủ để chốt **memory method**, nhưng còn ba thứ nên kiểm chứng bằng prototype nhỏ trước khi khoá cứng thiết kế:  
một là hiệu quả thật của pronoun/entity relation memory trên `zh-vi` và `ko-vi`;  
hai là trade-off giữa BM25 và vector retrieval cho style examples trong truyện dài;  
ba là confidence calibration cho manga OCR/layout corrections khi nguồn OCR vốn có xu hướng “ảo giác chữ” hoặc miss vùng. Điểm này không làm báo cáo mất giá trị, nhưng đúng là cần một prototype đo đạc trước khi đi sang phase app architecture. citeturn33view0turn33view1turn28view1