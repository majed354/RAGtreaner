"""Build deterministic composite package-router training questions from gold rows."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1" / "train.jsonl"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "composite_mixup_train.jsonl"
)

MIXUP_TEMPLATES = (
    "ملف مركب للجمع فقط. افصل كل محور واجمع حزمته النظامية كاملة: أولا: {first} ثانيا: {second}",
    "قضية فيها مسألتان مستقلتان داخل سؤال واحد. لا تجعل المجال الأقوى يسقط الآخر: {first} وفي محور آخر: {second}",
    "اختبار جمع متعدد المحاور: {first} ثم نشأت مسألة إضافية منفصلة: {second} اجمع أنظمة ولوائح كل محور.",
    "يريد المراجع مصادر كل جزء من الواقعة لا جوابا نهائيا: {first} وكذلك: {second}",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def compatible(left: dict[str, Any], right: dict[str, Any], max_labels: int) -> bool:
    if left.get("domain") == right.get("domain"):
        return False
    if left.get("scenario_family_id") and left.get("scenario_family_id") == right.get("scenario_family_id"):
        return False
    labels = set(left.get("all_labels") or []) | set(right.get("all_labels") or [])
    return 1 < len(labels) <= max_labels


def composite_row(index: int, left: dict[str, Any], right: dict[str, Any], template: str) -> dict[str, Any]:
    core = unique([*(left.get("core_labels") or []), *(right.get("core_labels") or [])])
    companions = unique([*(left.get("companion_labels") or []), *(right.get("companion_labels") or [])])
    labels = unique([*core, *companions])
    return {
        "question_id": f"router_mixup_v1_{index:05d}",
        "question": template.format(first=str(left["question"]), second=str(right["question"])),
        "split": "train",
        "router_role": "train_mixup",
        "domain": "router_composite_mixup",
        "benchmark_category": "package_router_composite_mixup",
        "scenario_family_id": f"mixup::{left.get('domain') or 'unknown'}::{right.get('domain') or 'unknown'}",
        "source_note": "package_router_composite_mixup_v1",
        "source_question_ids": [left.get("question_id"), right.get("question_id")],
        "source_domains": [left.get("domain"), right.get("domain")],
        "core_labels": core,
        "companion_labels": companions,
        "all_labels": labels,
        "optional_labels": unique([*(left.get("optional_labels") or []), *(right.get("optional_labels") or [])]),
        "excluded_labels": unique([*(left.get("excluded_labels") or []), *(right.get("excluded_labels") or [])]),
    }


def append_pair(
    out: list[dict[str, Any]],
    seen_pairs: set[tuple[str, str]],
    left: dict[str, Any],
    right: dict[str, Any],
    template: str,
    max_labels: int,
    source_note: str,
) -> bool:
    pair = tuple(sorted((str(left.get("question_id") or ""), str(right.get("question_id") or ""))))
    if pair in seen_pairs or not compatible(left, right, max_labels):
        return False
    seen_pairs.add(pair)
    row = composite_row(len(out) + 1, left, right, template)
    row["source_note"] = source_note
    out.append(row)
    return True


def append_rare_label_mixups(
    out: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    seen_pairs: set[tuple[str, str]],
    rng: random.Random,
    args: argparse.Namespace,
) -> dict[str, int]:
    label_counts = Counter(label for row in rows for label in row.get("all_labels") or [])
    rows_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for label in row.get("all_labels") or []:
            rows_by_label[str(label)].append(row)

    rare_labels = [
        label
        for label, count in sorted(label_counts.items(), key=lambda item: (item[1], item[0]))
        if 0 < count < args.rare_threshold
    ]
    built_by_label: dict[str, int] = {}
    for label in rare_labels:
        built = 0
        label_rows = rows_by_label[label]
        max_attempts = max(args.rare_cases_per_label * 30, 100)
        for attempt in range(max_attempts):
            if built >= args.rare_cases_per_label:
                break
            left = label_rows[attempt % len(label_rows)]
            right = rng.choice(rows)
            if append_pair(
                out,
                seen_pairs,
                left,
                right,
                MIXUP_TEMPLATES[len(out) % len(MIXUP_TEMPLATES)],
                args.max_labels,
                "package_router_rare_label_mixup_v1",
            ):
                built += 1
        if built:
            built_by_label[label] = built
    return built_by_label


def build(args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(args.seed)
    rows = [
        row
        for row in load_jsonl(args.input)
        if row.get("question")
        and row.get("all_labels")
        and row.get("source_note") != "compound_issue_stress_v4"
        and len(row.get("all_labels") or []) <= args.max_source_labels
    ]
    if len(rows) < 2:
        raise SystemExit(f"not enough router rows in {args.input}")

    out: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    attempts = 0
    max_attempts = max(args.cases * 30, 1000)
    while len(out) < args.cases and attempts < max_attempts:
        attempts += 1
        left, right = rng.sample(rows, 2)
        append_pair(
            out,
            seen_pairs,
            left,
            right,
            MIXUP_TEMPLATES[len(out) % len(MIXUP_TEMPLATES)],
            args.max_labels,
            "package_router_composite_mixup_v1",
        )

    if len(out) < args.cases:
        raise SystemExit(f"built only {len(out)} of requested {args.cases} mixup rows after {attempts} attempts")
    built_by_rare_label = append_rare_label_mixups(out, rows, seen_pairs, rng, args)
    write_jsonl(args.output, out)
    label_counts = [len(row["all_labels"]) for row in out]
    return {
        "status": "ok",
        "input": str(args.input),
        "output": str(args.output),
        "source_rows": len(rows),
        "mixup_rows": len(out),
        "random_mixup_rows": args.cases,
        "rare_mixup_rows": sum(built_by_rare_label.values()),
        "rare_labels_augmented": len(built_by_rare_label),
        "seed": args.seed,
        "max_source_labels": args.max_source_labels,
        "max_labels": args.max_labels,
        "min_output_labels": min(label_counts),
        "max_output_labels": max(label_counts),
        "mean_output_labels": round(sum(label_counts) / len(label_counts), 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cases", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument("--max-source-labels", type=int, default=12)
    parser.add_argument("--max-labels", type=int, default=22)
    parser.add_argument("--rare-threshold", type=int, default=30)
    parser.add_argument("--rare-cases-per-label", type=int, default=28)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
