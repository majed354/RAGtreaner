"""Build a section-repair dataset for v8 refinement.

v8 focuses on two things:
1. clean/canonical supervision by normalizing common teacher filler phrases
2. extra memo repair examples that emphasize the sections that frequently drop

The dataset stays multi-mode, but memo repair examples receive additional
weight without collapsing the whole round into a memo-only specialist.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


BLOCK_PATTERNS = [
    "حدث خطأ أثناء معالجة سؤالك",
    "<|channel>thought",
    "Thinking Process",
    "<|start_header_id|>thought",
    "لم ترفق",
    "بانتظار النصوص",
    "يرجى تزويدي",
]

FILLER_REPLACEMENTS = [
    (r"من ظاهر النص(?:وص)? المسترجع(?:ة)?\s*،\s*يثبت أن\s*", ""),
    (r"من ظاهر النص(?:وص)? المسترجع(?:ة)?\s*يثبت أن\s*", ""),
    (r"من ظاهر النص(?:وص)? المسترجع(?:ة)?\s*،\s*", ""),
    (r"من ظاهر النص(?:وص)? المسترجع(?:ة)?\s*", ""),
    (r"من المحتمل أن يثبت\s*", ""),
    (r"قد يثبت ما إذا كان\s*", ""),
]

MEMO_HEADINGS = [
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

LATE_MEMO_HEADINGS = [
    "الدفوع أو الاحتمالات المقابلة:",
    "ما لم يثبته النص أو الوقائع:",
    "الخلاصة والتوصية العملية:",
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


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def unique_article_refs(text: str) -> int:
    refs = re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)
    return len(set(refs))


def section_count(text: str, sections: list[str]) -> int:
    return sum(1 for section in sections if section in text)


def assistant_index(example: dict[str, Any]) -> int | None:
    for idx, msg in enumerate(example.get("messages", [])):
        if msg.get("role") == "assistant":
            return idx
    return None


def normalize_headings(text: str) -> str:
    heading_patterns = MEMO_HEADINGS + [
        "1) النظام المنطبق:",
        "2) الحكم المباشر:",
        "3) المواد المستند إليها:",
        "4) القيود أو الاستثناءات:",
        "5) ما لم يثبته النص:",
        "6) الخلاصة العملية:",
        "1) التكييف الأولي للقضية:",
        "2) الأنظمة المحتملة الانطباق:",
        "3) المسائل القانونية الأساسية:",
        "4) ما يدعم الطرف الأول:",
        "5) ما يدعم الطرف الثاني:",
        "6) نقاط الضعف:",
        "7) ما قد يغير النتيجة:",
        "8) ما لم يثبته النص:",
        "9) التقدير الأولي:",
    ]
    for heading in heading_patterns:
        text = re.sub(rf"(?m)^\s*[-*]\s*{re.escape(heading)}", heading, text)
    return text


def normalize_assistant_text(text: str) -> str:
    output = text
    for pattern, replacement in FILLER_REPLACEMENTS:
        output = re.sub(pattern, replacement, output)
    output = normalize_headings(output)
    output = re.sub(r"[ \t]+\n", "\n", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip()


def transform_example(example: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(example)
    idx = assistant_index(row)
    if idx is None:
        return row
    row["messages"][idx]["content"] = normalize_assistant_text(
        str(row["messages"][idx].get("content", ""))
    )
    return row


def is_usable(example: dict[str, Any]) -> bool:
    idx = assistant_index(example)
    if idx is None:
        return False
    text = str(example["messages"][idx].get("content", ""))
    if not text.strip():
        return False
    if any(pattern in text for pattern in BLOCK_PATTERNS):
        return False
    if repeated_line_count(text) > 0:
        return False
    return True


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


def select_memo_repair(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if detect_mode(row) != "legal_memo":
            continue
        idx = assistant_index(row)
        if idx is None:
            continue
        text = str(row["messages"][idx].get("content", ""))
        if section_count(text, LATE_MEMO_HEADINGS) != len(LATE_MEMO_HEADINGS):
            continue
        if section_count(text, MEMO_HEADINGS) < 9:
            continue
        if unique_article_refs(text) < 2:
            continue
        if not 1400 <= len(text) <= 3200:
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            section_count(
                str(row["messages"][assistant_index(row)].get("content", "")),
                MEMO_HEADINGS,
            ),
            unique_article_refs(
                str(row["messages"][assistant_index(row)].get("content", ""))
            ),
            -abs(
                len(str(row["messages"][assistant_index(row)].get("content", ""))) - 2200
            ),
        ),
        reverse=True,
    )
    return candidates[: max(0, min(limit, len(candidates)))]


def build_manifest(
    *,
    output_dir: Path,
    base_dir: Path,
    seed_dir: Path,
    seed_repeat: int,
    memo_repair_limit: int,
    memo_repair_repeat: int,
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
            "normalized_supervision": True,
            "base_clean_full_train": True,
            "seed_repeat": seed_repeat,
            "memo_repair_limit": memo_repair_limit,
            "memo_repair_repeat": memo_repair_repeat,
            "valid_test": "base_plus_seed_clean",
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
    parser.add_argument("--seed-repeat", type=int, default=1)
    parser.add_argument("--memo-repair-limit", type=int, default=48)
    parser.add_argument("--memo-repair-repeat", type=int, default=2)
    args = parser.parse_args()

    base_train = [transform_example(row) for row in load_jsonl(args.base_dir / "train.jsonl")]
    base_valid = [transform_example(row) for row in load_jsonl(args.base_dir / "valid.jsonl")]
    base_test = [transform_example(row) for row in load_jsonl(args.base_dir / "test.jsonl")]

    seed_train = [transform_example(row) for row in load_jsonl(args.seed_dir / "train.jsonl")]
    seed_valid = [transform_example(row) for row in load_jsonl(args.seed_dir / "valid.jsonl")]
    seed_test = [transform_example(row) for row in load_jsonl(args.seed_dir / "test.jsonl")]

    base_train = [row for row in base_train if is_usable(row)]
    base_valid = [row for row in base_valid if is_usable(row)]
    base_test = [row for row in base_test if is_usable(row)]
    seed_train = [row for row in seed_train if is_usable(row)]
    seed_valid = [row for row in seed_valid if is_usable(row)]
    seed_test = [row for row in seed_test if is_usable(row)]

    base_plus_seed_train = dedupe_rows(base_train + (seed_train * max(1, args.seed_repeat)))
    memo_repair_rows = select_memo_repair(base_train + seed_train, args.memo_repair_limit)
    train_rows = base_plus_seed_train + (
        memo_repair_rows * max(1, args.memo_repair_repeat)
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
        memo_repair_limit=args.memo_repair_limit,
        memo_repair_repeat=args.memo_repair_repeat,
        splits=splits,
    )

    print(
        json.dumps(
            {
                "base_train_clean": len(base_train),
                "seed_train_clean": len(seed_train),
                "memo_repair_selected": len(memo_repair_rows),
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
