# Corpus Manifest

آخر تحديث: 2026-07-02

## مصدر النسخة

- المشروع المحلي الأصلي: `/Users/majd/Desktop/codex/شات الاستشارات`
- الخدمة المعتمدة: `http://127.0.0.1:8000`
- Chroma collection: `saudi_legal_consultations`
- Chroma actual count في الجهاز الأصلي: `22810`

## الملفات القانونية المرفوعة

### البيانات المنظمة

المسار:

`data/structured/`

المحتوى:

- `905` ملفًا.
- `by_regulation/`: ملفات JSON/TXT/HTML لكل نظام أو لائحة.
- `verbatim_texts/`: نصوص حرفية مساعدة.
- `official_snapshots/`: لقطات رسمية محفوظة.
- `regulations.json` و`regulations.csv`: فهرس الأنظمة واللوائح.
- `articles.jsonl` و`articles.csv`: المواد النظامية.
- `paragraphs.jsonl` و`paragraphs.csv`: الفقرات.
- `chunks.csv`: chunks جاهزة للفحص.
- `chunks.jsonl.gz`: نسخة مضغوطة من `chunks.jsonl`.

سبب ضغط `chunks.jsonl`:

- الحجم الأصلي: `100,457,228` بايت.
- هذا يتجاوز حد GitHub العملي للملف الواحد.
- الاستعادة:

```bash
gunzip -k data/structured/chunks.jsonl.gz
```

### وثائق القوانين واللوائح

المسار:

`documents/`

المحتوى:

- `319` ملفًا.
- `documents/knowledge/saudi_regulations/`: نصوص القوانين واللوائح التي تغذي قاعدة المعرفة.
- `documents/saudi_regulations/`: ملفات onboarded وinbox، ومنها لوائح PDF/TXT مضافة يدويًا.

## ما لم يرفع

### Chroma الجاهزة

لم ترفع:

- `data/chromadb/`
- `.chroma/`

السبب:

- الحجم يقارب `8GB`.
- الأفضل إعادة بنائها على الجهاز الجديد من corpus المرفوع.

### ملفات البيئة والأسرار

لم ترفع:

- `.env`
- `.env.save`

المرفوع بدلًا منها:

- `.env.example`

## ملفات التقييم المرفقة

`reports/` يحتوي التقارير المعتمدة المختارة:

- `manual_strong_collection_gate_20260526_after_article_precision_collection_restored.json`
- `manual_article_precision_regression375_collection_after_final_patch.json`
- `manual_article_precision_heldout375_collection_after_final_patch.json`
- `manual_article_precision_gate_20260526_final_local.json`
- `manual_article_precision_blind100_20260702_after_final_answer_phrase_routes.json`
- `manual_answer_grounding_heldout30_20260702_after_final_phrase_routes.json`
- `consultation_quality_hard6_20260702_baseline.json`
- `rag_optimization_journal.md`
- `rag_decision_log.md`

`data/eval/` يحتوي ملفات حالات صغيرة قابلة لإعادة التشغيل:

- `legal_eval_hard_set.jsonl`
- `manual_article_precision_gate_20260526.jsonl`
- `manual_answer_grounding_heldout30_20260702_from_blind100_remainder.jsonl`
