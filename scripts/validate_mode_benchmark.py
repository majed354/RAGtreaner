"""فحص بسيط لسلامة benchmark المسارات القانونية."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


REQUIRED_FIELDS = {
    "benchmark_id",
    "suite_version",
    "mode",
    "question",
    "expected_behavior",
    "expected_regulations",
    "expected_articles",
    "question_type",
    "notes",
}
VALID_MODES = {"legal_opinion", "legal_memo", "legal_analysis"}
VALID_BEHAVIORS = {"answer", "refuse"}


def validate_row(path: Path, line_number: int, row: dict) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(row))
    if missing:
        errors.append(f"{path.name}:{line_number} missing fields: {', '.join(missing)}")
    mode = row.get("mode")
    if mode not in VALID_MODES:
        errors.append(f"{path.name}:{line_number} invalid mode: {mode}")
    behavior = row.get("expected_behavior")
    if behavior not in VALID_BEHAVIORS:
        errors.append(f"{path.name}:{line_number} invalid expected_behavior: {behavior}")
    if row.get("suite_version") != "legal_modes_v1":
        errors.append(f"{path.name}:{line_number} invalid suite_version: {row.get('suite_version')}")
    if not isinstance(row.get("expected_regulations", []), list):
        errors.append(f"{path.name}:{line_number} expected_regulations must be a list")
    if not isinstance(row.get("expected_articles", []), list):
        errors.append(f"{path.name}:{line_number} expected_articles must be a list")
    return errors


def validate_jsonl(path: Path) -> tuple[list[str], Counter]:
    errors: list[str] = []
    counts: Counter = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            errors.extend(validate_row(path, line_number, row))
            counts[row.get("mode", "unknown")] += 1
    return errors, counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    all_errors: list[str] = []
    total_counts: Counter = Counter()
    for path in args.paths:
        errors, counts = validate_jsonl(path)
        all_errors.extend(errors)
        total_counts.update(counts)

    if all_errors:
        for error in all_errors:
            print(error)
        raise SystemExit(1)

    print("Benchmark validation passed.")
    print(json.dumps({"counts": dict(total_counts)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
