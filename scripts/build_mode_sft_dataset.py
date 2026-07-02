"""Build MLX-ready SFT chat datasets from teacher legal mode outputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "prompt_templates"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "training" / "legal_modes_seed_v1" / "sft_messages"

PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_system_prompt(prompt_dir: Path, mode: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE.get(mode)
    if not prompt_name:
        raise ValueError(f"Unsupported mode: {mode}")
    return (prompt_dir / prompt_name).read_text(encoding="utf-8").strip()


def build_user_message(question: str, context: str) -> str:
    return f"القضية:\n{question}\n\nالنصوص المسترجعة:\n{context}"


def choose_split(case_id: str, ordered_case_ids: list[str]) -> str:
    total = len(ordered_case_ids)
    valid_count = max(2, round(total * 0.1))
    test_count = max(2, round(total * 0.1))
    train_end = max(0, total - valid_count - test_count)
    valid_end = train_end + valid_count
    if case_id in ordered_case_ids[:train_end]:
        return "train"
    if case_id in ordered_case_ids[train_end:valid_end]:
        return "valid"
    return "test"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--teachers", type=Path, required=True)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    contexts = {
        row["benchmark_id"]: row
        for row in (
            json.loads(line)
            for line in args.contexts.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    teacher_rows = load_json(args.teachers).get("rows", [])

    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    for row in teacher_rows:
        if row.get("status") != "completed":
            continue
        grouped_rows.setdefault(row.get("benchmark_id", ""), []).append(row)

    complete_case_ids = sorted(
        case_id
        for case_id, rows in grouped_rows.items()
        if case_id in contexts and {row.get("mode") for row in rows} == {"legal_opinion", "legal_memo", "legal_analysis"}
    )

    split_payloads = {"train": [], "valid": [], "test": []}
    split_manifests = {"train": [], "valid": [], "test": []}
    for case_id in complete_case_ids:
        split = choose_split(case_id, complete_case_ids)
        context_row = contexts[case_id]
        question = context_row.get("question", "")
        context = context_row.get("context", "")
        for row in sorted(grouped_rows[case_id], key=lambda item: item.get("mode", "")):
            mode = row.get("mode", "")
            payload = {
                "messages": [
                    {"role": "system", "content": load_system_prompt(args.prompt_dir, mode)},
                    {"role": "user", "content": build_user_message(question, context)},
                    {"role": "assistant", "content": row.get("answer", "").strip()},
                ]
            }
            split_payloads[split].append(payload)
            split_manifests[split].append(
                {
                    "benchmark_id": case_id,
                    "mode": mode,
                    "question": question,
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split_name, rows in split_payloads.items():
        write_jsonl(args.output_dir / f"{split_name}.jsonl", rows)
        (args.output_dir / f"{split_name}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(rows),
                    "modes": dict(Counter(item["mode"] for item in split_manifests[split_name])),
                    "rows": split_manifests[split_name],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "cases_total": len(complete_case_ids),
                "examples_total": sum(len(rows) for rows in split_payloads.values()),
                "splits": {split: len(rows) for split, rows in split_payloads.items()},
                "modes_total": dict(
                    Counter(
                        item["mode"]
                        for manifest in split_manifests.values()
                        for item in manifest
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "cases_total": len(complete_case_ids),
                "examples_total": sum(len(rows) for rows in split_payloads.values()),
                "splits": {split: len(rows) for split, rows in split_payloads.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
