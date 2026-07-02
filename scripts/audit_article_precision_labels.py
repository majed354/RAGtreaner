#!/usr/bin/env python3
"""Audit article-precision gold labels before using them as retrieval gates.

The audit is intentionally conservative: it does not judge the legal answer,
and it does not call the RAG service. It only checks whether each expected
article pair has enough textual support in the question, teacher note, axis
metadata, or the official structured article text to be used as a blind label.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured" / "by_regulation"

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
TOKEN_RE = re.compile(r"[0-9A-Za-z\u0600-\u06FF]+")
ARTICLE_WORD_RE = re.compile(r"\b(?:المادة|مادة|المواد|مواد|المادتين|المادتان|الماده)\b")

STOPWORDS = {
    "في",
    "من",
    "على",
    "عن",
    "إلى",
    "الى",
    "أو",
    "او",
    "و",
    "ثم",
    "هل",
    "ما",
    "ماذا",
    "متى",
    "كيف",
    "هذا",
    "هذه",
    "ذلك",
    "تلك",
    "التي",
    "الذي",
    "الذين",
    "اللاتي",
    "كما",
    "بموجب",
    "وفق",
    "وفقا",
    "نظام",
    "النظام",
    "اللائحة",
    "لائحة",
    "المادة",
    "مواد",
    "المواد",
    "رقم",
    "حيث",
    "بشأن",
    "بعد",
    "قبل",
    "غير",
    "كل",
    "أي",
    "اي",
    "كان",
    "كانت",
    "يكون",
    "تكون",
    "مع",
    "بين",
    "قد",
    "أحد",
    "احد",
    "عند",
    "لدى",
    "لدي",
    "إن",
    "ان",
    "أن",
    "أنها",
    "انه",
    "إنه",
    "إذا",
    "اذا",
}


def normalize_text(value: Any) -> str:
    text = str(value or "").translate(ARABIC_DIGITS)
    text = ARABIC_DIACRITICS_RE.sub("", text)
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
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.lower()


def tokenize(value: Any) -> set[str]:
    tokens: set[str] = set()
    for token in TOKEN_RE.findall(normalize_text(value)):
        if len(token) < 3 or token.isdigit() or token in STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def extract_numbers_near_article_words(value: Any) -> set[int]:
    text = normalize_text(value)
    if not ARTICLE_WORD_RE.search(text):
        return set()
    numbers: set[int] = set()
    for raw in re.findall(r"\b\d{1,4}\b", text):
        try:
            numbers.add(int(raw))
        except Exception:
            continue
    return numbers


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def expected_pairs(row: dict[str, Any]) -> list[tuple[str, int]]:
    pairs: list[tuple[str, int]] = []
    for slug, articles in (row.get("expected_articles_by_slug") or {}).items():
        for article in articles or []:
            try:
                pairs.append((str(slug), int(article)))
            except Exception:
                continue
    return sorted(set(pairs))


def axis_pairs(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for items in (row.get("axis_article_pairs") or {}).values():
        for item in items or []:
            if isinstance(item, str) and ":" in item:
                slug, article = item.rsplit(":", 1)
                try:
                    values.add(f"{slug}:{int(article)}")
                except Exception:
                    continue
    return values


class ArticleCorpus:
    def __init__(self, structured_dir: Path) -> None:
        self.structured_dir = structured_dir
        self._cache: dict[str, dict[int, dict[str, Any]]] = {}
        self._titles: dict[str, str] = {}

    def _load_slug(self, slug: str) -> dict[int, dict[str, Any]]:
        if slug in self._cache:
            return self._cache[slug]
        path = self.structured_dir / f"{slug}.json"
        articles: dict[int, dict[str, Any]] = {}
        title = ""
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            title = str((payload.get("metadata") or {}).get("title_ar") or "")
            for article in payload.get("articles") or []:
                try:
                    index = int(article.get("article_index") or 0)
                except Exception:
                    continue
                if index > 0:
                    articles[index] = article
        self._cache[slug] = articles
        self._titles[slug] = title
        return articles

    def get(self, slug: str, article_index: int) -> dict[str, Any] | None:
        return self._load_slug(slug).get(article_index)

    def title(self, slug: str) -> str:
        self._load_slug(slug)
        return self._titles.get(slug, "")


def article_text(article: dict[str, Any] | None) -> str:
    if not article:
        return ""
    parts = [
        article.get("article_heading"),
        article.get("article_type_label_ar"),
        " ".join(article.get("legal_function_tags_ar") or []),
        " ".join(article.get("topic_tags_ar") or []),
        article.get("text_for_index"),
        article.get("text_verbatim"),
    ]
    return " ".join(str(part or "") for part in parts)


def audit_pair(
    row: dict[str, Any],
    slug: str,
    article_index: int,
    *,
    corpus: ArticleCorpus,
    min_overlap: int,
) -> dict[str, Any]:
    question = str(row.get("question") or "")
    review_note = str((row.get("auto_review") or {}).get("review_note") or "")
    combined_context = f"{question}\n{review_note}"
    context_tokens = tokenize(combined_context)
    review_numbers = extract_numbers_near_article_words(review_note)
    question_numbers = extract_numbers_near_article_words(question)

    article = corpus.get(slug, article_index)
    text = article_text(article)
    overlap_tokens = sorted(context_tokens & tokenize(text))
    heading_overlap = sorted(context_tokens & tokenize(article.get("article_heading") if article else ""))

    key = f"{slug}:{article_index}"
    in_axis = key in axis_pairs(row)
    review_mentions = article_index in review_numbers
    question_mentions = article_index in question_numbers
    review_has_article_refs = bool(review_numbers)
    risk_level = "ok"
    reasons: list[str] = []

    if not article:
        risk_level = "high"
        reasons.append("missing_article_entry")
    elif review_has_article_refs and not review_mentions and not question_mentions and len(overlap_tokens) < min_overlap:
        risk_level = "high"
        reasons.append("teacher_note_mentions_other_articles")
    elif not review_mentions and not question_mentions and len(overlap_tokens) == 0:
        risk_level = "high"
        reasons.append("no_textual_support")
    elif not review_mentions and not question_mentions and len(overlap_tokens) < min_overlap:
        risk_level = "medium"
        reasons.append("weak_textual_support")

    if in_axis and risk_level == "medium":
        risk_level = "low"
        reasons.append("axis_pair_present")
    elif in_axis:
        reasons.append("axis_pair_present")
    if review_mentions:
        reasons.append("teacher_note_mentions_article")
    if question_mentions:
        reasons.append("question_mentions_article")

    support_score = 0.0
    support_score += 3.0 if review_mentions else 0.0
    support_score += 2.0 if question_mentions else 0.0
    support_score += 1.5 if in_axis else 0.0
    support_score += min(len(overlap_tokens), 8) / 2.0

    return {
        "pair": key,
        "slug": slug,
        "title_ar": corpus.title(slug),
        "article": article_index,
        "risk_level": risk_level,
        "support_score": round(support_score, 3),
        "reasons": reasons,
        "review_article_numbers": sorted(review_numbers),
        "question_article_numbers": sorted(question_numbers),
        "overlap_count": len(overlap_tokens),
        "overlap_tokens": overlap_tokens[:16],
        "heading_overlap_tokens": heading_overlap[:8],
        "article_exists": bool(article),
    }


def audit_case(row: dict[str, Any], *, corpus: ArticleCorpus, min_overlap: int) -> dict[str, Any]:
    pair_reports = [
        audit_pair(row, slug, article, corpus=corpus, min_overlap=min_overlap)
        for slug, article in expected_pairs(row)
    ]
    risk_order = {"ok": 0, "low": 1, "medium": 2, "high": 3}
    max_risk = max((risk_order.get(report["risk_level"], 0) for report in pair_reports), default=0)
    status = "auto_approved" if max_risk < risk_order["high"] else "needs_adjudication"
    high_risk = [report for report in pair_reports if report["risk_level"] == "high"]
    medium_risk = [report for report in pair_reports if report["risk_level"] == "medium"]
    return {
        "audit_version": "article_label_audit_v1",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "max_risk": next(name for name, level in risk_order.items() if level == max_risk),
        "expected_pair_count": len(pair_reports),
        "high_risk_count": len(high_risk),
        "medium_risk_count": len(medium_risk),
        "pair_reports": pair_reports,
        "approved_article_pairs": [
            report["pair"] for report in pair_reports if report["risk_level"] in {"ok", "low", "medium"}
        ],
        "review_article_pairs": [report["pair"] for report in high_risk],
    }


def summarize(
    rows: list[dict[str, Any]],
    all_approved: list[dict[str, Any]],
    exported_approved: list[dict[str, Any]],
    review: list[dict[str, Any]],
) -> dict[str, Any]:
    label_counts = Counter()
    domain_counts = Counter(str(row.get("domain") or "uncategorized") for row in rows)
    review_domain_counts = Counter(str(row.get("domain") or "uncategorized") for row in review)
    reason_counts = Counter()
    top_risky: list[dict[str, Any]] = []

    for row in rows:
        audit = row.get("label_audit") or {}
        for report in audit.get("pair_reports") or []:
            label_counts[str(report.get("risk_level") or "unknown")] += 1
            if report.get("risk_level") == "high":
                for reason in report.get("reasons") or []:
                    reason_counts[str(reason)] += 1
                top_risky.append(
                    {
                        "question_id": row.get("question_id"),
                        "domain": row.get("domain"),
                        "pair": report.get("pair"),
                        "title_ar": report.get("title_ar"),
                        "support_score": report.get("support_score"),
                        "reasons": report.get("reasons"),
                        "overlap_tokens": report.get("overlap_tokens"),
                    }
                )

    return {
        "audit_version": "article_label_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_total": len(rows),
        "cases_auto_approved": len(all_approved),
        "cases_auto_approved_exported": len(exported_approved),
        "cases_needing_adjudication": len(review),
        "auto_approved_rate": round(len(all_approved) / max(1, len(rows)), 4),
        "labels_by_risk": dict(label_counts),
        "domains_total": len(domain_counts),
        "domain_counts": dict(domain_counts),
        "review_domain_counts": dict(review_domain_counts),
        "high_risk_reason_counts": dict(reason_counts),
        "top_risky_labels": sorted(top_risky, key=lambda item: (item.get("support_score") or 0.0))[:30],
    }


def write_markdown(path: Path, report: dict[str, Any], approved_output: Path, review_output: Path) -> None:
    lines = [
        f"# {path.stem}",
        "",
        "## Summary",
        "",
        f"- cases_total: `{report['cases_total']}`",
        f"- cases_auto_approved: `{report['cases_auto_approved']}`",
        f"- cases_auto_approved_exported: `{report['cases_auto_approved_exported']}`",
        f"- cases_needing_adjudication: `{report['cases_needing_adjudication']}`",
        f"- auto_approved_rate: `{report['auto_approved_rate']}`",
        f"- labels_by_risk: `{json.dumps(report['labels_by_risk'], ensure_ascii=False)}`",
        f"- approved_output: `{approved_output}`",
        f"- review_output: `{review_output}`",
        "",
        "## High Risk Reasons",
        "",
    ]
    if report["high_risk_reason_counts"]:
        for reason, count in sorted(report["high_risk_reason_counts"].items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{reason}`: `{count}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Top Risky Labels", ""])
    if report["top_risky_labels"]:
        for item in report["top_risky_labels"][:20]:
            lines.append(
                f"- `{item['question_id']}` `{item['pair']}` score=`{item['support_score']}` "
                f"reasons=`{json.dumps(item['reasons'], ensure_ascii=False)}`"
            )
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approved-output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, required=True)
    parser.add_argument("--structured-dir", type=Path, default=DEFAULT_STRUCTURED_DIR)
    parser.add_argument("--max-approved", type=int, default=0)
    parser.add_argument("--min-overlap", type=int, default=2)
    args = parser.parse_args()

    corpus = ArticleCorpus(args.structured_dir)
    audited_rows: list[dict[str, Any]] = []
    all_approved_rows: list[dict[str, Any]] = []
    approved_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []

    for row in read_jsonl(args.cases):
        audited = dict(row)
        audited["label_audit"] = audit_case(row, corpus=corpus, min_overlap=args.min_overlap)
        audited_rows.append(audited)
        if audited["label_audit"]["status"] == "auto_approved":
            all_approved_rows.append(audited)
            if args.max_approved <= 0 or len(approved_rows) < args.max_approved:
                approved_rows.append(audited)
        else:
            review_rows.append(audited)

    report = summarize(audited_rows, all_approved_rows, approved_rows, review_rows)
    report["cases"] = audited_rows
    report["approved_output"] = str(args.approved_output)
    report["review_output"] = str(args.review_output)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_jsonl(args.approved_output, approved_rows)
    write_jsonl(args.review_output, review_rows)
    write_markdown(args.output.with_suffix(".md"), report, args.approved_output, args.review_output)

    compact = {key: value for key, value in report.items() if key != "cases"}
    print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
