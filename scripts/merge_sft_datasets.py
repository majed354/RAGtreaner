"""Merge multiple SFT chat datasets into one split-preserving corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def parse_sources(raw_sources: list[str]) -> list[tuple[Path, int]]:
    parsed: list[tuple[Path, int]] = []
    for item in raw_sources:
        value = str(item).strip()
        if not value:
            continue
        if ":" in value:
            path_str, repeat_str = value.rsplit(":", 1)
            parsed.append((Path(path_str), max(1, int(repeat_str))))
        else:
            parsed.append((Path(value), 1))
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    merged = {"train": [], "valid": [], "test": []}
    manifest_sources: list[dict[str, Any]] = []
    for source_dir, repeat in parse_sources(args.sources):
        source_dir = source_dir.resolve()
        manifest_sources.append({"path": str(source_dir), "repeat": repeat})
        for split in ("train", "valid", "test"):
            rows = load_jsonl(source_dir / f"{split}.jsonl")
            if repeat > 1:
                rows = rows * repeat
            merged[split].extend(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in merged.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "sources": manifest_sources,
                "splits": {split: len(rows) for split, rows in merged.items()},
                "examples_total": sum(len(rows) for rows in merged.values()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "splits": {split: len(rows) for split, rows in merged.items()},
                "examples_total": sum(len(rows) for rows in merged.values()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
