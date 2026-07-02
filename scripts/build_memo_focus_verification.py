"""Build a memo-focused verification slice from scored benchmark results.

This script creates a smaller benchmark/context subset centered on the memo
sections that still fail most often after removing obvious evaluation artifacts
such as low generation budgets.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "legal_memo_cases.jsonl"
DEFAULT_CONTEXTS = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "current_reference"
    / "legal_memo_frozen.contexts.jsonl"
)
DEFAULT_SCORED = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "gemma4_e2b_legal_v3_resume_v1"
    / "legal_memo_mlx_adapter.scored.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "memo_focus_v1"
DEFAULT_TARGET_SECTIONS = [
    "الدفوع أو الاحتمالات المقابلة",
    "ما لم يثبته النص أو الوقائع",
    "الخلاصة والتوصية العملية",
]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sort_key(row: dict[str, Any], target_sections: list[str]) -> tuple[Any, ...]:
    missing_sections = row.get("missing_sections", [])
    target_missing = sum(1 for section in target_sections if section in missing_sections)
    return (
        -target_missing,
        row.get("answer_only_score", 0.0),
        row.get("section_coverage", 1.0),
        row.get("score", 1.0),
        row.get("benchmark_id", ""),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--contexts", type=Path, default=DEFAULT_CONTEXTS)
    parser.add_argument("--scored", type=Path, default=DEFAULT_SCORED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument(
        "--target-section",
        action="append",
        dest="target_sections",
        default=None,
        help="Section name to prioritize. Can be provided multiple times.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_sections = args.target_sections or DEFAULT_TARGET_SECTIONS

    benchmark_rows = load_jsonl(args.benchmark)
    benchmark_lookup = {row["benchmark_id"]: row for row in benchmark_rows}
    context_rows = load_jsonl(args.contexts)
    context_lookup = {row["benchmark_id"]: row for row in context_rows}
    scored_report = json.loads(args.scored.read_text(encoding="utf-8"))
    scored_rows = scored_report.get("rows", [])

    eligible_rows = [row for row in scored_rows if row.get("benchmark_id") in benchmark_lookup and row.get("benchmark_id") in context_lookup]
    prioritized_rows = sorted(eligible_rows, key=lambda row: sort_key(row, target_sections))

    chosen_rows: list[dict[str, Any]] = []
    for row in prioritized_rows:
        if len(chosen_rows) >= args.limit:
            break
        chosen_rows.append(row)

    chosen_ids = [row["benchmark_id"] for row in chosen_rows]
    chosen_benchmarks = [benchmark_lookup[row_id] for row_id in chosen_ids]
    chosen_contexts = [context_lookup[row_id] for row_id in chosen_ids]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_out = args.output_dir / "legal_memo_focus_cases.jsonl"
    contexts_out = args.output_dir / "legal_memo_focus.contexts.jsonl"
    manifest_out = args.output_dir / "manifest.json"

    write_jsonl(benchmark_out, chosen_benchmarks)
    write_jsonl(contexts_out, chosen_contexts)

    missing_counter = Counter()
    target_missing_counter = Counter()
    for row in chosen_rows:
        missing_counter.update(row.get("missing_sections", []))
        for section in target_sections:
            if section in row.get("missing_sections", []):
                target_missing_counter.update([section])

    manifest = {
        "source_scored_report": str(args.scored),
        "source_benchmark": str(args.benchmark),
        "source_contexts": str(args.contexts),
        "cases_total": len(chosen_rows),
        "target_sections": target_sections,
        "section_gap_counts": dict(missing_counter),
        "target_section_gap_counts": dict(target_missing_counter),
        "cases": [
            {
                "benchmark_id": row["benchmark_id"],
                "answer_only_score": row.get("answer_only_score"),
                "section_coverage": row.get("section_coverage"),
                "missing_sections": row.get("missing_sections", []),
            }
            for row in chosen_rows
        ],
    }
    manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "cases_total": len(chosen_rows),
                "benchmark_out": str(benchmark_out),
                "contexts_out": str(contexts_out),
                "target_section_gap_counts": dict(target_missing_counter),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
