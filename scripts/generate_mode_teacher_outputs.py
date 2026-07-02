"""Generate teacher outputs for legal modes using frozen contexts."""

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

DEFAULT_PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "prompt_templates"
DEFAULT_OUTPUT = ROOT / "data" / "training" / "legal_modes_seed_v1" / "teacher_outputs.json"
PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("benchmark_id", "")), str(row.get("mode", "")))


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_keys: list[tuple[str, str]] = []
    for row in rows:
        key = row_key(row)
        if key not in latest_by_key:
            ordered_keys.append(key)
        latest_by_key[key] = row
    return [latest_by_key[key] for key in ordered_keys]


def load_system_prompt(prompt_dir: Path, mode: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE.get(mode)
    if not prompt_name:
        raise ValueError(f"Unsupported mode: {mode}")
    return (prompt_dir / prompt_name).read_text(encoding="utf-8").strip()


def build_user_message(question: str, context: str) -> str:
    return f"القضية:\n{question}\n\nالنصوص المسترجعة:\n{context}"


def strip_confidence_suffix(answer: str) -> str:
    cleaned_lines = []
    for line in (answer or "").splitlines():
        if line.strip().startswith("CONFIDENCE:"):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "examples_total": len(rows),
        "completed": sum(1 for row in rows if row.get("status") == "completed"),
        "failed": sum(1 for row in rows if row.get("status") != "completed"),
        "mode_counts": dict(Counter(row.get("mode", "") for row in rows)),
        "status_counts": dict(Counter(row.get("status", "") for row in rows)),
    }


async def generate_teachers(
    *,
    contexts_path: Path,
    output_path: Path,
    prompt_dir: Path,
    reference_results_path: Path | None,
    provider: str | None,
    modes: list[str],
    per_case_timeout: float,
    max_tokens: int,
) -> None:
    from app.rag.engine import get_engine

    engine = get_engine()
    runtime_store = engine._runtime_store
    generation_runtime = (
        runtime_store.get_generation_for_provider(provider)
        if provider
        else runtime_store.get_active_generation()
    )

    contexts = load_jsonl(contexts_path)
    reused_opinion_rows: dict[str, dict[str, Any]] = {}
    if reference_results_path and reference_results_path.exists():
        for row in load_json(reference_results_path).get("rows", []):
            if row.get("status") == "completed":
                reused_opinion_rows[row.get("benchmark_id", "")] = row

    existing_rows = []
    if output_path.exists():
        existing_rows = dedupe_rows(load_json(output_path).get("rows", []))

    rows: list[dict[str, Any]] = list(existing_rows)
    row_indexes = {row_key(row): idx for idx, row in enumerate(rows)}

    def upsert_row(row: dict[str, Any]) -> None:
        key = row_key(row)
        existing_idx = row_indexes.get(key)
        if existing_idx is None:
            row_indexes[key] = len(rows)
            rows.append(row)
            return
        rows[existing_idx] = row

    for item in contexts:
        benchmark_id = item.get("benchmark_id", "")
        question = item.get("question", "")
        context = item.get("context", "")
        for mode in modes:
            existing_row = rows[row_indexes[(benchmark_id, mode)]] if (benchmark_id, mode) in row_indexes else None
            if existing_row and existing_row.get("status") == "completed":
                continue

            if mode == "legal_opinion" and benchmark_id in reused_opinion_rows:
                opinion_row = reused_opinion_rows[benchmark_id]
                upsert_row(
                    {
                        "benchmark_id": benchmark_id,
                        "mode": mode,
                        "question": question,
                        "status": "completed",
                        "provider": opinion_row.get("provider", "reference_reuse"),
                        "teacher_kind": "reference_reuse",
                        "answer": opinion_row.get("answer", ""),
                    }
                )
                dump_json(
                    output_path,
                    {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "contexts_path": str(contexts_path),
                        "provider": provider or "active_runtime",
                        "generation_status": generation_runtime,
                        "summary": summarize_rows(rows),
                        "rows": rows,
                    },
                )
                continue

            system_prompt = load_system_prompt(prompt_dir, mode)
            user_message = build_user_message(question, context)
            try:
                raw_answer = await asyncio.wait_for(
                    engine._generate_text_with_provider(
                        generation_runtime,
                        system_prompt=system_prompt,
                        user_message=user_message,
                        temperature=0.0,
                        max_tokens=max_tokens,
                    ),
                    timeout=per_case_timeout,
                )
                answer = strip_confidence_suffix(raw_answer)
                status = "completed" if answer else "empty"
                error_message = None if answer else "التوليد لم يرجع نصًا صالحًا."
            except Exception as exc:
                answer = f"تعذر توليد الجواب لهذه الحالة: {exc}"
                status = "failed"
                error_message = str(exc)

            row = {
                "benchmark_id": benchmark_id,
                "mode": mode,
                "question": question,
                "status": status,
                "provider": provider or generation_runtime.get("provider", "active_runtime"),
                "teacher_kind": "generated",
                "answer": answer,
            }
            if error_message:
                row["error"] = error_message
            upsert_row(row)
            dump_json(
                output_path,
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "contexts_path": str(contexts_path),
                    "provider": provider or "active_runtime",
                    "generation_status": generation_runtime,
                    "summary": summarize_rows(rows),
                    "rows": rows,
                },
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--reference-results", type=Path, default=None)
    parser.add_argument("--provider", type=str, default=None)
    parser.add_argument("--modes", nargs="+", default=["legal_opinion", "legal_memo", "legal_analysis"])
    parser.add_argument("--per-case-timeout", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=1600)
    args = parser.parse_args()

    asyncio.run(
        generate_teachers(
            contexts_path=args.contexts,
            output_path=args.output,
            prompt_dir=args.prompt_dir,
            reference_results_path=args.reference_results,
            provider=args.provider,
            modes=args.modes,
            per_case_timeout=args.per_case_timeout,
            max_tokens=args.max_tokens,
        )
    )


if __name__ == "__main__":
    main()
