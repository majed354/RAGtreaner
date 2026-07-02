# RAGtreaner

حزمة نقل وتشغيل لمشروع RAG الاستشارات القانونية السعودية.

هذه النسخة جمعت من المشروع المحلي:

`/Users/majd/Desktop/codex/شات الاستشارات`

والخدمة المعتمدة أثناء آخر قياس كانت:

`http://127.0.0.1:8000`

## المحتوى

- `app/`: كود خدمة RAG ولوحة الإدارة.
- `scripts/`: سكربتات البناء، ingest، gates، والتقييم.
- `data/structured/`: corpus القوانين السعودية المنظم.
- `documents/knowledge/saudi_regulations/`: نصوص القوانين واللوائح المعرفة.
- `documents/saudi_regulations/`: وثائق ولوائح أضيفت يدويًا أو onboarded.
- `data/eval/`: ملفات حالات صغيرة لازمة لإعادة تشغيل بعض gates.
- `reports/`: أهم تقارير القياس وسجل القرارات والتحسينات.
- `docs/`: تعليمات الاستعادة والسجل التنفيذي.

## ملاحظة مهمة عن Chroma

قاعدة Chroma الجاهزة في الجهاز الأصلي حجمها يقارب `8GB`، لذلك لم ترفع إلى GitHub.
المستودع يحتوي corpus والسكربتات اللازمة لإعادة بنائها على جهاز جديد.

## استعادة `chunks.jsonl`

ملف `data/structured/chunks.jsonl` الأصلي يتجاوز حد GitHub العملي للملف الواحد، لذلك رُفع مضغوطًا:

`data/structured/chunks.jsonl.gz`

بعد clone على جهاز جديد:

```bash
gunzip -k data/structured/chunks.jsonl.gz
```

## آخر حالة معتمدة

- `/health = ok`
- `project_root = /Users/majd/Desktop/codex/شات الاستشارات`
- `configured_server_port = 8000`
- `knowledge_base_chunks = 22810`
- Chroma actual count = `22810`
- profile: `jamia_recall`
- dense/semantic = `70%`
- lexical = `30%`
- `context_limit = 72`

## آخر نتيجة استراتيجية

مرحلة collection/article/answer-grounding أصبحت قوية جدًا على gates الحالية، لكن gate الاستشارة الصعبة الجديد كشف أن أعلى فجوة متبقية ليست جمع المواد غالبًا، بل تنزيل المواد على وقائع السؤال:

- `consultation_quality_hard6_20260702_baseline`
- score = `75.267/100`
- `5` حالات answer-level issue
- `1` حالة retrieval/package issue

الجولة التالية المنطقية: تحسين answer builder / consultation reasoning بشكل عام، لا ترقيع مواد منفردة.

راجع:

- `docs/RESTORE_ON_NEW_DEVICE_AR.md`
- `docs/CORPUS_MANIFEST.md`
- `docs/PROJECT_HANDOVER_AR.md`
- `reports/rag_optimization_journal.md`
- `reports/rag_decision_log.md`
