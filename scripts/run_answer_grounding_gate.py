#!/usr/bin/env python3
"""Evaluate whether the final answer grounds expected articles explicitly.

This is intentionally answer-level, not retrieval-level.  A case can pass
article precision because the material is in context, yet fail here if the
answer only lists article numbers without tying them to their regulations.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_BY_REGULATION = PROJECT_ROOT / "data" / "structured" / "by_regulation"
DEFAULT_CASES = PROJECT_ROOT / "data" / "eval" / "manual_answer_grounding_blind12_20260630.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "manual_answer_grounding_blind12_20260630_baseline.json"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
NON_WORD_RE = re.compile(r"[^\w\u0600-\u06ff]+")


def normalize(text: str) -> str:
    value = str(text or "").translate(ARABIC_DIGITS)
    value = DIACRITICS_RE.sub("", value)
    value = value.replace("ـ", "")
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    return " ".join(NON_WORD_RE.sub(" ", value.lower()).split())


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def expected_pairs(case: dict[str, Any]) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for slug, articles in (case.get("expected_articles_by_slug") or {}).items():
        for article in articles or []:
            try:
                pairs.append((str(slug), int(article)))
            except Exception:
                continue
    return sorted(set(pairs))


def pair_key(pair: tuple[str, int]) -> str:
    return f"{pair[0]}:{pair[1]}"


def title_aliases(slug: str) -> list[str]:
    aliases = [slug, slug.replace("-", " "), slug.replace("_", " ")]
    path = STRUCTURED_BY_REGULATION / f"{slug}.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = payload.get("metadata") or {}
            for key in ("title_ar", "title", "name", "short_title_ar"):
                value = str(metadata.get(key) or "").strip()
                if value:
                    aliases.append(value)
        except Exception:
            pass
    return list(dict.fromkeys(normalize(alias) for alias in aliases if normalize(alias)))


def article_patterns(article: int) -> list[re.Pattern[str]]:
    value = str(int(article))
    return [
        re.compile(rf"\bالماده\s*(?:رقم\s*)?\(?\s*{re.escape(value)}\s*\)?\b"),
        re.compile(rf"\bماده\s*(?:رقم\s*)?\(?\s*{re.escape(value)}\s*\)?\b"),
        re.compile(rf"\barticle\s*{re.escape(value)}\b", re.IGNORECASE),
        re.compile(rf":{re.escape(value)}\b"),
    ]


def article_mentioned(normalized_text: str, article: int) -> bool:
    return any(pattern.search(normalized_text) for pattern in article_patterns(article))


def split_segments(answer: str) -> list[str]:
    raw_segments: list[str] = []
    for line in str(answer or "").splitlines():
        line = line.strip()
        if not line:
            continue
        raw_segments.append(line)
        raw_segments.extend(part.strip() for part in re.split(r"[؛;]", line) if part.strip())
    return [normalize(segment) for segment in raw_segments if normalize(segment)]


def pair_bound_in_answer(answer: str, slug: str, article: int) -> bool:
    normalized_answer = normalize(answer)
    literal = normalize(f"{slug}:{article}")
    if literal and literal in normalized_answer:
        return True
    aliases = title_aliases(slug)
    for segment in split_segments(answer):
        if not article_mentioned(segment, article):
            continue
        if any(alias and alias in segment for alias in aliases):
            return True
    return False


def evaluate_axis(case: dict[str, Any], bound_pairs: set[str]) -> dict[str, Any]:
    axes: dict[str, Any] = {}
    axis_map = case.get("axis_article_pairs") or {"expected_material": [pair_key(pair) for pair in expected_pairs(case)]}
    for axis, values in axis_map.items():
        expected = [str(value) for value in values or [] if ":" in str(value)]
        covered = [value for value in expected if value in bound_pairs]
        missing = [value for value in expected if value not in bound_pairs]
        axes[str(axis)] = {
            "expected_article_pairs": expected,
            "bound_article_pairs": covered,
            "missing_bound_article_pairs": missing,
            "passed": not missing,
        }
    return axes


def query_service(case: dict[str, Any], service_url: str, answer_mode: str, retrieval_profile: str, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(
        {
            "question": case["question"],
            "answer_mode": answer_mode,
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
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def transport_row(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    pairs = [pair_key(pair) for pair in expected_pairs(case)]
    return {
        "question_id": case.get("question_id"),
        "domain": case.get("domain"),
        "question": case.get("question"),
        "transport_error": True,
        "transport_error_type": type(exc).__name__,
        "transport_error_message": str(exc)[:600],
        "expected_article_pairs": pairs,
        "answer_bound_article_pairs": [],
        "missing_answer_bound_article_pairs": pairs,
        "grounded_article_recall": 0.0,
        "article_number_recall": 0.0,
        "regulation_presence_rate": 0.0,
        "axis_coverage": {},
        "failed_axes": [],
        "passed": False,
    }


def evaluate_case(case: dict[str, Any], service_url: str, answer_mode: str, retrieval_profile: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        response = query_service(case, service_url, answer_mode, retrieval_profile, timeout_seconds)
    except (
        urllib.error.URLError,
        TimeoutError,
        socket.timeout,
        ConnectionError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ) as exc:
        return transport_row(case, exc)

    result = response.get("result") or {}
    answer = str(result.get("answer") or "")
    diagnostics = result.get("diagnostics") or {}
    pairs = expected_pairs(case)
    normalized_answer = normalize(answer)

    bound = {pair_key(pair) for pair in pairs if pair_bound_in_answer(answer, pair[0], pair[1])}
    article_number_hits = {
        pair_key(pair)
        for pair in pairs
        if article_mentioned(normalized_answer, pair[1])
    }
    regulation_hits = {
        slug
        for slug, _article in pairs
        if any(alias and alias in normalized_answer for alias in title_aliases(slug))
    }
    expected_slugs = {slug for slug, _article in pairs}
    axis_coverage = evaluate_axis(case, bound)
    failed_axes = [axis for axis, details in axis_coverage.items() if not details["passed"]]
    min_recall = float(case.get("min_grounded_article_recall") or 1.0)
    grounded_recall = len(bound) / max(1, len(pairs)) if pairs else 1.0
    article_number_recall = len(article_number_hits) / max(1, len(pairs)) if pairs else 1.0
    regulation_presence_rate = len(regulation_hits) / max(1, len(expected_slugs)) if expected_slugs else 1.0
    missing_bound = [pair_key(pair) for pair in pairs if pair_key(pair) not in bound]
    passed = grounded_recall >= min_recall and not failed_axes and regulation_presence_rate >= 1.0

    return {
        "question_id": case.get("question_id"),
        "domain": case.get("domain"),
        "question": case.get("question"),
        "transport_error": False,
        "service_status": response.get("status"),
        "confidence": result.get("confidence"),
        "expected_article_pairs": [pair_key(pair) for pair in pairs],
        "answer_bound_article_pairs": sorted(bound),
        "missing_answer_bound_article_pairs": missing_bound,
        "article_number_hits_without_binding": sorted(article_number_hits - bound),
        "grounded_article_recall": round(grounded_recall, 3),
        "grounded_article_points": round(grounded_recall * 100.0, 1),
        "article_number_recall": round(article_number_recall, 3),
        "regulation_presence_rate": round(regulation_presence_rate, 3),
        "axis_coverage": axis_coverage,
        "failed_axes": failed_axes,
        "passed": passed,
        "answer_preview": answer[:1800],
        "retrieval_direct_article_recall": diagnostics.get("direct_article_recall"),
        "retrieval_expected_article_entered_context_rate": diagnostics.get("expected_article_entered_context_rate"),
        "retrieval_mean_context_position": diagnostics.get("expected_article_mean_context_position"),
        "retrieval_pollution_rate": diagnostics.get("pollution_rate"),
    }


def summarize(rows: list[dict[str, Any]], benchmark_id: str, answer_mode: str, retrieval_profile: str) -> dict[str, Any]:
    non_operational = [row for row in rows if not row.get("transport_error")]
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_domain[str(row.get("domain") or "uncategorized")].append(row)

    def mean_key(items: list[dict[str, Any]], key: str) -> float | None:
        values = []
        for item in items:
            if item.get("transport_error") or item.get(key) is None:
                continue
            try:
                values.append(float(item.get(key)))
            except Exception:
                continue
        return round(mean(values), 3) if values else None

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": benchmark_id,
        "answer_mode": answer_mode,
        "retrieval_profile": retrieval_profile,
        "cases_total": len(rows),
        "non_operational_cases": len(non_operational),
        "answer_grounding_score_100": round(mean(float(row.get("grounded_article_points") or 0.0) for row in non_operational), 1)
        if non_operational
        else 0.0,
        "pass_rate": round(sum(1 for row in non_operational if row.get("passed")) / max(1, len(non_operational)), 3),
        "failed_cases": sum(1 for row in non_operational if not row.get("passed")),
        "transport_error_cases": sum(1 for row in rows if row.get("transport_error")),
        "article_number_recall": mean_key(non_operational, "article_number_recall"),
        "regulation_presence_rate": mean_key(non_operational, "regulation_presence_rate"),
        "retrieval_direct_article_recall": mean_key(non_operational, "retrieval_direct_article_recall"),
        "retrieval_context_entry_rate": mean_key(non_operational, "retrieval_expected_article_entered_context_rate"),
        "retrieval_mean_context_position": mean_key(non_operational, "retrieval_mean_context_position"),
        "domain_counts": dict(Counter(row.get("domain") for row in rows)),
        "by_domain": {
            domain: {
                "cases": len(items),
                "score": mean_key(items, "grounded_article_points"),
                "pass_rate": round(
                    sum(1 for item in items if not item.get("transport_error") and item.get("passed"))
                    / max(1, sum(1 for item in items if not item.get("transport_error"))),
                    3,
                ),
                "failed": sum(1 for item in items if not item.get("transport_error") and not item.get("passed")),
            }
            for domain, items in sorted(by_domain.items())
        },
        "worst_cases": [
            {
                "question_id": row.get("question_id"),
                "domain": row.get("domain"),
                "grounded_article_points": row.get("grounded_article_points"),
                "missing_answer_bound_article_pairs": row.get("missing_answer_bound_article_pairs", [])[:20],
                "article_number_hits_without_binding": row.get("article_number_hits_without_binding", [])[:20],
                "failed_axes": row.get("failed_axes", []),
                "retrieval_direct_article_recall": row.get("retrieval_direct_article_recall"),
            }
            for row in sorted(non_operational, key=lambda item: (float(item.get("grounded_article_points") or 0.0), str(item.get("question_id"))))[:12]
        ],
    }


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['benchmark_id']}",
        "",
        f"- answer grounding score: `{summary['answer_grounding_score_100']}/100`",
        f"- pass rate: `{summary['pass_rate']}`",
        f"- failed cases: `{summary['failed_cases']}`",
        f"- transport error cases: `{summary['transport_error_cases']}`",
        f"- article number recall: `{summary.get('article_number_recall')}`",
        f"- regulation presence rate: `{summary.get('regulation_presence_rate')}`",
        f"- retrieval direct article recall: `{summary.get('retrieval_direct_article_recall')}`",
        "",
        "## Worst Cases",
        "",
    ]
    for row in summary["worst_cases"]:
        lines.append(
            f"- `{row['question_id']}` `{row['domain']}`: `{row['grounded_article_points']}/100`; "
            f"missing_bound={row['missing_answer_bound_article_pairs']}; "
            f"unbound_numbers={row['article_number_hits_without_binding']}; "
            f"failed_axes={row['failed_axes']}; retrieval_recall={row['retrieval_direct_article_recall']}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--benchmark-id", default="answer_grounding_gate")
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--answer-mode", default="consultation")
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    rows = [
        evaluate_case(case, args.service_url, args.answer_mode, args.retrieval_profile, args.timeout_seconds)
        for case in load_cases(args.cases)
    ]
    summary = summarize(rows, args.benchmark_id, args.answer_mode, args.retrieval_profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


if __name__ == "__main__":
    main()
