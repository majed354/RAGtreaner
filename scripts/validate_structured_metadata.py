"""فحص سريع لاتساق توصيف المواد القانونية بعد البناء."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ARTICLES_JSONL = ROOT / "data" / "structured" / "articles.jsonl"


SUSPICIOUS_RULES = [
    (
        "definition_with_rights_heading",
        lambda row: row.get("article_type") == "definition"
        and any(term in (row.get("article_heading") or "") for term in ("الحقوق", "الولاية", "الحضانة")),
    ),
    (
        "definition_with_violations_heading",
        lambda row: row.get("article_type") == "definition"
        and any(term in (row.get("article_heading") or "") for term in ("المخالفات", "العقوبات", "التعدي")),
    ),
    (
        "missing_copyright_tag",
        lambda row: row.get("regulation_slug") == "copyright-law"
        and "copyright" not in (row.get("topic_tags") or []),
    ),
    (
        "missing_workplace_childcare_tag",
        lambda row: "أطفال العاملات" in (row.get("text_verbatim") or "")
        and "workplace_childcare" not in (row.get("topic_tags") or []),
    ),
]


def main() -> None:
    if not ARTICLES_JSONL.exists():
        raise SystemExit(f"Missing file: {ARTICLES_JSONL}")

    rows = []
    with ARTICLES_JSONL.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))

    summary: dict[str, list[str]] = {}
    for name, predicate in SUSPICIOUS_RULES:
        matches = [
            row.get("citation_short_ar", "unknown")
            for row in rows
            if predicate(row)
        ]
        summary[name] = matches[:20]

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
