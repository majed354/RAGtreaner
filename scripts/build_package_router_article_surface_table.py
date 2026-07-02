"""Build article-derived package-router surfaces from the structured corpus.

This is a collection-generalization artifact, not a manual benchmark.  It turns
each legal article/chunk into user-like package-routing surfaces so package
recall is not limited to hand-written gold questions, titles, or static issue
bundles.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.engine import (  # noqa: E402
    DEFAULT_COMPANION_REGULATIONS_BY_CORE,
    REGULATION_TITLE_OVERRIDES,
    _dedupe,
)


DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_article_surface_table_v1.jsonl"
)

STOPWORDS = {
    "النظام",
    "اللائحة",
    "المادة",
    "الفقرة",
    "الحكم",
    "أحكام",
    "احكام",
    "ذلك",
    "هذه",
    "هذا",
    "التي",
    "الذي",
    "على",
    "إلى",
    "الى",
    "عن",
    "في",
    "من",
    "كل",
    "أو",
    "او",
    "أي",
    "اي",
    "إذا",
    "اذا",
    "وفق",
    "بموجب",
    "يجب",
    "يجوز",
    "تكون",
    "يكون",
    "كان",
    "غير",
    "بما",
    "لها",
    "له",
    "للمحكمة",
}

CONCEPT_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("برمجيات الحاسب", "برمجيات الحاسب الآلي", "البرمجيات المنسوخة"),
        ("برنامج داخلي", "برنامج حاسب", "كود مصدري", "شفرة مصدرية", "نسخة من الكود", "سورس كود"),
    ),
    (
        ("نقل البيانات الشخصية", "خارج المملكة", "جهة خارج المملكة"),
        ("خادم خارج المملكة", "سيرفر خارج المملكة", "مزود سحابي أجنبي", "استضافة خارج المملكة"),
    ),
    (
        ("البيانات الحساسة", "الصحية", "الحيوية"),
        ("بصمة الوجه", "بصمة حيوية", "صور الوجوه", "نظام حضور بالبصمة"),
    ),
    (
        ("شهادة الإشغال", "الكود", "المخالفة الخطرة"),
        ("شهادة إشغال", "عدم مطابقة الأعمال", "عيب إنشائي", "تشققات وتسربات"),
    ),
    (
        ("وثيقة الإفصاح", "اتفاقية الامتياز", "مانح الامتياز"),
        ("فرنشايز", "منطقة حصرية", "رسوم امتياز", "إنهاء اتفاقية امتياز"),
    ),
    (
        ("نظام المدفوعات", "خدمات الدفع", "مزود خدمة الدفع"),
        ("مزود الدفع", "بوابة الدفع", "بيانات البطاقة", "خصم تلقائي", "اشتراك شهري"),
    ),
    (
        ("شركات التمويل", "التمويل الاستهلاكي", "المعلومات الائتمانية"),
        ("اشتر الآن وادفع لاحقًا", "الدفع لاحقًا", "رسوم وغرامات", "شروط السداد", "BNPL"),
    ),
    (
        ("النقل العام على الطرق", "توصيل الطلبات", "نشاط توصيل الطلبات"),
        ("تطبيق توصيل", "سائق توصيل", "سائقين مستقلين", "حوادث السائقين", "توصيل طلبات"),
    ),
    (
        ("نظام المرور", "التأمين على المركبة", "قيادة المركبة"),
        ("مركبة غير مؤمنة", "مركبات غير مؤمنة", "حادث سير", "حادث توصيل", "تأمين المركبة"),
    ),
)


def normalize(text: str) -> str:
    text = re.sub(r"[\u064B-\u065F\u0670]", "", text or "")
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    return " ".join(text.split())


def load_chunks(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def title_by_slug(chunks: list[dict[str, Any]]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for row in chunks:
        slug = str(row.get("regulation_slug") or "").strip()
        title = str(row.get("regulation_title_ar") or "").strip()
        if slug and title:
            titles.setdefault(slug, title)
    titles.update({slug: title for slug, title in REGULATION_TITLE_OVERRIDES.items() if slug and title})
    return titles


def companions_for(core: list[str]) -> list[str]:
    companions: list[str] = []
    for slug in core:
        companions.extend(DEFAULT_COMPANION_REGULATIONS_BY_CORE.get(slug, ()))
    return [slug for slug in _dedupe(companions) if slug not in set(core)]


def extract_terms(text: str, limit: int) -> list[str]:
    normalized = normalize(text)
    tokens = re.findall(r"[\u0621-\u064A]{4,}", normalized)
    counts: Counter[str] = Counter()
    for token in tokens:
        if token in STOPWORDS:
            continue
        if token.startswith(("وال", "بال", "كال", "فال")) and len(token) > 5:
            token = token[3:]
        elif token.startswith(("ال", "لل")) and len(token) > 5:
            token = token[2:]
        if token and token not in STOPWORDS:
            counts[token] += 1
    return [term for term, _count in counts.most_common(limit)]


def concept_expansions(text: str) -> list[str]:
    normalized = normalize(text)
    out: list[str] = []
    for triggers, expansions in CONCEPT_EXPANSIONS:
        if any(normalize(trigger) in normalized for trigger in triggers):
            out.extend(expansions)
    return _dedupe(out)


def compact_excerpt(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0]


def make_row(
    index: int,
    row: dict[str, Any],
    question: str,
    core: list[str],
    companions: list[str],
) -> dict[str, Any]:
    return {
        "question_id": f"router_article_surface_v1_{index:05d}",
        "question": " ".join(question.split()),
        "split": "train",
        "router_role": "train_article_surface_table",
        "domain": "router_article_surface",
        "benchmark_category": "package_router_article_surface_table",
        "scenario_family_id": f"article::{core[0]}::{row.get('article_index')}",
        "source_note": "article_chunk_surface_v1",
        "core_labels": core,
        "companion_labels": companions,
        "all_labels": _dedupe([*core, *companions]),
        "optional_labels": [],
        "excluded_labels": [],
        "source_chunk_id": row.get("chunk_id"),
        "source_article_index": row.get("article_index"),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    chunks = load_chunks(args.chunks)
    titles = title_by_slug(chunks)
    rows: list[dict[str, Any]] = []
    per_slug: Counter[str] = Counter()
    seen: set[tuple[str, tuple[str, ...]]] = set()
    index = 0

    for row in chunks:
        slug = str(row.get("regulation_slug") or "").strip()
        if not slug:
            continue
        if per_slug[slug] >= args.max_rows_per_regulation:
            continue
        text = str(row.get("text_for_index") or row.get("index_text") or row.get("text") or "").strip()
        if len(text) < args.min_text_chars:
            continue
        title = str(row.get("regulation_title_ar") or titles.get(slug) or slug)
        article = str(row.get("citation_short_ar") or row.get("article_label") or "").strip()
        tags = [
            str(item)
            for item in [
                row.get("article_type_label_ar"),
                *(row.get("legal_function_tags_ar") or []),
                *(row.get("topic_tags_ar") or []),
            ]
            if str(item or "").strip()
        ]
        terms = extract_terms(text, args.term_limit)
        expansions = concept_expansions(text)
        surface_terms = _dedupe([*tags, *terms[: args.term_limit], *expansions])
        if not surface_terms:
            continue
        excerpt = compact_excerpt(text, args.excerpt_chars)
        core = [slug]
        companions = companions_for(core)
        questions = [
            f"واقعة عملية تتضمن: {'، '.join(surface_terms[:18])}. ما الحزمة النظامية السعودية الواجبة؟",
            f"استرجع المرجع السعودي المناسب إذا ظهرت ألفاظ مثل: {'، '.join(surface_terms[:14])}.",
            f"لا تفوت {title} عند قضية تتعلق بـ {article}: {excerpt}.",
        ]
        for question in questions[: args.surfaces_per_chunk]:
            key = (question, tuple(_dedupe([*core, *companions])))
            if key in seen:
                continue
            seen.add(key)
            index += 1
            rows.append(make_row(index, row, question, core, companions))
        per_slug[slug] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "status": "ok",
        "output": str(args.output),
        "rows": len(rows),
        "unique_labels": len({row["core_labels"][0] for row in rows}),
        "max_rows_per_regulation": args.max_rows_per_regulation,
        "surfaces_per_chunk": args.surfaces_per_chunk,
        "source_counts": dict(sorted(per_slug.items())),
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
    parser.add_argument("--max-rows-per-regulation", type=int, default=90)
    parser.add_argument("--surfaces-per-chunk", type=int, default=2)
    parser.add_argument("--term-limit", type=int, default=18)
    parser.add_argument("--excerpt-chars", type=int, default=340)
    parser.add_argument("--min-text-chars", type=int, default=80)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
