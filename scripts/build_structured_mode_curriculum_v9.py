"""Build a broader, cleaner structured curriculum for v9 master training."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTICLES_PATH = ROOT / "data" / "structured" / "articles.jsonl"
PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "prompt_templates"
OUTPUT_DIR = ROOT / "data" / "training" / "structured_mode_curriculum_v9" / "sft_messages"

PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}

TRAIN_REGULATIONS = {
    "personal-status-law",
    "labor-law",
    "copyright-law",
    "companies-law",
    "civil-transactions-law",
    "law-of-evidence",
    "protection-from-abuse-law",
    "whistleblowers-witnesses-experts-and-victims-protection-law",
    "electronic-transactions-law",
    "universities-law",
}

RESERVED_OOD_REGULATIONS = {
    "basic-law-of-governance",
    "criminal-procedure-law",
    "law-of-sharia-procedure",
    "government-tenders-and-procurement-law",
    "real-estate-brokerage-law",
    "communications-and-information-technology-law",
}

SUPPORTED_ARTICLE_TYPES = {
    "rights",
    "condition",
    "prohibition",
    "exception",
    "procedure",
    "liability",
    "penalty",
    "violation",
    "definition",
    "general",
}

QUESTION_TEMPLATE_BY_TYPE = {
    "rights": "ما الحق أو الاختصاص الذي يثبته النظام في مسألة {topic}؟",
    "condition": "ما الشروط أو الالتزامات النظامية المتعلقة بـ{topic}؟",
    "prohibition": "هل يوجد منع أو حظر نظامي متعلق بـ{topic}؟",
    "exception": "ما القيد أو الاستثناء النظامي الوارد في مسألة {topic}؟",
    "procedure": "ما الإجراء أو السلطة النظامية المتصلة بـ{topic}؟",
    "liability": "ما المسؤولية أو الأثر التعويضي المتعلق بـ{topic}؟",
    "penalty": "ما الجزاء أو العقوبة النظامية المرتبطة بـ{topic}؟",
    "violation": "ما المخالفة أو التعدي النظامي في مسألة {topic}؟",
    "definition": "كيف يعرّف النظام مسألة {topic} أو يحدد حكمها الأولي؟",
    "general": "ما الحكم النظامي المباشر في مسألة {topic}؟",
}

SECTION_TEMPLATE_BY_TYPE = {
    "exception": "هذا النص يبرز قيدًا أو استثناءً يجب الوقوف عند حدوده كما ورد دون توسع.",
    "condition": "الحكم هنا مرتبط بالشروط أو الالتزامات الواردة في النص نفسه، ولا يصح فصله عنها.",
    "prohibition": "ظاهر النص يفيد المنع أو الحظر في الحدود المذكورة فيه.",
    "procedure": "يتعلق النص بإجراء أو سلطة نظامية محددة، ويجب التزامها كما وردت.",
    "liability": "يفهم من النص أثر يتعلق بالمسؤولية أو التعويض في حدود ما قرره النظام.",
    "penalty": "يتضمن النص جزاءً أو عقوبة، ولا يثبت منه ما وراء الحد الوارد فيه.",
    "violation": "يفيد النص وجود مخالفة أو تعدٍّ منظم، ويقتصر الاستناد على عبارته.",
    "rights": "يفيد النص تقرير حق أو اختصاص، ولا يثبت منه وحده ما وراء هذا الحق.",
    "definition": "النص هنا يقرر تعريفًا أو حكمًا تأسيسيًا يجب الانطلاق منه.",
    "general": "لم يظهر في هذا النص المسترجع استثناء مستقل خارج حدود الحكم الوارد فيه.",
}

MIN_TEXT_CHARS = 60
MAX_TEXT_CHARS = 1600
CONTEXT_CHAR_LIMIT = 520


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(text: str) -> str:
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in (text or "").splitlines()]
    paragraphs = [part for part in paragraphs if part]
    return "\n".join(paragraphs)


def compact_text(text: str, limit: int = CONTEXT_CHAR_LIMIT) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def load_system_prompt(prompt_dir: Path, mode: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE[mode]
    return (prompt_dir / prompt_name).read_text(encoding="utf-8").strip()


def article_text(row: dict[str, Any]) -> str:
    return normalize_text(str(row.get("text_verbatim") or row.get("text_for_index") or ""))


def is_generic_heading(text: str) -> bool:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return True
    generic_patterns = [
        r"^الأولى$",
        r"^الثانية$",
        r"^الثالثة$",
        r"^الرابعة$",
        r"^الخامسة$",
        r"^السادسة$",
        r"^السابعة$",
        r"^الثامنة$",
        r"^التاسعة$",
        r"^العاشرة$",
        r"^الحادية عشرة$",
        r"^الثانية عشرة$",
        r"^الثالثة عشرة$",
        r"^الرابعة عشرة$",
        r"^الخامسة عشرة$",
        r"^السادسة عشرة$",
        r"^السابعة عشرة$",
        r"^الثامنة عشرة$",
        r"^التاسعة عشرة$",
        r"^العشرون$",
        r"^المادة\s+.+$",
    ]
    return any(re.fullmatch(pattern, value) for pattern in generic_patterns)


def topic_from_text(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    first_line = next((line.strip() for line in normalized.splitlines() if line.strip()), "")
    if not first_line:
        return ""
    first_line = re.split(r"[.:\n؛،]", first_line, maxsplit=1)[0].strip()
    words = first_line.split()
    return " ".join(words[:8]).strip()


def article_topic(row: dict[str, Any]) -> str:
    tags = [str(tag).strip() for tag in (row.get("topic_tags_ar") or []) if str(tag).strip()]
    if tags:
        return " / ".join(tags[:2])
    heading = str(row.get("article_heading") or row.get("article_label") or "").strip()
    if heading and not is_generic_heading(heading):
        return heading
    fallback = topic_from_text(article_text(row))
    if fallback:
        return fallback
    return str(row.get("regulation_title_ar") or "المسألة النظامية").strip()


def build_question(row: dict[str, Any]) -> str:
    topic = article_topic(row)
    article_type = row.get("article_type") or "general"
    template = QUESTION_TEMPLATE_BY_TYPE.get(article_type, QUESTION_TEMPLATE_BY_TYPE["general"])
    return template.format(topic=topic)


def build_context(row: dict[str, Any]) -> str:
    citation = str(row.get("citation_short_ar") or "").strip()
    article_type_ar = str(row.get("article_type_label_ar") or "حكم عام").strip()
    article_index = row.get("article_index", "")
    article_label = str(row.get("article_label") or "").strip()
    text = compact_text(article_text(row))
    return (
        f"[المصدر 1 | النظام: {row.get('regulation_title_ar', '')} | رقم المادة: {article_index} | "
        f"تسمية المادة: {article_label} | نوع المادة: {article_type_ar} | الإحالة الرسمية: {citation}]\n"
        f"{text}"
    )


def build_user_message(row: dict[str, Any]) -> str:
    return f"القضية:\n{build_question(row)}\n\nالنصوص المسترجعة:\n{build_context(row)}"


def build_opinion_answer(row: dict[str, Any]) -> str:
    title = str(row.get("regulation_title_ar") or "").strip()
    citation = str(row.get("citation_short_ar") or "").strip()
    article_type_ar = str(row.get("article_type_label_ar") or "حكم عام").strip()
    article_type = row.get("article_type") or "general"
    rule_text = compact_text(article_text(row), limit=460)
    limitation_text = SECTION_TEMPLATE_BY_TYPE.get(article_type, SECTION_TEMPLATE_BY_TYPE["general"])
    return "\n".join(
        [
            "1) النظام المنطبق:",
            f"- {title}",
            "",
            "2) الحكم المباشر:",
            f"- يثبت من ظاهر النص المسترجع في {citation} أن: {rule_text}",
            "",
            "3) المواد المستند إليها:",
            f"- {citation} - {article_type_ar}",
            "",
            "4) القيود أو الاستثناءات:",
            f"- {limitation_text}",
            "",
            "5) ما لم يثبته النص:",
            "- هذا المثال التدريبي مبني على مادة مسترجعة واحدة؛ لذلك لا يثبت منه وحده ما وراء الحكم المباشر أو تفاصيل التطبيق على وقائع إضافية.",
            "",
            "6) الخلاصة العملية:",
            f"- الأقرب نظامًا في هذه المسألة هو الوقوف عند ظاهر {citation} دون التوسع في استنتاجات غير منصوصة أو وقائع غير معروضة.",
        ]
    ).strip()


def build_memo_answer(row: dict[str, Any]) -> str:
    topic = article_topic(row)
    title = str(row.get("regulation_title_ar") or "").strip()
    citation = str(row.get("citation_short_ar") or "").strip()
    article_type_ar = str(row.get("article_type_label_ar") or "حكم عام").strip()
    rule_text = compact_text(article_text(row), limit=520)
    return "\n".join(
        [
            "عنوان المذكرة:",
            f"مذكرة أولية بشأن {topic}",
            "",
            "السؤال محل الرأي:",
            f"ما الحكم النظامي المتعلق بـ{topic} في {title}؟",
            "",
            "الجواب المختصر:",
            f"- من ظاهر النص المسترجع، يثبت أن {rule_text}",
            "- درجة القوة: قوي من جهة الحكم المباشر، ومشروط من جهة التطبيق الواقعي.",
            "",
            "الوقائع ذات الأثر القانوني:",
            f"- الثابت في هذا المثال التدريبي هو وجود سؤال مباشر عن {topic}.",
            "- لا توجد وقائع إضافية أو مستندات مرفقة تتجاوز النص المسترجع.",
            "",
            "النظام أو النصوص المنطبقة:",
            f"- {citation} - {article_type_ar}",
            "",
            "المسائل القانونية:",
            "1. ما القاعدة النظامية المباشرة التي يقررها النص؟",
            "2. ما حدود الاعتماد على هذه المادة منفردة عند التطبيق العملي؟",
            "",
            "التحليل:",
            "المسألة الأولى:",
            f"- القاعدة النظامية: {rule_text}",
            "- التطبيق على الوقائع: في هذا المثال لا توجد إلا وقائع عامة، لذلك يقتصر التطبيق على بيان الحكم كما ورد في النص.",
            f"- النتيجة: المرجع المباشر هو {citation}.",
            "المسألة الثانية:",
            "- القاعدة النظامية: النص الحالي يكفي لإثبات الحكم المباشر، ولا يكفي وحده لإثبات جميع التفاصيل الواقعية المحتملة.",
            "- التطبيق: يلزم استكمال الوقائع إذا كان النزاع العملي يتجاوز مضمون المادة الواحدة.",
            "- النتيجة: تبقى أي تفاصيل إضافية رهن نصوص أو وقائع أخرى.",
            "",
            "الدفوع أو الاحتمالات المقابلة:",
            "- قد يدفع طرف آخر بأن الواقعة العملية تحتوي عناصر لم يعالجها هذا النص منفردًا.",
            "- مدى قوة هذا الاعتراض: متوسط من جهة التطبيق الواقعي، لا من جهة أصل الحكم المستفاد من المادة.",
            "",
            "ما لم يثبته النص أو الوقائع:",
            "- لا توجد وقائع تفصيلية لاختبار الاستثناءات أو الشروط الواقعية.",
            "- لا يثبت من هذا المثال وحده وجود نصوص مكملة أو معارضة.",
            "",
            "الخلاصة والتوصية العملية:",
            f"- المرجع المباشر هنا هو {citation}.",
            "- عند تحويل المثال إلى ملف عملي، يلزم ضم الوقائع التفصيلية والنصوص المكملة ذات الصلة قبل الجزم النهائي.",
        ]
    ).strip()


def build_analysis_answer(row: dict[str, Any]) -> str:
    topic = article_topic(row)
    title = str(row.get("regulation_title_ar") or "").strip()
    citation = str(row.get("citation_short_ar") or "").strip()
    article_type_ar = str(row.get("article_type_label_ar") or "حكم عام").strip()
    return "\n".join(
        [
            "1) التكييف الأولي للقضية:",
            f"- هذه مسألة نظامية أولية تتعلق بـ{topic} في إطار {title}.",
            "- المعروض هنا مثال تدريبي محدود بمادة مسترجعة واحدة.",
            "",
            "2) الأنظمة المحتملة الانطباق:",
            f"- {title}",
            "",
            "3) المسائل القانونية الأساسية:",
            "1. ما الحكم الصريح الذي تقرره المادة المسترجعة؟",
            "2. ما حدود الاستناد إلى هذا النص منفردًا؟",
            "",
            "4) ما يدعم الطرف الأول:",
            f"- التمسك بظاهر {citation}.",
            f"  - السند: {article_type_ar}",
            "  - قوة الحجة: قوية",
            "",
            "5) ما يدعم الطرف الثاني:",
            "- المجادلة بأن التطبيق العملي يتوقف على وقائع أو نصوص مكملة غير معروضة هنا.",
            "  - السند: استنتاج منظم من حدود المثال لا نص صريح مستقل.",
            "  - قوة الحجة: متوسطة",
            "",
            "6) نقاط الضعف:",
            "- يضعف موقف الطرف الأول إذا ظهرت وقائع خاصة أو نصوص لاحقة تغير نطاق المادة.",
            "- يضعف موقف الطرف الثاني إذا لم يقدم نصًا صريحًا يزيح ظاهر المادة المسترجعة.",
            "",
            "7) ما قد يغير النتيجة:",
            "- ورود وقائع إضافية ذات أثر نظامي.",
            "- ظهور مادة خاصة أو استثناء مرتبط بالمسألة.",
            "",
            "8) ما لم يثبته النص:",
            "- لا يثبت من هذا المثال وحده ما وراء الحكم المباشر الوارد في المادة المسترجعة.",
            "",
            "9) التقدير الأولي:",
            f"- من ظاهر النصوص، الموقف الأقوى هو الأخذ بظاهر {citation}.",
            "- هذا التقدير مشروط بعدم وجود نص خاص أو واقعة مغيرة للنتيجة.",
        ]
    ).strip()


def build_messages(row: dict[str, Any], prompt_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    user_message = build_user_message(row)
    answers = {
        "legal_opinion": build_opinion_answer(row),
        "legal_memo": build_memo_answer(row),
        "legal_analysis": build_analysis_answer(row),
    }
    payloads: list[tuple[str, dict[str, Any]]] = []
    for mode, assistant_text in answers.items():
        payloads.append(
            (
                mode,
                {
                    "messages": [
                        {"role": "system", "content": load_system_prompt(prompt_dir, mode)},
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": assistant_text},
                    ]
                },
            )
        )
    return payloads


def group_key(row: dict[str, Any]) -> tuple[str, str, str]:
    primary_tag = article_topic(row)
    article_type = str(row.get("article_type") or "general").strip()
    return (str(row.get("regulation_slug") or "").strip(), primary_tag, article_type)


def case_id(row: dict[str, Any]) -> str:
    slug = str(row.get("regulation_slug") or "").strip()
    index = int(row.get("article_index") or 0)
    return f"curriculum_v9::{slug}::{index}"


def is_candidate(row: dict[str, Any]) -> bool:
    slug = str(row.get("regulation_slug") or "").strip()
    if slug not in TRAIN_REGULATIONS:
        return False
    article_type = str(row.get("article_type") or "general").strip()
    if article_type not in SUPPORTED_ARTICLE_TYPES:
        return False
    if not article_topic(row):
        return False
    text = article_text(row)
    if len(text) < MIN_TEXT_CHARS or len(text) > MAX_TEXT_CHARS:
        return False
    if not str(row.get("citation_short_ar") or "").strip():
        return False
    return True


def candidate_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    text = article_text(row)
    text_len = len(text)
    paragraph_count = int(row.get("indexable_paragraph_count") or row.get("paragraph_count") or 0)
    article_index = int(row.get("article_index") or 0)
    return (
        int(120 <= text_len <= 850),
        int(80 <= text_len <= 1200),
        int(paragraph_count <= 6),
        min(paragraph_count, 6),
        -abs(text_len - 360),
        -article_index,
    )


def select_articles(
    rows: list[dict[str, Any]],
    *,
    max_cases: int,
    max_per_group: int,
    max_cases_per_regulation: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not is_candidate(row):
            continue
        grouped[group_key(row)].append(row)

    ordered_groups: list[tuple[tuple[str, str, str], list[dict[str, Any]]]] = []
    for key in sorted(grouped):
        candidates = sorted(grouped[key], key=candidate_rank, reverse=True)
        ordered_groups.append((key, candidates[:max_per_group]))

    selected: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    selected_per_regulation: Counter[str] = Counter()
    for round_index in range(max_per_group):
        for _, candidates in ordered_groups:
            if len(selected) >= max_cases:
                return selected
            if round_index >= len(candidates):
                continue
            row = candidates[round_index]
            regulation_slug = str(row.get("regulation_slug") or "").strip()
            if selected_per_regulation[regulation_slug] >= max_cases_per_regulation:
                continue
            current_case_id = case_id(row)
            if current_case_id in seen_case_ids:
                continue
            seen_case_ids.add(current_case_id)
            selected.append(row)
            selected_per_regulation[regulation_slug] += 1
    return selected


def choose_split(case_id_value: str) -> str:
    bucket = int(hashlib.sha1(case_id_value.encode("utf-8")).hexdigest(), 16) % 10
    if bucket < 8:
        return "train"
    if bucket == 8:
        return "valid"
    return "test"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--articles", type=Path, default=ARTICLES_PATH)
    parser.add_argument("--prompt-dir", type=Path, default=PROMPT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--max-cases", type=int, default=450)
    parser.add_argument("--max-per-group", type=int, default=4)
    parser.add_argument("--max-cases-per-regulation", type=int, default=100)
    args = parser.parse_args()

    articles = load_jsonl(args.articles)
    selected_articles = select_articles(
        articles,
        max_cases=max(1, args.max_cases),
        max_per_group=max(1, args.max_per_group),
        max_cases_per_regulation=max(1, args.max_cases_per_regulation),
    )

    split_payloads: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    split_manifest: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    for row in selected_articles:
        current_case_id = case_id(row)
        split = choose_split(current_case_id)
        for mode, payload in build_messages(row, args.prompt_dir):
            split_payloads[split].append(payload)
            split_manifest[split].append(
                {
                    "case_id": current_case_id,
                    "mode": mode,
                    "regulation_slug": row.get("regulation_slug", ""),
                    "citation": row.get("citation_short_ar", ""),
                    "topic": article_topic(row),
                    "article_type": row.get("article_type", ""),
                    "article_index": row.get("article_index"),
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split_name, rows in split_payloads.items():
        write_jsonl(args.output_dir / f"{split_name}.jsonl", rows)
        (args.output_dir / f"{split_name}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(rows),
                    "modes": dict(Counter(item["mode"] for item in split_manifest[split_name])),
                    "regulations": dict(Counter(item["regulation_slug"] for item in split_manifest[split_name])),
                    "article_types": dict(Counter(item["article_type"] for item in split_manifest[split_name])),
                    "rows": split_manifest[split_name],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    payload = {
        "dataset_version": "structured_mode_curriculum_v9",
        "cases_total": len(selected_articles),
        "examples_total": sum(len(rows) for rows in split_payloads.values()),
        "splits": {name: len(rows) for name, rows in split_payloads.items()},
        "modes_total": dict(
            Counter(
                item["mode"]
                for manifest in split_manifest.values()
                for item in manifest
            )
        ),
        "regulations_total": dict(
            Counter(
                item["regulation_slug"]
                for manifest in split_manifest.values()
                for item in manifest
            )
        ),
        "article_types_total": dict(
            Counter(
                item["article_type"]
                for manifest in split_manifest.values()
                for item in manifest
            )
        ),
        "training_regulations": sorted(TRAIN_REGULATIONS),
        "reserved_ood_regulations": sorted(RESERVED_OOD_REGULATIONS),
        "selection_policy": {
            "max_cases": args.max_cases,
            "max_per_group": args.max_per_group,
            "max_cases_per_regulation": args.max_cases_per_regulation,
            "group_by": ["regulation_slug", "primary_topic_tag", "article_type"],
            "split_policy": "sha1(case_id) => 80/10/10",
            "text_char_window": [MIN_TEXT_CHARS, MAX_TEXT_CHARS],
        },
    }
    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
