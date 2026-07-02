"""Build a teacher-facing review packet from a legal RAG eval report."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "data" / "eval" / "legal_eval_hard_report.json"
DEFAULT_TAXONOMY = ROOT / "data" / "eval" / "legal_teacher_gap_taxonomy.json"
DEFAULT_OUTPUT_JSON = ROOT / "data" / "eval" / "legal_teacher_review_packet.json"
DEFAULT_OUTPUT_MD = ROOT / "data" / "eval" / "legal_teacher_review_packet.md"

PASSING_TAXONOMIES = {"ok", "correct_refusal"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_list(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    return [str(value) for value in (values or []) if str(value).strip()]


def taxonomy_maps(taxonomy_payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    gap_map = {row["id"]: row for row in taxonomy_payload.get("gap_types", [])}
    flag_map = {row["id"]: row for row in taxonomy_payload.get("issue_flags", [])}
    return gap_map, flag_map


def row_needs_attention(row: dict[str, Any], threshold: float) -> bool:
    return row.get("taxonomy", "") not in PASSING_TAXONOMIES or float(row.get("score", 0.0)) < threshold


def summarize_counter(values: list[str], limit: int = 8) -> list[dict[str, Any]]:
    counter = Counter(value for value in values if value)
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def cluster_key(row: dict[str, Any]) -> str:
    diagnostics = row.get("diagnostics", {}) or {}
    taxonomy = row.get("taxonomy", "uncategorized")
    missing_articles = stable_list(diagnostics.get("missing_direct_articles"))[:3]
    missing_core = stable_list(diagnostics.get("missing_core_regulations"))[:2]
    flags = stable_list(diagnostics.get("issue_flags"))[:2]

    parts = [taxonomy]
    if missing_core:
        parts.append("missing_core=" + ",".join(missing_core))
    if missing_articles:
        parts.append("missing_articles=" + ",".join(missing_articles))
    if flags:
        parts.append("flags=" + ",".join(flags))
    if len(parts) == 1:
        expected = stable_list(row.get("expected_regulations"))[:2]
        if expected:
            parts.append("expected=" + ",".join(expected))
    return " | ".join(parts)


def patch_targets_for_row(
    row: dict[str, Any],
    gap_meta: dict[str, Any] | None,
    flag_map: dict[str, dict[str, Any]],
) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()

    def add(values: list[str] | tuple[str, ...] | None) -> None:
        for value in values or []:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                targets.append(text)

    diagnostics = row.get("diagnostics", {}) or {}
    add((gap_meta or {}).get("patch_targets"))
    for flag in stable_list(diagnostics.get("issue_flags")):
        add((flag_map.get(flag) or {}).get("patch_targets"))
    if diagnostics.get("missing_direct_articles"):
        add(["direct_article_scoring", "article_reranker"])
    if diagnostics.get("missing_companion_regulations"):
        add(["companion_regulations", "bundle_expansion"])
    if row.get("unexpected_regulations"):
        add(["cross_domain_pruning", "negative_traps"])
    return targets


def diagnosis_for_row(
    row: dict[str, Any],
    gap_meta: dict[str, Any] | None,
    flag_map: dict[str, dict[str, Any]],
) -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        normalized = text.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            findings.append(normalized)

    if gap_meta:
        add(gap_meta.get("definition", ""))
        for cause in gap_meta.get("likely_root_causes", []):
            add(str(cause))

    diagnostics = row.get("diagnostics", {}) or {}
    for flag in stable_list(diagnostics.get("issue_flags")):
        meaning = (flag_map.get(flag) or {}).get("meaning", "")
        if meaning:
            add(meaning)

    if diagnostics.get("missing_core_regulations"):
        add("يوجد نظام أو لائحة جوهرية مفقودة من الحزمة النهائية.")
    if diagnostics.get("missing_companion_regulations"):
        add("الحزمة القانونية ناقصة من حيث اللوائح أو الوثائق المرافقة.")
    if diagnostics.get("missing_direct_articles"):
        add("المادة الحاكمة أو المادة التي تغيّر النتيجة لم تظهر بوضوح.")
    if row.get("unexpected_regulations"):
        add("هناك تلوث من أنظمة غير متوقعة في هذا النوع من القضايا.")

    return findings


def build_working_brief(row: dict[str, Any], gap_meta: dict[str, Any] | None) -> dict[str, Any]:
    diagnostics = row.get("diagnostics", {}) or {}
    expected_regulations = stable_list(row.get("expected_regulations"))
    expected_articles = row.get("expected_articles", [])
    return {
        "track": "working",
        "goal": "اختبار قريب من الفشل الحالي للتأكد أن patch يعالج نفس gap family.",
        "design_rule": (gap_meta or {}).get(
            "working_set_test_rule",
            "أنشئ قضية قريبة من الفشل الحالي لكن بصياغة جديدة دون نسخ السؤال حرفيًا.",
        ),
        "must_hit_regulations": expected_regulations,
        "must_hit_articles": expected_articles or diagnostics.get("missing_direct_articles", []),
        "must_cover_flags": stable_list(diagnostics.get("issue_flags")),
        "must_avoid": stable_list(row.get("unexpected_regulations")),
    }


def build_heldout_brief(row: dict[str, Any], gap_meta: dict[str, Any] | None) -> dict[str, Any]:
    diagnostics = row.get("diagnostics", {}) or {}
    expected_regulations = stable_list(row.get("expected_regulations"))
    expected_articles = row.get("expected_articles", [])
    return {
        "track": "heldout",
        "goal": "اختبار جديد غير داخل في حلقة الإصلاح اليومية لكشف overfitting على النمط الحالي.",
        "design_rule": (gap_meta or {}).get(
            "heldout_set_rule",
            "أنشئ قضية جديدة من نفس gap family لكن بكيانات ووقائع وصياغة مختلفة جذريًا.",
        ),
        "must_hit_regulations": expected_regulations,
        "must_hit_articles": expected_articles or diagnostics.get("missing_direct_articles", []),
        "variation_requirements": [
            "غيّر الصياغة السطحية",
            "غيّر نوع الكيان أو السياق التجاري/الوظيفي متى أمكن",
            "لا تكرر الألفاظ المفتاحية نفسها من السؤال الحالي إلا عند الضرورة"
        ],
        "must_avoid": stable_list(row.get("unexpected_regulations")),
    }


def row_packet(
    row: dict[str, Any],
    gap_map: dict[str, dict[str, Any]],
    flag_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    gap_meta = gap_map.get(row.get("taxonomy", ""))
    diagnostics = row.get("diagnostics", {}) or {}
    return {
        "question_id": row.get("question_id", ""),
        "question": row.get("question", ""),
        "score": float(row.get("score", 0.0)),
        "taxonomy": row.get("taxonomy", ""),
        "question_type": row.get("question_type", ""),
        "benchmark_category": row.get("benchmark_category", ""),
        "confidence": row.get("confidence", ""),
        "expected_regulations": row.get("expected_regulations", []),
        "expected_articles": row.get("expected_articles", []),
        "missing_core_regulations": diagnostics.get("missing_core_regulations", []),
        "missing_companion_regulations": diagnostics.get("missing_companion_regulations", []),
        "missing_direct_articles": diagnostics.get("missing_direct_articles", []),
        "missing_bundle_articles": diagnostics.get("missing_bundle_articles", []),
        "unexpected_regulations": row.get("unexpected_regulations", []),
        "issue_flags": diagnostics.get("issue_flags", []),
        "teacher_diagnosis": diagnosis_for_row(row, gap_meta, flag_map),
        "patch_targets": patch_targets_for_row(row, gap_meta, flag_map),
        "working_set_brief": build_working_brief(row, gap_meta),
        "heldout_set_brief": build_heldout_brief(row, gap_meta),
    }


def build_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[cluster_key(row)].append(row)

    clusters: list[dict[str, Any]] = []
    for key, items in grouped.items():
        clusters.append(
            {
                "cluster_key": key,
                "taxonomy": items[0].get("taxonomy", ""),
                "case_count": len(items),
                "average_score": round(mean(float(item.get("score", 0.0)) for item in items), 3),
                "question_types": summarize_counter([str(item.get("question_type", "")) for item in items], limit=6),
                "expected_regulations": summarize_counter(
                    [reg for item in items for reg in stable_list(item.get("expected_regulations"))],
                    limit=8,
                ),
                "issue_flags": summarize_counter(
                    [flag for item in items for flag in stable_list((item.get("diagnostics", {}) or {}).get("issue_flags"))],
                    limit=10,
                ),
                "missing_direct_articles": summarize_counter(
                    [article for item in items for article in stable_list((item.get("diagnostics", {}) or {}).get("missing_direct_articles"))],
                    limit=10,
                ),
                "question_ids": [item.get("question_id", "") for item in items],
            }
        )
    return sorted(clusters, key=lambda item: (item["average_score"], -item["case_count"], item["cluster_key"]))


def build_packet(report: dict[str, Any], taxonomy_payload: dict[str, Any], threshold: float, max_cases: int) -> dict[str, Any]:
    rows = report.get("rows", [])
    gap_map, flag_map = taxonomy_maps(taxonomy_payload)
    attention_rows = [row for row in rows if row_needs_attention(row, threshold)]
    ranked_rows = sorted(attention_rows, key=lambda row: (float(row.get("score", 0.0)), row.get("question_id", "")))
    selected_rows = ranked_rows[:max_cases]
    clusters = build_clusters(attention_rows)

    taxonomy_counts = Counter(row.get("taxonomy", "uncategorized") for row in attention_rows)
    patch_target_counts = Counter(
        target
        for row in selected_rows
        for target in patch_targets_for_row(row, gap_map.get(row.get("taxonomy", "")), flag_map)
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": report.get("summary", {}).get("source_report") or None,
        "source_summary": report.get("summary", {}),
        "threshold": threshold,
        "attention_cases": len(attention_rows),
        "attention_taxonomy_counts": dict(taxonomy_counts),
        "top_patch_targets": [{"target": name, "count": count} for name, count in patch_target_counts.most_common(10)],
        "clusters": clusters[:10],
        "teacher_cases": [row_packet(row, gap_map, flag_map) for row in selected_rows],
        "teacher_rules": taxonomy_payload.get("teacher_rules", {}),
    }


def render_counter(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "لا يوجد"
    return "، ".join(f"{row['value']} ({row['count']})" for row in rows)


def render_markdown(packet: dict[str, Any], report_path: Path, taxonomy_path: Path) -> str:
    lines = [
        "# Teacher Review Packet",
        "",
        f"- تقرير المصدر: `{report_path}`",
        f"- taxonomy: `{taxonomy_path}`",
        f"- عتبة الانتباه: **{packet['threshold']}**",
        f"- الحالات التي تحتاج مراجعة: **{packet['attention_cases']}**",
        "",
        "## أعلى مسارات الإصلاح",
    ]

    if packet.get("top_patch_targets"):
        for row in packet["top_patch_targets"]:
            lines.append(f"- `{row['target']}` — {row['count']}")
    else:
        lines.append("- لا توجد حالات متعثرة فوق العتبة الحالية.")

    lines.extend(["", "## أهم العناقيد"])
    if not packet.get("clusters"):
        lines.append("- لا توجد عناقيد مفتوحة.")
    else:
        for cluster in packet["clusters"]:
            lines.extend(
                [
                    f"### {cluster['cluster_key']}",
                    f"- taxonomy: `{cluster['taxonomy']}`",
                    f"- الحالات: **{cluster['case_count']}**",
                    f"- متوسط الدرجة: **{cluster['average_score']}**",
                    f"- الأنظمة المتوقعة: {render_counter(cluster['expected_regulations'])}",
                    f"- الأعلام: {render_counter(cluster['issue_flags'])}",
                    f"- المواد المباشرة المفقودة: {render_counter(cluster['missing_direct_articles'])}",
                    f"- الحالات: {', '.join(cluster['question_ids'])}",
                    "",
                ]
            )

    lines.extend(["## حالات المعلّم", ""])
    for index, row in enumerate(packet.get("teacher_cases", []), start=1):
        lines.extend(
            [
                f"### {index}. {row['question_id']}",
                f"- score: **{row['score']}**",
                f"- taxonomy: `{row['taxonomy']}`",
                f"- question_type: `{row['question_type']}`",
                f"- confidence: `{row['confidence']}`",
                f"- السؤال: {row['question']}",
                f"- الأنظمة المتوقعة: {', '.join(row['expected_regulations']) or 'لا يوجد'}",
                f"- المواد المتوقعة: {', '.join(str(item) for item in row['expected_articles']) or 'لا يوجد'}",
                f"- الأنظمة الجوهرية المفقودة: {', '.join(row['missing_core_regulations']) or 'لا يوجد'}",
                f"- اللوائح/الأنظمة المرافقة المفقودة: {', '.join(row['missing_companion_regulations']) or 'لا يوجد'}",
                f"- المواد المباشرة المفقودة: {', '.join(str(item) for item in row['missing_direct_articles']) or 'لا يوجد'}",
                f"- الأنظمة غير المتوقعة: {', '.join(row['unexpected_regulations']) or 'لا يوجد'}",
                "- تشخيص المعلّم:",
            ]
        )
        for item in row["teacher_diagnosis"]:
            lines.append(f"  - {item}")
        lines.append("- patch targets:")
        for target in row["patch_targets"]:
            lines.append(f"  - `{target}`")
        lines.append("- working set brief:")
        lines.append(f"  - الهدف: {row['working_set_brief']['goal']}")
        lines.append(f"  - قاعدة التصميم: {row['working_set_brief']['design_rule']}")
        lines.append(
            "  - يجب أن يضرب: "
            + (", ".join(row["working_set_brief"]["must_hit_regulations"]) or "لا يوجد")
        )
        lines.append(
            "  - يجب أن يبرز المواد: "
            + (", ".join(str(item) for item in row["working_set_brief"]["must_hit_articles"]) or "لا يوجد")
        )
        lines.append("- held-out brief:")
        lines.append(f"  - الهدف: {row['heldout_set_brief']['goal']}")
        lines.append(f"  - قاعدة التصميم: {row['heldout_set_brief']['design_rule']}")
        for item in row["heldout_set_brief"]["variation_requirements"]:
            lines.append(f"  - {item}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--max-cases", type=int, default=8)
    args = parser.parse_args()

    report = load_json(args.report)
    taxonomy_payload = load_json(args.taxonomy)
    packet = build_packet(report, taxonomy_payload, args.threshold, args.max_cases)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(render_markdown(packet, args.report, args.taxonomy), encoding="utf-8")

    print(json.dumps(
        {
            "generated_at": packet["generated_at"],
            "report": str(args.report),
            "output_json": str(args.output_json),
            "output_md": str(args.output_md),
            "attention_cases": packet["attention_cases"],
            "teacher_cases": len(packet["teacher_cases"]),
            "top_patch_targets": packet["top_patch_targets"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
