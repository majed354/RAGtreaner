"""Build a staged v11 base-clean dataset from the curated v9 unique pool.

The goal is to recreate the spirit of the successful early path:
raw -> small clean general foundation -> targeted boost

This builder intentionally avoids the broader v9 master recipe:
- use only the primary sources (`seed_v1` + `structured_v9`)
- ignore v9 train-time oversamples
- keep the dataset balanced across the three legal modes
- lightly replay seed train examples to preserve the strongest anchors
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT / "data" / "training" / "final_legal_modes_v9_master"

SEED_LABEL = "seed_v1"
STRUCTURED_LABEL = "structured_v9"
ALLOWED_SOURCES = {SEED_LABEL, STRUCTURED_LABEL}

TARGET_CHARS = {
    "legal_opinion": 1000,
    "legal_memo": 2350,
    "legal_analysis": 1250,
}

SOURCE_PRIORITY = {
    SEED_LABEL: 3,
    STRUCTURED_LABEL: 1,
}

SUSPICIOUS_PATTERNS = [
    "• • • • •",
    "Thinking Process",
    "<|channel>thought",
    "<|start_header_id|>thought",
    "وحده وحده وحده",
    "استثناء أو قيد | استثناء أو قيد",
    "حق أو اختصاص | حق أو اختصاص",
]


@dataclass
class Example:
    split: str
    mode: str
    signature: str
    row: dict[str, Any]
    source_label: str
    source_split: str
    quality_score: float
    unique_article_refs: int
    section_coverage: float
    assistant_chars: int


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


def assistant_text(row: dict[str, Any]) -> str:
    return "\n".join(
        str(message.get("content", ""))
        for message in row.get("messages", [])
        if message.get("role") == "assistant"
    ).strip()


def has_suspicious_pattern(text: str) -> bool:
    if any(pattern in text for pattern in SUSPICIOUS_PATTERNS):
        return True

    tokens = [token for token in re.split(r"\s+", text) if token]
    streak = 1
    for index in range(1, len(tokens)):
        if tokens[index] == tokens[index - 1]:
            streak += 1
            if streak >= 5:
                return True
        else:
            streak = 1
    return False


def manifest_entry(example: Example, *, seed_replayed: bool = False, replay_index: int | None = None) -> dict[str, Any]:
    payload = {
        "signature": example.signature,
        "mode": example.mode,
        "source_label": example.source_label,
        "source_split": example.source_split,
        "quality_score": example.quality_score,
        "unique_article_refs": example.unique_article_refs,
        "section_coverage": example.section_coverage,
        "assistant_chars": example.assistant_chars,
    }
    if seed_replayed:
        payload["seed_replayed"] = True
    if replay_index is not None:
        payload["replay_index"] = replay_index
    return payload


def quality_sort_key(example: Example) -> tuple[float, float, int, float, str]:
    target_chars = TARGET_CHARS.get(example.mode, 1500)
    length_closeness = -abs(example.assistant_chars - target_chars)
    return (
        float(SOURCE_PRIORITY.get(example.source_label, 0)),
        example.quality_score,
        example.unique_article_refs,
        length_closeness,
        example.signature,
    )


def load_examples(input_dir: Path) -> dict[str, list[Example]]:
    result: dict[str, list[Example]] = {}
    seen_signatures: set[str] = set()

    for split in ("train", "valid", "test"):
        manifest = json.loads((input_dir / f"{split}.manifest.json").read_text(encoding="utf-8"))
        rows = load_jsonl(input_dir / f"{split}.jsonl")
        manifest_rows = manifest.get("rows", [])
        if len(rows) != len(manifest_rows):
            raise ValueError(f"Row/manifest mismatch in {split}: {len(rows)} != {len(manifest_rows)}")

        selected: list[Example] = []
        for meta, row in zip(manifest_rows, rows):
            if split == "train" and meta.get("oversampled"):
                continue

            signature = str(meta.get("signature", ""))
            if not signature or signature in seen_signatures:
                continue

            source_label = str(meta.get("source_label", ""))
            if source_label not in ALLOWED_SOURCES:
                continue

            answer = assistant_text(row)
            if not answer or has_suspicious_pattern(answer):
                continue

            seen_signatures.add(signature)
            selected.append(
                Example(
                    split=split,
                    mode=str(meta["mode"]),
                    signature=signature,
                    row=row,
                    source_label=source_label,
                    source_split=str(meta.get("source_split", "")),
                    quality_score=float(meta.get("quality_score", 0.0)),
                    unique_article_refs=int(meta.get("unique_article_refs", 0)),
                    section_coverage=float(meta.get("section_coverage", 0.0)),
                    assistant_chars=len(answer),
                )
            )
        result[split] = selected
    return result


def choose_examples(examples: list[Example], target_count: int) -> tuple[list[Example], int]:
    if target_count <= 0:
        return [], 0

    seed_examples = sorted(
        [example for example in examples if example.source_label == SEED_LABEL],
        key=quality_sort_key,
        reverse=True,
    )
    structured_examples = sorted(
        [example for example in examples if example.source_label == STRUCTURED_LABEL],
        key=quality_sort_key,
        reverse=True,
    )

    chosen = list(seed_examples[:target_count])
    remaining = max(0, target_count - len(chosen))
    if remaining > 0:
        chosen.extend(structured_examples[:remaining])

    return chosen, min(len(seed_examples), target_count)


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for entry in entries:
        counts[entry["mode"]] += 1
    return dict(counts)


def summarize_sources(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for entry in entries:
        counts[entry["source_label"]] += 1
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-per-mode", type=int, default=300)
    parser.add_argument("--valid-per-mode", type=int, default=40)
    parser.add_argument("--test-per-mode", type=int, default=40)
    parser.add_argument("--seed-train-repeat", type=int, default=2)
    args = parser.parse_args()

    split_target_counts = {
        "train": args.train_per_mode,
        "valid": args.valid_per_mode,
        "test": args.test_per_mode,
    }
    mode_names = ("legal_opinion", "legal_memo", "legal_analysis")
    split_examples = load_examples(args.input_dir)

    split_rows: dict[str, list[dict[str, Any]]] = {}
    split_manifest_rows: dict[str, list[dict[str, Any]]] = {}
    selected_seed_counts = defaultdict(lambda: Counter())

    for split in ("train", "valid", "test"):
        rows: list[dict[str, Any]] = []
        manifest_rows: list[dict[str, Any]] = []

        for mode in mode_names:
            pool = [example for example in split_examples[split] if example.mode == mode]
            selected, selected_seed_count = choose_examples(pool, split_target_counts[split])
            if len(selected) < split_target_counts[split]:
                raise ValueError(
                    f"Not enough {mode} examples for split {split}: {len(selected)} < {split_target_counts[split]}"
                )

            selected = sorted(selected, key=quality_sort_key, reverse=True)
            rows.extend(example.row for example in selected)
            manifest_rows.extend(manifest_entry(example) for example in selected)
            selected_seed_counts[split][mode] = selected_seed_count

            if split == "train" and args.seed_train_repeat > 1:
                seed_selected = [example for example in selected if example.source_label == SEED_LABEL]
                for replay_index in range(1, args.seed_train_repeat):
                    rows.extend(example.row for example in seed_selected)
                    manifest_rows.extend(
                        manifest_entry(example, seed_replayed=True, replay_index=replay_index)
                        for example in seed_selected
                    )

        split_rows[split] = rows
        split_manifest_rows[split] = manifest_rows

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "valid", "test"):
        write_jsonl(args.output_dir / f"{split}.jsonl", split_rows[split])
        (args.output_dir / f"{split}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(split_rows[split]),
                    "modes": summarize_entries(split_manifest_rows[split]),
                    "sources": summarize_sources(split_manifest_rows[split]),
                    "rows": split_manifest_rows[split],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    mode_counts = Counter()
    source_counts = Counter()
    replayed_seed_count = 0
    for split in ("train", "valid", "test"):
        for entry in split_manifest_rows[split]:
            mode_counts[entry["mode"]] += 1
            source_counts[entry["source_label"]] += 1
            if entry.get("seed_replayed"):
                replayed_seed_count += 1

    manifest = {
        "dataset_version": "v11_base_clean",
        "source_dataset": str(args.input_dir.resolve()),
        "strategy": {
            "recipe": "raw_to_clean_general_foundation_before_targeted_boost",
            "allowed_sources": sorted(ALLOWED_SOURCES),
            "ignore_v9_train_oversamples": True,
            "train_per_mode_unique": args.train_per_mode,
            "valid_per_mode_unique": args.valid_per_mode,
            "test_per_mode_unique": args.test_per_mode,
            "seed_train_repeat": args.seed_train_repeat,
            "selection": "include available seed rows first, then fill with top structured_v9 rows by source priority + quality + article refs + target length closeness",
        },
        "splits": {split: len(rows) for split, rows in split_rows.items()},
        "examples_total": sum(len(rows) for rows in split_rows.values()),
        "modes_total": dict(mode_counts),
        "sources_total": dict(source_counts),
        "seed_selected_unique_by_split_mode": {
            split: dict(selected_seed_counts[split]) for split in ("train", "valid", "test")
        },
        "seed_replayed_train_examples": replayed_seed_count,
        "selection_rules": {
            "base_pool": "v9 master unique rows only",
            "sources": "seed_v1 + structured_v9 only",
            "supplements_excluded": ["section_repair_v8"],
            "suspicious_pattern_filter": True,
            "balanced_split_targets": True,
        },
    }
    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
