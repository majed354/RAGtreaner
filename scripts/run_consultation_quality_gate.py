#!/usr/bin/env python3
"""Targeted consultation-quality gate for the local Saudi legal RAG service.

The older gates prove that the right systems and articles can be collected.
This gate asks the next question: did the consultation answer actually map the
retrieved legal material to the factual axes of the question?
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "data" / "eval" / "legal_eval_hard_set.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "consultation_quality_hard6_baseline.json"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"
REGULATIONS_PATH = PROJECT_ROOT / "data" / "structured" / "regulations.json"
STRUCTURED_BY_REGULATION = PROJECT_ROOT / "data" / "structured" / "by_regulation"

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
NON_WORD_RE = re.compile(r"[^\w\u0600-\u06ff]+")

STOPWORDS = {
    "اثر",
    "الا",
    "الاطار",
    "الاقرب",
    "الانظمه",
    "الخاص",
    "الدفاع",
    "السعودي",
    "المباشر",
    "النظام",
    "النظامي",
    "النص",
    "النصوص",
    "او",
    "اي",
    "الى",
    "انه",
    "انها",
    "به",
    "بين",
    "ذات",
    "ذلك",
    "على",
    "عن",
    "عند",
    "في",
    "قبل",
    "كل",
    "كما",
    "لا",
    "له",
    "ما",
    "مع",
    "من",
    "هذا",
    "هذه",
    "هو",
    "هي",
}

PRACTICAL_TERMS = {
    "يبطل",
    "يجوز",
    "لا يجوز",
    "يجب",
    "يلزم",
    "يلتزم",
    "يحق",
    "تستطيع",
    "يستطيع",
    "فسخ",
    "استرجاع",
    "تعويض",
    "انذار",
    "اشعار",
    "تحقيق",
    "سماع",
    "دفاع",
    "افصاح",
    "اخطار",
    "ابلاغ",
    "اتلاف",
    "حذف",
    "تقييم",
    "مخاطر",
    "موافقة",
    "جزاء",
    "شكوى",
    "سرية",
    "اثبات",
    "بينة",
    "محكمة",
    "مطالبة",
    "اعادة",
    "العربون",
    "المدير",
    "الشركاء",
}

CASE_PARTY_TERMS = {
    "موظف",
    "موظفه",
    "صاحب العمل",
    "الشركة",
    "شركة",
    "مستهلك",
    "المتجر",
    "تطبيق",
    "المستخدمين",
    "مزود",
    "مشتري",
    "بائع",
    "مدير",
    "المنشاة",
    "المنشاه",
    "الشركاء",
}

STRUCTURE_TERMS = {
    "الخلاصه",
    "الحكم",
    "النظام المنطبق",
    "المواد",
    "القيود",
    "الاستثناءات",
    "ما لم يثبته",
    "التطبيق",
    "النتيجه",
    "الاجراءات",
    "المخاطر",
}

CAVEAT_TERMS = {
    "اذا",
    "بحسب",
    "ما لم",
    "قبل الجزم",
    "يلزم التحقق",
    "الادله",
    "اثبات",
    "تقدير",
    "مهله",
    "مواعيد",
    "اختصاص",
    "شروط",
    "وقائع",
    "دون الجزم",
}


def normalize(text: Any) -> str:
    value = str(text or "").translate(ARABIC_DIGITS)
    value = DIACRITICS_RE.sub("", value)
    value = value.replace("ـ", "")
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    return " ".join(NON_WORD_RE.sub(" ", value.lower()).split())


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_regulation_aliases() -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = defaultdict(set)

    if REGULATIONS_PATH.exists():
        try:
            for row in json.loads(REGULATIONS_PATH.read_text(encoding="utf-8")):
                slug = row.get("slug") or row.get("regulation_slug")
                if not slug:
                    continue
                aliases[slug].add(normalize(slug))
                aliases[slug].add(normalize(str(slug).replace("-", " ")))
                for key in ("title_ar", "regulation_title_ar", "name_ar", "title_en", "name_en"):
                    value = row.get(key)
                    if value:
                        aliases[slug].add(normalize(value))
        except Exception:
            pass

    for path in STRUCTURED_BY_REGULATION.glob("*.json"):
        slug = path.stem
        aliases[slug].add(normalize(slug))
        aliases[slug].add(normalize(slug.replace("-", " ")))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            metadata = payload.get("metadata") or {}
            for key in ("title_ar", "title", "name", "short_title_ar"):
                value = metadata.get(key)
                if value:
                    aliases[slug].add(normalize(value))
        except Exception:
            continue

    return {slug: {alias for alias in values if alias} for slug, values in aliases.items()}


def is_companion_regulation(slug: str, aliases: dict[str, set[str]]) -> bool:
    normalized_slug = normalize(slug)
    title_blob = " ".join(aliases.get(slug, set()))
    return any(
        marker in normalized_slug or marker in title_blob
        for marker in (
            "regulation",
            "implementing",
            "transfer",
            "controls",
            "laeha",
            "layhh",
            "اللائحه",
            "لائحه",
            "ضوابط",
        )
    )


def article_patterns(article: int) -> list[re.Pattern[str]]:
    value = str(int(article))
    return [
        re.compile(rf"\bالماده\s*(?:رقم\s*)?\(?\s*{re.escape(value)}\s*\)?\b"),
        re.compile(rf"\bماده\s*(?:رقم\s*)?\(?\s*{re.escape(value)}\s*\)?\b"),
        re.compile(rf"\barticle\s*{re.escape(value)}\b", re.IGNORECASE),
        re.compile(rf":{re.escape(value)}\b"),
    ]


def article_mentioned(normalized_text: str, article: int) -> bool:
    return any(pattern.search(normalized_text) for pattern in article_patterns(article))


def split_segments(text: str) -> list[str]:
    pieces: list[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pieces.append(line)
        pieces.extend(part.strip() for part in re.split(r"[؛;،,.]", line) if part.strip())
    return [normalize(piece) for piece in pieces if normalize(piece)]


def aliases_present(slug: str, normalized_text: str, aliases: dict[str, set[str]]) -> bool:
    slug_aliases = aliases.get(slug) or {normalize(slug), normalize(slug.replace("-", " "))}
    return any(alias and alias in normalized_text for alias in slug_aliases)


def matched_regulations(
    expected_regulations: list[str],
    answer: str,
    sources: list[str],
    diagnostics: dict[str, Any],
    aliases: dict[str, set[str]],
    *,
    in_answer_only: bool = False,
) -> list[str]:
    answer_norm = normalize(answer)
    source_norm = normalize("\n".join(sources))
    observed = set(diagnostics.get("top_regulations") or [])
    dominant_domain = diagnostics.get("dominant_domain")
    if dominant_domain:
        observed.add(str(dominant_domain))

    hits = []
    for slug in expected_regulations:
        if aliases_present(slug, answer_norm, aliases):
            hits.append(slug)
            continue
        if in_answer_only:
            continue
        if slug in observed or aliases_present(slug, source_norm, aliases):
            hits.append(slug)
    return sorted(set(hits))


def matched_articles(
    expected_articles: list[int],
    answer: str,
    sources: list[str],
    diagnostics: dict[str, Any],
    *,
    in_answer_only: bool = False,
) -> list[int]:
    answer_norm = normalize(answer)
    source_norm = normalize("\n".join(sources))
    observed_articles = {
        int(value)
        for value in diagnostics.get("top_articles", []) or []
        if str(value).translate(ARABIC_DIGITS).isdigit()
    }

    hits: list[int] = []
    for raw_article in expected_articles:
        try:
            article = int(str(raw_article).translate(ARABIC_DIGITS))
        except ValueError:
            continue
        if article_mentioned(answer_norm, article):
            hits.append(article)
            continue
        if not in_answer_only and (article in observed_articles or article_mentioned(source_norm, article)):
            hits.append(article)
    return sorted(set(hits))


def pair_bound_in_answer(answer: str, slug: str, article: int, aliases: dict[str, set[str]]) -> bool:
    answer_norm = normalize(answer)
    literal = normalize(f"{slug}:{article}")
    if literal and literal in answer_norm:
        return True
    for segment in split_segments(answer):
        if article_mentioned(segment, article) and aliases_present(slug, segment, aliases):
            return True
    return False


def direct_issue_keywords(issue: str) -> list[str]:
    tokens = []
    for token in normalize(issue).split():
        if len(token) < 4 or token in STOPWORDS:
            continue
        tokens.append(token)
    return list(dict.fromkeys(tokens))


def term_presence_score(terms: set[str], normalized_text: str, target: int = 3) -> float:
    if not terms:
        return 1.0
    hits = sum(1 for term in terms if normalize(term) in normalized_text)
    return min(hits / max(1, min(target, len(terms))), 1.0)


def rate(numerator: int, denominator: int) -> float:
    return round(numerator / max(1, denominator), 3)


def bounded_mean(values: list[float], default: float = 1.0) -> float:
    clean = [max(0.0, min(1.0, float(value))) for value in values]
    return round(mean(clean), 3) if clean else default


def query_service(case: dict[str, Any], service_url: str, answer_mode: str, retrieval_profile: str, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(
        {
            "question": case["question"],
            "answer_mode": answer_mode,
            "retrieval_profile": retrieval_profile,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        service_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def transport_row(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "question_id": case.get("question_id"),
        "benchmark_category": case.get("benchmark_category"),
        "question_type": case.get("question_type"),
        "question": case.get("question"),
        "classification": "operational issue",
        "passed": False,
        "transport_error": True,
        "transport_error_type": type(exc).__name__,
        "transport_error_message": str(exc)[:800],
        "consultation_quality_score_100": 0.0,
        "axis_scores": {},
        "top_gaps": ["transport_error"],
    }


def issue_coverage_details(
    case: dict[str, Any],
    answer: str,
    sources: list[str],
    diagnostics: dict[str, Any],
    aliases: dict[str, set[str]],
) -> tuple[float, float, list[dict[str, Any]]]:
    answer_norm = normalize(answer)
    details: list[dict[str, Any]] = []
    context_scores: list[float] = []
    answer_scores: list[float] = []

    for sub_issue in case.get("sub_issues", []) or []:
        expected_regulations = [str(value) for value in sub_issue.get("expected_regulations", [])]
        expected_articles = [
            int(str(value).translate(ARABIC_DIGITS))
            for value in sub_issue.get("expected_articles", []) or []
            if str(value).translate(ARABIC_DIGITS).isdigit()
        ]
        regulation_target = int(sub_issue.get("min_expected_regulation_hits") or len(expected_regulations) or 0)
        article_target = int(sub_issue.get("min_expected_article_hits") or len(expected_articles) or 0)
        context_regs = matched_regulations(expected_regulations, answer, sources, diagnostics, aliases)
        answer_regs = matched_regulations(expected_regulations, answer, sources, diagnostics, aliases, in_answer_only=True)
        context_articles = matched_articles(expected_articles, answer, sources, diagnostics)
        answer_articles = matched_articles(expected_articles, answer, sources, diagnostics, in_answer_only=True)
        keywords = direct_issue_keywords(str(sub_issue.get("issue") or ""))
        keyword_score = term_presence_score(set(keywords), answer_norm, target=2)

        context_reg_score = min(len(context_regs) / max(1, regulation_target), 1.0) if regulation_target else 1.0
        answer_reg_score = min(len(answer_regs) / max(1, regulation_target), 1.0) if regulation_target else 1.0
        context_article_score = min(len(context_articles) / max(1, article_target), 1.0) if article_target else 1.0
        answer_article_score = min(len(answer_articles) / max(1, article_target), 1.0) if article_target else 1.0

        context_score = bounded_mean([context_reg_score, context_article_score])
        answer_evidence_score = bounded_mean([answer_reg_score, answer_article_score])
        answer_issue_score = round((keyword_score * 0.35) + (answer_evidence_score * 0.65), 3)
        context_scores.append(context_score)
        answer_scores.append(answer_issue_score)
        details.append(
            {
                "issue": sub_issue.get("issue"),
                "issue_keywords": keywords[:8],
                "expected_regulations": expected_regulations,
                "expected_articles": expected_articles,
                "context_matched_regulations": context_regs,
                "answer_matched_regulations": answer_regs,
                "context_matched_articles": context_articles,
                "answer_matched_articles": answer_articles,
                "keyword_score": round(keyword_score, 3),
                "context_score": context_score,
                "answer_issue_score": answer_issue_score,
                "covered_in_context": context_score >= 0.85,
                "covered_in_answer": answer_issue_score >= 0.75,
            }
        )

    return bounded_mean(context_scores), bounded_mean(answer_scores), details


def answer_structure_score(answer: str) -> float:
    answer_norm = normalize(answer)
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    numbered = sum(1 for line in lines if re.match(r"^\s*\d+\s*[.)-]?\s*", line))
    term_score = term_presence_score(STRUCTURE_TERMS, answer_norm, target=4)
    length_score = min(len(answer_norm.split()) / 140, 1.0)
    numbered_score = min(numbered / 4, 1.0)
    return round((term_score * 0.40) + (length_score * 0.25) + (numbered_score * 0.35), 3)


def practical_application_score(case: dict[str, Any], answer: str) -> float:
    answer_norm = normalize(answer)
    question_norm = normalize(case.get("question", ""))
    practical_score = term_presence_score(PRACTICAL_TERMS, answer_norm, target=6)
    party_terms = {term for term in CASE_PARTY_TERMS if normalize(term) in question_norm}
    party_score = term_presence_score(party_terms, answer_norm, target=2) if party_terms else 0.75
    issue_terms = {
        token
        for sub_issue in case.get("sub_issues", []) or []
        for token in direct_issue_keywords(str(sub_issue.get("issue") or ""))
    }
    issue_term_score = term_presence_score(issue_terms, answer_norm, target=5)
    applied_phrases = {
        "في هذه الواقعه",
        "على هذه الواقعه",
        "بناء على",
        "لذلك",
        "عمليا",
        "الاقرب",
        "ينبغي",
        "الخلاصه",
    }
    phrase_score = term_presence_score(applied_phrases, answer_norm, target=2)
    return round(
        (practical_score * 0.35)
        + (party_score * 0.20)
        + (issue_term_score * 0.30)
        + (phrase_score * 0.15),
        3,
    )


def caveat_risk_score(answer: str) -> float:
    answer_norm = normalize(answer)
    return round(term_presence_score(CAVEAT_TERMS, answer_norm, target=4), 3)


def confidence_axis(confidence: str) -> float:
    return {"high": 1.0, "medium": 0.75, "low": 0.15}.get(str(confidence or "").lower(), 0.5)


def classify(
    *,
    transport_error: bool,
    axis_scores: dict[str, float],
    diagnostics: dict[str, Any],
) -> str:
    if transport_error:
        return "operational issue"

    retrieval_gap = (
        bool(diagnostics.get("fatal_core_doc_miss"))
        or axis_scores["material_context_score"] < 0.82
        or axis_scores["issue_context_score"] < 0.75
    )
    answer_gap = (
        axis_scores["answer_material_score"] < 0.75
        or axis_scores["issue_answer_score"] < 0.75
        or axis_scores["practical_application_score"] < 0.65
    )

    if retrieval_gap:
        return "retrieval/package issue"
    if answer_gap:
        return "answer-level issue"
    return "ok"


def evaluate_case(
    case: dict[str, Any],
    service_url: str,
    answer_mode: str,
    retrieval_profile: str,
    timeout_seconds: float,
    aliases: dict[str, set[str]],
) -> dict[str, Any]:
    try:
        response = query_service(case, service_url, answer_mode, retrieval_profile, timeout_seconds)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        socket.timeout,
        ConnectionError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ) as exc:
        return transport_row(case, exc)

    result = response.get("result") or {}
    answer = str(result.get("answer") or "")
    sources = [str(value) for value in result.get("sources") or []]
    diagnostics = result.get("diagnostics") or {}
    confidence = str(result.get("confidence") or "low")

    expected_regs = [str(value) for value in case.get("expected_regulations", []) or []]
    expected_articles = [
        int(str(value).translate(ARABIC_DIGITS))
        for value in case.get("expected_articles", []) or []
        if str(value).translate(ARABIC_DIGITS).isdigit()
    ]
    context_regs = matched_regulations(expected_regs, answer, sources, diagnostics, aliases)
    answer_regs = matched_regulations(expected_regs, answer, sources, diagnostics, aliases, in_answer_only=True)
    context_articles = matched_articles(expected_articles, answer, sources, diagnostics)
    answer_articles = matched_articles(expected_articles, answer, sources, diagnostics, in_answer_only=True)
    governing_regs = [slug for slug in expected_regs if not is_companion_regulation(slug, aliases)]
    companion_regs = [slug for slug in expected_regs if is_companion_regulation(slug, aliases)]
    governing_hits = [slug for slug in context_regs if slug in governing_regs]
    companion_hits = [slug for slug in context_regs if slug in companion_regs]

    bound_pairs: list[str] = []
    for sub_issue in case.get("sub_issues", []) or []:
        regs = [str(value) for value in sub_issue.get("expected_regulations", []) or []]
        articles = [
            int(str(value).translate(ARABIC_DIGITS))
            for value in sub_issue.get("expected_articles", []) or []
            if str(value).translate(ARABIC_DIGITS).isdigit()
        ]
        if len(regs) == 1:
            for article in articles:
                if pair_bound_in_answer(answer, regs[0], article, aliases):
                    bound_pairs.append(f"{regs[0]}:{article}")

    issue_context_score, issue_answer_score, issue_details = issue_coverage_details(
        case,
        answer,
        sources,
        diagnostics,
        aliases,
    )
    regulation_context_rate = rate(len(context_regs), len(expected_regs)) if expected_regs else 1.0
    regulation_answer_rate = rate(len(answer_regs), len(expected_regs)) if expected_regs else 1.0
    article_context_rate = rate(len(context_articles), len(expected_articles)) if expected_articles else 1.0
    article_answer_rate = rate(len(answer_articles), len(expected_articles)) if expected_articles else 1.0
    bound_pair_rate = rate(len(set(bound_pairs)), sum(1 for issue in issue_details for article in issue.get("expected_articles", []))) if expected_articles else 1.0

    governing_system_score = rate(len(governing_hits), len(governing_regs)) if governing_regs else 1.0
    companion_regulation_score = rate(len(companion_hits), len(companion_regs)) if companion_regs else 1.0
    material_context_score = round(
        (regulation_context_rate * 0.35)
        + (article_context_rate * 0.35)
        + (governing_system_score * 0.15)
        + (companion_regulation_score * 0.15),
        3,
    )
    answer_material_score = round(
        (regulation_answer_rate * 0.30)
        + (article_answer_rate * 0.45)
        + (bound_pair_rate * 0.25),
        3,
    )

    axis_scores = {
        "governing_system_presence_score": governing_system_score,
        "companion_regulation_presence_score": companion_regulation_score,
        "precise_articles_context_score": article_context_rate,
        "precise_articles_answer_score": article_answer_rate,
        "material_context_score": material_context_score,
        "answer_material_score": answer_material_score,
        "issue_context_score": issue_context_score,
        "issue_answer_score": issue_answer_score,
        "practical_application_score": practical_application_score(case, answer),
        "structure_score": answer_structure_score(answer),
        "caveat_risk_score": caveat_risk_score(answer),
        "confidence_score": confidence_axis(confidence),
    }
    score = round(
        (
            axis_scores["material_context_score"] * 0.18
            + axis_scores["answer_material_score"] * 0.17
            + axis_scores["issue_answer_score"] * 0.20
            + axis_scores["practical_application_score"] * 0.20
            + axis_scores["structure_score"] * 0.10
            + axis_scores["caveat_risk_score"] * 0.08
            + axis_scores["confidence_score"] * 0.07
        )
        * 100,
        1,
    )
    classification = classify(transport_error=False, axis_scores=axis_scores, diagnostics=diagnostics)
    top_gaps = [
        key
        for key, value in sorted(axis_scores.items(), key=lambda item: item[1])
        if value < 0.75
    ][:5]
    passed = (
        classification == "ok"
        and score >= 85.0
        and axis_scores["material_context_score"] >= 0.85
        and axis_scores["issue_answer_score"] >= 0.80
        and axis_scores["practical_application_score"] >= 0.65
    )

    return {
        "question_id": case.get("question_id"),
        "benchmark_category": case.get("benchmark_category"),
        "question_type": case.get("question_type"),
        "question": case.get("question"),
        "expected_regulations": expected_regs,
        "expected_articles": expected_articles,
        "matched_context_regulations": context_regs,
        "matched_answer_regulations": answer_regs,
        "matched_context_articles": context_articles,
        "matched_answer_articles": answer_articles,
        "answer_bound_article_pairs": sorted(set(bound_pairs)),
        "missing_context_regulations": [slug for slug in expected_regs if slug not in context_regs],
        "missing_context_articles": [article for article in expected_articles if article not in context_articles],
        "missing_answer_regulations": [slug for slug in expected_regs if slug not in answer_regs],
        "missing_answer_articles": [article for article in expected_articles if article not in answer_articles],
        "sub_issue_details": issue_details,
        "classification": classification,
        "passed": passed,
        "transport_error": False,
        "confidence": confidence,
        "consultation_quality_score_100": score,
        "axis_scores": axis_scores,
        "top_gaps": top_gaps,
        "answer_preview": answer[:2200],
        "source_count": len(sources),
        "diagnostics": {
            "quality_status": diagnostics.get("status"),
            "retrieval_profile": diagnostics.get("retrieval_profile"),
            "retrieval_profile_config": diagnostics.get("retrieval_profile_config", {}),
            "issue_flags": diagnostics.get("issue_flags", []),
            "dominant_domain": diagnostics.get("dominant_domain"),
            "top_regulations": diagnostics.get("top_regulations", []),
            "top_articles": diagnostics.get("top_articles", []),
            "required_core_regulations": diagnostics.get("required_core_regulations", []),
            "covered_core_regulations": diagnostics.get("covered_core_regulations", []),
            "missing_core_regulations": diagnostics.get("missing_core_regulations", []),
            "core_doc_recall": diagnostics.get("core_doc_recall"),
            "required_companion_regulations": diagnostics.get("required_companion_regulations", []),
            "covered_companion_regulations": diagnostics.get("covered_companion_regulations", []),
            "missing_companion_regulations": diagnostics.get("missing_companion_regulations", []),
            "companion_doc_recall": diagnostics.get("companion_doc_recall"),
            "direct_article_recall": diagnostics.get("direct_article_recall"),
            "expected_article_entered_context_rate": diagnostics.get("expected_article_entered_context_rate"),
            "expected_article_mean_context_position": diagnostics.get("expected_article_mean_context_position"),
            "bundle_completeness": diagnostics.get("bundle_completeness"),
            "fatal_core_doc_miss": diagnostics.get("fatal_core_doc_miss"),
            "pollution_rate": diagnostics.get("pollution_rate"),
            "document_class_counts": diagnostics.get("document_class_counts", {}),
        },
    }


def summarize(rows: list[dict[str, Any]], benchmark_id: str, answer_mode: str, retrieval_profile: str) -> dict[str, Any]:
    non_operational = [row for row in rows if not row.get("transport_error")]
    passed_rows = [row for row in non_operational if row.get("passed")]
    axis_names = sorted({key for row in non_operational for key in (row.get("axis_scores") or {})})
    by_classification = Counter(row.get("classification") for row in rows)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("benchmark_category") or "uncategorized")].append(row)

    def avg(values: list[float]) -> float | None:
        return round(mean(values), 3) if values else None

    axis_averages = {
        axis: avg([float(row["axis_scores"][axis]) for row in non_operational if axis in row.get("axis_scores", {})])
        for axis in axis_names
    }
    worst_rows = sorted(
        non_operational,
        key=lambda row: (float(row.get("consultation_quality_score_100") or 0.0), str(row.get("question_id"))),
    )[:10]

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": benchmark_id,
        "answer_mode": answer_mode,
        "retrieval_profile": retrieval_profile,
        "cases_total": len(rows),
        "non_operational_cases": len(non_operational),
        "transport_error_cases": sum(1 for row in rows if row.get("transport_error")),
        "consultation_quality_score_100": avg(
            [float(row.get("consultation_quality_score_100") or 0.0) for row in non_operational]
        )
        or 0.0,
        "pass_rate": round(len(passed_rows) / max(1, len(non_operational)), 3),
        "passed_cases": len(passed_rows),
        "failed_cases": len(non_operational) - len(passed_rows),
        "classification_counts": dict(by_classification),
        "axis_averages": axis_averages,
        "by_category": {
            category: {
                "cases": len(items),
                "score": avg(
                    [
                        float(item.get("consultation_quality_score_100") or 0.0)
                        for item in items
                        if not item.get("transport_error")
                    ]
                ),
                "pass_rate": round(
                    sum(1 for item in items if not item.get("transport_error") and item.get("passed"))
                    / max(1, sum(1 for item in items if not item.get("transport_error"))),
                    3,
                ),
                "classification_counts": dict(Counter(item.get("classification") for item in items)),
            }
            for category, items in sorted(by_category.items())
        },
        "worst_cases": [
            {
                "question_id": row.get("question_id"),
                "score": row.get("consultation_quality_score_100"),
                "classification": row.get("classification"),
                "top_gaps": row.get("top_gaps", []),
                "missing_context_regulations": row.get("missing_context_regulations", []),
                "missing_context_articles": row.get("missing_context_articles", []),
                "missing_answer_articles": row.get("missing_answer_articles", []),
                "axis_scores": row.get("axis_scores", {}),
            }
            for row in worst_rows
        ],
    }


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary['benchmark_id']}",
        "",
        f"- consultation quality score: `{summary['consultation_quality_score_100']}/100`",
        f"- pass rate: `{summary['pass_rate']}`",
        f"- passed cases: `{summary['passed_cases']}`",
        f"- failed cases: `{summary['failed_cases']}`",
        f"- transport error cases: `{summary['transport_error_cases']}`",
        f"- classification counts: `{summary['classification_counts']}`",
        "",
        "## Axis Averages",
        "",
    ]
    for axis, value in summary["axis_averages"].items():
        lines.append(f"- `{axis}`: `{value}`")

    lines.extend(["", "## Worst Cases", ""])
    for row in summary["worst_cases"]:
        lines.append(
            f"- `{row['question_id']}` score=`{row['score']}` class=`{row['classification']}` "
            f"gaps=`{row['top_gaps']}` missing_context_regs=`{row['missing_context_regulations']}` "
            f"missing_context_articles=`{row['missing_context_articles']}` missing_answer_articles=`{row['missing_answer_articles']}`"
        )

    lines.extend(["", "## Case Details", ""])
    for row in rows:
        if row.get("transport_error"):
            lines.append(
                f"- `{row.get('question_id')}` OPERATIONAL: {row.get('transport_error_type')} "
                f"{row.get('transport_error_message')}"
            )
            continue
        lines.append(
            f"- `{row.get('question_id')}` score=`{row.get('consultation_quality_score_100')}` "
            f"pass=`{row.get('passed')}` class=`{row.get('classification')}` gaps=`{row.get('top_gaps')}`"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--benchmark-id", default="consultation_quality_hard6")
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--answer-mode", default="consultation")
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()

    aliases = load_regulation_aliases()
    rows = [
        evaluate_case(case, args.service_url, args.answer_mode, args.retrieval_profile, args.timeout_seconds, aliases)
        for case in load_cases(args.cases)
    ]
    summary = summarize(rows, args.benchmark_id, args.answer_mode, args.retrieval_profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


if __name__ == "__main__":
    main()
