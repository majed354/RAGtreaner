"""Build a held-out OOD benchmark for the legal mode suite."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTICLES_PATH = ROOT / "data" / "structured" / "articles.jsonl"
OUTPUT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_ood_v9"

OOD_REGULATIONS = [
    "basic-law-of-governance",
    "communications-and-information-technology-law",
    "criminal-procedure-law",
    "government-tenders-and-procurement-law",
    "law-of-sharia-procedure",
    "real-estate-brokerage-law",
]

QUESTION_TEMPLATE_BY_TYPE = {
    "rights": "ما الحق أو الاختصاص الذي يثبته النظام في مسألة {topic}؟",
    "condition": "ما الشروط أو الالتزامات النظامية المتعلقة بـ{topic}؟",
    "prohibition": "هل يوجد منع أو حظر نظامي متعلق بـ{topic}؟",
    "exception": "ما القيد أو الاستثناء النظامي الوارد في مسألة {topic}؟",
    "procedure": "ما الإجراء أو السلطة النظامية المتصلة بـ{topic}؟",
    "liability": "ما المسؤولية أو الأثر النظامي المتعلق بـ{topic}؟",
    "penalty": "ما الجزاء أو العقوبة النظامية المرتبطة بـ{topic}؟",
    "violation": "ما المخالفة أو التعدي النظامي في مسألة {topic}؟",
    "definition": "كيف يعرّف النظام مسألة {topic} أو يحدد حكمها الأولي؟",
    "general": "ما الحكم النظامي المباشر في مسألة {topic}؟",
}

OPINION_REQUIRED_SECTIONS = [
    "النظام المنطبق",
    "الحكم المباشر",
    "المواد المستند إليها",
    "القيود أو الاستثناءات",
    "ما لم يثبته النص",
    "الخلاصة العملية",
]

MEMO_REQUIRED_SECTIONS = [
    "عنوان المذكرة",
    "السؤال محل الرأي",
    "الجواب المختصر",
    "الوقائع ذات الأثر القانوني",
    "النظام أو النصوص المنطبقة",
    "المسائل القانونية",
    "التحليل",
    "الدفوع أو الاحتمالات المقابلة",
    "ما لم يثبته النص أو الوقائع",
    "الخلاصة والتوصية العملية",
]

ANALYSIS_REQUIRED_SECTIONS = [
    "التكييف الأولي للقضية",
    "الأنظمة المحتملة الانطباق",
    "المسائل القانونية الأساسية",
    "ما يدعم الطرف الأول",
    "ما يدعم الطرف الثاني",
    "نقاط الضعف",
    "ما قد يغير النتيجة",
    "ما لم يثبته النص",
    "التقدير الأولي",
]

COMMON_SCORING_AXES = [
    "mode_compliance",
    "regulation_routing_accuracy",
    "article_grounding_accuracy",
    "unsupported_inference_rate",
    "cross_domain_contamination",
    "refusal_quality",
    "citation_clarity",
]

REFUSAL_CASES = [
    {
        "question_id": "ood_refusal_001",
        "question": "ما الحكم النظامي في براءات اختراع الأجهزة التعليمية الذكية وفق النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع براءات الاختراع غير موجود في corpus الحالي ويجب أن ينتج رفضًا منضبطًا.",
    },
    {
        "question_id": "ood_refusal_002",
        "question": "ما نسبة ضريبة القيمة المضافة على الخدمات القانونية الرقمية وفق النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع ضريبة القيمة المضافة غير موجود في corpus الحالي.",
    },
    {
        "question_id": "ood_refusal_003",
        "question": "ما الأحكام النظامية للتأمين البحري على الشحنات التجارية في النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع التأمين البحري غير موجود في corpus الحالي.",
    },
    {
        "question_id": "ood_refusal_004",
        "question": "ما تنظيم السندات التنفيذية للأوراق التجارية في النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع الأوراق التجارية خارج corpus الحالي.",
    },
    {
        "question_id": "ood_refusal_005",
        "question": "ما أحكام تراخيص الطيران المدني الخاص بالطائرات الصغيرة وفق النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع الطيران المدني غير موجود في corpus الحالي.",
    },
    {
        "question_id": "ood_refusal_006",
        "question": "ما الأحكام النظامية للإفلاس وإعادة التنظيم المالي في النصوص المفهرسة الحالية؟",
        "question_type": "refusal",
        "benchmark_category": "ood_refusal",
        "expected_behavior": "refuse",
        "expected_regulations": [],
        "expected_articles": [],
        "notes": "موضوع الإفلاس خارج corpus الحالي.",
    },
]


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
    lines = [re.sub(r"\s+", " ", part).strip() for part in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def is_generic_heading(text: str) -> bool:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return True
    generic_patterns = [
        r"^الأولى.*$",
        r"^الثانية.*$",
        r"^الثالثة.*$",
        r"^الرابعة.*$",
        r"^الخامسة.*$",
        r"^السادسة.*$",
        r"^السابعة.*$",
        r"^الثامنة.*$",
        r"^التاسعة.*$",
        r"^العاشرة.*$",
        r"^الحادية عشرة.*$",
        r"^الثانية عشرة.*$",
        r"^الثالثة عشرة.*$",
        r"^الرابعة عشرة.*$",
        r"^الخامسة عشرة.*$",
        r"^السادسة عشرة.*$",
        r"^السابعة عشرة.*$",
        r"^الثامنة عشرة.*$",
        r"^التاسعة عشرة.*$",
        r"^العشر.*$",
        r"^المادة\s+.+$",
    ]
    return any(re.fullmatch(pattern, value) for pattern in generic_patterns)


def article_topic(row: dict[str, Any]) -> str:
    text = normalize_text(row.get("text_verbatim") or row.get("text_for_index") or "")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    first_line = re.split(r"[.:\n؛،]", first_line, maxsplit=1)[0].strip()
    if first_line:
        return " ".join(first_line.split()[:8]).strip()
    tags = [str(tag).strip() for tag in (row.get("topic_tags_ar") or []) if str(tag).strip()]
    if tags:
        return " / ".join(tags[:2])
    heading = str(row.get("article_heading") or row.get("article_label") or "").strip()
    if heading and not is_generic_heading(heading):
        return heading
    return str(row.get("regulation_title_ar") or "المسألة النظامية")


def is_candidate(row: dict[str, Any]) -> bool:
    slug = str(row.get("regulation_slug") or "").strip()
    if slug not in OOD_REGULATIONS:
        return False
    text = normalize_text(row.get("text_verbatim") or row.get("text_for_index") or "")
    if len(text) < 60 or len(text) > 1800:
        return False
    topic = article_topic(row)
    if not topic:
        return False
    return True


def candidate_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    text = normalize_text(row.get("text_verbatim") or row.get("text_for_index") or "")
    article_type = str(row.get("article_type") or "general")
    paragraph_count = int(row.get("indexable_paragraph_count") or row.get("paragraph_count") or 0)
    return (
        int(article_type in {"exception", "condition", "procedure", "prohibition", "rights"}),
        -abs(len(text) - 420),
        -paragraph_count,
    )


def select_answer_cases(rows: list[dict[str, Any]], per_regulation: int = 4) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if is_candidate(row):
            grouped[str(row.get("regulation_slug"))].append(row)

    selected: list[dict[str, Any]] = []
    for regulation_slug in OOD_REGULATIONS:
        candidates = sorted(grouped.get(regulation_slug, []), key=candidate_rank, reverse=True)
        seen_topics: set[str] = set()
        for row in candidates:
            topic = article_topic(row)
            article_type = str(row.get("article_type") or "general")
            topic_key = f"{topic}::{article_type}"
            if topic_key in seen_topics:
                continue
            selected.append(row)
            seen_topics.add(topic_key)
            if len(seen_topics) >= per_regulation:
                break
    if len(selected) != len(OOD_REGULATIONS) * per_regulation:
        raise SystemExit(f"Expected {len(OOD_REGULATIONS) * per_regulation} held-out answer cases, got {len(selected)}")
    return selected


def build_answer_case(row: dict[str, Any]) -> dict[str, Any]:
    question_type = str(row.get("article_type") or "general")
    question_template = QUESTION_TEMPLATE_BY_TYPE.get(question_type, QUESTION_TEMPLATE_BY_TYPE["general"])
    topic = article_topic(row)
    slug = str(row.get("regulation_slug") or "").strip()
    article_index = int(row.get("article_index") or 0)
    return {
        "question_id": f"ood_{slug}_{article_index}",
        "question": question_template.format(topic=topic),
        "question_type": question_type,
        "benchmark_category": "ood_heldout_regulation",
        "expected_behavior": "answer",
        "expected_regulations": [slug],
        "expected_articles": [article_index],
        "min_expected_regulation_hits": 1,
        "min_expected_article_hits": 1,
        "notes": f"حالة OOD من نظام محتجز خارج التدريب: {row.get('regulation_title_ar', slug)}.",
    }


def build_mode_row(case: dict[str, Any], mode: str) -> dict[str, Any]:
    required_sections = {
        "legal_opinion": OPINION_REQUIRED_SECTIONS,
        "legal_memo": MEMO_REQUIRED_SECTIONS,
        "legal_analysis": ANALYSIS_REQUIRED_SECTIONS,
    }[mode]
    scoring_profile = {
        "legal_opinion": "legal_opinion_v1",
        "legal_memo": "legal_memo_v1",
        "legal_analysis": "legal_analysis_v1",
    }[mode]
    return {
        "benchmark_id": f"{mode.split('_', 1)[1] if '_' in mode else mode}::{case['question_id']}",
        "suite_version": "legal_modes_v1",
        "mode": mode,
        "source_dataset": "data/structured/articles.jsonl" if case["expected_behavior"] == "answer" else "manual_ood_refusal_cases",
        "question_id": case["question_id"],
        "question": case["question"],
        "expected_behavior": case["expected_behavior"],
        "expected_regulations": case.get("expected_regulations", []),
        "expected_articles": case.get("expected_articles", []),
        "min_expected_regulation_hits": case.get("min_expected_regulation_hits", len(case.get("expected_regulations", [])) or 1),
        "min_expected_article_hits": case.get("min_expected_article_hits", len(case.get("expected_articles", []))),
        "question_type": case.get("question_type", ""),
        "benchmark_category": case.get("benchmark_category", ""),
        "required_sections": required_sections,
        "scoring_profile": scoring_profile,
        "scoring_axes": COMMON_SCORING_AXES,
        "notes": case.get("notes", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--articles", type=Path, default=ARTICLES_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    articles = load_jsonl(args.articles)
    answer_cases = [build_answer_case(row) for row in select_answer_cases(articles)]
    base_cases = answer_cases + list(REFUSAL_CASES)
    if len(base_cases) != 30:
        raise SystemExit(f"Expected 30 base OOD cases, got {len(base_cases)}")

    opinion_rows = [build_mode_row(case, "legal_opinion") for case in base_cases]
    memo_rows = [build_mode_row(case, "legal_memo") for case in base_cases]
    analysis_rows = [build_mode_row(case, "legal_analysis") for case in base_cases]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "legal_opinion_cases.jsonl", opinion_rows)
    write_jsonl(args.output_dir / "legal_memo_cases.jsonl", memo_rows)
    write_jsonl(args.output_dir / "legal_analysis_cases.jsonl", analysis_rows)

    manifest = {
        "suite_version": "legal_modes_v1",
        "benchmark_name": "legal_modes_ood_v9",
        "description_ar": "Benchmark OOD محتجز خارج تدريب v9 من أنظمة غير مستخدمة في dataset التدريب، مع حالات رفض حدودية.",
        "cases_per_mode": 30,
        "base_cases_total": 30,
        "rows_total": 90,
        "answer_cases": len(answer_cases),
        "refusal_cases": len(REFUSAL_CASES),
        "ood_regulations": OOD_REGULATIONS,
        "categories_total": dict(Counter(case["benchmark_category"] for case in base_cases)),
        "question_types_total": dict(Counter(case["question_type"] for case in base_cases)),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
