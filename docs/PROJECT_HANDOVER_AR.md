# Project Handover

آخر تحديث: 2026-07-02

## الهدف

بناء RAG قانوني سعودي قادر على:

- استرجاع النظام الحاكم.
- استرجاع اللوائح التنفيذية والضوابط المرافقة.
- إدخال المواد الدقيقة داخل السياق.
- ربط المواد بأسماء الأنظمة في الجواب.
- تقديم استشارة عملية تنزل المواد على وقائع السؤال.

## الحالة الحالية

### readiness

- الخدمة: `http://127.0.0.1:8000`
- `/health = ok`
- `project_root = /Users/majd/Desktop/codex/شات الاستشارات`
- `configured_server_port = 8000`
- `knowledge_base_chunks = 22810`
- Chroma actual count = `22810`
- retrieval profile = `jamia_recall`
- semantic/dense = `70%`
- lexical = `30%`
- `context_limit = 72`

## ما تحسن فعليًا

### 1. الجمع العام والمواد الدقيقة

المرحلة الأوضح نجاحًا هي collection/article precision.

تقارير معتمدة:

- `manual_strong_collection_gate_20260526_after_article_precision_collection_restored.json`
  - collection score = `100/100`
  - core recall = `1.0`
  - companion recall = `1.0`
  - full package rate = `1.0`
  - fatal core miss = `0`

- `manual_article_precision_regression375_collection_after_final_patch.json`
  - collection score = `100/100`
  - cases = `375/375`

- `manual_article_precision_heldout375_collection_after_final_patch.json`
  - collection score = `100/100`
  - cases = `375/375`

- `manual_article_precision_blind100_20260702_after_final_answer_phrase_routes.json`
  - article score = `100/100`
  - pass rate = `1.0`
  - case context entry rate = `1.0`
  - pollution rate = `0.001`

الخلاصة:

لم نعد في مرحلة "النظام لا يجمع المواد". هذه المرحلة أغلقت مبدئيًا على gates الحالية.

### 2. ربط المواد بالأنظمة داخل الجواب

قبل هذه المرحلة كانت الإجابة تذكر أرقام مواد عارية أحيانًا.

بعد الإصلاح:

- صار قسم المواد يعرض:
  - اسم النظام أو اللائحة.
  - المواد المرتبطة بها.

تقرير معتمد:

- `manual_answer_grounding_heldout30_20260702_after_final_phrase_routes.json`
  - answer grounding score = `100/100`
  - pass rate = `1.0`
  - article number recall = `1.0`
  - regulation presence rate = `1.0`

### 3. تحسين ترتيب السياق

تحسن موضع المواد داخل السياق بعد context ranking وrequired article seeding.

أمثلة:

- answer grounding mean context position تحسن سابقًا من نحو `48.9` إلى `33.675` ثم في heldout30 الأخير إلى `14.037`.
- blind100 الأخير:
  - case mean context position = `10.1`
  - case context entry rate = `1.0`

### 4. hygiene القياس

تم فصل:

- operational issue
- retrieval/package issue
- answer-level issue
- eval/gold issue

أمثلة مهمة:

- فشل `Operation not permitted` من sandbox لا يعد RAG gap.
- مادة طيران غير متصلة بالواقعة صُنفت eval/gold issue لا retrieval gap.

## أين كنا ندور

كنا ندور عندما كنا نخلط بين:

- فشل تشغيل الخدمة أو sandbox.
- فشل gold label.
- سقوط مادة فعلية من السياق.
- ضعف صياغة الجواب رغم وجود المادة.

كما كنا نطيل دورات article precision بعد أن وصلت إلى `100/100` على شرائح متعددة. هذا أفاد في تثبيت الجمع، لكنه لم يعد المسار الأكثر إنتاجية للترقية النوعية.

## أعلى فجوة متبقية

آخر gate جديد:

- `consultation_quality_hard6_20260702_baseline.json`
- score = `75.267/100`
- pass rate = `0.0`
- `5` حالات answer-level issue
- `1` حالة retrieval/package issue

متوسط المحاور:

- material context score = `0.87`
- issue context score = `0.882`
- answer material score = `0.799`
- issue answer score = `0.671`
- practical application score = `0.38`

الخلاصة:

الفجوة الكبرى الآن ليست أن النظام لا يجد القانون غالبًا، بل أن answer builder الحالي قالبي ولا ينزل المواد على الوقائع بما يكفي.

## الجولة التالية المنطقية

1. تحسين answer builder للاستشارة:
   - محور الواقعة.
   - النظام/اللائحة.
   - المادة.
   - التطبيق العملي.
   - النتيجة/المخاطر/الإجراء.

2. إعادة تشغيل `consultation_quality_hard6`.

3. إذا تحسن hard6 دون كسر article/answer grounding:
   - بناء hard consultation slice أوسع.
   - ثم regression صغير.
   - ثم held-out.

4. لا نعود إلى ترقيعات retrieval إلا في الحالات التي تثبت فيها diagnostics أن المادة أو النظام غير موجود في السياق.

## قاعدة العمل

لا تعتمد على ذاكرة المحادثة.

ابدأ دائمًا من:

1. `/health`
2. Chroma actual count
3. `jamia_recall` weights
4. `context_limit`
5. آخر تقارير `reports/`
6. التمييز الصارم بين operational/retrieval/answer-level
