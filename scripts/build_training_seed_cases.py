"""Build a non-eval training seed split for legal mode fine-tuning."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_TEMPLATE = ROOT / "data" / "eval" / "legal_eval_set.template.jsonl"
OUTPUT_DIR = ROOT / "data" / "training" / "legal_modes_seed_v1"
OUTPUT_CASES = OUTPUT_DIR / "legal_opinion_seed_cases.jsonl"

OPINION_REQUIRED_SECTIONS = [
    "النظام المنطبق",
    "الحكم المباشر",
    "المواد المستند إليها",
    "القيود أو الاستثناءات",
    "ما لم يثبته النص",
    "الخلاصة العملية",
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
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_seed_case(row: dict) -> dict:
    question_id = str(row.get("question_id", "")).strip()
    return {
        "benchmark_id": f"seed::{question_id}" if question_id else "seed::unknown",
        "suite_version": "legal_modes_v1",
        "mode": "legal_opinion",
        "source_dataset": str(SOURCE_TEMPLATE.relative_to(ROOT)),
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
        "benchmark_category": row.get("benchmark_category", "training_seed"),
        "required_sections": OPINION_REQUIRED_SECTIONS,
        "scoring_profile": "legal_opinion_v1",
        "scoring_axes": COMMON_SCORING_AXES,
        "notes": row.get("notes", ""),
    }


def main() -> None:
    rows = load_jsonl(SOURCE_TEMPLATE)
    seed_cases = [build_seed_case(row) for row in rows]
    write_jsonl(OUTPUT_CASES, seed_cases)
    print(f"Built {len(seed_cases)} training seed cases -> {OUTPUT_CASES}")


if __name__ == "__main__":
    main()
