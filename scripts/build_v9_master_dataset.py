"""Assemble a high-quality v9 master dataset from curated local sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "training" / "final_legal_modes_v9_master"

DEFAULT_PRIMARY_SOURCES = [
    ("seed_v1", ROOT / "data" / "training" / "legal_modes_seed_v1" / "sft_messages"),
    ("structured_v9", ROOT / "data" / "training" / "structured_mode_curriculum_v9" / "sft_messages"),
]
DEFAULT_SUPPLEMENT_SOURCES = [
    ("section_repair_v8", ROOT / "data" / "training" / "final_section_repair_v8"),
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

FILLER_PATTERNS = [
    "من ظاهرها",
    "من المحتمل أن يثبت",
    "قد يثبت ما إذا كان",
]

SECTIONS_BY_MODE = {
    "legal_opinion": [
        "النظام المنطبق",
        "الحكم المباشر",
        "المواد المستند إليها",
        "القيود أو الاستثناءات",
        "ما لم يثبته النص",
        "الخلاصة العملية",
    ],
    "legal_memo": [
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
    ],
    "legal_analysis": [
        "التكييف الأولي للقضية",
        "الأنظمة المحتملة الانطباق",
        "المسائل القانونية الأساسية",
        "ما يدعم الطرف الأول",
        "ما يدعم الطرف الثاني",
        "نقاط الضعف",
        "ما قد يغير النتيجة",
        "ما لم يثبته النص",
        "التقدير الأولي",
    ],
}

SUPPLEMENT_MODE_CAPS = {
    "legal_opinion": 50,
    "legal_memo": 50,
    "legal_analysis": 10,
}

SOURCE_PRIORITY = {
    "seed_v1": 3,
    "section_repair_v8": 2,
    "structured_v9": 1,
}

TARGET_ASSISTANT_CHARS = {
    "legal_opinion": 1200,
    "legal_memo": 2300,
    "legal_analysis": 1800,
}

TARGET_MODE_RATIOS = {
    "legal_opinion": 0.34,
    "legal_memo": 0.34,
    "legal_analysis": 0.32,
}


@dataclass
class Candidate:
    source_label: str
    source_split: str
    mode: str
    signature: str
    row: dict[str, Any]
    quality_score: float
    unique_article_refs: int
    section_coverage: float
    repeated_line_count: int
    filler_hits: int


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


def system_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in messages if msg.get("role") == "system").strip()


def user_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in messages if msg.get("role") == "user").strip()


def assistant_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in messages if msg.get("role") == "assistant").strip()


def normalize_for_match(text: str) -> str:
    value = str(text or "")
    value = value.replace("**", "")
    value = value.replace("*", "")
    value = re.sub(r"^[\-\u2022]\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"\(\d+\)|\d+[.)]", " ", value)
    value = value.replace(":", " ")
    value = value.replace("—", " ")
    value = value.replace("-", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def detect_mode(messages: list[dict[str, Any]]) -> str:
    combined = normalize_for_match("\n".join([system_text(messages), assistant_text(messages)]))
    if "عنوان المذكرة" in combined and "الخلاصة والتوصية العملية" in combined:
        return "legal_memo"
    if "التكييف الأولي للقضية" in combined and "التقدير الأولي" in combined:
        return "legal_analysis"
    if "النظام المنطبق" in combined and "الخلاصة العملية" in combined:
        return "legal_opinion"
    return "unknown"


def repeated_line_count(text: str) -> int:
    counts = Counter(line.strip() for line in text.splitlines() if line.strip())
    return sum(1 for count in counts.values() if count >= 3)


def unique_article_refs(text: str) -> int:
    refs = re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)
    return len(set(refs))


def filler_hits(text: str) -> int:
    return sum(text.count(pattern) for pattern in FILLER_PATTERNS)


def section_coverage(mode: str, text: str) -> float:
    required = SECTIONS_BY_MODE.get(mode, [])
    if not required:
        return 0.0
    normalized_text = normalize_for_match(text)
    hits = sum(1 for section in required if normalize_for_match(section) in normalized_text)
    return hits / len(required)


def has_block_pattern(text: str) -> bool:
    return any(pattern in text for pattern in BLOCK_PATTERNS)


def signature_for(messages: list[dict[str, Any]], mode: str) -> str:
    payload = {
        "mode": mode,
        "system": system_text(messages),
        "user": user_text(messages),
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def quality_score(mode: str, text: str) -> float:
    target_chars = TARGET_ASSISTANT_CHARS.get(mode, 1500)
    coverage = section_coverage(mode, text)
    refs = unique_article_refs(text)
    repeated = repeated_line_count(text)
    fillers = filler_hits(text)
    length_penalty = abs(len(text) - target_chars) / max(target_chars, 1)
    score = (
        (coverage * 100.0)
        + min(refs, 8) * 4.0
        - repeated * 12.0
        - fillers * 4.0
        - length_penalty * 15.0
    )
    return round(score, 3)


def build_candidate(row: dict[str, Any], source_label: str, source_split: str) -> Candidate | None:
    messages = row.get("messages", [])
    mode = detect_mode(messages)
    if mode == "unknown":
        return None
    assistant = assistant_text(messages)
    if not assistant or has_block_pattern(assistant):
        return None
    coverage = section_coverage(mode, assistant)
    repeated = repeated_line_count(assistant)
    refs = unique_article_refs(assistant)
    fillers = filler_hits(assistant)
    if coverage < 1.0:
        return None
    if repeated > 0:
        return None
    if refs == 0:
        return None
    if len(assistant) < 300:
        return None
    signature = signature_for(messages, mode)
    return Candidate(
        source_label=source_label,
        source_split=source_split,
        mode=mode,
        signature=signature,
        row=row,
        quality_score=quality_score(mode, assistant),
        unique_article_refs=refs,
        section_coverage=coverage,
        repeated_line_count=repeated,
        filler_hits=fillers,
    )


def choose_better(left: Candidate, right: Candidate) -> Candidate:
    if right.quality_score != left.quality_score:
        return right if right.quality_score > left.quality_score else left
    if SOURCE_PRIORITY.get(right.source_label, 0) != SOURCE_PRIORITY.get(left.source_label, 0):
        return right if SOURCE_PRIORITY.get(right.source_label, 0) > SOURCE_PRIORITY.get(left.source_label, 0) else left
    return right if right.unique_article_refs > left.unique_article_refs else left


def load_source_candidates(source_label: str, source_dir: Path) -> tuple[dict[str, Candidate], Counter]:
    best_by_signature: dict[str, Candidate] = {}
    counts = Counter()
    for split in ("train", "valid", "test"):
        path = source_dir / f"{split}.jsonl"
        if not path.exists():
            continue
        for row in load_jsonl(path):
            candidate = build_candidate(row, source_label, split)
            if candidate is None:
                counts["filtered_out"] += 1
                continue
            counts["usable"] += 1
            counts[f"usable::{candidate.mode}"] += 1
            existing = best_by_signature.get(candidate.signature)
            best_by_signature[candidate.signature] = candidate if existing is None else choose_better(existing, candidate)
    counts["unique_signatures"] = len(best_by_signature)
    return best_by_signature, counts


def choose_split(signature: str) -> str:
    bucket = int(signature[:8], 16) % 10
    if bucket < 8:
        return "train"
    if bucket == 8:
        return "valid"
    return "test"


def desired_mode_counts(target_total: int) -> dict[str, int]:
    counts = {
        mode: int(target_total * ratio)
        for mode, ratio in TARGET_MODE_RATIOS.items()
    }
    remainder = target_total - sum(counts.values())
    for mode in ("legal_opinion", "legal_memo", "legal_analysis"):
        if remainder <= 0:
            break
        counts[mode] += 1
        remainder -= 1
    return counts


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mode_counts = Counter()
    for row in rows:
        mode = detect_mode(row.get("messages", []))
        mode_counts[mode] += 1
    return dict(mode_counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--target-total", type=int, default=2500)
    args = parser.parse_args()

    primary_best: dict[str, Candidate] = {}
    source_stats: dict[str, Any] = {}
    for source_label, source_dir in DEFAULT_PRIMARY_SOURCES:
        candidates, counts = load_source_candidates(source_label, source_dir)
        source_stats[source_label] = {
            "path": str(source_dir),
            "counts": dict(counts),
        }
        for signature, candidate in candidates.items():
            existing = primary_best.get(signature)
            primary_best[signature] = candidate if existing is None else choose_better(existing, candidate)

    supplement_pool: list[Candidate] = []
    for source_label, source_dir in DEFAULT_SUPPLEMENT_SOURCES:
        candidates, counts = load_source_candidates(source_label, source_dir)
        source_stats[source_label] = {
            "path": str(source_dir),
            "counts": dict(counts),
        }
        supplement_pool.extend(candidate for signature, candidate in candidates.items() if signature not in primary_best)

    supplement_pool.sort(
        key=lambda candidate: (
            candidate.mode,
            candidate.quality_score,
            candidate.unique_article_refs,
            SOURCE_PRIORITY.get(candidate.source_label, 0),
        ),
        reverse=True,
    )

    selected_supplements: list[Candidate] = []
    supplement_counts = Counter()
    for candidate in supplement_pool:
        cap = SUPPLEMENT_MODE_CAPS.get(candidate.mode, 0)
        if supplement_counts[candidate.mode] >= cap:
            continue
        selected_supplements.append(candidate)
        supplement_counts[candidate.mode] += 1

    final_candidates = list(primary_best.values()) + selected_supplements
    final_candidates.sort(key=lambda candidate: (candidate.mode, candidate.signature))

    split_rows: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    split_manifest: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    for candidate in final_candidates:
        split = choose_split(candidate.signature)
        split_rows[split].append(candidate.row)
        split_manifest[split].append(
            {
                "signature": candidate.signature,
                "mode": candidate.mode,
                "source_label": candidate.source_label,
                "source_split": candidate.source_split,
                "quality_score": candidate.quality_score,
                "unique_article_refs": candidate.unique_article_refs,
                "section_coverage": candidate.section_coverage,
            }
        )

    unique_mode_counts = Counter(candidate.mode for candidate in final_candidates)
    oversample_added = Counter()
    if args.target_total > len(final_candidates):
        target_counts = desired_mode_counts(args.target_total)
        repeat_pool: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in final_candidates:
            repeat_pool[candidate.mode].append(candidate)
        for mode in repeat_pool:
            repeat_pool[mode].sort(
                key=lambda candidate: (
                    SOURCE_PRIORITY.get(candidate.source_label, 0),
                    candidate.quality_score,
                    candidate.unique_article_refs,
                ),
                reverse=True,
            )

        for mode, target_count in target_counts.items():
            deficit = max(0, target_count - unique_mode_counts.get(mode, 0))
            if deficit <= 0:
                continue
            pool = repeat_pool.get(mode, [])
            if not pool:
                continue
            for index in range(deficit):
                candidate = pool[index % len(pool)]
                split_rows["train"].append(candidate.row)
                split_manifest["train"].append(
                    {
                        "signature": candidate.signature,
                        "mode": candidate.mode,
                        "source_label": candidate.source_label,
                        "source_split": candidate.source_split,
                        "quality_score": candidate.quality_score,
                        "unique_article_refs": candidate.unique_article_refs,
                        "section_coverage": candidate.section_coverage,
                        "oversampled": True,
                        "oversample_index": index + 1,
                    }
                )
                oversample_added[mode] += 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in split_rows.items():
        write_jsonl(args.output_dir / f"{split}.jsonl", rows)
        (args.output_dir / f"{split}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(rows),
                    "modes": summarize_rows(rows),
                    "rows": split_manifest[split],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    manifest = {
        "dataset_version": "final_legal_modes_v9_master",
        "examples_total": sum(len(rows) for rows in split_rows.values()),
        "unique_examples_total": len(final_candidates),
        "splits": {split: len(rows) for split, rows in split_rows.items()},
        "modes_total": dict(
            Counter(
                item["mode"]
                for manifest_rows in split_manifest.values()
                for item in manifest_rows
            )
        ),
        "source_stats": source_stats,
        "selected_primary_unique": len(primary_best),
        "selected_supplements": len(selected_supplements),
        "selected_supplement_modes": dict(supplement_counts),
        "oversample_added": dict(oversample_added),
        "target_total": args.target_total,
        "supplement_mode_caps": SUPPLEMENT_MODE_CAPS,
        "selection_rules": {
            "require_full_section_coverage": True,
            "require_article_reference": True,
            "reject_repeated_lines": True,
            "reject_block_patterns": True,
            "split_policy": "signature hash => 80/10/10",
            "oversample_policy": "train_only weighted repetition toward target_total",
        },
    }
    (args.output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
