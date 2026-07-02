"""Build a structured silver curriculum from tagged legal articles."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_PATH = ROOT / "data" / "structured" / "articles.jsonl"
PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "prompt_templates"
OUTPUT_DIR = ROOT / "data" / "training" / "structured_mode_curriculum_v1" / "sft_messages"

PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}

TARGET_REGULATIONS = {
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
MAX_ARTICLES_PER_TOPIC = 4
MAX_TEXT_CHARS = 420

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


def compact_text(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def load_system_prompt(prompt_dir: Path, mode: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE[mode]
    return (prompt_dir / prompt_name).read_text(encoding="utf-8").strip()


def article_topic(row: dict[str, Any]) -> str:
    tags = [tag for tag in (row.get("topic_tags_ar") or []) if str(tag).strip()]
    if tags:
        return " / ".join(tags[:2])
    heading = str(row.get("article_heading") or row.get("article_label") or "").strip()
    if heading:
        return heading
    return str(row.get("regulation_title_ar") or "المسألة النظامية").strip()


def build_question(row: dict[str, Any]) -> str:
    topic = article_topic(row)
    article_type = row.get("article_type") or "general"
    template = QUESTION_TEMPLATE_BY_TYPE.get(article_type, QUESTION_TEMPLATE_BY_TYPE["general"])
    return template.format(topic=topic)


def build_context(row: dict[str, Any]) -> str:
    citation = row.get("citation_short_ar", "")
    article_type_ar = row.get("article_type_label_ar", "حكم عام")
    article_index = row.get("article_index", "")
    article_label = row.get("article_label", "")
    text = compact_text(row.get("text_verbatim") or row.get("text_for_index") or "")
    return (
        f"[المصدر 1 | النظام: {row.get('regulation_title_ar', '')} | رقم المادة: {article_index} | "
        f"تسمية المادة: {article_label} | نوع المادة: {article_type_ar} | الإحالة الرسمية: {citation}]\n"
        f"{text}"
    )


def build_user_message(row: dict[str, Any]) -> str:
    return f"القضية:\n{build_question(row)}\n\nالنصوص المسترجعة:\n{build_context(row)}"


def build_opinion_answer(row: dict[str, Any]) -> str:
    title = row.get("regulation_title_ar", "")
    citation = row.get("citation_short_ar", "")
    article_type_ar = row.get("article_type_label_ar", "حكم عام")
    article_type = row.get("article_type") or "general"
    rule_text = compact_text(row.get("text_verbatim") or row.get("text_for_index") or "")
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
    title = row.get("regulation_title_ar", "")
    citation = row.get("citation_short_ar", "")
    article_type_ar = row.get("article_type_label_ar", "حكم عام")
    rule_text = compact_text(row.get("text_verbatim") or row.get("text_for_index") or "")
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
    title = row.get("regulation_title_ar", "")
    citation = row.get("citation_short_ar", "")
    article_type_ar = row.get("article_type_label_ar", "حكم عام")
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


def select_articles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        regulation_slug = row.get("regulation_slug", "")
        if regulation_slug not in TARGET_REGULATIONS:
            continue
        if not row.get("topic_tags_ar"):
            continue
        article_type = row.get("article_type") or "general"
        if article_type not in QUESTION_TEMPLATE_BY_TYPE:
            continue
        primary_tag = str((row.get("topic_tags_ar") or [""])[0]).strip()
        if not primary_tag:
            continue
        grouped[(regulation_slug, primary_tag)].append(row)

    selected: list[dict[str, Any]] = []
    for (_, _), items in sorted(grouped.items()):
        ranked = sorted(
            items,
            key=lambda item: (
                item.get("article_type") == "general",
                len(item.get("text_verbatim") or item.get("text_for_index") or ""),
                item.get("article_index", 0),
            ),
        )
        selected.extend(ranked[:MAX_ARTICLES_PER_TOPIC])
    return selected


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


def choose_split(case_id: str, ordered_case_ids: list[str]) -> str:
    total = len(ordered_case_ids)
    valid_count = max(4, round(total * 0.1))
    test_count = max(4, round(total * 0.1))
    train_end = max(0, total - valid_count - test_count)
    valid_end = train_end + valid_count
    if case_id in ordered_case_ids[:train_end]:
        return "train"
    if case_id in ordered_case_ids[train_end:valid_end]:
        return "valid"
    return "test"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--articles", type=Path, default=ARTICLES_PATH)
    parser.add_argument("--prompt-dir", type=Path, default=PROMPT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    articles = load_jsonl(args.articles)
    selected_articles = select_articles(articles)
    case_ids = [
        f"curriculum::{row['regulation_slug']}::{row['article_index']}"
        for row in selected_articles
    ]

    split_payloads = {"train": [], "valid": [], "test": []}
    split_manifest = {"train": [], "valid": [], "test": []}
    for row in selected_articles:
        case_id = f"curriculum::{row['regulation_slug']}::{row['article_index']}"
        split = choose_split(case_id, case_ids)
        for mode, payload in build_messages(row, args.prompt_dir):
            split_payloads[split].append(payload)
            split_manifest[split].append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "regulation_slug": row.get("regulation_slug", ""),
                    "citation": row.get("citation_short_ar", ""),
                    "topic": article_topic(row),
                    "article_type": row.get("article_type", ""),
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
                    "rows": split_manifest[split_name],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
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
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "cases_total": len(selected_articles),
                "examples_total": sum(len(rows) for rows in split_payloads.values()),
                "splits": {name: len(rows) for name, rows in split_payloads.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
