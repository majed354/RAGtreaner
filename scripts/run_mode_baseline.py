"""تشغيل baseline معزول على benchmark المسارات القانونية."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODE_TO_ENGINE_ANSWER_MODE = {
    "legal_opinion": "consultation",
    "legal_memo": "legal_memo",
}


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            cases.append(json.loads(raw))
    return cases


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("status") == "completed"]
    skipped = [row for row in rows if row.get("status") != "completed"]
    mode_counts = Counter(row.get("mode", "") for row in rows)
    status_counts = Counter(row.get("status", "") for row in rows)
    confidence_counts = Counter(row.get("confidence", "") for row in completed if row.get("confidence"))

    by_mode: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("mode", "unknown")].append(row)

    for mode, items in grouped.items():
        mode_completed = [item for item in items if item.get("status") == "completed"]
        by_mode[mode] = {
            "cases": len(items),
            "completed": len(mode_completed),
            "skipped": len(items) - len(mode_completed),
            "average_displayed_source_count": round(
                mean(item.get("displayed_source_count", 0) for item in mode_completed),
                3,
            ) if mode_completed else None,
            "average_retrieved_source_count": round(
                mean(item.get("retrieved_source_count", 0) for item in mode_completed),
                3,
            ) if mode_completed else None,
        }

    return {
        "cases_total": len(rows),
        "cases_completed": len(completed),
        "cases_skipped": len(skipped),
        "mode_counts": dict(mode_counts),
        "status_counts": dict(status_counts),
        "confidence_counts": dict(confidence_counts),
        "by_mode": by_mode,
    }


async def run_baseline(
    benchmark_path: Path,
    output_json: Path,
    contexts_jsonl: Path | None,
    *,
    provider: str | None,
    per_case_timeout: float,
) -> None:
    from app.rag.engine import get_engine

    engine = get_engine()
    benchmark_cases = load_cases(benchmark_path)
    result_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    generation_status = engine.get_generation_status()

    for case in benchmark_cases:
        benchmark_id = case.get("benchmark_id", "")
        mode = case.get("mode", "")
        engine_answer_mode = MODE_TO_ENGINE_ANSWER_MODE.get(mode)

        if not engine_answer_mode:
            result_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": mode,
                    "status": "unsupported_mode",
                    "question": case.get("question", ""),
                    "confidence": "",
                    "needs_escalation": None,
                    "retrieved_source_count": 0,
                    "displayed_source_count": 0,
                    "provider": provider or "active_runtime",
                    "notes": "هذا المسار غير مدعوم حاليًا داخل محرك التطبيق.",
                }
            )
            continue

        try:
            bundle = await asyncio.wait_for(
                engine._prepare_query_bundle(case["question"]),
                timeout=per_case_timeout,
            )
        except Exception as exc:
            result_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": mode,
                    "status": "bundle_error",
                    "question": case.get("question", ""),
                    "confidence": "low",
                    "needs_escalation": True,
                    "retrieved_source_count": 0,
                    "displayed_source_count": 0,
                    "provider": provider or "active_runtime",
                    "notes": f"تعذر تجهيز retrieval bundle: {exc}",
                }
            )
            continue

        context_rows.append(
            {
                "benchmark_id": benchmark_id,
                "mode": mode,
                "question": case.get("question", ""),
                "prebuilt_result": bundle.get("prebuilt_result") is not None,
                "context": bundle.get("context", ""),
                "source_catalog": bundle.get("source_catalog", []),
                "similarity_scores": bundle.get("similarity_scores", []),
                "quality_report": bundle.get("quality_report", {}) if bundle.get("prebuilt_result") is None else (
                    (bundle.get("prebuilt_result").diagnostics or {}) if bundle.get("prebuilt_result") else {}
                ),
            }
        )

        try:
            if provider:
                result = await asyncio.wait_for(
                    engine.query_with_provider(
                        case["question"],
                        provider=provider,
                        answer_mode=engine_answer_mode,
                    ),
                    timeout=per_case_timeout,
                )
            else:
                result = await asyncio.wait_for(
                    engine.query(
                        case["question"],
                        answer_mode=engine_answer_mode,
                    ),
                    timeout=per_case_timeout,
                )

            diagnostics = result.diagnostics or {}
            result_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": mode,
                    "status": "completed",
                    "question": case.get("question", ""),
                    "answer": result.answer,
                    "confidence": result.confidence,
                    "needs_escalation": result.needs_escalation,
                    "retrieved_source_count": diagnostics.get("retrieved_source_count", 0),
                    "displayed_source_count": diagnostics.get("displayed_source_count", len(result.sources)),
                    "displayed_sources": result.sources,
                    "similarity_scores": result.similarity_scores,
                    "diagnostics": diagnostics,
                    "provider": provider or "active_runtime",
                }
            )
        except asyncio.TimeoutError:
            result_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": mode,
                    "status": "timeout",
                    "question": case.get("question", ""),
                    "confidence": "low",
                    "needs_escalation": True,
                    "retrieved_source_count": 0,
                    "displayed_source_count": 0,
                    "provider": provider or "active_runtime",
                    "notes": f"تجاوز المهلة ({per_case_timeout} ثانية).",
                }
            )
        except Exception as exc:
            result_rows.append(
                {
                    "benchmark_id": benchmark_id,
                    "mode": mode,
                    "status": "run_error",
                    "question": case.get("question", ""),
                    "confidence": "low",
                    "needs_escalation": True,
                    "retrieved_source_count": 0,
                    "displayed_source_count": 0,
                    "provider": provider or "active_runtime",
                    "notes": str(exc),
                }
            )

    summary = summarize_rows(result_rows)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(benchmark_path),
        "provider": provider or "active_runtime",
        "generation_status": generation_status,
        "summary": summary,
        "rows": result_rows,
    }
    dump_json(output_json, payload)
    if contexts_jsonl:
        dump_jsonl(contexts_jsonl, context_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contexts-output", type=Path, default=None)
    parser.add_argument("--provider", type=str, default=None)
    parser.add_argument("--per-case-timeout", type=float, default=180.0)
    args = parser.parse_args()

    benchmark_path = args.benchmark if args.benchmark.is_absolute() else (ROOT / args.benchmark).resolve()
    output_json = args.output if args.output.is_absolute() else (ROOT / args.output).resolve()
    contexts_jsonl = None
    if args.contexts_output:
        contexts_jsonl = args.contexts_output if args.contexts_output.is_absolute() else (ROOT / args.contexts_output).resolve()

    asyncio.run(
        run_baseline(
            benchmark_path,
            output_json,
            contexts_jsonl,
            provider=args.provider,
            per_case_timeout=args.per_case_timeout,
        )
    )


if __name__ == "__main__":
    main()
