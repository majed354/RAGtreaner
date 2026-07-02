"""Summarize article precision reports by operational/retrieval/answer issue.

This is a diagnostic layer over run_article_precision_gate.py output. It does
not rescore; it classifies failures so manual work targets clusters rather than
one-off legal families.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "summary": data.get("summary", data),
        "rows": data.get("rows", []),
    }


def load_matrix(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["pair"]: item for item in data.get("article_pairs", [])}


def classify_row(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("transport_error"):
        return "operational issue", "transport_error"

    missing_core = row.get("missing_core_regulations") or []
    missing_impl = row.get("missing_implementing_regulations") or []
    unrouted = row.get("unrouted_expected_article_pairs") or []
    missing_unrouted = row.get("missing_unrouted_expected_article_pairs") or []
    covered_unrouted = row.get("covered_unrouted_expected_article_pairs") or []
    missing_pairs = row.get("missing_article_pairs") or []
    failed_axes = row.get("failed_axes") or []

    if missing_core:
        return "retrieval/package issue", "missing_core_regulation"
    if missing_impl:
        return "retrieval/package issue", "missing_implementing_regulation"
    if missing_pairs:
        if missing_unrouted or any(pair in set(unrouted) for pair in missing_pairs):
            return "retrieval/package issue", "expected_article_not_routed_and_missing"
        selected_count = row.get("selected_context_count")
        context_limit = None
        semantic_profile = row.get("semantic_profile") or {}
        if isinstance(semantic_profile, dict):
            context_limit = semantic_profile.get("context_limit")
        try:
            at_limit = int(selected_count or 0) >= int(context_limit or 10**9)
        except Exception:
            at_limit = False
        if at_limit:
            return "retrieval/package issue", "context_budget_displacement"
        return "retrieval/package issue", "missing_article_material"
    if failed_axes:
        return "retrieval/package issue", "axis_material_gap"
    if unrouted:
        if covered_unrouted:
            return "ok", "article_present_but_not_directly_routed"
        return "retrieval/package issue", "expected_article_not_routed"
    if not row.get("passed", True):
        return "answer-level issue", "gate_logic_or_answer_surface"
    return "ok", "ok"


def summarize(report: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    rows = report["rows"]
    classification_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    domain_reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
    missing_pairs: Counter[str] = Counter()
    missing_regulations: Counter[str] = Counter()
    failed_axes: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []

    for row in rows:
        classification, reason = classify_row(row)
        classification_counts[classification] += 1
        reason_counts[reason] += 1
        domain = str(row.get("domain") or "uncategorized")
        domain_reason_counts[domain][reason] += 1
        if classification == "ok":
            continue

        for pair in row.get("missing_article_pairs") or []:
            missing_pairs[pair] += 1
        for reg in (row.get("missing_core_regulations") or []) + (row.get("missing_implementing_regulations") or []):
            missing_regulations[reg] += 1
        for axis in row.get("failed_axes") or []:
            failed_axes[f"{domain}:{axis}"] += 1

        enriched_pairs = []
        for pair in (row.get("missing_article_pairs") or [])[:12]:
            meta = matrix.get(pair, {})
            enriched_pairs.append(
                {
                    "pair": pair,
                    "citation_short_ar": meta.get("citation_short_ar"),
                    "domains": meta.get("domains", []),
                    "axes": meta.get("axes", []),
                }
            )

        findings.append(
            {
                "question_id": row.get("question_id"),
                "domain": domain,
                "classification": classification,
                "reason": reason,
                "article_points": row.get("article_points"),
                "missing_core_regulations": row.get("missing_core_regulations") or [],
                "missing_implementing_regulations": row.get("missing_implementing_regulations") or [],
                "missing_article_pairs": row.get("missing_article_pairs") or [],
                "missing_article_pair_details": enriched_pairs,
                "failed_axes": row.get("failed_axes") or [],
                "transport_error_message": row.get("transport_error_message"),
            }
        )

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_summary": report["summary"],
        "classification_counts": dict(classification_counts),
        "reason_counts": dict(reason_counts),
        "domain_reason_counts": {domain: dict(counter) for domain, counter in sorted(domain_reason_counts.items())},
        "top_missing_article_pairs": [
            {"pair": pair, "count": count, "citation_short_ar": matrix.get(pair, {}).get("citation_short_ar")}
            for pair, count in missing_pairs.most_common(30)
        ],
        "top_missing_regulations": [
            {"regulation_slug": slug, "count": count} for slug, count in missing_regulations.most_common(30)
        ],
        "top_failed_axes": [{"axis": axis, "count": count} for axis, count in failed_axes.most_common(30)],
        "blocking_findings": findings[:100],
        "decision": "PASS" if classification_counts.get("ok", 0) == len(rows) else "FAIL",
    }


def markdown_report(summary: dict[str, Any]) -> str:
    source = summary.get("source_summary") or {}
    lines = [
        "# Article Precision Gap Summary",
        "",
        f"- decision: `{summary['decision']}`",
        f"- source benchmark: `{source.get('benchmark_id')}`",
        f"- cases: `{source.get('cases_total')}`",
        f"- transport errors: `{source.get('transport_error_cases', 0)}`",
        "",
        "## Classification Counts",
        "",
    ]
    for key, count in sorted(summary["classification_counts"].items()):
        lines.append(f"- `{key}`: `{count}`")

    lines.extend(["", "## Reason Counts", ""])
    for key, count in sorted(summary["reason_counts"].items()):
        lines.append(f"- `{key}`: `{count}`")

    lines.extend(["", "## Top Missing Article Pairs", ""])
    if summary["top_missing_article_pairs"]:
        for item in summary["top_missing_article_pairs"][:12]:
            citation = item.get("citation_short_ar") or ""
            lines.append(f"- `{item['pair']}` x`{item['count']}` {citation}".rstrip())
    else:
        lines.append("- لا توجد")

    lines.extend(["", "## Blocking Findings", ""])
    if summary["blocking_findings"]:
        for item in summary["blocking_findings"][:20]:
            lines.append(
                f"- `{item['classification']}` / `{item['reason']}`: "
                f"`{item['question_id']}` `{item['domain']}`"
            )
    else:
        lines.append("- لا توجد")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = load_report(args.report)
    matrix = load_matrix(args.matrix)
    result = summarize(report, matrix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(result), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("decision", "classification_counts", "reason_counts")}, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


if __name__ == "__main__":
    main()
