"""Summarize a manually filled internal live scorecard."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def score_row(row: dict[str, Any]) -> int | None:
    keys = [
        "mode_adherence",
        "grounding_fidelity",
        "insufficiency_honesty",
        "structure_quality",
        "practical_utility",
    ]
    values = [row.get(key) for key in keys]
    if any(value is None for value in values):
        return None
    return int(sum(int(value) for value in values))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if score_row(row) is not None]
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in completed:
        by_mode[str(row["answer_mode"])].append(row)

    mode_summary: dict[str, Any] = {}
    for mode, items in by_mode.items():
        scores = [score_row(item) for item in items]
        safe_scores = [score for score in scores if score is not None]
        mode_summary[mode] = {
            "cases_scored": len(items),
            "average_total_score": round(sum(safe_scores) / len(safe_scores), 2) if safe_scores else None,
            "acceptable_cases": sum(1 for item in items if item.get("acceptable") is True),
            "thought_leak_cases": sum(1 for item in items if item.get("thought_leak") is True),
            "fabricated_citation_cases": sum(1 for item in items if item.get("fabricated_citation") is True),
            "repetition_or_filler_cases": sum(1 for item in items if item.get("repetition_or_filler") is True),
        }

    overall_scores = [score_row(row) for row in completed]
    safe_overall_scores = [score for score in overall_scores if score is not None]
    return {
        "cases_total": len(rows),
        "cases_scored": len(completed),
        "cases_unscored": len(rows) - len(completed),
        "average_total_score": round(sum(safe_overall_scores) / len(safe_overall_scores), 2)
        if safe_overall_scores
        else None,
        "acceptable_cases": sum(1 for row in completed if row.get("acceptable") is True),
        "thought_leak_cases": sum(1 for row in completed if row.get("thought_leak") is True),
        "fabricated_citation_cases": sum(1 for row in completed if row.get("fabricated_citation") is True),
        "repetition_or_filler_cases": sum(1 for row in completed if row.get("repetition_or_filler") is True),
        "mode_counts": dict(Counter(str(row["answer_mode"]) for row in rows)),
        "by_mode": mode_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scorecard", type=Path, required=True)
    args = parser.parse_args()

    rows = load_jsonl(args.scorecard)
    payload = summarize(rows)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
