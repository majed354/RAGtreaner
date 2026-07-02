"""تشغيل حلقة تقييم RAG ثم استخراج سجل فجوات قابل للتنفيذ."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import run_legal_eval as base_eval

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = ROOT / "data" / "eval" / "legal_eval_hard_set.jsonl"
DEFAULT_EVAL_OUTPUT = ROOT / "data" / "eval" / "legal_eval_gap_loop_report.json"
DEFAULT_ANALYSIS_OUTPUT = ROOT / "data" / "eval" / "legal_gap_ledger.json"
DEFAULT_SUMMARY_OUTPUT = ROOT / "data" / "eval" / "legal_gap_ledger.md"

PASSING_TAXONOMIES = {"ok", "correct_refusal"}

TAXONOMY_SEVERITY = {
    "core_doc_miss": 5.0,
    "retrieval_domain_miss": 4.5,
    "cross_domain_noise": 4.0,
    "coverage_gap": 3.5,
    "article_gap": 3.0,
    "package_gap": 2.5,
    "quality_gate_refusal": 2.0,
    "generation_or_validation_failure": 2.0,
    "partial_confidence": 1.0,
    "missed_refusal": 2.0,
}

TAXONOMY_ACTIONS = {
    "core_doc_miss": [
        "عزّز domain routing وحقن النص الخاص قبل أي مادة عامة أو إجرائية.",
        "أضف mandatory core docs للحالات التي لها نظام خاص صريح.",
    ],
    "retrieval_domain_miss": [
        "ارفع قيود metadata/domain policy بحيث يُحصر البحث في العائلة النظامية الصحيحة أولاً.",
        "وسّع قاموس issue spotting للمفاهيم التي تحدد النظام الحاكم مباشرة.",
    ],
    "cross_domain_noise": [
        "شدّد cross-domain pruning وخفّض وزن الأنظمة القريبة لفظياً إذا غاب النظام الحاكم.",
        "أضف negative traps واستخدمها كعقوبات في reranking.",
    ],
    "coverage_gap": [
        "حسّن claim decomposition بحيث تُستخرج كل عناصر الواقعة قبل الاسترجاع.",
        "أضف coverage checker يمنع التوليد عند بقاء مطالبة جوهرية بلا تغطية.",
    ],
    "article_gap": [
        "ارفع وزن المواد الحاكمة مباشرة ووسّع pool الاسترجاع الأولي قبل التصفية.",
        "أضف expected direct articles وحقنها عند وجود intent قانوني واضح.",
    ],
    "package_gap": [
        "فعّل companion articles وcompanion regulations بشكل إلزامي للمطالبات المركبة.",
        "راجع bundle completeness قبل مرحلة التوليد.",
    ],
    "quality_gate_refusal": [
        "راجع quality gate كي لا يخفض الثقة إذا كانت الحزمة القانونية كافية فعلاً.",
    ],
    "generation_or_validation_failure": [
        "راجع طبقة التوليد/التحقق لأن الاسترجاع قد يكون كافياً لكن الجواب النهائي لا يعكسه.",
    ],
    "partial_confidence": [
        "حسّن legal confidence scoring أو اكتمال الحزمة لرفع الحالات الوسطية إلى high.",
    ],
    "missed_refusal": [
        "شدّد abstention gate في الموضوعات غير المغطاة داخل corpus.",
    ],
}

ISSUE_FLAG_ACTIONS = {
    "missing_primary_law_anchor": "عزّز حقن primary law anchor قبل النصوص المساندة أو الإجرائية.",
    "procedural_or_supplementary_drift": "خفّض وزن النصوص الإجرائية/المساندة إذا لم يحضر النص الموضوعي الحاكم.",
    "missing_issue_coverage": "أضف subqueries أو claim intents للمسائل الجزئية غير المغطاة.",
    "missing_issue_domains": "راجع domain router في الأسئلة متعددة العناصر أو متعددة الأنظمة.",
    "issue_domain_mismatch": "أعد معايرة legal reranker ليقدم system/sector match على التشابه اللفظي.",
    "missing_legal_function_support": "أضف function tagging للمواد حتى نحضر الأصل والعلاج والإجراء والاستثناء معًا.",
    "weak_evidence": "وسّع عدد النتائج الأولية أو حسّن source pruning حتى لا تضعف الحزمة النهائية.",
    "missing_legal_axes": "أضف tagging وظيفي للمواد: أصل الحكم، علاج، إجراء، استثناء، حق.",
    "missing_exception_support": "أضف استرجاعاً موازياً للاستثناءات والقيود والشروط النافية.",
    "missing_rights_support": "أضف expected rights articles للحزم التي تتطلب حقاً لصاحب الطلب.",
    "missing_violation_support": "أضف expected violation/remedy articles عند النزاعات الجزائية أو التنظيمية.",
    "thin_article_coverage": "ارفع سقف candidate pool أو حسّن article-level reranking.",
    "over_concentrated_context": "خفّف concentration penalty أو وسّع diversity في اختيار السياق.",
    "narrow_system_coverage": "أضف bundle expansion للأنظمة المكملة واللوائح التنفيذية.",
    "fatal_core_doc_miss": "اجعل غياب core doc سبباً لإعادة الاسترجاع تلقائياً قبل التوليد.",
}


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_list(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    return [str(value) for value in (values or []) if str(value).strip()]


def row_needs_attention(row: dict[str, Any], min_score: float) -> bool:
    taxonomy = row.get("taxonomy", "")
    score = float(row.get("score", 0.0))
    return taxonomy not in PASSING_TAXONOMIES or score < min_score


def summarize_counter(values: list[str], limit: int = 8) -> list[dict[str, Any]]:
    counter = Counter(value for value in values if value)
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common(limit)
    ]


def build_gap_signature(row: dict[str, Any]) -> str:
    diagnostics = row.get("diagnostics", {}) or {}
    taxonomy = row.get("taxonomy", "uncategorized")
    parts = [taxonomy]

    for label, values in (
        ("missing_core", stable_list(diagnostics.get("missing_core_regulations"))[:2]),
        ("missing_companion", stable_list(diagnostics.get("missing_companion_regulations"))[:2]),
        ("missing_articles", stable_list(diagnostics.get("missing_direct_articles"))[:3]),
        ("unexpected", stable_list(row.get("unexpected_regulations"))[:2]),
        ("flags", stable_list(diagnostics.get("issue_flags"))[:2]),
    ):
        if values:
            parts.append(f"{label}={','.join(values)}")

    if len(parts) == 1:
        dominant_domain = (
            diagnostics.get("dominant_domain")
            or (row.get("expected_regulations") or [None])[0]
            or "unknown"
        )
        parts.append(f"domain={dominant_domain}")

    return " | ".join(parts)


def collect_actions(rows: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    seen: set[str] = set()

    def add(items: list[str] | tuple[str, ...] | None) -> None:
        for item in items or []:
            if item and item not in seen:
                seen.add(item)
                actions.append(item)

    for row in rows:
        taxonomy = row.get("taxonomy", "")
        diagnostics = row.get("diagnostics", {}) or {}
        add(TAXONOMY_ACTIONS.get(taxonomy, []))

        if diagnostics.get("missing_core_regulations"):
            add(
                [
                    "أضف mandatory core regulation injection للحالات التي يظهر فيها نظام حاكم مفقود.",
                ]
            )
        if diagnostics.get("missing_companion_regulations"):
            add(
                [
                    "فعّل companion regulation expansion وربط النظام بلائحته التنفيذية أو الضوابط التابعة.",
                ]
            )
        if diagnostics.get("missing_direct_articles"):
            add(
                [
                    "حسّن direct-article scoring واسمح بحقن المواد الحاسمة حتى لو لم تدخل top candidates مبكراً.",
                ]
            )
        if diagnostics.get("missing_bundle_articles"):
            add(
                [
                    "عزّز mandatory companion articles للحزم القانونية المتكررة.",
                ]
            )
        if diagnostics.get("missing_claim_intents"):
            add(
                [
                    "وسّع claim intent taxonomy واربط كل intent بحزمة مواد ووثائق متوقعة.",
                ]
            )
        if row.get("unexpected_regulations") or row.get("contamination_trap_hits"):
            add(
                [
                    "شدد filtering على الأنظمة غير المتوقعة وفعّل عقوبة أعلى للتلوث بين الأنظمة.",
                ]
            )
        for flag in stable_list(diagnostics.get("issue_flags")):
            action = ISSUE_FLAG_ACTIONS.get(flag)
            if action:
                add([action])

    if not actions:
        add(["راجِع هذه المجموعة يدويًا ثم حوّلها إلى قاعدة عامة داخل router أو reranker أو bundle builder."])

    return actions


def cluster_rows(rows: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row_needs_attention(row, min_score):
            continue
        grouped[build_gap_signature(row)].append(row)

    clusters: list[dict[str, Any]] = []
    for signature, items in grouped.items():
        diagnostics_rows = [(item.get("diagnostics", {}) or {}) for item in items]
        taxonomy = items[0].get("taxonomy", "uncategorized")
        avg_score = round(mean(float(item.get("score", 0.0)) for item in items), 3)
        severity = TAXONOMY_SEVERITY.get(taxonomy, 1.0)
        priority_score = round(len(items) * severity * (1 + (1 - avg_score)), 3)

        clusters.append(
            {
                "gap_signature": signature,
                "taxonomy": taxonomy,
                "case_count": len(items),
                "average_score": avg_score,
                "priority_score": priority_score,
                "question_types": summarize_counter([str(item.get("question_type", "")) for item in items], limit=6),
                "benchmark_categories": summarize_counter(
                    [str(item.get("benchmark_category", "")) for item in items],
                    limit=6,
                ),
                "expected_regulations": summarize_counter(
                    [reg for item in items for reg in stable_list(item.get("expected_regulations"))],
                    limit=8,
                ),
                "dominant_domains": summarize_counter(
                    [str(diagnostics.get("dominant_domain", "")) for diagnostics in diagnostics_rows],
                    limit=8,
                ),
                "issue_flags": summarize_counter(
                    [flag for diagnostics in diagnostics_rows for flag in stable_list(diagnostics.get("issue_flags"))],
                    limit=10,
                ),
                "missing_core_regulations": summarize_counter(
                    [reg for diagnostics in diagnostics_rows for reg in stable_list(diagnostics.get("missing_core_regulations"))],
                    limit=10,
                ),
                "missing_companion_regulations": summarize_counter(
                    [reg for diagnostics in diagnostics_rows for reg in stable_list(diagnostics.get("missing_companion_regulations"))],
                    limit=10,
                ),
                "missing_direct_articles": summarize_counter(
                    [article for diagnostics in diagnostics_rows for article in stable_list(diagnostics.get("missing_direct_articles"))],
                    limit=10,
                ),
                "missing_bundle_articles": summarize_counter(
                    [article for diagnostics in diagnostics_rows for article in stable_list(diagnostics.get("missing_bundle_articles"))],
                    limit=10,
                ),
                "missing_claim_intents": summarize_counter(
                    [intent for diagnostics in diagnostics_rows for intent in stable_list(diagnostics.get("missing_claim_intents"))],
                    limit=10,
                ),
                "unexpected_regulations": summarize_counter(
                    [reg for item in items for reg in stable_list(item.get("unexpected_regulations"))],
                    limit=10,
                ),
                "suggested_actions": collect_actions(items),
                "cases": [
                    {
                        "question_id": item.get("question_id", ""),
                        "score": float(item.get("score", 0.0)),
                        "taxonomy": item.get("taxonomy", ""),
                        "question_type": item.get("question_type", ""),
                        "expected_regulations": item.get("expected_regulations", []),
                        "unexpected_regulations": item.get("unexpected_regulations", []),
                        "missing_core_regulations": (item.get("diagnostics", {}) or {}).get("missing_core_regulations", []),
                        "missing_companion_regulations": (item.get("diagnostics", {}) or {}).get("missing_companion_regulations", []),
                        "missing_direct_articles": (item.get("diagnostics", {}) or {}).get("missing_direct_articles", []),
                        "issue_flags": (item.get("diagnostics", {}) or {}).get("issue_flags", []),
                    }
                    for item in sorted(items, key=lambda current: (float(current.get("score", 0.0)), current.get("question_id", "")))
                ],
            }
        )

    return sorted(clusters, key=lambda item: (-item["priority_score"], item["average_score"], item["gap_signature"]))


def build_top_actions(clusters: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    action_counter: Counter[str] = Counter()
    for cluster in clusters:
        weight = max(1, int(round(cluster.get("priority_score", 1.0))))
        for action in cluster.get("suggested_actions", []):
            action_counter[action] += weight
    return [
        {"action": action, "weight": weight}
        for action, weight in action_counter.most_common(limit)
    ]


def build_summary(
    report: dict[str, Any],
    clusters: list[dict[str, Any]],
    min_score: float,
    report_path: Path,
) -> dict[str, Any]:
    rows = report.get("rows", [])
    attention_rows = [row for row in rows if row_needs_attention(row, min_score)]
    taxonomy_counts = Counter(row.get("taxonomy", "uncategorized") for row in attention_rows)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(report_path),
        "cases_total": len(rows),
        "attention_cases": len(attention_rows),
        "attention_rate": round(len(attention_rows) / max(1, len(rows)), 3),
        "average_score_all": round(mean(float(row.get("score", 0.0)) for row in rows), 3) if rows else 0.0,
        "average_score_attention": round(mean(float(row.get("score", 0.0)) for row in attention_rows), 3)
        if attention_rows
        else 0.0,
        "attention_threshold": min_score,
        "attention_taxonomy_counts": dict(taxonomy_counts),
        "top_actions": build_top_actions(clusters),
        "top_gap_signatures": [
            {
                "gap_signature": cluster["gap_signature"],
                "taxonomy": cluster["taxonomy"],
                "case_count": cluster["case_count"],
                "average_score": cluster["average_score"],
                "priority_score": cluster["priority_score"],
            }
            for cluster in clusters[:10]
        ],
        "weakest_cases": [
            {
                "question_id": row.get("question_id", ""),
                "score": row.get("score", 0.0),
                "taxonomy": row.get("taxonomy", ""),
                "expected_regulations": row.get("expected_regulations", []),
                "unexpected_regulations": row.get("unexpected_regulations", []),
            }
            for row in sorted(attention_rows, key=lambda item: (float(item.get("score", 0.0)), item.get("question_id", "")))[:10]
        ],
    }


def render_counter(counter_rows: list[dict[str, Any]]) -> str:
    if not counter_rows:
        return "لا يوجد"
    return "، ".join(f"{row['value']} ({row['count']})" for row in counter_rows)


def build_markdown_summary(
    benchmark_path: Path | None,
    report_path: Path,
    analysis_path: Path,
    summary: dict[str, Any],
    clusters: list[dict[str, Any]],
) -> str:
    lines = [
        "# Legal RAG Gap Loop",
        "",
        f"- تقرير التقييم: `{report_path}`",
        f"- ملف التحليل: `{analysis_path}`",
    ]
    if benchmark_path:
        lines.append(f"- benchmark: `{benchmark_path}`")
    lines.extend(
        [
            f"- إجمالي الحالات: **{summary['cases_total']}**",
            f"- حالات تحتاج انتباه: **{summary['attention_cases']}** ({summary['attention_rate']})",
            f"- متوسط الدرجة العام: **{summary['average_score_all']}**",
            f"- متوسط الدرجة للحالات المتعثرة: **{summary['average_score_attention']}**",
            "",
            "## أعلى الإجراءات المقترحة",
        ]
    )

    if summary.get("top_actions"):
        for row in summary["top_actions"]:
            lines.append(f"- {row['action']} — وزن {row['weight']}")
    else:
        lines.append("- لا توجد فجوات مفتوحة وفق العتبة الحالية.")

    lines.extend(["", "## أهم عناقيد الفجوات"])

    if not clusters:
        lines.append("- لا توجد عناقيد مفتوحة وفق العتبة الحالية.")
        return "\n".join(lines) + "\n"

    for index, cluster in enumerate(clusters[:10], start=1):
        lines.extend(
            [
                f"### {index}. {cluster['gap_signature']}",
                f"- taxonomy: `{cluster['taxonomy']}`",
                f"- الحالات: **{cluster['case_count']}**",
                f"- متوسط الدرجة: **{cluster['average_score']}**",
                f"- الأولوية: **{cluster['priority_score']}**",
                f"- الأنظمة المتوقعة: {render_counter(cluster['expected_regulations'])}",
                f"- المجالات السائدة: {render_counter(cluster['dominant_domains'])}",
                f"- الأعلام التشخيصية: {render_counter(cluster['issue_flags'])}",
                f"- الأنظمة الجوهرية المفقودة: {render_counter(cluster['missing_core_regulations'])}",
                f"- اللوائح/الأنظمة المرافقة المفقودة: {render_counter(cluster['missing_companion_regulations'])}",
                f"- المواد المباشرة المفقودة: {render_counter(cluster['missing_direct_articles'])}",
                f"- مواد الحزمة المفقودة: {render_counter(cluster['missing_bundle_articles'])}",
                f"- intents المفقودة: {render_counter(cluster['missing_claim_intents'])}",
                f"- الأنظمة غير المتوقعة: {render_counter(cluster['unexpected_regulations'])}",
                "- الإجراءات:",
            ]
        )
        for action in cluster["suggested_actions"][:6]:
            lines.append(f"  - {action}")
        lines.append("- الحالات:")
        for case in cluster["cases"][:5]:
            lines.append(
                "  - "
                f"{case['question_id']} | score={case['score']} | regs={','.join(case['expected_regulations']) or 'n/a'}"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


async def ensure_report(
    benchmark_path: Path | None,
    report_path: Path,
    service_url: str | None,
    per_case_timeout: float,
    category_filter: str | None,
    question_type_filter: str | None,
    force_eval: bool,
) -> Path:
    if report_path.exists() and not force_eval and benchmark_path is None:
        return report_path

    if report_path.exists() and not force_eval and benchmark_path is not None:
        return report_path

    if benchmark_path is None:
        raise ValueError("يلزم benchmark لتشغيل تقييم جديد إذا لم يكن التقرير موجودًا مسبقًا.")

    await base_eval.run_eval(
        benchmark_path,
        report_path,
        per_case_timeout=per_case_timeout,
        category_filter=category_filter,
        question_type_filter=question_type_filter,
        service_url=service_url,
    )
    return report_path


def write_outputs(
    analysis_path: Path,
    summary_path: Path,
    benchmark_path: Path | None,
    report_path: Path,
    report_payload: dict[str, Any],
    clusters: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    analysis_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_path": str(benchmark_path) if benchmark_path else None,
        "report_path": str(report_path),
        "source_summary": report_payload.get("summary", {}),
        "summary": summary,
        "gap_clusters": clusters,
    }
    analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(
        build_markdown_summary(benchmark_path, report_path, analysis_path, summary, clusters),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_EVAL_OUTPUT)
    parser.add_argument("--analysis-output", type=Path, default=DEFAULT_ANALYSIS_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY_OUTPUT)
    parser.add_argument("--service-url", type=str, default=None)
    parser.add_argument("--per-case-timeout", type=float, default=180.0)
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--question-type", type=str, default=None)
    parser.add_argument("--attention-threshold", type=float, default=0.95)
    parser.add_argument("--force-eval", action="store_true")
    args = parser.parse_args()

    benchmark_path = args.benchmark
    if benchmark_path is None and not args.report.exists():
        benchmark_path = DEFAULT_BENCHMARK

    report_path = asyncio.run(
        ensure_report(
            benchmark_path=benchmark_path,
            report_path=args.report,
            service_url=args.service_url,
            per_case_timeout=args.per_case_timeout,
            category_filter=args.category,
            question_type_filter=args.question_type,
            force_eval=args.force_eval,
        )
    )

    report_payload = load_report(report_path)
    clusters = cluster_rows(report_payload.get("rows", []), args.attention_threshold)
    summary = build_summary(report_payload, clusters, args.attention_threshold, report_path)
    write_outputs(
        analysis_path=args.analysis_output,
        summary_path=args.summary_output,
        benchmark_path=benchmark_path,
        report_path=report_path,
        report_payload=report_payload,
        clusters=clusters,
        summary=summary,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved gap analysis to: {args.analysis_output}")
    print(f"Saved markdown summary to: {args.summary_output}")


if __name__ == "__main__":
    main()
