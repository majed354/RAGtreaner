"""Build a small deduplicated smoke subset from an SFT dataset directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def detect_mode(row: dict[str, Any]) -> str:
    messages = row.get("messages", [])
    combined = "\n".join(str(msg.get("content", "")) for msg in messages if msg.get("role") in {"system", "assistant"})
    if "عنوان المذكرة" in combined and "الخلاصة والتوصية العملية" in combined:
        return "legal_memo"
    if "التكييف الأولي للقضية" in combined and "التقدير الأولي" in combined:
        return "legal_analysis"
    if "النظام المنطبق" in combined and "الخلاصة العملية" in combined:
        return "legal_opinion"
    return "unknown"


def row_signature(row: dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        signature = row_signature(row)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(row)
    return output


def choose_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit >= len(rows):
        return list(rows)
    keyed = sorted(rows, key=row_signature)
    return keyed[:limit]


def build_manifest(split_rows: dict[str, list[dict[str, Any]]], target_total: int) -> dict[str, Any]:
    mode_counts = Counter()
    for rows in split_rows.values():
        for row in rows:
            mode_counts[detect_mode(row)] += 1
    return {
        "dataset_version": "smoke_subset",
        "target_total": target_total,
        "examples_total": sum(len(rows) for rows in split_rows.values()),
        "splits": {split: len(rows) for split, rows in split_rows.items()},
        "modes_total": dict(mode_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-total", type=int, default=250)
    args = parser.parse_args()

    input_rows = {split: load_jsonl(args.input_dir / f"{split}.jsonl") for split in ("train", "valid", "test")}
    deduped = {split: dedupe_rows(rows) for split, rows in input_rows.items()}

    target_total = max(30, args.target_total)
    train_limit = int(target_total * 0.8)
    valid_limit = int(target_total * 0.1)
    test_limit = target_total - train_limit - valid_limit

    split_rows = {
        "train": choose_rows(deduped["train"], train_limit),
        "valid": choose_rows(deduped["valid"], valid_limit),
        "test": choose_rows(deduped["test"], test_limit),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in split_rows.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    manifest = build_manifest(split_rows, target_total)
    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
