"""Build a v10 specialist dataset from the curated unique v9 master pool.

This builder intentionally follows the spirit of v4/v5:
- keep the target mode heavy in train
- keep replay from the other two modes small and train-only
- keep valid/test focused on the target mode so the eval signal stays clean

It reuses the v9 master dataset as the quality-filtered source of truth and
ignores train-time oversampled rows from that dataset.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT / "data" / "training" / "final_legal_modes_v9_master"

SOURCE_PRIORITY = {
    "seed_v1": 3,
    "section_repair_v8": 2,
    "structured_v9": 1,
}

OTHER_MODES = {
    "legal_opinion": ("legal_memo", "legal_analysis"),
    "legal_memo": ("legal_opinion", "legal_analysis"),
    "legal_analysis": ("legal_opinion", "legal_memo"),
}


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


def load_examples(input_dir: Path) -> dict[str, list[Example]]:
    split_examples: dict[str, list[Example]] = {}
    seen_signatures: set[str] = set()

    for split in ("train", "valid", "test"):
        manifest = json.loads((input_dir / f"{split}.manifest.json").read_text(encoding="utf-8"))
        rows = load_jsonl(input_dir / f"{split}.jsonl")
        manifest_rows = manifest.get("rows", [])
        if len(rows) != len(manifest_rows):
            raise ValueError(f"Row/manifest length mismatch in split {split}: {len(rows)} != {len(manifest_rows)}")

        selected: list[Example] = []
        for meta, row in zip(manifest_rows, rows):
            if split == "train" and meta.get("oversampled"):
                continue
            signature = str(meta["signature"])
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            selected.append(
                Example(
                    split=split,
                    mode=str(meta["mode"]),
                    signature=signature,
                    row=row,
                    source_label=str(meta.get("source_label", "")),
                    source_split=str(meta.get("source_split", "")),
                    quality_score=float(meta.get("quality_score", 0.0)),
                    unique_article_refs=int(meta.get("unique_article_refs", 0)),
                    section_coverage=float(meta.get("section_coverage", 0.0)),
                )
            )
        split_examples[split] = selected
    return split_examples


def quality_sort_key(example: Example) -> tuple[float, float, int, str]:
    return (
        float(SOURCE_PRIORITY.get(example.source_label, 0)),
        example.quality_score,
        example.unique_article_refs,
        example.signature,
    )


def choose_top(rows: list[Example], limit: int) -> list[Example]:
    ranked = sorted(rows, key=quality_sort_key, reverse=True)
    return ranked[: max(0, min(limit, len(ranked)))]


def manifest_entry(example: Example, *, oversampled: bool = False, oversample_index: int | None = None) -> dict[str, Any]:
    payload = {
        "signature": example.signature,
        "mode": example.mode,
        "source_label": example.source_label,
        "source_split": example.source_split,
        "quality_score": example.quality_score,
        "unique_article_refs": example.unique_article_refs,
        "section_coverage": example.section_coverage,
    }
    if oversampled:
        payload["oversampled"] = True
    if oversample_index is not None:
        payload["oversample_index"] = oversample_index
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--target-mode",
        choices=("legal_opinion", "legal_memo", "legal_analysis"),
        required=True,
    )
    parser.add_argument("--replay-per-other-mode", type=int, default=64)
    parser.add_argument("--target-train-total", type=int, default=896)
    args = parser.parse_args()

    examples = load_examples(args.input_dir)
    other_modes = OTHER_MODES[args.target_mode]

    target_train = sorted(
        [example for example in examples["train"] if example.mode == args.target_mode],
        key=lambda item: item.signature,
    )
    replay_selected: list[Example] = []
    replay_counts = Counter()
    for mode in other_modes:
        chosen = choose_top(
            [example for example in examples["train"] if example.mode == mode],
            args.replay_per_other_mode,
        )
        replay_selected.extend(chosen)
        replay_counts[mode] = len(chosen)

    base_train_total = len(target_train) + len(replay_selected)
    target_train_total = max(args.target_train_total, base_train_total)

    oversample_pool = sorted(target_train, key=quality_sort_key, reverse=True)
    oversample_needed = max(0, target_train_total - base_train_total)

    train_rows = [example.row for example in target_train] + [example.row for example in replay_selected]
    train_manifest_rows = [manifest_entry(example) for example in target_train] + [
        manifest_entry(example) for example in replay_selected
    ]

    if oversample_needed > 0 and not oversample_pool:
        raise ValueError(f"No target-mode training rows found for {args.target_mode}")

    for index in range(oversample_needed):
        example = oversample_pool[index % len(oversample_pool)]
        train_rows.append(example.row)
        train_manifest_rows.append(
            manifest_entry(example, oversampled=True, oversample_index=index + 1)
        )

    valid_examples = sorted(
        [example for example in examples["valid"] if example.mode == args.target_mode],
        key=lambda item: item.signature,
    )
    test_examples = sorted(
        [example for example in examples["test"] if example.mode == args.target_mode],
        key=lambda item: item.signature,
    )

    split_rows = {
        "train": train_rows,
        "valid": [example.row for example in valid_examples],
        "test": [example.row for example in test_examples],
    }
    split_manifests = {
        "train": train_manifest_rows,
        "valid": [manifest_entry(example) for example in valid_examples],
        "test": [manifest_entry(example) for example in test_examples],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "valid", "test"):
        write_jsonl(args.output_dir / f"{split}.jsonl", split_rows[split])
        (args.output_dir / f"{split}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(split_rows[split]),
                    "modes": dict(Counter(entry["mode"] for entry in split_manifests[split])),
                    "rows": split_manifests[split],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    mode_counts = Counter()
    for split in ("train", "valid", "test"):
        for entry in split_manifests[split]:
            mode_counts[entry["mode"]] += 1

    manifest = {
        "dataset_version": f"v10_{args.target_mode}_specialist",
        "source_dataset": str(args.input_dir.resolve()),
        "strategy": {
            "recipe": "v4_style_specialist_from_v9_master",
            "target_mode": args.target_mode,
            "target_valid_test_only": True,
            "replay_per_other_mode_train": args.replay_per_other_mode,
            "requested_target_train_total": args.target_train_total,
            "effective_target_train_total": target_train_total,
        },
        "splits": {split: len(rows) for split, rows in split_rows.items()},
        "examples_total": sum(len(rows) for rows in split_rows.values()),
        "modes_total": dict(mode_counts),
        "unique_target_train_examples": len(target_train),
        "replay_counts_train": dict(replay_counts),
        "oversample_added_train": oversample_needed,
        "effective_train_target_ratio": round(
            (len(target_train) + oversample_needed) / max(len(train_rows), 1),
            6,
        ),
        "selection_rules": {
            "source": "v9 master unique pool only",
            "ignore_v9_train_oversamples": True,
            "train": "target full unique train + compact cross-mode replay + target-only oversample",
            "valid_test": "target mode only",
            "replay_selection": "top quality rows by source priority + quality score + article refs",
        },
    }
    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
