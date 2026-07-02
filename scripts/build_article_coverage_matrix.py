"""Build an article coverage matrix and generated probe cases.

The matrix turns manually curated article gates into reusable material-level
coverage data. Probe cases are compatible with run_article_precision_gate.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE_CASES = [
    ROOT / "data" / "eval" / "manual_article_precision_gate_20260526.jsonl",
    ROOT / "data" / "eval" / "manual_user_article_precision_slice_20260530.jsonl",
    ROOT / "data" / "eval" / "article_autopilot" / "autopilot_article_precision_bank.jsonl",
]
DEFAULT_MATRIX_OUTPUT = ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"
DEFAULT_PROBES_OUTPUT = ROOT / "data" / "eval" / "article_coverage_matrix_v1_probes.jsonl"
STRUCTURED_BY_REGULATION_DIR = ROOT / "data" / "structured" / "by_regulation"


def pair_key(slug: str, article: int) -> str:
    return f"{slug}:{article}"


def parse_pair(value: str) -> tuple[str, int] | None:
    if ":" not in str(value):
        return None
    slug, raw_article = str(value).rsplit(":", 1)
    try:
        return slug, int(raw_article)
    except Exception:
        return None


def load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                row["_source_file"] = str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
                cases.append(row)
    return cases


def expected_pairs_by_slug(case: dict[str, Any]) -> dict[str, set[int]]:
    raw = case.get("expected_articles_by_slug") or case.get("required_articles_by_slug") or {}
    pairs: dict[str, set[int]] = defaultdict(set)
    for slug, articles in raw.items():
        for article in articles or []:
            try:
                pairs[str(slug)].add(int(article))
            except Exception:
                continue
    return pairs


def axis_pairs(case: dict[str, Any]) -> dict[str, set[tuple[str, int]]]:
    axes: dict[str, set[tuple[str, int]]] = {}
    for axis, values in (case.get("axis_article_pairs") or {}).items():
        pairs: set[tuple[str, int]] = set()
        for value in values or []:
            parsed = parse_pair(str(value))
            if parsed:
                pairs.add(parsed)
        if pairs:
            axes[str(axis)] = pairs
    return axes


def load_article_catalog() -> tuple[dict[str, dict[str, Any]], dict[str, dict[int, dict[str, Any]]]]:
    regulations: dict[str, dict[str, Any]] = {}
    articles: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for path in sorted(STRUCTURED_BY_REGULATION_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metadata = data.get("metadata") or {}
        slug = str(metadata.get("slug") or path.stem)
        regulations[slug] = metadata
        for article in data.get("articles") or []:
            try:
                index = int(article.get("article_index") or 0)
            except Exception:
                continue
            if index:
                articles[slug][index] = article
    return regulations, articles


def group_pairs(pairs: set[tuple[str, int]]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {}
    for slug, article in sorted(pairs):
        grouped.setdefault(slug, []).append(article)
    return grouped


def regulation_expectations_for_axis(case: dict[str, Any], pairs: set[tuple[str, int]], key: str) -> list[str]:
    pair_slugs = {slug for slug, _article in pairs}
    return [slug for slug in case.get(key, []) if slug in pair_slugs]


def build_axis_probe(case: dict[str, Any], axis: str, pairs: set[tuple[str, int]]) -> dict[str, Any]:
    source_qid = str(case["question_id"])
    question = (
        f"{case['question']}\n\n"
        f"محور تدقيق آلي: ركز على محور `{axis}` واجمع المواد الدقيقة المرتبطة بهذا المحور داخل السياق."
    )
    return {
        "question_id": f"matrix_axis__{source_qid}__{axis}",
        "source_question_id": source_qid,
        "split": "matrix",
        "domain": case.get("domain", "uncategorized"),
        "benchmark_category": "article_axis_probe",
        "axis_name": axis,
        "question": question,
        "expected_core_regulations": regulation_expectations_for_axis(case, pairs, "expected_core_regulations"),
        "expected_companion_regulations": regulation_expectations_for_axis(case, pairs, "expected_companion_regulations"),
        "expected_implementing_regulations": regulation_expectations_for_axis(
            case, pairs, "expected_implementing_regulations"
        ),
        "expected_articles_by_slug": group_pairs(pairs),
        "axis_article_pairs": {axis: [pair_key(slug, article) for slug, article in sorted(pairs)]},
        "min_article_recall": float(case.get("min_article_recall", 1.0)),
    }


def build_base_probe(case: dict[str, Any]) -> dict[str, Any]:
    probe = {key: value for key, value in case.items() if not key.startswith("_")}
    probe["question_id"] = f"matrix_base__{case['question_id']}"
    probe["source_question_id"] = case["question_id"]
    probe["split"] = "matrix"
    probe["benchmark_category"] = "article_base_probe"
    return probe


def build_matrix(
    cases: list[dict[str, Any]],
    include_base_probes: bool,
    include_axis_probes: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    regulations_catalog, articles_catalog = load_article_catalog()
    pair_sources: dict[tuple[str, int], dict[str, Any]] = {}
    regulation_sources: dict[str, dict[str, Any]] = defaultdict(lambda: {"articles": set(), "source_cases": set(), "domains": set()})
    axes_rows: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []

    for case in cases:
        qid = str(case["question_id"])
        domain = str(case.get("domain") or "uncategorized")
        by_slug = expected_pairs_by_slug(case)
        if include_base_probes and by_slug:
            probes.append(build_base_probe(case))
        for slug, articles in by_slug.items():
            regulation_sources[slug]["articles"].update(articles)
            regulation_sources[slug]["source_cases"].add(qid)
            regulation_sources[slug]["domains"].add(domain)
            for article in articles:
                item = pair_sources.setdefault(
                    (slug, article),
                    {
                        "regulation_slug": slug,
                        "article_index": article,
                        "source_cases": set(),
                        "domains": set(),
                        "axes": set(),
                    },
                )
                item["source_cases"].add(qid)
                item["domains"].add(domain)
        for axis, pairs in axis_pairs(case).items():
            axis_id = f"{qid}::{axis}"
            axes_rows.append(
                {
                    "axis_id": axis_id,
                    "source_question_id": qid,
                    "domain": domain,
                    "axis": axis,
                    "expected_article_pairs": [pair_key(slug, article) for slug, article in sorted(pairs)],
                }
            )
            if include_axis_probes:
                probes.append(build_axis_probe(case, axis, pairs))
            for slug, article in pairs:
                item = pair_sources.setdefault(
                    (slug, article),
                    {
                        "regulation_slug": slug,
                        "article_index": article,
                        "source_cases": set(),
                        "domains": set(),
                        "axes": set(),
                    },
                )
                item["source_cases"].add(qid)
                item["domains"].add(domain)
                item["axes"].add(axis_id)

    article_pairs: list[dict[str, Any]] = []
    for (slug, article), item in sorted(pair_sources.items()):
        regulation_meta = regulations_catalog.get(slug, {})
        article_meta = articles_catalog.get(slug, {}).get(article, {})
        text = str(article_meta.get("text_for_index") or article_meta.get("text_verbatim") or "")
        article_pairs.append(
            {
                "pair": pair_key(slug, article),
                "regulation_slug": slug,
                "regulation_title_ar": regulation_meta.get("title_ar") or slug,
                "article_index": article,
                "citation_short_ar": article_meta.get("citation_short_ar")
                or f"{regulation_meta.get('title_ar') or slug}، المادة {article}",
                "article_type": article_meta.get("article_type"),
                "legal_function_tags": article_meta.get("legal_function_tags") or [],
                "topic_tags": article_meta.get("topic_tags") or [],
                "text_preview": " ".join(text.split())[:360],
                "source_cases": sorted(item["source_cases"]),
                "domains": sorted(item["domains"]),
                "axes": sorted(item["axes"]),
            }
        )

    regulations = []
    for slug, item in sorted(regulation_sources.items()):
        meta = regulations_catalog.get(slug, {})
        regulations.append(
            {
                "regulation_slug": slug,
                "regulation_title_ar": meta.get("title_ar") or slug,
                "articles": sorted(item["articles"]),
                "article_count": len(item["articles"]),
                "source_cases": sorted(item["source_cases"]),
                "domains": sorted(item["domains"]),
            }
        )

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_case_count": len(cases),
        "source_files": sorted({case.get("_source_file") for case in cases if case.get("_source_file")}),
        "regulation_count": len(regulations),
        "article_pair_count": len(article_pairs),
        "axis_count": len(axes_rows),
        "generated_probe_count": len(probes),
        "domain_counts": dict(Counter(str(case.get("domain") or "uncategorized") for case in cases)),
    }
    matrix = {
        "summary": summary,
        "source_cases": [
            {
                "question_id": case["question_id"],
                "domain": case.get("domain", "uncategorized"),
                "source_file": case.get("_source_file"),
                "expected_article_count": sum(len(values) for values in expected_pairs_by_slug(case).values()),
                "axis_count": len(axis_pairs(case)),
            }
            for case in cases
        ],
        "regulations": regulations,
        "article_pairs": article_pairs,
        "axes": axes_rows,
    }
    return matrix, probes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-cases", type=Path, action="append", default=None)
    parser.add_argument("--matrix-output", type=Path, default=DEFAULT_MATRIX_OUTPUT)
    parser.add_argument("--probes-output", type=Path, default=DEFAULT_PROBES_OUTPUT)
    parser.add_argument("--no-base-probes", action="store_true")
    parser.add_argument("--no-axis-probes", action="store_true")
    args = parser.parse_args()

    source_cases = args.source_cases if args.source_cases else DEFAULT_SOURCE_CASES
    cases = load_cases(source_cases)
    matrix, probes = build_matrix(
        cases,
        include_base_probes=not args.no_base_probes,
        include_axis_probes=not args.no_axis_probes,
    )

    args.matrix_output.parent.mkdir(parents=True, exist_ok=True)
    args.matrix_output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.probes_output.write_text(
        "\n".join(json.dumps(probe, ensure_ascii=False) for probe in probes) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(matrix["summary"], ensure_ascii=False, indent=2))
    print(f"Saved matrix to: {args.matrix_output}")
    print(f"Saved probes to: {args.probes_output}")


if __name__ == "__main__":
    main()
