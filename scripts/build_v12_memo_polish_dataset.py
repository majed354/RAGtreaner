"""Build a conservative v4-based memo-polish dataset.

This round keeps the successful v4 memo-boost recipe as the backbone and adds
only a small amount of higher-signal memo supervision:

1. replace weaker memo rows from v4 when the same prompt exists with a clearly
   better normalized memo answer from later clean datasets
2. add a small set of new memo prompts from v8/v11 quality-filtered pools
3. keep the original v4 replay rows untouched

The goal is to preserve the v4 sweet spot while nudging memo answer quality and
citation discipline upward without reintroducing broad regressions.
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

CANONICAL_MEMO_HEADING_LINES = {
    heading: f"- **{heading}**:"
    for heading in MEMO_HEADINGS
}


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


def assistant_index(example: dict[str, Any]) -> int | None:
    for idx, msg in enumerate(example.get("messages", [])):
        if msg.get("role") == "assistant":
            return idx
    return None


def prompt_key(example: dict[str, Any]) -> str:
    payload = [
        {
            "role": msg.get("role"),
            "content": msg.get("content", ""),
        }
        for msg in example.get("messages", [])
        if msg.get("role") != "assistant"
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def detect_mode(example: dict[str, Any]) -> str:
    combined = "\n".join(
        str(msg.get("content", ""))
        for msg in example.get("messages", [])
        if msg.get("role") in {"system", "assistant"}
    )
    if "عنوان المذكرة" in combined and "الخلاصة والتوصية العملية" in combined:
        return "legal_memo"
    if "التكييف الأولي للقضية" in combined and "التقدير الأولي" in combined:
        return "legal_analysis"
    if "النظام المنطبق" in combined and "الخلاصة العملية" in combined:
        return "legal_opinion"
    return "unknown"


def assistant_text(example: dict[str, Any]) -> str:
    idx = assistant_index(example)
    if idx is None:
        return ""
    return str(example["messages"][idx].get("content", ""))


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def unique_article_refs(text: str) -> int:
    refs = re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)
    return len(set(refs))


def normalize_memo_headings(text: str) -> str:
    normalized = text
    for heading, canonical in CANONICAL_MEMO_HEADING_LINES.items():
        pattern = re.compile(
            rf"(?mi)^\s*(?:[-*•]|\d+[)\.-])?\s*(?:\*\*)?\s*{re.escape(heading)}\s*(?:\*\*)?\s*:\s*"
        )
        normalized = pattern.sub(f"{canonical}\n", normalized)
    return normalized


def normalize_assistant_text(text: str) -> str:
    output = text
    for pattern, replacement in FILLER_REPLACEMENTS:
        output = re.sub(pattern, replacement, output)
    output = normalize_memo_headings(output)
    output = re.sub(r"(?m)^\s*•(?:\s*•)+\s*$", "", output)
    output = re.sub(r"[ \t]+\n", "\n", output)
    output = re.sub(r"\n{3,}", "\n\n", output)
    return output.strip()


def transform_example(example: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(example)
    if detect_mode(row) != "legal_memo":
        return row
    idx = assistant_index(row)
    if idx is None:
        return row
    row["messages"][idx]["content"] = normalize_assistant_text(
        str(row["messages"][idx].get("content", ""))
    )
    return row


def section_count(text: str, sections: list[str]) -> int:
    return sum(1 for section in sections if section in text)


def memo_quality_score(example: dict[str, Any]) -> tuple[int, int, int, int]:
    text = assistant_text(example)
    section_hits = section_count(text, MEMO_HEADINGS)
    late_hits = section_count(text, LATE_MEMO_HEADINGS)
    refs = unique_article_refs(text)
    length_penalty = abs(len(text) - 2100)
    return (section_hits, late_hits, refs, -length_penalty)


def is_usable_memo(example: dict[str, Any]) -> bool:
    if detect_mode(example) != "legal_memo":
        return False
    text = assistant_text(example)
    if not text.strip():
        return False
    if any(pattern in text for pattern in BLOCK_PATTERNS):
        return False
    if repeated_line_count(text) > 0:
        return False
    if section_count(text, MEMO_HEADINGS) < len(MEMO_HEADINGS):
        return False
    if section_count(text, LATE_MEMO_HEADINGS) < len(LATE_MEMO_HEADINGS):
        return False
    if unique_article_refs(text) < 2:
        return False
    if not 1300 <= len(text) <= 3400:
        return False
    return True


def sort_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=memo_quality_score, reverse=True)


def build_manifest(
    *,
    output_dir: Path,
    v4_dir: Path,
    v8_dir: Path,
    v11_dir: Path,
    augment_repeat: int,
    v8_augment_limit: int,
    v11_augment_limit: int,
    replacements_total: int,
    replaced_by_source: Counter[str],
    added_by_source: Counter[str],
    splits: dict[str, list[dict[str, Any]]],
) -> None:
    mode_counts = Counter()
    for split_rows in splits.values():
        for row in split_rows:
            mode_counts[detect_mode(row)] += 1

    payload = {
        "dataset_version": "v12_memo_polish",
        "sources": {
            "v4_base": str(v4_dir.resolve()),
            "v8_candidates": str(v8_dir.resolve()),
            "v11_candidates": str(v11_dir.resolve()),
        },
        "strategy": {
            "recipe": "resume_v4_keep_base_replace_weaker_memo_rows_add_small_clean_memo_slice",
            "replacement_policy": "same-prompt memo rows replaced only when candidate quality is strictly better",
            "augment_repeat": augment_repeat,
            "v8_augment_limit": v8_augment_limit,
            "v11_augment_limit": v11_augment_limit,
            "valid_test": "inherit_v4",
        },
        "counts": {
            "replacements_total": replacements_total,
            "replaced_by_source": dict(replaced_by_source),
            "added_by_source": dict(added_by_source),
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
    parser.add_argument("--v4-dir", type=Path, required=True)
    parser.add_argument("--v8-dir", type=Path, required=True)
    parser.add_argument("--v11-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--v8-augment-limit", type=int, default=16)
    parser.add_argument("--v11-augment-limit", type=int, default=16)
    parser.add_argument("--augment-repeat", type=int, default=2)
    args = parser.parse_args()

    v4_train = load_jsonl(args.v4_dir / "train.jsonl")
    v4_valid = load_jsonl(args.v4_dir / "valid.jsonl")
    v4_test = load_jsonl(args.v4_dir / "test.jsonl")

    train_rows = [copy.deepcopy(row) for row in v4_train]
    prompt_to_train_idx = {prompt_key(row): idx for idx, row in enumerate(train_rows)}

    replacements_total = 0
    replaced_by_source: Counter[str] = Counter()
    added_by_source: Counter[str] = Counter()

    extra_candidates: dict[str, list[dict[str, Any]]] = {"v8": [], "v11": []}

    for source_name, source_dir in (("v8", args.v8_dir), ("v11", args.v11_dir)):
        source_rows = [transform_example(row) for row in load_jsonl(source_dir / "train.jsonl")]
        usable_rows = sort_candidates([row for row in source_rows if is_usable_memo(row)])

        for candidate in usable_rows:
            key = prompt_key(candidate)
            existing_idx = prompt_to_train_idx.get(key)
            if existing_idx is None:
                extra_candidates[source_name].append(candidate)
                continue

            existing_row = train_rows[existing_idx]
            if detect_mode(existing_row) != "legal_memo":
                continue
            if memo_quality_score(candidate) > memo_quality_score(transform_example(existing_row)):
                train_rows[existing_idx] = candidate
                replacements_total += 1
                replaced_by_source.update([source_name])

    used_prompt_keys = {prompt_key(row) for row in train_rows}
    augment_plan = (
        ("v8", args.v8_augment_limit),
        ("v11", args.v11_augment_limit),
    )
    for source_name, limit in augment_plan:
        chosen: list[dict[str, Any]] = []
        for candidate in extra_candidates[source_name]:
            key = prompt_key(candidate)
            if key in used_prompt_keys:
                continue
            used_prompt_keys.add(key)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
        if chosen:
            train_rows.extend(chosen * max(1, args.augment_repeat))
            added_by_source[source_name] += len(chosen) * max(1, args.augment_repeat)

    splits = {
        "train": train_rows,
        "valid": v4_valid,
        "test": v4_test,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in splits.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)

    build_manifest(
        output_dir=args.output_dir,
        v4_dir=args.v4_dir,
        v8_dir=args.v8_dir,
        v11_dir=args.v11_dir,
        augment_repeat=args.augment_repeat,
        v8_augment_limit=args.v8_augment_limit,
        v11_augment_limit=args.v11_augment_limit,
        replacements_total=replacements_total,
        replaced_by_source=replaced_by_source,
        added_by_source=added_by_source,
        splits=splits,
    )

    print(
        json.dumps(
            {
                "v4_train_base": len(v4_train),
                "replacements_total": replacements_total,
                "replaced_by_source": dict(replaced_by_source),
                "added_by_source": dict(added_by_source),
                "train_final": len(splits["train"]),
                "valid_final": len(splits["valid"]),
                "test_final": len(splits["test"]),
                "train_modes": dict(Counter(detect_mode(row) for row in splits["train"])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
