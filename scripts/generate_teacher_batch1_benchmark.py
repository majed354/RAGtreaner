from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path("/Users/majd/Desktop/codex/شات الاستشارات")
STRUCTURED_DIR = ROOT / "data" / "structured"
EVAL_DIR = ROOT / "data" / "eval"

WORKING_OUTPUT = EVAL_DIR / "legal_teacher_batch1_working_set_v1.jsonl"
HELDOUT_OUTPUT = EVAL_DIR / "legal_teacher_batch1_heldout_set_v1.jsonl"
MANIFEST_OUTPUT = EVAL_DIR / "legal_teacher_batch1_manifest_v1.json"
PLAN_OUTPUT = EVAL_DIR / "TEACHER_BATCH1_100_PLAN_V1.md"

WORKING_VARIANTS = ("anchor", "working_a", "working_b")
HELDOUT_VARIANTS = ("heldout",)
DIFFICULTY_BY_VARIANT = {
    "anchor": "medium",
    "working_a": "medium",
    "working_b": "hard",
    "heldout": "hard",
}


def load_regulation_slugs() -> set[str]:
    rows = json.loads((STRUCTURED_DIR / "regulations.json").read_text(encoding="utf-8"))
    return {str(row["slug"]) for row in rows}


def load_article_map() -> dict[str, set[int]]:
    article_map: dict[str, set[int]] = {}
    with (STRUCTURED_DIR / "articles.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            slug = str(row["regulation_slug"])
            article_map.setdefault(slug, set()).add(int(row["article_index"]))
    return article_map


def make_bundle(
    *,
    bundle_id: str,
    family: str,
    title: str,
    question_type: str,
    expected_regulations: list[str],
    allowed_regulations: list[str],
    expected_articles: list[int],
    min_expected_regulation_hits: int,
    min_expected_article_hits: int,
    contamination_traps: list[str],
    sub_issues: list[dict[str, Any]],
    notes: str,
    questions: dict[str, str],
) -> dict[str, Any]:
    return {
        "bundle_id": bundle_id,
        "family": family,
        "title": title,
        "question_type": question_type,
        "expected_regulations": expected_regulations,
        "allowed_regulations": allowed_regulations,
        "expected_articles": expected_articles,
        "min_expected_regulation_hits": min_expected_regulation_hits,
        "min_expected_article_hits": min_expected_article_hits,
        "contamination_traps": contamination_traps,
        "sub_issues": sub_issues,
        "notes": notes,
        "questions": questions,
    }


BUNDLES: list[dict[str, Any]] = [
    make_bundle(
        bundle_id="b01_labor_resignation_benefits",
        family="labor",
        title="الاستقالة مع مكافأة نهاية الخدمة ورصيد الإجازات والعمل الإضافي",
        question_type="multi_issue",
        expected_regulations=["labor-law"],
        allowed_regulations=["labor-law"],
        expected_articles=[84, 85, 88, 107, 111],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "civil-transactions-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "احتساب مكافأة نهاية الخدمة في حالة الاستقالة بعد خدمة بين خمس وعشر سنوات",
                "expected_regulations": ["labor-law"],
                "expected_articles": [84, 85],
                "min_expected_article_hits": 2,
            },
            {
                "issue": "تصفية الحقوق عند انتهاء علاقة العمل",
                "expected_regulations": ["labor-law"],
                "expected_articles": [88],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "استحقاق أجر ساعات العمل الإضافية",
                "expected_regulations": ["labor-law"],
                "expected_articles": [107],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "استحقاق مقابل رصيد الإجازات غير المستخدمة",
                "expected_regulations": ["labor-law"],
                "expected_articles": [111],
                "min_expected_article_hits": 1,
            },
        ],
        notes="قضية أساسية لاختبار الفرق بين المادة 84 والمادة 85 مع عدم إسقاط رصيد الإجازات والعمل الإضافي.",
        questions={
            "anchor": "موظف في شركة خاصة استقال بعد 8 سنوات خدمة، ويطالب بمكافأة نهاية الخدمة، ومقابل رصيد الإجازات السنوية غير المستخدمة، وأجر ساعات إضافية كان يؤديها بانتظام. ما النظام والمواد الأقرب؟",
            "working_a": "عامل بأجر شهري أنهى علاقته الوظيفية بالاستقالة بعد خدمة متصلة تجاوزت 8 سنوات، ثم طالب بمكافأة نهاية الخدمة، وبدل الإجازات المتراكمة، ومستحقات العمل الإضافي. ما المرجع النظامي الأقرب؟",
            "working_b": "موظف قديم في منشأة أهلية قدّم استقالته بعد أكثر من خمس سنوات وأقل من عشر، ويتمسك بأنه يستحق كامل مكافأة نهاية الخدمة إضافة إلى مقابل الإجازات التي لم يحصل عليها وأجور الساعات الإضافية. ما النظام والمواد الأقرب؟",
            "heldout": "بعد 8 سنوات في منشأة خاصة، ترك موظف العمل باستقالته ودار الخلاف حول ثلاثة بنود: قدر مكافأة نهاية الخدمة للمستقيل، ومقابل الأيام السنوية غير المتمتع بها، وأجر التكليف بالساعات الإضافية. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b02_labor_absence_termination",
        family="labor",
        title="الفصل بسبب الغياب دون استيفاء التحقيق والإنذار",
        question_type="multi_issue",
        expected_regulations=["labor-law"],
        allowed_regulations=["labor-law"],
        expected_articles=[71, 75, 76, 77, 80],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "civil-transactions-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "وجوب التحقيق وإبلاغ العامل كتابة قبل الجزاء التأديبي",
                "expected_regulations": ["labor-law"],
                "expected_articles": [71],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حالة الفصل بسبب الغياب وشروطها بما في ذلك الإنذار الكتابي",
                "expected_regulations": ["labor-law"],
                "expected_articles": [80],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإشعار في العقد غير المحدد المدة والتعويض عن عدم مراعاته",
                "expected_regulations": ["labor-law"],
                "expected_articles": [75, 76],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "التعويض عن الإنهاء لسبب غير مشروع",
                "expected_regulations": ["labor-law"],
                "expected_articles": [77],
                "min_expected_article_hits": 1,
            },
        ],
        notes="حزمة محورية لمنع الاكتفاء بالمادة 80 وحدها أو إغفال التحقيق والإنذار والإشعار.",
        questions={
            "anchor": "عامل بعقد غير محدد المدة وأجر شهري تغيب 12 يومًا متصلة بلا عذر، ففصلته الشركة برسالة واتساب دون إنذار مكتوب أو تحقيق. ما النظام والمواد الأقرب؟",
            "working_a": "موظف في منشأة خاصة انقطع عن العمل أيامًا متتالية، ثم تلقى إشعار فصل فوري عبر رسالة هاتفية من دون سماع دفاعه أو إنذار كتابي سابق. ما النصوص النظامية الأقرب؟",
            "working_b": "شركة أنهت عقد عامل غير محدد المدة بسبب غياب متصل قبل بلوغ الحد الذي يجيز الفصل، ومن غير محضر تحقيق أو إنذار مكتوب. ما النظام والمواد الأقرب؟",
            "heldout": "فُصل عامل بأجر شهري من عمله بسبب غياب متصل، لكن صاحب العمل لم يحقق معه ولم يوجه له إنذارًا كتابيًا، وأرسل الإنهاء عبر وسيلة مراسلة فقط. ما المرجع النظامي الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b03_labor_wage_delay_deductions",
        family="labor",
        title="تأخر الأجور والحسم غير المشروع والعمل الإضافي",
        question_type="multi_issue",
        expected_regulations=["labor-law"],
        allowed_regulations=["labor-law"],
        expected_articles=[90, 94, 107],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["companies-law", "civil-transactions-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "قواعد مواعيد دفع الأجر",
                "expected_regulations": ["labor-law"],
                "expected_articles": [90],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "المطالبة بسبب تأخر الأجر أو الحسم دون موافقة مكتوبة",
                "expected_regulations": ["labor-law"],
                "expected_articles": [94],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الأجر الإضافي عن الساعات الزائدة",
                "expected_regulations": ["labor-law"],
                "expected_articles": [107],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يركز على الالتقاط الدقيق لمسار الأجر المتأخر والحسم غير المشروع وعدم الاكتفاء بخطاب عام عن الحقوق العمالية.",
        questions={
            "anchor": "عامل بأجر شهري تأخر راتبه شهرين، وخصم صاحب العمل مبالغ من راتبه دون موافقة مكتوبة، كما كلفه بساعات إضافية لم تدفع. ما النظام والمواد الأقرب؟",
            "working_a": "موظف يشكو من تأخير صرف الأجور على نحو متكرر، ووجود حسم من راتبه بلا موافقة خطية، وعدم صرف مقابل ساعات العمل الإضافية. ما المرجع النظامي الأقرب؟",
            "working_b": "منشأة خاصة أخرت دفع الرواتب، ثم اقتطعت مبالغ من أجر عامل من غير سند واضح أو موافقة مكتوبة، واستمرت في تشغيله بعد الدوام دون أجر إضافي. ما النظام والمواد الأقرب؟",
            "heldout": "الخلاف يدور حول ثلاثة مطالب عمالية: راتب شهري متأخر، وحسم غير مشروع من الأجر، وعدم احتساب بدل الساعات الإضافية. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b04_labor_restructuring_wrongful_termination",
        family="labor",
        title="إنهاء عقد غير محدد المدة بسبب إعادة هيكلة مع أجور متأخرة وعمل إضافي",
        question_type="multi_issue",
        expected_regulations=["labor-law"],
        allowed_regulations=["labor-law"],
        expected_articles=[75, 77, 84, 88, 90, 94, 107],
        min_expected_regulation_hits=1,
        min_expected_article_hits=5,
        contamination_traps=["companies-law", "e-commerce-law", "criminal-procedure-law"],
        sub_issues=[
            {
                "issue": "الإشعار الكتابي في العقد غير المحدد المدة",
                "expected_regulations": ["labor-law"],
                "expected_articles": [75],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "التعويض عن الإنهاء غير المشروع",
                "expected_regulations": ["labor-law"],
                "expected_articles": [77],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "مكافأة نهاية الخدمة",
                "expected_regulations": ["labor-law"],
                "expected_articles": [84],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "تصفية الحقوق",
                "expected_regulations": ["labor-law"],
                "expected_articles": [88],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الأجر المتأخر والساعات الإضافية",
                "expected_regulations": ["labor-law"],
                "expected_articles": [90, 94, 107],
                "min_expected_article_hits": 2,
            },
        ],
        notes="هذه هي القضية المركبة التي كشفت سابقًا انجذاب المسترجع إلى عنقود خاطئ داخل نظام العمل.",
        questions={
            "anchor": "موظف عقده غير محدد المدة فُصل بسبب إعادة هيكلة دون إشعار كاف، مع وجود أجور متأخرة وساعات إضافية ومطالبة بمكافأة نهاية الخدمة. ما النظام والمواد الأقرب؟",
            "working_a": "شركة خاصة أنهت عقد عامل غير محدد المدة بحجة إعادة التنظيم الداخلي، من دون إشعار مكتوب كاف، وبقيت عليه رواتب متأخرة وساعات إضافية، وهو يطالب أيضًا بمكافأة نهاية الخدمة. ما المرجع النظامي الأقرب؟",
            "working_b": "العامل يطعن في إنهاء خدمته بسبب إعادة هيكلة المنشأة، ويتمسك بحقوق متعددة: التعويض عن الإنهاء، مكافأة نهاية الخدمة، أجور متأخرة، وساعات إضافية. ما النظام والمواد الأقرب؟",
            "heldout": "في نزاع عمالي مركب، أنهت المنشأة عقدًا غير محدد المدة بدعوى إعادة الهيكلة، مع تأخر في الرواتب ووجود ساعات إضافية غير مدفوعة، والعامل يطلب مستحقاته النهائية. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b05_companies_direct_conflict",
        family="companies",
        title="تعارض مصالح مباشر لمدير شركة ذات مسؤولية محدودة",
        question_type="multi_issue",
        expected_regulations=["companies-law"],
        allowed_regulations=["companies-law"],
        expected_articles=[26, 27],
        min_expected_regulation_hits=1,
        min_expected_article_hits=2,
        contamination_traps=["labor-law", "government-tenders-and-procurement-law", "civil-transactions-law"],
        sub_issues=[
            {
                "issue": "واجبات العناية والولاء",
                "expected_regulations": ["companies-law"],
                "expected_articles": [26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "منع المصلحة المباشرة أو غير المباشرة دون ترخيص وإفصاح",
                "expected_regulations": ["companies-law"],
                "expected_articles": [27],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يقيس التزام الراغ بالمواد الموضوعية الحاكمة قبل الانجراف إلى نصوص أقل مركزية.",
        questions={
            "anchor": "مدير شركة ذات مسؤولية محدودة أبرم عقد توريد بين الشركة ومنشأة يملكها هو شخصيًا دون ترخيص أو إفصاح للشركاء. ما النظام والمواد الأقرب قبل الجزاءات؟",
            "working_a": "مدير شركة محدودة المسؤولية تعاقد باسم الشركة مع مؤسسة مملوكة له مباشرة دون أن يكشف عن مصلحته أو يحصل على موافقة الشركاء. ما النظام والمواد الأقرب؟",
            "working_b": "شركة اكتشفت أن مديرها أجرى تعاملًا مع كيان يملكه لنفسه، ومرر الصفقة من غير ترخيص أو إفصاح سابق. ما النصوص الأقرب من نظام الشركات؟",
            "heldout": "وقع مدير شركة ذات مسؤولية محدودة عقدًا للشركة مع نشاط تجاري يعود إليه شخصيًا، من غير إذن أو كشف للمصلحة. ما النظام والمواد الأقرب قبل الانتقال للجزاء أو المسؤولية؟",
        },
    ),
    make_bundle(
        bundle_id="b06_companies_indirect_relative_conflict",
        family="companies",
        title="مصلحة غير مباشرة عبر قريب لمدير شركة",
        question_type="multi_issue",
        expected_regulations=["companies-law"],
        allowed_regulations=["companies-law"],
        expected_articles=[26, 27],
        min_expected_regulation_hits=1,
        min_expected_article_hits=2,
        contamination_traps=["labor-law", "government-tenders-and-procurement-law", "civil-transactions-law"],
        sub_issues=[
            {
                "issue": "واجب الولاء والعناية تجاه مصلحة الشركة",
                "expected_regulations": ["companies-law"],
                "expected_articles": [26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "المصلحة غير المباشرة والإفصاح عنها",
                "expected_regulations": ["companies-law"],
                "expected_articles": [27],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يستهدف الفشل الشائع عندما يلتقط النظام نصوصًا عن المنافسة أو المسؤولية قبل نص تعارض المصالح نفسه.",
        questions={
            "anchor": "مدير شركة ذات مسؤولية محدودة مرر تعاملاً مع مؤسسة يملكها شقيقه وحقق منفعة غير مباشرة دون ترخيص أو إفصاح. ما النظام والمواد الأقرب؟",
            "working_a": "تبيّن أن مدير الشركة استفاد من صفقة أبرمتها الشركة مع منشأة تعود لزوجته أو لقريب له، من غير كشف للمصلحة غير المباشرة. ما النصوص النظامية الأقرب؟",
            "working_b": "أدخل مدير شركة محدودة المسؤولية شركته في عقد مع كيان يملكه أحد أقاربه بما يحقق له منفعة غير مباشرة، دون موافقة أو إفصاح. ما النظام والمواد الأقرب؟",
            "heldout": "دار خلاف داخل شركة ذات مسؤولية محدودة حول صفقة مع جهة يملكها قريب للمدير، ويقال إن المدير كان مستفيدًا منها على نحو غير مباشر من غير تصريح. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b07_companies_partner_manager_competition",
        family="companies",
        title="منافسة الشريك المدير للشركة",
        question_type="multi_issue",
        expected_regulations=["companies-law"],
        allowed_regulations=["companies-law"],
        expected_articles=[26, 27, 40],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["labor-law", "government-tenders-and-procurement-law", "civil-transactions-law"],
        sub_issues=[
            {
                "issue": "واجبات الولاء والعمل لمصلحة الشركة",
                "expected_regulations": ["companies-law"],
                "expected_articles": [26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "المصلحة في الأعمال والعقود والإفصاح عنها",
                "expected_regulations": ["companies-law"],
                "expected_articles": [27],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "منع الشريك من منافسة الشركة دون موافقة الباقين",
                "expected_regulations": ["companies-law"],
                "expected_articles": [40],
                "min_expected_article_hits": 1,
            },
        ],
        notes="قضية متعمدة لتمييز المادة 40 حين تكون المنافسة صادرة من شريك مدير لا من مدير فقط.",
        questions={
            "anchor": "شريك مدير في شركة ذات مسؤولية محدودة أنشأ منشأة تمارس النشاط نفسه وبدأ يوجه العملاء إليها دون موافقة باقي الشركاء. ما النظام والمواد الأقرب؟",
            "working_a": "شريك ومدير في الشركة مارس لحسابه نشاطًا من نوع نشاط الشركة، واستغل موقعه في تحويل بعض الفرص التجارية إلى منشأة أخرى له. ما المرجع النظامي الأقرب؟",
            "working_b": "مدير شريك أسس كيانًا منافسًا للشركة التي يديرها، وارتبط ذلك أيضًا بتعاملات تحقق له مصلحة خاصة. ما المواد الأقرب في نظام الشركات؟",
            "heldout": "يقال إن شريكًا مديرًا في شركة محدودة المسؤولية نافس الشركة بنفسه ومن خلال كيان آخر يملكه، من غير موافقة بقية الشركاء. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b08_companies_board_related_party",
        family="companies",
        title="عضو مجلس إدارة ومصلحة مباشرة في عقد للشركة",
        question_type="multi_issue",
        expected_regulations=["companies-law"],
        allowed_regulations=["companies-law"],
        expected_articles=[26, 27, 71],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["labor-law", "government-tenders-and-procurement-law", "civil-transactions-law"],
        sub_issues=[
            {
                "issue": "واجبات العناية والولاء",
                "expected_regulations": ["companies-law"],
                "expected_articles": [26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حظر المصلحة المباشرة أو غير المباشرة دون ترخيص",
                "expected_regulations": ["companies-law"],
                "expected_articles": [27],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "وجوب إبلاغ المجلس بالمصلحة وإثبات ذلك في المحضر",
                "expected_regulations": ["companies-law"],
                "expected_articles": [71],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يركز على المجلس ومحاضر الإفصاح، لا على نصوص المسؤولية أو العقوبات اللاحقة فقط.",
        questions={
            "anchor": "عضو مجلس إدارة في شركة مساهمة شارك في التصويت على عقد للشركة مع جهة له فيها مصلحة مباشرة دون الإفصاح للمجلس. ما النظام والمواد الأقرب؟",
            "working_a": "أبرمت شركة مساهمة عقدًا مع جهة يرتبط بها عضو مجلس الإدارة، ويقال إنه لم يبلغ المجلس بمصلحته قبل مناقشة العقد. ما النصوص الأقرب؟",
            "working_b": "عضو مجلس إدارة كانت له منفعة مباشرة في تعامل للشركة، ولم يثبت الإفصاح عنها في محضر الاجتماع. ما المواد الأقرب من نظام الشركات؟",
            "heldout": "النزاع يتعلق بعضو مجلس إدارة له مصلحة في صفقة للشركة وشارك في القرار من غير إفصاح منضبط للمجلس. ما النظام والمواد الأقرب قبل البحث في الجزاءات؟",
        },
    ),
    make_bundle(
        bundle_id="b09_companies_manager_liability_damages",
        family="companies",
        title="مسؤولية المدير عن الضرر الناشئ من مخالفة واجباته",
        question_type="multi_issue",
        expected_regulations=["companies-law"],
        allowed_regulations=["companies-law"],
        expected_articles=[26, 28],
        min_expected_regulation_hits=1,
        min_expected_article_hits=2,
        contamination_traps=["labor-law", "government-tenders-and-procurement-law", "civil-transactions-law"],
        sub_issues=[
            {
                "issue": "واجب العناية والولاء كأساس للمخالفة",
                "expected_regulations": ["companies-law"],
                "expected_articles": [26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "مسؤولية المدير عن تعويض الضرر بسبب المخالفة أو الإهمال",
                "expected_regulations": ["companies-law"],
                "expected_articles": [28],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مهم لمنع ضياع طبقة المسؤولية المدنية عندما يكون السؤال عن الضرر اللاحق بالشركة.",
        questions={
            "anchor": "مدير شركة أبرم تصرفات ألحقت بالشركة خسائر جسيمة بسبب مخالفة واجباته وعدم مراعاة مصلحتها، وتبحث الشركة عن النصوص الأقرب للمطالبة بمسؤوليته وتعويضها. ما النظام والمواد الأقرب؟",
            "working_a": "شركة تريد الرجوع على مديرها لتعويض أضرار نتجت عن قرارات متعارضة مع مصلحة الشركة وعن تقصير في أداء واجباته. ما المرجع النظامي الأقرب؟",
            "working_b": "أدت مخالفات المدير وإهماله في إدارة شؤون الشركة إلى ضرر بالشركة وببعض الشركاء، فما النظام والمواد الأقرب لتأصيل واجباته ومسؤوليته؟",
            "heldout": "الخلاف يتمحور حول أفعال مدير قيل إنها خالفت واجبات العناية والولاء وألحقت بالشركة ضررًا ماليًا، وتبحث الشركة عن النصوص الأقرب للتعويض. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b10_pdpl_breach_retention_transfer",
        family="pdpl",
        title="تسرّب بيانات مع احتفاظ بعد انتهاء الغرض ونقل خارج المملكة",
        question_type="multi_issue",
        expected_regulations=[
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "pdpl-transfer-regulation",
        ],
        allowed_regulations=[
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "pdpl-transfer-regulation",
        ],
        expected_articles=[18, 20, 29],
        min_expected_regulation_hits=3,
        min_expected_article_hits=3,
        contamination_traps=["e-commerce-law", "criminal-procedure-law", "companies-law"],
        sub_issues=[
            {
                "issue": "إتلاف البيانات أو الاحتفاظ بها بعد انتهاء الغرض",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [18],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإشعار عن التسرب أو الوصول غير المشروع",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [20],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "نقل البيانات الشخصية إلى خارج المملكة",
                "expected_regulations": ["personal-data-protection-law", "pdpl-transfer-regulation"],
                "expected_articles": [29],
                "min_expected_regulation_hits": 2,
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإطار التنفيذي للحوادث والضمانات",
                "expected_regulations": ["pdpl-implementing-regulation"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="مبني على النص النافذ الحالي الذي يجعل الإتلاف بعد انتهاء الغرض في المادة 18 لا المادة 21.",
        questions={
            "anchor": "تطبيق صحي يحتفظ بالتقارير الطبية وصور الهوية. وقع وصول غير مشروع للبيانات، وتبين أن النسخ الاحتياطية ترسل خارج المملكة، كما احتفظت الشركة بالبيانات بعد انتهاء الغرض. ما الأنظمة والمواد الأقرب؟",
            "working_a": "منصة صحية أبقت بيانات المرضى في الأرشيف بعد انتهاء الحاجة إليها، ثم حدث وصول غير مصرح به، واتضح أن النسخ الاحتياطية محفوظة لدى مزود خارج المملكة. ما المرجع النظامي الأقرب؟",
            "working_b": "جهة تحكم ببيانات شخصية وصحية لم تتلف البيانات بعد انتهاء الغرض من جمعها، وتعرضت القاعدة لتسرّب، كما ظهر أن جزءًا من التخزين الاحتياطي يتم خارج المملكة. ما الأنظمة والمواد الأقرب؟",
            "heldout": "شركة تدير خدمة رقمية في القطاع الصحي خزّنت البيانات بعد انتهاء غرضها، ثم وقع اختراق جزئي، وتبين أن نسخًا من البيانات تنقل إلى بنية سحابية خارج المملكة. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b11_pdpl_marketing_privacy_requests",
        family="pdpl",
        title="التسويق دون موافقة واضحة وطلبات صاحب البيانات",
        question_type="multi_issue",
        expected_regulations=["personal-data-protection-law", "pdpl-implementing-regulation"],
        allowed_regulations=["personal-data-protection-law", "pdpl-implementing-regulation"],
        expected_articles=[4, 5, 21, 25, 26],
        min_expected_regulation_hits=2,
        min_expected_article_hits=4,
        contamination_traps=["e-commerce-law", "companies-law", "criminal-procedure-law"],
        sub_issues=[
            {
                "issue": "حقوق صاحب البيانات في العلم والوصول والحصول على نسخة",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [4],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الموافقة على المعالجة والرجوع عنها",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [5],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الاستجابة لطلبات صاحب البيانات خلال المدة والوسيلة المناسبة",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [21],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "استخدام وسائل الاتصال في التسويق المباشر",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [25, 26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الضوابط التنفيذية المتعلقة بالإشعارات وسياسة الخصوصية",
                "expected_regulations": ["pdpl-implementing-regulation"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="يقيس قدرة الراغ على تثبيت المرجع الرئيس بدل التشتت في إحالات جانبية أو لوائح غير محورية.",
        questions={
            "anchor": "منصة خدمات رقمية استخدمت أرقام العملاء وبريدهم لإرسال مواد تسويقية دون موافقة واضحة، ولا تظهر سياسة خصوصية مفهومة، ثم طلب العميل نسخة من بياناته وإيقاف المعالجة. ما الأنظمة والمواد الأقرب؟",
            "working_a": "شركة تقدم خدمة عبر التطبيق تجمع بيانات المستخدمين ثم ترسل رسائل دعائية دون موافقة صريحة، ولا توفر سياسة خصوصية واضحة، كما لم تستجب سريعًا لطلب نسخة من البيانات. ما المرجع النظامي الأقرب؟",
            "working_b": "المستخدم يشكو من ثلاث نقاط: تسويق مباشر دون موافقة ظاهرة، غياب إشعار خصوصية منضبط، وتأخر الجهة في معالجة طلبه للوصول إلى بياناته وإيقاف الاستخدام. ما الأنظمة والمواد الأقرب؟",
            "heldout": "جهة رقمية تستعمل بيانات التواصل في الدعاية، من غير وضوح كاف في الخصوصية، ثم طلب صاحب البيانات العلم بما لديها عنه والحصول على نسخة والحد من المعالجة. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b12_pdpl_sensitive_health_marketing",
        family="pdpl",
        title="استخدام بيانات صحية حساسة في التسويق ثم حدوث وصول غير مشروع",
        question_type="multi_issue",
        expected_regulations=["personal-data-protection-law", "pdpl-implementing-regulation"],
        allowed_regulations=["personal-data-protection-law", "pdpl-implementing-regulation"],
        expected_articles=[5, 20, 25, 26],
        min_expected_regulation_hits=2,
        min_expected_article_hits=3,
        contamination_traps=["e-commerce-law", "companies-law", "criminal-procedure-law"],
        sub_issues=[
            {
                "issue": "الموافقة على المعالجة أو تغيير الغرض",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [5],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "قيود التسويق عبر وسائل الاتصال الشخصية",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [25, 26],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإشعار عن الوصول غير المشروع أو التسرب",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [20],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الضوابط التنفيذية للمعالجة والحوادث",
                "expected_regulations": ["pdpl-implementing-regulation"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="مصمم لإجبار الراغ على فهم خصوصية السياق الصحي والحساسية التسويقية دون القفز إلى أنظمة عامة.",
        questions={
            "anchor": "عيادة رقمية جمعت بيانات صحية حساسة، ثم استخدمتها لاحقًا في رسائل ترويجية، وبعد ذلك وقع وصول غير مصرح به إلى بعض الملفات. ما الأنظمة والمواد الأقرب؟",
            "working_a": "مركز صحي إلكتروني احتفظ ببيانات مرضاه واستخدمها في حملات دعائية، ثم تبيّن حصول وصول غير مشروع إلى جزء من السجلات. ما المرجع النظامي الأقرب؟",
            "working_b": "الوقائع تتعلق ببيانات صحية حساسة استعملت لأغراض تسويقية ثم تعرضت لخرق أمني. ما الأنظمة والمواد الأقرب من زاوية حماية البيانات الشخصية؟",
            "heldout": "منشأة صحية رقمية جمعت بيانات حساسة لعلاج المرضى، ثم أعادت توظيفها في التسويق المباشر وواجهت لاحقًا حادث وصول غير مشروع للملفات. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b13_pdpl_employee_data_retention",
        family="pdpl",
        title="الاحتفاظ ببيانات الموظف السابق وطلب نسخة أو حذف",
        question_type="multi_issue",
        expected_regulations=["personal-data-protection-law", "pdpl-implementing-regulation"],
        allowed_regulations=["personal-data-protection-law", "pdpl-implementing-regulation", "labor-law"],
        expected_articles=[4, 5, 18, 21],
        min_expected_regulation_hits=2,
        min_expected_article_hits=3,
        contamination_traps=["companies-law", "criminal-procedure-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "حقوق صاحب البيانات في العلم والوصول والحصول على نسخة",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [4],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "المعالجة لغاية محددة وعدم تغيير الغرض بغير مسوغ",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [5],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "إتلاف البيانات عند انتهاء الغرض أو ضبط الاحتفاظ بها",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [18],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الاستجابة لطلبات الموظف السابق المتعلقة بحقوقه",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [21],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مفيد لتمييز مرجعية PDPL حتى في سياق علاقة عمل، مع السماح بذكر نظام العمل كإطار مساند فقط.",
        questions={
            "anchor": "شركة احتفظت بصور هويات الموظفين وعقودهم وبيانات الاتصال بعد انتهاء علاقة العمل، ورفضت تزويد موظف سابق بنسخة من بياناته أو معالجة طلبه بالحذف. ما الأنظمة والمواد الأقرب؟",
            "working_a": "بعد انتهاء التوظيف، أبقت المنشأة على ملفات الموظف السابق ورفضت الرد على طلبه للوصول إلى بياناته وتسوية وضع الاحتفاظ بها. ما المرجع النظامي الأقرب؟",
            "working_b": "موظف سابق يشتكي من استمرار الشركة في الاحتفاظ ببياناته ومن عدم الاستجابة لطلب نسخة منها وطلب حذفها متى انتفى الغرض. ما الأنظمة والمواد الأقرب؟",
            "heldout": "الخلاف هنا ليس على مستحقات العمل، بل على بيانات موظف سابق احتفظت بها المنشأة بعد انتهاء الغرض ولم تستجب لطلبه في الوصول إليها ومعالجة وضعها. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b14_pdpl_ecommerce_crossborder",
        family="pdpl",
        title="متجر إلكتروني يعالج البيانات للتسويق والتحليلات وينقل نسخًا خارج المملكة",
        question_type="cross_domain",
        expected_regulations=[
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "pdpl-transfer-regulation",
            "e-commerce-law",
        ],
        allowed_regulations=[
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "pdpl-transfer-regulation",
            "e-commerce-law",
        ],
        expected_articles=[5, 18, 20, 25, 29],
        min_expected_regulation_hits=4,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "criminal-procedure-law", "law-of-evidence"],
        sub_issues=[
            {
                "issue": "الموافقة على المعالجة والتسويق المباشر",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [5, 25],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "إتلاف البيانات عند انتهاء الغرض أو ضبط الاحتفاظ بها",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [18],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإخطار عن الوصول غير المشروع إن وقع حادث",
                "expected_regulations": ["personal-data-protection-law"],
                "expected_articles": [20],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "النقل إلى خارج المملكة وضماناته",
                "expected_regulations": ["personal-data-protection-law", "pdpl-transfer-regulation"],
                "expected_articles": [29],
                "min_expected_regulation_hits": 2,
                "min_expected_article_hits": 1,
            },
            {
                "issue": "المرجعية القطاعية للتجارة الإلكترونية",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="Cross-domain مقصود لإجبار الراغ على جمع الحزمة الأساسية والغطاء القطاعي معًا.",
        questions={
            "anchor": "متجر إلكتروني سعودي يجمع بيانات العملاء للشراء والتتبع والتحليلات، ويستخدمها في التسويق لاحقًا، ويحفظ نسخًا احتياطية لدى مزود سحابي خارج المملكة. ما الأنظمة والمواد الأقرب؟",
            "working_a": "منصة بيع إلكتروني تستخدم بيانات المتسوقين في الرسائل التسويقية، وتستبقي البيانات للتحليلات، ولديها بنية نسخ احتياطي خارج المملكة. ما المرجع النظامي الأقرب؟",
            "working_b": "الوقائع تتعلق بمتجر إلكتروني يعالج بيانات المستهلكين لأغراض تشغيلية وتسويقية وينقل بعض النسخ إلى الخارج، مع احتمال بقاء البيانات مدة أطول من الغرض الأصلي. ما الأنظمة والمواد الأقرب؟",
            "heldout": "تاجر إلكتروني داخل المملكة يجمع بيانات المشترين، يوظفها لاحقًا في التسويق، ويعتمد على مزود سحابي أجنبي لحفظ النسخ الاحتياطية. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b15_ecommerce_delay_misleading_refund",
        family="ecommerce",
        title="تأخر التسليم والإعلان المضلل ورفض رد المبلغ",
        question_type="multi_issue",
        expected_regulations=["e-commerce-law"],
        allowed_regulations=["e-commerce-law"],
        expected_articles=[6, 10, 11, 14, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "personal-data-protection-law", "anti-money-laundering-law"],
        sub_issues=[
            {
                "issue": "الإفصاح الإلزامي في المحل الإلكتروني",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [6],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "اعتبار الإعلان الإلكتروني وثيقة تعاقدية مكملة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [10],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حظر الإعلان الكاذب أو المضلل",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حق المستهلك في الفسخ عند تأخر التسليم أكثر من خمسة عشر يومًا",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [14],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الجزاءات أو الإجراء عند مخالفة النظام أو اللائحة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مصمم لالتقاط المادة 14 صراحة بدل الغرق في حق السبعة أيام وحده.",
        questions={
            "anchor": "مستهلك اشترى جهازًا من متجر إلكتروني داخل المملكة. الإعلان ذكر أن التسليم خلال 3 أيام، لكن المتجر سلّم بعد 20 يومًا، ورفض إلغاء الطلب ورد المبلغ، كما تبيّن أن الإعلان كان يتضمن وعودًا مضللة عن سرعة التوصيل. ما النظام والمواد الأقرب؟",
            "working_a": "متجر إلكتروني وعد بتوصيل سريع في الإعلان، ثم تأخر أكثر من 15 يومًا عن التسليم ورفض إعادة المبلغ للمستهلك. ما المرجع النظامي الأقرب؟",
            "working_b": "النزاع يدور حول محل إلكتروني قدّم إعلانًا مضللًا عن مدة التوصيل، وتأخر فعليًا في التنفيذ عشرين يومًا، ثم رفض فسخ العقد. ما النظام والمواد الأقرب؟",
            "heldout": "اشترى مستهلك من متجر إلكتروني سعودي بناءً على إعلان عن تسليم سريع، لكن التنفيذ تجاوز خمسة عشر يومًا وانتهى برفض رد ما دفعه. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b16_ecommerce_nonconforming_return",
        family="ecommerce",
        title="تسليم منتج غير مطابق ورفض الاسترجاع",
        question_type="multi_issue",
        expected_regulations=["e-commerce-law"],
        allowed_regulations=["e-commerce-law"],
        expected_articles=[6, 10, 11, 13, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "personal-data-protection-law", "anti-money-laundering-law"],
        sub_issues=[
            {
                "issue": "الإفصاح عن البيانات الأساسية لموفر الخدمة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [6],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإعلان الإلكتروني كجزء من الوثائق التعاقدية",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [10],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حظر الادعاء الكاذب أو المضلل",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حق فسخ العقد أو الاسترجاع في الحالات المنصوص عليها",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [13],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الجزاءات أو الإجراء عند المخالفة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="حزمة لتمييز عدم المطابقة عن مجرد التأخير، مع تثبيت أثر الإعلان على العقد.",
        questions={
            "anchor": "مستهلك اشترى هاتفًا من متجر إلكتروني سعودي. الإعلان ذكر مواصفات معينة، لكن المنتج المسلَّم جاء بلون وسعة مختلفين، ثم رفض المتجر الاسترجاع بحجة أنه لا يوجد استرجاع بعد فتح التغليف. ما النظام والمواد الأقرب؟",
            "working_a": "المحل الإلكتروني سلّم منتجًا غير مطابق للوصف المعروض في الإعلان، ثم تمسك بشرط مطلق يمنع الاسترجاع بعد الفتح. ما المرجع النظامي الأقرب؟",
            "working_b": "المستهلك استند إلى إعلان المتجر الإلكتروني في الشراء، لكن السلعة وصلت بصفات مختلفة، ورفض البائع الفسخ أو الاسترجاع. ما النظام والمواد الأقرب؟",
            "heldout": "ظهر خلاف في تجارة إلكترونية لأن المنتج المسلَّم لا يطابق الوصف والمواصفات المعلن عنها، مع رفض إعادة المبلغ. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b17_ecommerce_disclosure_shipping",
        family="ecommerce",
        title="عدم الإفصاح عن بيانات المتجر ورسوم الشحن وموعد التسليم",
        question_type="multi_issue",
        expected_regulations=["e-commerce-law"],
        allowed_regulations=["e-commerce-law"],
        expected_articles=[6, 10, 11, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["companies-law", "personal-data-protection-law", "anti-money-laundering-law"],
        sub_issues=[
            {
                "issue": "الإفصاح عن هوية موفر الخدمة ووسائل الاتصال",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [6],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "أثر ما يرد في الإعلان أو العرض الإلكتروني",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [10],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "البيانات أو الادعاءات المضللة بشأن الرسوم أو الشحن",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "إجراء أو جزاء المخالفة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يركز على الإفصاح والشفافية في بيانات المتجر وخطاب العرض، ولو لم تكن كل التفاصيل واردة صراحة في المادة نفسها.",
        questions={
            "anchor": "متجر إلكتروني لم يفصح بوضوح عن بياناته التجارية ووسائل التواصل، ولم يبين رسوم الشحن إلا بعد الدفع، كما لم يوضح موعد التسليم بدقة. ما النظام والمواد الأقرب؟",
            "working_a": "مستهلك تعامل مع محل إلكتروني يخفي بيانات التاجر الأساسية ويعرض أسعارًا لا تشمل رسوم الشحن الحقيقية إلا في مرحلة متأخرة. ما النظام والمواد الأقرب؟",
            "working_b": "الخلاف متعلق بمتجر إلكتروني لم يذكر بوضوح هوية موفر الخدمة ووسائل التواصل، وقدم بيانًا غير شفاف عن الرسوم والتوصيل. ما المرجع النظامي الأقرب؟",
            "heldout": "تقوم الشكوى على ضعف الإفصاح في محل إلكتروني: بيانات التاجر غير واضحة، ورسوم الشحن أضيفت لاحقًا، وموعد التسليم لم يبيَّن بيانًا منضبطًا. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b18_ecommerce_service_coolingoff",
        family="ecommerce",
        title="فسخ عقد خدمة إلكترونية قبل الاستخدام",
        question_type="multi_issue",
        expected_regulations=["e-commerce-law"],
        allowed_regulations=["e-commerce-law"],
        expected_articles=[10, 13, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["companies-law", "personal-data-protection-law", "anti-money-laundering-law"],
        sub_issues=[
            {
                "issue": "اعتبار الإعلان أو العرض الإلكتروني جزءًا من العقد",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [10],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حق المستهلك في فسخ العقد خلال المدة المقررة إذا لم يستخدم الخدمة أو المنتج",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [13],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الإجراء أو الجزاء عند مخالفة النظام أو اللائحة",
                "expected_regulations": ["e-commerce-law"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مفيد لمنع قصر النظام على السلع فقط وإهمال الخدمات الإلكترونية.",
        questions={
            "anchor": "مستهلك اشترى اشتراك خدمة رقمية عبر متجر إلكتروني، ثم عدل خلال أيام قبل استعمال الخدمة وطلب الفسخ واسترداد المبلغ، فرفض المتجر. ما النظام والمواد الأقرب؟",
            "working_a": "أبرم مستهلك عقد خدمة إلكترونية عبر منصة بيع، ثم طلب فسخه خلال المهلة النظامية قبل الانتفاع بالخدمة، فقوبل بالرفض. ما المرجع النظامي الأقرب؟",
            "working_b": "الوقائع تتعلق بخدمة بيعت إلكترونيًا بناء على عرض منشور في المنصة، ثم طلب المستهلك إلغاء التعاقد سريعًا قبل الاستخدام. ما النظام والمواد الأقرب؟",
            "heldout": "خدمة رقمية تعاقد عليها مستهلك من خلال متجر إلكتروني، لكنه رجع عن التعاقد قبل الاستعمال وخلال مدة وجيزة، وامتنع المتجر عن رد المقابل. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b19_civil_arbun_digital_evidence",
        family="civil_evidence",
        title="العربون مع حجية المراسلات الإلكترونية والتحويل البنكي",
        question_type="cross_domain",
        expected_regulations=["civil-transactions-law", "law-of-evidence", "electronic-transactions-law"],
        allowed_regulations=["civil-transactions-law", "law-of-evidence", "electronic-transactions-law"],
        expected_articles=[44, 53, 57, 63],
        min_expected_regulation_hits=3,
        min_expected_article_hits=3,
        contamination_traps=["labor-law", "anti-money-laundering-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "حكم العربون عند العدول عن العقد",
                "expected_regulations": ["civil-transactions-law"],
                "expected_articles": [44],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "تعريف الدليل الرقمي وحجية الدليل غير الرسمي",
                "expected_regulations": ["law-of-evidence"],
                "expected_articles": [53, 57],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حجية المستخرجات ومن بينها وسائل الدفع الرقمية",
                "expected_regulations": ["law-of-evidence"],
                "expected_articles": [63],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "قبول السجل والتعامل الإلكتروني في الإثبات",
                "expected_regulations": ["electronic-transactions-law"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="Cross-domain أساسي لاختبار جمع المدني والإثبات والتعاملات الإلكترونية في حزمة واحدة.",
        questions={
            "anchor": "مشتري تفاوض مع بائع على شراء آلة صناعية عبر واتساب والبريد الإلكتروني، ثم حوّل 50,000 ريال بوصفه عربونًا. بعد يومين عدل عن الشراء وطالب باسترداد العربون كاملًا، ويتمسك بالمحادثات الإلكترونية وإشعار التحويل البنكي. ما الأنظمة والمواد الأقرب؟",
            "working_a": "اتفق طرفان عبر رسائل إلكترونية على صفقة معدات، ودفع المشتري مبلغًا على أنه عربون ثم رجع عن الشراء، ويحتج بالواتساب والتحويل البنكي. ما المرجع النظامي الأقرب؟",
            "working_b": "النزاع يدور حول أثر العربون عند العدول عن عقد تم التفاوض عليه رقمياً، مع الاعتماد على الرسائل الإلكترونية وإشعار التحويل كوسيلة إثبات. ما الأنظمة والمواد الأقرب؟",
            "heldout": "جرى تفاوض إلكتروني على بيع معدة، ثم دفع المشتري عربونًا بتحويل مصرفي قبل أن يتراجع عن الشراء، ويتمسك بالمراسلات الرقمية والمستخرج البنكي. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b20_arbitration_email_clause",
        family="civil_evidence",
        title="اتفاق التحكيم عبر البريد الإلكتروني والمراسلات الموثقة",
        question_type="cross_domain",
        expected_regulations=["nzam-althkym", "law-of-evidence", "electronic-transactions-law"],
        allowed_regulations=["nzam-althkym", "law-of-evidence", "electronic-transactions-law"],
        expected_articles=[9, 11, 53, 57],
        min_expected_regulation_hits=3,
        min_expected_article_hits=3,
        contamination_traps=["labor-law", "anti-money-laundering-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "وجوب كتابة اتفاق التحكيم وكفاية تبادل المراسلات الموثقة",
                "expected_regulations": ["nzam-althkym"],
                "expected_articles": [9],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "أثر الدفع بوجود اتفاق التحكيم أمام المحكمة",
                "expected_regulations": ["nzam-althkym"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الدليل الرقمي وحجيته بين الأطراف",
                "expected_regulations": ["law-of-evidence"],
                "expected_articles": [53, 57],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "الغطاء الإلكتروني للمراسلات أو السجل",
                "expected_regulations": ["electronic-transactions-law"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="يعالج فشلًا سابقًا حيث التقط النظام طبقة الإثبات وترك نظام التحكيم نفسه.",
        questions={
            "anchor": "شركتان اتفقتا عبر رسائل بريد إلكتروني موثقة على أن تكون المنازعات عن طريق التحكيم، لكن لا يوجد عقد ورقي موقع. ما الأنظمة والمواد الأقرب؟",
            "working_a": "الطرفان تبادلا رسائل إلكترونية تضمنت شرط تحكيم واضحًا، ثم أنكر أحدهما وجود اتفاق مكتوب لأنه لا يوجد محرر ورقي. ما المرجع النظامي الأقرب؟",
            "working_b": "الخلاف يتناول مدى كفاية البريد الإلكتروني والمراسلات الرقمية لإثبات شرط التحكيم، وأثر ذلك إذا رُفعت الدعوى أمام المحكمة. ما الأنظمة والمواد الأقرب؟",
            "heldout": "اشترط طرفان التحكيم في مراسلات إلكترونية موثقة من دون عقد ورقي موقّع، ثم ثار النزاع حول حجية هذا الشرط وأثره القضائي. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b21_electronic_signature_written_form",
        family="civil_evidence",
        title="التوقيع الإلكتروني واستيفاء شرط الكتابة",
        question_type="cross_domain",
        expected_regulations=["electronic-transactions-law", "law-of-evidence"],
        allowed_regulations=["electronic-transactions-law", "law-of-evidence"],
        expected_articles=[5, 8, 14, 53, 57, 63],
        min_expected_regulation_hits=2,
        min_expected_article_hits=4,
        contamination_traps=["labor-law", "anti-money-laundering-law", "e-commerce-law"],
        sub_issues=[
            {
                "issue": "حجية التعاملات والسجلات والتوقيعات الإلكترونية",
                "expected_regulations": ["electronic-transactions-law"],
                "expected_articles": [5],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "اعتبار السجل الإلكتروني أصلًا بذاته متى توافرت شروطه الفنية",
                "expected_regulations": ["electronic-transactions-law"],
                "expected_articles": [8],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "استيفاء التوقيع الإلكتروني لشرط التوقيع الخطي",
                "expected_regulations": ["electronic-transactions-law"],
                "expected_articles": [14],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حجية الدليل الرقمي ومستخرجاته",
                "expected_regulations": ["law-of-evidence"],
                "expected_articles": [53, 57, 63],
                "min_expected_article_hits": 1,
            },
        ],
        notes="يركز على الكتابة والتوقيع والسجل الإلكتروني مع طبقة الإثبات كغطاء مكمل.",
        questions={
            "anchor": "شركتان تبادلتا عرضًا وقبولًا إلكترونيًا ووقع المفوضان توقيعًا إلكترونيًا على مستند PDF لعقد توريد، ثم نازع أحدهما بحجة عدم وجود توقيع خطي أو أصل ورقي. ما الأنظمة والمواد الأقرب؟",
            "working_a": "عقد تجاري أبرم إلكترونيًا بتوقيع رقمي، ثم أنكر أحد الطرفين حجيته لعدم وجود نسخة ورقية موقعة بخط اليد. ما المرجع النظامي الأقرب؟",
            "working_b": "الخلاف يدور حول ما إذا كان التوقيع الإلكتروني والسجل الرقمي يكفيان لاستيفاء شرط الكتابة والإثبات في عقد توريد. ما الأنظمة والمواد الأقرب؟",
            "heldout": "أُبرم التعاقد عبر مستند إلكتروني موقّع رقمياً، ثم احتج أحد الأطراف بغياب الأصل الورقي لنفي الحجية. ما الأنظمة والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b22_franchise_disclosure_registration",
        family="special",
        title="الإفصاح والقيد واللغة في الامتياز التجاري",
        question_type="multi_issue",
        expected_regulations=["nzam-alamtyaz-altjary"],
        allowed_regulations=["nzam-alamtyaz-altjary"],
        expected_articles=[6, 7, 11, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["companies-law", "e-commerce-law", "law-of-trademarks*"],
        sub_issues=[
            {
                "issue": "قيد اتفاقية الامتياز ووثيقة الإفصاح",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [6],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "تسليم وثيقة الإفصاح قبل أربعة عشر يومًا على الأقل وقبل أي مقابل",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [7],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "وجوب العربية أو الترجمة المعتمدة في اتفاقية الامتياز",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حق الإنهاء عند الإخلال الجوهري بالتزامات الإفصاح أو القيد",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مقصود به منع الانجراف إلى نظام العلامات التجارية لمجرد التقارب اللفظي مع الامتياز.",
        questions={
            "anchor": "مانح امتياز سلّم صاحب الامتياز مسودة الاتفاقية بالإنجليزية فقط، وأخذ دفعة مالية قبل التوقيع، ولم يسلمه وثيقة الإفصاح قبل 14 يومًا، ولم يقيد الاتفاقية ووثيقة الإفصاح. ما النظام والمواد الأقرب؟",
            "working_a": "صاحب امتياز محتمل دفع مبلغًا لمانح الامتياز قبل استلام الإفصاح النظامي، كما أن الاتفاقية لم تكن بالعربية ولم تُقيد. ما المرجع النظامي الأقرب؟",
            "working_b": "النزاع يتعلق بعقد امتياز تجاري قُدم باللغة الإنجليزية فقط، مع دفع مقابل مبكر وغياب القيد والإفصاح في المواعيد المقررة. ما النظام والمواد الأقرب؟",
            "heldout": "قبل التوقيع على امتياز تجاري، حصل مانح الامتياز على مقابل مالي ولم يقدم وثيقة الإفصاح في الوقت المطلوب، وكانت الاتفاقية بغير العربية ومن دون قيد. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b23_franchise_termination_right",
        family="special",
        title="حق الإنهاء بسبب إخلال جوهري بالتزامات الامتياز",
        question_type="multi_issue",
        expected_regulations=["nzam-alamtyaz-altjary"],
        allowed_regulations=["nzam-alamtyaz-altjary"],
        expected_articles=[7, 11, 17],
        min_expected_regulation_hits=1,
        min_expected_article_hits=3,
        contamination_traps=["companies-law", "e-commerce-law", "law-of-trademarks*"],
        sub_issues=[
            {
                "issue": "الإفصاح المسبق والمهلة النظامية قبل العقد أو الدفع",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [7],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "لغة الاتفاقية أو الترجمة المعتمدة",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [11],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حق صاحب الامتياز في الإنهاء عند الإخلال الجوهري",
                "expected_regulations": ["nzam-alamtyaz-altjary"],
                "expected_articles": [17],
                "min_expected_article_hits": 1,
            },
        ],
        notes="حزمة أضيق من السابقة، تركز على حق الإنهاء نفسه بدل توزيع الانتباه على كل عناصر الامتياز دفعة واحدة.",
        questions={
            "anchor": "صاحب امتياز يريد إنهاء الاتفاقية لأن مانح الامتياز أخذ المقابل مبكرًا، ولم يلتزم بالإفصاح السابق، وكانت الاتفاقية بغير العربية من دون ترجمة معتمدة. ما النظام والمواد الأقرب؟",
            "working_a": "دار الخلاف حول إمكان إنهاء امتياز تجاري بسبب قصور الإفصاح وتقديم الاتفاقية بلغة أجنبية فقط. ما النظام والمواد الأقرب؟",
            "working_b": "بعد التعاقد، اكتشف صاحب الامتياز أن مانح الامتياز لم يلتزم بمتطلبات الإفصاح المسبق واللغة، ويريد معرفة النصوص الأقرب لحقه في الإنهاء. ما المرجع النظامي الأقرب؟",
            "heldout": "الإشكال يتمحور حول امتياز تجاري أخل فيه المانح بواجبات الإفصاح وبمتطلب العربية أو الترجمة، وصاحب الامتياز يبحث عن أساس الإنهاء. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b24_brokerage_earnest_commission",
        family="special",
        title="العربون والعمولة في الوساطة العقارية",
        question_type="multi_issue",
        expected_regulations=["real-estate-brokerage-law"],
        allowed_regulations=["real-estate-brokerage-law"],
        expected_articles=[13, 14, 15, 16],
        min_expected_regulation_hits=1,
        min_expected_article_hits=4,
        contamination_traps=["civil-transactions-law", "labor-law", "companies-law"],
        sub_issues=[
            {
                "issue": "ضوابط عربون الصفقة العقارية وحده الأقصى",
                "expected_regulations": ["real-estate-brokerage-law"],
                "expected_articles": [13],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "تحديد عمولة الوساطة وطرف تحملها",
                "expected_regulations": ["real-estate-brokerage-law"],
                "expected_articles": [14],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "حالات استحقاق الوسيط للعمولة",
                "expected_regulations": ["real-estate-brokerage-law"],
                "expected_articles": [15],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "ضوابط تسلم الوسيط للأموال وعدم التصرف فيها خارج الحدود النظامية",
                "expected_regulations": ["real-estate-brokerage-law"],
                "expected_articles": [16],
                "min_expected_article_hits": 1,
            },
        ],
        notes="مصمم لاختبار الحزمة الخاصة بنظام الوساطة العقارية بدل الاكتفاء بقاعدة عمولة عامة أو عربون مدني مجرد.",
        questions={
            "anchor": "وسيط عقاري أخذ من المشتري عربونًا نسبته 8% من الصفقة، ثم تعثرت الصفقة، فاحتفظ بالعربون وادعى استحقاق العمولة كاملة. ما النظام والمواد الأقرب؟",
            "working_a": "في صفقة عقارية عبر وسيط مرخص، تسلم الوسيط مبلغًا وسمّاه عربونًا بنسبة تتجاوز 5%، ثم طالب كذلك بعمولته كاملة بعد تعثر الصفقة. ما المرجع النظامي الأقرب؟",
            "working_b": "النزاع يتعلق بوسيط عقاري احتفظ بعربون مرتفع النسبة وتحدث عن استحقاق عمولته رغم عدم إتمام الصفقة. ما النظام والمواد الأقرب؟",
            "heldout": "خلاف عقاري حول وسيط تسلم مبلغًا من أحد الأطراف، اعتبره عربونًا، ثم ادعى العمولة واحتفظ بالمبلغ بعد تعثر البيع. ما النظام والمواد الأقرب؟",
        },
    ),
    make_bundle(
        bundle_id="b25_workplace_harassment_mechanism",
        family="special",
        title="التحرش في بيئة العمل وآلية الشكوى والتحقيق السري",
        question_type="cross_domain",
        expected_regulations=["nzam-mkafhh-jrymh-althrsh", "workplace-behavioral-misconduct-controls"],
        allowed_regulations=["nzam-mkafhh-jrymh-althrsh", "workplace-behavioral-misconduct-controls", "labor-law"],
        expected_articles=[5],
        min_expected_regulation_hits=2,
        min_expected_article_hits=1,
        contamination_traps=["criminal-procedure-law", "companies-law"],
        sub_issues=[
            {
                "issue": "المرجع الخاص بجريمة التحرش في بيئة العمل",
                "expected_regulations": ["nzam-mkafhh-jrymh-althrsh"],
                "expected_articles": [5],
                "min_expected_article_hits": 1,
            },
            {
                "issue": "ضوابط الحماية من التعديات السلوكية وآليات الشكوى داخل المنشأة",
                "expected_regulations": ["workplace-behavioral-misconduct-controls"],
                "expected_articles": [],
                "min_expected_regulation_hits": 1,
            },
        ],
        notes="الهدف هو تثبيت النظام الخاص وضوابط بيئة العمل قبل أي إحالة عامة إلى السلامة أو الإجراءات الجزائية.",
        questions={
            "anchor": "موظفة في منشأة خاصة اشتكت من رسائل وإيحاءات وطلب لقاءات منفردة من مديرها، وتقول إن المنشأة لم تضع آلية داخلية فعالة لاستقبال الشكوى أو التحقيق السري. ما الأنظمة واللوائح ذات الصلة؟",
            "working_a": "عاملة في القطاع الخاص تقول إن مديرها أرسل لها رسائل ذات إيحاءات وطلب الخلوة بها، كما أن المنشأة لا تملك مسارًا داخليًا واضحًا وسريًا لمعالجة الشكوى. ما الأنظمة واللوائح الأقرب؟",
            "working_b": "القضية تتعلق بتحرش في بيئة العمل وبغياب آلية داخلية جادة لتلقي الشكاوى والتحقق منها بسرية. ما المرجع النظامي الأقرب؟",
            "heldout": "في منشأة أهلية، اشتكت موظفة من سلوكيات ورسائل من مديرها تمس الحياء، وتقول إن جهة العمل لم تضع قناة شكوى وتحقيق سرية وفعالة. ما الأنظمة واللوائح ذات الصلة؟",
        },
    ),
]


def build_case(bundle_index: int, bundle: dict[str, Any], variant: str) -> dict[str, Any]:
    split = "working" if variant in WORKING_VARIANTS else "heldout"
    question_id = f"teacher_b1_{bundle_index:03d}_{variant}"
    return {
        "question_id": question_id,
        "bundle_id": bundle["bundle_id"],
        "bundle_index": bundle_index,
        "bundle_family": bundle["family"],
        "bundle_title": bundle["title"],
        "benchmark_category": f"teacher_batch1_{bundle['bundle_id']}",
        "question_type": bundle["question_type"],
        "split": split,
        "variant": variant,
        "difficulty": DIFFICULTY_BY_VARIANT[variant],
        "question": bundle["questions"][variant],
        "expected_regulations": bundle["expected_regulations"],
        "allowed_regulations": bundle["allowed_regulations"],
        "expected_articles": bundle["expected_articles"],
        "min_expected_regulation_hits": bundle["min_expected_regulation_hits"],
        "min_expected_article_hits": bundle["min_expected_article_hits"],
        "expected_behavior": "answer",
        "contamination_traps": bundle["contamination_traps"],
        "sub_issues": bundle["sub_issues"],
        "notes": bundle["notes"],
    }


def validate_bundles(bundles: list[dict[str, Any]], regulation_slugs: set[str], article_map: dict[str, set[int]]) -> None:
    bundle_ids = [bundle["bundle_id"] for bundle in bundles]
    if len(bundle_ids) != len(set(bundle_ids)):
        raise ValueError("Duplicate bundle_id detected.")
    if len(bundles) != 25:
        raise ValueError(f"Expected 25 bundles, found {len(bundles)}.")

    for bundle in bundles:
        for slug in bundle["expected_regulations"] + bundle["allowed_regulations"]:
            if slug not in regulation_slugs:
                raise ValueError(f"Unknown regulation slug in bundle {bundle['bundle_id']}: {slug}")

        article_universe: set[int] = set()
        for slug in bundle["expected_regulations"]:
            article_universe.update(article_map.get(slug, set()))

        for article in bundle["expected_articles"]:
            if article not in article_universe:
                raise ValueError(
                    f"Article {article} in bundle {bundle['bundle_id']} not found under expected regulations."
                )

        missing_variants = {key for key in ("anchor", "working_a", "working_b", "heldout") if key not in bundle["questions"]}
        if missing_variants:
            raise ValueError(f"Bundle {bundle['bundle_id']} missing variants: {sorted(missing_variants)}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_manifest(working_rows: list[dict[str, Any]], heldout_rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = working_rows + heldout_rows
    family_counts = Counter(row["bundle_family"] for row in all_rows)
    variant_counts = Counter(row["variant"] for row in all_rows)
    split_counts = Counter(row["split"] for row in all_rows)
    return {
        "batch_id": "teacher_batch1_v1",
        "total_bundles": len(BUNDLES),
        "total_cases": len(all_rows),
        "working_cases": len(working_rows),
        "heldout_cases": len(heldout_rows),
        "family_counts": dict(sorted(family_counts.items())),
        "variant_counts": dict(sorted(variant_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "bundle_ids": [bundle["bundle_id"] for bundle in BUNDLES],
    }


def build_plan_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Teacher Batch 1 Plan",
        "",
        "هذه الدفعة الأولى من مسار Teacher Loop مبنية على 25 bundle × 4 variants = 100 قضية.",
        "",
        "## الملخص",
        "",
        f"- عدد الـbundles: {manifest['total_bundles']}",
        f"- إجمالي القضايا: {manifest['total_cases']}",
        f"- قضايا working: {manifest['working_cases']}",
        f"- قضايا held-out: {manifest['heldout_cases']}",
        "",
        "## التوزيع حسب العائلة",
        "",
    ]
    for family, count in manifest["family_counts"].items():
        lines.append(f"- `{family}`: {count} قضية")
    lines.extend(
        [
            "",
            "## قائمة الـBundles",
            "",
        ]
    )
    for idx, bundle in enumerate(BUNDLES, start=1):
        lines.append(f"{idx}. `{bundle['bundle_id']}`: {bundle['title']}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    regulation_slugs = load_regulation_slugs()
    article_map = load_article_map()
    validate_bundles(BUNDLES, regulation_slugs, article_map)

    working_rows: list[dict[str, Any]] = []
    heldout_rows: list[dict[str, Any]] = []

    for index, bundle in enumerate(BUNDLES, start=1):
        for variant in WORKING_VARIANTS:
            working_rows.append(build_case(index, bundle, variant))
        for variant in HELDOUT_VARIANTS:
            heldout_rows.append(build_case(index, bundle, variant))

    if len(working_rows) != 75 or len(heldout_rows) != 25:
        raise ValueError("Unexpected split counts generated for batch 1.")

    write_jsonl(WORKING_OUTPUT, working_rows)
    write_jsonl(HELDOUT_OUTPUT, heldout_rows)

    manifest = build_manifest(working_rows, heldout_rows)
    MANIFEST_OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    PLAN_OUTPUT.write_text(build_plan_markdown(manifest), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
