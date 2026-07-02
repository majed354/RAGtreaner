#!/usr/bin/env python3
"""Build a small blind slice for answer-level grounding checks.

The input is an article-precision report that already proved collection.  This
builder selects passed cases with relatively fragile context positions so the
answer gate tests whether the final answer cites the right regulation/article
pairs, not merely whether retrieval can find them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = PROJECT_ROOT / "data" / "eval" / "manual_article_precision_blind40_20260630_after_hint_filter.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "manual_answer_grounding_blind12_20260630.jsonl"


def pair_map(pair_keys: list[str]) -> dict[str, list[int]]:
    grouped: dict[str, set[int]] = {}
    for value in pair_keys or []:
        if ":" not in str(value):
            continue
        slug, raw_article = str(value).rsplit(":", 1)
        try:
            article = int(raw_article)
        except Exception:
            continue
        grouped.setdefault(slug, set()).add(article)
    return {slug: sorted(values) for slug, values in sorted(grouped.items())}


def clean_case(row: dict[str, Any]) -> dict[str, Any]:
    expected_pairs = row.get("expected_article_pairs") or []
    return {
        "question_id": row.get("question_id"),
        "split": "answer_grounding_blind",
        "domain": row.get("domain"),
        "question": row.get("question"),
        "expected_articles_by_slug": pair_map(expected_pairs),
        "expected_core_regulations": row.get("expected_core_regulations") or [],
        "expected_companion_regulations": row.get("expected_companion_regulations") or [],
        "expected_implementing_regulations": row.get("expected_implementing_regulations") or [],
        "axis_article_pairs": row.get("axis_coverage")
        and {
            axis: details.get("expected_article_pairs") or []
            for axis, details in (row.get("axis_coverage") or {}).items()
        }
        or {"expected_material": expected_pairs},
        "min_grounded_article_recall": 1.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--max-pairs", type=int, default=8)
    args = parser.parse_args()

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    rows = [
        row
        for row in payload.get("rows") or []
        if row.get("passed")
        and not row.get("transport_error")
        and 0 < len(row.get("expected_article_pairs") or []) <= args.max_pairs
    ]
    rows.sort(
        key=lambda row: (
            float(row.get("expected_article_entered_context_rate") or 1.0),
            -(float(row.get("expected_article_mean_context_position") or 0.0)),
            str(row.get("domain") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    used_domains: set[str] = set()
    for row in rows:
        domain = str(row.get("domain") or "")
        if domain in used_domains:
            continue
        selected.append(clean_case(row))
        used_domains.add(domain)
        if len(selected) >= args.count:
            break

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "output": str(args.output),
        "source_report": str(args.report),
        "count": len(selected),
        "domains": [row.get("domain") for row in selected],
        "expected_pair_count": sum(
            len(articles)
            for row in selected
            for articles in (row.get("expected_articles_by_slug") or {}).values()
        ),
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
