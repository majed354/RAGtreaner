"""Build a failure-cluster aware balanced dataset for v7 refinement.

v7 is intentionally broader than v6:
- keep the full refined multi-mode base set for retention
- add clean seed examples across all modes for higher-fit supervision
- add extra memo-focused examples that emphasize the late sections that keep
  dropping in benchmark failures

The goal is to improve memo robustness without repeating the narrow-regression
pattern seen in v6.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


MEMO_SECTIONS = [
    "عنوان المذكرة:",
    "السؤال محل الرأي:",
    "الجواب المختصر:",
    "الوقائع ذات الأثر القانوني:",
    "النظام أو النصوص المنطبقة:",
    "المسائل القانونية:",
    "التحليل:",
    "الدفوع أو الاحتمالات المقابلة:",
    "ما لم يثبته النص أو الوقائع:",
    "الخلاصة والتوصية العملية:",
]

LATE_MEMO_SECTIONS = [
    "الدفوع أو الاحتمالات المقابلة:",
    "ما لم يثبته النص أو الوقائع:",
    "الخلاصة والتوصية العملية:",
]

FILLER_PATTERNS = [
    "من ظاهر النص المسترجع",
    "قد يثبت ما إذا كان",
    "من المحتمل أن يثبت",
]

BLOCK_PATTERNS = [
    "حدث خطأ أثناء معالجة سؤالك",
    "<|channel>thought",
    "Thinking Process",
    "<|start_header_id|>thought",
    "لم ترفق",
    "بانتظار النصوص",
    "يرجى تزويدي",
]


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


def unique_article_refs(text: str) -> int:
    refs = re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)
    return len(set(refs))


def section_count(text: str, sections: list[str]) -> int:
    return sum(1 for section in sections if section in text)


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def has_block_pattern(text: str) -> bool:
    return any(pattern in text for pattern in BLOCK_PATTERNS)


def filler_count(text: str) -> int:
    return sum(text.count(pattern) for pattern in FILLER_PATTERNS)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if payload in seen:
            continue
        seen.add(payload)
        output.append(row)
    return output


def clean_seed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        text = assistant_text(row)
        if has_block_pattern(text):
            continue
        if repeated_line_count(text) > 0:
            continue
        cleaned.append(row)
    return cleaned


def score_memo_focus_row(row: dict[str, Any]) -> tuple[int, int, int, int, int, int, str]:
    text = assistant_text(row)
    late_hits = section_count(text, LATE_MEMO_SECTIONS)
    full_hits = section_count(text, MEMO_SECTIONS)
    refs = unique_article_refs(text)
    fillers = filler_count(text)
    repeated = repeated_line_count(text)
    target_len_penalty = abs(len(text) - 2400)
    return (
        late_hits,
        full_hits,
        refs,
        -fillers,
        -repeated,
        -target_len_penalty,
        text[:120],
    )


def select_memo_focus_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if detect_mode(row) != "legal_memo":
            continue
        text = assistant_text(row)
        if has_block_pattern(text):
            continue
        if section_count(text, LATE_MEMO_SECTIONS) != len(LATE_MEMO_SECTIONS):
            continue
        if section_count(text, MEMO_SECTIONS) < 9:
            continue
        if unique_article_refs(text) < 2:
            continue
        if repeated_line_count(text) > 0:
            continue
        if not 1400 <= len(text) <= 3600:
            continue
        candidates.append(row)
    candidates.sort(key=score_memo_focus_row, reverse=True)
    return candidates[: max(0, min(limit, len(candidates)))]


def build_manifest(
    *,
    output_dir: Path,
    base_dir: Path,
    seed_dir: Path,
    seed_repeat: int,
    memo_focus_limit: int,
    memo_focus_repeat: int,
    splits: dict[str, list[dict[str, Any]]],
) -> None:
    mode_counts = Counter()
    for split_rows in splits.values():
        for row in split_rows:
            mode_counts[detect_mode(row)] += 1

    payload = {
        "source_base_dir": str(base_dir.resolve()),
        "source_seed_dir": str(seed_dir.resolve()),
        "strategy": {
            "base_refined_full_train": True,
            "clean_seed_repeat": seed_repeat,
            "memo_focus_limit": memo_focus_limit,
            "memo_focus_repeat": memo_focus_repeat,
            "valid_test": "base_full_plus_clean_seed",
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
    parser.add_argument("--seed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed-repeat", type=int, default=2)
    parser.add_argument("--memo-focus-limit", type=int, default=40)
    parser.add_argument("--memo-focus-repeat", type=int, default=2)
    args = parser.parse_args()

    base_train = load_jsonl(args.base_dir / "train.jsonl")
    base_valid = load_jsonl(args.base_dir / "valid.jsonl")
    base_test = load_jsonl(args.base_dir / "test.jsonl")

    seed_train = clean_seed_rows(load_jsonl(args.seed_dir / "train.jsonl"))
    seed_valid = clean_seed_rows(load_jsonl(args.seed_dir / "valid.jsonl"))
    seed_test = clean_seed_rows(load_jsonl(args.seed_dir / "test.jsonl"))

    memo_focus_rows = select_memo_focus_rows(base_train + seed_train, args.memo_focus_limit)

    train_rows = dedupe_rows(
        base_train
        + (seed_train * max(1, args.seed_repeat))
        + (memo_focus_rows * max(1, args.memo_focus_repeat))
    )

    valid_rows = dedupe_rows(base_valid + seed_valid)
    test_rows = dedupe_rows(base_test + seed_test)

    splits = {
        "train": train_rows,
        "valid": valid_rows,
        "test": test_rows,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    build_manifest(
        output_dir=args.output_dir,
        base_dir=args.base_dir,
        seed_dir=args.seed_dir,
        seed_repeat=args.seed_repeat,
        memo_focus_limit=args.memo_focus_limit,
        memo_focus_repeat=args.memo_focus_repeat,
        splits=splits,
    )

    print(
        json.dumps(
            {
                "base_train": len(base_train),
                "clean_seed_train": len(seed_train),
                "memo_focus_selected": len(memo_focus_rows),
                "train_final": len(train_rows),
                "valid_final": len(valid_rows),
                "test_final": len(test_rows),
                "train_modes": dict(Counter(detect_mode(row) for row in train_rows)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
