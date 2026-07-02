"""Build a high-fit memo specialist dataset for v6 refinement.

This round intentionally avoids flooding the model with every memo example.
Instead, it combines:
- clean teacher memo examples from the seed set
- a capped slice of stronger structured memo examples from the refined base set
- compact replay from clean opinion/analysis seed examples

The goal is to improve memo completeness and citation behavior while reducing
the risk of cross-mode regression in a small model.
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

ANALYSIS_SECTIONS = [
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

OPINION_SECTIONS = [
    "1) النظام المنطبق:",
    "2) الحكم المباشر:",
    "3) المواد المستند إليها:",
    "4) القيود أو الاستثناءات:",
    "5) ما لم يثبته النص:",
    "6) الخلاصة العملية:",
]

FILLER_PATTERNS = [
    "من ظاهر النص المسترجع",
    "قد يثبت ما إذا كان",
    "من المحتمل أن يثبت",
]

WAITING_PATTERNS = [
    "لم ترفق",
    "بانتظار النصوص",
    "يرجى تزويدي",
]

THOUGHT_PATTERNS = [
    "<|channel>thought",
    "Thinking Process",
    "<|start_header_id|>thought",
]

ERROR_PATTERNS = [
    "حدث خطأ أثناء معالجة سؤالك",
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


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def section_count(text: str, sections: list[str]) -> int:
    return sum(1 for section in sections if section in text)


def filler_count(text: str) -> int:
    return sum(text.count(pattern) for pattern in FILLER_PATTERNS)


def has_bad_pattern(text: str) -> bool:
    return any(
        pattern in text
        for pattern in WAITING_PATTERNS + THOUGHT_PATTERNS + ERROR_PATTERNS
    )


def score_memo_candidate(example: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    text = assistant_text(example)
    sections = section_count(text, MEMO_SECTIONS)
    refs = unique_article_refs(text)
    repeated = repeated_line_count(text)
    fillers = filler_count(text)
    length_penalty = abs(len(text) - 2400)
    return (
        sections,
        refs,
        -fillers,
        -repeated,
        -length_penalty,
        text[:120],
    )


def score_replay_candidate(
    example: dict[str, Any], sections: list[str]
) -> tuple[int, int, int, int, str]:
    text = assistant_text(example)
    return (
        section_count(text, sections),
        unique_article_refs(text),
        -repeated_line_count(text),
        -len(text),
        text[:120],
    )


def select_top_memos(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if detect_mode(row) != "legal_memo":
            continue
        text = assistant_text(row)
        if has_bad_pattern(text):
            continue
        if section_count(text, MEMO_SECTIONS) != len(MEMO_SECTIONS):
            continue
        if unique_article_refs(text) < 3:
            continue
        if repeated_line_count(text) > 0:
            continue
        if not 1500 <= len(text) <= 3600:
            continue
        candidates.append(row)
    candidates.sort(key=score_memo_candidate, reverse=True)
    return candidates[: max(0, min(limit, len(candidates)))]


def select_replay(
    rows: list[dict[str, Any]], mode: str, limit: int, sections: list[str]
) -> list[dict[str, Any]]:
    candidates = [row for row in rows if detect_mode(row) == mode]
    candidates = [row for row in candidates if not has_bad_pattern(assistant_text(row))]
    candidates = [row for row in candidates if len(assistant_text(row)) >= 400]
    candidates = [row for row in candidates if unique_article_refs(assistant_text(row)) >= 1]
    candidates = [row for row in candidates if repeated_line_count(assistant_text(row)) == 0]
    candidates.sort(key=lambda row: score_replay_candidate(row, sections), reverse=True)
    return candidates[: max(0, min(limit, len(candidates)))]


def build_manifest(
    *,
    output_dir: Path,
    base_dir: Path,
    seed_dir: Path,
    seed_memo_repeat: int,
    structured_memo_limit: int,
    structured_memo_repeat: int,
    opinion_replay_limit: int,
    analysis_replay_limit: int,
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
            "seed_memo_repeat": seed_memo_repeat,
            "structured_memo_limit": structured_memo_limit,
            "structured_memo_repeat": structured_memo_repeat,
            "opinion_replay_limit": opinion_replay_limit,
            "analysis_replay_limit": analysis_replay_limit,
            "valid_test": "memo_only_combined",
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
    parser.add_argument("--seed-memo-repeat", type=int, default=4)
    parser.add_argument("--structured-memo-limit", type=int, default=48)
    parser.add_argument("--structured-memo-repeat", type=int, default=2)
    parser.add_argument("--opinion-replay-limit", type=int, default=16)
    parser.add_argument("--analysis-replay-limit", type=int, default=16)
    args = parser.parse_args()

    base_train = load_jsonl(args.base_dir / "train.jsonl")
    base_valid = load_jsonl(args.base_dir / "valid.jsonl")
    base_test = load_jsonl(args.base_dir / "test.jsonl")

    seed_train = load_jsonl(args.seed_dir / "train.jsonl")
    seed_valid = load_jsonl(args.seed_dir / "valid.jsonl")
    seed_test = load_jsonl(args.seed_dir / "test.jsonl")

    seed_memos = [row for row in seed_train if detect_mode(row) == "legal_memo"]
    structured_memos = select_top_memos(base_train, args.structured_memo_limit)
    opinion_replay = select_replay(
        seed_train,
        mode="legal_opinion",
        limit=args.opinion_replay_limit,
        sections=OPINION_SECTIONS,
    )
    analysis_replay = select_replay(
        seed_train,
        mode="legal_analysis",
        limit=args.analysis_replay_limit,
        sections=ANALYSIS_SECTIONS,
    )

    train_rows = (
        seed_memos * max(1, args.seed_memo_repeat)
        + structured_memos * max(1, args.structured_memo_repeat)
        + opinion_replay
        + analysis_replay
    )

    valid_rows = [row for row in base_valid if detect_mode(row) == "legal_memo"] + [
        row for row in seed_valid if detect_mode(row) == "legal_memo"
    ]
    test_rows = [row for row in base_test if detect_mode(row) == "legal_memo"] + [
        row for row in seed_test if detect_mode(row) == "legal_memo"
    ]

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
        seed_memo_repeat=args.seed_memo_repeat,
        structured_memo_limit=args.structured_memo_limit,
        structured_memo_repeat=args.structured_memo_repeat,
        opinion_replay_limit=args.opinion_replay_limit,
        analysis_replay_limit=args.analysis_replay_limit,
        splits=splits,
    )

    print(
        json.dumps(
            {
                "seed_memos": len(seed_memos),
                "structured_memos_selected": len(structured_memos),
                "opinion_replay": len(opinion_replay),
                "analysis_replay": len(analysis_replay),
                "train_final": len(train_rows),
                "valid_final": len(valid_rows),
                "test_final": len(test_rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
