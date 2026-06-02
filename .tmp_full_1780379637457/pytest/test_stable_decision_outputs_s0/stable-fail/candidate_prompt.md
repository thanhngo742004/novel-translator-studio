# MVP4.8 Candidate Prompt

Prompt SHA-256: `sha256:cb2babb87e6cb03a3cf06f3b450356a7d17680deaccd28a3c1cae3753c5c4921`

```text
Translate Chinese literary prose into natural Vietnamese.
Use concise Vietnamese webnovel style.
Do not expand, explain, embellish, or paraphrase beyond the source.
Do not add translator notes.
Keep system panel/bracket formatting compact.
Translate by merged evaluation unit when the provided JSON contains unit IDs; otherwise translate paragraph-by-paragraph.
Return JSON only, with this shape: {"paragraphs":[{"paragraph_id":"p001","text":"..."}]}
Do not use markdown fences.
Every requested paragraph_id or unit_id must appear exactly once.
Do not add extra paragraph_id values.
Keep paragraph order exactly as provided.
Each returned text field must be one compact complete Vietnamese paragraph or merged unit.
Do not force original micro paragraph breaks inside merged units.
Use the per-unit target_max and strict_max values from the user JSON.
Compression must rewrite complete Vietnamese sentences; never cut words to fit budget.
Each paragraph fails validation if it exceeds strict_max after compression, has dangling brackets, or looks truncated.
Required glossary mappings when the source term appears: [{"source": "韩绝", "target": "Hàn Tuyệt"}, {"source": "玉清宗", "target": "Ngọc Thanh Tông"}, {"source": "炼气境", "target": "Luyện Khí cảnh"}, {"source": "筑基", "target": "Trúc Cơ"}, {"source": "灵根", "target": "linh căn"}, {"source": "修为", "target": "tu vi"}, {"source": "先天气运", "target": "tiên thiên khí vận"}]
Return only the JSON object.
```
