# 05 — Model Routing Spec

## Goal

Every task can choose its own provider, API key env var, base URL, endpoint type and model/capability class.

## Provider types

Support architecture for:

- `openai_responses`
- `openai_chat_compatible`
- `anthropic_messages`
- `google_gemini` optional later
- local OpenAI-compatible endpoints such as Ollama/LM Studio later

## MVP0 implementation

MVP0 should implement only:

- config schema
- provider config loader
- provider validation shape
- mock provider adapter
- model run logging skeleton

Do not call real provider APIs in MVP0.

## Provider config shape

```yaml
providers:
  mock:
    type: mock
    base_url: "mock://local"
    api_key_env: "MOCK_API_KEY"

  openai_compatible_local:
    type: openai_chat_compatible
    base_url: "http://localhost:1234/v1"
    api_key_env: "LOCAL_API_KEY"
    api_key_optional: true
```

## Task routing shape

```yaml
tasks:
  language_detect:
    primary:
      provider: mock
      model_class: cheap_text
    policy:
      structured_output: true
      max_cost_usd: 0.001
```

## Future task classes

- language_detect
- chapter_alignment
- glossary_extract
- pronoun_extract
- style_learning
- rough_translate
- literary_translate
- context_review
- hallucination_guard
- memory_curator
- ocr_correction
- manga_translate
- manga_typeset_review
- plugin_export
