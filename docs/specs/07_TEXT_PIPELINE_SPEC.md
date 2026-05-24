# 07 — Text Pipeline Spec

## Future pipeline

1. Import raw / convert.
2. Detect language.
3. Normalize text.
4. Detect chapter boundary.
5. Check alignment when raw + human translation are provided.
6. Segment chapter.
7. Retrieve memory bundle.
8. Rough translation or polish from convert draft.
9. Literary translation with style profile.
10. Context review.
11. Hallucination guard.
12. Style review.
13. Export final translation.
14. Import user correction.
15. Update memory.

## MVP0

No real text pipeline in MVP0.

## MVP1

Start with:

- import raw text
- normalize simple text
- save documents/chapters/segments
- mock translation pipeline or mock provider call
- save task run/model run
