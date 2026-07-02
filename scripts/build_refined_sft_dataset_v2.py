"""Build a refined v2 SFT dataset for small-model training."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from audit_sft_dataset_quality import analyze_example, load_jsonl


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def mode_of(example: dict[str, Any]) -> str:
    return analyze_example(example)["mode"]


def should_drop_train(example: dict[str, Any]) -> tuple[bool, list[str]]:
    audit = analyze_example(example)
    reasons: list[str] = []
    if audit["suspicious"]["teacher_waiting_for_context"] > 0:
        reasons.append("teacher_waiting_for_context")
    if audit["suspicious"]["thought_leak"] > 0:
        reasons.append("thought_leak")
    if audit["suspicious"]["filler_phrase"] > 0:
        reasons.append("filler_phrase")
    if audit["repeated_line_count"] > 0:
        reasons.append("repeated_lines")
    if audit["low_citation_density"]:
        reasons.append("low_citation_density")
    return (len(reasons) > 0), reasons


def build_manifest(
    *,
    output_dir: Path,
    splits: dict[str, list[dict[str, Any]]],
    sources: list[dict[str, Any]],
    dropped_train: list[dict[str, Any]],
) -> None:
    mode_counts = Counter()
    for split_rows in splits.values():
        for row in split_rows:
            mode_counts[mode_of(row)] += 1

    payload = {
        "sources": sources,
        "splits": {split: len(rows) for split, rows in splits.items()},
        "examples_total": sum(len(rows) for rows in splits.values()),
        "modes_total": dict(mode_counts),
        "dropped_from_train": {
            "count": len(dropped_train),
            "reasons": dict(Counter(reason for item in dropped_train for reason in item["reasons"])),
        },
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--teacher-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--memo-repeat", type=int, default=2)
    args = parser.parse_args()

    base_train = load_jsonl(args.base_dir / "train.jsonl")
    base_valid = load_jsonl(args.base_dir / "valid.jsonl")
    base_test = load_jsonl(args.base_dir / "test.jsonl")
    teacher_train = load_jsonl(args.teacher_dir / "train.jsonl")

    kept_train: list[dict[str, Any]] = []
    dropped_train: list[dict[str, Any]] = []
    for idx, row in enumerate(base_train):
        drop, reasons = should_drop_train(row)
        if drop:
            dropped_train.append({"index": idx, "mode": mode_of(row), "reasons": reasons})
            continue
        kept_train.append(row)

    clean_teacher_memos = [row for row in teacher_train if mode_of(row) == "legal_memo" and not should_drop_train(row)[0]]
    refined_train = kept_train + (clean_teacher_memos * max(1, args.memo_repeat))

    splits = {
        "train": refined_train,
        "valid": base_valid,
        "test": base_test,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    build_manifest(
        output_dir=args.output_dir,
        splits=splits,
        sources=[
            {"path": str(args.base_dir.resolve()), "type": "base_filtered"},
            {
                "path": str(args.teacher_dir.resolve()),
                "type": "teacher_memo_oversample",
                "memo_repeat": args.memo_repeat,
            },
        ],
        dropped_train=dropped_train,
    )
    print(
        json.dumps(
            {
                "train_before": len(base_train),
                "train_after_filter": len(kept_train),
                "teacher_memo_added": len(clean_teacher_memos) * max(1, args.memo_repeat),
                "train_final": len(refined_train),
                "dropped_train": len(dropped_train),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
