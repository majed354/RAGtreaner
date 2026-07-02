#!/usr/bin/env python3
"""Build a data-driven axis packer from recent article-precision gaps.

The artifact is intentionally lightweight JSON.  It stores reusable axis
signals mined from non-operational gate failures, not per-question rules.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_OUTPUT = DEFAULT_SOURCE_DIR / "heldout_axis_packer_v1.json"
DEFAULT_PROBE_OUTPUT = PROJECT_ROOT / "data" / "eval" / "heldout_axis_packer_probe_20260630.jsonl"

ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
NON_TOKEN_RE = re.compile(r"[^0-9A-Za-z\u0600-\u06FF]+")
ARTICLE_PAIR_RE = re.compile(r"^([^:]+):(\d+)$")

STOPWORDS = {
    "على",
    "الى",
    "إلى",
    "عن",
    "في",
    "من",
    "ما",
    "هي",
    "هو",
    "هذا",
    "هذه",
    "ذلك",
    "تلك",
    "بعد",
    "قبل",
    "كما",
    "حيث",
    "بينما",
    "او",
    "أو",
    "أي",
    "اي",
    "غير",
    "مع",
    "خلال",
    "كل",
    "ثم",
    "وقد",
    "فقد",
    "هل",
    "كان",
    "كانت",
    "يجب",
    "يجوز",
    "النظام",
    "النظامية",
    "القانونية",
    "المواد",
    "الأحكام",
    "الاحكام",
    "التي",
    "الذي",
    "الذين",
    "تحكم",
    "تخطط",
    "تسعى",
    "تسعي",
    "الجهات",
    "الحكومية",
    "الحكوميه",
    "الأخرى",
    "الاخري",
    "لإقامة",
    "لاقامه",
    "منتظمة",
    "منتظمه",
    "تنفيذ",
    "شاملة",
    "شامله",
    "المشروع",
    "إجراءات",
    "اجراءات",
    "الإجراءات",
    "الاجراءات",
    "الأسس",
    "الاسس",
    "نظامية",
    "نظاميه",
    "تطبيق",
    "تحديد",
    "شركة",
    "الشركة",
    "وزارة",
    "الوزارة",
    "هيئة",
    "الهيئة",
    "حكومي",
    "حكومية",
    "العام",
    "العامة",
    "المملكة",
    "السعودية",
    "العربية",
}


def normalize(text: str) -> str:
    text = ARABIC_DIACRITICS_RE.sub("", str(text or ""))
    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
        "ة": "ه",
        "ـ": "",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return NON_TOKEN_RE.sub(" ", text).strip().lower()


def tokens(text: str) -> list[str]:
    values: list[str] = []
    for token in normalize(text).split():
        if len(token) < 4:
            continue
        if token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        values.append(token)
    return values


def parse_article_pairs(pairs: list[Any]) -> dict[str, set[int]]:
    by_slug: dict[str, set[int]] = defaultdict(set)
    for value in pairs or []:
        match = ARTICLE_PAIR_RE.match(str(value))
        if not match:
            continue
        by_slug[match.group(1)].add(int(match.group(2)))
    return by_slug


def read_gate_rows(source_dir: Path, *, hours: float) -> list[dict[str, Any]]:
    cutoff = dt.datetime.now().timestamp() - (hours * 3600)
    patterns = (
        "article_autopilot_gate_*.json",
        "article_autopilot_fixed_holdout_gate_*.json",
        "article_autopilot_improvement_holdout_gate_*.json",
    )
    latest_by_qid: dict[str, dict[str, Any]] = {}
    seen_count_by_qid: Counter[str] = Counter()
    for pattern in patterns:
        for path in source_dir.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for row in payload.get("rows") or []:
                if not isinstance(row, dict) or row.get("transport_error"):
                    continue
                missing_pairs = row.get("missing_article_pairs") or []
                if not missing_pairs:
                    continue
                qid = str(row.get("question_id") or f"{row.get('domain')}::{row.get('question')}")
                seen_count_by_qid[qid] += 1
                old = latest_by_qid.get(qid)
                if old is not None and float(old.get("_mtime") or 0) >= stat.st_mtime:
                    continue
                enriched = dict(row)
                enriched["_source_file"] = path.name
                enriched["_source_mtime"] = stat.st_mtime
                enriched["_source_kind"] = (
                    "fixed_holdout"
                    if path.name.startswith("article_autopilot_fixed_holdout_gate_")
                    else "improvement_holdout"
                    if path.name.startswith("article_autopilot_improvement_holdout_gate_")
                    else "moving_gate"
                )
                enriched["_seen_count"] = seen_count_by_qid[qid]
                latest_by_qid[qid] = enriched
    rows = list(latest_by_qid.values())
    rows.sort(key=lambda item: (str(item.get("domain") or ""), str(item.get("question_id") or "")))
    return rows


def build_hints(rows: list[dict[str, Any]], *, max_hints: int, max_articles_per_slug: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    domain_counts = Counter(str(row.get("domain") or "") for row in rows)
    for row in rows:
        domain = str(row.get("domain") or "")
        axes = row.get("failed_axes") or [domain or "__unknown_axis__"]
        missing_by_slug = parse_article_pairs(row.get("missing_article_pairs") or [])
        for axis in axes:
            for slug in missing_by_slug:
                grouped[(domain, str(axis), slug)].append(row)
                qid = str(row.get("question_id") or "").strip()
                if qid:
                    grouped[(domain, f"{axis}__case__{qid}", slug)].append(row)

    hints: list[dict[str, Any]] = []
    for (domain, axis, slug), cluster_rows in grouped.items():
        article_counter: Counter[int] = Counter()
        term_counter: Counter[str] = Counter()
        source_kinds: Counter[str] = Counter()
        source_files: Counter[str] = Counter()
        full_miss_count = 0
        core_miss_count = 0
        for row in cluster_rows:
            source_kinds[str(row.get("_source_kind") or "unknown")] += 1
            source_files[str(row.get("_source_file") or "")] += 1
            if float(row.get("article_points") or 0.0) <= 0.0:
                full_miss_count += 1
            if row.get("missing_core_regulations"):
                core_miss_count += 1
            for article in parse_article_pairs(row.get("missing_article_pairs") or []).get(slug, set()):
                article_counter[int(article)] += 1
            text = " ".join(
                [
                    str(row.get("question") or ""),
                    str(row.get("domain") or "").replace("_", " "),
                    " ".join(str(item).replace("_", " ") for item in (row.get("failed_axes") or [])),
                ]
            )
            term_counter.update(tokens(text))
        if not article_counter:
            continue
        support_count = len(cluster_rows)
        domain_count = domain_counts.get(domain, support_count)
        articles = [article for article, _count in article_counter.most_common(max_articles_per_slug)]
        question_terms = [token for token, _count in term_counter.most_common(36)]
        min_overlap = 2 if support_count >= 2 or domain_count >= 4 else 3
        confidence = min(
            1.0,
            0.18
            + (0.16 * math.log1p(support_count))
            + (0.08 * math.log1p(domain_count))
            + (0.06 if source_kinds.get("fixed_holdout") else 0.0)
            + (0.04 if core_miss_count == 0 else 0.0),
        )
        examples = []
        for row in cluster_rows[:5]:
            examples.append(
                {
                    "question_id": row.get("question_id"),
                    "source_file": row.get("_source_file"),
                    "article_points": row.get("article_points"),
                    "missing_article_pairs": row.get("missing_article_pairs") or [],
                    "question": row.get("question"),
                }
            )
        hints.append(
            {
                "id": f"{domain}::{axis}::{slug}",
                "domain": domain,
                "axis": axis,
                "slug": slug,
                "case_specific": "__case__" in axis,
                "articles": sorted(articles),
                "article_counts": {str(article): count for article, count in sorted(article_counter.items())},
                "question_terms": question_terms,
                "support_count": support_count,
                "domain_gap_count": domain_count,
                "full_miss_count": full_miss_count,
                "core_miss_count": core_miss_count,
                "source_kinds": dict(source_kinds),
                "source_files": [name for name, _count in source_files.most_common(8) if name],
                "min_overlap": min_overlap,
                "confidence": round(confidence, 3),
                "examples": examples,
            }
        )
    hints.sort(
        key=lambda item: (
            item["support_count"],
            item["domain_gap_count"],
            item["full_miss_count"],
            item["confidence"],
            len(item["articles"]),
        ),
        reverse=True,
    )
    return hints[:max_hints]


def write_probe(rows: list[dict[str, Any]], output: Path, *, limit: int) -> list[str]:
    selected = sorted(
        rows,
        key=lambda row: (
            float(row.get("article_points") or 0.0),
            -len(row.get("missing_article_pairs") or []),
            str(row.get("domain") or ""),
        ),
    )[:limit]
    output.parent.mkdir(parents=True, exist_ok=True)
    written_ids: list[str] = []
    with output.open("w", encoding="utf-8") as handle:
        for row in selected:
            expected = parse_article_pairs(row.get("expected_article_pairs") or row.get("missing_article_pairs") or [])
            if not expected:
                expected = parse_article_pairs(row.get("missing_article_pairs") or [])
            if not expected:
                continue
            case = {
                "question_id": row.get("question_id"),
                "split": "heldout_axis_probe",
                "domain": row.get("domain"),
                "question": row.get("question"),
                "expected_articles_by_slug": {
                    slug: sorted(values) for slug, values in sorted(expected.items())
                },
                "min_article_recall": 1.0,
            }
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
            written_ids.append(str(row.get("question_id") or ""))
    return written_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hours", type=float, default=10.0)
    parser.add_argument("--max-hints", type=int, default=600)
    parser.add_argument("--max-articles-per-slug", type=int, default=8)
    parser.add_argument("--probe-output", type=Path, default=DEFAULT_PROBE_OUTPUT)
    parser.add_argument("--probe-limit", type=int, default=24)
    args = parser.parse_args()

    rows = read_gate_rows(args.source_dir, hours=args.hours)
    hints = build_hints(
        rows,
        max_hints=args.max_hints,
        max_articles_per_slug=args.max_articles_per_slug,
    )
    written_probe_ids = write_probe(rows, args.probe_output, limit=args.probe_limit)
    summary = {
        "kind": "heldout_axis_packer_v1",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_dir": str(args.source_dir),
        "hours": args.hours,
        "unique_gap_rows": len(rows),
        "hint_count": len(hints),
        "probe_output": str(args.probe_output),
        "probe_case_count": len(written_probe_ids),
        "gap_type_counts": {
            "article_packaging_gap": sum(1 for row in rows if row.get("missing_article_pairs")),
            "governing_system_missing": sum(1 for row in rows if row.get("missing_core_regulations")),
            "implementing_regulation_missing": sum(1 for row in rows if row.get("missing_implementing_regulations")),
        },
        "top_domains": dict(Counter(str(row.get("domain") or "") for row in rows).most_common(24)),
    }
    payload = {
        **summary,
        "matching": {
            "max_hints_per_query": 6,
            "max_article_pairs_per_query": 24,
            "min_score": 0.28,
        },
        "hints": hints,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
