# الاستعادة على جهاز جديد

هذا الدليل يهدف إلى نقل مشروع RAG القانوني السعودي إلى جهاز آخر من هذا المستودع.

## 1. استنساخ المستودع

```bash
git clone https://github.com/majed354/RAGtreaner.git
cd RAGtreaner
```

## 2. إنشاء البيئة

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. إعداد البيئة

```bash
cp .env.example .env
```

ثم عدل `.env` بحسب الجهاز الجديد.

مهم:

- لا تغيّر المنفذ إذا أردت مطابقة القياسات السابقة.
- المنفذ المعتمد: `8000`.

## 4. استعادة chunks

```bash
gunzip -k data/structured/chunks.jsonl.gz
```

بعد ذلك يجب أن يوجد:

`data/structured/chunks.jsonl`

## 5. إعادة بناء Chroma

قاعدة Chroma الجاهزة لم ترفع لأنها تقارب `8GB`.

أعد بناء الفهرس من corpus الموجود. السكربتات ذات الصلة موجودة في:

- `scripts/build_structured_legal_corpus.py`
- `scripts/organize_saudi_regulations.py`
- `scripts/onboard_regulations_inbox.py`

في المشروع الأصلي كان العدد المعتمد:

- `knowledge_base_chunks = 22810`
- Chroma actual count = `22810`
- collection = `saudi_legal_consultations`

بعد إعادة البناء، تحقق من أن العدد يساوي `22810` قبل اعتماد القياسات.

## 6. تشغيل الخدمة

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

ثم تحقق:

```bash
curl -s http://127.0.0.1:8000/health
```

المتوقع في النسخة الأصلية:

- `status = ok`
- `configured_server_port = 8000`
- `knowledge_base_chunks = 22810`

## 7. تشغيل gates سريعة

بعد ثبات الخدمة:

```bash
python scripts/run_article_precision_gate.py \
  --cases data/eval/manual_article_precision_gate_20260526.jsonl \
  --output reports/local_article_precision_check.json \
  --service-url http://127.0.0.1:8000/internal/rag/query \
  --retrieval-profile jamia_recall
```

ثم gate جودة الاستشارة الصعبة:

```bash
python scripts/run_consultation_quality_gate.py \
  --cases data/eval/legal_eval_hard_set.jsonl \
  --output reports/local_consultation_quality_hard6.json \
  --service-url http://127.0.0.1:8000/internal/rag/query \
  --answer-mode consultation \
  --retrieval-profile jamia_recall \
  --timeout-seconds 240
```

## 8. خط الأساس المتوقع

آخر readiness في الجهاز الأصلي:

- `/health = ok`
- `knowledge_base_chunks = 22810`
- Chroma actual count = `22810`
- `jamia_recall`: dense `70%`, lexical `30%`
- `context_limit = 72`

آخر collection/article gates:

- collection/article precision: `100/100` على الشرائح المعتمدة.
- answer grounding heldout30: `100/100`.

آخر gate للاستشارة العملية:

- consultation quality hard6: `75.267/100`.
- أعلى gap: answer-level application, لا collection عام.

## 9. ما لا تعتمد عليه

- لا تعتبر أي timeout أو فشل اتصال فجوة RAG.
- لا تشغل full regression قبل ثبات `/health` وعدد Chroma.
- لا تبدأ بتعديل retrieval إذا كانت المواد موجودة في السياق لكن الجواب لا يطبقها على الواقعة؛ هذا answer-level issue.
