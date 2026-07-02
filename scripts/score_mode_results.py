"""Score generated legal-mode benchmark results without rerunning the model."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import run_legal_eval as base_eval

ROOT = Path(__file__).resolve().parent.parent
REGULATIONS_PATH = ROOT / "data" / "structured" / "regulations.json"
DEFAULT_BENCHMARK = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "legal_opinion_cases.jsonl"
DEFAULT_RESULTS = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "current_reference"
    / "legal_opinion_active_runtime.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULTS.with_name("legal_opinion_active_runtime.scored.json")


def load_generated_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_case_lookup(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case["benchmark_id"]: case for case in cases}


def find_section_hits(answer: str, required_sections: list[str]) -> tuple[list[str], list[str]]:
    normalized_answer = base_eval.normalize_text(answer)
    hits: list[str] = []
    missing: list[str] = []
    for section in required_sections:
        normalized_section = base_eval.normalize_text(section)
        if normalized_section and normalized_section in normalized_answer:
            hits.append(section)
        else:
            missing.append(section)
    return hits, missing


def citation_clarity(answer: str, displayed_source_count: int) -> tuple[float, list[int]]:
    mentions = sorted(base_eval.extract_article_mentions(answer))
    if mentions and displayed_source_count > 0:
        return 1.0, mentions
    if mentions or displayed_source_count > 0:
        return 0.5, mentions
    return 0.0, mentions


def answer_regulation_hits(answer: str, expected_regulations: list[str], aliases: dict[str, set[str]]) -> list[str]:
    return base_eval.regulation_hits(expected_regulations, answer, [], {}, aliases)


def answer_article_hits(answer: str, expected_articles: list[int]) -> list[int]:
    cited_articles = base_eval.extract_article_mentions(answer)
    return sorted(article for article in expected_articles if article in cited_articles)


def compute_answer_only_score(case: dict[str, Any], row: dict[str, Any]) -> float:
    regulation_target = max(1, case.get("min_expected_regulation_hits", len(case.get("expected_regulations", [])) or 1))
    article_target = case.get("min_expected_article_hits", len(case.get("expected_articles", [])))

    regulation_score = (
        1.0
        if not case.get("expected_regulations")
        else min(len(row.get("answer_matched_regulations", [])) / regulation_target, 1.0)
    )
    article_score = (
        1.0
        if not article_target
        else min(len(row.get("answer_matched_articles", [])) / article_target, 1.0)
    )
    format_score = row.get("section_coverage", 0.0)
    citation_score = row.get("citation_clarity", 0.0)
    confidence_score = base_eval.confidence_score(row.get("confidence", "medium"), case.get("expected_behavior", "answer"))

    score = (
        (format_score * 0.30)
        + (article_score * 0.25)
        + (regulation_score * 0.25)
        + (citation_score * 0.10)
        + (confidence_score * 0.10)
    )
    return round(max(min(score, 1.0), 0.0), 3)


def score_row(
    case: dict[str, Any],
    result_row: dict[str, Any],
    aliases: dict[str, set[str]],
) -> dict[str, Any]:
    answer = result_row.get("answer", "")
    sources_text = result_row.get("displayed_sources", []) or []
    diagnostics = result_row.get("diagnostics", {}) or {}
    confidence = result_row.get("confidence", "low")
    required_sections = case.get("required_sections", []) or []
    section_hits, missing_sections = find_section_hits(answer, required_sections)
    section_target = len(required_sections)
    citation_score, citation_mentions = citation_clarity(
        answer,
        int(result_row.get("displayed_source_count", len(sources_text)) or 0),
    )
    answer_only_regulation_hits = answer_regulation_hits(answer, case.get("expected_regulations", []), aliases)
    answer_only_article_hits = answer_article_hits(answer, case.get("expected_articles", []))

    matched_regulations = base_eval.regulation_hits(
        case.get("expected_regulations", []),
        answer,
        sources_text,
        diagnostics,
        aliases,
    )
    matched_articles = base_eval.article_hits(
        answer,
        sources_text,
        case.get("expected_articles", []),
        diagnostics,
    )
    refusal_detected = base_eval.detect_refusal(answer, confidence, diagnostics)
    unexpected_regs = base_eval.unexpected_regulation_hits(case.get("expected_regulations", []), diagnostics)

    row = {
        "benchmark_id": case["benchmark_id"],
        "question_id": case["question_id"],
        "mode": case.get("mode", ""),
        "status": result_row.get("status", "unknown"),
        "question": case["question"],
        "question_type": case.get("question_type", ""),
        "benchmark_category": case.get("benchmark_category", ""),
        "expected_behavior": case.get("expected_behavior", "answer"),
        "expected_regulations": case.get("expected_regulations", []),
        "expected_articles": case.get("expected_articles", []),
        "min_expected_regulation_hits": case.get(
            "min_expected_regulation_hits",
            len(case.get("expected_regulations", [])) or 1,
        ),
        "min_expected_article_hits": case.get(
            "min_expected_article_hits",
            len(case.get("expected_articles", [])),
        ),
        "confidence": confidence,
        "needs_escalation": bool(result_row.get("needs_escalation", False)),
        "refusal_detected": refusal_detected,
        "matched_regulations": matched_regulations,
        "matched_regulation_count": len(matched_regulations),
        "matched_articles": matched_articles,
        "matched_article_count": len(matched_articles),
        "unexpected_regulations": unexpected_regs,
        "answer_preview": answer[:2000],
        "source_count": int(result_row.get("displayed_source_count", len(sources_text)) or 0),
        "retrieved_source_count": int(result_row.get("retrieved_source_count", 0) or 0),
        "required_sections": required_sections,
        "section_hits": section_hits,
        "missing_sections": missing_sections,
        "section_coverage": round(len(section_hits) / max(1, section_target), 3),
        "citation_clarity": citation_score,
        "citation_mentions": citation_mentions,
        "answer_matched_regulations": answer_only_regulation_hits,
        "answer_matched_regulation_count": len(answer_only_regulation_hits),
        "answer_matched_articles": answer_only_article_hits,
        "answer_matched_article_count": len(answer_only_article_hits),
        "diagnostics": {
            "quality_status": diagnostics.get("status"),
            "issue_flags": diagnostics.get("issue_flags", []),
            "dominant_domain": diagnostics.get("dominant_domain"),
            "top_regulations": diagnostics.get("top_regulations", []),
            "top_articles": diagnostics.get("top_articles", []),
            "query_roles": diagnostics.get("query_roles", []),
            "covered_roles": diagnostics.get("covered_roles", []),
            "missing_roles": diagnostics.get("missing_roles", []),
            "issue_count": diagnostics.get("issue_count"),
            "covered_issue_ids": diagnostics.get("covered_issue_ids", []),
            "missing_issue_ids": diagnostics.get("missing_issue_ids", []),
            "missing_issue_domains": diagnostics.get("missing_issue_domains", []),
            "helper_failures": diagnostics.get("helper_failures", []),
            "primary_ratio": diagnostics.get("primary_ratio"),
            "dominant_concentration": diagnostics.get("dominant_concentration"),
            "unique_article_count": diagnostics.get("unique_article_count"),
        },
    }
    row["score"] = base_eval.compute_case_score(case, row)
    row["answer_only_score"] = compute_answer_only_score(case, row)
    row["taxonomy"] = base_eval.classify_case(row)
    return row


def summarize_mode_quality(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("mode") or "uncategorized"].append(row)

    summary: dict[str, dict[str, Any]] = {}
    for mode, items in sorted(grouped.items()):
        summary[mode] = {
            "cases": len(items),
            "average_score": round(mean(item["score"] for item in items), 3),
            "average_answer_only_score": round(mean(item["answer_only_score"] for item in items), 3),
            "average_section_coverage": round(mean(item["section_coverage"] for item in items), 3),
            "cases_with_full_section_coverage": sum(1 for item in items if item["section_coverage"] >= 1.0),
            "average_citation_clarity": round(mean(item["citation_clarity"] for item in items), 3),
            "taxonomy_counts": dict(Counter(item["taxonomy"] for item in items)),
        }
    return summary


def build_summary(cases: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = base_eval.build_summary(cases, rows)
    summary["average_answer_only_score"] = round(mean(row["answer_only_score"] for row in rows), 3) if rows else 0.0
    summary["cases_with_answer_only_score_at_least_0_75"] = sum(1 for row in rows if row["answer_only_score"] >= 0.75)
    summary["cases_with_answer_only_score_at_least_0_85"] = sum(1 for row in rows if row["answer_only_score"] >= 0.85)
    summary["average_section_coverage"] = round(mean(row["section_coverage"] for row in rows), 3) if rows else 0.0
    summary["cases_with_full_section_coverage"] = sum(1 for row in rows if row["section_coverage"] >= 1.0)
    summary["average_citation_clarity"] = round(mean(row["citation_clarity"] for row in rows), 3) if rows else 0.0
    summary["by_mode"] = summarize_mode_quality(rows)
    summary["lowest_scoring_cases"] = [
        {
            "benchmark_id": row["benchmark_id"],
            "score": row["score"],
            "answer_only_score": row["answer_only_score"],
            "taxonomy": row["taxonomy"],
            "section_coverage": row["section_coverage"],
            "unexpected_regulations": row["unexpected_regulations"],
            "missing_sections": row["missing_sections"],
        }
        for row in sorted(rows, key=lambda item: (item["answer_only_score"], item["score"], item["section_coverage"]))[:5]
    ]
    return summary


def write_report(
    output_path: Path,
    benchmark_path: Path,
    results_path: Path,
    source_report: dict[str, Any],
    cases: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(benchmark_path),
        "results_path": str(results_path),
        "generation_status": source_report.get("generation_status", {}),
        "provider": source_report.get("provider", "unknown"),
        "summary": build_summary(cases, rows),
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cases = base_eval.load_cases(args.benchmark)
    case_lookup = build_case_lookup(cases)
    aliases = base_eval.load_regulation_aliases(REGULATIONS_PATH)
    source_report = load_generated_report(args.results)

    rows: list[dict[str, Any]] = []
    for result_row in source_report.get("rows", []):
        benchmark_id = result_row.get("benchmark_id")
        if benchmark_id not in case_lookup:
            continue
        rows.append(score_row(case_lookup[benchmark_id], result_row, aliases))

    write_report(args.output, args.benchmark, args.results, source_report, cases, rows)
    print(json.dumps(build_summary(cases, rows), ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")


if __name__ == "__main__":
    main()
