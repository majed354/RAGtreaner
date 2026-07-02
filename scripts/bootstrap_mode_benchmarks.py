"""تجهيز benchmark مستقل للمسارات القانونية دون المساس بملفات RAG."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ADVANCED = ROOT / "data" / "eval" / "legal_eval_advanced_set.jsonl"
BENCHMARK_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1"
OPINION_OUTPUT = BENCHMARK_DIR / "legal_opinion_cases.jsonl"
MEMO_OUTPUT = BENCHMARK_DIR / "legal_memo_cases.jsonl"
ANALYSIS_OUTPUT = BENCHMARK_DIR / "legal_analysis_cases.jsonl"

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


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_legal_opinion_case(row: dict) -> dict:
    question_id = str(row.get("question_id", "")).strip()
    benchmark_id = f"opinion::{question_id}" if question_id else "opinion::unknown"
    return {
        "benchmark_id": benchmark_id,
        "suite_version": "legal_modes_v1",
        "mode": "legal_opinion",
        "source_dataset": str(SOURCE_ADVANCED.relative_to(ROOT)),
        "question_id": question_id,
        "question": row.get("question", ""),
        "expected_behavior": row.get("expected_behavior", "answer"),
        "expected_regulations": row.get("expected_regulations", []),
        "expected_articles": row.get("expected_articles", []),
        "min_expected_regulation_hits": row.get(
            "min_expected_regulation_hits",
            len(row.get("expected_regulations", [])) or 1,
        ),
        "min_expected_article_hits": row.get(
            "min_expected_article_hits",
            len(row.get("expected_articles", [])),
        ),
        "question_type": row.get("question_type", ""),
        "benchmark_category": row.get("benchmark_category", ""),
        "required_sections": OPINION_REQUIRED_SECTIONS,
        "scoring_profile": "legal_opinion_v1",
        "scoring_axes": COMMON_SCORING_AXES,
        "notes": row.get("notes", ""),
    }


def build_legal_memo_case(row: dict) -> dict:
    question_id = str(row.get("question_id", "")).strip()
    benchmark_id = f"memo::{question_id}" if question_id else "memo::unknown"
    return {
        "benchmark_id": benchmark_id,
        "suite_version": "legal_modes_v1",
        "mode": "legal_memo",
        "source_dataset": str(SOURCE_ADVANCED.relative_to(ROOT)),
        "question_id": question_id,
        "question": row.get("question", ""),
        "expected_behavior": row.get("expected_behavior", "answer"),
        "expected_regulations": row.get("expected_regulations", []),
        "expected_articles": row.get("expected_articles", []),
        "min_expected_regulation_hits": row.get(
            "min_expected_regulation_hits",
            len(row.get("expected_regulations", [])) or 1,
        ),
        "min_expected_article_hits": row.get(
            "min_expected_article_hits",
            len(row.get("expected_articles", [])),
        ),
        "question_type": row.get("question_type", ""),
        "benchmark_category": row.get("benchmark_category", ""),
        "required_sections": MEMO_REQUIRED_SECTIONS,
        "scoring_profile": "legal_memo_v1",
        "scoring_axes": COMMON_SCORING_AXES,
        "notes": row.get("notes", ""),
    }


def build_legal_analysis_case(row: dict) -> dict:
    question_id = str(row.get("question_id", "")).strip()
    benchmark_id = f"analysis::{question_id}" if question_id else "analysis::unknown"
    return {
        "benchmark_id": benchmark_id,
        "suite_version": "legal_modes_v1",
        "mode": "legal_analysis",
        "source_dataset": str(SOURCE_ADVANCED.relative_to(ROOT)),
        "question_id": question_id,
        "question": row.get("question", ""),
        "expected_behavior": row.get("expected_behavior", "answer"),
        "expected_regulations": row.get("expected_regulations", []),
        "expected_articles": row.get("expected_articles", []),
        "min_expected_regulation_hits": row.get(
            "min_expected_regulation_hits",
            len(row.get("expected_regulations", [])) or 1,
        ),
        "min_expected_article_hits": row.get(
            "min_expected_article_hits",
            len(row.get("expected_articles", [])),
        ),
        "question_type": row.get("question_type", ""),
        "benchmark_category": row.get("benchmark_category", ""),
        "required_sections": ANALYSIS_REQUIRED_SECTIONS,
        "scoring_profile": "legal_analysis_v1",
        "scoring_axes": COMMON_SCORING_AXES,
        "notes": row.get("notes", ""),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    advanced_rows = load_jsonl(SOURCE_ADVANCED)
    opinion_rows = [build_legal_opinion_case(row) for row in advanced_rows]
    memo_rows = [build_legal_memo_case(row) for row in advanced_rows]
    analysis_rows = [build_legal_analysis_case(row) for row in advanced_rows]
    write_jsonl(OPINION_OUTPUT, opinion_rows)
    write_jsonl(MEMO_OUTPUT, memo_rows)
    write_jsonl(ANALYSIS_OUTPUT, analysis_rows)
    print(
        json.dumps(
            {
                "legal_opinion": {"cases": len(opinion_rows), "output": str(OPINION_OUTPUT)},
                "legal_memo": {"cases": len(memo_rows), "output": str(MEMO_OUTPUT)},
                "legal_analysis": {"cases": len(analysis_rows), "output": str(ANALYSIS_OUTPUT)},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
