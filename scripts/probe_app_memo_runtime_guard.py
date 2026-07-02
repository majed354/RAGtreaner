"""Probe the real app-side memo runtime guard via app.mlx_local_service."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mlx_local_service import (
    build_mlx_local_user_prompt,
    generate_with_mlx_local,
    resolve_mlx_local_prompt,
)
from app.runtime_settings import get_runtime_settings_store


DEFAULT_CONTEXTS = PROJECT_ROOT / "data" / "benchmarks" / "legal_modes_v1" / "results" / "current_reference" / "legal_memo_frozen.contexts.jsonl"


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contexts", type=Path, default=DEFAULT_CONTEXTS)
    parser.add_argument("--benchmark-id", action="append", required=True)
    args = parser.parse_args()

    rows = load_rows(args.contexts)
    lookup = {row["benchmark_id"]: row for row in rows}
    runtime = get_runtime_settings_store().get_generation_for_provider("mlx_local")
    prompt = resolve_mlx_local_prompt("legal_memo")

    results: list[dict] = []
    for benchmark_id in args.benchmark_id:
        row = lookup[benchmark_id]
        answer = await generate_with_mlx_local(
            runtime,
            system_prompt=prompt,
            user_message=build_mlx_local_user_prompt(row["question"], row["context"]),
            answer_mode="legal_memo",
        )
        results.append(
            {
                "benchmark_id": benchmark_id,
                "char_count": len(answer),
                "answer_tail": answer[-700:],
            }
        )

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
