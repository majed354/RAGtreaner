"""Build a small v14 structure-repair dataset on top of v13.

The intent is to preserve the strong single-adapter gains from v13 while
repairing the remaining structural weak spots:

- late memo sections dropping in some cases
- late analysis sections dropping in some cases
- opinion quality should be preserved through lightweight anchors
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
    "عنوان المذكرة",
    "السؤال محل الرأي",
    "الجواب المختصر",
    "الوقائع ذات الأثر القانوني",
    "النظام أو النصوص المنطبقة",
    "المسائل القانونية",
    "التحليل",
    "الدفوع أو الاحتمالات المقابلة",
    "ما لم يثبته النص أو الوقائع",
    "الخلاصة والتوصية العملية",
]

LATE_MEMO_HEADINGS = [
    "الدفوع أو الاحتمالات المقابلة",
    "ما لم يثبته النص أو الوقائع",
    "الخلاصة والتوصية العملية",
]

ANALYSIS_HEADINGS = [
    "التكييف الأولي للقضية",
    "الأنظمة المحتملة الانطباق",
    "المسائل القانونية الأساسية",
    "ما يدعم الطرف الأول",
    "ما يدعم الطرف الثاني",
    "نقاط الضعف",
    "ما قد يغير النتيجة",
    "ما لم يثبته النص",
    "التقدير الأولي",
]

LATE_ANALYSIS_HEADINGS = [
    "ما يدعم الطرف الثاني",
    "نقاط الضعف",
    "ما قد يغير النتيجة",
    "ما لم يثبته النص",
    "التقدير الأولي",
]


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


def assistant_index(example: dict[str, Any]) -> int | None:
    for idx, msg in enumerate(example.get("messages", [])):
        if msg.get("role") == "assistant":
            return idx
    return None


def assistant_text(example: dict[str, Any]) -> str:
    idx = assistant_index(example)
    if idx is None:
        return ""
    return str(example["messages"][idx].get("content", ""))


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


def prompt_key(example: dict[str, Any]) -> str:
    payload = [
        {"role": msg.get("role"), "content": msg.get("content", "")}
        for msg in example.get("messages", [])
        if msg.get("role") != "assistant"
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def unique_article_refs(text: str) -> int:
    refs = re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)
    return len(set(refs))


def section_count(text: str, sections: list[str]) -> int:
    return sum(1 for section in sections if section in text)


def normalize_assistant_text(text: str) -> str:
    output = text
    for pattern, replacement in FILLER_REPLACEMENTS:
        output = re.sub(pattern, replacement, output)
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


def base_usable(example: dict[str, Any]) -> bool:
    text = assistant_text(example)
    if not text.strip():
        return False
    if any(pattern in text for pattern in BLOCK_PATTERNS):
        return False
    if repeated_line_count(text) > 0:
        return False
    if unique_article_refs(text) == 0:
        return False
    return True


def is_memo_repair_candidate(example: dict[str, Any]) -> bool:
    if detect_mode(example) != "legal_memo" or not base_usable(example):
        return False
    text = assistant_text(example)
    if section_count(text, MEMO_HEADINGS) < len(MEMO_HEADINGS):
        return False
    if section_count(text, LATE_MEMO_HEADINGS) < len(LATE_MEMO_HEADINGS):
        return False
    if unique_article_refs(text) < 2:
        return False
    return 1500 <= len(text) <= 3400


def is_analysis_repair_candidate(example: dict[str, Any]) -> bool:
    if detect_mode(example) != "legal_analysis" or not base_usable(example):
        return False
    text = assistant_text(example)
    if section_count(text, ANALYSIS_HEADINGS) < len(ANALYSIS_HEADINGS):
        return False
    if section_count(text, LATE_ANALYSIS_HEADINGS) < len(LATE_ANALYSIS_HEADINGS):
        return False
    if unique_article_refs(text) < 2:
        return False
    return 900 <= len(text) <= 3200


def is_opinion_anchor(example: dict[str, Any]) -> bool:
    if detect_mode(example) != "legal_opinion" or not base_usable(example):
        return False
    text = assistant_text(example)
    if unique_article_refs(text) < 2:
        return False
    return 550 <= len(text) <= 1600


def sort_memo_candidate(example: dict[str, Any]) -> tuple[int, int, int, int, str]:
    text = assistant_text(example)
    return (
        section_count(text, MEMO_HEADINGS),
        section_count(text, LATE_MEMO_HEADINGS),
        unique_article_refs(text),
        -abs(len(text) - 2200),
        prompt_key(example),
    )


def sort_analysis_candidate(example: dict[str, Any]) -> tuple[int, int, int, int, str]:
    text = assistant_text(example)
    return (
        section_count(text, ANALYSIS_HEADINGS),
        section_count(text, LATE_ANALYSIS_HEADINGS),
        unique_article_refs(text),
        -abs(len(text) - 1500),
        prompt_key(example),
    )


def sort_opinion_anchor(example: dict[str, Any]) -> tuple[int, int, str]:
    text = assistant_text(example)
    return (
        unique_article_refs(text),
        -abs(len(text) - 950),
        prompt_key(example),
    )


def dedupe_by_prompt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = prompt_key(row)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def take_pool(
    rows: list[dict[str, Any]],
    *,
    predicate,
    sort_key,
    limit: int,
    skip_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    seen_keys = set(skip_keys or set())
    for row in sorted((transform_example(item) for item in rows), key=sort_key, reverse=True):
        if not predicate(row):
            continue
        key = prompt_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def build_manifest(
    *,
    output_dir: Path,
    sources: dict[str, str],
    strategy: dict[str, Any],
    splits: dict[str, list[dict[str, Any]]],
) -> None:
    mode_counts = Counter()
    for split_rows in splits.values():
        for row in split_rows:
            mode_counts[detect_mode(row)] += 1

    payload = {
        "dataset_version": "v14_structure_repair",
        "sources": sources,
        "strategy": strategy,
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
    parser.add_argument("--v8-dir", type=Path, required=True)
    parser.add_argument("--v11-dir", type=Path, required=True)
    parser.add_argument("--v13-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--memo-limit", type=int, default=24)
    parser.add_argument("--memo-repeat", type=int, default=2)
    parser.add_argument("--analysis-limit", type=int, default=20)
    parser.add_argument("--analysis-repeat", type=int, default=2)
    parser.add_argument("--opinion-anchor-limit", type=int, default=24)
    parser.add_argument("--valid-per-mode", type=int, default=4)
    parser.add_argument("--test-per-mode", type=int, default=4)
    args = parser.parse_args()

    v8_train = load_jsonl(args.v8_dir / "train.jsonl")
    v8_valid = load_jsonl(args.v8_dir / "valid.jsonl")
    v8_test = load_jsonl(args.v8_dir / "test.jsonl")
    v11_train = load_jsonl(args.v11_dir / "train.jsonl")
    v11_valid = load_jsonl(args.v11_dir / "valid.jsonl")
    v11_test = load_jsonl(args.v11_dir / "test.jsonl")
    v13_train = load_jsonl(args.v13_dir / "train.jsonl")
    v13_valid = load_jsonl(args.v13_dir / "valid.jsonl")
    v13_test = load_jsonl(args.v13_dir / "test.jsonl")

    memo_source_train = v8_train + v11_train
    memo_source_eval = v8_valid + v8_test + v11_valid + v11_test
    analysis_source_train = v11_train + v8_train
    analysis_source_eval = v11_valid + v11_test + v8_valid + v8_test
    opinion_source_train = v13_train
    opinion_source_eval = v13_valid + v13_test

    memo_train_unique = take_pool(
        memo_source_train,
        predicate=is_memo_repair_candidate,
        sort_key=sort_memo_candidate,
        limit=args.memo_limit,
    )
    analysis_train_unique = take_pool(
        analysis_source_train,
        predicate=is_analysis_repair_candidate,
        sort_key=sort_analysis_candidate,
        limit=args.analysis_limit,
    )
    opinion_anchor_train = take_pool(
        opinion_source_train,
        predicate=is_opinion_anchor,
        sort_key=sort_opinion_anchor,
        limit=args.opinion_anchor_limit,
    )

    memo_train = memo_train_unique * max(1, args.memo_repeat)
    analysis_train = analysis_train_unique * max(1, args.analysis_repeat)

    train_rows = memo_train + analysis_train + opinion_anchor_train

    used_eval_keys = set(prompt_key(row) for row in memo_train_unique + analysis_train_unique + opinion_anchor_train)

    memo_valid_test = take_pool(
        memo_source_eval,
        predicate=is_memo_repair_candidate,
        sort_key=sort_memo_candidate,
        limit=args.valid_per_mode + args.test_per_mode,
        skip_keys=used_eval_keys,
    )
    used_eval_keys.update(prompt_key(row) for row in memo_valid_test)

    analysis_valid_test = take_pool(
        analysis_source_eval,
        predicate=is_analysis_repair_candidate,
        sort_key=sort_analysis_candidate,
        limit=args.valid_per_mode + args.test_per_mode,
        skip_keys=used_eval_keys,
    )
    used_eval_keys.update(prompt_key(row) for row in analysis_valid_test)

    opinion_valid_test = take_pool(
        opinion_source_eval,
        predicate=is_opinion_anchor,
        sort_key=sort_opinion_anchor,
        limit=args.valid_per_mode + args.test_per_mode,
        skip_keys=used_eval_keys,
    )

    valid_rows = (
        memo_valid_test[: args.valid_per_mode]
        + analysis_valid_test[: args.valid_per_mode]
        + opinion_valid_test[: args.valid_per_mode]
    )
    test_rows = (
        memo_valid_test[args.valid_per_mode : args.valid_per_mode + args.test_per_mode]
        + analysis_valid_test[args.valid_per_mode : args.valid_per_mode + args.test_per_mode]
        + opinion_valid_test[args.valid_per_mode : args.valid_per_mode + args.test_per_mode]
    )

    splits = {
        "train": train_rows,
        "valid": dedupe_by_prompt(valid_rows),
        "test": dedupe_by_prompt(test_rows),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    build_manifest(
        output_dir=args.output_dir,
        sources={
            "memo_sources": f"{args.v8_dir},{args.v11_dir}",
            "analysis_sources": f"{args.v11_dir},{args.v8_dir}",
            "opinion_anchor_source": str(args.v13_dir),
        },
        strategy={
            "recipe": "v13_plus_memo_analysis_structure_repair_with_opinion_anchors",
            "memo_limit": args.memo_limit,
            "memo_repeat": args.memo_repeat,
            "analysis_limit": args.analysis_limit,
            "analysis_repeat": args.analysis_repeat,
            "opinion_anchor_limit": args.opinion_anchor_limit,
            "valid_per_mode": args.valid_per_mode,
            "test_per_mode": args.test_per_mode,
        },
        splits=splits,
    )

    print(
        json.dumps(
            {
                "memo_train_unique": len(memo_train_unique),
                "analysis_train_unique": len(analysis_train_unique),
                "opinion_anchor_train": len(opinion_anchor_train),
                "train_final": len(splits["train"]),
                "valid_final": len(splits["valid"]),
                "test_final": len(splits["test"]),
                "train_modes": dict(Counter(detect_mode(row) for row in splits["train"])),
                "valid_modes": dict(Counter(detect_mode(row) for row in splits["valid"])),
                "test_modes": dict(Counter(detect_mode(row) for row in splits["test"])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
