"""Build a multi-label Saudi legal package-router dataset from gold recall cases."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v4_7000" / "gold_package_recall_7000_v4.jsonl"
DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"


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


def title_catalog(path: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            slug = str(row.get("regulation_slug") or "").strip()
            title = str(row.get("regulation_title_ar") or "").strip()
            if slug and title:
                titles.setdefault(slug, title)
    return titles


def router_row(case: dict[str, Any], role: str) -> dict[str, Any]:
    core = [str(slug) for slug in case.get("required_core_regulations") or [] if slug]
    companions = [str(slug) for slug in case.get("required_companion_regulations") or [] if slug]
    return {
        "question_id": str(case.get("question_id") or ""),
        "question": str(case.get("question") or ""),
        "split": str(case.get("split") or role),
        "router_role": role,
        "domain": case.get("domain"),
        "benchmark_category": case.get("benchmark_category"),
        "scenario_family_id": case.get("scenario_family_id"),
        "source_note": case.get("source_note"),
        "core_labels": list(dict.fromkeys(core)),
        "companion_labels": list(dict.fromkeys(companions)),
        "all_labels": list(dict.fromkeys([*core, *companions])),
        "optional_labels": [str(slug) for slug in case.get("optional_regulations") or [] if slug],
        "excluded_labels": [str(slug) for slug in case.get("excluded_regulations") or [] if slug],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter()
    core = Counter()
    companions = Counter()
    domains = Counter()
    families = Counter()
    source_notes = Counter()
    for row in rows:
        labels.update(row["all_labels"])
        core.update(row["core_labels"])
        companions.update(row["companion_labels"])
        domains[str(row.get("domain") or "")] += 1
        families[str(row.get("scenario_family_id") or "")] += 1
        source_notes[str(row.get("source_note") or "")] += 1
    return {
        "cases": len(rows),
        "unique_labels": len(labels),
        "unique_core_labels": len(core),
        "unique_companion_labels": len(companions),
        "unique_domains": len([item for item in domains if item]),
        "unique_scenario_families": len([item for item in families if item]),
        "top_labels": labels.most_common(20),
        "top_core_labels": core.most_common(20),
        "top_companion_labels": companions.most_common(20),
        "source_notes": dict(source_notes),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.cases)
    titles = title_catalog(args.chunks)
    by_role: dict[str, list[dict[str, Any]]] = {"train": [], "heldout": []}
    all_labels: set[str] = set()

    train_splits = set(args.train_splits)
    heldout_splits = set(args.heldout_splits)
    for case in cases:
        split = str(case.get("split") or "")
        role = "train" if split in train_splits else ("heldout" if split in heldout_splits else "skip")
        if role == "skip":
            continue
        row = router_row(case, role)
        if not row["question"] or not row["all_labels"]:
            continue
        by_role[role].append(row)
        all_labels.update(row["all_labels"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "train.jsonl", by_role["train"])
    write_jsonl(args.output_dir / "heldout.jsonl", by_role["heldout"])
    write_jsonl(args.output_dir / "all_router_rows.jsonl", [*by_role["train"], *by_role["heldout"]])

    label_catalog = [
        {"slug": slug, "title_ar": titles.get(slug, slug)}
        for slug in sorted(all_labels)
    ]
    (args.output_dir / "label_catalog.json").write_text(
        json.dumps(label_catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = {
        "dataset": args.dataset_name,
        "cases_path": str(args.cases),
        "chunks_path": str(args.chunks),
        "split_policy": {
            "train_splits": args.train_splits,
            "heldout_splits": args.heldout_splits,
            "heldout_used_for_fit": False,
            "note": "Scenario families may still have authored variants across gold splits; use manual external slices for out-of-gold audit.",
        },
        "roles": {
            "train": summarize(by_role["train"]),
            "heldout": summarize(by_role["heldout"]),
        },
        "label_catalog_size": len(label_catalog),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dataset-name", default="saudi_legal_package_router_v1")
    parser.add_argument("--train-splits", nargs="+", default=["dev", "regression"])
    parser.add_argument("--heldout-splits", nargs="+", default=["heldout"])
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
