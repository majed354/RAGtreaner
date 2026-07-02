#!/usr/bin/env python3
"""Build a blind article-precision slice outside the current gap packer.

The slice is blind relative to the heldout-axis packer: it excludes question
IDs seen in packer examples and article pairs already encoded in packer hints.
It then stratifies across common, mid-frequency, and long-tail domains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BANK = PROJECT_ROOT / "data" / "eval" / "article_autopilot" / "autopilot_article_precision_bank.jsonl"
DEFAULT_PACKER = PROJECT_ROOT / "data" / "eval" / "article_autopilot" / "heldout_axis_packer_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "manual_article_precision_blind40_20260630_after_heldout_axis_router.jsonl"


def pair_keys(row: dict[str, Any]) -> set[str]:
    pairs: set[str] = set()
    for slug, articles in (row.get("expected_articles_by_slug") or {}).items():
        for article in articles or []:
            try:
                pairs.add(f"{slug}:{int(article)}")
            except Exception:
                continue
    return pairs


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def packer_exclusions(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    excluded_ids: set[str] = set()
    excluded_pairs: set[str] = set()
    for hint in payload.get("hints") or []:
        slug = str(hint.get("slug") or "")
        for article in hint.get("articles") or []:
            try:
                excluded_pairs.add(f"{slug}:{int(article)}")
            except Exception:
                continue
        for example in hint.get("examples") or []:
            qid = str(example.get("question_id") or "").strip()
            if qid:
                excluded_ids.add(qid)
    return excluded_ids, excluded_pairs


def report_question_ids(eval_dir: Path) -> set[str]:
    patterns = [
        "heldout_axis_packer_probe_20260630*.json",
        "manual_article_precision_*20260630*heldout_axis_router*.json",
        "manual_article_precision_*20260630*coverage_packer*.json",
    ]
    ids: set[str] = set()
    for pattern in patterns:
        for path in eval_dir.glob(pattern):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for row in payload.get("rows") or []:
                qid = str(row.get("question_id") or "").strip()
                if qid:
                    ids.add(qid)
    return ids


def stable_key(value: str, seed: int) -> str:
    return hashlib.sha1(f"{seed}:{value}".encode("utf-8")).hexdigest()


def select_rows(rows: list[dict[str, Any]], *, count: int, seed: int) -> list[dict[str, Any]]:
    domain_counts = Counter(str(row.get("domain") or "uncategorized") for row in rows)
    pair_counts = Counter(pair for row in rows for pair in pair_keys(row))
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        domain = str(row.get("domain") or "uncategorized")
        frequency = domain_counts[domain]
        if frequency >= 40:
            bucket = "common"
        elif frequency <= 5:
            bucket = "long_tail"
        else:
            bucket = "mid"
        candidate = dict(row)
        pairs = pair_keys(candidate)
        candidate["_pair_count_score"] = sum(pair_counts[pair] for pair in pairs)
        candidate["_bucket"] = bucket
        buckets[bucket].append(candidate)

    quotas = {
        "common": max(1, round(count * 0.35)),
        "mid": max(1, round(count * 0.325)),
        "long_tail": max(1, count - round(count * 0.35) - round(count * 0.325)),
    }
    selected: list[dict[str, Any]] = []
    used_domains: set[str] = set()
    used_slugs: set[str] = set()

    def row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        domain = str(row.get("domain") or "")
        pairs = sorted(pair_keys(row))
        first_slug = pairs[0].split(":", 1)[0] if pairs else ""
        return (
            int(row.get("_pair_count_score") or 0),
            abs(len(pairs) - 3),
            domain in used_domains,
            first_slug in used_slugs,
            stable_key(str(row.get("question_id") or row.get("question") or ""), seed),
        )

    def add_from_bucket(name: str, quota: int) -> None:
        nonlocal selected
        pool = sorted(buckets.get(name) or [], key=row_sort_key)
        for row in pool:
            if len(selected) >= count:
                return
            if sum(1 for item in selected if item.get("_bucket") == name) >= quota:
                return
            domain = str(row.get("domain") or "")
            row_slugs = {pair.split(":", 1)[0] for pair in pair_keys(row)}
            if domain in used_domains:
                continue
            if row_slugs & used_slugs:
                continue
            selected.append(row)
            used_domains.add(domain)
            used_slugs.update(row_slugs)

    for bucket_name in ("common", "mid", "long_tail"):
        add_from_bucket(bucket_name, quotas[bucket_name])

    if len(selected) < count:
        remaining = [
            row
            for bucket_rows in buckets.values()
            for row in bucket_rows
            if str(row.get("question_id") or "") not in {str(item.get("question_id") or "") for item in selected}
        ]
        for row in sorted(remaining, key=row_sort_key):
            if len(selected) >= count:
                break
            domain = str(row.get("domain") or "")
            if domain in used_domains and len(used_domains) < count:
                continue
            selected.append(row)
            used_domains.add(domain)
            used_slugs.update(pair.split(":", 1)[0] for pair in pair_keys(row))

    return selected[:count]


def clean_case(row: dict[str, Any]) -> dict[str, Any]:
    case = {
        "question_id": row.get("question_id"),
        "split": "heldout",
        "domain": row.get("domain"),
        "question": row.get("question"),
        "expected_articles_by_slug": row.get("expected_articles_by_slug") or {},
        "expected_core_regulations": row.get("expected_core_regulations") or [],
        "expected_companion_regulations": row.get("expected_companion_regulations") or [],
        "expected_implementing_regulations": row.get("expected_implementing_regulations") or [],
        "axis_article_pairs": row.get("axis_article_pairs") or {},
        "auto_review": row.get("auto_review") or {},
        "min_article_recall": float(row.get("min_article_recall") or 1.0),
    }
    return case


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--packer", type=Path, default=DEFAULT_PACKER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=630)
    args = parser.parse_args()

    packer_ids, packer_pairs = packer_exclusions(args.packer)
    previous_report_ids = report_question_ids(PROJECT_ROOT / "data" / "eval")
    excluded_ids = packer_ids | previous_report_ids
    rows = read_jsonl(args.bank)
    filtered = []
    for row in rows:
        qid = str(row.get("question_id") or "").strip()
        pairs = pair_keys(row)
        if not qid or qid in excluded_ids or not pairs:
            continue
        if pairs & packer_pairs:
            continue
        review = row.get("auto_review") or {}
        if float(review.get("gate_article_points") or 0.0) < 100.0:
            continue
        filtered.append(row)

    selected = select_rows(filtered, count=args.count, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(clean_case(row), ensure_ascii=False) + "\n")

    summary_path = args.output.with_suffix(".summary.json")
    domain_counts = Counter(str(row.get("domain") or "uncategorized") for row in selected)
    pair_total = sum(len(pair_keys(row)) for row in selected)
    summary = {
        "output": str(args.output),
        "count": len(selected),
        "bank_rows": len(rows),
        "filtered_candidates": len(filtered),
        "excluded_question_ids": len(excluded_ids),
        "excluded_packer_pairs": len(packer_pairs),
        "unique_domains": len(domain_counts),
        "domain_counts": dict(domain_counts),
        "expected_pair_count": pair_total,
        "seed": args.seed,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
