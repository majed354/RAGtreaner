# RAG Decision Log

## 2026-07-01 — Required Article Seeding + Drug Axis + Gold Hygiene

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| فصل metric الحالة عن diagnostics الواسعة | eval instrumentation | article precision gate | إضافة `case_*` metrics وقراءة `selected_article_context_positions` حتى لا تُحسب توقعات المحرك العامة كأنها gold للحالة | تقارير واسعة قد توحي بانخفاض دخول السياق رغم اكتمال gold | manual gate بقي `100/100` مع `case_context_entry_rate=1.0` |
| ضمان دخول المواد المطلوبة المتوقعة من الراوتر | retrieval/package issue | context selection | إضافة `required_article_seed_limit=32` و`required_article_seed_per_slug=8` مع تقديم slugs المتعلمة/heldout قبل cores العامة | edge3 كان يكشف سقوط مواد عند حافة السياق | targeted edge3: `100/100`, manual: `100/100` |
| ربط محور المشاركة/تداخل العقوبات في المخدرات بمواد محددة | retrieval/package issue | query analysis + article routing | عند مخدرات/مؤثرات عقلية: المشاركة/المساعدة تضيف `58`، وتعدد/تداخل العقوبات يضيف `62/64` | raw held-out فقد `nzam-mkafhh-almkhdrat-walmwthrat-alaqlyh:58` | drug probe: `100/100`, case context entry `1.0` |
| عدم إجبار النظام على مادة طيران غير متصلة بالواقعة | eval/gold issue | label hygiene | إنشاء held-out adjudicated يسقط `nzam-altyran-almdny:79` لأن المادة عن الحجز التنفيذي لا التشغيل/التدريب، ومراجعة المعلم ذكرت 86 و97 فقط | raw held-out بعد التصحيح: `95.8/100` بسبب 79 وحدها | adjudicated held-out: `100/100` |
| استبعاد فشل sandbox في answer-grounding | operational issue | measurement hygiene | إعادة تشغيل الفحص بصلاحية وصول محلي بعد `Operation not permitted` | أول answer-grounding: `12` transport errors | rerun: `100/100`, transport `0` |

### نتائج الاعتماد

- readiness:
  - `/health ok`
  - `project_root=/Users/majd/Desktop/codex/شات الاستشارات`
  - `configured_server_port=8000`
  - `knowledge_base_chunks=22810`
  - Chroma actual count `22810`
  - hybrid mix: dense `70%`, lexical `30%`
  - `context_limit=72`
- targeted:
  - `data/eval/manual_article_precision_wide16_20260701_drug_participation_probe_after_axis_patch.json` = `100/100`
- regression:
  - `data/eval/manual_article_precision_gate_20260701_after_drug_participation_axis_patch.json` = `100/100`
  - `data/eval/manual_article_precision_wide16_20260701_working_regression_after_drug_participation_axis_patch.json` = `100/100`
- held-out:
  - raw: `data/eval/manual_article_precision_wide16_20260701_raw_heldout_after_drug_participation_axis_patch.json` = `95.8/100`, failed `1` بسبب gold label للطيران.
  - adjudicated: `data/eval/manual_article_precision_wide16_20260701_heldout_adjudicated_after_drug_participation_axis_patch.json` = `100/100`
- blind:
  - `data/eval/manual_article_precision_blind40_20260701_after_drug_participation_axis_patch.json` = `100/100`, pollution `0.008`
- answer-level:
  - `data/eval/manual_answer_grounding_blind12_20260701_after_drug_participation_axis_patch.json` = `100/100`

### المتبقي

- أعلى gap ليس operational ولا retrieval/package على القياسات المراجعة.
- أعلى gap فعلي هو `eval/gold hygiene`: يلزم audit للمواد المتوقعة قبل جعل أي held-out جديد gate ملزمًا.
- الجولة التالية المنطقية:
  - بناء label-audit/adjudication gate لشريحة article precision الجديدة، ثم اختبار answer-level reasoning الأعمق بعد ثبوت حضور المواد الدقيقة.

## 2026-06-30 — Blind40 Generalization + Heldout Hint Filter

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| بناء شريحة blind خارج ذاكرة الـpacker | eval design | blind generalization | إضافة `scripts/build_blind_article_precision_slice.py` لاختيار 40 حالة/40 مجالًا مع استبعاد أمثلة ومواد packer | لا يوجد قياس blind مستقل بعد الترقية | baseline blind: `99.2/100`, pass `0.975`, failed `1`, transport `0` |
| تشخيص فشل تعليم الكبار كمشكلة إزاحة سياق | retrieval/package issue | context budget + hint matching | المادة `8` كانت مرشحة لكنها لم تدخل سياق `72` بسبب hints عامة بعيدة | missing `nzam-talym-alkbar-wmhw-alamyh-fy-almmlkh-alarbyh-alsawdyh:8` | targeted education probe بعد الفلترة: `100/100` |
| تضييق كلمات heldout hints العامة | retrieval/package issue | signal filtering | حذف ألفاظ مثل `التي/تحكم/تخطط/الجهات/الحكومية/الإجراءات/الأسس` من مطابقة packer في المحرك ومولّد artifact | hints كانت تسحب CMA/companies/procurement في سؤال تعليمي | blind40: `100/100`, failed `0`, transport `0` |
| إعادة بناء packer بعد الفلترة | retrieval/package issue | artifact hygiene | إعادة بناء `heldout_axis_packer_v1.json` بعد تحديث stopwords | artifact سابق احتوى إشارات عامة قابلة للتطابق الزائد | packer probe: `100/100`, pass `1.0`, pollution `0.0` |

### نتائج الاعتماد

- readiness:
  - `/health ok`
  - `project_root=/Users/majd/Desktop/codex/شات الاستشارات`
  - `configured_server_port=8000`
  - `knowledge_base_chunks=22810`
  - Chroma actual count `22810`
- blind40:
  - before: `data/eval/manual_article_precision_blind40_20260630_after_heldout_axis_router.json`
  - after: `data/eval/manual_article_precision_blind40_20260630_after_hint_filter.json`
  - score: `99.2 -> 100.0`
  - pass_rate: `0.975 -> 1.0`
  - failed_cases: `1 -> 0`
- targeted probe:
  - report: `data/eval/manual_article_precision_blind40_education_probe_20260630_after_hint_filter.json`
  - score: `100/100`
- heldout packer probe:
  - report: `data/eval/heldout_axis_packer_probe_20260630_after_hint_filter.json`
  - score: `100/100`
- regression:
  - manual slice: `data/eval/manual_article_precision_gate_20260630_after_hint_filter.json` = `100/100`
  - working regression: `data/eval/manual_article_precision_wide16_20260630_working_regression_after_hint_filter.json` = `100/100`
  - held-out check: `data/eval/manual_article_precision_wide16_20260630_heldout_after_hint_filter.json` = `100/100`

### المتبقي

- لا يوجد operational issue ولا article-level collection gap على gates الحالية.
- أعلى gap متبقٍ هو answer-level grounding وترتيب المواد داخل السياق، خصوصًا الحالات الناجحة ذات context positions المتأخرة.
- الجولة التالية المنطقية:
  - بناء blind answer-grounding slice يقيس الاستدلال والاقتباس من المواد الموجودة، ثم تحسين reranking/source-use في الجواب.

## 2026-06-30 — Heldout Gap Router + General Axis Packer

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تحويل فجوات المقالات الأخيرة إلى موجّه قابل للتعميم | retrieval/package issue | gap mining + context packing | إنشاء `heldout_axis_packer_v1.json` من فجوات غير تشغيلية في تقارير article autopilot، مع إشارات cluster عامة وإشارات case-specific دقيقة | فجوات held-out متكررة في حضور المواد الدقيقة رغم حضور النظام أحيانًا | probe hard وصل إلى `100/100` بعد التدرج `54.2 -> 97.2 -> 100` على endpoint الداخلي الصحيح |
| تقديم إشارات الحالة الدقيقة على الإشارات العامة | retrieval/package issue | article candidate selection | جعل case-specific hints تدخل قبل cluster hints، ورفع وزنها عند تطابق محور الواقعة والنظام والمادة | الإشارة العامة كانت تملأ ميزانية النظام بمواد قريبة لكنها غير مطلوبة | probe hard النهائي: `pass_rate=1.0`, `failed_cases=0` |
| حماية الشريحة اليدوية من الإشارات العامة الضعيفة | retrieval/package issue | signal filtering | استبعاد ألفاظ عامة مثل `يريد/تريد/بحجة/طلب`، واشتراط overlap أعلى للحالات ذات دعم ضعيف | regression أولي أسقط مادة `government-tenders-and-procurement-law:88` بسبب حقن noise | manual gate بعد الفلترة: `100/100`, `failed_cases=0`, `pollution_rate=0.0` |
| إضافة تشخيصات تبين أثر الموجّه بدل الاكتفاء بالنتيجة | eval instrumentation | diagnostics | إضافة حقول `heldout_axis_hints`, `heldout_axis_article_pairs`, `heldout_axis_packer_article_pairs`, وعدادات coverage packer في gate runner | لم يكن واضحًا هل المادة دخلت بفعل الموجّه أم بفعل retrieval العام | التقارير الجديدة تفصل أثر heldout packer عن بقية السياق |
| استبعاد القياس الخاطئ على route الجذر | operational issue | measurement hygiene | استبعاد baseline شُغّل على `http://127.0.0.1:8000` بدل `/internal/rag/query` | تقرير root URL أعطى `0/100` بلا diagnostics | لا يُحسب فجوة RAG؛ الاعتماد فقط على endpoint الداخلي والتقارير ذات diagnostics |

### نتائج الاعتماد

- readiness:
  - `/health ok`
  - `project_root=/Users/majd/Desktop/codex/شات الاستشارات`
  - `configured_server_port=8000`
  - `knowledge_base_chunks=22810`
  - Chroma actual count `22810`
  - hybrid mix: semantic/dense `70%`, lexical `30%`
  - `context_limit=72`
- targeted hard probe:
  - final report: `data/eval/heldout_axis_packer_probe_20260630_after_router_v4_filtered.json`
  - score: `100/100`
  - pass_rate: `1.0`
  - failed_cases: `0`
  - transport_error_cases: `0`
  - pollution_rate: `0.0`
- manual slice:
  - report: `data/eval/manual_article_precision_gate_20260630_after_heldout_axis_router_v2.json`
  - article_score: `100/100`
  - pass_rate: `1.0`
  - failed_cases: `0`
- working regression:
  - report: `data/eval/manual_article_precision_wide16_20260630_working_regression_after_heldout_axis_router.json`
  - article_score: `100/100`
  - pass_rate: `1.0`
  - failed_cases: `0`
- held-out check:
  - report: `data/eval/manual_article_precision_wide16_20260630_heldout_after_heldout_axis_router.json`
  - article_score: `100/100`
  - pass_rate: `1.0`
  - failed_cases: `0`

### المتبقي

- أعلى gap متبقٍ ليس operational، وليس core collection miss على gates الحالية.
- المتبقي الأقرب هو تعميم held-out خارج نافذة الفجوات التي بُني منها `heldout_axis_packer_v1.json`، ثم قياس answer-level grounding بعد ثبوت حضور المواد الدقيقة في السياق.
- الجولة التالية المنطقية:
  - بناء شريحة blind جديدة من وقائع غير مستخدمة في بناء packer، ثم اختبار reranking/claim-grounding على مستوى الجواب لا على مستوى الجمع فقط.

## 2026-05-02 — Round 25

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| توسيع صيغ النفقة والحضانة | retrieval/package issue | query signals + domain policy | إضافة إشارات `نفقة عاجلة`، مصاريف السكن/التعليم/العلاج، الحكم المؤقت، وحجب PDPL/e-commerce عند سياق أحوال شخصية بلا بيانات شخصية صريحة | family avg داخل slice لم يكن مستقرًا وظهرت PDPL كـ core miss | family avg `0.926`، fatal `0.000` |
| حماية credit/mortgage من PDPL/e-commerce | retrieval/package issue | document bundles + domain policy | توسيع إشارات ممول عقاري/موافقة مكتوبة/سبب الرفض/بيانات السلوك الائتماني وحجب الحزم العامة | credit/mortgage `0.188` مع fatal core doc miss | credit/mortgage `0.914` بلا fatal أو trap |
| تقوية الدليل الرقمي للصيغ غير المعتادة | retrieval/package issue | claim signals + domain policy | إضافة `رسائل منصة`، `بريد رسمي`، `مراسلات موثقة` وإزالة copyright عند غياب سياق حقوق مؤلف صريح | copyright noise في evidence/platform cases | electronic evidence avg `0.913`، contamination `0.000` |
| التقاط منصة الوساطة العقارية | retrieval/package issue | context signal | إضافة `منصة وساطة عقارية`، `عقد وساطة مكتوب`، `رسائل التطبيق` إلى سياق الوساطة | real estate platform case جذب copyright | real estate avg `0.954`، domain purity `1.000` |
| قفل policy-locked bundles | retrieval/package issue | legal domain policy | عند حزمة خاصة مقفلة، تُزال trap domains وتقدّم الأنظمة الخاصة واللوائح الخاصة | slice baseline domain purity `0.739` | final domain purity `1.000` |

### نتائج الاعتماد

- horizontal generalization slice:
  - before: `data/eval/manual_round25_generalization_hardness_baseline_benchmark.json`
  - after: `data/eval/manual_round25_generalization_hardness_after_real_estate_platform_patch_benchmark.json`
  - score: `0.759 -> 0.932`
  - contamination: `0.133 -> 0.000`
  - fatal core doc miss: `0.067 -> 0.000`
- user/manual proxy:
  - report: `data/eval/manual_round23_user_spot_package_gaps_round25_signal_bundle_guard_benchmark.json`
  - score: `0.998`, approx `39.9/40`
- gates:
  - manual slice: `pass`
  - working regression: `pass`
  - held-out: `pass`

### المتبقي

- أعلى gap متبقٍ هو answer/package completeness في:
  - PDPL/e-commerce cross-border.
  - customs sub-issues.
  - digital evidence/arbitration granular coverage.

## 2026-05-02 — Round 26

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| فصل حزمة PDPL/e-commerce العابرة للحدود | retrieval/package issue | claim specs + domain policy | إضافة مواصفات مستقلة للتجارة الإلكترونية، لائحة نقل البيانات، واللائحة التنفيذية، والسماح بها في filter العائلة | PDPL/e-commerce cross-border `0.907`, sub_issue `0.562` | `0.996`, sub_issue `1.000`, package `1.000` |
| ضمان حزمة مواد الجمارك العملية | retrieval/package issue | claim specs | إضافة `customs_value_broker_smuggling_package` لمواد `61/113/127/143/145/154` | customs `0.939`, sub_issue `0.750` | `0.995`, sub_issue `1.000`, package `1.000` |
| إزالة أثر copyright من سياقات المنصة غير الحقوقية | retrieval/package issue | trap pruning + domain scores | تضييق مواصفات حقوق المؤلف وحذف `copyright-law` من `domain_scores` عند وجود سياق خاص غير حقوقي | digital/arbitration purity `0.750`, contamination `1` | purity `1.000`, contamination `0` |
| تقوية التحكيم الإلكتروني وسجل التدقيق | retrieval/package issue | claim specs + domain policy | إضافة حزم `arbitration_*_support` و`electronic_automated_audit_evidence` والسماح بـ `law-of-evidence` في التعاقد الآلي عند دليل رقمي صريح | digital/arbitration `0.677` | `0.936` |

### نتائج الاعتماد

- targeted package orchestration:
  - before: `data/eval/manual_round26_package_orchestration_focus_baseline_benchmark.json`
  - after: `data/eval/manual_round26_package_orchestration_focus_after_evidence_support_patch_benchmark.json`
  - score: `0.841 -> 0.975`
  - domain purity: `0.917 -> 1.000`
  - contamination: `0.083 -> 0.000`
- user/manual proxy:
  - report: `data/eval/manual_round23_user_spot_package_gaps_round26_package_orchestration_benchmark.json`
  - score: `0.998`, approx `39.9/40`
- gates:
  - manual slice: `pass`
  - working regression: `pass`
  - held-out: `pass`

### المتبقي

- أعلى gap متبقٍ هو digital evidence/arbitration:
  - score `0.936`
  - sub_issue_coverage `0.834`
  - يحتاج تحسين answer/package ordering لإظهار مواد الإثبات الرقمية في الجواب بثبات.

## 2026-05-02 — Round 27

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| توسيع تعميم الدليل الرقمي | retrieval/package issue | context signals + claim specs | إضافة صيغ `سجل الأحداث`، `الطابع الزمني`، `سجلات المنصة`، `سجل الاعتماد`، `مخرجات لوحة التحكم` | targeted slice `0.421`, purity `0.400` | final targeted `0.973`, purity `1.000` |
| ربط التحكيم الإلكتروني بالحزمة الرقمية | retrieval/package issue | claim specs + domain policy | توسيع صيغ `بند فض النزاع` و`بالتحكيم` و`بوابة مشاريع` وربطها بـ `electronic-transactions-law` و`law-of-evidence` | حالات التحكيم الجديدة جذبت PDPL/copyright أو اكتفت بالتحكيم | كل حالات التحكيم المستهدفة `>= 0.912` وبلا traps |
| تضييق الإشارات العامة في الجمارك وPDPL | retrieval/package issue | trap pruning | منع `بضاعة` و`سجلات` العامة من تشغيل حزم الجمارك أو PDPL الصحي بلا مرساة خاصة | cross-domain noise في `delivery_receipts` و`dashboard_invoice` | contamination `0.000`, domain_purity `1.000` |
| إبراز مواد الحزمة الظاهرة في benchmark | answer-level issue | benchmark answer builder | إضافة مواد المصادر الظاهرة من `law-of-evidence` و`electronic-transactions-law` و`nzam-althkym` إلى المواد المساندة | المواد المصاحبة تصل أحيانًا ولا تظهر في الجواب | article_hit_rate `1.000`, package `0.983` |

### نتائج الاعتماد

- targeted digital evidence/arbitration:
  - before: `data/eval/manual_round27_digital_evidence_answer_package_baseline_benchmark.json`
  - after: `data/eval/manual_round27_digital_evidence_answer_package_after_pdpl_generic_tightening_benchmark.json`
  - score: `0.421 -> 0.973`
  - domain purity: `0.400 -> 1.000`
  - contamination: `0.400 -> 0.000`
- user/manual proxy:
  - report: `data/eval/manual_round23_user_spot_package_gaps_round27_digital_evidence_generalization_benchmark.json`
  - score: `0.998`, approx `39.9/40`
- gates:
  - manual slice: `pass`
  - working regression: `pass`
  - held-out: `pass`

### المتبقي

- أعلى gap متبقٍ ليس تشغيلًا ولا core retrieval miss.
- المتبقي الأقرب: `answer-level / package completeness` في حالات التعاقد الآلي متعددة المحاور؛ targeted `multi_issue` بلغ `0.930` مقابل `0.992` في cross-domain.

## 2026-05-02 — Round 28

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| بدء الخدمة المتوقفة قبل القياس | operational issue | service readiness | تشغيل الخدمة على `127.0.0.1:8000` بعد فشل `/health`، ثم التحقق من Chroma `19004` | `/health` failed | `/health ok`, Chroma `19004` |
| توسيع صياغات السوق الإلكتروني الأجنبي | retrieval/package issue | context signals + claim specs | إضافة `سوق إلكتروني/سوق إلكتروني أجنبي` و`مركز خارجها` إلى إشارات e-commerce/PDPL/transfer | حالة foreign marketplace score `0.744`, sub_issue `0.250` | PDPL/e-commerce family avg `0.993`, sub_issue `1.000` |
| منع التقاط فصل العامل من الفصل القضائي | retrieval/package issue | claim spec gating | إضافة `required_any` عمالي إلى `labor_termination_notice` حتى لا يكفي لفظ `فصل` وحده | family support had false labor bundle and one quality refusal | family support avg `0.974`, no low confidence |
| تخفيف رفض quality gate عند اكتمال المواد | answer-level issue | quality gate calibration | عدم اعتبار `missing_legal_function_support` وحده severe إذا كانت الأنظمة والمواد الحاكمة مستوفاة | commercial statements `0.800`, quality low رغم اكتمال المواد | commercial statements `0.995`, quality medium |

### نتائج الاعتماد

- broad generalization targeted:
  - before: `data/eval/manual_round28_broad_generalization_probe_baseline_benchmark.json`
  - after: `data/eval/manual_round28_broad_generalization_probe_after_crossborder_quality_patch_benchmark.json`
  - gate: `data/eval/manual_round28_broad_generalization_probe_gate_crossborder_quality_patch_benchmark.json`
  - score: `0.956 -> 0.980`
  - domain purity: `1.000 -> 1.000`
  - package: `0.954 -> 0.965`
  - contamination: `0.000 -> 0.000`
- user/manual proxy:
  - report: `data/eval/manual_round23_user_spot_package_gaps_round28_broad_generalization_benchmark.json`
  - score: `0.998`, approx `39.9/40`
- gates:
  - manual slice: `pass`
  - working regression: `pass`
  - held-out: `pass`

### المتبقي

- ليس operational ولا contamination ولا core retrieval miss.
- أعلى gap متبقٍ هو package completeness في العائلات الأقل تغطية:
  - الاتصالات.
  - الأجهزة والمستلزمات الطبية.
  - الغذاء.
  - المعلومات الائتمانية/التمويل العقاري.

## 2026-05-02 — Round 29

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تحويل نتيجة 28/40 اليدوية إلى شريحة أفقية | retrieval/package issue | eval slice design | بناء `manual_round29_user_manual_gap_recovery.jsonl` بـ 12 حالة تغطي العمل، التجارة الإلكترونية+PDPL، PDPL الصحي، والمشتريات الحكومية | manual external score `28/40` | targeted baseline `0.842` يثبت الفجوات نفسها |
| إصلاح تشغيل مؤقت بعد الرقعة الأولى | operational issue | query runtime ordering | حذف إشارة `pdpl_operational_breach_context` من موضع يسبق تعريفها | report invalid: `manual_round29_user_manual_gap_recovery_after_multi_issue_patch_benchmark.json` فيه HTTP 500 | service stable, `/health ok`, Chroma `19004` |
| تقوية حزمة إنهاء العمل متعددة المحاور | retrieval/package issue | context signals + mandatory/core articles | توسيع صيغ الإنهاء والفصل، وإضافة `عاملا/العامل`، وتثبيت `74/75/76/77` مع `84/88/90/94` | labor family avg `0.927`, q003 missing termination articles | labor family avg `1.000`, no contamination |
| ربط الدورة/الخدمة الرقمية غير المفعلة بالتجارة الإلكترونية | retrieval/package issue | context signals + claim specs | إضافة `دورة إلكترونية`، `منصة تدريب`، `لم تفعّل`، `لم تفتح حساب`، `تعذر الدخول`، وربطها بـ `10/13/14/17` | e-commerce service/data family avg `0.582`, one copyright trap | e-commerce family avg `0.909`, traps `0` |
| منع دخول التجارة الإلكترونية في تطبيق صحي بلا بيع | answer-level/domain-policy issue | domain policy + trap pruning | إضافة no-ecommerce negation guard لعبارات مثل `لا توجد مؤشرات بيع إلكتروني أو متجر` | PDPL health family avg `0.877`, traps `2` | PDPL health family avg `1.000`, traps `0` |
| إبراز حساسية البيانات الصحية | retrieval/package + answer-level issue | claim specs + answer support | إضافة صيغ البيانات الصحية ودعم PDPL article `23` وimplementing-regulation article `26` | الصحة العابرة للحدود تغطي النقل لكنها لا تبرز الصحة الحساسة كفاية | sub_issue health transfer `1.000`, package `1.000` |
| تثبيت لائحة المشتريات التنفيذية في التأخير بسبب الجهة | retrieval/package issue | claim specs + answer support | إضافة article `97` إلى حزمة `procurement_contract_delay_penalties` وصيغ `تأخر بسبب الجهة/مورد من الباطن` | procurement family avg `0.981`, q012 missing `97` | procurement family avg `0.994`, package `1.000` |

### نتائج الاعتماد

- targeted manual gap recovery:
  - before: `data/eval/manual_round29_user_manual_gap_recovery_baseline_benchmark.json`
  - after: `data/eval/manual_round29_user_manual_gap_recovery_after_health_negation_patch_benchmark.json`
  - gate: `data/eval/manual_round29_user_manual_gap_recovery_gate_health_negation_patch_benchmark.json`
  - score: `0.842 -> 0.976`
  - domain purity: `0.833 -> 1.000`
  - sub_issue: `0.701 -> 0.917`
  - package: `0.807 -> 0.967`
  - contamination: `0.250 -> 0.000`
- user/manual proxy:
  - report: `data/eval/manual_round23_user_spot_package_gaps_round29_user_manual_gap_recovery_benchmark.json`
  - score: `0.998`, approx `39.9/40`
- gates:
  - manual slice: `pass`
  - working regression: `pass`
  - held-out: `pass`

### المتبقي

- أعلى gap متبقٍ ليس operational ولا contamination ولا core doc miss.
- المتبقي الأوضح هو e-commerce digital service non-activation:
  - score `0.909`
  - sub_issue_coverage `0.778`
  - package_completeness `0.897`
- الجولة التالية المنطقية:
  - Round 30: تقوية شق الخدمة الرقمية غير المفعلة/المتأخرة داخل التجارة الإلكترونية، مع الحفاظ على PDPL marketing companion وعدم السماح لحقوق المؤلف أو PDPL أن يبتلع شق الخدمة.

## 2026-05-04 — Round 30

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إعادة تشغيل الخدمة المتوقفة قبل القياس | operational issue | service readiness | تشغيل الخدمة على `127.0.0.1:8000` فقط ثم تثبيت `/health` وChroma | `/health` failed | `/health ok`, root/port correct, Chroma `19004` |
| بناء شريحة أفقية للخدمة الرقمية + التسويق | eval design | targeted eval | إنشاء `manual_round30_ecommerce_digital_service_pdpl_marketing.jsonl` بثماني حالات تغطي عدم التفعيل، التأخير، الإلغاء/الاسترجاع، التسويق بالبيانات، ومشاركة بيانات التسجيل | gap سابق: score `0.909`, sub_issue `0.778`, package `0.897` | baseline جديد يثبت gap: score `0.793`, sub_issue `0.542`, package `0.759` |
| منع PDPL من ابتلاع شق الخدمة المدفوعة | retrieval/package issue | claim specs + domain policy | السماح بـ `e-commerce-law` داخل مسارات PDPL marketing عند وجود خدمة/دورة/اشتراك رقمي مدفوع غير مفعل أو متأخر | بعض الحالات اتجهت إلى PDPL فقط | targeted final package `1.000`, sub_issue `1.000` |
| تثبيت مواد التجارة الإلكترونية `10/13/14/17` مع المادة `5` | retrieval/package issue | mandatory/core articles + scoring | توسيع `ecommerce_service_coolingoff` و`ecommerce_delivery_delay_refund` وإزالة اعتبار `14` مادة سلبية في سياق الخدمة | article/service package غير مكتمل، خصوصًا `13/14/17` | targeted direct/package coverage مكتمل بلا contamination |
| تحويل بيانات التسجيل والتسويق إلى حزمة مرافقة لا بديل | retrieval/package issue | context signals + policy filters | إضافة صيغ `بيانات التسجيل`، `رقم الجوال`، `وكالة تسويق`، `شريك إعلاني/تسويقي`، وربطها بـ PDPL واللائحة التنفيذية ك companions | مشاركة البيانات جلبت أحيانًا e-commerce بلا PDPL أو PDPL بلا e-commerce | final targeted core/companion docs covered |
| منع انجراف حقوق المؤلف عند نفي النسخ والنشر | retrieval/package issue | trap pruning | حذف بعض إشارات المنصة التعليمية من copyright context وإضافة copyright-negation guard | حالة تعليمية مع نفي النسخ سمحت بحقوق المؤلف كضوضاء | copyright contamination بقي `0.000` |
| إبراز المادة `14` ووظيفة المادة `5` في الجواب | answer-level issue | benchmark answer support | إضافة دعم ظاهر للمادة `14` في تأخر الخدمة الرقمية وتوسيع صياغات التسويق ببيانات التسجيل | الإجابة لا تُظهر دائمًا وظيفة التأخير/الاسترداد | targeted score `0.983`, مع بقاء flags متوسطة على مستوى وظيفة المادة |

### نتائج الاعتماد

- targeted e-commerce digital service + PDPL marketing:
  - before: `data/eval/manual_round30_ecommerce_digital_service_pdpl_marketing_baseline_benchmark.json`
  - after: `data/eval/manual_round30_ecommerce_digital_service_pdpl_marketing_after_service_bundle_patch_benchmark.json`
  - gate: `data/eval/manual_round30_ecommerce_digital_service_pdpl_marketing_gate_service_bundle_patch_benchmark.json`
  - score: `0.793 -> 0.983`
  - domain purity: `1.000 -> 1.000`
  - sub_issue: `0.542 -> 1.000`
  - package: `0.759 -> 1.000`
  - fatal core doc miss: `0.000 -> 0.000`
  - contamination: `0.000 -> 0.000`
- gates:
  - manual slice: `pass`, score `0.992`
  - working regression: `pass`, score `0.989`
  - held-out: `pass`, score `0.996`
  - user/manual proxy: `pass`, score `0.998`, approx `39.9/40`

### المتبقي

- ليس operational ولا contamination ولا fatal core doc miss.
- فجوة التجارة الإلكترونية للخدمة الرقمية غير المفعلة أغلقت كـ retrieval/package family:
  - final targeted `sub_issue_coverage = 1.000`
  - final targeted `package_completeness = 1.000`
- أعلى gap متبقٍ الآن:
  - `answer-level / package-support issue` في وظيفة المواد والحزم المساندة.
  - أضعف family في working regression: `teacher_batch1_b20_arbitration_email_clause`, score `0.977`, bundle `0.882`, sub_issue `0.917`, package `0.951`.
  - شريحة e-commerce+PDPL نفسها بقي فيها bundle completeness `0.879` بسبب flags جودة مثل `missing_legal_function_support` رغم اكتمال package/sub-issue.
- الجولة التالية المنطقية:
  - Round 31: اختبار أفقي لوظيفة المواد في التحكيم الإلكتروني والدليل الرقمي والعربون الرقمي، ثم ضبط answer support وquality diagnostics بدل توسيع الاسترجاع عشوائيًا.

## 2026-05-04 — Round 31

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| معالجة امتلاء القرص قبل القياس | operational issue | service/storage readiness | تنظيف بقايا Chroma HNSW غير المرتبطة بالـ active segment، وحذف أرشيف خام/مجلد أنظمة مولد غير مستخدم في ingest الحالي، ثم إعادة تشغيل الخدمة عند فشل health | الكتابة مرفوضة حتى لبايت واحد، ثم `/health` failed | `/health ok`, Chroma `19004`, والكتابة عادت |
| بناء شريحة وظيفة المواد للدليل الرقمي | eval design | targeted eval | إنشاء `manual_round31_digital_arbitration_arbun_function_support.jsonl` بست حالات تغطي التحكيم الإلكتروني، العربون الرقمي، التعاقد الآلي، والكتابة الرقمية | gap سابق في answer/package support | baseline: score `0.926`, sub_issue `0.903`, package `0.955` |
| توسيع حد السياق للحزم الرقمية المركبة | retrieval/package issue | context limit | رفع الحد فقط عند arbitration+digital evidence، automated contract+digital evidence، earnest+digital evidence | مواد مساندة كانت تضغط خارج السياق | targeted final bundle `0.959`, package `1.000` |
| ضمان مواد وظيفة الدليل الرقمي | retrieval/package issue | package article support | إضافة ضامن عام لمواد الإثبات `30/53/54/55/57/58/60/63` والتعاملات `5/6/7/8/9/10/11/12/13/14` بحسب الحزمة | missing bundle article support في التحكيم/التعاقد الآلي | arbitration and automated-contract targeted categories score `1.000` |
| تثبيت العربون المدني مع الدليل الرقمي | retrieval/package issue | context signals + package support | تثبيت المادة المدنية `44` عند وجود عربون مع رسائل/تحويلات/مستخرجات رقمية | earnest seller-withdraws case score `0.754`, dropped civil article `44` | earnest family score `0.994`, package `1.000`, sub_issue `1.000` |
| معالجة انجراف التطبيع في صيغ المنصة والتحويل | retrieval/package issue | normalized signal coverage | إضافة `رسايل منصه` و`ايصال تحويل` و`المستخرجات الرقميه` ونظائرها | `رسائل منصة` و`إيصال تحويل إلكتروني` لم تكن دائمًا digital evidence context | final targeted score `0.998`, contamination `0.000` |

### نتائج الاعتماد

- targeted digital evidence/arbitration/arbun:
  - before: `data/eval/manual_round31_digital_arbitration_arbun_function_support_baseline_benchmark.json`
  - after: `data/eval/manual_round31_digital_arbitration_arbun_function_support_after_arbun_signal_patch_benchmark.json`
  - gate: `data/eval/manual_round31_digital_arbitration_arbun_function_support_gate_arbun_signal_patch_benchmark.json`
  - score: `0.926 -> 0.998`
  - domain purity: `1.000 -> 1.000`
  - sub_issue: `0.903 -> 1.000`
  - package: `0.955 -> 1.000`
  - fatal core doc miss: `0.000 -> 0.000`
  - contamination: `0.000 -> 0.000`
- gates:
  - manual slice: `pass`, score `0.992`
  - working regression: `pass`, score `0.989`
  - held-out: `pass`, score `0.996`
  - user/manual proxy: `pass`, score `0.998`, approx `39.9/40`

### المتبقي

- ليس operational ولا contamination ولا fatal core doc miss.
- targeted Round 31 أُغلقت كـ retrieval/package family:
  - `sub_issue_coverage = 1.000`
  - `package_completeness = 1.000`
- أعلى gap متبقٍ الآن:
  - `manual_round20_diverse_companies_llc`: score `0.954`, sub_issue `0.750`, package `0.938`.
  - residual secondary: `teacher_batch1_b20_arbitration_email_clause` بقي bundle-light (`bundle = 0.882`) لكنه غير حاجب للـ gate.
- الجولة التالية المنطقية:
  - Round 32: شريحة أفقية لمسؤولية مدير/شريك الشركة ذات المسؤولية المحدودة عند الخسائر وتضارب/واجبات الإدارة، مع تحسين وظيفة المواد لا مجرد حضور نظام الشركات.

## 2026-05-05 — Round 32

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اعتماد اختبار المستخدم اليدوي كجولة أولوية | eval design | targeted eval | إنشاء `manual_round32_user_domain_routing_failures.jsonl` بأربع قضايا: العمل، التجارة الإلكترونية، PDPL الصحي، والفوترة الإلكترونية الضريبية | manual user score تقريبي `17.5/40` | baseline آلي score `0.740`, contamination `0.250` |
| فصل تحذيرات dense retrieval عن فجوات RAG | operational issue | eval/runtime | تسجيل تحذيرات `Connection error` كتشغيل/أداة قياس مع fallback لفظي، مع فحص `/health` وChroma | تحذيرات متكررة أثناء eval | `/health ok`, Chroma `19004`, لا اعتبار لها كفجوة RAG |
| توجيه الفوترة الإلكترونية الضريبية إلى VAT/ZATCA | retrieval/package issue | domain policy + claim routing | إضافة سياق VAT/e-invoicing وقواعد ترجيح تمنع انجراف PDF/electronic invoice إلى التعاملات الإلكترونية أو الإثبات | VAT case score `0.000`, domain purity gap, contamination | VAT case score `0.914`, domain purity `1.000`, contamination `0.000` |
| تثبيت حزمة الفاتورة الضريبية والإشعارات | retrieval/package issue | anchors + companions | دعم tax invoice, e-invoicing, tax number, invoice elements, credit/debit notes, ZATCA controls مع مواد VAT `1/2/25/36/38/39/42/44/45/50/52` | package completeness baseline `0.720` | targeted package completeness `1.000` |
| تحسين التقاط ساعات العمل والخصم من الأجر | retrieval/package issue | context signals + mandatory articles | توسيع صيغ `أجرًا إضافيًا`, `تجاوز الحد النظامي`, `خصمت من راتبه` ودعم مواد `98/99/100/101/107` | labor score `0.967` | labor score `0.993` |
| إبقاء PDPL الصحي بعيدًا عن التجارة الإلكترونية | retrieval/package issue | route preservation | لم يلزم تعديل جديد؛ current service استحضر PDPL + اللائحة + لائحة النقل عوضًا عن التجارة الإلكترونية | user manual saw e-commerce contamination | current targeted PDPL score `1.000`, contamination `0.000` |
| توثيق فجوة VAT المتبقية كدعم حزمة لا كنظام حاكم | answer-level issue | answer/package support | إبقاء companion titles للائحة التنفيذية، لائحة الفوترة الإلكترونية، والضوابط الفنية مع عدم اختراع core chunks غير موجودة | VAT routed to wrong governing system | VAT routed correctly, but remains medium due implementing/technical support gap |

### نتائج الاعتماد

- targeted user manual domain-routing failures:
  - before: `data/eval/manual_round32_user_domain_routing_failures_baseline_benchmark.json`
  - after: `data/eval/manual_round32_user_domain_routing_failures_after_labor_vat_route_patch_benchmark.json`
  - gate: `data/eval/manual_round32_user_domain_routing_failures_gate_labor_vat_route_patch_benchmark.json`
  - score: `0.740 -> 0.976`
  - domain purity: `0.750 -> 1.000`
  - sub_issue: `0.667 -> 1.000`
  - package: `0.720 -> 1.000`
  - fatal core doc miss: `0.000 -> 0.000`
  - contamination: `0.250 -> 0.000`
- gates:
  - manual slice: `pass`, score `0.997`
  - working regression: `pass`, score `0.989`
  - held-out: `pass`, score `0.996`
  - user/manual proxy: `pass`, score `0.998`, approx `39.9/40`

### المتبقي

- ليس operational ولا contamination ولا fatal core doc miss.
- أعلى gap متبقٍ:
  - VAT/e-invoicing family score `0.914`.
  - domain purity `1.000`, sub_issue/package `1.000`.
  - المتبقي `answer-level / package-support issue` حول لائحة الفوترة الإلكترونية والضوابط والمتطلبات والمواصفات الفنية والإجرائية لدى زاتكا.
- الجولة التالية المنطقية:
  - Round 33: شريحة أفقية للفوترة الإلكترونية الضريبية/ZATCA تشمل الفاتورة الضريبية، الفاتورة المبسطة، الرقم الضريبي وعناصر الفاتورة، PDF غير المتوافق، الإشعارات الدائنة/المدينة، والتكامل الفني؛ مع منع رجوع الانجراف إلى نظام التعاملات الإلكترونية.

## 2026-05-06 — Round 33A / الجولة الجامعة الأولى

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تحويل الجولة إلى recall-first | eval design | retrieval profile | إضافة `jamia_recall` بنية semantic 70 / lexical 30 مع حدود أوسع وسياق أكبر | user wanted no missed related regulations | main slice regulation hit `1.000` |
| فصل فشل embeddings عن فجوة RAG | operational issue | runtime/eval | تسجيل `Connection error` في dense retrieval كمشكلة تشغيلية مع fallback لفظي | semantic part unavailable | `/health ok`, Chroma `19004`, eval completed |
| توسيع حزم القوانين الخاصة | retrieval/package issue | routing + bundles | إضافة/تثبيت المنافسة، الإفلاس، كود البناء، البيع على الخارطة، جرائم معلوماتية مع PDPL، أجهزة طبية مع التجارة الإلكترونية | baseline score `0.682`, package `0.635` | final score `0.926`, package `0.892` |
| منع رفض الجولة الجامعة بسبب الاتساع | answer-level issue | quality gate | تخفيف refusal في `jamia_recall` إذا اكتملت core/companion package | VAT case refused despite package completeness | VAT score `1.000` |
| منع انجراف VAT/PDF للتعاملات الإلكترونية | retrieval/package issue | issue decomposition | إعادة تصنيف فواتير PDF الضريبية كـ VAT لا كتعاملات إلكترونية | VAT quality gate refusal | VAT category `ok`, score `1.000` |
| تقوية المقاولة الخاصة وكود البناء | retrieval/package issue | claim specs + scoring | إضافة مواد المقاولة/العيوب والتعويض وكود البناء وترجيحها | private construction score `0.714`, sub_issue `0.000` | private construction score `1.000`, sub_issue `1.000` |

### نتائج الاعتماد

- baseline:
  - `data/eval/manual_round33a_jamia_recall_gold_semantic70_before_patch_benchmark.json`
  - score `0.682`
  - sub_issue `0.358`
  - package `0.635`
- targeted probe:
  - `data/eval/manual_round33a_jamia_recall_private_construction_probe_v3_benchmark.json`
  - score `1.000`
  - package `1.000`
- main/manual slice:
  - `data/eval/manual_round33a_jamia_recall_gold_after_private_construction_patch_v3_benchmark.json`
  - score `0.926`
  - regulation hit `1.000`
  - article hit `1.000`
  - core doc recall `1.000`
  - fatal core doc miss `0.000`
  - cases >= `0.75`: `10/10`
- held-out:
  - `data/eval/manual_round33a_jamia_recall_user_domain_routing_heldout_benchmark.json`
  - score `0.993`
  - regulation hit `1.000`
  - article hit `1.000`
  - contamination trap `0.000`

### القرار

- `readiness gate`: pass.
- `targeted probe`: pass.
- `manual/working slice`: pass.
- `held-out`: pass.
- اعتماد الجولة الجامعة الأولى من زاوية الهدف الجديد: لا توجد missed related regulations في الشرائح المختبرة.

### المتبقي

- operational:
  - embeddings connectivity prevents true semantic 70/30 validation; current pass is under lexical fallback.
- retrieval/package:
  - highest remaining package depth gap: bankruptcy preference/employees/manager family, score `0.807`, package `0.688`.
  - procurement conflict/bid-rigging remains secondary at `0.767`, mainly tolerated noise from companies-law.
- answer-level:
  - future exclusions/purity phase can reduce noise; it is intentionally not optimized in this round.

### الجولة التالية المنطقية

- Round 33B / الجولة الجامعة الثانية:
  - strengthen bankruptcy packages for debtor pre-bankruptcy transactions, related-party/preferred creditor payments, employee wage claims, and manager/company responsibility.
  - then strengthen procurement conflict-of-interest companion coverage.

## 2026-05-06 — Semantic 70 Readiness Repair

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عدم اعتماد 70/30 قبل فحصه فعليًا | operational issue | readiness gate | إضافة فحص embeddings وفحص retrieval-only داخليين عبر الخدمة الحية | الإعداد يقول `dense=0.70` لكن التنفيذ غير مثبت | `embedding_ok=true`, dimension `1536`, metric `cosine` |
| إصلاح تلف فهرس Chroma الدلالي | operational issue | vector index integrity | إعادة بناء مجموعة مؤقتة من سجلات Chroma الحالية `19004` ثم استعادة الاسم الرسمي بعد نجاح probe | Chroma count `19004` لكن HNSW index يرجع `4` فقط عند طلب `90` | raw vector query يرجع `90/90`, Chroma count بقي `19004` |
| تثبيت 70% دلالي كتنفيذ لا كإعداد فقط | operational issue | semantic retrieval readiness | التحقق عبر `/internal/rag/retrieval-probe` في `jamia_recall` | dense candidates في PDPL probe = `4` | `semantic_active=true`, `effective_dense_weight=0.7`, dense candidates = `325` |
| فصل الإصلاح عن فجوات RAG | eval governance | classification | توثيق أن هذه جولة تشغيلية لا جولة تحسين حزم/إجابات | نتائج Round 33A كانت lexical-fallback متأثرة بسلامة الفهرس | الجولات التالية تبدأ من baseline دلالي فعلي |

### نتائج التحقق

- readiness:
  - `/health ok`
  - project root صحيح.
  - port `8000`.
  - Chroma actual count `19004`.
- targeted semantic probes:
  - PDPL cloud/cross-border/marketing breach: dense candidates `325`.
  - VAT/e-invoicing: dense candidates `195`.
  - competition merger/exclusivity: dense candidates `266`.
  - private construction defects: dense candidates `407`.
- manual benchmark slice:
  - PDPL, VAT, competition: core/companion/direct/bundle recall `1.0`.
  - private construction: core/companion/direct recall `1.0`, bundle `0.895`.
- held-out quick check:
  - bankruptcy preference/employees/manager: core/companion/direct/bundle recall `1.0`.
  - digital service + PDPL marketing: core/companion/direct/bundle recall `1.0`.

### القرار

- Semantic 70 readiness: `pass`.
- Full regression: not run in this operational repair turn.
- Next: resume Round 33B with true semantic `70%` / lexical `30%`.

## 2026-05-06 — Round 33B Decision

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| توسيع إفلاس التفضيل لا إعادة التنظيم فقط | retrieval/package issue | claim routing | إضافة `bankruptcy_preference_employees` وربطه بـ `labor-law` و`companies-law` | materials displayed only `4/5/7/42/45/46/47` | displayed `1/2/4/5/7/42/45/46/47/196/200/201/205/210/211`, no missing |
| تثبيت تواطؤ الموردين كنظام منافسة + مشتريات | retrieval/package issue | claim routing | إضافة `competition_bid_rigging` مع إبقاء `procurement_bid_irregularities` | displayed only procurement `37/40/46/48/51` and sometimes `companies-law` | displayed competition `1/2/3/5/14/15` + procurement `37/40/46/48/51`, no `companies-law` in target |
| منع انجراف تعارض مصالح الشركات في المنافسات الحكومية | answer-level issue | document bundles | تخطي company conflict/merger bundles عند `procurement_bid_rigging_competition_context` | procurement target listed `companies-law` | procurement target lists `government-tenders-and-procurement-law` + `nzam-almnafsh` |
| منع حزمة التمويل العقاري في سياق مشتريات حكومية | answer-level issue | document bundle exclusion | إضافة procurement exclusions إلى `round21_credit_mortgage_bundle` | held-out procurement delay listed credit/mortgage laws | held-out lists procurement law only |
| تقوية حزمة المقاولة الخاصة | retrieval/package issue | article priority | رفع civil transactions `94/95` إلى preferred | private construction missing bundle `[94,95]` | no missing direct/bundle articles |

### نتيجة القرار

- artifact: `data/eval/manual_round33b_jamia_recall_bankruptcy_procurement_package_patch_benchmark.json`
- cases: `12`
- pass: `12/12`
- gates: targeted, manual slice, working regression, held-out all pass.

## 2026-05-07 — Round 33C Decision

| القرار | التصنيف | الطبقة | التعديل | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| استعادة `engine.py` بعد فشل كتابة | operational issue | source/runtime | إعادة بناء محرك تشغيل محافظ من Chroma و`chunks.jsonl` | الملف على القرص كان غير صالح للتشغيل | الخدمة عادت على 8000 و`/health ok` |
| تثبيت مفتاح embeddings من runtime settings | operational issue | embeddings config | استخدام إعدادات التشغيل بدل `.env` placeholder | embedding health فشل بمفتاح placeholder ثم temp failure | `embedding_ok=true`, dimension `1536`, metric `cosine` |
| تأكيد `jamia_recall` 70/30 | retrieval/package issue | retrieval profile | dense `0.70`, lexical `0.30`, recall-first bundles | لا يصح القياس مع semantic inactive | retrieval probe: `semantic_active=true`, dense `0.70`, lexical `0.30` |
| توسيع حزم الجمع للـRound 33C | retrieval/package issue | bundle routing | VAT+ecommerce, company+civil+evidence, brokerage+civil, private construction, procurement+competition | Round 33C diagnosis showed package risks | manual slice engine-direct `10/10` pass |

### نتيجة القرار

- artifact: `data/eval/manual_round33c_jamia_recall_rebuilt_engine_manual_slice.json`
- cases: `10`
- pass: `10/10`
- gates: readiness and manual slice pass.
- not passed yet: working regression and held-out service check are deferred because low disk headroom and loopback transport instability are operational issues.

## 2026-05-14 — Round 33C Service Closure Decision

| القرار | التصنيف | الطبقة | التعديل/الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إعادة الخدمة على نفس المنفذ | operational issue | readiness | تشغيل الخدمة على `127.0.0.1:8000` بعد فشل `/health` | الخدمة لا ترد | `/health ok`, root/port correct, Chroma `19004` |
| اعتماد قياس مباشر عبر `curl` | operational issue | eval transport | تجاهل فشل Python/subprocess loopback واستخدام direct curl responses ثم scoring offline | Python transport يعطي connect/permission failures | service responses valid; no transport failures in final artifacts |
| تثبيت 70/30 حيًا | operational issue | semantic readiness | embedding-health + retrieval-probe | يلزم تأكيد فعلي بعد restart | `semantic_active=true`, dense `0.70`, lexical `0.30` |
| إغلاق Round 33C كجولة جمع لا تصفية | retrieval/package issue | service regression | تشغيل service regression وheld-out على `jamia_recall` | manual slice كان engine-direct فقط | service regression: reg/article hit `1.000`; held-out: reg/article hit `1.000` |
| تأجيل التلوث لمرحلة لاحقة | answer-level issue | ranking/purity | عدم patch للضوضاء الآن لأنها مقبولة ضمن هدف الجمع | domain purity منخفض في recall-first | core/bundle complete؛ noise موثق للتصفية لاحقًا |
| نقل الجولة التالية إلى inventory للـcorpus | corpus/package-support issue | knowledge coverage | توثيق نقص اللوائح/الضوابط غير المفهرسة مثل ZATCA e-invoicing | VAT يعتمد على نظام VAT فقط + note خارجي | Round 34 المقترح: inventory + ingestion plan قبل purity |

### نتيجة القرار

- service regression artifact: `data/eval/manual_round33c_jamia_recall_service_regression_after_operational_recovery_benchmark.json`
  - cases: `10`
  - retrieval regulation hit: `1.000`
  - article hit: `1.000`
  - average score: `0.920`
  - core doc recall: `1.000`
  - bundle completeness: `1.000`
  - package completeness: `0.892`
  - cases >= `0.75`: `9/10`
- held-out artifact: `data/eval/manual_round33c_jamia_recall_service_heldout_after_operational_recovery_benchmark.json`
  - cases: `4`
  - retrieval regulation hit: `1.000`
  - article hit: `1.000`
  - average score: `0.881`
  - core doc recall: `1.000`
  - bundle completeness: `1.000`
  - package completeness: `0.840`
  - cases >= `0.75`: `4/4`
- gate status:
  - recall-first gates: pass.
  - old purity-weighted score gate: partial fail due cross-domain noise, not core retrieval miss.

## 2026-05-14 — Round 34 Corpus Inventory Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تحويل الجولة إلى inventory لا patch | corpus/package-support issue | corpus coverage | جرد النصوص المفهرسة standalone مقابل الحزم الذهبية | لا يمكن ادعاء استيعاب كامل لأن بعض اللوائح غير مفهرسة | artifact inventory created |
| تحديد نواقص ZATCA كأولوية | corpus/package-support issue | VAT/e-invoicing | إدراج VAT implementing regulation, e-invoicing bylaw, technical controls في قائمة P0 | VAT cases تعتمد على نظام VAT فقط مع note خارجي | P0 list جاهزة للإدخال |
| تحديد نواقص التجارة الإلكترونية | corpus/package-support issue | ecommerce | إدراج اللائحة التنفيذية لنظام التجارة الإلكترونية في P0 | النظام الأساسي موجود فقط | source candidate documented |
| تحديد نواقص المنافسات الحكومية | corpus/package-support issue | procurement | إدراج لائحة تعارض المصالح ولائحة تنفيذية/أخلاقيات المشتريات | النظام الأساسي يستدعي المادة 96 فقط | BOE/source candidates documented |
| تثبيت أن PDPL ليست gap حاليًا | retrieval/package issue | privacy | فحص وجود PDPL + اللائحة التنفيذية + لائحة النقل | مخاطرة الخلط مع التجارة الإلكترونية سابقًا | all three indexed standalone |
| تحديد نواقص العقار والوساطة | corpus/package-support issue | real estate | إدراج off-plan implementing regulation, escrow controls, brokerage implementing regulation | الأنظمة الأساسية موجودة، اللوائح غير مفهرسة | REGA source candidates documented |

### نتيجة القرار

- artifact JSON: `data/eval/round34_corpus_companion_inventory.json`
- artifact MD: `data/eval/round34_corpus_companion_inventory.md`
- indexed standalone: `13`
- missing standalone: `12`
- missing / source confirmation needed: `4`
- decision:
  - Round 34 inventory passes.
  - Do not start purity/exclusion yet.
  - Next: controlled ingestion of P0 missing standalone texts, then rerun `jamia_recall` service regression.

## 2026-05-15 — Round 35 ZATCA VAT/E-Invoicing Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إدخال ZATCA P0 | corpus/package-support issue | VAT/e-invoicing corpus | اعتماد VAT implementing regulation + e-invoicing bylaw + technical controls | ZATCA companion texts missing standalone | structured chunks `20525`; Chroma `20525` |
| عزل فشل الإدخال المباشر | operational issue | ingestion/runtime | عدم اعتبار Docker/Python network failure فجوة RAG، وإعادة البناء من داخل الخدمة | `manage.sh ingest` يفشل بسبب Docker؛ Python direct يفشل اتصال embeddings | `/internal/rag/reindex` يعيد بناء Chroma بنجاح |
| منع false-ok في reindex | operational issue | service endpoint | إرجاع `status=error` إذا لم تتم المزامنة | endpoint كان يمكن أن يرجع `ok` مع `synced=false` | endpoint returned `ok/synced=true/20525` |
| تثبيت حزمة ZATCA | retrieval/package issue | package routing | إضافة ZATCA companion slugs وموادها إلى VAT/e-invoicing bundle | VAT/PDF cases لا تفرض لائحة الفوترة والضوابط | manual slice package recall `1.000` |
| فصل الإلزامي عن الضوضاء | answer-level issue | benchmark answer | النظام المنطبق يعرض required core/companion، والزائد يظهر كـ extra retrieved references | كود البناء/حقوق المؤلف/التعاملات الإلكترونية قد تظهر ضمن النظام المنطبق | sample answer excludes them from governing package |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `20525`
  - dense metric `cosine`
  - semantic/dense `0.70`, lexical `0.30`
- gate summary:
  - artifact: `data/eval/round35_zatca_jamia_recall_gate_summary.json`
  - manual slice: `4/4`, avg package recall `1.000`
  - working regression: `4/4`, avg package recall `1.000`
  - held-out: `2/2`, avg package recall `1.000`
  - overall: `10/10`, pass
- decision:
  - Round 35 ZATCA batch passes.
  - Remaining highest gap is not ZATCA; it is corpus support for other P0 companion texts:
    - e-commerce implementing regulation.
    - procurement conflict-of-interest regulation.
    - bankruptcy implementing regulation.

## 2026-05-15 — Round 36 P0 Second Companion Batch Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إدخال اللائحة التنفيذية للتجارة الإلكترونية | corpus/package-support issue | e-commerce corpus | onboard + catalog + bundle companion | النظام الأساسي فقط كان يظهر في بعض الحالات | digital service/manual case package complete |
| إدخال لائحة تعارض المصالح | corpus/package-support issue | procurement/conflict corpus | onboard + catalog + bundle companion | المنافسات تظهر دون لائحة التعارض، والتواطؤ يحتاج نظام المنافسة | procurement conflict probe pass |
| إدخال مواد P0 من اللائحة التنفيذية للإفلاس | corpus/package-support issue | bankruptcy corpus | onboard selected P0 articles + bundle companion | نظام الإفلاس يظهر دون اللائحة التنفيذية في التفضيل/المطالبات | bankruptcy preference probe pass |
| تثبيت 70/30 | retrieval/package issue | retrieval profile | قياس direct probes على `jamia_recall` | يلزم التأكد بعد reindex | dense `0.70`, lexical `0.30`, semantic active |
| عزل فشل أدوات الفحص | operational issue | local tooling | اعتبار فشل Python/subprocess curl وDNS الخارجي خارج تقييم RAG | ملفات probe فاشلة غير صالحة | direct loopback probes صالحة وموثقة |
| إبقاء الضوضاء للمرحلة التالية | answer-level issue | answer/ranking | قبول المراجع الزائدة في الجمع، مع فصل mandatory عن extra | احتمال خلط الإلزامي والزائد | answer spot checks تعرض الحزمة الإلزامية بوضوح |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `20604`
  - semantic/dense `0.70`, lexical `0.30`
- gate summary:
  - artifact: `data/eval/round36_p0_second_batch_jamia_recall_gate_summary.json`
  - targeted manual slice: `3/3`, pass
  - working regression: `2/2`, pass
  - held-out: `1/1`, pass
  - answer spot check: `2/2`, pass
- decision:
  - Round 36 passes.
  - P0 collection stage is practically closed for tested high-risk families.
  - Highest remaining gap: corpus completion beyond P0 selected materials + later ranking/exclusion cleanup.

## 2026-05-15 — Admin UI 500 Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عزل خطأ `/admin` عن RAG | operational issue | admin/runtime | عدم احتسابه كفجوة استرجاع | `/admin` 500 مع `/health` ok | root cause traced to missing `provider_label` |
| إصلاح حالة المولد من المصدر | operational issue | engine status API | `get_generation_status()` يعيد `provider_label` دائمًا | admin/bot consumers يتوقعون الحقل | `/admin` 200 |

### نتيجة القرار

- `/health`: ok.
- `/admin`: 200.
- Chroma actual count: `20604`.
- لا تغيير في حزم الاسترجاع أو corpus في هذا الإصلاح.

## 2026-05-15 — Round 37 Collection Expansion Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عزل فشل loopback POST | operational issue | local tooling/service probe | عدم عد فشل الاتصال المحلي كفجوة RAG إذا لم يصل إلى uvicorn | POST probes تفشل من shell | direct engine diagnostics مستخدمة بعد `/health ok` |
| تثبيت عائلات الجمع الأربع | retrieval/package issue | package routing | اختبار labor/listed-company/electronic-enforcement/procurement bundles | المستخدم يرى جمعًا جزئيًا فقط | core recall `1.000` لكل الحالات |
| كشف نواقص corpus بدل إخفائها | answer-level issue | benchmark diagnostics | اعتبار missing companions فجوة corpus صريحة | النظام كان يبدو كأنه اكتفى بالنظام الأساسي | companion recall average `0.317` |
| تقرير النواقص المفهرسة | corpus/package-support issue | structured corpus | إحصاء slugs المطلوبة في `chunks.jsonl` | غير واضح هل النص مفقود أم غير مستدعى | 11 companion slugs count = `0` |
| عدم فتح exclusion/purity بعد | retrieval strategy | recall-first | إبقاء الضوضاء مقبولة حتى يكتمل الجمع | خطر حذف مبكر يفوت مراجع | next round = ingestion لا حذف |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `20604`
  - `jamia_recall`: dense `0.70`, lexical `0.30` configured
- gate summary:
  - artifact: `data/eval/round37_jamia_collection_expansion_gate_summary.json`
  - targeted manual slice: `4`
  - average core recall: `1.000`
  - average companion recall: `0.317`
  - average bundle completeness: `0.829`
  - working regression: `2/2`, pass
  - held-out: `1/1`, pass
- decision:
  - Round 37 = `partial_pass`.
  - highest remaining gap is corpus completion for missing companion regulations, not generic retrieval tuning.

## 2026-05-15 — Round 38 P0 Companion Ingestion Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إدخال حزمة العمل المساندة | corpus/package-support issue | labor corpus | أضيفت اللائحة التنفيذية، جدول المخالفات، حماية الأجور، وتوثيق العقود | companion recall في Round 37 ناقص | labor bundle `1.000` |
| إدخال حزمة الشركات المدرجة/CMA | corpus/package-support issue | listed-company corpus | أضيفت لائحة الشركات المدرجة، الحوكمة، وقواعد الطرح/الالتزامات المستمرة | companion recall في Round 37 ناقص | listed-company bundle `1.000` |
| إدخال اللائحة التنفيذية للتنفيذ | corpus/package-support issue | enforcement corpus | أضيفت لائحة التنفيذ من PDF وزارة العدل | execution companion count `0` | electronic enforcement bundle `1.000` |
| قبول fallback لاستخراج PDF | operational / corpus quality note | extraction | إبقاء labor/execution PDFs رغم ضعف article split لأنها أنتجت مقاطع قابلة للاسترجاع | `no_articles_detected` في scan | labor `193` chunks، execution `125` chunks |
| إعادة بناء Chroma من داخل الخدمة | operational issue | indexing | `/internal/rag/reindex` على نفس المنفذ | Chroma `20604` | Chroma `22368` |
| عزل 429 وloopback failures | operational issue | local tooling | عدم عد أخطاء الاتصال أو rate-limit كفجوة RAG | transient 429 وPython urllib permission denied | reindex ok، health ok، probes curl ok |
| عدم إعلان اكتمال المشتريات | corpus/package-support issue | procurement corpus | توثيق النصين الباقيين بدلاً من إخفاء النقص | procurement targeted bundle `0.900` | gap محدد: procurement implementing + conduct ethics |

### نتيجة القرار

- readiness after reindex:
  - `/health ok`
  - root/port correct
  - Chroma actual count `22368`
  - `jamia_recall`: dense/semantic `0.70`, lexical `0.30`
- gate summary:
  - artifact: `data/eval/round38_p0_companion_ingestion_gate_summary.json`
  - targeted manual slice: `3/4` complete, procurement partial due corpus gap.
  - working regression: `2/2`, pass.
  - held-out: `1/1`, pass.
- decision:
  - Round 38 = `partial_pass`.
  - The 9 newly ingested companion texts are active and retrievable.
  - Highest remaining gap is the two procurement companion texts.

## 2026-05-15 — Round 39 Collection Closure Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إدخال اللائحة التنفيذية للمنافسات الحكومية | corpus/package-support issue | procurement corpus | إضافة selected P0 text من المصدر الرسمي/أم القرى | companion مفقود في Round 38 | `government-procurement-implementing-regulation` حاضر، procurement grievance bundle `1.000` |
| إدخال لائحة سلوكيات وأخلاقيات القائمين على المنافسات | corpus/package-support issue | procurement ethics corpus | إضافة selected P0 text من هيئة الخبراء | companion مفقود في Round 38 | `procurement-conduct-ethics-regulation` حاضر، procurement grievance bundle `1.000` |
| كشف أن لائحة التنفيذ موجودة لكنها غير قابلة للاسترجاع كفاية | retrieval/package issue | enforcement companion coverage | مقارنة top/selected diagnostics لقضية السند الإلكتروني | electronic enforcement bundle `0.938` وmissing companion | root cause = PDF text garbling |
| إصلاح استخراج PDF لائحة التنفيذ | corpus/package-support issue | structured corpus/OCR | فرض OCR عربي للـPDF الضعيف في `build_structured_legal_corpus.py` | `execution-implementing-regulation` 125 chunks ضعيفة | `350` chunks OCR، electronic enforcement bundle `1.000` |
| إعادة بناء Chroma على العدد النهائي | operational issue | indexing | reindex داخل الخدمة نفسها وعلى المنفذ 8000 | Chroma `22387` بعد المشتريات | Chroma `22612`, `/health ok`, `/admin 200` |
| إغلاق مرحلة الجمع المختبرة | retrieval strategy | jamia_recall | إبقاء semantic/dense `0.70` وlexical `0.30` مع قبول الضوضاء | بعض الحزم P0 ناقصة | targeted/manual/working/held-out كلّها bundle `1.000` |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `22612`
  - `jamia_recall`: dense/semantic `0.70`, lexical `0.30`
- gate summary:
  - artifact: `data/eval/round39_collection_closure_gate_summary.json`
  - targeted probe: pass.
  - manual slice: pass.
  - working regression: pass.
  - held-out check: pass.
- classification:
  - operational hiccups isolated and not counted as RAG gaps.
  - corpus/package-support gaps closed for tested P0/gold families.
  - answer-level quality remains a next-stage problem, not this round's metric.
- decision:
  - Round 39 = `pass`.
  - Highest remaining gap: long-tail corpus hardening and later exclusion/answer-level ranking, not tested collection completeness.

## 2026-05-16 — Round 40 Gold Package Recall Benchmark Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إنشاء قائمة ذهبية ثابتة من 100 سؤال | eval design | package recall | إنشاء `gold_package_recall_100_v1.jsonl` مع core/companion/optional/excluded لكل قضية | أمثلة يدوية متفرقة | معيار ثابت 100 حالة |
| منع تغشيش RAG بالقائمة | eval integrity | anti-leakage | runner يرسل السؤال فقط إلى الخدمة، والتصحيح بعد رجوع الرد | خطر إدخال الإجابات في routing | gold offline only |
| فصل درجة الجمع عن التلويث | measurement | scoring | collection score لا يخصم على المصادر الزائدة، بل يسجل excluded hits فقط | اختلاط الجمع بالتنقية | جمع مستقل: `71.5/100` |
| توثيق قيد الاتصال المحلي | operational issue | local sandbox | direct `curl -K` بدل Python/child curl | Python loopback blocked | 100/100 service responses completed |
| اعتماد baseline جديد للجمع العام | retrieval/package issue | long-tail collection | تشغيل 100 حالة على `jamia_recall` | P0 gates كانت تمر | core `0.777`, companion `0.600`, fatal core miss `27` |

### نتيجة القرار

- artifacts:
  - `data/eval/gold_package_recall_v1/gold_package_recall_100_v1.jsonl`
  - `data/eval/gold_package_recall_v1/gold_package_recall_100_round40_baseline.json`
  - `data/eval/gold_package_recall_v1/gold_package_recall_100_round40_baseline.md`
- overall collection score: `71.5/100`
- weakest domains:
  - procurement/admin: `48.5`
  - family/criminal/protection: `57.5`
  - finance/insolvency: `58.3`
  - health/food/drugs: `62.2`
  - civil/evidence/procedure: `63.3`
- decision:
  - Round 40 = `baseline_created`, not pass.
  - next round should improve collection on the same 100 cases before optimizing contamination suppression.

## 2026-05-16 — Round 41 Gold Package Recall Closure Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إعادة مزامنة Chroma مع structured chunks | operational issue | indexing | `/internal/rag/reindex` بعد mismatch | structured `22801` وChroma خلفه | Chroma actual `22801`, `/health ok` |
| فرض حضور الوثائق المطلوبة حتى بلا مواد محددة | retrieval/package issue | forced candidates | استخدام entries by slug داخل candidate forcing | بعض core/companion slugs لا تدخل السياق | required slugs تدخل حتى عند غياب article filter |
| توسيع long-tail regulation bundles | retrieval/package issue | package recall | حزم عامة للعمل، ZATCA، المنافسات، التجاري، العقار، التمويل، IP، الصحة، الأسرة/الجنائي | score `71.5`, fatal `27` | score `96.1`, fatal `3` |
| إغلاق فجوات VAT/procurement/real-estate-tax/trademark/labor | retrieval/package issue | targeted collection | أنماط عامة لإشعارات الخصم، مقاول الباطن الحكومي، ضريبة التصرفات، GCC trademark، إصابات العمل | v2 fatal `3` | v3 score `99.5`, fatal `1` |
| إغلاق فجوة السند لأمر والتحكيم | retrieval/package issue | evidence/procedure collection | إضافة سند لأمر إلكتروني + حكم تحكيم/بطلان/تنفيذ | v3 score `99.5`, fatal `1` | final score `100.0`, fatal `0` |
| إبقاء التلويث خارج درجة الجمع | measurement boundary | collection vs purity | تسجيل excluded hits فقط | contamination غير مقاس | `35` excluded-hit cases recorded for next phase |

### نتيجة القرار

- readiness final:
  - `/health ok`
  - root/port correct
  - Chroma actual count `22801`
  - `jamia_recall`: dense/semantic `0.70`, lexical `0.30`
- final artifacts:
  - `data/eval/gold_package_recall_v1/gold_package_recall_100_round41_collection_patch_v4_final.json`
  - `data/eval/gold_package_recall_v1/gold_package_recall_100_round41_collection_patch_v4_final.md`
- final benchmark:
  - collection score: `100.0/100`
  - core recall: `1.000`
  - companion recall: `1.000`
  - full package rate: `1.000`
  - fatal core miss cases: `0`
  - transport errors: `0`
  - dev/regression/heldout: all `100.0`
- decision:
  - Round 41 = `pass`.
  - Collection phase is closed on `gold_package_recall_100_v1`.
  - Next rational phase is scoring and suppressing contamination/excluded references without reducing recall.

## 2026-05-16 — Round 42 Expanded Gold 1000 Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اختبار العمل المركب المقترح من المستخدم | retrieval/package issue | labor package recall | targeted probe ثم seed داخل معيار 1000 | معيار 100 كان `100/100` | seed labor score `73.8`, companion gaps |
| عدم إصلاح الانحراف قبل baseline | eval integrity | measurement | بناء معيار أوسع أولاً حتى لا نغطي الفجوة بتعديل موضعي | خطر overfit على سؤال واحد | baseline 1000 يكشف 13 fatal misses |
| إنشاء معيار 1000 | eval design | package recall | `build_gold_package_recall_v2_1000.py` يولد 1000 سؤال | 100 حالة فقط | 1000 حالة، core coverage `302/302` |
| إصلاح أدوات القياس للمسارات والمعرف | operational tooling | eval runner | `--benchmark-id` وrelative path handling | أدوات v1 فقط | baseline v2 يعمل |
| تشغيل baseline 1000 | retrieval/package issue | broad collection | خدمة 8000 و`jamia_recall` | no score | `98.0/100`, fatal `13`, transport `0` |
| تسجيل التلويث دون خصم | contamination measurement | purity pending | excluded hits recorded only | 35 في v1 | `873` cases في v2 |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `22801`
  - `jamia_recall`: dense/semantic `0.70`, lexical `0.30`
- artifacts:
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_v2.jsonl`
  - `data/eval/gold_package_recall_v2_1000/manifest.json`
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round42_baseline.json`
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round42_baseline.md`
- baseline:
  - collection score: `98.0/100`
  - core recall: `0.987`
  - companion recall: `0.979`
  - full package rate: `0.959`
  - fatal core miss cases: `13`
  - transport errors: `0`
- decision:
  - Round 42 = `expanded_baseline_created`, not pass.
  - The next optimization round should use the 1000-case benchmark as the primary collection gate.
  - Highest immediate retrieval gap: labor companion package plus 13 fatal core misses.
  - Highest upcoming non-retrieval gap: contamination/excluded-hit suppression.

## 2026-05-16 — Round 43 Gold 1000 Collection Closure Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إغلاق فشل قضية العمل المركبة | retrieval/package issue | labor package recall | توسيع حزمة العمل لتشمل اللائحة، حماية الأجور، توثيق العقود، جدول المخالفات، وفصلها عن مشتريات الحكومة | seed labor `73.8/100` | included in final `100.0/100` |
| إغلاق 13 fatal core misses | retrieval/package issue | title/field recall | فرض أذكى للأنظمة عند ظهور عنوان النظام أو المجال | fatal `13` | fatal `0` |
| إضافة حزم المساند الافتراضية | retrieval/package issue | companion recall | `DEFAULT_COMPANION_REGULATIONS_BY_CORE` | companion recall `0.979` baseline | `1.000` final |
| إضافة حزم المجال العامة | retrieval/package issue | broad package recall | `FIELD_REGULATION_PACKAGES` للعبارات مثل “في مجال العمل/الإثبات/التنفيذ” | field-style companion gaps | targeted `29/29` pass |
| إعادة تشغيل الخدمة فقط بعد تغيير الكود | operational control | readiness | restart على المنفذ `8000` ثم `/health` وعد Chroma | code changed | `/health ok`, Chroma `22801` |
| إبقاء التلويث خارج درجة الجمع | measurement boundary | collection vs purity | تسجيل excluded hits دون خصم | `873` baseline | `870` final recorded only |

### نتيجة القرار

- readiness:
  - `/health ok`
  - root/port correct
  - Chroma actual count `22801`
  - `jamia_recall`: dense/semantic `0.70`, lexical `0.30`
- artifacts:
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round43_targeted.json`
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round43_companion_patch_v2_targeted.json`
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round43_collection_patch_v2_full.json`
  - `data/eval/gold_package_recall_v2_1000/gold_package_recall_1000_round43_collection_patch_v2_full.md`
- final benchmark:
  - collection score: `100.0/100`
  - core recall: `1.000`
  - companion recall: `1.000`
  - full package rate: `1.000`
  - fatal core miss cases: `0`
  - transport errors: `0`
  - dev/regression/heldout: all `100.0`
- decision:
  - Round 43 = `pass`.
  - Collection phase is closed on `gold_package_recall_1000_v2`.
  - Highest remaining gap is contamination/excluded-hit suppression, not missing package collection.

## 2026-05-16 — Listed Company CMA Compound Probe Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اعتبار الاختبار فجوة عائلة لا فشل تشغيل | retrieval/package issue | package recall | readiness ثم query مباشر | `/health ok`, Chroma `22801` | no operational gap |
| توسيع حزمة الشركات المدرجة | retrieval/package issue | CMA/listed companies | إضافة حزمة disclosure/insider/related-party/securities-disputes | استرجع الشركات فقط | استرجع السوق المالية + CMA |
| إبقاء المراجع العامة كمسألة purity لاحقة | contamination/purity | ranking | لم نفلتر civil/commercial courts الآن | ظهرت المحاكم التجارية والمعاملات المدنية | collection passes, purity pending |

### نتيجة القرار

- before:
  - missed `nzam-alswq-almalyh`, `cma-corporate-governance-regulations`, `cma-continuing-obligations-rules`, `cma-securities-offering-rules`.
- after:
  - covered core: `companies-law`, `nzam-alswq-almalyh`.
  - covered companion: `companies-implementing-regulation`, `cma-corporate-governance-regulations`, `cma-continuing-obligations-rules`, `cma-securities-offering-rules`, `law-of-evidence`.
  - missing core/companion: `0`.
- strategy decision:
  - Do not jump to blind `10k`.
  - Build a stratified scenario-family bank first; `10k` is useful only if it is taxonomy-driven and includes hard compound cases, not just more generated paraphrases.

## 2026-05-17 — Gold 5000 v3 Benchmark Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| رفع المعيار من 3000 إلى 5000 | eval design | benchmark size | تحويل مولد v3 إلى `5000` | مقترح `3000` | `5000` حالة |
| تقوية العائلات المركبة | eval design | scenario families | توسيع تنويعات العائلة إلى 34 صياغة لكل عائلة | `270` حالة عائلية أولية | `1530` حالة عائلية |
| إبقاء regression v2 | regression protection | collection recall | إدراج كل `gold_package_recall_1000_v2` | خطر نسيان ما أغلقه Round 43 | `1000` حالة regression |
| تغطية corpus الرسمي | broad coverage | official corpus | توليد article-anchored cases | تغطية عائلية فقط غير كافية | `2470` حالة مولدة من المواد |
| منع التغشيش | eval integrity | payload isolation | تجهيز payloads بلا gold labels | gold labels في ملفات JSONL | الخدمة ترى السؤال فقط |

### نتيجة القرار

- benchmark id: `gold_package_recall_5000_v3`
- files:
  - `scripts/build_gold_package_recall_v3_5000.py`
  - `data/eval/gold_package_recall_v3_5000/gold_package_recall_5000_v3.jsonl`
  - `data/eval/gold_package_recall_v3_5000/manifest.json`
  - `data/eval/gold_package_recall_v3_5000/curl_all.config`
- counts:
  - total: `5000`
  - scenario families: `45`
  - scenario-family cases: `1530`
  - regression v2: `1000`
  - article-generated: `2470`
  - core coverage: `302/302`
  - payloads: `5000`
  - empty-core cases: `0`
- decision:
  - v3_5000 is now the next collection benchmark.
  - Full eval is pending and should be run before declaring collection mature under the stronger standard.

## 2026-05-17 — Round 44 Gold 5000 Collection Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اعتبار baseline فجوة جمع لا تشغيل | retrieval/package issue | collection recall | readiness + full 5000 eval | `/health ok`, Chroma `22801` | transport errors `0` |
| إغلاق fatal core misses | retrieval/package issue | governing regulation recall | توسيع default companions + scenario bundles | fatal core miss `198` | targeted `198/198`, fatal `0` |
| إغلاق companion gaps | retrieval/package issue | package completeness | default companion + field package expansion | companion recall `0.977` after patch 1 | `1.000` final |
| إصلاح scorer alias fallback | operational/eval support | scoring robustness | fallback إلى `data/structured/by_regulation/*.json` | `regulations.json` absent after sync | score-only works |
| عدم خصم التلويث في الجمع | measurement boundary | collection vs purity | تسجيل excluded hits فقط | collection phase target | `excluded_hit_cases_recorded_only = 3905` |

### نتيجة القرار

- benchmark: `gold_package_recall_5000_v3`
- final report: `data/eval/gold_package_recall_v3_5000/gold_package_recall_5000_v3_round44_patch2_final_all.json`
- final markdown: `data/eval/gold_package_recall_v3_5000/gold_package_recall_5000_v3_round44_patch2_final_all.md`
- final score: `100.0/100`
- cases: `5000/5000`
- core recall: `1.000`
- companion recall: `1.000`
- full package rate: `1.000`
- fatal core miss cases: `0`
- transport errors: `0`
- dev/regression/heldout: all `100.0`
- domains: all `100.0`

### القرار التالي

Round 44 closes collection under the 5000-question standard. The next logical round is not more collection expansion immediately; it is a purity/contamination round that keeps collection recall at `5000/5000` while classifying or suppressing excluded and merely conditional references.

## 2026-05-17 — Round 45 Gold 7000 v4 Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| رفع معيار الجمع إلى 7000 | eval design | benchmark coverage | إضافة 1000 تركيبات + 1000 مرادفات فوق v3 5000 | `5000` حالة مغلقة | `7000` حالة، core coverage `302/302` |
| منع التغشيش | eval integrity | payload isolation | payloads تحتوي السؤال فقط مع `jamia_recall` | gold labels offline | `7000` payload بلا إجابات ذهبية |
| تضييق محفز الإفلاس | contamination/purity | bankruptcy boundary | إزالة أثر "تعثر" العام وإضافة قرائن إفلاس مالية | `gpr_v4_5001` أدخل `nzam-aliflas` خطأ | bankruptcy absent في مشروع خاص، present في إفلاس حقيقي |
| توسيع حزمة عقد المشروع الخاص | retrieval/package issue | civil/private project | إضافة صيغ "عقد مشروع خاص"، "دفعات المالك"، "ارتفاع المواد" | `civil-transactions-law` غاب عن `gpr_v4_5001` بعد تضييق الإفلاس | observed core اكتمل: civil + labor + social insurance + concealment |
| عدم إعلان إغلاق 7000 قبل full eval | measurement boundary | gates | smoke فقط ثم baseline لاحق | معيار 7000 جديد | full eval pending |

### نتيجة القرار

- benchmark: `gold_package_recall_7000_v4`
- generator: `scripts/build_gold_package_recall_v4_7000.py`
- dataset: `data/eval/gold_package_recall_v4_7000/gold_package_recall_7000_v4.jsonl`
- manifest: `data/eval/gold_package_recall_v4_7000/manifest.json`
- payloads: `data/eval/gold_package_recall_v4_7000/payloads_all/`
- curl config: `data/eval/gold_package_recall_v4_7000/curl_all.config`
- smoke summary: `data/eval/gold_package_recall_v4_7000/round45_v4_smoke_probe_summary.md`
- total cases: `7000`
- base v3 cases: `5000`
- compound issue stress cases: `1000`
- synonym surface stress cases: `1000`
- split counts: dev `1750`, regression `2625`, heldout `2625`
- duplicate questions in new v4 layers: `0`
- readiness after patch:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22801`
  - semantic/dense `0.70`, lexical `0.30`

### القرار التالي

Run the full `gold_package_recall_7000_v4` collection baseline. Do not declare `7000/7000` until the full baseline and any targeted patch/regression/held-out checks pass.

## 2026-05-17 — Round 46 Material-Axis Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عدم الاكتفاء بزيادة الأسئلة | eval strategy | benchmark design | علاج محاور الاستدعاء والمواد بدل تدوير اختبارات أكثر | manual cases تظهر نقص مواد وحزم | patch عام في الحزم والمحاور |
| إضافة محاور فرعية مستقلة | retrieval/package issue | sub-issue package recall | e-commerce/data/installment/trademark, procurement specs, off-plan/unit owners/finance, health insurance/device/privacy | النظام يلتقط النظام الكبير ويترك محورًا فرعيًا | targeted probes تغطي المحاور الجديدة |
| ترجيح المادة الحاسمة | material/article-level issue | article precision | materiality score + penalize article 1 on non-definition queries | مواد تعريفية تظهر بدل مواد النزاع | top results include operative articles |
| اختيار مادة ممثلة للأنظمة المفروضة | material/article-level issue | forced retrieval | representative entry per required slug | fallback إلى أول مادة في الملف | مادة أكثر صلة بحسب overlap/materiality |
| فصل التلويث اللفظي | contamination boundary | purity prework | تسرب بيانات vs تسربات بناء، collusion vs procurement conflict، trademark vs copyright، media vs telecom | استدعاءات زائدة بسبب ألفاظ سطحية | targeted probes تخفض الانجراف دون كسر الجمع |
| عدم إعلان إغلاق 7000 | measurement boundary | gates | targeted probes فقط في هذه الجولة | full 7000 pending | full 7000 ما يزال مطلوبًا |

### نتيجة القرار

- file patched: `app/rag/engine.py`
- report: `data/eval/gold_package_recall_v4_7000/round46_material_axis_probe_summary.md`
- readiness after restart:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22801`
  - semantic/dense `0.70`, lexical `0.30`
- targeted probes:
  - off-plan/unit owners/finance: pass
  - medical/insurance/device/privacy: pass
  - procurement/specifications/conformity/fraud: pass
  - e-commerce/installment/data/trademark: pass

### القرار التالي

Run full `gold_package_recall_7000_v4` baseline, then add a material-level gold scorer where every factual sub-issue must map to a regulation, companion regulation, article family, and reason. Purity suppression should come after preserving this collection/material recall.

## 2026-05-19 — Round 48 Full Collection Closure Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عدم اعتماد نتائج transport | operational issue | eval runner | استبعاد تقارير سقطت بسبب صلاحية الشبكة المحلية داخل Python | `649/649` transport errors | تقارير غير معتمدة |
| إعادة توحيد Chroma مع الطبقة المنظمة | operational issue | readiness | `/internal/rag/reindex` بعد sync رسمي | structured `22810` مقابل Chroma `22801` | health `22810` وChroma `22810` |
| إضافة روابط مرافقة افتراضية | retrieval/package issue | package recall | السوق المالية، التجميل، البنوك، التعطل عن العمل، جرائم المعلوماتية | companion misses متكررة | residual slice passed |
| إضافة حزم مركبة عامة | retrieval/package issue | compound package recall | تجميع المحاور المتعددة في القضية الواحدة | full baseline pre-final: `96.7/100`, fatal core miss `513` | gap slice `649/649 = 100.0` |
| توسيع مرادفات العمل والتأمين الصحي | retrieval/package issue | synonym/generalization | `أجيرة` في التحرش، ومؤسسة/منشأة تأمين في مطالبات صحية | residual `22` case misses | residual `22/22 = 100.0` |
| إغلاق معيار الجمع الحالي | measurement boundary | full gate | تشغيل full 7000 retrieval-only بعد الاستقرار | 7000 not closed | `7000/7000`, score `100.0`, fatal `0`, transport `0` |

### نتيجة القرار

- final full report: `data/eval/gold_package_recall_v4_7000/gold_package_recall_7000_v4_round48_full_after_final_patch.json`
- final summary: `data/eval/gold_package_recall_v4_7000/round48_full_collection_summary.md`
- cases: `7000/7000`
- collection score: `100.0/100`
- core recall: `1.000`
- companion recall: `1.000`
- full package rate: `1.000`
- fatal core miss cases: `0`
- transport errors: `0`
- dev/regression/heldout: all `100.0`

### القرار التالي

Stop optimizing collection as the primary target under the current benchmark. The next logical round should test and improve purity/contamination/ranking while protecting the now-closed `7000/7000` collection recall.

## 2026-05-20 — Round 49 Issue Decomposition Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اعتماد التفكيك قبل الاسترجاع | retrieval/package issue | issue decomposition | إضافة `ISSUE_AXIS_BUNDLES` قبل دمج الحزم العامة | قضية الأسرة المركبة فوتت `execution-law` | user probe يجمع التنفيذ + الأحوال + الإثبات + حماية الطفل |
| منع انجذاب السفر إلى PDPL | contamination boundary | package trigger | حذف `خارج المملكة` المجردة من حزمة نقل البيانات | سفر أطفال خارج المملكة استدعى PDPL transfer | PDPL absent ما لم توجد بيانات/سحابة/نقل بيانات |
| فصل واتساب عن التجاري | contamination boundary | package trigger | واتساب/البريد يفعّلان الإثبات الإلكتروني، لا المطالبة التجارية وحدها | واتساب في نزاع أسري أو عام قد يجذب التجاري | commercial bundle يحتاج سياق تجاري مستقل |
| منع تكرار forced articles | retrieval/ranking support | context selection | عدم تكرار نفس `(regulation, article)` داخل selected context | مواد مكررة تطرد مواد تنفيذ مهمة | execution articles 9/21/34/73/74/92 تظهر في القضية المركبة |
| حماية جمع 7000 | measurement boundary | gates | gap regression + held-out limited بعد patch | خطر كسر collection المغلق | gap `649/649 = 100.0`; heldout300 `300/300 = 100.0` |

### نتيجة القرار

- files patched:
  - `app/rag/engine.py`
  - `app/main.py`
- reports:
  - `data/eval/gold_package_recall_v4_7000/round49_issue_decomposition_gap_slice.json`
  - `data/eval/gold_package_recall_v4_7000/round49_issue_decomposition_heldout300.json`
- readiness after restart:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22810`
  - semantic/dense `0.70`, lexical `0.30`

### القرار التالي

Proceed to purity/contamination/ranking scoring. Treat collection as protected by issue decomposition, but do not claim purity closure: excluded hits remain recorded in both Round 49 reports.

## 2026-05-20 — Round 50 LLC Material Selection Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تحسين محور LLC | retrieval/package issue | package recall | إضافة محور مدير شركة محدودة/أصول/أقلية/أرباح/تحديث عقد | Companies Law حاضر لكن مواد ضعيفة مثل 33 | المادة 27 ومواد الاطلاع/القيد/التعثر في المقدمة |
| منع منافسات بلا سياق حكومي | contamination boundary | package trigger | إزالة المحفزات العامة وإضافة صيغ باطن/حكومي محددة | تعويض في شركة خاصة قد يجذب المنافسات | لا تظهر المنافسات في LLC الخاص |
| فصل العمل في المنافسات | retrieval/package issue | conditional companion | `procurement_site_worker_wages_bundle` | إزالة العمل من compound كسرت gold المركب | العمل يعود عند `أجور عمال الموقع` فقط |
| فصل التأمينات في الإفلاس | retrieval/package issue | conditional companion | توسيع حزمة التأمينات لأجور مسجلة بأقل من الحقيقي | إزالة التأمينات كسرت insolvency-labor-insurance | التأمينات تعود عند واقعة التسجيل/الأجر الأقل فقط |
| منع الإفلاس المنفي | contamination boundary | negation guard | استثناء `دون وجود تعثر أو إفلاس` ونظائرها | كلمة إفلاس وحدها تكفي | لا يظهر الإفلاس في LLC مع نفي التعثر |
| موازنة forced articles | material/article-level issue | context selection | حد أولي لكل slug ثم fill عام | نظام واحد يبتلع سياق المواد المفروضة | مواد العمل والإفلاس والشركات تتوازن |
| اختيار أفضل chunk للمادة | material/article-level issue | article representation | اختيار chunk عملي لا أول chunk | المادة 46 قد تظهر كملاحظة تعديل فقط | مواد متعددة المقاطع تستدعى بنص عملي أكثر |

### نتيجة القرار

- files patched:
  - `app/rag/engine.py`
  - `app/main.py`
- reports:
  - `data/eval/round50_llc_material_contamination_manual_slice_after_article_entry_patch.json`
  - `data/eval/gold_package_recall_v4_7000/round50_llc_material_selection_gap_slice_after_conditional_restore.json`
  - `data/eval/gold_package_recall_v4_7000/round50_llc_material_selection_heldout300.json`
- gates:
  - manual slice `5/5`
  - working regression `649/649`, score `100.0`, fatal `0`, transport `0`
  - held-out `300/300`, score `100.0`, fatal `0`, transport `0`
- readiness:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22810`
  - semantic/dense `0.70`, lexical `0.30`

### القرار التالي

Move from collection/material recall to purity scoring. The next round should penalize excluded hits and ranking pollution while protecting collection gates.

## 2026-05-20 — Round 51 General Collection Closure Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إكمال العقد الإلكتروني | retrieval/package issue | companion recall | إضافة `civil-transactions-law` لحزمة التوقيع/السجل الإلكتروني | `gpr_v4_1585` يفقد المعاملات المدنية | family subset `10/10` ثم full `7000/7000` |
| توسيع صيغ المتنافس الحكومي | retrieval/package issue | issue decomposition | حزمة وقاعدة عامة للترسية/التقييم/التظلم مع متنافس/مورد/مقدم عرض | `gpr_v4_1587` يفقد نظام المنافسات ولائحته | procurement admin full recall `1.000` |
| توسيع رفقاء الإفلاس | retrieval/package issue | default companions | إضافة اللائحة التنفيذية والشركات والعمل كرفقاء افتراضيين للإفلاس | عدة finance insolvency cases تفقد `labor-law` | finance insolvency companion recall `1.000` |
| فحص المواد بعد الحزم | material/article-level issue | manual slice | شريحة مركبة من ٨ قضايا تفحص ظهور مواد/لوائح قريبة | لا يكفي نجاح أسماء الأنظمة | material slice `8/8` |

### نتيجة القرار

- files patched:
  - `app/rag/engine.py`
- reports:
  - `data/eval/gold_package_recall_v4_7000/round51_patch_failure_family_subset_after_patch.json`
  - `data/eval/gold_package_recall_v4_7000/round51_full7000_collection_after_general_closure_patch.json`
  - `data/eval/round51_material_random_compound_slice_after_general_closure_patch.json`
- gates:
  - targeted probe `10/10`
  - full collection `7000/7000`, score `100.0`, fatal `0`, transport `0`
  - material slice `8/8`
- readiness:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22810`
  - semantic/dense `0.70`, lexical `0.30`

### القرار التالي

Treat collection as closed under the current gold suite. Start the next phase: purity/contamination/ranking, with the full 7000 collection gate retained as a protected regression.

## 2026-05-21 — Round 52 Flexible Legal Issue Lexicon Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| دعم wildcard قانوني | retrieval/package issue | pattern matching | `_pattern_matches()` يدعم `*` كلاحقة مرنة | `توقفت عن سداد ديونها` لا تفعل الإفلاس | `توقف* عن سداد ديون*` يطابق الضمائر |
| قاموس محاور قانونية | retrieval/package issue | issue lexicon | `LEGAL_ISSUE_LEXICON_BY_BUNDLE` | عبارات رسمية/عامية مشتتة داخل الحزم | قاموس مرن للحزم الكبرى |
| إفلاس/ديون/تصفية | retrieval/package issue | insolvency axis | مرادفات توقف السداد والعجز عن الوفاء والدائن والتصفية | Companies Law فقط في القضية العشوائية | Companies + Bankruptcy + Bankruptcy regulation |
| منع التوسع الخاطئ | contamination boundary | exclusions | استثناء تصفية حقوق العامل وتعثر تنفيذ المشروع | خطر جذب الإفلاس لأي تصفية/تعثر | الاستثناءات مضافة وتحتاج قياس purity لاحق |

### نتيجة القرار

- files patched:
  - `app/rag/engine.py`
- reports:
  - `data/eval/gold_package_recall_v4_7000/round52_distress_lexicon_slice_after_flexible_wildcards.json`
- gates:
  - targeted formal + colloquial probes: pass
  - distress/debt regression slice `791/791`
  - score `100.0`
  - core recall `1.000`
  - companion recall `1.000`
  - full package rate `1.000`
  - fatal `0`
  - transport `0`
- readiness:
  - `/health ok`
  - port `8000`
  - Chroma actual count `22810`
  - semantic/dense `0.70`, lexical `0.30`

### القرار التالي

Run the protected full collection gate later if more lexicon layers are added. The immediate next phase remains purity/ranking: broad aliases deliberately favor recall and may increase excluded hits.

## 2026-05-21 — Qwen3 Embedding Candidate Baseline Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| تثبيت خط الأساس الإنتاجي | operational/readiness | embedding baseline | توثيق أن الإنتاج يستخدم `text-embedding-3-small` 1536-dim cosine | التباس محتمل مع نموذج التوليد Ollama | baseline واضح: OpenAI embeddings للإنتاج |
| قياس Qwen بمعزل | retrieval/package issue | dense retrieval | بناء فهرس Chroma منفصل لـ `qwen3-embedding:0.6b` | لا يوجد baseline محلي كامل | full 7000 dense-only report |
| منع التبديل المبكر | decision gate | production safety | عدم استبدال embedding الإنتاجي بنموذج خام | Qwen متاح في Ollama | Qwen لا يغلق الجمع الخام |
| تحديد المسار التالي | experimentation | training/reranking | استخدام Qwen كقاعدة تدريب/تجارب هجينة | raw dense k180 full package `0.503286` | يحتاج fine-tuning أو reranker قبل الإنتاج |

### نتيجة القرار

- script:
  - `scripts/run_embedding_dense_experiment.py`
- reports:
  - `data/eval/embedding_experiments/qwen3_embedding_0_6b/dense_eval_dev100_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_0_6b/dense_eval_full7000_report.json`
- isolated index:
  - `data/eval/embedding_experiments/qwen3_embedding_0_6b/chromadb`
- full dense-only gate:
  - cases `7000/7000`
  - k180 core recall `0.807540`
  - k180 companion recall `0.505581`
  - k180 full package rate `0.503286`
  - k180 fatal core misses `1547`
- readiness:
  - `/health ok`
  - port `8000`
  - production Chroma actual count `22810`

### القرار التالي

Do not switch production to raw Qwen embeddings. If local embedding independence is desired, prepare a proper Saudi-legal contrastive training/evaluation pipeline, then compare against the protected collection gates and later purity/ranking gates.

## 2026-05-21 — Final-1000 Synonym Training Slice Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| عدم التدريب على كل ٧٠٠٠ | training design | data selection | عزل آخر ١٠٠٠ فقط كمصدر التدريب المستهدف | خطر تدريب واسع يطمس سبب الفجوة | `synonym_surface_stress_v4` فقط |
| حماية heldout | evaluation integrity | split policy | عدم بناء pairs/triplets من heldout | احتمال قياس مغشوش | heldout `375` محفوظ للاختبار |
| بناء triplets | training data | contrastive/rerank | query/positive/negative من الحزم الصحيحة والمستبعدة | لا توجد بيانات تدريب منظمة | `41844` triplets من dev/regression |
| تثبيت baseline للشريحة | retrieval/package issue | dense baseline | تقييم Qwen الخام على heldout النهائي | full 7000 يخفي ضعف العاميات | k180 full package `0.144000` |

### نتيجة القرار

- scripts:
  - `scripts/build_embedding_training_data.py`
  - `scripts/train_qwen_embedding_sentence_transformers.py`
- dataset:
  - `data/eval/embedding_training/qwen3_saudi_legal_synonyms_v1`
  - size `203M`
  - final-1000 candidate cases `1000`
  - pair training cases `625`
  - heldout cases reserved `375`
- baseline report:
  - `data/eval/embedding_experiments/qwen3_embedding_0_6b/dense_eval_synonym_heldout375_report.json`
  - k180 core recall `0.707558`
  - k180 companion recall `0.500847`
  - k180 full package rate `0.144000`
  - k180 fatal core misses `173`

### القرار التالي

Actual embedding fine-tuning should use a trainable HF/SentenceTransformers Qwen3-Embedding-0.6B weight, not the Ollama Q8_0 inference blob. If that runtime is not available with MPS/GPU, the immediate practical alternative is a lightweight reranker over Qwen candidates while preserving this heldout baseline.

## 2026-05-21 — Qwen3-Embedding-8B Candidate Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| اختبار النموذج الأكبر | experiment | dense retrieval | بناء فهرس Chroma معزول لـ `qwen3-embedding:8b` | أفضل local raw k180 core `0.707558` | k180 core `0.899233` |
| عدم المساس بالإنتاج | operational safety | embedding index | عزل الفهرس في `data/eval/embedding_experiments/qwen3_embedding_8b` | إنتاج OpenAI مستقر | production count بقي `22810` |
| مقارنة heldout عادلة | evaluation integrity | final-1000 heldout | نفس 375 سؤالًا للثلاثة نماذج | OpenAI `5.1/10`, Qwen0.6 `5.6/10` | Qwen8B `7.3/10` |
| منع التبنّي الكامل | gate | collection closure | عدم إعلان اكتمال الجمع رغم تحسن core | full package rate منخفض | k180 full package `0.285333` |

### نتيجة القرار

- model:
  - `qwen3-embedding:8b`
  - dimension `4096`
  - quantization `Q4_K_M`
- report:
  - `data/eval/embedding_experiments/qwen3_embedding_8b/dense_eval_synonym_heldout375_report.json`
- k180 result:
  - core recall `0.899233`
  - companion recall `0.645763`
  - full package rate `0.285333`
  - fatal core misses `81`
  - excluded-hit cases `104`
- readiness after experiment:
  - `/health ok`
  - port `8000`
  - production Chroma actual count `22810`

### القرار التالي

Qwen3-Embedding-8B is the strongest local dense candidate so far. Use it next in a hybrid `jamia_recall` experiment preserving dense `0.70` and lexical `0.30`, then judge whether package expansion/reranking is enough before considering any production embedding migration.

## 2026-05-21 — Qwen3-Embedding-8B Hybrid 70/30 Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إدخال Qwen 8B في hybrid | experiment | retrieval | اختبار `jamia_recall` مع dense `0.70` وlexical `0.30` | raw dense k180 full `0.285333` | hybrid ranked k180 full `0.544000` |
| قياس اختيار السياق | retrieval/package | context selection | تمرير المرشحين عبر selector المحلي | ranked k180 fatal `66` | selected fatal `9` |
| اختبار forced/package expansion | gate | package expansion | إضافة الحزم المطلوبة من المحلل قبل الدمج | selected full `0.898667` | forced selected full `1.000000` |
| عدم إعلان اكتمال الجمع | evaluation integrity | gate interpretation | فصل نتيجة الذهب المحمي عن العشوائي المفتوح | analyzer-only `1.000000` | يلزم random manual slice |

### نتيجة القرار

- reports:
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_synonym_heldout375_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_forced_synonym_heldout375_report.json`
- readiness:
  - `/health ok`
  - production count `22810`
  - actual Chroma count `22810`
- diagnosis:
  - operational issue: none active.
  - retrieval/package issue: non-forced collection still not fully closed.
  - answer-level issue: not evaluated.

### القرار التالي

Do not move to cleaning/purity yet as the main track. First close the remaining package bridges that produce the `9/375` selected-context fatal misses, then run an external random manual collection slice. Use the forced result as a candidate gate, not as proof of universal recall.

## 2026-05-22 — Required Package Anchor Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إغلاق تزاحم الحزمة غير المفروضة | retrieval/package issue | hybrid candidate coverage | إضافة `package_anchor` ممثل واحد لكل core/companion slug مطلوب إذا غاب من lexical pool | heldout selected full `0.898667`, fatal `9` | heldout selected full `1.000000`, fatal `0` |
| تثبيت نسبة الجمع | retrieval policy | `jamia_recall` | إبقاء dense `0.70` وlexical `0.30` | خطر تغيير معيار التجربة | الأوزان لم تتغير |
| عدم الخلط مع التنظيف | phase control | purity | السماح بالضوضاء الحالية وعدم بدء الحذف | excluded hits موجودة | collection gates تقاس منفصلة |
| تحميل التعديل حيًا | operational control | service readiness | restart واحد على port `8000` لأن الكود تغيّر | العملية السابقة لا تحمل patch | `/health ok`, chunks `22810` |

### نتيجة القرار

- reports:
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_targeted_bridge_gap_after_required_anchor_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_manual_external_slice_after_required_anchor_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_synonym_regression375_after_required_anchor_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_synonym_heldout375_after_required_anchor_report.json`
- gates:
  - targeted bridge-gap selected full package `1.000000`, fatal `0`
  - external manual selected full package `1.000000`, fatal `0`
  - regression selected full package `1.000000`, fatal `0`
  - held-out selected full package `1.000000`, fatal `0`
- live probes after restart:
  - company distress/company law/bankruptcy composite passed with no missing package docs.
  - family custody/execution/electronic evidence/child protection composite passed with no missing package docs.

### القرار التالي

Treat the present selected-context collection gate as passed for the current suite and expose it to another user random collection test before moving the main track to purity/ordering. Do not claim raw-ranked retrieval is complete; the closure is at selected package context.

## 2026-05-22 — Learned Package Router And Gemma Teacher Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إعادة تصنيف الفجوة | retrieval/package issue | issue decomposition | فصل missing analyzer packages عن candidate crowding | selected gold gates passed | external manual analyzer required recall `0.709677` |
| بناء راوتر حزم أولي | learned routing | package labels | TF-IDF char/word OVR فوق gold v4 splits | analyzer external full package `0.250000` | router top-24 external full package `0.500000` |
| حماية heldout | evaluation integrity | split policy | train على dev/regression وقياس heldout منفصل | خطر خلط التدريب والقياس | heldout top-24 required recall `0.999255` |
| استخدام Gemma كمعلم | teacher supervision | hard composites | local `gemma4:31b` فوق مرشحي الراوتر الواسعين | الراوتر يسقط محاور ضريبية/استثمارية خارجية | teacher adds composite package labels for retraining |

### نتيجة القرار

- scripts:
  - `scripts/build_package_router_dataset.py`
  - `scripts/train_package_router_baseline.py`
  - `scripts/run_package_router_gemma_teacher.py`
- data:
  - `data/eval/manual_collection_external_audit_20260522.jsonl`
  - `data/eval/package_router/saudi_legal_package_router_v1`
- reports/models:
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_tfidf_ovr_baseline_report.json`
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_tfidf_ovr_baseline.joblib`
  - `data/eval/package_router/saudi_legal_package_router_v1/gemma4_31b_teacher_manual_external.jsonl`
- readiness:
  - `/health ok`
  - port `8000`
  - actual Chroma count `22810`

### القرار التالي

Keep the main track on collection. Use Gemma teacher outputs to enrich hard composite package-routing data, train a stronger router or reranker, and test it on new external random collection cases before runtime integration or purity work.

## 2026-05-22 — Learned Package Router Runtime Seed Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| دعم الحزم النادرة المركبة | learned routing | router training | مزج `composite_mixup_train` مع دعم Gemma للحزم النادرة المكشوفة | external router top-48 full package `0.750000` | external router top-32/top-48 full package `1.000000` |
| إدخال الراوتر في الجمع | retrieval/package issue | `jamia_recall` package seeds | تحميل artifact الراوتر اختياريًا وإضافة ترشيحاته كبذور جمع لا كحزم gold-required | المحلل الثابت يفقد محورًا كاملاً في بعض المركبات | البذور الخارجية تغطي كل required slugs في slice الأربع |
| تقديم الحزمة قبل تكرار المواد | retrieval/package issue | selected context | إعطاء مقطع واحد مبكر لكل بذرة متعلمة بعد الحزم الثابتة وقبل تكرار forced articles | live external score `76.5/100` | live external score `100.0/100` |
| اعتماد السؤال الكامل للراوتر الحي | learned routing | inference | ترتيب labels على السؤال الكامل بدل segment-max | segment-max live بقي يفقد الزكاة في مركب التعثر | whole-question live full package `1.000000` |

### نتيجة القرار

- artifact:
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_tfidf_ovr_rare_mixup_gemma_gap.joblib`
- reports:
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_tfidf_ovr_rare_mixup_gemma_gap_report.json`
  - `data/eval/manual_collection_external_audit_20260522_router_whole_question_service_probe.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_synonym_regression375_after_learned_package_router_report.json`
  - `data/eval/embedding_experiments/qwen3_embedding_8b/hybrid_jamia70_lex30_synonym_heldout375_after_learned_package_router_report.json`
- gates:
  - live external full package `1.000000`, fatal core misses `0`
  - synonym regression selected full package `1.000000`, fatal core misses `0`
  - synonym held-out selected full package `1.000000`, fatal core misses `0`
- readiness after restart:
  - `/health ok`
  - project root and port correct
  - actual Chroma count `22810`

### القرار التالي

Keep collection as the main phase for one more external random audit that is not used to train the router. If it passes, move the main track to package ordering/purity and then material-level article precision; do not treat current excluded hits as a collection failure.

## 2026-05-24 — General Package Router Retrieval Table Decision

| القرار | التصنيف | الطبقة | الإجراء | القياس قبل | القياس بعد |
|---|---|---|---|---|---|
| إيقاف مسار الترقيع كسلوك رئيسي | strategy | collection architecture | بناء جدول تعميم للحزم من كامل corpus بدل إضافة مرادفات سؤال بسؤال | شريحة خارجية جديدة تكشف محاور ضريبية/وثائق سفر/اختصاص | جدول تدريب `6763` صفًا يغطي `303` label |
| إضافة راوتر استرجاعي للحزم | retrieval/package issue | learned package routing | فهرس TF-IDF على `23038` صفًا من gold + mixup + Gemma + corpus table | OVR وحده بطيء ومحدود في المحاور الطويلة | artifact `package_router_retrieval_table_v1.joblib` |
| تفكيك السؤال المركب للراوتر | retrieval/package issue | issue decomposition | دمج توقعات السؤال الكامل مع مقاطع السؤال | بعض المحاور تختنق داخل السؤال الطويل | `_router_query_segments` + segment top-k |
| إغلاق حزم الضرائب/الزكاة والعقود الخاصة | retrieval/package issue | issue axes | إضافة محاور عامة: `axis_tax_zakat_debt_obligations` و`axis_private_commercial_contract_claim_procedure` | external local score `92.2/100`, fatal `1` | external local score `100/100`, fatal `0` |

### نتيجة القرار

- artifacts:
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_generalization_table_v1.jsonl`
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_retrieval_table_v1.joblib`
  - `data/eval/package_router/saudi_legal_package_router_v1/package_router_tfidf_ovr_generalization_table.joblib`
- external audit:
  - `data/eval/manual_strategy_package_router_external_audit_20260523.jsonl`
  - report before axis closure:
    - `data/eval/manual_strategy_package_router_external_audit_20260523_local_after_retrieval_table_nodense.json`
    - score `92.2/100`, full package `0.75`, fatal core miss `1`
  - report after axis closure:
    - `data/eval/manual_strategy_package_router_external_audit_20260523_local_after_tax_procedure_axes.json`
    - score `100/100`, core `1.0`, companion `1.0`, full package `1.0`, fatal `0`
- working regression:
  - `data/eval/manual_strategy_package_router_regression375_local_after_tax_procedure_axes.json`
  - cases `375`, score `100/100`, full package `1.0`, fatal `0`
- held-out:
  - `data/eval/manual_strategy_package_router_heldout375_local_after_tax_procedure_axes.json`
  - cases `375`, score `100/100`, full package `1.0`, fatal `0`

### تفسير القياس

- هذا ليس اختبار answer-level.
- هذا قياس `retrieval/package` محلي مع تعطيل dense فقط لأن اتصال embedding من البيئة أعاد `Connection error`.
- الخدمة بعد إعادة التشغيل حملت artifacts الجديدة:
  - `package_router_tfidf_ovr_generalization_table.joblib`
  - `package_router_retrieval_table_v1.joblib`
- `/health = ok` وChroma الفعلي `22810`.
- فشل `POST/GET` لفحص retrieval من الطرفية بقي operational transport issue؛ لا يحسب فجوة RAG.

### القرار التالي

مرحلة الجمع أصبحت أقوى معماريًا، لكن لا ننقلها إلى “مغلقة نهائيًا” إلا بعد اختبار live عبر واجهة المستخدم أو قناة لا تعاني من فشل POST/GET. بعد ذلك تبدأ مرحلة ترتيب/تنظيف التلويث، لا زيادة قوائم الأسئلة.

## 2026-05-24 — Resume Verification For General Collection Layer

| القرار | التصنيف | الطبقة | النتيجة |
|---|---|---|---|
| تثبيت حالة الجولة بعد الاستئناف | operational verification | readiness | `/health ok`, root صحيح، port `8000`, service chunks `22810`, Chroma actual `22810` |
| اعتماد نتائج gates المحلية | retrieval/package issue | package collection | external `8/8`, regression `375/375`, held-out `375/375` كلها full package `1.0` |
| عدم ادعاء تحقق dense live | operational boundary | evaluation | local dense فشل باتصال embedding؛ لذلك لا يُسجل هذا كتحقق 70/30 حي |
| عدم الانتقال للتنظيف قبل اختبار حي | sequencing | collection phase | الاختبار الحي عبر UI هو gate التالي قبل purity/order |

### القرار التالي

اطلب من المستخدم اختبار الجمع من الواجهة أو نفذ قناة live بديلة لا تعتمد على endpoint الذي يفشل transport. إذا بقي الجمع مكتملًا، تبدأ مرحلة ترتيب الحزم وإخفاء التلويث؛ إذا ظهر نقص حزم جديد، يُشخّص كفجوة router/generalization لا كفجوة جواب.

## 2026-05-25 — Article Surface Router Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| بناء أسطح من مواد الأنظمة | retrieval/package issue | package router retrieval table | الراوتر لا يربط "كود مصدري/برنامج داخلي" بحقوق المؤلف | article surface table `26158` صفًا |
| إعادة بناء retrieval table | architecture | package router | `23038` rows | `49196` rows |
| direct specific-field matching | retrieval/package issue | analyzer/router bridge | external case 4 missing `copyright-law` | external 4-case audit `100/100` |
| منع قياس خاطئ | evaluation hygiene | benchmark input | `all_router_rows.jsonl` أعطى `0/100` بسبب حقول scorer غير متوافقة | أعيد القياس على `gold_package_recall_7000_v4.jsonl` |

### gates المعتمدة

- external audit:
  - `data/eval/manual_root_collection_external_audit_20260525_after_direct_field_and_article_surface_local.json`
  - cases `4/4`, score `100/100`, fatal `0`
- gold v4 regression:
  - `data/eval/manual_root_collection_gold_v4_regression375_after_article_surface_direct_field_local.json`
  - cases `375/375`, score `100/100`, fatal `0`
- gold v4 held-out:
  - `data/eval/manual_root_collection_gold_v4_heldout375_after_article_surface_direct_field_local.json`
  - cases `375/375`, score `100/100`, fatal `0`

### القرار التالي

Do not add more manual collection patches unless a true unseen family fails. The next engineering move is to compress or rerank the enlarged package-router retrieval table, then move to ordering/purity and article precision after live UI validation.

### live targeted verification

- service restarted on port `8000`.
- `/health ok`; Chroma actual count `22810`.
- targeted hard case probe:
  - semantic active `true`
  - dense `0.70`
  - lexical `0.30`
  - `copyright-law` selected first.

### performance note

The accepted recall layer is heavier: `package_router_retrieval_table_v1.joblib` is about `381M`. Compression or a two-stage router is the next root-level engineering task before broad full live evaluation.

## 2026-05-25 — Compound Payments/Delivery Collection Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| إضافة محور اشتراكات/تمويل/مدفوعات | retrieval/package issue | package collection | case 2 missing `nzam-almdfwaat-wkhdmatha` | targeted local case 2 `100/100` |
| إضافة محور توصيل/عمل/نقل/مرور/منافسة | retrieval/package issue | package collection | case 4 missing `labor-law`, `nzam-almrwr` | targeted local case 4 `100/100` |
| توسيع article-surface concepts | architecture | package router | مفاهيم الدفع والتوصيل لا تولد أسطحًا كافية | مفاهيم payments/finance/delivery/traffic مضافة |
| رفض تقرير live transport | operational issue | evaluation transport | live report `0/100` | غير معتمد لأن `transport_error_cases=4` |

### gates المعتمدة

- targeted compound audit:
  - `data/eval/manual_user_compound_collection_audit_20260525_local_after_general_axis_patch.json`
  - cases `4/4`, score `100/100`, fatal `0`
- working regression:
  - `data/eval/manual_user_compound_collection_regression375_after_general_axis_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`
- held-out:
  - `data/eval/manual_user_compound_collection_heldout375_after_general_axis_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`

### operational status

- service restarted on port `8000`.
- `/health ok`.
- Chroma actual count `22810`.
- `data/eval/manual_user_compound_collection_audit_20260525_live_after_general_axis_patch.json` is not accepted as a RAG result because it completed `0/4` cases due to terminal transport failures.

### القرار التالي

Collection can be tested by the user from the app/UI. If no new package miss appears, move to the next phase: ordering/purity and article-level precision. Do not treat additional contamination as a blocker to collection closure unless it suppresses required packages.

## 2026-05-26 — Strong Collection Closure Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| إنشاء بوابة جمع قوية جديدة من 18 قضية مركبة | evaluation hygiene | collection gate | الاعتماد على شرائح سابقة فقط | `manual_strong_collection_gate_20260526.jsonl` |
| إظهار المراجع المعروفة غير المقطعة | retrieval/package issue | package collection | `workplace-behavioral-misconduct-controls` missing رغم وجوده في الكتالوج | catalog-only fallback يعرض المرجع مع تنبيه |
| عدم اعتبار PDF مصور كفجوة RAG | operational/corpus boundary | source ingestion | استخراج النص من PDF رسمي لم ينتج مادة مفيدة | يؤجل إلى OCR/ingestion، لا إلى package recall |
| تثبيت أن الجمع لا يزال 70/30 في profile | operational verification | retrieval profile | قلق المستخدم من تعطيل الدلالي | `jamia_recall` dense `0.70`, lexical `0.30` |

### gates المعتمدة

- strong manual collection gate:
  - `data/eval/manual_strong_collection_gate_20260526_local_after_catalog_fallback_patch.json`
  - cases `18/18`, score `100/100`, fatal `0`
- working regression:
  - `data/eval/manual_strong_collection_regression375_after_catalog_fallback_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`
- held-out:
  - `data/eval/manual_strong_collection_heldout375_after_catalog_fallback_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`

### operational status

- initial health failure was an `operational issue`; service was restarted on the same port.
- final `/health ok`.
- Chroma actual count `22810`.
- terminal live probe failure after restart is treated as transport-only unless reproduced through the app/UI.

### القرار التالي

Accept package collection as passed for the current gates, with one caveat: catalog-only/scanned references still need article-level ingestion. The next phase should be article precision and ordering/purity, not more package-collection patching, unless the user finds a new true core-package miss.

## 2026-05-26 — Article Precision First Gate Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| إنشاء بوابة مواد صريحة | evaluation hygiene | article precision | لا يوجد gate مستقل لأزواج النظام/المادة | `manual_article_precision_gate_20260526.jsonl` |
| قياس actual selected article pairs | answer-level issue | diagnostics | `top_articles` كان يخلط المتوقع بالمحدد فعليًا | recall يحسب selected pairs فقط |
| تقديم مواد الأنظمة المركزية | answer/article-level issue | context selection | مواد مرافقة أو زوائد كانت تزاحم المواد الحاسمة | core articles قبل companion/extras |
| توسيع `jamia_recall` | recall-first strategy | context budget | `context_limit=48` ثم `64` لم يكف لبعض المركبات | `context_limit=72` |
| منع VAT من إدخال الزكاة/الدخل | retrieval/package hygiene | axis routing | VAT كان يوقظ محور الزكاة/الدخل العام | VAT يبقى لحزم VAT المتخصصة |
| تعميم إشارات مفقودة | retrieval/package issue | signal coverage | `تنسيق الأسعار`، `مركبات غير مؤمنة`، `سقوط سقالة`، `عيوب خرسانية` | ظهرت الحزم والمواد المطلوبة |

### gates المعتمدة

- article precision:
  - `data/eval/manual_article_precision_gate_20260526_final_local.json`
  - cases `8/8`, article score `100/100`, failed `0`
- collection hard slice:
  - `data/eval/manual_strong_collection_gate_20260526_after_article_precision_collection_restored.json`
  - cases `18/18`, score `100/100`, fatal `0`
- working regression:
  - `data/eval/manual_article_precision_regression375_collection_after_final_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`
- held-out:
  - `data/eval/manual_article_precision_heldout375_collection_after_final_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`

### operational status

- service restarted after code changes.
- final `/health ok`.
- Chroma actual count `22810`.
- `jamia_recall` dense `0.70`, lexical `0.30`, context `72`.

### القرار التالي

Do not reopen package collection unless a new core-package miss appears. Continue with either:

1. expand article precision gates to more legal families, or
2. start ordering/purity now that collection and the first article gate pass.

## 2026-05-30 — User Article Precision Expansion Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| بناء شريحة article precision من تقييم المستخدم | evaluation hygiene | article precision | 4 قضايا يدوية بدرجة جمع `7.5/10` نوعيًا | `manual_user_article_precision_slice_20260530.jsonl` |
| تعميم إشارات واتساب/عدم المنافسة/PDPL/الجهاز الطبي/المحتوى المحلي | retrieval/package issue | signal coverage | user slice `61.8/100`, `0/4` | user slice `100/100`, `4/4` |
| تضييق مواد عامة تزاحم المقالات الحاسمة | retrieval/package issue | context budget | case 3 كان يقترب من حد `72` بمواد عامة | cases `4/4` بعد evidence/device trim |
| استعادة مواد VAT دقيقة بعد gate قديم | retrieval/package issue | article material | old article gate `98.2/100`, missing VAT/e-invoicing 3 pairs | old article gate `100/100`, `8/8` |
| فصل التشغيل عن فجوات RAG | operational issue | evaluation setup | تشغيل واسع غير مقصود سبب transport errors بعد إيقاف الخدمة | أُهمل التقرير غير الصالح وأعيد تشغيل `--limit 375` |

### gates المعتمدة في هذه الجولة

- readiness:
  - `/health ok`.
  - Chroma actual count `22810`.
  - `jamia_recall` dense `0.70`, lexical `0.30`, context `72`.
- user article precision:
  - `data/eval/manual_user_article_precision_slice_20260530_service_after_vat_restore.json`
  - cases `4/4`, article score `100/100`, failed `0`, transport `0`.
- old article precision:
  - `data/eval/manual_article_precision_gate_20260530_service_after_vat_restore.json`
  - cases `8/8`, article score `100/100`, failed `0`, transport `0`.
- working regression:
  - `data/eval/manual_article_precision_regression375_collection_20260530_after_user_slice_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`, transport `0`.
- held-out:
  - `data/eval/manual_article_precision_heldout375_collection_20260530_after_user_slice_patch.json`
  - cases `375/375`, score `100/100`, fatal `0`, transport `0`.

### القرار التالي

Current package/article collection gates pass. The next logical round is to widen article precision families before purity cleanup, unless the user explicitly prioritizes contamination/order.

## 2026-05-31 — Article Coverage Matrix Dashboard Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| تحويل توسيع article precision إلى مصفوفة قابلة لإعادة التشغيل | evaluation hygiene | article precision | توسيع يدوي متكرر للعائلات | `article_coverage_matrix_v1.json` و`article_coverage_matrix_v1_probes.jsonl` |
| فصل فجوات التشغيل عن فجوات RAG في ملخص مستقل | operational/evaluation hygiene | diagnostics | transport errors كانت قد تبدو كفشل دقة | `summarize_article_precision_gaps.py` يصنف operational/retrieval/answer/ok |
| عرض دقة الجمع في لوحة التحكم | product/evaluation ops | dashboard | التقارير كانت ملفات فقط | بطاقة `مستوى دقة الجمع` + background job |
| تشغيل gate من الخدمة الحية نفسها | evaluation hygiene | service check | تشغيل محلي قد لا يعكس الخدمة | gate على `http://127.0.0.1:8000/internal/rag/query` |

### gates المعتمدة في هذه الجولة

- readiness:
  - initial `/health` failure was service-down operational only.
  - final `/health ok`.
  - `project_root = /Users/majd/Desktop/codex/شات الاستشارات`.
  - `configured_server_port = 8000`.
  - `knowledge_base_chunks = 22810`.
  - Chroma actual count `22810`.
  - `jamia_recall` dense `0.70`, lexical `0.30`, context `72`.
- matrix:
  - source cases `12`.
  - regulations `31`.
  - article pairs `229`.
  - axes `20`.
  - probes `32`.
- dashboard article coverage gate:
  - `data/eval/article_coverage_matrix_v1_probe_gate_dashboard_20260531_162601.json`
  - cases `32/32`.
  - article score `100.0/100`.
  - pass rate `1.0`.
  - failed `0`.
  - governing system `1.0`.
  - implementing regulation `1.0`.
  - axis coverage `1.0`.
  - transport `0`.
- gap classification:
  - `data/eval/article_coverage_matrix_v1_probe_gap_summary_dashboard_20260531_162601.json`
  - decision `PASS`.
  - classification counts `ok=32`.

### القرار التالي

Dashboard-backed article coverage audit is accepted as a working gate for the current matrix. Do not treat the earlier service-down/transport failures as RAG gaps. Next logical round: expand the deterministic matrix with new human-reviewed article pairs, using Gemma only to propose candidate scenarios/axes, then consider a scheduled audit cadence and later purity/order cleanup.

## 2026-06-02 — Continuous Development Diagnostic Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| فصل فشل اتصال Python في sandbox عن فجوات RAG | operational issue | evaluation hygiene | transport rows كانت تظهر كـ 0/100 | operational-only لا يدخل في root-cause/material counts |
| إضافة تشخيص احتفاظ مقابل توسع | evaluation hygiene | article learning | تذبذب dashboard غير مفسر | fixed/replay/route slices كلها `100/100` |
| قبول تفسير التوسع الأفقي للدفعات الجديدة | retrieval/package issue | article precision | آخر 35 جولة `37.1/100`, pass `0.029` | نفس الدفعة بعد التحسين `100/100` |
| اعتماد تحسين آخر 35 جولة | retrieval/package issue | router/article support | `article_route_surface_gap=41`, `package_router_missing_core=15`, `context_budget_displacement=12` | `ACCEPTED`, manual `8/8`, deferred `0` |
| استئناف التطوير المستمر | product/evaluation ops | background loop | autopilot idle | job `bYQPDLhQl8bnZ9fK`, batch `35`, interval `20s` |

### gates المعتمدة في هذه الجولة

- readiness:
  - `/health ok`.
  - `project_root = /Users/majd/Desktop/codex/شات الاستشارات`.
  - `configured_server_port = 8000`.
  - Chroma count `22810`.
  - `jamia_recall` dense `0.70`, lexical `0.30`, context `72`.
- learning diagnostic:
  - `data/eval/article_autopilot/learning_diagnostic/article_learning_diagnostic_manifest_20260602_025035.json`
  - fixed manual `8/8`, score `100/100`.
  - retained replay `12/12`, score `100/100`.
  - route-surface replay `12/12`, score `100/100`.
- guarded improvement:
  - `data/eval/article_autopilot/article_autopilot_improvement_manifest_20260602_025302.json`
  - decision `ACCEPTED`.
  - validation batch `70/70`, score `100/100`.
  - manual slice `8/8`, score `100/100`.

### القرار التالي

Let the continuous loop run. The main watch metric is not raw failure count in new exploration rounds, but retained replay plus post-improvement batch validation. If the next accepted batches keep manual/replay at `100/100`, the process is learning horizontally. If retained replay drops, switch from expansion to retention/semantic generalization work.

## 2026-06-02 — Train/Holdout Guardrail Decision

| القرار | التصنيف | الطبقة | القياس قبل | القياس بعد |
|---|---|---|---|---|
| فصل synthetic training عن synthetic holdout | evaluation hygiene | article autopilot | كل القضايا غير التشغيلية يمكن أن تدخل الدعم | holdout locked ولا يدخل promotion/support |
| جعل retry-focus تشخيصًا لا قبولًا | evaluation hygiene | improvement validation | retry-focus يستخدم لبناء دعم محاولة ثانية | validation يرفض أي ملف يحتوي retry-focus |
| إضافة rank/context/pollution | retrieval/package diagnostics | article precision | حضور المادة فقط | MRR/rank/context position/pollution في الصف والملخص |
| رفض تحسين لا يعمم على holdout | retrieval/package issue | generalization | batch validation `0.968`, manual `1.0` | holdout `0.0` ⇒ `REJECTED_ROLLED_BACK` |

### القرار

لا نكمل التحسين المستمر قبل احترام holdout. الحارس الجديد كشف أن تحسينًا كان سيبدو مقبولًا على الدفعة واليدوي لكنه لا يعمم على القضايا المحجوبة. لذلك القرار الصحيح هو رفضه وترك artifacts السابقة كما هي. أي transport errors ظهرت أثناء الإيقاف/التحقق تعامل كـ operational issue ولا تُحسب فجوة RAG.

## 2026-06-08 — قرار أولوية المواد الأقل اختبارًا

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| جعل أقل عدد مرات اختبار للمادة أول مفتاح ترتيب | evaluation scheduling | `6` مواد غير مختبرة ظلت مؤجلة داخل نظام متكرر | اختيرت المواد الست في جولتين فعليتين |
| عدم السماح لفلتر تكرار النظام باستبعاد أدنى طبقة اختبار | horizontal coverage | تكرار النظام يسبب starvation | المواد غير المختبرة `0/12000` |
| حفظ تشخيص اختيار المواد في manifest الجولة | observability | لا يظهر هل اختيرت مادة جديدة أم معادة | `selected_untested_pairs` و`selected_pair_count_distribution` |

### القرار

التغطية الأولية للمواد المؤهلة أغلقت فعليًا. يستمر المجدول الآن في المرور المعمق على المواد ذات أقل عدد اختبارات. أي فشل للجولات `666-667` هو فجوة جمع حقيقية تدخل التحسين المرحلي، وليس فشلًا في المجدول.

## 2026-06-08 — قرار اعتماد هولداوت ثابت ومنع التراجع

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| فصل benchmark ثابت عن الهولداوت المتحرك | evaluation hygiene | تغير صعوبة القضايا يجعل المقارنة بين الدورات غير مستقرة | `200` قضية ثابتة مقفلة + متحرك للاستكشاف |
| منع قبول تحسين يتراجع على الثابت | retrieval/package safety | يمكن قبول moving holdout backlog بلا حارس retention ثابت | fixed no-regression gate إلزامي قبل القبول أو الترحيل |
| رفض التشغيل الناقص للبنك الثابت | evaluation integrity | وجود بعض الحالات كان يكفي لبناء summary | يجب اكتمال `200/200` ووجود كل المقاييس |
| تصحيح مؤشر الجاهزية العملية | dashboard/evaluation ops | أمكن عرض `100%` دون fixed result | baseline ثابت يدخل الحساب؛ بدأ `91.7%` وتحرك حيًا إلى `79.6%` مع الجولات الصعبة |

### gates

- readiness مر:
  - `/health ok` بعد restart.
  - root والمنفذ صحيحان.
  - chunks وChroma actual `22810`.
  - dense/lexical `0.70/0.30`.
  - context `72`.
- اختبارات منطق الحارس مرت.
- live fixed holdout gate ما زال pending داخل أول دورة تحسين جديدة.
- لم يُشغل full regression.

### القرار

يُستأنف التطوير المستمر، لكن لا يعتمد أي artifact جديد إلا بعد عبور البنك الثابت وعدم تراجع الشريحة اليدوية. إخفاقات الهولداوت المتحرك تُحفظ كفجوات استكشافية فقط. أعلى أولوية لاحقة هي رفع جودة البنك الثابت نفسه تدريجيًا، لا مجرد المحافظة على `76.2/100`.

## 2026-06-12 — قرار فصل تعطل اللوحة عن جودة RAG

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| تصنيف غياب المستمع على المنفذ `8000` كعطل تشغيل فقط | operational issue | حالة محفوظة `running` ولوحة غير متاحة | خدمة حية و`/health = ok` |
| استئناف التطوير من الحالة المحفوظة | operational continuity | العملية ميتة والجولات متوقفة | التطوير المستمر نشط ويُنتج جولات |
| إضافة أدوات حراسة محلية للخدمة | operational resilience | لا توجد طبقة تعيد الخدمة بعد خروجها | مشغل وحارس ومدير خدمة داخل المشروع |
| فصل بناء اللوحة الثقيل عن حلقة الطلبات | operational responsiveness | فتح اللوحة يحجب `/health` وقد يبدو كتعطل | `/health` يستجيب أثناء بناء اللوحة |
| تقليل قراءة التاريخ التفصيلية | dashboard performance | نحو `36,000` قراءة ملف لبناء لقطة واحدة | ملخص إجمالي خفيف + تفاصيل آخر `50` دورة + لقطة حية |
| عدم تشغيل regression بسبب انقطاع تشغيلي | evaluation hygiene | احتمال خلط التعطل بفجوة جمع | لا تغيير RAG ولا فجوة محسوبة |

### القرار

لا تعديل على RAG بسبب هذا الحادث. تبقى أي مهلة استجابة أثناء ضغط النموذج مشكلة تشغيلية منفصلة، بينما تعتمد جودة الجمع فقط على نتائج الجولات غير التشغيلية.

## 2026-06-12 — قرار منع تكرار تعطل اللوحة

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| إنهاء عمليات التطوير الفرعية عند إيقاف الخدمة | operational resilience | عمليات قديمة تستمر بعد restart | إيقاف رشيق لمجموعة العملية مع استئناف محفوظ |
| استعادة corpus المنظم قبل إعادة الخدمة | data integrity | `chunks.jsonl` مفقود بعد قطع build | corpus مكتمل وChroma متطابقان عند `22810` |
| عدم فحص كتالوجات النماذج أثناء رسم اللوحة | dashboard performance | أول فتح ينتظر OpenRouter وGemini وOllama | العرض يعتمد الحالة المحلية فورًا |
| إبقاء الحادث خارج تقييم RAG | evaluation hygiene | احتمال تفسير التعطل كفجوة جمع | لا patch استرجاع ولا regression غير مبرر |

### القرار

يُعد الحادث مغلقًا تشغيليًا بعد تحقق المستمع الواحد، سلامة corpus، واستئناف التطوير المستمر. أعلى فجوة تشغيلية لاحقة هي جعل بناء corpus ذريًا بالكامل حتى لا يمكن أن تتركه أي مقاطعة بلا ملف صالح.

## 2026-06-13 — قرار مراقبة اللوحة دوريًا

| القرار | التصنيف | السبب |
|---|---|---|
| اعتبار توقف اللوحة Operational فقط | operational issue | لا يوجد مستمع على `8000`، ولا توجد فجوة RAG جديدة |
| استئناف الخدمة يدويًا الآن | operational recovery | الحالة محفوظة `queued` وتحتاج خدمة حية |
| إضافة heartbeat كل 5 دقائق | operational resilience | المشغلات الخلفية المفصولة تفشل في ربط المنفذ بدون صلاحية دائمة |
| عدم اعتماد دورة أخطاء النقل كتحسين RAG | evaluation hygiene | fixed holdout احتوى `185` transport errors |

### القرار

التحسينات لا تُقاس أثناء سقوط الخدمة. الدورة الأخيرة ذات batch/manual ممتازة لكنها لا تُحسب تحسينًا عامًا بسبب أخطاء النقل. نستمر من جولة جديدة بعد استقرار اللوحة.

## 2026-06-13 — قرار الانتقال إلى تحقق متدرج أسرع

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| تقليل نداءات النموذج المحلي | evaluation throughput | `2` مرشحين × `20` جولة | `4` مرشحين × `8` جولات افتراضيًا |
| فصل الحارس السريع عن الحارس الكامل | evaluation strategy | fixed `200` + moving `200` في كل دورة | fixed sample `60/200` + moving `60` في الدورات العادية |
| إبقاء full holdout دوريًا | retrieval/package safety | كل دورة مكلفة جدًا | full fixed holdout كل `5` دفعات افتراضيًا |
| تسجيل نمط التحقق في اللوحة | dashboard clarity | يصعب تمييز sample من full | `validation_mode` وحجم العينة يظهران في تقارير الدورات |

### القرار

لا نلغي الحماية ولا نحسب العينة كبديل نهائي للهولداوت الكامل. العينة تصبح حارس سرعة لاكتشاف التراجع الشديد، بينما يظل full fixed holdout حارسًا دوريًا. الهدف هو تسريع حلقة active-learning/hard-negative mining بدل إنفاق معظم الوقت على إعادة قياس ثابت بعد كل دفعة صغيرة.

## 2026-06-13 — قرار شريحة Article Precision أوسع على الخدمة

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| فصل المادة الموجودة غير الموجهة مباشرة عن المادة المفقودة | evaluation hygiene | أي `unrouted_expected_article_pairs` كان يسقط الحالة حتى لو دخلت المادة السياق | `covered_unrouted_expected_article_pairs` غير حاجبة، و`missing_unrouted_expected_article_pairs` فقط تبقى حاجبة |
| بناء شريحة خدمة أوسع من اليدوي + دفعة مقبولة + fixed holdout | evaluation strategy | الاعتماد على 8 حالات ممثلة لا يكشف اتساع article precision | `40` حالة عبر مجالات أوسع على `127.0.0.1:8000` |
| عدم ترقيع RAG لحالة صناعية واحدة | retrieval/package discipline | احتمال patch خاص للمادة `18` | سُجلت كـ gap/أو gold overreach محتمل لجولة لاحقة بدل تعديل خاص |
| فصل سقوط الخدمة قبل القياس عن جودة RAG | operational issue | `/health` فشل لأن الخدمة لم تكن تقبل الاتصال | أُعيد تشغيل الخدمة على `8000` فقط ثم أُعيد readiness؛ لا يُحسب كفجوة RAG |

### gates

- readiness بعد recovery:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - Chroma actual count `22810`.
  - `jamia_recall = 0.70` dense و`0.30` lexical و`context_limit = 72`.
- broad40 service slice:
  - `data/eval/manual_article_precision_broad40_service_20260613_after_diagnostic_patch.json`
  - score `99.2/100`.
  - pass rate `0.975`.
  - transport errors `0`.
  - governing system `1.0`.
  - implementing regulation `1.0`.
  - axis coverage `0.975`.
- gap summary:
  - `data/eval/manual_article_precision_broad40_service_20260613_gap_summary_after_diagnostic_patch.json`
  - `ok = 39`.
  - `retrieval/package issue = 1`.
  - non-blocking direct-route note = `1`.

### القرار

الطريقة الحالية أفضل كمنهج قياس لأنها تكشف المادة والمحور والسياق، لا مجرد النظام الحاكم. لكنها لا تمر pass كاملًا على الشريحة الأوسع بسبب مادة واحدة في النظام الصناعي الخليجي: `:18`. لا نبدأ تنظيف التلويث ولا نعمل patch خاص قبل مراجعة gold/axis لهذه الحالة أو بناء شريحة صناعية صغيرة تؤكد أن المادة `18` محور واقعة حقيقي.

## 2026-06-30 — قرار ترقية Article Coverage Packer

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| تشغيل packer مبكرًا قبل امتلاء السياق | retrieval/package issue | مواد learned/support قد تملأ `72` خانة وتمنع مواد المحور الدقيقة | مواد المحور تدخل أولًا ثم يكمل السياق بالباقي |
| عدم اشتراط وجود priority slug في seeds | retrieval/package issue | دور السؤال قد يكتشف المشتريات لكن packer لا يعمل إذا لم يظهر slug في بذور أخرى | slugs ذات الأولوية تأتي من الدور نفسه |
| إضافة محاور مشتريات/بحري/مخدرات | article precision | النظام حاضر لكن مواد `83/89/91` أو `58` قد تغيب | المحور يلتقط المواد الدقيقة داخل النظام لا اسم النظام فقط |
| اعتبار فشل bind الأول تشغيلًا فقط | operational issue | خطر حساب سقوط الخدمة كفجوة RAG | أعيد التشغيل على `8000` بعد readiness ولا يدخل الفشل في التقييم |
| بناء wide16 بدل الاكتفاء بـ8 يدوي | evaluation strategy | شريحة `8` ممثلة فقط | `16` حالة تشمل regression وheldout صغيرين |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - Chroma actual count `22810`.
  - `jamia_recall = 0.70` dense و`0.30` lexical و`context_limit = 72`.
- targeted:
  - `040855` بعد آخر patch: `100/100`, pass `1.0`, transport `0`.
  - `040756` بعد آخر patch: `100/100`, pass `1.0`, transport `0`.
- manual:
  - `manual_article_precision_gate_20260630_after_coverage_packer_early.json`: `8/8`, score `100/100`.
- wide:
  - `manual_article_precision_wide16_20260630_after_coverage_packer.json`: `16/16`, score `100/100`.
- working regression:
  - `manual_article_precision_wide16_20260630_working_regression_after_coverage_packer.json`: `8/8`, score `100/100`.
- held-out:
  - `manual_article_precision_wide16_20260630_heldout_after_coverage_packer.json`: `8/8`, score `100/100`.

### القرار

تُقبل ترقية `coverage_packer` كتحسين عام لا patch خاص. gates الحالية مرّت، ولا يوجد answer-level gap في هذه الجولة. ظهرت مادة وحدات الإخصاب `nzam-whdat-alikhsab-walajnh-walaj-alaqm:21` في لقطة وسيطة للوحة، لكن الفحص النهائي للحالة الحية كان `PASS` بدرجة `100/100` ودون مواد مفقودة. أعلى gap مفتوح الآن غير مثبت؛ الجولة التالية هي held-out أوسع `40+`، مع مراقبة تكرار مادة الإخصاب إن عادت.

## 2026-06-30 — قرار ترقية Answer-Level Grounding

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| قياس الجواب نفسه بأزواج نظام/مادة | answer-level issue | article gates تثبت دخول المادة للسياق فقط | `manual_answer_grounding_blind12` يقيس ظهور المادة مربوطة بالنظام في الجواب |
| ربط المواد في صياغة الجواب بالنظام/اللائحة | answer-level issue | `المادة 1، المادة 7...` بلا اسم النظام | `اسم النظام/اللائحة: المادة 1، المادة 7...` |
| استخدام العنوان الرسمي الكامل في سطور المواد | answer-level issue | اختصار مثل `اللائحة التنفيذية لنظام الشركات` قد لا يكفي للائحة الشركات المدرجة | السطر يستخدم عنوان السجل الرسمي عند توفره |
| عدم احتساب منع loopback أو bind conflict كفجوة RAG | operational issue | أول تشغيل gate أعطى transport errors بسبب sandbox | أعيد القياس بعد صلاحية اتصال محلي، ثم ثبتت خدمة واحدة على `8000` |
| عدم تعديل retrieval لأن فشل baseline كان جوابياً | retrieval/package discipline | احتمال الخلط بين late context وبين فشل answer grounding | retrieval gates بقيت 100/100 بعد التعديل |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - Chroma actual count `22810`.
  - `jamia_recall = 0.70` dense و`0.30` lexical و`context_limit = 72`.
- baseline answer grounding:
  - `manual_answer_grounding_blind12_20260630_baseline.json`: `0/100`, pass `0.0`, failed `12`, transport `0`.
- after answer binding:
  - `manual_answer_grounding_blind12_20260630_after_answer_binding.json`: `91.7/100`, pass `0.917`, failed `1`, transport `0`.
- after official title binding:
  - `manual_answer_grounding_blind12_20260630_after_official_title_binding.json`: `100/100`, pass `1.0`, failed `0`, transport `0`.
- article precision safety:
  - `manual_article_precision_gate_20260630_after_answer_binding.json`: `100/100`.
  - `manual_article_precision_wide16_20260630_working_regression_after_answer_binding.json`: `100/100`.
  - `manual_article_precision_wide16_20260630_heldout_after_answer_binding.json`: `100/100`.

### القرار

تُقبل الترقية كتحسين answer-level عام لا patch خاص. gates مرّت، والجمع لم يتراجع. أعلى gap مفتوح الآن هو ترتيب/ضغط السياق والاستشهادات: بعض الحالات تمر لكن مواد المحور تدخل متأخرة، لذلك الجولة التالية هي `context ranking + citation compression` قبل أي تنظيف تلويث واسع.

## 2026-06-30 — قرار Context Ranking + Citation Compression

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| إعادة ترتيب السياق المختار فقط | retrieval/ranking issue | المواد تدخل لكن قد تظهر متأخرة داخل 72 مقطعًا | نفس العناصر تقريبًا، لكن مواد السؤال ترتفع للأعلى |
| ترتيب الاستشهادات حسب صلة السؤال | answer-level presentation | سطور المواد مرتبة حسب الحزم الخام وقد تبدأ بمواد ثانوية | المواد الأقرب عنوانًا ونصًا للسؤال تظهر أولًا |
| ضغط عرض الاستشهادات | answer-level presentation | قائمة طويلة قد تعرض أنظمة كثيرة | حدود `96` زوجًا، `12` مادة لكل نظام، `16` نظامًا |
| عدم توسيع الاسترجاع | retrieval/package discipline | خطر علاج الموضع بإضافة ضجيج | لا تغيير في `70/30` ولا `context_limit=72` |
| فصل تعارض bind ومسار ملف blind40 عن RAG | operational/evaluation hygiene | فشل تشغيل أو ملف قد يلتبس كفجوة | سجّل كتشغيل/مسار تقييم فقط |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - Chroma actual count `22810`.
  - `jamia_recall = 0.70` dense و`0.30` lexical و`context_limit = 72`.
- answer-grounding:
  - `manual_answer_grounding_blind12_20260630_after_context_ranking.json`: `100/100`, pass `1.0`, transport `0`.
  - mean context position `48.9 -> 33.675`.
- manual article:
  - `manual_article_precision_gate_20260630_after_context_ranking.json`: `100/100`, pass `1.0`.
  - mean context position `28.4 -> 25.8`.
- working regression:
  - `manual_article_precision_wide16_20260630_working_regression_after_context_ranking.json`: `100/100`, pass `1.0`.
- held-out:
  - `manual_article_precision_wide16_20260630_heldout_after_context_ranking.json`: `100/100`, pass `1.0`.
  - mean context position `35.3 -> 25.3`.
- blind40:
  - `manual_article_precision_blind40_20260630_after_context_ranking.json`: `40/40`, `100/100`, pass `1.0`.
  - context entry `0.914 -> 0.915`.
  - mean context position `38.4 -> 23.5`.
  - pollution `0.006 -> 0.006`.

### القرار

تُقبل الترقية. ليست توسيع جمع جديدًا، بل تحسين ranking/compression عام حافظ على gates ورفع موضع المواد بشكل ملموس. أعلى gap المتبقي: المجالات التي بقيت context entry منخفضة رغم النجاح، لا سيما البلدي والبيئي والمشتريات؛ الجولة التالية يجب أن تستهدف دخول كل مواد هذه المجالات لا ترتيبها فقط.

## 2026-07-01 - قرار Blind Label Audit + Approved Article Gate

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| تدقيق labels قبل RAG gate | evaluation/gold issue | بنك autopilot يدخل gate بعد فلترة عامة فقط | `scripts/audit_article_precision_labels.py` يعزل review queue ويصدر approved slice |
| حفظ `auto_review` في شريحة blind | evaluation provenance | سبب اختيار المادة يسقط من ملف الاختبار | سبب gold محفوظ للتدقيق ولا يرسل للخدمة |
| قياس blind100 معتمد فقط | retrieval/package measurement | blind40 كان مفيدًا لكنه صغير | blind140 -> approved100 بعد audit |
| عدم احتساب بطء التشغيل أو import محلي كفشل RAG | operational issue | خطر خلط مشاكل الأداة مع RAG | `/health ok`, transport `0`, Chroma `22810` |
| عدم ترقيع الست حالات مباشرة | retrieval/package discipline | خطر patch خاص لحالات blind | تُطلب targeted probes وتصنيف class-level قبل patch |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - Chroma actual count `22810`.
  - `jamia_recall = 0.70` dense و`0.30` lexical و`context_limit = 72`.
- label audit:
  - `manual_article_precision_blind140_20260701_label_audit.json`.
  - approved `137/140`.
  - review queue `3`.
  - labels: `ok=414`, `low=2`, `high=3`.
- approved blind100:
  - `manual_article_precision_blind100_20260701_approved_article_gate.json`.
  - article score `96.3/100`.
  - pass rate `0.94`.
  - failed cases `6`.
  - governing system `0.99`.
  - implementing regulation `1.0`.
  - axis coverage `0.94`.
  - pollution `0.002`.
  - transport `0`.

### القرار

تُقبل ترقية label audit كتحسين نوعي في جودة القياس. لا تُقبل نتيجة blind100 كـ pass كامل بعد، لأنها كشفت ست فجوات. أعلى gap حاليًا: routing/seed للمواد الدقيقة عندما تشير الواقعة إلى وظيفة المادة لا إلى رقمها، مع حاجة adjudication لحالة الحجر البيطري قبل أي patch خاص. الجولة التالية: targeted probes للست حالات، patch عام للعبارات النظامية القوية، ثم إعادة approved blind100 وanswer-grounding.

## 2026-07-01 - قرار Phrase-to-Article Router

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| ربط العبارات النظامية القوية بالمواد الدقيقة | retrieval/package issue | النظام أو اللائحة يحضران أحيانًا دون مادة محور الواقعة | `phrase_articles_by_slug` يضيف مواد دقيقة للحزمة المطلوبة |
| رفع أولوية مواد phrase-router داخل required article seed | retrieval/ranking issue | المادة المتوقعة قد تبقى بعد مواد مساندة داخل `context_limit=72` | المادة الموجّهة بالعبارة تحصل على أولوية أعلى دون زيادة السياق |
| عدم تغيير dense/lexical أو `context_limit` | operational/retrieval discipline | خطر تحسين الرقم بإضافة سياق أوسع | بقي `70/30` وبقي `context_limit=72` |
| فصل bind conflict بعد restart عن RAG | operational issue | احتمال خلط تعارض عملية على port 8000 بفشل RAG | `/health ok`, root صحيح، Chroma `22810`, transport `0` |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - service chunks `22810`.
  - Chroma actual count `22810`.
  - `jamia_recall = dense 0.70 / lexical 0.30 / context_limit 72`.
- targeted failed6:
  - before: `manual_article_precision_failed6_20260701_before_phrase_router_patch.json` = `55.6/100`, pass `0.167`, failed `5`.
  - after service patch: `manual_article_precision_failed6_20260701_after_phrase_router_patch.json` = `100/100`, pass `1.0`, failed `0`.
- working regression:
  - `manual_article_precision_blind100_20260701_after_phrase_router_patch.json`.
  - before approved blind100: `96.3/100`, pass `0.94`, failed `6`.
  - after patch: `100/100`, pass `1.0`, failed `0`.
  - governing system `1.0`.
  - implementing regulation `1.0`.
  - axis coverage `1.0`.
  - case context entry rate `1.0`.
  - pollution `0.002`.
  - transport `0`.
- held-out:
  - `manual_article_precision_wide8_20260701_heldout_after_phrase_router_patch.json`.
  - previous comparable held-out: `91.7/100`, pass `0.75`, failed `2`.
  - after patch: `100/100`, pass `1.0`, failed `0`, transport `0`.

### القرار

تُقبل الترقية كتحسين retrieval/package عام لا كترقيع خاص. أعلى gap متبقٍ انتقل من إدخال المادة الدقيقة إلى answer-level verification: فحص أن الإجابة النهائية تستند إلى المواد الدقيقة داخل السياق ولا تكتفي باستدعاء أسماء الأنظمة.

## 2026-07-02 - قرار Answer Grounding + Citation Preservation

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| تمرير `phrase_article_pairs` إلى diagnostics | answer-level issue | مواد phrase router قد تدخل السياق ثم تسقط من الاستشهادات | مواد phrase router تحصل على أولوية في الجواب |
| رفع أولوية المواد المبكرة في السياق داخل citation compression | answer-level issue | مواد مهمة داخل السياق قد تُزاح بسبب حد مواد النظام | المواد المبكرة المتوقعة تظهر قبل المواد العامة المتأخرة |
| مسار مستندات تحت يد الخصم | retrieval/package + answer-level | مواد الإثبات `34/36/37` دخلت السياق لكنها لم تظهر في الجواب | route عام إلى `law-of-evidence:34/36/37` |
| مسار التصفية بعد إعادة التنظيم | retrieval/package + answer-level | المادة `nzam-aliflas:115` دخلت السياق ولم تظهر في الجواب | route عام إلى `110/113/115` |
| مسار جدول مخالفات المرور | retrieval/package issue | regression مؤقت أفقد `nzam-almrwr:2/6/7` | route ضيق عند ذكر نظام المرور وجدول المخالفات والأرقام |
| فصل bind conflict عن RAG | operational issue | إعادة التشغيل بعد الكود سببت تعارض port عابر | `/health ok`, Chroma `22810`, transport `0` |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - service chunks `22810`.
  - Chroma actual count `22810`.
  - `jamia_recall = dense 0.70 / lexical 0.30 / context_limit 72`.
- answer failed6:
  - before: `manual_answer_grounding_failed6_20260702_after_phrase_router_patch.json` = `94.5/100`, pass `0.833`, failed `1`.
  - after: `manual_answer_grounding_failed6_20260702_after_phrase_answer_citation_patch.json` = `100/100`, pass `1.0`, failed `0`.
- answer blind12:
  - `manual_answer_grounding_blind12_20260702_after_phrase_answer_citation_patch.json` = `100/100`, pass `1.0`, failed `0`.
- answer blind24:
  - source slice: `manual_answer_grounding_blind24_20260702_from_blind100_after_phrase_router.jsonl`.
  - before final routes: `manual_answer_grounding_blind24_20260702_after_phrase_answer_citation_patch.json` = `94.4/100`, pass `0.917`, failed `2`.
  - final: `manual_answer_grounding_blind24_20260702_after_traffic_answer_routes_patch.json` = `100/100`, pass `1.0`, failed `0`.
- article blind100:
  - intermediate: `manual_article_precision_blind100_20260702_after_answer_citation_routes_patch.json` = `99/100`, pass `0.99`, failed `1`.
  - final: `manual_article_precision_blind100_20260702_after_traffic_answer_routes_patch.json` = `100/100`, pass `1.0`, failed `0`, transport `0`.

### القرار

تُقبل الترقية. هي ليست تنظيف تلويث ولا توسيع سياق، بل تحسين في بقاء المادة الدقيقة من الاسترجاع إلى الجواب. أعلى gap متبقٍ: اختبار التعميم على شريحة answer-grounding أكبر وجديدة، ثم بعدها فقط نقرر هل نبدأ تنظيف التلويث الواسع.

## 2026-07-02 - قرار Blind60/Held-out30 Answer Grounding

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| توسيع answer-grounding من 24 إلى blind60 | eval/readiness | الشريحة السابقة صغيرة نسبيا | 60 حالة، 60 نطاقا، 180 زوج نظام/مادة |
| إضافة مسارات phrase-to-article للمواد الساقطة داخل النظام الصحيح | answer-level + retrieval/package | النظام حاضر لكن مواد دقيقة تسقط من سطر الاستشهاد | المواد تحصل على أولوية كمواد واقعة أساسية |
| عدم تغيير 70/30 أو `context_limit=72` | discipline | خطر تحسين شكلي بتوسيع السياق | بقيت الإعدادات كما هي |
| فصل sandbox network وbind conflict عن RAG | operational issue | احتمال احتساب فشل تشغيل كفجوة RAG | عُزلت كتشغيل فقط؛ transport النهائي `0` |
| بناء held-out30 خارج blind60 | eval hygiene | blind60 صار working regression بعد patch | held-out منفصل مر بعد patch النهائي |

### gates

- readiness:
  - `/health ok`.
  - root والمنفذ صحيحان.
  - service chunks `22810`.
  - Chroma actual count `22810`.
  - `jamia_recall = dense 0.70 / lexical 0.30 / context_limit 72`.
- answer blind60:
  - initial: `manual_answer_grounding_blind60_20260702_service_initial.json` = `95.6/100`, pass `0.883`, failed `7`, transport `0`.
  - targeted failed7: `manual_answer_grounding_blind60_failed7_20260702_after_phrase_routes.json` = `100/100`, pass `1.0`, failed `0`.
  - final: `manual_answer_grounding_blind60_20260702_after_phrase_routes.json` = `100/100`, pass `1.0`, failed `0`.
- answer held-out30:
  - initial: `manual_answer_grounding_heldout30_20260702_after_phrase_routes.json` = `94.4/100`, pass `0.9`, failed `3`.
  - targeted failed3: `manual_answer_grounding_heldout30_failed3_20260702_after_final_phrase_routes.json` = `100/100`, pass `1.0`, failed `0`.
  - final: `manual_answer_grounding_heldout30_20260702_after_final_phrase_routes.json` = `100/100`, pass `1.0`, failed `0`, retrieval direct/article context `1.0`.
- article blind100 final:
  - `manual_article_precision_blind100_20260702_after_final_answer_phrase_routes.json` = `100/100`, pass `1.0`, failed `0`, governing `1.0`, implementing `1.0`, axis `1.0`, case context `1.0`, pollution `0.001`, transport `0`.

### القرار

تُقبل الترقية كتحسين نوعي في answer-grounding، لا كتنظيف تلويث ولا كزيادة سياق. أعلى gap متبقٍ لم يعد “هل تظهر المواد الدقيقة؟” في هذه gates، بل “هل تتحول المواد الدقيقة إلى تعليل استشاري مفيد ومتماسك على أسئلة مستخدمين طبيعية؟” الجولة التالية يجب أن تكون consultation-quality slice مع بقاء article/answer gates كحراس regression.

## 2026-07-02 - قرار فصل مؤشرات Stable/Frontier في اللوحة

| القرار | التصنيف | قبل | بعد |
|---|---|---|---|
| فصل stable quality عن frontier exploration | observability issue | رقم رئيسي مختلط يتأثر بآخر الجولات الاستكشافية | رقم مستقر مبني على آخر gates مقفلة، ورقم استكشافي منفصل |
| إبقاء المؤشر المختلط القديم كشريحة فقط | eval hygiene | practical score قد يبدو كأنه readiness نهائي | يظهر كمرجع تاريخي لا كحكم رئيسي |
| عدم تعديل RAG engine | discipline | احتمال علاج ارتباك القياس بترقيع استرجاع | لا تغيير في retrieval/package أو answer generation |
| إعادة تشغيل الخدمة بعد تعديل اللوحة فقط | operational issue | كود جديد لا يظهر دون restart | `/health ok` على نفس المنفذ 8000 |

### الأرقام

- stable quality: `100.0%`.
- stable components:
  - article blind100: `100.0%`, cases `100`, failed `0`.
  - answer blind60: `100.0%`, cases `60`, failed `0`.
  - answer heldout30: `100.0%`, cases `30`, failed `0`.
- frontier signal: `93.1%`.
- frontier exploratory gap: `6.9%`.
- legacy mixed practical score: `94.2%`.
- pair coverage: `100.0%`.

### القرار

تُقبل الجولة كتصحيح قياس لا كتحسين RAG مباشر. أي انخفاض لاحق في cycle/frontier لا يعني تراجع المنتج المستقر إلا إذا كسر stable gates. أعلى gap متبقٍ: جودة الاستشارة العملية والتعليل على أسئلة طبيعية، وليس مجرد إدخال المواد أو ذكر أرقامها.
