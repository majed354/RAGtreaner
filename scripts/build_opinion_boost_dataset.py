"""Build an opinion-focused refinement dataset with light replay for retention.

The goal is to recover `legal_opinion` quality after the memo-focused v4 round,
while preserving the stronger memo/analysis behavior through compact replay.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


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


def detect_mode(example: dict[str, Any]) -> str:
    system = "\n".join(
        str(msg.get("content", ""))
        for msg in example.get("messages", [])
        if msg.get("role") == "system"
    )
    if "عنوان المذكرة" in system:
        return "legal_memo"
    if "التكييف الأولي للقضية" in system:
        return "legal_analysis"
    if "النظام المنطبق" in system:
        return "legal_opinion"
    return "unknown"


def assistant_text(example: dict[str, Any]) -> str:
    return next(
        (
            str(msg.get("content", ""))
            for msg in example.get("messages", [])
            if msg.get("role") == "assistant"
        ),
        "",
    )


def pick_replay(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    # Prefer concise but citation-bearing replay so retention costs stay small.
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            len(assistant_text(row)),
            -assistant_text(row).count("المادة"),
            assistant_text(row)[:120],
        ),
    )
    return sorted_rows[: max(0, min(limit, len(sorted_rows)))]


def build_manifest(
    *,
    output_dir: Path,
    base_dir: Path,
    opinion_repeat: int,
    replay_per_mode: int,
    splits: dict[str, list[dict[str, Any]]],
) -> None:
    mode_counts = Counter()
    for split_rows in splits.values():
        for row in split_rows:
            mode_counts[detect_mode(row)] += 1

    payload = {
        "source_base_dir": str(base_dir.resolve()),
        "strategy": {
            "opinion_repeat": opinion_repeat,
            "replay_per_mode_train": replay_per_mode,
            "valid_test": "opinion_only",
        },
        "splits": {split: len(rows) for split, rows in splits.items()},
        "examples_total": sum(len(rows) for rows in splits.values()),
        "modes_total": dict(mode_counts),
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--opinion-repeat", type=int, default=3)
    parser.add_argument("--replay-per-mode", type=int, default=32)
    args = parser.parse_args()

    train_rows = load_jsonl(args.base_dir / "train.jsonl")
    valid_rows = load_jsonl(args.base_dir / "valid.jsonl")
    test_rows = load_jsonl(args.base_dir / "test.jsonl")

    train_by_mode = Counter()
    opinion_train: list[dict[str, Any]] = []
    memo_train: list[dict[str, Any]] = []
    analysis_train: list[dict[str, Any]] = []
    for row in train_rows:
        mode = detect_mode(row)
        train_by_mode[mode] += 1
        if mode == "legal_opinion":
            opinion_train.append(row)
        elif mode == "legal_memo":
            memo_train.append(row)
        elif mode == "legal_analysis":
            analysis_train.append(row)

    opinion_valid = [row for row in valid_rows if detect_mode(row) == "legal_opinion"]
    opinion_test = [row for row in test_rows if detect_mode(row) == "legal_opinion"]

    replay_train = pick_replay(memo_train, args.replay_per_mode) + pick_replay(
        analysis_train, args.replay_per_mode
    )
    opinion_boost_train = (opinion_train * max(1, args.opinion_repeat)) + replay_train

    splits = {
        "train": opinion_boost_train,
        "valid": opinion_valid,
        "test": opinion_test,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    build_manifest(
        output_dir=args.output_dir,
        base_dir=args.base_dir,
        opinion_repeat=args.opinion_repeat,
        replay_per_mode=args.replay_per_mode,
        splits=splits,
    )

    print(
        json.dumps(
            {
                "base_train_by_mode": dict(train_by_mode),
                "opinion_train": len(opinion_train),
                "replay_train": len(replay_train),
                "train_final": len(opinion_boost_train),
                "valid_final": len(opinion_valid),
                "test_final": len(opinion_test),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
