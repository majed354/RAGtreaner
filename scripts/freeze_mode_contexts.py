"""Freeze retrieval contexts for a benchmark without generating answers."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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


async def freeze_contexts(benchmark_path: Path, output_path: Path, per_case_timeout: float) -> None:
    from app.rag.engine import get_engine

    engine = get_engine()
    benchmark_cases = load_jsonl(benchmark_path)
    output_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for case in benchmark_cases:
        benchmark_id = case.get("benchmark_id", "")
        question = case.get("question", "")
        try:
            bundle = await asyncio.wait_for(engine._prepare_query_bundle(question), timeout=per_case_timeout)
            quality_report = bundle.get("quality_report", {}) if bundle.get("prebuilt_result") is None else (
                (bundle.get("prebuilt_result").diagnostics or {}) if bundle.get("prebuilt_result") else {}
            )
            output_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": case.get("mode", ""),
                    "question": question,
                    "prebuilt_result": bundle.get("prebuilt_result") is not None,
                    "context": bundle.get("context", ""),
                    "source_catalog": bundle.get("source_catalog", []),
                    "similarity_scores": bundle.get("similarity_scores", []),
                    "quality_report": quality_report,
                    "status": "completed",
                }
            )
            status_counts["completed"] += 1
        except Exception as exc:
            output_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": case.get("mode", ""),
                    "question": question,
                    "context": "",
                    "source_catalog": [],
                    "similarity_scores": [],
                    "quality_report": {},
                    "status": "failed",
                    "error": str(exc),
                }
            )
            status_counts["failed"] += 1

    write_jsonl(output_path, output_rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(benchmark_path),
        "output": str(output_path),
        "rows": len(output_rows),
        "status_counts": dict(status_counts),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-case-timeout", type=float, default=180.0)
    args = parser.parse_args()

    asyncio.run(freeze_contexts(args.benchmark, args.output, args.per_case_timeout))


if __name__ == "__main__":
    main()
