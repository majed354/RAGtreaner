"""تشغيل تقييم منظم لمسار RAG القانوني المحلي على مجموعة JSONL."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_INPUT = ROOT / "data" / "eval" / "legal_eval_set.template.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "eval" / "legal_eval_report.json"
REGULATIONS_PATH = ROOT / "data" / "structured" / "regulations.json"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"
DEFAULT_ANSWER_MODE = "consultation"

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
ARTICLE_NUMBER_PATTERNS = [
    re.compile(r"رقم المادة\s*[:：]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"المادة\s+(\d+)\b", re.IGNORECASE),
]
REFUSAL_PATTERNS = [
    "لا تتوافر في النصوص المسترجعة",
    "لا تتوافر في النصوص الحالية",
    "لا توجد في النصوص المسترجعة",
    "لا توجد في النصوص الحالية",
    "لا أتمكن من الجزم",
    "لم أتمكن من إيجاد معلومات كافية",
    "لم أتمكن من إيجاد نصوص كافية",
    "النصوص المتاحة لا تكفي",
]
SEVERE_FLAGS = {
    "cross_domain_noise",
    "fatal_core_doc_miss",
    "missing_issue_coverage",
    "missing_issue_domains",
    "missing_primary_law_anchor",
    "issue_domain_mismatch",
    "narrow_system_coverage",
    "weak_evidence",
}
MEDIUM_FLAGS = {
    "over_concentrated_context",
    "procedural_or_supplementary_drift",
    "thin_article_coverage",
    "missing_legal_axes",
    "missing_exception_support",
    "missing_rights_support",
    "missing_violation_support",
}


def build_summary(cases: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    taxonomy_counts: Counter[str] = Counter(row.get("taxonomy", "") for row in rows)
    confidence_counts: Counter[str] = Counter(row.get("confidence", "") for row in rows)

    cases_with_expected_regulation = sum(1 for case in cases if case.get("expected_regulations"))
    cases_with_expected_articles = sum(1 for case in cases if case.get("expected_articles"))
    expected_answer_cases = sum(1 for case in cases if case.get("expected_behavior", "answer") == "answer")
    expected_refusal_cases = sum(1 for case in cases if case.get("expected_behavior", "answer") == "refuse")

    retrieval_regulation_hits = sum(
        1 for row in rows if row.get("expected_regulations") and row.get("matched_regulations")
    )
    article_hit_cases = sum(
        1 for row in rows if row.get("expected_articles") and row.get("matched_articles")
    )
    correct_refusals = sum(
        1
        for row in rows
        if row.get("expected_behavior", "answer") == "refuse" and row.get("refusal_detected")
    )
    score_sum = sum(float(row.get("score", 0.0)) for row in rows)
    average_core_doc_recall = (
        mean(metric_value(row.get("diagnostics", {}), "core_doc_recall", 1.0) for row in rows)
        if rows
        else 0.0
    )
    average_bundle_completeness = (
        mean(metric_value(row.get("diagnostics", {}), "bundle_completeness", 1.0) for row in rows)
        if rows
        else 0.0
    )
    average_domain_purity = (
        mean(float(row.get("domain_purity", 1.0)) for row in rows)
        if rows
        else 0.0
    )
    average_sub_issue_coverage = (
        mean(float(row.get("sub_issue_coverage", 1.0)) for row in rows)
        if rows
        else 0.0
    )
    average_package_completeness = (
        mean(float(row.get("package_completeness", 1.0)) for row in rows)
        if rows
        else 0.0
    )
    fatal_core_doc_miss_cases = sum(
        1
        for row in rows
        if bool((row.get("diagnostics", {}) or {}).get("fatal_core_doc_miss"))
    )
    contamination_trap_cases = sum(1 for row in rows if row.get("contamination_trap_hits"))

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "cases_total": len(cases),
        "cases_completed": len(rows),
        "cases_with_expected_regulation": cases_with_expected_regulation,
        "retrieval_regulation_hit_cases": retrieval_regulation_hits,
        "retrieval_regulation_hit_rate": round(retrieval_regulation_hits / max(1, cases_with_expected_regulation), 3),
        "cases_with_expected_articles": cases_with_expected_articles,
        "article_hit_cases": article_hit_cases,
        "article_hit_rate": round(article_hit_cases / max(1, cases_with_expected_articles), 3),
        "expected_answer_cases": expected_answer_cases,
        "expected_refusal_cases": expected_refusal_cases,
        "correct_refusals": correct_refusals,
        "correct_refusal_rate": round(correct_refusals / max(1, expected_refusal_cases), 3),
        "average_score": round(score_sum / max(1, len(rows)), 3),
        "average_core_doc_recall": round(average_core_doc_recall, 3),
        "average_bundle_completeness": round(average_bundle_completeness, 3),
        "average_domain_purity": round(average_domain_purity, 3),
        "average_sub_issue_coverage": round(average_sub_issue_coverage, 3),
        "average_package_completeness": round(average_package_completeness, 3),
        "fatal_core_doc_miss_cases": fatal_core_doc_miss_cases,
        "fatal_core_doc_miss_rate": round(fatal_core_doc_miss_cases / max(1, len(rows)), 3),
        "contamination_trap_cases": contamination_trap_cases,
        "contamination_trap_rate": round(contamination_trap_cases / max(1, len(rows)), 3),
        "cases_scoring_at_least_0_75": sum(1 for row in rows if row["score"] >= 0.75),
        "cases_scoring_at_least_0_85": sum(1 for row in rows if row["score"] >= 0.85),
        "cases_scoring_at_least_0_9": sum(1 for row in rows if row["score"] >= 0.9),
        "confidence_counts": dict(confidence_counts),
        "taxonomy_counts": dict(taxonomy_counts),
        "by_question_type": summarize_by_field(rows, "question_type"),
        "by_benchmark_category": summarize_by_field(rows, "benchmark_category"),
    }


def write_report(output_path: Path, cases: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    summary = build_summary(cases, rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_cases(
    path: Path,
    category_filter: str | None = None,
    question_type_filter: str | None = None,
) -> list[dict[str, Any]]:
    cases = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if category_filter and row.get("benchmark_category") != category_filter:
                continue
            if question_type_filter and row.get("question_type") != question_type_filter:
                continue
            cases.append(row)
    return cases


def normalize_text(text: str) -> str:
    normalized = " ".join((text or "").replace("\n", " ").split()).strip().lower()
    return normalized.translate(ARABIC_DIGITS)


def strip_regulation_prefix(title: str) -> str:
    value = (title or "").strip()
    prefixes = (
        "اللائحة التنفيذية لنظام ",
        "لائحة تنظيم ",
        "لائحة ",
        "نظام ",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix) :].strip()
    return value


def load_regulation_aliases(path: Path) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    if not path.exists():
        return aliases

    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        slug = row.get("slug") or row.get("regulation_slug")
        if not slug:
            continue
        normalized_aliases = {
            normalize_text(slug),
            normalize_text(slug.replace("-", " ")),
        }
        for title in [
            row.get("title_ar"),
            row.get("regulation_title_ar"),
            row.get("name_ar"),
            row.get("title_en"),
            row.get("name_en"),
        ]:
            if not title:
                continue
            normalized_aliases.add(normalize_text(title))
            shortened = strip_regulation_prefix(title)
            if shortened and shortened != title:
                normalized_aliases.add(normalize_text(shortened))
        aliases[slug] = {alias for alias in normalized_aliases if alias}
    return aliases


def extract_article_mentions(text: str) -> set[int]:
    haystack = normalize_text(text)
    hits: set[int] = set()
    for pattern in ARTICLE_NUMBER_PATTERNS:
        for match in pattern.finditer(haystack):
            try:
                hits.add(int(match.group(1)))
            except (TypeError, ValueError):
                continue
    return hits


def coerce_article_number(value: Any) -> int | None:
    text = str(value or "").strip().translate(ARABIC_DIGITS)
    return int(text) if text.isdigit() else None


def article_hits(
    answer: str,
    source_texts: list[str],
    expected_articles: list[int],
    diagnostics: dict[str, Any] | None = None,
) -> list[int]:
    observed_articles = {
        article
        for article in (
            coerce_article_number(raw_article)
            for raw_article in (diagnostics or {}).get("top_articles", [])
        )
        if article is not None
    }
    cited_articles = extract_article_mentions(answer + "\n" + "\n".join(source_texts))
    hits = []
    for article in expected_articles:
        if article in observed_articles or article in cited_articles:
            hits.append(article)
    return sorted(set(hits))


def regulation_hits(
    expected_regulations: list[str],
    answer: str,
    source_texts: list[str],
    diagnostics: dict[str, Any] | None = None,
    aliases: dict[str, set[str]] | None = None,
) -> list[str]:
    observed_slugs = set((diagnostics or {}).get("top_regulations", []))
    dominant_domain = (diagnostics or {}).get("dominant_domain")
    if dominant_domain:
        observed_slugs.add(dominant_domain)

    observed_normalized = {normalize_text(slug) for slug in observed_slugs if slug}
    haystack = normalize_text(answer + "\n" + "\n".join(source_texts))
    hits = []

    for slug in expected_regulations:
        slug_aliases = (aliases or {}).get(
            slug,
            {
                normalize_text(slug),
                normalize_text(slug.replace("-", " ")),
            },
        )
        if slug in observed_slugs or normalize_text(slug) in observed_normalized:
            hits.append(slug)
            continue
        if any(alias and alias in haystack for alias in slug_aliases):
            hits.append(slug)
    return sorted(set(hits))


def unexpected_regulation_hits(
    allowed_regulations: list[str],
    diagnostics: dict[str, Any] | None = None,
) -> list[str]:
    observed = list((diagnostics or {}).get("top_regulations", []))
    allowed = set(allowed_regulations)
    unexpected = [slug for slug in observed if slug and slug not in allowed]
    return sorted(set(unexpected))


def observed_regulations(diagnostics: dict[str, Any] | None = None) -> list[str]:
    observed = set((diagnostics or {}).get("top_regulations", []))
    dominant_domain = (diagnostics or {}).get("dominant_domain")
    if dominant_domain:
        observed.add(dominant_domain)
    return sorted(slug for slug in observed if slug)


def _slug_matches_pattern(pattern: str, slug: str) -> bool:
    if not pattern or not slug:
        return False
    if pattern.endswith("*"):
        return slug.startswith(pattern[:-1])
    return slug == pattern


def contamination_trap_hits(case: dict[str, Any], diagnostics: dict[str, Any] | None = None) -> list[str]:
    observed = observed_regulations(diagnostics)
    hits = []
    for pattern in [str(item).strip() for item in case.get("contamination_traps", []) if str(item).strip()]:
        if any(_slug_matches_pattern(pattern, slug) for slug in observed):
            hits.append(pattern)
    return sorted(set(hits))


def evaluate_sub_issues(
    case: dict[str, Any],
    answer: str,
    source_texts: list[str],
    diagnostics: dict[str, Any] | None = None,
    aliases: dict[str, set[str]] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    sub_issues = case.get("sub_issues", [])
    if not sub_issues:
        return 1.0, []

    details: list[dict[str, Any]] = []
    covered_count = 0
    for sub_issue in sub_issues:
        expected_regulations = sub_issue.get("expected_regulations", [])
        expected_articles = sub_issue.get("expected_articles", [])
        matched_regulations = regulation_hits(
            expected_regulations,
            answer,
            source_texts,
            diagnostics,
            aliases,
        )
        matched_articles = article_hits(
            answer,
            source_texts,
            expected_articles,
            diagnostics,
        )
        regulation_target = max(
            1,
            sub_issue.get("min_expected_regulation_hits", len(expected_regulations) or 1),
        ) if expected_regulations else 0
        article_target = sub_issue.get("min_expected_article_hits", len(expected_articles))

        regulation_score = (
            min(len(matched_regulations) / regulation_target, 1.0)
            if regulation_target
            else 1.0
        )
        article_score = (
            min(len(matched_articles) / article_target, 1.0)
            if article_target
            else 1.0
        )
        covered = regulation_score >= 1.0 and article_score >= 1.0
        if covered:
            covered_count += 1

        details.append(
            {
                "issue": sub_issue.get("issue", ""),
                "expected_regulations": expected_regulations,
                "expected_articles": expected_articles,
                "matched_regulations": matched_regulations,
                "matched_articles": matched_articles,
                "regulation_score": round(regulation_score, 3),
                "article_score": round(article_score, 3),
                "covered": covered,
            }
        )

    return round(covered_count / max(1, len(sub_issues)), 3), details


def compute_domain_purity(
    case: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
    trap_hits: list[str] | None = None,
) -> float:
    observed = observed_regulations(diagnostics)
    allowed_regulations = set(case.get("allowed_regulations", []) or case.get("expected_regulations", []))
    if not observed:
        return 0.0 if trap_hits else 1.0

    if allowed_regulations:
        allowed_observed = sum(1 for slug in observed if slug in allowed_regulations)
        purity = allowed_observed / len(observed)
    else:
        purity = 1.0

    trap_penalty = min(len(trap_hits or []) * 0.25, 0.75)
    return round(max(0.0, min(1.0, purity - trap_penalty)), 3)


def compute_package_completeness(case: dict[str, Any], row: dict[str, Any]) -> float:
    expected_regulations = case.get("expected_regulations", [])
    expected_articles = case.get("expected_articles", [])
    regulation_target = max(1, case.get("min_expected_regulation_hits", len(expected_regulations) or 1))
    article_target = case.get("min_expected_article_hits", len(expected_articles))

    components: list[float] = [float(row.get("domain_purity", 1.0))]
    if expected_regulations:
        components.append(min(row.get("matched_regulation_count", 0) / regulation_target, 1.0))
    if article_target:
        components.append(min(row.get("matched_article_count", 0) / article_target, 1.0))
    if case.get("sub_issues"):
        components.append(float(row.get("sub_issue_coverage", 1.0)))
    return round(sum(components) / len(components), 3) if components else 1.0


def detect_refusal(answer: str, confidence: str, diagnostics: dict[str, Any] | None = None) -> bool:
    normalized_answer = normalize_text(answer)
    if confidence == "low":
        return True
    if (diagnostics or {}).get("status") == "low":
        return True
    return any(pattern in normalized_answer for pattern in REFUSAL_PATTERNS)


def confidence_score(level: str, expected_behavior: str) -> float:
    if expected_behavior == "refuse":
        return {"low": 1.0, "medium": 0.55, "high": 0.0}.get(level, 0.0)
    return {"high": 1.0, "medium": 0.75, "low": 0.2}.get(level, 0.2)


def metric_value(diagnostics: dict[str, Any] | None, field_name: str, default: float = 1.0) -> float:
    value = (diagnostics or {}).get(field_name, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_case_score(case: dict[str, Any], row: dict[str, Any]) -> float:
    expected_behavior = case.get("expected_behavior", "answer")
    diagnostics = row.get("diagnostics", {}) or {}
    issue_flags = set(diagnostics.get("issue_flags", []))

    if expected_behavior == "refuse":
        if row.get("refusal_detected") and row.get("confidence") == "low":
            return 1.0
        if row.get("refusal_detected"):
            return 0.75
        return 0.0

    expected_regulations = case.get("expected_regulations", [])
    expected_articles = case.get("expected_articles", [])
    regulation_target = max(1, case.get("min_expected_regulation_hits", len(expected_regulations) or 1))
    article_target = case.get("min_expected_article_hits", len(expected_articles))

    regulation_score = 1.0 if not expected_regulations else min(len(row.get("matched_regulations", [])) / regulation_target, 1.0)
    article_score = 1.0 if not article_target else min(len(row.get("matched_articles", [])) / article_target, 1.0)
    conf_score = confidence_score(row.get("confidence", "medium"), expected_behavior)
    core_doc_recall = metric_value(diagnostics, "core_doc_recall", 1.0)
    bundle_completeness = metric_value(diagnostics, "bundle_completeness", 1.0)
    sub_issue_score = float(row.get("sub_issue_coverage", 1.0))
    domain_purity = float(row.get("domain_purity", 1.0))
    package_completeness = float(row.get("package_completeness", 1.0))

    score = (
        (regulation_score * 0.30)
        + (article_score * 0.25)
        + (sub_issue_score * 0.15)
        + (domain_purity * 0.10)
        + (package_completeness * 0.05)
        + (conf_score * 0.10)
    )
    score += core_doc_recall * 0.03
    score += bundle_completeness * 0.02

    quality_status = diagnostics.get("quality_status")
    if quality_status == "high":
        score += 0.06
    elif quality_status == "medium":
        score += 0.02
    elif quality_status == "low":
        score -= 0.12

    if row.get("unexpected_regulations"):
        score -= 0.06
    if row.get("contamination_trap_hits"):
        score -= min(len(row["contamination_trap_hits"]) * 0.08, 0.16)
    if diagnostics.get("fatal_core_doc_miss"):
        score -= 0.12
    if issue_flags & SEVERE_FLAGS:
        score -= 0.08
    elif issue_flags & MEDIUM_FLAGS:
        score -= 0.04

    return round(max(min(score, 1.0), 0.0), 3)


def classify_case(row: dict[str, Any]) -> str:
    expected_behavior = row.get("expected_behavior", "answer")
    diagnostics = row.get("diagnostics", {}) or {}
    issue_flags = set(diagnostics.get("issue_flags", []))

    if expected_behavior == "refuse":
        return "correct_refusal" if row.get("refusal_detected") else "missed_refusal"

    if diagnostics.get("fatal_core_doc_miss"):
        return "core_doc_miss"
    if row.get("contamination_trap_hits") or row.get("domain_purity", 1.0) < 0.75 or row.get("unexpected_regulations"):
        return "cross_domain_noise"
    if row.get("sub_issue_coverage", 1.0) < 0.7:
        return "coverage_gap"
    if row.get("matched_regulation_count", 0) < row.get("min_expected_regulation_hits", 0):
        return "retrieval_domain_miss"
    if row.get("matched_article_count", 0) < row.get("min_expected_article_hits", 0):
        return "article_gap"
    if row.get("package_completeness", 1.0) < 0.65:
        return "package_gap"
    if diagnostics.get("quality_status") == "low":
        return "quality_gate_refusal"
    if issue_flags & {"missing_issue_coverage", "missing_issue_domains", "issue_domain_mismatch"}:
        return "coverage_gap"
    if row.get("confidence") == "low":
        return "generation_or_validation_failure"
    if row.get("confidence") == "medium":
        return "partial_confidence"
    return "ok"


def summarize_by_field(rows: list[dict[str, Any]], field_name: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(field_name) or "uncategorized"].append(row)

    summary: dict[str, dict[str, Any]] = {}
    for field_value, items in sorted(grouped.items()):
        summary[field_value] = {
            "cases": len(items),
            "average_score": round(mean(item["score"] for item in items), 3),
            "average_core_doc_recall": round(
                mean(metric_value(item.get("diagnostics", {}), "core_doc_recall", 1.0) for item in items),
                3,
            ),
            "average_bundle_completeness": round(
                mean(metric_value(item.get("diagnostics", {}), "bundle_completeness", 1.0) for item in items),
                3,
            ),
            "average_domain_purity": round(mean(float(item.get("domain_purity", 1.0)) for item in items), 3),
            "average_sub_issue_coverage": round(
                mean(float(item.get("sub_issue_coverage", 1.0)) for item in items),
                3,
            ),
            "average_package_completeness": round(
                mean(float(item.get("package_completeness", 1.0)) for item in items),
                3,
            ),
            "fatal_core_doc_miss_cases": sum(
                1
                for item in items
                if bool((item.get("diagnostics", {}) or {}).get("fatal_core_doc_miss"))
            ),
            "contamination_trap_cases": sum(1 for item in items if item.get("contamination_trap_hits")),
            "at_least_0_75": sum(1 for item in items if item["score"] >= 0.75),
            "at_least_0_85": sum(1 for item in items if item["score"] >= 0.85),
            "confidence_counts": dict(Counter(item["confidence"] for item in items)),
            "taxonomy_counts": dict(Counter(item["taxonomy"] for item in items)),
        }
    return summary


async def query_service(
    service_url: str,
    question: str,
    answer_mode: str = "consultation",
    retrieval_profile: str | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    payload_obj = {
        "question": question,
        "answer_mode": answer_mode,
    }
    if retrieval_profile:
        payload_obj["retrieval_profile"] = retrieval_profile
    payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        service_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    def _send() -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_send)


async def run_eval(
    input_path: Path,
    output_path: Path,
    per_case_timeout: float = 180.0,
    category_filter: str | None = None,
    question_type_filter: str | None = None,
    service_url: str | None = None,
    answer_mode: str = DEFAULT_ANSWER_MODE,
    retrieval_profile: str | None = None,
) -> None:
    aliases = load_regulation_aliases(REGULATIONS_PATH)
    cases = load_cases(
        input_path,
        category_filter=category_filter,
        question_type_filter=question_type_filter,
    )
    rows: list[dict[str, Any]] = []

    for case in cases:
        try:
            if service_url:
                response_payload = await asyncio.wait_for(
                    query_service(
                        service_url,
                        case["question"],
                        answer_mode=answer_mode,
                        retrieval_profile=retrieval_profile,
                        timeout_seconds=per_case_timeout,
                    ),
                    timeout=per_case_timeout + 5,
                )
                result_payload = (response_payload or {}).get("result", {})
                sources_text = result_payload.get("sources") or []
                diagnostics = result_payload.get("diagnostics") or {}
                answer_preview = str(result_payload.get("answer", ""))[:1400]
                confidence = str(result_payload.get("confidence", "low"))
                needs_escalation = bool(result_payload.get("needs_escalation", confidence == "low"))
            else:
                from app.rag.engine import get_engine

                engine = get_engine()
                result = await asyncio.wait_for(
                    engine.query(
                        case["question"],
                        answer_mode=answer_mode,
                        retrieval_profile=retrieval_profile or "",
                    ),
                    timeout=per_case_timeout,
                )
                sources_text = result.sources or []
                diagnostics = result.diagnostics or {}
                answer_preview = result.answer[:1400]
                confidence = result.confidence
                needs_escalation = result.needs_escalation
        except asyncio.TimeoutError:
            sources_text = []
            diagnostics = {
                "status": "low",
                "issue_flags": ["eval_timeout"],
                "helper_failures": ["eval_timeout"],
            }
            answer_preview = f"تعذر إكمال تقييم هذا السؤال خلال المهلة المحددة ({per_case_timeout} ثانية)."
            confidence = "low"
            needs_escalation = True
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
            TimeoutError,
        ) as exc:
            sources_text = []
            diagnostics = {
                "status": "low",
                "issue_flags": ["eval_transport_error"],
                "helper_failures": [type(exc).__name__],
            }
            answer_preview = f"تعذر تشغيل هذا السؤال عبر الخدمة المحلية: {exc}"
            confidence = "low"
            needs_escalation = True
        except Exception as exc:
            sources_text = []
            diagnostics = {
                "status": "low",
                "issue_flags": ["eval_error"],
                "helper_failures": [type(exc).__name__],
            }
            answer_preview = f"تعذر تشغيل هذا السؤال أثناء التقييم: {exc}"
            confidence = "low"
            needs_escalation = True

        matched_regulations = regulation_hits(
            case.get("expected_regulations", []),
            answer_preview,
            sources_text,
            diagnostics,
            aliases,
        )
        matched_articles = article_hits(
            answer_preview,
            sources_text,
            case.get("expected_articles", []),
            diagnostics,
        )
        refusal_detected = detect_refusal(answer_preview, confidence, diagnostics)
        allowed_regulations = case.get("allowed_regulations", []) or case.get("expected_regulations", [])
        unexpected_regs = unexpected_regulation_hits(allowed_regulations, diagnostics)
        trap_hits = contamination_trap_hits(case, diagnostics)
        sub_issue_coverage, sub_issue_details = evaluate_sub_issues(
            case,
            answer_preview,
            sources_text,
            diagnostics,
            aliases,
        )
        domain_purity = compute_domain_purity(case, diagnostics, trap_hits)

        row = {
            "question_id": case["question_id"],
            "question": case["question"],
            "question_type": case.get("question_type", ""),
            "benchmark_category": case.get("benchmark_category", ""),
            "expected_behavior": case.get("expected_behavior", "answer"),
            "expected_regulations": case.get("expected_regulations", []),
            "allowed_regulations": allowed_regulations,
            "expected_articles": case.get("expected_articles", []),
            "sub_issues": case.get("sub_issues", []),
            "min_expected_regulation_hits": case.get(
                "min_expected_regulation_hits",
                len(case.get("expected_regulations", [])) or 1,
            ),
            "min_expected_article_hits": case.get(
                "min_expected_article_hits",
                len(case.get("expected_articles", [])),
            ),
            "confidence": confidence,
            "needs_escalation": needs_escalation,
            "refusal_detected": refusal_detected,
            "matched_regulations": matched_regulations,
            "matched_regulation_count": len(matched_regulations),
            "matched_articles": matched_articles,
            "matched_article_count": len(matched_articles),
            "unexpected_regulations": unexpected_regs,
            "contamination_trap_hits": trap_hits,
            "domain_purity": domain_purity,
            "sub_issue_coverage": sub_issue_coverage,
            "sub_issue_count": len(case.get("sub_issues", [])),
            "sub_issue_details": sub_issue_details,
            "answer_preview": answer_preview,
            "source_count": len(sources_text),
            "diagnostics": {
                "quality_status": diagnostics.get("status"),
                "retrieval_profile": diagnostics.get("retrieval_profile"),
                "retrieval_profile_config": diagnostics.get("retrieval_profile_config", {}),
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
                "required_claim_intents": diagnostics.get("required_claim_intents", []),
                "covered_claim_intents": diagnostics.get("covered_claim_intents", []),
                "missing_claim_intents": diagnostics.get("missing_claim_intents", []),
                "expected_direct_articles": diagnostics.get("expected_direct_articles", []),
                "covered_direct_articles": diagnostics.get("covered_direct_articles", []),
                "missing_direct_articles": diagnostics.get("missing_direct_articles", []),
                "direct_article_recall": diagnostics.get("direct_article_recall"),
                "expected_bundle_articles": diagnostics.get("expected_bundle_articles", []),
                "covered_bundle_articles": diagnostics.get("covered_bundle_articles", []),
                "missing_bundle_articles": diagnostics.get("missing_bundle_articles", []),
                "bundle_article_recall": diagnostics.get("bundle_article_recall"),
                "required_core_regulations": diagnostics.get("required_core_regulations", []),
                "covered_core_regulations": diagnostics.get("covered_core_regulations", []),
                "missing_core_regulations": diagnostics.get("missing_core_regulations", []),
                "core_doc_recall": diagnostics.get("core_doc_recall"),
                "required_companion_regulations": diagnostics.get("required_companion_regulations", []),
                "covered_companion_regulations": diagnostics.get("covered_companion_regulations", []),
                "missing_companion_regulations": diagnostics.get("missing_companion_regulations", []),
                "companion_doc_recall": diagnostics.get("companion_doc_recall"),
                "bundle_completeness": diagnostics.get("bundle_completeness"),
                "fatal_core_doc_miss": diagnostics.get("fatal_core_doc_miss"),
                "document_class_counts": diagnostics.get("document_class_counts", {}),
                "missing_primary_law_anchor": diagnostics.get("missing_primary_law_anchor"),
                "procedural_or_supplementary_drift": diagnostics.get("procedural_or_supplementary_drift"),
                "unsupported_domain_signals": diagnostics.get("unsupported_domain_signals", []),
                "reference_date_signals": diagnostics.get("reference_date_signals", []),
                "domain_policy": diagnostics.get("domain_policy", {}),
                "helper_failures": diagnostics.get("helper_failures", []),
                "primary_ratio": diagnostics.get("primary_ratio"),
                "dominant_concentration": diagnostics.get("dominant_concentration"),
                "unique_article_count": diagnostics.get("unique_article_count"),
            },
        }
        row["package_completeness"] = compute_package_completeness(case, row)
        row["score"] = compute_case_score(case, row)
        row["taxonomy"] = classify_case(row)
        rows.append(row)
        write_report(output_path, cases, rows)

    summary = build_summary(cases, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--per-case-timeout", type=float, default=180.0)
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--question-type", type=str, default=None)
    parser.add_argument("--service-url", type=str, default=None)
    parser.add_argument("--answer-mode", type=str, default=DEFAULT_ANSWER_MODE)
    parser.add_argument("--retrieval-profile", type=str, default=None)
    args = parser.parse_args()
    asyncio.run(
        run_eval(
            args.input,
            args.output,
            per_case_timeout=args.per_case_timeout,
            category_filter=args.category,
            question_type_filter=args.question_type,
            service_url=args.service_url,
            answer_mode=args.answer_mode,
            retrieval_profile=args.retrieval_profile,
        )
    )


if __name__ == "__main__":
    main()
