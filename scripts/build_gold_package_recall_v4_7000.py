"""Build a 7000-case gold benchmark for Saudi legal RAG package recall.

v4 keeps the approved 5000-case v3 benchmark intact, then adds:
- 1000 compound-issue stress cases built from multi-axis legal facts.
- 1000 synonym/surface-form stress cases that avoid relying on one wording.

Gold labels are evaluation-only. The service payloads contain only question,
answer_mode, and retrieval_profile.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_gold_package_recall_v3_5000 import (
    REGULATIONS_PATH,
    ROOT,
    SCENARIO_FAMILIES,
    V3_COMPANION_BY_SLUG,
    normalize_space,
    present_slugs,
)


TARGET_CASES = 7000
BASE_CASES_PATH = ROOT / "data" / "eval" / "gold_package_recall_v3_5000" / "gold_package_recall_5000_v3.jsonl"
OUT_DIR = ROOT / "data" / "eval" / "gold_package_recall_v4_7000"
CASES_PATH = OUT_DIR / "gold_package_recall_7000_v4.jsonl"
MANIFEST_PATH = OUT_DIR / "manifest.json"
README_PATH = OUT_DIR / "README.md"

SPLIT_CYCLE = ("dev", "regression", "heldout", "regression", "dev", "heldout", "regression", "heldout")

COMPOUND_STYLES = (
    "قضية مركبة لاختبار الجمع فقط: {scenario}. اجمع كل الأنظمة واللوائح والضوابط لكل محور، ولا تجعل النظام العام بديلاً عن الخاص. {focus}",
    "ملف طويل فيه أكثر من مسألة: {scenario}. ما الحزمة النظامية السعودية الكاملة التي يجب أن يسترجعها RAG؟ {focus}",
    "استعلام gold للتركيبات: {scenario}. المطلوب جمع المراجع الإلزامية والمساندة والمشروطة والمستبعدة. {focus}",
    "مراجع خارجي يختبر الاستيعاب الأفقي لهذه الوقائع: {scenario}. ما الأنظمة واللوائح التي يجب ألا تفوت؟ {focus}",
    "لا تعط فتوى، بل فكك الواقعة إلى مسائل واجمع مصادر كل مسألة: {scenario}. {focus}",
    "صياغة مستخدم غير مرتبة: {scenario}. حدد النظام الخاص لكل محور ثم اللوائح المكملة. {focus}",
    "في مراجعة أولية لنزاع متعدد الأطراف: {scenario}. اجمع مصادر العمال والمنشأة والطرف المتعاقد والجهة المختصة. {focus}",
    "اختبار ضد الانجراف لكلمة سطحية: {scenario}. اجمع الحزمة الخاصة ولا تنجرف إلى نظام عام لا يغطي كل المحاور. {focus}",
    "قضية فيها حقوق والتزامات وإجراءات: {scenario}. ما الحزمة النظامية الشاملة قبل أي جواب؟ {focus}",
    "استرجاع قانوني جامع لهذه الواقعة: {scenario}. افصل النظام الحاكم عن اللوائح التنفيذية والضوابط القطاعية. {focus}",
    "ملف امتثال ومطالبة وتعويض: {scenario}. اجمع الأنظمة السعودية ذات الصلة بكل ذرة قانونية. {focus}",
    "اختبار استيعاب متعدد الذرات: {scenario}. اذكر المراجع التي يعد غيابها نقصاً جوهرياً. {focus}",
    "واقعة مختلطة بين أكثر من مجال: {scenario}. ما المراجع الأساسية والمساندة التي يجب أن تظهر؟ {focus}",
    "سؤال مصمم لكشف نقص الجمع في القضايا المركبة: {scenario}. استدع كل حزمة ولا تكتف بأقوى مجال ظاهر. {focus}",
    "يريد المستخدم ملف مصادر لا جواباً تحليلياً: {scenario}. ما النصوص واللوائح التي يجب جمعها أولاً؟ {focus}",
    "اختبار held-out للتركيب الأفقي: {scenario}. اجمع مصادر المسائل الموضوعية والإجرائية والإثباتية. {focus}",
    "في قضية واحدة اجتمعت عدة أنظمة: {scenario}. استخرج الحزمة القانونية لكل محور. {focus}",
    "راجع هذه الوقائع كما لو كنت تقيم RAG قانونياً سعودياً: {scenario}. ما المصادر الواجبة؟ {focus}",
    "سؤال طويل بقصد إرباك الاسترجاع: {scenario}. المطلوب جمع الحزمة كاملة، لا اختيار مجال واحد. {focus}",
    "صياغة مهنية مختصرة: {scenario}. ما الأنظمة واللوائح والضوابط التي يجب استرجاعها؟ {focus}",
)

COMPOUND_FOCUS = (
    "ركز على عدم إسقاط اللائحة أو الضابط القطاعي.",
    "ركز على منع استبدال النظام الخاص بنظام عام.",
)

SYNONYM_STYLES = (
    "بألفاظ مختلفة عن النصوص النظامية: {scenario}. ما المراجع السعودية التي يجب جمعها؟",
    "المستخدم لم يسم النظام صراحة وقال فقط: {scenario}. استخرج الحزمة القانونية الكاملة.",
    "استعلام عامي/مهني مختلط: {scenario}. ما الأنظمة واللوائح الأقرب؟",
    "اختبار مرادفات لاختبار الاستدعاء الدلالي: {scenario}. اجمع النظام واللائحة والضابط.",
    "صياغة لا تستخدم أسماء الأنظمة غالباً: {scenario}. ما المصادر التي يجب ألا تغيب؟",
    "طلب بحث قانوني بصياغة سطحية: {scenario}. حدد المراجع الإلزامية والمساندة.",
    "نفس القضية بعبارات بديلة: {scenario}. اجمع الحزمة النظامية ولا تعتمد على الكلمات المفتاحية وحدها.",
    "سؤال مستخدم غير متخصص: {scenario}. ما النصوص السعودية التي ينبغي استرجاعها؟",
    "الوقائع التالية صيغت بمرادفات كثيرة: {scenario}. ما النظام الخاص واللوائح المكملة؟",
    "اختبار ضد فشل المرادفات: {scenario}. اجمع المراجع الواجبة التطبيق.",
)

SYNONYM_ANGLES = (
    "تعامل مع الأوصاف العملية لا مع أسماء الأنظمة فقط.",
    "لا تسقط اللائحة إذا لم يذكرها المستخدم صراحة.",
    "استخرج النظام الخاص حتى لو وردت عبارات عامة.",
    "فرّق بين المرجع المركزي والمرجع المساند.",
    "لا تجعل التشابه اللفظي يقود إلى مجال آخر.",
    "افترض أن المستخدم لا يعرف اسم النظام.",
    "اجمع الحزمة قبل أي تحليل أو ترجيح.",
    "انتبه للحقوق والجزاءات والإجراءات معاً.",
    "لا تهمل الضوابط الفنية أو القطاعية.",
    "اعتبر كل محور واقعة مستقلة داخل السؤال.",
    "لا تكتف بمرجع واحد إذا تعددت المسائل.",
    "استحضر اللوائح المكملة عند وجود امتثال.",
    "استحضر الإثبات عند وجود رسائل أو مستندات.",
    "استحضر الاختصاص عند وجود مطالبة أو تظلم.",
    "صنّف المرجع المشروط ولا تجعله بديلاً.",
    "اختبر الاستيعاب الدلالي لا المطابقة الحرفية.",
    "لا تسقط المرجع الخاص بسبب وجود كلمات تجارية عامة.",
)

SYNONYM_REPLACEMENTS = (
    ("موظف", ("عامل", "أجير", "أحد العاملين", "موظف")),
    ("موظفون", ("عمال", "عاملون", "موظفو المنشأة", "طاقم العمل")),
    ("عامل", ("موظف", "أجير", "شخص يعمل لدى المنشأة", "عامل")),
    ("الرواتب", ("الأجور", "المستحقات الشهرية", "المرتبات", "الرواتب")),
    ("تأخر", ("تأجل", "لم يصرف في موعده", "تأخر", "تراخى")),
    ("تأخرت", ("تأجلت", "لم تصرف في موعدها", "تأخرت", "تراخت")),
    ("خصم", ("حسم", "اقتطاع", "تنزيل مبلغ", "خصم")),
    ("عقد", ("اتفاق", "تعاقد", "محرر", "عقد")),
    ("فسخ", ("إنهاء", "إلغاء", "حل الرابطة", "فسخ")),
    ("إلغاء", ("فسخ", "إنهاء", "إبطال الطلب", "إلغاء")),
    ("استرجاع", ("استرداد", "رد المبلغ", "إعادة المقابل", "استرجاع")),
    ("بيانات", ("معلومات شخصية", "معطيات", "سجلات", "بيانات")),
    ("تسرب", ("انكشاف", "تسريب", "اختراق أدى لخروج", "تسرب")),
    ("مزود سحابي", ("شركة استضافة خارجية", "منصة تخزين أجنبية", "مورد تقني", "مزود سحابي")),
    ("مستهلك", ("عميل", "مشترٍ", "مستخدم الخدمة", "مستهلك")),
    ("متجر إلكتروني", ("منصة بيع عبر الإنترنت", "محل رقمي", "موقع بيع", "متجر إلكتروني")),
    ("فاتورة", ("مستند بيع", "إشعار مطالبة", "فاتورة", "وثيقة ضريبية")),
    ("شركة", ("منشأة", "كيان تجاري", "مؤسسة", "شركة")),
    ("مدير", ("مسؤول فعلي", "من يتولى الإدارة", "القائم على التشغيل", "مدير")),
    ("منافسة", ("طرح شراء", "عطاء", "مناقصة", "منافسة")),
    ("مقاول", ("منفذ أعمال", "متعهد", "شركة تنفيذ", "مقاول")),
    ("تعويض", ("جبر ضرر", "مقابل الأضرار", "مطالبة مالية", "تعويض")),
    ("غرامة", ("جزاء مالي", "عقوبة مالية", "مخالفة مالية", "غرامة")),
    ("إثبات", ("بينة", "دليل", "مستندات ورسائل", "إثبات")),
    ("اختصاص", ("جهة نظر النزاع", "المسار القضائي", "الطريق الإجرائي", "اختصاص")),
)


COMPOUND_FAMILIES: list[dict[str, Any]] = [
    {
        "family_id": "compound_contracting_labor_insurance_concealment_private_project",
        "domain": "compound_labor_commercial",
        "scenario": "شركة مقاولات متوسطة لديها سعوديون وغير سعوديين في مواقع إنشائية خطرة؛ تأخرت الأجور، فرضت إجازات بلا أجر، نقلت عمالاً بين المدن، أصيب عامل بسبب سقوط معدة وضعف التدريب، سجلت سعودياً في التأمينات بأجر أقل وأدرجت أسماء لا تعمل فعلياً، وظهر أن غير سعودي يدير الحسابات والعقود كملاك فعليين، ثم تعثرت في عقد مشروع خاص بسبب دفعات المالك وارتفاع المواد",
        "core": ["labor-law", "nzam-altamynat-alajtmaayh", "nzam-mkafhh-altstr", "civil-transactions-law"],
        "companions": ["labor-implementing-regulation", "labor-violations-penalties-table", "wage-protection-rules", "labor-contract-documentation-rules", "nzam-alsjl-altjary", "companies-law", "nzam-almhakm-altjaryh", "law-of-evidence"],
        "optional": ["nzam-tsnyf-almqawlyn"],
        "excluded": ["nzam-aliflas", "government-tenders-and-procurement-law"],
    },
    {
        "family_id": "compound_ecommerce_device_fraud_pdpl_vat",
        "domain": "compound_ecommerce_tax_privacy",
        "scenario": "منصة باعت جهازاً طبياً منزلياً بادعاءات علاجية، تأخر التسليم ورفضت رد المبلغ، جمعت رقم الهوية وتاريخ الميلاد بلا حاجة، استخدمت رقم الجوال في التسويق، وأصدرت فاتورة إلكترونية ناقصة الحقول الضريبية",
        "core": ["e-commerce-law", "commercial-fraud-law", "personal-data-protection-law", "nzam-drybh-alqymh-almdafh"],
        "companions": ["ecommerce-implementing-regulation", "pdpl-implementing-regulation", "nzam-alajhzh-walmstlzmat-altbyh", "zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls", "law-of-evidence"],
        "optional": ["nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
        "excluded": ["companies-law"],
    },
    {
        "family_id": "compound_listed_company_disclosure_insider_competition_related_party",
        "domain": "compound_cma_competition_companies",
        "scenario": "شركة مساهمة مدرجة صححت نتائج مالية جوهرية بعد إعلان متفائل فهبط السهم، وباع عضو مجلس أسهماً قبل التصحيح، ووافق المجلس على عقد توريد مع طرف للرئيس التنفيذي فيه مصلحة، كما تستحوذ الشركة على منافس صغير وتفرض حصرية على العملاء",
        "core": ["companies-law", "nzam-alswq-almalyh", "nzam-almnafsh"],
        "companions": ["companies-implementing-regulation", "cma-corporate-governance-regulations", "cma-continuing-obligations-rules", "cma-securities-offering-rules", "law-of-evidence"],
        "optional": ["civil-transactions-law"],
        "excluded": ["e-commerce-law", "labor-law"],
    },
    {
        "family_id": "compound_procurement_conflict_bidrigging_contractor_labor",
        "domain": "compound_procurement_labor_competition",
        "scenario": "مقاول في منافسة حكومية اشتكى من ترسية على مورد تربطه قرابة بعضو لجنة الفحص، وظهرت شبهة اتفاق موردين على رفع الأسعار، وبعد الترسية تأخر تسليم الموقع وتعاقد المقاول من الباطن، وفي المشروع تأخرت أجور عمال الموقع",
        "core": ["government-tenders-and-procurement-law", "nzam-almnafsh", "labor-law"],
        "companions": ["government-procurement-implementing-regulation", "procurement-conflict-of-interest-regulation", "procurement-conduct-ethics-regulation", "nzam-almrafaat-amam-dywan-almzalm", "labor-implementing-regulation", "wage-protection-rules", "labor-violations-penalties-table"],
        "optional": ["law-of-evidence"],
        "excluded": ["nzam-aliflas"],
    },
    {
        "family_id": "compound_health_app_breach_cloud_marketing_cyber",
        "domain": "compound_privacy_health_cyber",
        "scenario": "تطبيق صحي تابع لمنشأة خاصة يجمع بيانات صحية وهوية وموقع، ينقلها لمزود سحابي خارج المملكة، يشاركها مع شركة تسويق، ثم حدث اختراق وتسرب ولم يبلغ المستخدمين في الوقت المناسب",
        "core": ["personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation", "anti-cybercrime-law"],
        "companions": ["alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh", "law-of-evidence"],
        "optional": ["nzam-aldman-alshy-altaawny"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_private_health_malpractice_pdpl_insurance",
        "domain": "compound_health_privacy_insurance",
        "scenario": "مريض في مستشفى خاص تعرض لخطأ طبي ورفضت المنشأة تسليمه ملفه الطبي، ثم شاركت بياناته مع شركة تأمين رفضت المطالبة بحجة استثناءات غير واضحة في الوثيقة",
        "core": ["alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh", "personal-data-protection-law", "nzam-mraqbh-shrkat-altamyn-altaawny"],
        "companions": ["pdpl-implementing-regulation", "nzam-aldman-alshy-altaawny", "civil-transactions-law", "law-of-evidence"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_offplan_broker_title_vat_ret",
        "domain": "compound_real_estate_tax",
        "scenario": "مشتري حجز وحدة على الخارطة عبر وسيط، تأخر المطور وغير المواصفات، ويزعم المشتري أن الدفعات لم تدخل حساب الضمان، كما نشأ خلاف عن ضريبة التصرفات العقارية وضريبة القيمة المضافة ورسوم الوساطة والتسجيل",
        "core": ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth", "real-estate-brokerage-law", "nzam-drybh-altsrfat-alaqaryh", "nzam-drybh-alqymh-almdafh"],
        "companions": ["civil-transactions-law", "nzam-altsjyl-alayny-llaqar", "zatca-vat-implementing-regulation", "law-of-evidence"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["nzam-mlkyh-alwhdat-alaqaryh-wfrzha-wadarha"],
    },
    {
        "family_id": "compound_fintech_payment_cyber_pdpl_bank",
        "domain": "compound_finance_privacy_cyber",
        "scenario": "عميل محفظة دفع إلكترونية تعرض لتحويلات غير مصرح بها بعد اختراق حسابه، والشركة المرخصة احتفظت بسجلات ناقصة وشاركت بيانات الموقع مع شريك تحليلي، والبنك تأخر في معالجة الاعتراض",
        "core": ["nzam-almdfwaat-wkhdmatha", "nzam-albnk-almrkzy-alsawdy", "anti-cybercrime-law", "personal-data-protection-law"],
        "companions": ["nzam-mraqbh-albnwk", "pdpl-implementing-regulation", "law-of-evidence"],
        "optional": ["civil-transactions-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_bankruptcy_employees_preference_insurance",
        "domain": "compound_insolvency_labor_insurance",
        "scenario": "شركة متعثرة توقفت عن سداد الديون، دفعت لمورد قريب من المدير قبل طلب إعادة التنظيم، تأخرت رواتب العمال، وسجلت بعض الموظفين في التأمينات بأجور أقل من الحقيقي",
        "core": ["nzam-aliflas", "labor-law", "nzam-altamynat-alajtmaayh"],
        "companions": ["bankruptcy-implementing-regulation", "companies-law", "labor-implementing-regulation", "wage-protection-rules", "law-of-evidence"],
        "optional": ["execution-law"],
        "excluded": ["government-tenders-and-procurement-law"],
    },
    {
        "family_id": "compound_food_ecommerce_fraud_pdpl_recall",
        "domain": "compound_food_ecommerce_privacy",
        "scenario": "متجر إلكتروني باع منتجاً غذائياً ملوثاً ببطاقة بيانات مضللة، ثم أرسل رسائل تسويقية للمشترين وشارك بياناتهم مع شركة إعلان، وظهرت بلاغات تسمم تستدعي السحب",
        "core": ["nzam-alghdhaa", "nzam-alhyyh-alaamh-llghdhaa-waldwaa", "commercial-fraud-law", "e-commerce-law", "personal-data-protection-law"],
        "companions": ["ecommerce-implementing-regulation", "pdpl-implementing-regulation", "law-of-evidence"],
        "optional": ["anti-cybercrime-law"],
        "excluded": ["copyright-law"],
    },
    {
        "family_id": "compound_medical_device_import_ecommerce_vat",
        "domain": "compound_medical_device_tax_consumer",
        "scenario": "مورد أجهزة طبية استورد جهازاً دون استيفاء متطلبات الهيئة وباعه عبر منصة إلكترونية بضمان مضلل، ثم أصدر فاتورة ضريبية ناقصة وظهرت بلاغات عطل وسلامة",
        "core": ["nzam-alajhzh-walmstlzmat-altbyh", "nzam-alhyyh-alaamh-llghdhaa-waldwaa", "e-commerce-law", "nzam-drybh-alqymh-almdafh"],
        "companions": ["commercial-fraud-law", "ecommerce-implementing-regulation", "zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "law-of-evidence"],
        "optional": ["personal-data-protection-law"],
        "excluded": ["companies-law"],
    },
    {
        "family_id": "compound_franchise_register_trademark_concealment",
        "domain": "compound_franchise_register_concealment",
        "scenario": "مستثمر أخذ امتياز مطاعم دون وثيقة إفصاح كافية ولم يقيد الامتياز، واستعمل اسماً تجارياً وعلامة محل نزاع، ثم ظهر أن غير سعودي يدير النشاط فعلياً من وراء المالك السعودي",
        "core": ["nzam-alamtyaz-altjary", "nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh", "nzam-mkafhh-altstr"],
        "companions": ["companies-law", "civil-transactions-law", "nzam-almhakm-altjaryh", "law-of-evidence"],
        "optional": ["anti-money-laundering-law"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "compound_agency_competition_trademark_ecommerce",
        "domain": "compound_agency_competition_ip",
        "scenario": "وكيل تجاري حصري لمنتج أجنبي لم يسجل الوكالة، ثم عين الموكل موزعاً آخر وباع منتجات بعلامة مشابهة عبر متجر إلكتروني مع شرط حصرية يمنع المنافسين",
        "core": ["nzam-alwkalat-altjaryh", "nzam-almnafsh", "nzam-alalamat-altjaryh", "e-commerce-law"],
        "companions": ["civil-transactions-law", "nzam-almhakm-altjaryh", "qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh", "commercial-fraud-law", "law-of-evidence"],
        "optional": ["nzam-alsjl-altjary"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "compound_arbitration_award_execution_electronic_evidence",
        "domain": "compound_civil_evidence_enforcement",
        "scenario": "صدر حكم تحكيم في عقد تجاري إلكتروني وطلب الدائن تنفيذه مع سند لأمر إلكتروني ورسائل إقرار، بينما يدفع المدين ببطلان شرط التحكيم وتزوير التوقيع الإلكتروني",
        "core": ["nzam-althkym", "execution-law", "nzam-alawraq-altjaryh", "electronic-transactions-law", "law-of-evidence"],
        "companions": ["execution-implementing-regulation", "civil-transactions-law", "nzam-almhakm-altjaryh"],
        "optional": ["nzam-altkalyf-alqdayyh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_civil_whatsapp_vat_einvoice_execution",
        "domain": "compound_civil_tax_evidence",
        "scenario": "مورد يطالب بقيمة توريد مثبت بعرض سعر وواتساب وبريد إلكتروني، وأصدر فواتير إلكترونية ناقصة، والمدين ينكر العقد والتوقيع ويطلب الدائن لاحقاً التنفيذ",
        "core": ["civil-transactions-law", "law-of-evidence", "electronic-transactions-law", "nzam-drybh-alqymh-almdafh"],
        "companions": ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls", "nzam-almhakm-altjaryh", "execution-law"],
        "optional": ["nzam-altkalyf-alqdayyh"],
        "excluded": ["nzam-aliflas"],
    },
    {
        "family_id": "compound_environment_municipal_civil_health",
        "domain": "compound_environment_municipal",
        "scenario": "مصنع داخل حي سكني يمارس نشاطاً مقلقاً ومضراً بالصحة دون ترخيص بلدي وبيئي كاف، صرف مخلفات في وادٍ، وتضرر سكان ومزارع مجاورة ويطلبون وقف النشاط والتعويض",
        "core": ["nzam-albyyh", "nzam-ijraaat-altrakhys-albldyh", "nzam-alanshth-almqlqh-llrahh-aw-alkhtrh-aw-almdrh-balshh-aw-albyyh", "civil-transactions-law"],
        "companions": ["nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"],
        "optional": ["alnzam-alshy"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "compound_family_abuse_child_harassment_evidence",
        "domain": "compound_family_criminal_protection",
        "scenario": "أم تطلب حضانة ونفقة وتدابير عاجلة لطفل ظهرت عليه آثار إيذاء في المدرسة، مع ادعاء تحرش من قريب ورسائل تثبت التهديد والامتناع عن النفقة",
        "core": ["personal-status-law", "nzam-hmayh-altfl", "protection-from-abuse-law", "nzam-mkafhh-jrymh-althrsh"],
        "companions": ["criminal-procedure-law", "law-of-evidence"],
        "optional": ["nzam-alahdath"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "compound_media_telecom_pdpl_copyright",
        "domain": "compound_media_telecom_ip_privacy",
        "scenario": "منصة إعلامية رقمية تبث مقاطع ودورات منسوخة دون إذن، ترسل رسائل تسويقية لمتابعين باستخدام بيانات الموقع وسجل المشاهدة، وتعرض محتوى مرئياً دون ترخيص واضح",
        "core": ["copyright-law", "communications-and-information-technology-law", "personal-data-protection-law", "nzam-alialam-almryy-walmsmwa"],
        "companions": ["pdpl-implementing-regulation", "nzam-almtbwaat-walnshr", "law-of-evidence", "nzam-almhakm-altjaryh"],
        "optional": ["e-commerce-law"],
        "excluded": ["nzam-drybh-alqymh-almdafh"],
    },
    {
        "family_id": "compound_aml_real_estate_broker_bank_pdpl",
        "domain": "compound_aml_real_estate_finance",
        "scenario": "وسيط عقاري ومؤسسة مالية لاحظا تحويلات مجزأة ومالكاً مستفيداً غير واضح في صفقة عقار، مع جمع بيانات هوية واسعة ومشاركة بعضها مع أطراف خارجية وتأخر الإبلاغ عن الاشتباه",
        "core": ["anti-money-laundering-law", "real-estate-brokerage-law", "personal-data-protection-law"],
        "companions": ["nzam-mraqbh-albnwk", "pdpl-implementing-regulation", "civil-transactions-law", "law-of-evidence"],
        "optional": ["nzam-drybh-altsrfat-alaqaryh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_movable_security_bankruptcy_execution_civil",
        "domain": "compound_finance_security_enforcement",
        "scenario": "شركة رهنت معدات ومخزوناً ضماناً لقرض ثم باعت بعضها، وبعد التعثر طالب الدائن المضمون بالأولوية والتنفيذ بينما بدأ دائن آخر إجراءات إفلاس",
        "core": ["nzam-dman-alhqwq-balamwal-almnqwlh", "civil-transactions-law", "execution-law", "nzam-aliflas"],
        "companions": ["bankruptcy-implementing-regulation", "law-of-evidence"],
        "optional": ["nzam-alrhn-altjary"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_contractor_classification_private_and_government_boundary",
        "domain": "compound_construction_procurement_boundary",
        "scenario": "مقاول مصنف ينفذ مشروعاً خاصاً وآخر حكومياً؛ في الخاص ظهرت عيوب إنشائية، وفي الحكومي استبعد عرضه بسبب التصنيف وتظلم من قرار الترسية والتقييم",
        "core": ["civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy", "nzam-tsnyf-almqawlyn", "government-tenders-and-procurement-law"],
        "companions": ["government-procurement-implementing-regulation", "nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["nzam-aliflas"],
    },
    {
        "family_id": "compound_employment_remote_qiwa_pdpl_social",
        "domain": "compound_labor_privacy_insurance",
        "scenario": "منشأة طلبت من موظفين العمل عن بعد دون توثيق واضح في قوى، راقبت مواقعهم وأجهزتهم، أخرت أجورهم، وسجلت بعضهم في التأمينات بأجر مختلف عن الأجر الفعلي",
        "core": ["labor-law", "personal-data-protection-law", "nzam-altamynat-alajtmaayh"],
        "companions": ["labor-implementing-regulation", "labor-contract-documentation-rules", "wage-protection-rules", "labor-violations-penalties-table", "pdpl-implementing-regulation", "law-of-evidence"],
        "optional": ["communications-and-information-technology-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_cosmetics_influencer_ecommerce_pdpl_fraud",
        "domain": "compound_cosmetics_media_ecommerce",
        "scenario": "مؤثر أعلن عن مستحضر تجميل بادعاءات علاجية عبر منصة بيع، تسبب المنتج في حساسية شديدة، وجمعت المنصة بيانات المشترين للتسويق دون إفصاح كاف",
        "core": ["nzam-mntjat-altjmyl", "nzam-alhyyh-alaamh-llghdhaa-waldwaa", "e-commerce-law", "commercial-fraud-law", "personal-data-protection-law"],
        "companions": ["ecommerce-implementing-regulation", "pdpl-implementing-regulation", "nzam-alialam-almryy-walmsmwa", "law-of-evidence"],
        "optional": ["copyright-law"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "compound_events_juvenile_cyber_harassment",
        "domain": "compound_criminal_child_cyber",
        "scenario": "حدث شارك في اختراق حساب زميل وابتزازه بنشر صور، وتخلل الواقعة تحرش وتهديد، والمدرسة تأخرت في الإبلاغ وحفظ الأدلة",
        "core": ["nzam-alahdath", "anti-cybercrime-law", "nzam-mkafhh-jrymh-althrsh", "nzam-hmayh-altfl"],
        "companions": ["criminal-procedure-law", "law-of-evidence", "protection-from-abuse-law"],
        "optional": ["personal-data-protection-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "compound_securities_offering_marketing_pdpl",
        "domain": "compound_cma_privacy_marketing",
        "scenario": "شركة تسوق طرح أوراق مالية لمستثمرين أفراد برسائل مستهدفة بنيت على بياناتهم الشخصية، مع إعلان ناقص عن المخاطر وتداول سابق من مطلعين",
        "core": ["nzam-alswq-almalyh", "cma-securities-offering-rules", "personal-data-protection-law"],
        "companions": ["cma-continuing-obligations-rules", "cma-corporate-governance-regulations", "pdpl-implementing-regulation", "law-of-evidence"],
        "optional": ["companies-law"],
        "excluded": ["e-commerce-law"],
    },
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def slug_list(values: list[str], all_slugs: set[str]) -> list[str]:
    return present_slugs(values, all_slugs)


def clean_case(case: dict[str, Any]) -> dict[str, Any]:
    row = dict(case)
    row["source_case_id"] = case.get("question_id")
    row["source_note"] = f"base_{case.get('source_note', 'gold_package_recall_5000_v3')}"
    for key in ("question_id", "benchmark_category", "question_type", "split", "expected_regulations", "allowed_regulations"):
        row.pop(key, None)
    return row


def make_compound_cases(all_slugs: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for family in COMPOUND_FAMILIES:
        core = slug_list(family["core"], all_slugs)
        companions = slug_list(family["companions"], all_slugs)
        optional = slug_list(family.get("optional", []), all_slugs)
        excluded = slug_list(family.get("excluded", []), all_slugs)
        missing_core = [slug for slug in family["core"] if slug not in all_slugs]
        if missing_core:
            skipped.append({"family_id": family["family_id"], "missing_core": missing_core})
            continue
        for variant in range(40):
            style = COMPOUND_STYLES[variant % len(COMPOUND_STYLES)]
            focus = COMPOUND_FOCUS[(variant // len(COMPOUND_STYLES)) % len(COMPOUND_FOCUS)]
            cases.append(
                {
                    "domain": family["domain"],
                    "scenario_family_id": family["family_id"],
                    "scenario_variant": variant + 1,
                    "question": normalize_space(style.format(scenario=family["scenario"], focus=focus)),
                    "required_core_regulations": core,
                    "required_companion_regulations": companions,
                    "optional_regulations": optional,
                    "excluded_regulations": excluded,
                    "expected_articles": [],
                    "gold_answer_summary": "قضية تركيبية متعددة المسائل؛ يجب جمع: " + "، ".join(core + companions),
                    "source_note": "compound_issue_stress_v4",
                }
            )
    return cases, skipped


def apply_synonym_variant(text: str, variant: int) -> str:
    out = text
    for index, (needle, replacements) in enumerate(SYNONYM_REPLACEMENTS):
        if needle in out:
            out = out.replace(needle, replacements[(variant + index) % len(replacements)])
    surface_prefixes = (
        "المستخدم وصفها هكذا دون أسماء أنظمة: ",
        "بصياغة غير قانونية دقيقة: ",
        "بكلمات قريبة من الواقع العملي: ",
        "في ملف مختلط وعباراته غير منتظمة: ",
    )
    return normalize_space(surface_prefixes[variant % len(surface_prefixes)] + out)


def make_synonym_cases(all_slugs: set[str]) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for family in SCENARIO_FAMILIES:
        seeds.append(
            {
                "family_id": family["family_id"],
                "domain": family["domain"],
                "scenario": family["scenario"],
                "core": family["core"],
                "companions": family.get("companions", []),
                "optional": family.get("optional", []),
                "excluded": family.get("excluded", []),
            }
        )
    for family in COMPOUND_FAMILIES:
        seeds.append(
            {
                "family_id": family["family_id"],
                "domain": family["domain"],
                "scenario": family["scenario"],
                "core": family["core"],
                "companions": family.get("companions", []),
                "optional": family.get("optional", []),
                "excluded": family.get("excluded", []),
            }
        )

    cases: list[dict[str, Any]] = []
    variant = 0
    while len(cases) < 1000:
        seed = seeds[variant % len(seeds)]
        core = slug_list(seed["core"], all_slugs)
        if not core:
            variant += 1
            continue
        companions = slug_list(seed["companions"], all_slugs)
        optional = slug_list(seed["optional"], all_slugs)
        excluded = slug_list(seed["excluded"], all_slugs)
        scenario = apply_synonym_variant(seed["scenario"], variant)
        style = SYNONYM_STYLES[(variant + variant // len(seeds)) % len(SYNONYM_STYLES)]
        angle = SYNONYM_ANGLES[variant % len(SYNONYM_ANGLES)]
        cases.append(
            {
                "domain": seed["domain"],
                "scenario_family_id": seed["family_id"],
                "scenario_variant": variant + 1,
                "question": normalize_space(style.format(scenario=scenario) + " " + angle),
                "required_core_regulations": core,
                "required_companion_regulations": companions,
                "optional_regulations": optional,
                "excluded_regulations": excluded,
                "expected_articles": [],
                "gold_answer_summary": "اختبار مرادفات؛ يجب ألا يعتمد الاسترجاع على لفظ واحد: " + "، ".join(core + companions),
                "source_note": "synonym_surface_stress_v4",
            }
        )
        variant += 1
    return cases


def assign_ids(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        row = dict(case)
        row["question_id"] = f"gpr_v4_{index:04d}"
        row["benchmark_category"] = f"gold_package_recall_v4_{row.get('domain') or 'unknown'}"
        row["question_type"] = f"package_recall_{SPLIT_CYCLE[(index - 1) % len(SPLIT_CYCLE)]}"
        row["split"] = SPLIT_CYCLE[(index - 1) % len(SPLIT_CYCLE)]
        expected = list(dict.fromkeys(row.get("required_core_regulations", []) + row.get("required_companion_regulations", [])))
        row["expected_regulations"] = expected
        row["allowed_regulations"] = list(dict.fromkeys(expected + row.get("optional_regulations", [])))
        row["min_expected_regulation_hits"] = len(expected)
        row["min_expected_article_hits"] = 0
        row["expected_behavior"] = "answer"
        out.append(row)
    return out


def write_payloads_and_curl(cases: list[dict[str, Any]]) -> None:
    payload_dir = OUT_DIR / "payloads_all"
    responses_dir = OUT_DIR / "responses_all"
    config_path = OUT_DIR / "curl_all.config"
    payload_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for case in cases:
        qid = case["question_id"]
        payload = {
            "question": case["question"],
            "answer_mode": "benchmark",
            "retrieval_profile": "jamia_recall",
        }
        payload_path = payload_dir / f"{qid}.json"
        response_path = responses_dir / f"{qid}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        if lines:
            lines.append("next")
        lines.extend(
            [
                "max-time = 120",
                'header = "Content-Type: application/json"',
                f"data-binary = @{payload_path.relative_to(ROOT)}",
                f"output = {response_path.relative_to(ROOT)}",
                "url = http://127.0.0.1:8000/internal/rag/query",
            ]
        )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    regs = json.loads(REGULATIONS_PATH.read_text(encoding="utf-8"))
    all_slugs = {row["slug"] for row in regs if row.get("slug")}

    base_cases = [clean_case(case) for case in load_jsonl(BASE_CASES_PATH)]
    compound_cases, skipped_compound = make_compound_cases(all_slugs)
    synonym_cases = make_synonym_cases(all_slugs)
    cases = assign_ids(base_cases + compound_cases + synonym_cases)

    if len(cases) != TARGET_CASES:
        raise RuntimeError(f"Expected {TARGET_CASES} cases, got {len(cases)}")
    if any(not case.get("required_core_regulations") for case in cases):
        raise RuntimeError("At least one case has no required core regulations")
    duplicate_questions = len(cases) - len({case["question"] for case in cases})
    question_counts = Counter(case["question"] for case in cases)
    new_duplicate_rows = sum(
        1
        for case in cases
        if case.get("source_note") in {"compound_issue_stress_v4", "synonym_surface_stress_v4"}
        and question_counts[case["question"]] > 1
    )

    with CASES_PATH.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    write_payloads_and_curl(cases)

    core_slugs = Counter(slug for case in cases for slug in case.get("required_core_regulations", []))
    companion_slugs = Counter(slug for case in cases for slug in case.get("required_companion_regulations", []))
    source_notes = Counter(case.get("source_note") for case in cases)
    family_counts = Counter(case.get("scenario_family_id") for case in cases if case.get("scenario_family_id"))
    domains = Counter(case.get("domain") for case in cases)
    splits = Counter(case.get("split") for case in cases)

    manifest = {
        "benchmark_id": "gold_package_recall_7000_v4",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_cases": TARGET_CASES,
        "cases": len(cases),
        "base_v3_cases": len(base_cases),
        "compound_issue_stress_cases": len(compound_cases),
        "synonym_surface_stress_cases": len(synonym_cases),
        "compound_families": len(COMPOUND_FAMILIES),
        "scenario_families_total": len(family_counts),
        "regulations_available": len(all_slugs),
        "regulations_covered_as_core": len(core_slugs),
        "regulations_covered_as_companion": len(companion_slugs),
        "split_counts": dict(splits),
        "domain_counts": dict(domains),
        "source_note_counts": dict(source_notes),
        "scenario_family_counts": dict(family_counts),
        "duplicate_questions": duplicate_questions,
        "duplicate_questions_in_new_v4_layers": new_duplicate_rows,
        "duplicate_questions_inherited_from_base_v3": duplicate_questions if new_duplicate_rows == 0 else None,
        "skipped_compound_families": skipped_compound,
        "anti_leakage": {
            "service_payload_fields": ["question", "answer_mode", "retrieval_profile"],
            "gold_labels_used_only_offline": True,
            "do_not_import_into_rag_engine": True,
        },
        "design_notes": [
            "The 5000 v3 cases remain unchanged as the base layer.",
            "The 1000 compound cases target multi-issue decomposition failures.",
            "The 1000 synonym cases target surface-form and paraphrase failures without gold-label leakage.",
            "Collection scoring still records but does not penalize contamination; purity is a separate benchmark layer.",
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    README_PATH.write_text(
        "\n".join(
            [
                "# Gold Package Recall 7000 v4",
                "",
                "معيار ذهبي خارجي جديد مبني فوق v3.",
                "",
                "## التركيب",
                "",
                f"- قاعدة v3 المعتمدة: `{len(base_cases)}`",
                f"- اختبارات تركيبية متعددة المسائل: `{len(compound_cases)}`",
                f"- اختبارات مرادفات وصياغات سطحية: `{len(synonym_cases)}`",
                f"- الإجمالي: `{len(cases)}`",
                "",
                "## الهدف",
                "",
                "- اختبار تفكيك القضية المركبة إلى مسائل قانونية.",
                "- اختبار عدم الاعتماد على لفظ واحد أو اسم النظام الصريح.",
                "- الحفاظ على منع التغشيش: الخدمة لا ترى إلا السؤال.",
                "",
                "## الملفات",
                "",
                "- `gold_package_recall_7000_v4.jsonl`",
                "- `manifest.json`",
                "- `payloads_all/`",
                "- `curl_all.config`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Wrote {CASES_PATH}")


if __name__ == "__main__":
    main()
