"""Build a 1000-case gold benchmark for Saudi legal RAG package recall.

This benchmark is evaluation-only. The RAG service must only receive the
question text. Gold labels stay in data/eval and are consumed by offline
scoring scripts after the service returns.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REGULATIONS_PATH = ROOT / "data" / "structured" / "regulations.json"
BY_REGULATION_DIR = ROOT / "data" / "structured" / "by_regulation"
V1_CASES_PATH = ROOT / "data" / "eval" / "gold_package_recall_v1" / "gold_package_recall_100_v1.jsonl"
OUT_DIR = ROOT / "data" / "eval" / "gold_package_recall_v2_1000"
CASES_PATH = OUT_DIR / "gold_package_recall_1000_v2.jsonl"
MANIFEST_PATH = OUT_DIR / "manifest.json"
README_PATH = OUT_DIR / "README.md"
TARGET_CASES = 1000

SPLIT_CYCLE = ("dev", "regression", "heldout", "regression", "dev", "heldout", "regression", "heldout")

AR_STOPWORDS = {
    "في",
    "من",
    "على",
    "إلى",
    "الى",
    "عن",
    "أن",
    "ان",
    "أو",
    "او",
    "لا",
    "ما",
    "مع",
    "كل",
    "أي",
    "اي",
    "هذه",
    "هذا",
    "تلك",
    "ذلك",
    "كان",
    "كانت",
    "يكون",
    "يجب",
    "يجوز",
    "النظام",
    "اللائحة",
    "لائحة",
    "المادة",
    "الفقرة",
    "أحكام",
    "نظام",
    "وفق",
    "دون",
    "غير",
    "ذات",
    "ذو",
    "ذوي",
    "بحسب",
    "بموجب",
    "لدى",
    "بعد",
    "قبل",
    "إذا",
    "اذا",
    "المملكة",
    "السعودية",
    "العربية",
    "يجوز",
    "ويجوز",
    "يحدد",
    "تحدد",
    "يصدر",
    "تصدر",
}

DOMAIN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "labor",
        (
            "نظام العمل",
            "اللائحة التنفيذية لنظام العمل",
            "حماية الأجور",
            "حماية الاجور",
            "توثيق عقود العمل",
            "عقود العمل",
            "التأمينات الاجتماعية",
            "التامينات الاجتماعية",
            "تعطل عن العمل",
        ),
    ),
    ("ecommerce_consumer", ("تجارة إلكترونية", "التجارة الإلكترونية", "غش", "مستهلك", "منتج")),
    ("privacy_data", ("بيانات", "خصوصية", "معلومات ائتمانية", "جرائم معلوماتية", "سيبراني")),
    ("tax_zatca", ("ضريبة", "زكاة", "زكاه", "جمارك", "فوترة")),
    ("corporate_commercial", ("شركات", "شركة", "تجاري", "امتياز", "وكالات", "سجل", "علامات", "أوراق تجارية")),
    ("civil_evidence_procedure", ("مدنية", "إثبات", "تنفيذ", "مرافعات", "محاكم", "تحكيم", "توثيق")),
    ("procurement_admin", ("منافسات", "مشتريات", "ديوان المظالم", "إداري", "اداري", "بلدية", "ترخيص")),
    ("real_estate_construction", ("عقار", "عقارية", "بناء", "كود البناء", "مقاولين", "رهن عقاري")),
    ("finance_insolvency", ("إفلاس", "افلاس", "بنوك", "تمويل", "تأمين", "مدفوعات", "ساما")),
    ("ip_media_telecom", ("مؤلف", "ملكية فكرية", "إعلام", "اتصالات", "علامات تجارية", "براءات")),
    ("health_food_drugs", ("صحي", "صحية", "غذاء", "دواء", "تجميل", "طبي", "صيدلانية")),
    ("family_criminal_protection", ("أحوال شخصية", "حماية", "طفل", "أحداث", "جزائي", "جنائي", "تحرش", "إرهاب")),
]

COMPANION_BY_SLUG: dict[str, list[str]] = {
    "labor-law": [
        "labor-implementing-regulation",
        "wage-protection-rules",
        "labor-contract-documentation-rules",
        "labor-violations-penalties-table",
    ],
    "labor-implementing-regulation": ["labor-law", "labor-violations-penalties-table"],
    "wage-protection-rules": ["labor-law", "labor-implementing-regulation"],
    "labor-contract-documentation-rules": ["labor-law", "labor-implementing-regulation"],
    "labor-violations-penalties-table": ["labor-law", "labor-implementing-regulation"],
    "e-commerce-law": ["ecommerce-implementing-regulation"],
    "ecommerce-implementing-regulation": ["e-commerce-law"],
    "personal-data-protection-law": ["pdpl-implementing-regulation"],
    "pdpl-implementing-regulation": ["personal-data-protection-law"],
    "pdpl-transfer-regulation": ["personal-data-protection-law", "pdpl-implementing-regulation"],
    "nzam-drybh-alqymh-almdafh": ["zatca-vat-implementing-regulation"],
    "zatca-vat-implementing-regulation": ["nzam-drybh-alqymh-almdafh"],
    "zatca-e-invoicing-bylaw": ["nzam-drybh-alqymh-almdafh", "zatca-e-invoicing-technical-controls"],
    "zatca-e-invoicing-technical-controls": ["zatca-e-invoicing-bylaw", "nzam-drybh-alqymh-almdafh"],
    "government-tenders-and-procurement-law": [
        "government-procurement-implementing-regulation",
        "procurement-conflict-of-interest-regulation",
        "procurement-conduct-ethics-regulation",
    ],
    "government-procurement-implementing-regulation": ["government-tenders-and-procurement-law"],
    "procurement-conflict-of-interest-regulation": ["government-tenders-and-procurement-law"],
    "procurement-conduct-ethics-regulation": ["government-tenders-and-procurement-law"],
    "nzam-aliflas": ["bankruptcy-implementing-regulation"],
    "bankruptcy-implementing-regulation": ["nzam-aliflas"],
    "companies-law": ["companies-implementing-regulation"],
    "companies-implementing-regulation": ["companies-law"],
    "nzam-alamtyaz-altjary": ["nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh"],
    "nzam-alwkalat-altjaryh": ["nzam-almhakm-altjaryh", "civil-transactions-law"],
    "nzam-alawraq-altjaryh": ["law-of-evidence", "nzam-almhakm-altjaryh"],
    "nzam-althkym": ["execution-law", "law-of-evidence"],
    "execution-law": ["execution-implementing-regulation", "law-of-evidence"],
    "execution-implementing-regulation": ["execution-law"],
    "law-of-evidence": ["law-of-sharia-procedure"],
    "nzam-almhakm-altjaryh": ["law-of-evidence"],
    "civil-transactions-law": ["law-of-evidence"],
    "nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth": ["real-estate-brokerage-law", "civil-transactions-law"],
    "real-estate-brokerage-law": ["civil-transactions-law", "law-of-evidence"],
    "nzam-altsjyl-alayny-llaqar": ["civil-transactions-law", "law-of-evidence"],
    "nzam-ttbyq-kwd-albnaa-alsawdy": ["civil-transactions-law", "law-of-evidence"],
    "nzam-alalamat-altjaryh": ["qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
    "anti-cybercrime-law": ["personal-data-protection-law"],
    "nzam-hmayh-altfl": ["personal-status-law"],
}

DOMAIN_DISTRACTORS: dict[str, list[str]] = {
    "labor": ["government-tenders-and-procurement-law", "nzam-alkhdmh-almdnyh", "civil-transactions-law"],
    "ecommerce_consumer": ["companies-law", "nzam-almnafsh", "nzam-aliflas"],
    "privacy_data": ["e-commerce-law", "commercial-fraud-law", "companies-law"],
    "tax_zatca": ["nzam-drybh-aldkhl", "nzam-jbayh-alzkah", "e-commerce-law"],
    "corporate_commercial": ["labor-law", "nzam-aliflas", "e-commerce-law"],
    "civil_evidence_procedure": ["labor-law", "government-tenders-and-procurement-law", "e-commerce-law"],
    "procurement_admin": ["civil-transactions-law", "nzam-tsnyf-almqawlyn", "labor-law"],
    "real_estate_construction": ["government-tenders-and-procurement-law", "labor-law", "e-commerce-law"],
    "finance_insolvency": ["e-commerce-law", "labor-law", "nzam-ttbyq-kwd-albnaa-alsawdy"],
    "ip_media_telecom": ["e-commerce-law", "commercial-fraud-law", "labor-law"],
    "health_food_drugs": ["e-commerce-law", "commercial-fraud-law", "labor-law"],
    "family_criminal_protection": ["labor-law", "civil-transactions-law", "e-commerce-law"],
    "long_tail_official": ["labor-law", "civil-transactions-law", "e-commerce-law"],
}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def title_alias(title: str) -> str:
    value = normalize_space(title)
    for prefix in (
        "اللائحة التنفيذية لنظام ",
        "لائحة تنظيم ",
        "لائحة ",
        "نظام ",
        "القانون \"النظام\" الموحد ",
        "القانون ",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    return value


def domain_for(row: dict[str, Any], article: dict[str, Any] | None = None) -> str:
    title_haystack = " ".join([str(row.get("slug") or ""), str(row.get("title_ar") or "")])
    for domain, needles in DOMAIN_KEYWORDS:
        if any(needle in title_haystack for needle in needles):
            return domain
    topic_tags = set((article or {}).get("topic_tags") or [])
    if "labor" in topic_tags and any(token in title_haystack for token in ("labor", "work", "العمل", "الأجور")):
        return "labor"
    if "enforcement" in topic_tags and any(token in title_haystack for token in ("تنفيذ", "مرافعات", "إثبات", "تحكيم")):
        return "civil_evidence_procedure"
    return "long_tail_official"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_regulation_articles(slug: str) -> list[dict[str, Any]]:
    path = BY_REGULATION_DIR / f"{slug}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("articles") or [])


def article_score(article: dict[str, Any]) -> float:
    tags = set(article.get("legal_function_tags") or [])
    score = 0.0
    weights = {
        "obligation": 4.0,
        "prohibition": 4.0,
        "penalty": 3.8,
        "remedy": 3.6,
        "procedure": 3.2,
        "authority": 3.0,
        "condition": 2.8,
        "deadline": 2.3,
        "exception": 2.0,
        "definition": -0.8,
    }
    for tag, weight in weights.items():
        if tag in tags:
            score += weight
    text_len = len(article_text(article))
    if 180 <= text_len <= 1800:
        score += 2.0
    elif text_len > 80:
        score += 1.0
    heading = normalize_space(article.get("article_heading") or article.get("article_label") or "")
    if heading and "تعريف" not in heading:
        score += 0.5
    return score


def article_text(article: dict[str, Any]) -> str:
    return normalize_space(str(article.get("text_for_index") or article.get("text_verbatim") or ""))


def choose_articles(articles: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    usable = [article for article in articles if len(article_text(article)) >= 60]
    if not usable:
        usable = articles[:]
    chosen = sorted(usable, key=article_score, reverse=True)[:limit]
    return chosen or articles[:1]


def snippet_for_question(row: dict[str, Any], article: dict[str, Any], variant: int) -> str:
    text = article_text(article)
    title = str(row.get("title_ar") or "")
    alias = title_alias(title)
    for removable in (title, alias):
        if removable:
            text = text.replace(removable, "")
    text = normalize_space(text)
    words = re.findall(r"[\u0600-\u06FF]{4,}", text)
    keywords = []
    seen = set()
    for word in words:
        word = word.strip("،.؛:()[]")
        for prefix in ("و", "ف", "ب", "ل"):
            if len(word) > 5 and word.startswith(prefix) and word[1:] in AR_STOPWORDS:
                word = word[1:]
        if word in AR_STOPWORDS or word in seen:
            continue
        seen.add(word)
        keywords.append(word)
        if len(keywords) >= 10:
            break
    compact = "، ".join(keywords[:7])
    excerpt = text[:360].rstrip("،.؛: ")
    field = alias[:110] if alias else "القطاع محل الواقعة"
    if variant % 3 == 0 and compact:
        return f"في مجال {field}، ظهرت في 2026 واقعة عملية تتكرر فيها عناصر: {compact}."
    if variant % 3 == 1 and excerpt:
        return f"في مجال {field}، ظهرت واقعة تحتاج إلى تحديد المرجع الحاكم: {excerpt}."
    if compact and excerpt:
        return f"في مجال {field}، وقائع مختلطة تشمل {compact}، مع حاجة لتحديد المرجع النظامي والمواد الأقرب."
    return excerpt or compact or "واقعة تحتاج إلى تحديد النظام السعودي المختص والمواد الأقرب."


def companions_for(slug: str, all_slugs: set[str]) -> list[str]:
    companions = [item for item in COMPANION_BY_SLUG.get(slug, []) if item in all_slugs and item != slug]
    return list(dict.fromkeys(companions))[:5]


def exclusions_for(domain: str, core: list[str], companions: list[str], all_slugs: set[str]) -> list[str]:
    blocked = set(core) | set(companions)
    values = [slug for slug in DOMAIN_DISTRACTORS.get(domain, DOMAIN_DISTRACTORS["long_tail_official"]) if slug in all_slugs and slug not in blocked]
    return list(dict.fromkeys(values))[:4]


def seed_round42_labor_case() -> dict[str, Any]:
    return {
        "domain": "labor",
        "question": (
            "منشأة خاصة في 2026 لديها موظفون سعوديون وغير سعوديين. أخرت الرواتب شهرين، "
            "ومددت فترة التجربة عبر البريد الإلكتروني، وأنهت عقد موظف محدد المدة قبل نهايته بحجة ضعف الأداء، "
            "وطلبت من موظف آخر العمل عن بعد دون توثيق واضح، ولم توثق بعض العقود في منصة قوى. "
            "يوجد شرط عدم منافسة واسع لمدة سنتين يشمل كل المملكة، ويطلب العمال مستحقات نهاية الخدمة وتعويضًا عن الفصل."
        ),
        "required_core_regulations": ["labor-law"],
        "required_companion_regulations": [
            "labor-implementing-regulation",
            "wage-protection-rules",
            "labor-contract-documentation-rules",
            "labor-violations-penalties-table",
        ],
        "optional_regulations": ["nzam-altamynat-alajtmaayh", "law-of-evidence"],
        "excluded_regulations": ["government-tenders-and-procurement-law", "government-procurement-implementing-regulation"],
        "gold_answer_summary": (
            "المركز نظام العمل ولائحته: الأجور وحماية الأجور، فترة التجربة، إنهاء العقد المحدد، "
            "توثيق العقود في المنصة المعتمدة، شرط عدم المنافسة، مكافأة نهاية الخدمة والتعويض."
        ),
        "source_note": "seeded_from_round42_failed_manual_probe",
    }


def convert_seed_case(case: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "domain": case.get("domain") or "seed",
        "question": case["question"],
        "required_core_regulations": case.get("required_core_regulations", []),
        "required_companion_regulations": case.get("required_companion_regulations", []),
        "optional_regulations": case.get("optional_regulations", []),
        "excluded_regulations": case.get("excluded_regulations", []),
        "gold_answer_summary": case.get("gold_answer_summary", ""),
        "source_note": "seeded_from_gold_package_recall_v1",
    }
    return keep


def make_article_case(
    row: dict[str, Any],
    article: dict[str, Any],
    all_slugs: set[str],
    variant: int,
) -> dict[str, Any]:
    slug = row["slug"]
    title = row.get("title_ar") or slug
    domain = domain_for(row, article)
    core = [slug]
    companions = companions_for(slug, all_slugs)
    optional = [item for item in (row.get("related_regulations") or []) if item in all_slugs and item not in core + companions]
    excluded = exclusions_for(domain, core, companions, all_slugs)
    article_label = article.get("article_label") or article.get("article_heading") or article.get("article_index")
    prompt_prefix = (
        "استرجع النظام أو اللائحة السعودية الواجبة التطبيق والمواد الأقرب، "
        "وافصل بين المرجع الأساسي والمراجع المساندة."
    )
    question = f"{snippet_for_question(row, article, variant)} {prompt_prefix}"
    return {
        "domain": domain,
        "question": question,
        "required_core_regulations": core,
        "required_companion_regulations": companions,
        "optional_regulations": optional[:5],
        "excluded_regulations": excluded,
        "expected_articles": [
            {
                "regulation_slug": slug,
                "article_label": str(article_label),
                "citation": article.get("citation_short_ar"),
            }
        ],
        "gold_answer_summary": f"يجب أن يظهر: {title}. المادة المرجعية: {article.get('citation_short_ar') or article_label}.",
        "source_note": "article_anchored_machine_generated",
        "regulation_title_ar": title,
        "regulation_slug": slug,
    }


def assign_ids(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for index, case in enumerate(cases, start=1):
        row = dict(case)
        row["question_id"] = f"gpr_v2_{index:04d}"
        row["benchmark_category"] = f"gold_package_recall_v2_{row.get('domain') or 'unknown'}"
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    regs = json.loads(REGULATIONS_PATH.read_text(encoding="utf-8"))
    regs = [row for row in regs if row.get("slug")]
    all_slugs = {row["slug"] for row in regs}

    cases: list[dict[str, Any]] = [seed_round42_labor_case()]
    cases.extend(convert_seed_case(case) for case in load_jsonl(V1_CASES_PATH))

    needed = TARGET_CASES - len(cases)
    article_choices: dict[str, list[dict[str, Any]]] = {}
    for row in regs:
        article_choices[row["slug"]] = choose_articles(load_regulation_articles(row["slug"]))

    per_slug_counter: dict[str, int] = defaultdict(int)
    generated = 0
    ordered_regs = sorted(regs, key=lambda item: (item.get("catalog_source") != "official_catalog", item["slug"]))
    while generated < needed:
        for row in ordered_regs:
            slug = row["slug"]
            choices = article_choices.get(slug) or []
            if not choices:
                continue
            index = per_slug_counter[slug] % len(choices)
            cases.append(make_article_case(row, choices[index], all_slugs, per_slug_counter[slug]))
            per_slug_counter[slug] += 1
            generated += 1
            if generated >= needed:
                break

    cases = assign_ids(cases[:TARGET_CASES])
    with CASES_PATH.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    core_slugs = Counter(slug for case in cases for slug in case.get("required_core_regulations", []))
    domains = Counter(case.get("domain") for case in cases)
    splits = Counter(case.get("split") for case in cases)
    manifest = {
        "benchmark_id": "gold_package_recall_1000_v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_cases": TARGET_CASES,
        "cases": len(cases),
        "seed_cases": 1 + len(load_jsonl(V1_CASES_PATH)),
        "article_generated_cases": len(cases) - (1 + len(load_jsonl(V1_CASES_PATH))),
        "regulations_available": len(regs),
        "regulations_covered_as_core": len(core_slugs),
        "official_catalog_regulations_covered": sum(
            1 for row in regs if row.get("catalog_source") == "official_catalog" and row["slug"] in core_slugs
        ),
        "custom_catalog_regulations_covered": sum(
            1 for row in regs if row.get("catalog_source") == "custom_catalog" and row["slug"] in core_slugs
        ),
        "split_counts": dict(splits),
        "domain_counts": dict(domains),
        "least_repeated_core_slugs": core_slugs.most_common()[-20:],
        "anti_leakage": {
            "service_payload_fields": ["question", "answer_mode", "retrieval_profile"],
            "gold_labels_used_only_offline": True,
            "do_not_import_into_rag_engine": True,
        },
        "known_limitations": [
            "Article-anchored cases are machine generated from the official structured corpus and should be spot-checked over time.",
            "Collection scoring does not penalize extra unrelated regulations; contamination scoring is a separate later layer.",
            "Some one-article OCR-heavy supplementary references may produce less natural scenario wording.",
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    README_PATH.write_text(
        "\n".join(
            [
                "# Gold Package Recall 1000 v2",
                "",
                "معيار ذهبي خارجي لاختبار جمع الحزمة النظامية في RAG القانوني السعودي.",
                "",
                "## القاعدة",
                "",
                "- لا تُرسل الإجابات الذهبية إلى الخدمة.",
                "- يرسل runner نص السؤال فقط مع `answer_mode=benchmark` و `retrieval_profile=jamia_recall`.",
                "- التصحيح يحصل بعد رجوع الرد من خلال `required_core_regulations` و `required_companion_regulations`.",
                "- المصادر الزائدة تسجل في هذه المرحلة ولا تخصم؛ تنقية التلويث مرحلة لاحقة.",
                "",
                "## التركيب",
                "",
                f"- الحالات: `{len(cases)}`",
                f"- الأنظمة/اللوائح المغطاة كـcore: `{len(core_slugs)}` من `{len(regs)}`",
                f"- seed cases: `{manifest['seed_cases']}`",
                f"- article-generated cases: `{manifest['article_generated_cases']}`",
                "",
                "## ملفات",
                "",
                "- `gold_package_recall_1000_v2.jsonl`",
                "- `manifest.json`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Wrote {CASES_PATH}")


if __name__ == "__main__":
    main()
