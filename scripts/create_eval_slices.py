from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a JSONL evaluation set into smaller slices.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--slice-size", type=int, default=10)
    parser.add_argument("--prefix", type=str, default="slice")
    args = parser.parse_args()

    rows = load_rows(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for old_file in args.output_dir.glob(f"{args.prefix}_*.jsonl"):
        old_file.unlink()

    total = len(rows)
    if total == 0:
        raise ValueError("Input file is empty.")

    shard_count = 0
    for start in range(0, total, args.slice_size):
        shard_count += 1
        chunk = rows[start : start + args.slice_size]
        shard_path = args.output_dir / f"{args.prefix}_{shard_count:02d}.jsonl"
        write_rows(shard_path, chunk)

    manifest = {
        "input": str(args.input),
        "output_dir": str(args.output_dir),
        "slice_size": args.slice_size,
        "total_rows": total,
        "slice_count": shard_count,
        "prefix": args.prefix,
    }
    (args.output_dir / f"{args.prefix}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
