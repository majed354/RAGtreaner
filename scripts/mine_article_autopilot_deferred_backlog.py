"""Mine deferred article-autopilot failures into targeted corpus-surface support.

The miner does not train on holdout questions.  It uses deferred failures only
to identify repeated missing regulation/article pairs, then selects existing
corpus article-surface rows for those pairs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT_DIR = ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_BACKLOG = AUTOPILOT_DIR / "deferred_improvement_backlog.jsonl"
DEFAULT_SURFACE_ROWS = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_article_surface_table_v1.jsonl"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def parse_pair(raw: Any) -> tuple[str, int] | None:
    text = str(raw or "").strip()
    if ":" not in text:
        return None
    slug, article_raw = text.rsplit(":", 1)
    try:
        article = int(article_raw)
    except Exception:
        return None
    if not slug or article <= 0:
        return None
    return slug, article


def surface_pair(row: dict[str, Any]) -> str | None:
    labels = [str(item) for item in row.get("core_labels") or [] if item]
    if not labels:
        return None
    try:
        article = int(row.get("source_article_index") or 0)
    except Exception:
        return None
    if article <= 0:
        return None
    return f"{labels[0]}:{article}"


def mine(args: argparse.Namespace) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    target_pairs_path = args.target_pairs_output or output_dir / f"deferred_backlog_target_pairs_{timestamp}.jsonl"
    target_surface_path = args.target_surface_output or output_dir / f"deferred_backlog_target_surface_rows_{timestamp}.jsonl"
    report_path = args.report_output or output_dir / f"deferred_backlog_mining_report_{timestamp}.json"

    backlog_rows = load_jsonl(args.backlog)
    if args.recent_limit > 0:
        backlog_rows = backlog_rows[-args.recent_limit :]

    pair_records: Counter[str] = Counter()
    pair_questions: dict[str, set[str]] = defaultdict(set)
    pair_domains: dict[str, Counter[str]] = defaultdict(Counter)
    pair_axes: dict[str, Counter[str]] = defaultdict(Counter)
    pair_causes: dict[str, Counter[str]] = defaultdict(Counter)
    pair_reasons: dict[str, Counter[str]] = defaultdict(Counter)
    record_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for index, row in enumerate(backlog_rows):
        record_type = str(row.get("record_type") or "legacy_case")
        record_types[record_type] += 1
        reason = str(row.get("reason") or "")
        reasons[reason] += 1
        pairs = list(row.get("missing_article_pairs") or [])
        pairs.extend(row.get("unrouted_expected_article_pairs") or [])
        qid = str(row.get("question_id") or "")
        if not qid:
            qid = f"{row.get('source_manifest') or 'unknown'}::{index}"
        domain = str(row.get("domain") or "")
        cause = str(row.get("root_cause") or "")
        axes = [str(item) for item in row.get("failed_axes") or [] if item]
        for raw_pair in pairs:
            parsed = parse_pair(raw_pair)
            if parsed is None:
                continue
            slug, article = parsed
            pair_key = f"{slug}:{article}"
            pair_records[pair_key] += 1
            pair_questions[pair_key].add(qid)
            if domain:
                pair_domains[pair_key][domain] += 1
            if cause:
                pair_causes[pair_key][cause] += 1
            if reason:
                pair_reasons[pair_key][reason] += 1
            for axis in axes:
                pair_axes[pair_key][axis] += 1

    ranked_pairs = sorted(
        pair_records,
        key=lambda pair: (len(pair_questions[pair]), pair_records[pair], pair),
        reverse=True,
    )
    target_rows: list[dict[str, Any]] = []
    for pair in ranked_pairs:
        unique_questions = len(pair_questions[pair])
        if unique_questions < args.min_unique_questions and pair_records[pair] < args.min_records:
            continue
        parsed = parse_pair(pair)
        if parsed is None:
            continue
        slug, article = parsed
        target_rows.append(
            {
                "pair": pair,
                "slug": slug,
                "article": article,
                "record_count": pair_records[pair],
                "unique_question_count": unique_questions,
                "top_domains": [
                    {"domain": key, "count": value}
                    for key, value in pair_domains[pair].most_common(args.summary_top_items)
                ],
                "top_axes": [
                    {"axis": key, "count": value}
                    for key, value in pair_axes[pair].most_common(args.summary_top_items)
                ],
                "root_causes": [
                    {"root_cause": key, "count": value}
                    for key, value in pair_causes[pair].most_common(args.summary_top_items)
                ],
                "reasons": [
                    {"reason": key, "count": value}
                    for key, value in pair_reasons[pair].most_common(args.summary_top_items)
                ],
            }
        )
        if len(target_rows) >= args.max_pairs:
            break

    target_pair_set = {str(row["pair"]) for row in target_rows}
    selected_surface_rows: list[dict[str, Any]] = []
    per_pair: Counter[str] = Counter()
    for row in load_jsonl(args.surface_rows):
        pair = surface_pair(row)
        if not pair or pair not in target_pair_set:
            continue
        if args.max_surface_rows_per_pair and per_pair[pair] >= args.max_surface_rows_per_pair:
            continue
        selected_surface_rows.append(row)
        per_pair[pair] += 1
        if args.max_surface_rows and len(selected_surface_rows) >= args.max_surface_rows:
            break

    write_jsonl(target_pairs_path, target_rows)
    write_jsonl(target_surface_path, selected_surface_rows)

    surface_covered_pairs = set(per_pair)
    top_clusters = [
        {
            **row,
            "surface_rows": per_pair.get(str(row["pair"]), 0),
            "surface_available": str(row["pair"]) in surface_covered_pairs,
        }
        for row in target_rows[: args.report_top_pairs]
    ]
    report = {
        "status": "ok",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method": "deferred backlog dedupe -> frequent missing article pairs -> targeted corpus article-surface rows",
        "backlog_path": str(args.backlog),
        "surface_rows_path": str(args.surface_rows),
        "backlog_rows_read": len(backlog_rows),
        "record_types": dict(record_types),
        "reasons": dict(reasons.most_common(args.summary_top_items)),
        "unique_missing_pairs": len(pair_records),
        "target_pair_count": len(target_rows),
        "target_pairs_path": str(target_pairs_path),
        "target_surface_rows_path": str(target_surface_path),
        "target_surface_rows": len(selected_surface_rows),
        "surface_covered_pair_count": len(surface_covered_pairs),
        "surface_missing_pair_count": max(0, len(target_rows) - len(surface_covered_pairs)),
        "min_unique_questions": args.min_unique_questions,
        "min_records": args.min_records,
        "max_pairs": args.max_pairs,
        "max_surface_rows": args.max_surface_rows,
        "max_surface_rows_per_pair": args.max_surface_rows_per_pair,
        "top_clusters": top_clusters,
        "training_guard": "holdout questions are not emitted; only existing corpus-surface rows are selected.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backlog", type=Path, default=DEFAULT_BACKLOG)
    parser.add_argument("--surface-rows", type=Path, default=DEFAULT_SURFACE_ROWS)
    parser.add_argument("--output-dir", type=Path, default=AUTOPILOT_DIR)
    parser.add_argument("--target-pairs-output", type=Path, default=None)
    parser.add_argument("--target-surface-output", type=Path, default=None)
    parser.add_argument("--report-output", type=Path, default=None)
    parser.add_argument("--recent-limit", type=int, default=50000)
    parser.add_argument("--min-unique-questions", type=int, default=2)
    parser.add_argument("--min-records", type=int, default=8)
    parser.add_argument("--max-pairs", type=int, default=300)
    parser.add_argument("--max-surface-rows", type=int, default=1200)
    parser.add_argument("--max-surface-rows-per-pair", type=int, default=4)
    parser.add_argument("--summary-top-items", type=int, default=5)
    parser.add_argument("--report-top-pairs", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(mine(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
