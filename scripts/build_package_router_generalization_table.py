"""Build a broad package-router training table from the full legal corpus.

This dataset is intentionally not a new manual benchmark.  It is a structured
training surface for the collection router: regulation titles, stripped titles,
field aliases, companion-closure edges, and existing issue bundles are converted
into user-like retrieval questions so the router can see every onboarded
regulation, not only the labels that happened to appear in the hand-written gold
set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.engine import (  # noqa: E402
    DEFAULT_COMPANION_REGULATIONS_BY_CORE,
    FIELD_REGULATION_PACKAGES,
    ISSUE_AXIS_BUNDLES,
    LEGAL_DOCUMENT_BUNDLES,
    REGULATION_TITLE_OVERRIDES,
    _dedupe,
    _strip_regulation_prefixes,
)


DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_generalization_table_v1.jsonl"
)

SURFACE_PREFIXES = (
    "اجمع كل النصوص السعودية المتعلقة بـ",
    "ما الأنظمة واللوائح الواجبة التطبيق على واقعة تتعلق بـ",
    "أحتاج الحزمة النظامية الكاملة في موضوع",
    "وش النظام واللائحة المناسبة إذا كانت القضية عن",
    "قضية مركبة، لا تفوت المرجع الخاص بموضوع",
    "في 2026 ظهرت واقعة عملية عنوانها",
)

COMPOUND_CONNECTORS = (
    "مع الإثبات والاختصاص عند وجود نزاع.",
    "مع اللوائح التنفيذية والضوابط القطاعية إن وجدت.",
    "وافصل النظام الأساسي عن اللائحة والأنظمة المساندة.",
    "لا تكتف بالمصدر العام إذا كان هناك نظام خاص مباشر.",
)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_corpus_titles(path: Path) -> dict[str, str]:
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
    titles.update({slug: title for slug, title in REGULATION_TITLE_OVERRIDES.items() if slug and title})
    return dict(sorted(titles.items()))


def companion_labels(core_labels: list[str]) -> list[str]:
    companions: list[str] = []
    for slug in core_labels:
        companions.extend(DEFAULT_COMPANION_REGULATIONS_BY_CORE.get(slug, ()))
    return [slug for slug in _dedupe(companions) if slug not in set(core_labels)]


def make_row(
    index: int,
    question: str,
    core: list[str],
    companions: list[str] | None = None,
    *,
    source_note: str,
    scenario_family_id: str,
    excluded: list[str] | None = None,
) -> dict[str, Any]:
    clean_core = [slug for slug in _dedupe(core) if slug]
    clean_companions = [slug for slug in _dedupe(companions or []) if slug and slug not in set(clean_core)]
    return {
        "question_id": f"router_generalization_v1_{index:05d}",
        "question": " ".join(question.split()),
        "split": "train",
        "router_role": "train_generalization_table",
        "domain": "router_generalization",
        "benchmark_category": "package_router_generalization_table",
        "scenario_family_id": scenario_family_id,
        "source_note": source_note,
        "core_labels": clean_core,
        "companion_labels": clean_companions,
        "all_labels": _dedupe([*clean_core, *clean_companions]),
        "optional_labels": [],
        "excluded_labels": excluded or [],
    }


def title_surfaces(title: str) -> list[str]:
    stripped = _strip_regulation_prefixes(title)
    surfaces = [title]
    if stripped and stripped != title:
        surfaces.extend(
            [
                stripped,
                f"أحكام {stripped}",
                f"مخالفات {stripped}",
                f"إجراءات {stripped}",
            ]
        )
    return [item for item in _dedupe(surfaces) if len(item) >= 3]


def build_title_rows(titles: dict[str, str], start: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    index = start
    for slug, title in titles.items():
        core = [slug]
        companions = companion_labels(core)
        for surface in title_surfaces(title):
            for prefix in SURFACE_PREFIXES[:4]:
                index += 1
                rows.append(
                    make_row(
                        index,
                        f"{prefix} {surface}. {COMPOUND_CONNECTORS[index % len(COMPOUND_CONNECTORS)]}",
                        core,
                        companions,
                        source_note="corpus_title_surface_v1",
                        scenario_family_id=f"title::{slug}",
                    )
                )
    return rows, index


def build_field_rows(start: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    index = start
    for package in FIELD_REGULATION_PACKAGES:
        core = list(package.get("core") or [])
        companions = list(package.get("companions") or []) or companion_labels(core)
        for field in package.get("fields") or ():
            for prefix in SURFACE_PREFIXES:
                index += 1
                rows.append(
                    make_row(
                        index,
                        f"{prefix} {field}. {COMPOUND_CONNECTORS[index % len(COMPOUND_CONNECTORS)]}",
                        core,
                        companions,
                        source_note="field_alias_package_surface_v1",
                        scenario_family_id=f"field::{field}",
                    )
                )
    return rows, index


def build_companion_closure_rows(titles: dict[str, str], start: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    index = start
    for core_slug, companions_tuple in sorted(DEFAULT_COMPANION_REGULATIONS_BY_CORE.items()):
        if core_slug not in titles and core_slug not in REGULATION_TITLE_OVERRIDES:
            continue
        core_title = titles.get(core_slug, REGULATION_TITLE_OVERRIDES.get(core_slug, core_slug))
        companion_titles = [
            titles.get(slug, REGULATION_TITLE_OVERRIDES.get(slug, slug))
            for slug in companions_tuple
        ]
        companion_text = "، ".join(companion_titles[:6])
        for template in (
            f"إذا حضرت واقعة عن {core_title} فاجمع معها الحزمة المرافقة، خصوصا: {companion_text}.",
            f"سؤال جمع فقط: {core_title} مع اللوائح والأنظمة المساندة التي لا ينبغي أن تسقط.",
            f"ملف مركب محوره {core_title}. المطلوب إكمال الحزمة لا مجرد ذكر النظام.",
        ):
            index += 1
            rows.append(
                make_row(
                    index,
                    template,
                    [core_slug],
                    list(companions_tuple),
                    source_note="companion_closure_edge_v1",
                    scenario_family_id=f"companion::{core_slug}",
                )
            )
    return rows, index


def bundle_question(bundle: dict[str, Any], titles: dict[str, str]) -> str:
    patterns = [
        str(item)
        for item in [*bundle.get("all_patterns", ()), *bundle.get("any_patterns", ())]
        if str(item).strip()
    ]
    pattern_text = "، ".join(patterns[:10])
    core_titles = [
        titles.get(slug, REGULATION_TITLE_OVERRIDES.get(slug, slug))
        for slug in bundle.get("core_regulations") or ()
    ]
    if pattern_text:
        return f"واقعة مركبة تشمل: {pattern_text}. اجمع الحزمة النظامية الكاملة دون إسقاط اللوائح."
    return f"واقعة في محور {' و'.join(core_titles)}. اجمع النظام واللائحة والأنظمة المساندة."


def build_bundle_rows(titles: dict[str, str], start: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    index = start
    for bundle in [*ISSUE_AXIS_BUNDLES, *LEGAL_DOCUMENT_BUNDLES]:
        core = list(bundle.get("core_regulations") or [])
        companions = list(bundle.get("companion_regulations") or [])
        if not core and not companions:
            continue
        index += 1
        rows.append(
            make_row(
                index,
                bundle_question(bundle, titles),
                core,
                companions,
                source_note="static_bundle_surface_v1",
                scenario_family_id=f"bundle::{bundle.get('id')}",
                excluded=list(bundle.get("excluded_patterns") or []),
            )
        )
    return rows, index


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for row in rows:
        key = (row["question"], tuple(row["all_labels"]))
        by_key.setdefault(key, row)
    out = []
    for index, row in enumerate(by_key.values(), start=1):
        copied = dict(row)
        copied["question_id"] = f"router_generalization_v1_{index:05d}"
        out.append(copied)
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(str(label) for row in rows for label in row.get("all_labels") or [])
    sources = Counter(str(row.get("source_note") or "") for row in rows)
    return {
        "rows": len(rows),
        "unique_labels": len(labels),
        "source_notes": dict(sources),
        "least_supported_labels": labels.most_common()[:-16:-1],
        "top_supported_labels": labels.most_common(16),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    titles = load_corpus_titles(args.chunks)
    rows: list[dict[str, Any]] = []
    index = 0
    for builder in (
        lambda start: build_title_rows(titles, start),
        build_field_rows,
        lambda start: build_companion_closure_rows(titles, start),
        lambda start: build_bundle_rows(titles, start),
    ):
        new_rows, index = builder(index)
        rows.extend(new_rows)
    rows = dedupe_rows(rows)
    write_jsonl(args.output, rows)
    manifest = {
        "status": "ok",
        "chunks": str(args.chunks),
        "output": str(args.output),
        "summary": summarize(rows),
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
