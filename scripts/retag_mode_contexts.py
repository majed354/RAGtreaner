"""Reuse frozen contexts across legal benchmark modes that share the same questions."""

from __future__ import annotations

import argparse
import json
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def benchmark_suffix(benchmark_id: str) -> str:
    return str(benchmark_id).split("::", 1)[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-contexts", type=Path, required=True)
    parser.add_argument("--target-benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_rows = load_jsonl(args.source_contexts)
    target_cases = load_jsonl(args.target_benchmark)

    source_by_suffix = {benchmark_suffix(row.get("benchmark_id", "")): row for row in source_rows}
    output_rows: list[dict[str, Any]] = []
    missing_suffixes: list[str] = []

    for case in target_cases:
        suffix = case.get("question_id") or benchmark_suffix(case.get("benchmark_id", ""))
        source_row = source_by_suffix.get(str(suffix))
        if not source_row:
            missing_suffixes.append(str(suffix))
            continue
        output_rows.append(
            {
                **source_row,
                "benchmark_id": case.get("benchmark_id", source_row.get("benchmark_id")),
                "mode": case.get("mode", source_row.get("mode")),
                "question": case.get("question", source_row.get("question", "")),
            }
        )

    if missing_suffixes:
        raise SystemExit(f"Missing source contexts for: {', '.join(missing_suffixes)}")

    write_jsonl(args.output, output_rows)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(output_rows),
                "target_mode": output_rows[0].get("mode") if output_rows else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
