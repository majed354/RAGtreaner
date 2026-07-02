"""Evaluate whether retrieval selects the expected article-level material.

This gate is intentionally narrower than package recall: it assumes the
regulation family has been collected, then checks whether the selected context
contains the operative article pairs needed for the issue.
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_cases(path: Path, split: str = "all", limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if split != "all" and row.get("split") != split:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def expected_pairs_from_case(case: dict[str, Any], query_data: dict[str, Any]) -> set[tuple[str, int]]:
    explicit = case.get("expected_articles_by_slug") or case.get("required_articles_by_slug")
    source = explicit if explicit else query_data.get("required_articles_by_slug", {})
    pairs: set[tuple[str, int]] = set()
    for slug, articles in (source or {}).items():
        for article in articles:
            try:
                pairs.add((str(slug), int(article)))
            except Exception:
                continue
    return pairs


def _pair_key(pair: tuple[str, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def _pair_keys_to_pairs(values: Any) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for value in values or []:
        if isinstance(value, str) and ":" in value:
            slug, article = value.rsplit(":", 1)
            try:
                pairs.add((slug, int(article)))
            except Exception:
                continue
    return pairs


DIAGNOSTIC_METRIC_KEYS = [
    "expected_article_ranks",
    "expected_article_context_positions",
    "expected_article_best_rank",
    "expected_article_mean_rank",
    "expected_article_mrr",
    "expected_article_best_context_position",
    "expected_article_mean_context_position",
    "expected_article_entered_context_rate",
    "pollution_rate",
    "irrelevant_context_count",
    "irrelevant_law_count",
    "irrelevant_laws",
    "selected_article_context_positions",
    "heldout_axis_hints",
    "heldout_axis_hint_count",
    "heldout_axis_article_pairs",
    "heldout_axis_packer_article_pairs",
    "heldout_axis_packer_article_count",
    "coverage_packer_article_pairs",
    "coverage_packer_article_count",
]


def _candidate_pair_key(candidate: dict[str, Any]) -> str | None:
    entry = candidate.get("entry") or {}
    try:
        article = int(entry.get("article_index") or 0)
    except Exception:
        article = 0
    slug = str(entry.get("regulation_slug") or "")
    if not slug or not article:
        return None
    return f"{slug}:{article}"


def _retrieval_metrics_from_candidates(
    ranked_candidates: list[dict[str, Any]],
    selected_candidates: list[dict[str, Any]],
    expected_pairs: set[tuple[str, int]],
    case: dict[str, Any],
    query_data: dict[str, Any],
) -> dict[str, Any]:
    ranked_article_rank: dict[str, int] = {}
    for index, candidate in enumerate(ranked_candidates, start=1):
        key = _candidate_pair_key(candidate)
        if key:
            ranked_article_rank.setdefault(key, index)
    selected_context_position: dict[str, int] = {}
    selected_slugs: list[str] = []
    for index, candidate in enumerate(selected_candidates, start=1):
        entry = candidate.get("entry") or {}
        slug = str(entry.get("regulation_slug") or "")
        if slug:
            selected_slugs.append(slug)
        key = _candidate_pair_key(candidate)
        if key:
            selected_context_position.setdefault(key, index)

    expected_keys = [_pair_key(pair) for pair in sorted(expected_pairs)]
    expected_article_ranks = {key: ranked_article_rank.get(key) for key in expected_keys}
    expected_article_context_positions = {key: selected_context_position.get(key) for key in expected_keys}
    found_rank_values = [rank for rank in expected_article_ranks.values() if rank]
    found_context_values = [position for position in expected_article_context_positions.values() if position]
    mrr = (
        sum((1.0 / rank) if rank else 0.0 for rank in expected_article_ranks.values()) / max(1, len(expected_keys))
        if expected_keys
        else 1.0
    )
    context_rate = len(found_context_values) / max(1, len(expected_keys)) if expected_keys else 1.0

    relevant_slugs = {
        str(slug)
        for slug in [
            *(case.get("expected_core_regulations") or []),
            *(case.get("expected_companion_regulations") or []),
            *(case.get("expected_implementing_regulations") or []),
            *list((case.get("expected_articles_by_slug") or {}).keys()),
            *(query_data.get("required_core_regulations") or []),
            *(query_data.get("required_companion_regulations") or []),
            *(query_data.get("required_regulations") or []),
            *list((query_data.get("required_articles_by_slug") or {}).keys()),
            *(query_data.get("learned_package_regulations") or []),
            *(query_data.get("learned_companion_regulations") or []),
            *list((query_data.get("learned_articles_by_slug") or {}).keys()),
        ]
        if str(slug).strip()
    }
    irrelevant_slugs = [slug for slug in selected_slugs if relevant_slugs and slug not in relevant_slugs]
    return {
        "expected_article_ranks": expected_article_ranks,
        "expected_article_context_positions": expected_article_context_positions,
        "expected_article_best_rank": min(found_rank_values) if found_rank_values else None,
        "expected_article_mean_rank": round(mean(found_rank_values), 1) if found_rank_values else None,
        "expected_article_mrr": round(mrr, 4),
        "expected_article_best_context_position": min(found_context_values) if found_context_values else None,
        "expected_article_mean_context_position": round(mean(found_context_values), 1) if found_context_values else None,
        "expected_article_entered_context_rate": round(context_rate, 3),
        "pollution_rate": round(len(irrelevant_slugs) / max(1, len(selected_candidates)), 3)
        if selected_candidates and relevant_slugs
        else 0.0,
        "irrelevant_context_count": len(irrelevant_slugs),
        "irrelevant_law_count": len(set(irrelevant_slugs)),
        "irrelevant_laws": sorted(set(irrelevant_slugs))[:24],
    }


def _metric_fields_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {key: diagnostics.get(key) for key in DIAGNOSTIC_METRIC_KEYS if key in diagnostics}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number > 0 else None


def _case_scoped_metric_fields(
    expected_pairs: set[tuple[str, int]],
    ranks: dict[str, Any] | None,
    context_positions: dict[str, Any] | None,
    selected_pairs: set[tuple[str, int]] | None = None,
) -> dict[str, Any]:
    """Subset broad service diagnostics to the gold article pairs for this case."""

    expected_keys = [_pair_key(pair) for pair in sorted(expected_pairs)]
    selected_keys = {_pair_key(pair) for pair in (selected_pairs or set())}
    rank_map = ranks or {}
    position_map = context_positions or {}
    case_ranks = {key: _coerce_positive_int(rank_map.get(key)) for key in expected_keys}
    case_positions = {key: _coerce_positive_int(position_map.get(key)) for key in expected_keys}
    found_ranks = [rank for rank in case_ranks.values() if rank]
    found_positions = [position for position in case_positions.values() if position]
    entered_keys = {
        key
        for key in expected_keys
        if case_positions.get(key) or (selected_keys and key in selected_keys)
    }
    mrr = (
        sum((1.0 / rank) if rank else 0.0 for rank in case_ranks.values()) / max(1, len(expected_keys))
        if expected_keys
        else 1.0
    )
    context_rate = len(entered_keys) / max(1, len(expected_keys)) if expected_keys else 1.0
    return {
        "case_expected_article_ranks": case_ranks,
        "case_expected_article_context_positions": case_positions,
        "case_expected_article_best_rank": min(found_ranks) if found_ranks else None,
        "case_expected_article_mean_rank": round(mean(found_ranks), 1) if found_ranks else None,
        "case_expected_article_mrr": round(mrr, 4),
        "case_expected_article_best_context_position": min(found_positions) if found_positions else None,
        "case_expected_article_mean_context_position": round(mean(found_positions), 1) if found_positions else None,
        "case_expected_article_entered_context_rate": round(context_rate, 3),
    }


def transport_error_row(
    case: dict[str, Any],
    *,
    error_type: str,
    error_message: str,
) -> dict[str, Any]:
    expected_pairs = expected_pairs_from_case(case, {})
    row = {
        "question_id": case.get("question_id", ""),
        "split": case.get("split", "dev"),
        "domain": case.get("domain", "uncategorized"),
        "question": case.get("question", ""),
        "transport_error": True,
        "transport_error_type": error_type,
        "transport_error_message": error_message[:600],
        "expected_article_pairs": [_pair_key(pair) for pair in sorted(expected_pairs)],
        "covered_article_pairs": [],
        "missing_article_pairs": [_pair_key(pair) for pair in sorted(expected_pairs)],
        "selected_article_pairs": [],
        "article_recall": 0.0,
        "article_points": 0.0,
        "min_article_recall": float(case.get("min_article_recall", 1.0)),
        "passed": False,
        "selected_context_count": 0,
    }
    return enrich_row(row, case, set(), expected_pairs, set(), expected_pairs, query_data={}, diagnostics={})


def expected_regulations_from_case(case: dict[str, Any], query_data: dict[str, Any], key: str) -> list[str]:
    explicit = case.get(key)
    if explicit is not None:
        return [str(item) for item in explicit if str(item).strip()]
    fallback_key = "required_core_regulations" if key == "expected_core_regulations" else "required_companion_regulations"
    return [str(item) for item in query_data.get(fallback_key, []) if str(item).strip()]


def evaluate_axis_coverage(axis_article_pairs: dict[str, Any], covered_pairs: set[tuple[str, int]]) -> dict[str, Any]:
    axes: dict[str, Any] = {}
    covered_keys = {_pair_key(pair) for pair in covered_pairs}
    for axis, values in (axis_article_pairs or {}).items():
        expected = sorted(_pair_key(pair) for pair in _pair_keys_to_pairs(values))
        covered = [pair for pair in expected if pair in covered_keys]
        missing = [pair for pair in expected if pair not in covered_keys]
        axes[str(axis)] = {
            "expected_article_pairs": expected,
            "covered_article_pairs": covered,
            "missing_article_pairs": missing,
            "passed": not missing,
        }
    return axes


def enrich_row(
    row: dict[str, Any],
    case: dict[str, Any],
    selected_slugs: set[str],
    expected_pairs: set[tuple[str, int]],
    covered_pairs: set[tuple[str, int]],
    missing_pairs: set[tuple[str, int]],
    query_data: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query_data = query_data or {}
    diagnostics = diagnostics or {}
    expected_core = expected_regulations_from_case(case, query_data, "expected_core_regulations")
    expected_companions = expected_regulations_from_case(case, query_data, "expected_companion_regulations")
    expected_implementing = [str(item) for item in case.get("expected_implementing_regulations", []) if str(item).strip()]

    covered_core = [slug for slug in expected_core if slug in selected_slugs]
    missing_core = [slug for slug in expected_core if slug not in selected_slugs]
    covered_companions = [slug for slug in expected_companions if slug in selected_slugs]
    missing_companions = [slug for slug in expected_companions if slug not in selected_slugs]
    covered_implementing = [slug for slug in expected_implementing if slug in selected_slugs]
    missing_implementing = [slug for slug in expected_implementing if slug not in selected_slugs]
    axis_coverage = evaluate_axis_coverage(case.get("axis_article_pairs") or {}, covered_pairs)
    failed_axes = [axis for axis, item in axis_coverage.items() if not item["passed"]]

    diagnostic_expected_pairs = _pair_keys_to_pairs(diagnostics.get("expected_direct_article_pairs", []))
    unrouted_expected_pairs = expected_pairs - diagnostic_expected_pairs if diagnostic_expected_pairs else set()
    covered_unrouted_expected_pairs = unrouted_expected_pairs & covered_pairs
    missing_unrouted_expected_pairs = unrouted_expected_pairs - covered_pairs

    row.update(
        {
            "expected_core_regulations": expected_core,
            "covered_core_regulations": covered_core,
            "missing_core_regulations": missing_core,
            "expected_companion_regulations": expected_companions,
            "covered_companion_regulations": covered_companions,
            "missing_companion_regulations": missing_companions,
            "expected_implementing_regulations": expected_implementing,
            "covered_implementing_regulations": covered_implementing,
            "missing_implementing_regulations": missing_implementing,
            "axis_coverage": axis_coverage,
            "failed_axes": failed_axes,
            "unrouted_expected_article_pairs": [f"{slug}:{article}" for slug, article in sorted(unrouted_expected_pairs)],
            "covered_unrouted_expected_article_pairs": [
                f"{slug}:{article}" for slug, article in sorted(covered_unrouted_expected_pairs)
            ],
            "missing_unrouted_expected_article_pairs": [
                f"{slug}:{article}" for slug, article in sorted(missing_unrouted_expected_pairs)
            ],
            "direct_article_routing_complete": not unrouted_expected_pairs,
            "governing_system_present": not missing_core,
            "implementing_regulation_present": not missing_implementing,
            "all_axes_covered": not failed_axes,
        }
    )
    row["passed"] = bool(
        row["passed"]
        and not missing_core
        and not missing_implementing
        and not failed_axes
        and not missing_unrouted_expected_pairs
    )
    return row


async def evaluate_case(engine: Any, case: dict[str, Any], retrieval_profile: str) -> dict[str, Any]:
    retrieval_result = await engine._hybrid_retrieve(
        case["question"],
        answer_mode="benchmark",
        retrieval_profile=retrieval_profile,
    )
    selected_candidates = retrieval_result.get("selected_candidates") or []
    query_data = retrieval_result.get("query_data") or {}
    selected_pairs = {
        (str(candidate["entry"].get("regulation_slug") or ""), int(candidate["entry"].get("article_index") or 0))
        for candidate in selected_candidates
        if candidate.get("entry") and int(candidate["entry"].get("article_index") or 0)
    }
    selected_slugs = {
        str(candidate["entry"].get("regulation_slug") or "")
        for candidate in selected_candidates
        if candidate.get("entry") and str(candidate["entry"].get("regulation_slug") or "").strip()
    }
    expected_pairs = expected_pairs_from_case(case, query_data)
    covered_pairs = selected_pairs & expected_pairs
    missing_pairs = expected_pairs - selected_pairs
    recall = len(covered_pairs) / max(1, len(expected_pairs)) if expected_pairs else 1.0
    min_recall = float(case.get("min_article_recall", 1.0))
    passed = recall >= min_recall and not missing_pairs if min_recall >= 1.0 else recall >= min_recall
    row = {
        "question_id": case["question_id"],
        "split": case.get("split", "dev"),
        "domain": case.get("domain", "uncategorized"),
        "question": case["question"],
        "matched_document_bundles": query_data.get("matched_document_bundles", []),
        "matched_issue_axis_bundles": query_data.get("matched_issue_axis_bundles", []),
        "expected_article_pairs": [f"{slug}:{article}" for slug, article in sorted(expected_pairs)],
        "covered_article_pairs": [f"{slug}:{article}" for slug, article in sorted(covered_pairs)],
        "missing_article_pairs": [f"{slug}:{article}" for slug, article in sorted(missing_pairs)],
        "selected_article_pairs": [f"{slug}:{article}" for slug, article in sorted(selected_pairs)],
        "article_recall": round(recall, 3),
        "article_points": round(recall * 100, 1),
        "min_article_recall": min_recall,
        "passed": passed,
        "selected_context_count": len(selected_candidates),
    }
    row.update(
        _retrieval_metrics_from_candidates(
            retrieval_result.get("ranked_candidates") or [],
            selected_candidates,
            expected_pairs,
            case,
            query_data,
        )
    )
    row.update(
        _case_scoped_metric_fields(
            expected_pairs,
            row.get("expected_article_ranks") or {},
            row.get("expected_article_context_positions") or {},
            selected_pairs,
        )
    )
    return enrich_row(row, case, selected_slugs, expected_pairs, covered_pairs, missing_pairs, query_data=query_data)


def evaluate_case_via_service(case: dict[str, Any], retrieval_profile: str, service_url: str, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(
        {
            "question": case["question"],
            "answer_mode": "benchmark",
            "retrieval_profile": retrieval_profile,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        service_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        ConnectionError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ) as exc:
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(max(1.0, float(timeout_seconds))),
                    "-X",
                    "POST",
                    service_url,
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    payload.decode("utf-8"),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            response_data = json.loads(completed.stdout)
        except Exception as curl_exc:
            curl_detail = str(curl_exc)
            if isinstance(curl_exc, subprocess.CalledProcessError):
                curl_detail = (curl_exc.stderr or curl_exc.stdout or str(curl_exc)).strip()
            return transport_error_row(
                case,
                error_type=type(curl_exc).__name__,
                error_message=f"{type(exc).__name__}: {str(exc)[:240]} | curl fallback: {curl_detail[:240]}",
            )

    result = response_data.get("result") or {}
    diagnostics = result.get("diagnostics") or {}
    selected_slugs = set((diagnostics.get("document_class_counts") or {}).keys())
    expected_pairs = expected_pairs_from_case(case, {})
    if not expected_pairs:
        expected_pairs = _pair_keys_to_pairs(diagnostics.get("expected_direct_article_pairs", []))
    diagnostic_selected_pairs = _pair_keys_to_pairs(diagnostics.get("selected_article_pairs", []))
    diagnostic_covered_pairs = _pair_keys_to_pairs(diagnostics.get("covered_direct_article_pairs", []))
    if case.get("expected_articles_by_slug") or case.get("required_articles_by_slug"):
        covered_pairs = expected_pairs & (diagnostic_selected_pairs or diagnostic_covered_pairs)
    else:
        covered_pairs = expected_pairs & diagnostic_covered_pairs
    missing_pairs = expected_pairs - covered_pairs
    recall = len(covered_pairs) / max(1, len(expected_pairs)) if expected_pairs else 1.0
    min_recall = float(case.get("min_article_recall", 1.0))
    passed = recall >= min_recall and not missing_pairs if min_recall >= 1.0 else recall >= min_recall
    row = {
        "question_id": case["question_id"],
        "split": case.get("split", "dev"),
        "domain": case.get("domain", "uncategorized"),
        "question": case["question"],
        "matched_document_bundles": diagnostics.get("matched_document_bundles", []),
        "matched_issue_axis_bundles": diagnostics.get("matched_issue_axis_bundles", []),
        "expected_article_pairs": [f"{slug}:{article}" for slug, article in sorted(expected_pairs)],
        "covered_article_pairs": [f"{slug}:{article}" for slug, article in sorted(covered_pairs)],
        "missing_article_pairs": [f"{slug}:{article}" for slug, article in sorted(missing_pairs)],
        "selected_article_pairs": diagnostics.get("selected_article_pairs") or diagnostics.get("covered_direct_article_pairs", []),
        "article_recall": round(recall, 3),
        "article_points": round(recall * 100, 1),
        "min_article_recall": min_recall,
        "passed": passed,
        "selected_context_count": diagnostics.get("selected_candidate_count"),
        "transport_error": False,
        "service_status": response_data.get("status"),
        "semantic_profile": diagnostics.get("retrieval_profile_config", {}),
    }
    row.update(_metric_fields_from_diagnostics(diagnostics))
    row.update(
        _case_scoped_metric_fields(
            expected_pairs,
            diagnostics.get("expected_article_ranks") or {},
            diagnostics.get("selected_article_context_positions")
            or diagnostics.get("expected_article_context_positions")
            or {},
            diagnostic_selected_pairs,
        )
    )
    return enrich_row(row, case, selected_slugs, expected_pairs, covered_pairs, missing_pairs, diagnostics=diagnostics)


def safe_evaluate_case_via_service(
    case: dict[str, Any],
    retrieval_profile: str,
    service_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        return evaluate_case_via_service(case, retrieval_profile, service_url, timeout_seconds)
    except Exception as exc:
        return transport_error_row(
            case,
            error_type=type(exc).__name__,
            error_message=f"uncaught service evaluation error: {str(exc)[:480]}",
        )


def summarize(rows: list[dict[str, Any]], benchmark_id: str, retrieval_profile: str) -> dict[str, Any]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_domain[str(row.get("domain") or "uncategorized")].append(row)

    def score_buckets(items: list[dict[str, Any]]) -> dict[str, int]:
        non_operational = [row for row in items if not row.get("transport_error")]
        return {
            "passed": sum(1 for row in non_operational if row.get("passed")),
            "near_miss_90_99": sum(
                1
                for row in non_operational
                if not row.get("passed") and 90.0 <= float(row.get("article_points") or 0.0) < 100.0
            ),
            "partial_50_89": sum(
                1
                for row in non_operational
                if not row.get("passed") and 50.0 <= float(row.get("article_points") or 0.0) < 90.0
            ),
            "low_0_49": sum(
                1
                for row in non_operational
                if not row.get("passed") and float(row.get("article_points") or 0.0) < 50.0
            ),
            "operational": sum(1 for row in items if row.get("transport_error")),
        }

    def mean_numeric(items: list[dict[str, Any]], key: str, digits: int = 3) -> float | None:
        values: list[float] = []
        for row in items:
            if row.get("transport_error"):
                continue
            value = row.get(key)
            if value is None:
                continue
            try:
                values.append(float(value))
            except Exception:
                continue
        return round(mean(values), digits) if values else None

    def group(items: list[dict[str, Any]]) -> dict[str, Any]:
        non_operational = [row for row in items if not row.get("transport_error")]
        return {
            "cases": len(items),
            "non_operational_cases": len(non_operational),
            "article_score_100": round(mean(float(row["article_points"]) for row in non_operational), 1)
            if non_operational
            else 0.0,
            "pass_rate": round(sum(1 for row in non_operational if row["passed"]) / max(1, len(non_operational)), 3),
            "failed_cases": sum(1 for row in non_operational if not row["passed"]),
            "transport_error_cases": sum(1 for row in items if row.get("transport_error")),
            "governing_system_rate": round(
                sum(1 for row in non_operational if row.get("governing_system_present", True))
                / max(1, len(non_operational)),
                3,
            ),
            "implementing_regulation_rate": round(
                sum(1 for row in non_operational if row.get("implementing_regulation_present", True))
                / max(1, len(non_operational)),
                3,
            ),
            "axis_coverage_rate": round(
                sum(1 for row in non_operational if row.get("all_axes_covered", True))
                / max(1, len(non_operational)),
                3,
            ),
            "article_mrr": mean_numeric(items, "expected_article_mrr"),
            "mean_expected_article_rank": mean_numeric(items, "expected_article_mean_rank", 1),
            "mean_context_position": mean_numeric(items, "expected_article_mean_context_position", 1),
            "context_entry_rate": mean_numeric(items, "expected_article_entered_context_rate"),
            "case_article_mrr": mean_numeric(items, "case_expected_article_mrr"),
            "case_mean_expected_article_rank": mean_numeric(items, "case_expected_article_mean_rank", 1),
            "case_mean_context_position": mean_numeric(items, "case_expected_article_mean_context_position", 1),
            "case_context_entry_rate": mean_numeric(items, "case_expected_article_entered_context_rate"),
            "pollution_rate": mean_numeric(items, "pollution_rate"),
            "irrelevant_law_count_avg": mean_numeric(items, "irrelevant_law_count", 1),
            "score_buckets": score_buckets(items),
        }

    non_operational_rows = [row for row in rows if not row.get("transport_error")]
    worst_pool = non_operational_rows if non_operational_rows else rows
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": benchmark_id,
        "retrieval_profile": retrieval_profile,
        "cases_total": len(rows),
        "non_operational_cases": len(non_operational_rows),
        "article_score_100": round(mean(float(row["article_points"]) for row in non_operational_rows), 1)
        if non_operational_rows
        else 0.0,
        "pass_rate": round(
            sum(1 for row in non_operational_rows if row["passed"]) / max(1, len(non_operational_rows)),
            3,
        ),
        "failed_cases": sum(1 for row in non_operational_rows if not row["passed"]),
        "governing_system_rate": round(
            sum(1 for row in non_operational_rows if row.get("governing_system_present", True))
            / max(1, len(non_operational_rows)),
            3,
        ),
        "implementing_regulation_rate": round(
            sum(1 for row in non_operational_rows if row.get("implementing_regulation_present", True))
            / max(1, len(non_operational_rows)),
            3,
        ),
        "axis_coverage_rate": round(
            sum(1 for row in non_operational_rows if row.get("all_axes_covered", True))
            / max(1, len(non_operational_rows)),
            3,
        ),
        "article_mrr": mean_numeric(rows, "expected_article_mrr"),
        "mean_expected_article_rank": mean_numeric(rows, "expected_article_mean_rank", 1),
        "mean_context_position": mean_numeric(rows, "expected_article_mean_context_position", 1),
        "context_entry_rate": mean_numeric(rows, "expected_article_entered_context_rate"),
        "case_article_mrr": mean_numeric(rows, "case_expected_article_mrr"),
        "case_mean_expected_article_rank": mean_numeric(rows, "case_expected_article_mean_rank", 1),
        "case_mean_context_position": mean_numeric(rows, "case_expected_article_mean_context_position", 1),
        "case_context_entry_rate": mean_numeric(rows, "case_expected_article_entered_context_rate"),
        "pollution_rate": mean_numeric(rows, "pollution_rate"),
        "irrelevant_law_count_avg": mean_numeric(rows, "irrelevant_law_count", 1),
        "transport_error_cases": sum(1 for row in rows if row.get("transport_error")),
        "score_buckets": score_buckets(rows),
        "domain_counts": dict(Counter(row.get("domain") for row in rows)),
        "by_domain": {key: group(items) for key, items in sorted(by_domain.items())},
        "worst_cases": [
            {
                "question_id": row["question_id"],
                "domain": row["domain"],
                "article_points": row["article_points"],
                "missing_article_pairs": row["missing_article_pairs"][:24],
                "missing_core_regulations": row.get("missing_core_regulations", []),
                "missing_implementing_regulations": row.get("missing_implementing_regulations", []),
                "failed_axes": row.get("failed_axes", []),
                "unrouted_expected_article_pairs": row.get("unrouted_expected_article_pairs", [])[:24],
                "expected_article_mean_rank": row.get("expected_article_mean_rank"),
                "expected_article_mean_context_position": row.get("expected_article_mean_context_position"),
                "expected_article_mrr": row.get("expected_article_mrr"),
                "case_expected_article_mean_rank": row.get("case_expected_article_mean_rank"),
                "case_expected_article_mean_context_position": row.get("case_expected_article_mean_context_position"),
                "case_expected_article_mrr": row.get("case_expected_article_mrr"),
                "case_expected_article_entered_context_rate": row.get("case_expected_article_entered_context_rate"),
                "pollution_rate": row.get("pollution_rate"),
            }
            for row in sorted(worst_pool, key=lambda item: (item["article_points"], item["question_id"]))[:12]
        ],
    }


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['benchmark_id']}",
        "",
        f"- article score: `{summary['article_score_100']}/100`",
        f"- pass rate: `{summary['pass_rate']}`",
        f"- failed cases: `{summary['failed_cases']}`",
        f"- score buckets: `{summary.get('score_buckets', {})}`",
        f"- governing system rate: `{summary['governing_system_rate']}`",
        f"- implementing regulation rate: `{summary['implementing_regulation_rate']}`",
        f"- axis coverage rate: `{summary['axis_coverage_rate']}`",
        f"- article MRR: `{summary.get('article_mrr')}`",
        f"- mean expected article rank: `{summary.get('mean_expected_article_rank')}`",
        f"- mean context position: `{summary.get('mean_context_position')}`",
        f"- case-scoped article MRR: `{summary.get('case_article_mrr')}`",
        f"- case-scoped mean expected article rank: `{summary.get('case_mean_expected_article_rank')}`",
        f"- case-scoped mean context position: `{summary.get('case_mean_context_position')}`",
        f"- case-scoped context entry rate: `{summary.get('case_context_entry_rate')}`",
        f"- pollution rate: `{summary.get('pollution_rate')}`",
        f"- transport error cases: `{summary['transport_error_cases']}`",
        "",
        "## By Domain",
        "",
    ]
    for domain, item in summary["by_domain"].items():
        lines.append(
            f"- `{domain}`: `{item['article_score_100']}/100`, pass `{item['pass_rate']}`, "
            f"core `{item['governing_system_rate']}`, impl `{item['implementing_regulation_rate']}`, "
            f"axes `{item['axis_coverage_rate']}`, mrr `{item.get('article_mrr')}`, "
            f"case_ctx `{item.get('case_context_entry_rate')}`, "
            f"pollution `{item.get('pollution_rate')}`, failed `{item['failed_cases']}`"
        )
    lines.extend(["", "## Worst Cases", ""])
    for row in summary["worst_cases"]:
        lines.append(
            f"- `{row['question_id']}` `{row['domain']}`: `{row['article_points']}/100`; "
            f"missing={row['missing_article_pairs']}; "
            f"missing_core={row.get('missing_core_regulations', [])}; "
            f"missing_impl={row.get('missing_implementing_regulations', [])}; "
            f"failed_axes={row.get('failed_axes', [])}; "
            f"unrouted={row.get('unrouted_expected_article_pairs', [])}; "
            f"rank={row.get('expected_article_mean_rank')}; "
            f"context={row.get('expected_article_mean_context_position')}; "
            f"case_rank={row.get('case_expected_article_mean_rank')}; "
            f"case_context={row.get('case_expected_article_mean_context_position')}; "
            f"case_context_entry={row.get('case_expected_article_entered_context_rate')}; "
            f"pollution={row.get('pollution_rate')}"
        )
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, split=args.split, limit=args.limit)
    if args.service_url:
        rows = [
            safe_evaluate_case_via_service(case, args.retrieval_profile, args.service_url, args.timeout_seconds)
            for case in cases
        ]
    else:
        from app.rag.engine import LegalRAGEngine

        engine = LegalRAGEngine()
        if args.local_no_dense:
            async def _no_dense(question: str, query_data: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            engine._dense_candidates = _no_dense

        rows = [await evaluate_case(engine, case, args.retrieval_profile) for case in cases]
    summary = summarize(rows, args.benchmark_id, args.retrieval_profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--benchmark-id", default="article_precision_gate")
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--split", choices=["all", "dev", "regression", "heldout"], default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--local-no-dense", action="store_true")
    parser.add_argument("--service-url", default="")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
