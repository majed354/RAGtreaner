from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any


TAXONOMY_SEVERITY = {
    "ok": 0,
    "partial_confidence": 1,
    "generation_or_validation_failure": 2,
    "package_gap": 2,
    "article_gap": 2,
    "coverage_gap": 2,
    "retrieval_domain_miss": 3,
    "quality_gate_refusal": 3,
    "cross_domain_noise": 4,
    "core_doc_miss": 5,
    "missed_refusal": 5,
}


@dataclass
class GateConfig:
    require_identical_question_set: bool = True
    max_average_score_drop: float = 0.02
    max_domain_purity_drop: float = 0.01
    max_package_completeness_drop: float = 0.02
    max_category_average_score_drop: float = 0.03
    max_case_score_drop: float = 0.05
    protected_case_floor: float = 0.90
    protected_case_min_after: float = 0.85
    fail_on_fatal_core_doc_miss_increase: bool = True
    fail_on_contamination_increase: bool = True


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "summary": data.get("summary", {}),
        "rows": data.get("rows", []),
    }


def row_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = str(row.get("question_id") or "").strip()
        if qid:
            indexed[qid] = row
    return indexed


def summarize_rows_by_category(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        category = str(row.get("benchmark_category") or "uncategorized")
        grouped.setdefault(category, []).append(row)

    summary: dict[str, dict[str, Any]] = {}
    for category, items in sorted(grouped.items()):
        summary[category] = {
            "cases": len(items),
            "average_score": round(mean(float(item.get("score", 0.0)) for item in items), 3),
            "fatal_core_doc_miss_cases": sum(
                1 for item in items if bool((item.get("diagnostics", {}) or {}).get("fatal_core_doc_miss"))
            ),
            "contamination_trap_cases": sum(1 for item in items if item.get("contamination_trap_hits")),
        }
    return summary


def taxonomy_severity(taxonomy: str | None) -> int:
    return TAXONOMY_SEVERITY.get(str(taxonomy or ""), 2)


def make_check(name: str, passed: bool, before: Any = None, after: Any = None, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "before": before,
        "after": after,
        "detail": detail,
    }


def build_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Regression Gate",
        "",
        f"- القرار: `{result['decision']}`",
        f"- before: `{result['before']['path']}`",
        f"- after: `{result['after']['path']}`",
        f"- overlap question ids: `{result['overlap']['question_ids']}`",
        f"- overlap benchmark categories: `{result['overlap']['benchmark_categories']}`",
        "",
        "## Checks",
        "",
    ]
    for check in result["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- `{status}` {check['name']}: {check.get('detail', '')}".rstrip())
    lines.extend(["", "## Blocking Findings", ""])
    if result["blocking_findings"]:
        for finding in result["blocking_findings"]:
            lines.append(f"- `{finding['kind']}`: {finding['message']}")
    else:
        lines.append("- لا توجد")
    lines.extend(["", "## Warnings", ""])
    if result["warnings"]:
        for warning in result["warnings"]:
            lines.append(f"- `{warning['kind']}`: {warning['message']}")
    else:
        lines.append("- لا توجد")
    return "\n".join(lines) + "\n"


def run_gate(before_report: dict[str, Any], after_report: dict[str, Any], config: GateConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    blocking_findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    before_summary = before_report["summary"]
    after_summary = after_report["summary"]
    before_rows = before_report["rows"]
    after_rows = after_report["rows"]

    before_complete = before_summary.get("cases_completed") == before_summary.get("cases_total")
    after_complete = after_summary.get("cases_completed") == after_summary.get("cases_total")
    checks.append(
        make_check(
            "before_report_complete",
            before_complete,
            before_summary.get("cases_completed"),
            before_summary.get("cases_total"),
            "يجب أن يكون تقرير before مكتملًا حتى يكون baseline موثوقًا.",
        )
    )
    checks.append(
        make_check(
            "after_report_complete",
            after_complete,
            after_summary.get("cases_completed"),
            after_summary.get("cases_total"),
            "يجب أن يكون تقرير after مكتملًا قبل اعتماد التعديل.",
        )
    )
    if not before_complete:
        blocking_findings.append(
            {
                "kind": "incomplete_before_report",
                "message": "تقرير before غير مكتمل، لذلك لا يصلح كأساس للمقارنة.",
            }
        )
    if not after_complete:
        blocking_findings.append(
            {
                "kind": "incomplete_after_report",
                "message": "تقرير after غير مكتمل، لذلك لا يمكن اعتماد التعديل عليه.",
            }
        )

    before_index = row_index(before_rows)
    after_index = row_index(after_rows)
    before_question_ids = set(before_index)
    after_question_ids = set(after_index)
    overlap_question_ids = sorted(before_question_ids & after_question_ids)

    question_set_match = before_question_ids == after_question_ids
    checks.append(
        make_check(
            "question_set_match",
            question_set_match if config.require_identical_question_set else True,
            len(before_question_ids),
            len(after_question_ids),
            "الـgate يفترض مقارنة نفس regression set قبل/بعد.",
        )
    )
    if config.require_identical_question_set and not question_set_match:
        missing_in_after = sorted(before_question_ids - after_question_ids)
        added_in_after = sorted(after_question_ids - before_question_ids)
        blocking_findings.append(
            {
                "kind": "question_set_mismatch",
                "message": (
                    "تقارير before/after لا تغطي نفس question_ids. "
                    f"missing_in_after={missing_in_after[:5]} added_in_after={added_in_after[:5]}"
                ),
            }
        )

    def summary_delta(metric_name: str, max_drop: float, label: str) -> None:
        before_value = float(before_summary.get(metric_name, 0.0))
        after_value = float(after_summary.get(metric_name, 0.0))
        delta = round(after_value - before_value, 3)
        passed = delta >= -max_drop
        checks.append(
            make_check(
                label,
                passed,
                before_value,
                after_value,
                f"delta={delta:+.3f}, allowed_drop={max_drop:.3f}",
            )
        )
        if not passed:
            blocking_findings.append(
                {
                    "kind": label,
                    "message": f"{metric_name} تراجع من {before_value:.3f} إلى {after_value:.3f} (delta={delta:+.3f}).",
                }
            )

    summary_delta("average_score", config.max_average_score_drop, "average_score_guard")
    summary_delta("average_domain_purity", config.max_domain_purity_drop, "domain_purity_guard")
    summary_delta(
        "average_package_completeness",
        config.max_package_completeness_drop,
        "package_completeness_guard",
    )

    before_fatal = int(before_summary.get("fatal_core_doc_miss_cases", 0))
    after_fatal = int(after_summary.get("fatal_core_doc_miss_cases", 0))
    fatal_passed = (after_fatal <= before_fatal) or (not config.fail_on_fatal_core_doc_miss_increase)
    checks.append(
        make_check(
            "fatal_core_doc_miss_guard",
            fatal_passed,
            before_fatal,
            after_fatal,
            "لا نسمح بزيادة fatal core doc misses.",
        )
    )
    if not fatal_passed:
        blocking_findings.append(
            {
                "kind": "fatal_core_doc_miss_increase",
                "message": f"زاد عدد fatal_core_doc_miss من {before_fatal} إلى {after_fatal}.",
            }
        )

    before_contam = float(before_summary.get("contamination_trap_rate", 0.0))
    after_contam = float(after_summary.get("contamination_trap_rate", 0.0))
    contamination_passed = (after_contam <= before_contam) or (not config.fail_on_contamination_increase)
    checks.append(
        make_check(
            "contamination_guard",
            contamination_passed,
            before_contam,
            after_contam,
            "لا نسمح بارتفاع contamination trap rate.",
        )
    )
    if not contamination_passed:
        blocking_findings.append(
            {
                "kind": "contamination_increase",
                "message": f"ارتفع contamination_trap_rate من {before_contam:.3f} إلى {after_contam:.3f}.",
            }
        )

    before_category_summary = summarize_rows_by_category(before_rows)
    after_category_summary = summarize_rows_by_category(after_rows)
    overlapping_categories = sorted(set(before_category_summary) & set(after_category_summary))
    category_regressions: list[dict[str, Any]] = []
    for category in overlapping_categories:
        before_avg = float(before_category_summary[category]["average_score"])
        after_avg = float(after_category_summary[category]["average_score"])
        delta = round(after_avg - before_avg, 3)
        if delta < -config.max_category_average_score_drop:
            category_regressions.append(
                {
                    "kind": "category_score_regression",
                    "category": category,
                    "message": (
                        f"الفئة {category} هبط متوسطها من {before_avg:.3f} إلى {after_avg:.3f} "
                        f"(delta={delta:+.3f})."
                    ),
                }
            )
    checks.append(
        make_check(
            "benchmark_category_guard",
            not category_regressions,
            len(overlapping_categories),
            len(category_regressions),
            "لا نسمح بهبوط bundle/category مغلق سابقًا بأكثر من العتبة.",
        )
    )
    blocking_findings.extend(category_regressions)

    case_regressions: list[dict[str, Any]] = []
    severe_case_warnings: list[dict[str, Any]] = []
    for question_id in overlap_question_ids:
        before_row = before_index[question_id]
        after_row = after_index[question_id]
        before_score = float(before_row.get("score", 0.0))
        after_score = float(after_row.get("score", 0.0))
        score_delta = round(after_score - before_score, 3)
        before_tax = str(before_row.get("taxonomy") or "")
        after_tax = str(after_row.get("taxonomy") or "")
        before_diag = before_row.get("diagnostics", {}) or {}
        after_diag = after_row.get("diagnostics", {}) or {}

        if before_score >= config.protected_case_floor:
            if after_score < config.protected_case_min_after:
                case_regressions.append(
                    {
                        "kind": "protected_case_floor_regression",
                        "question_id": question_id,
                        "message": (
                            f"{question_id} كان {before_score:.3f} وأصبح {after_score:.3f}, "
                            f"ونزل تحت protected floor {config.protected_case_min_after:.2f}."
                        ),
                    }
                )
            elif score_delta < -config.max_case_score_drop:
                case_regressions.append(
                    {
                        "kind": "protected_case_score_drop",
                        "question_id": question_id,
                        "message": (
                            f"{question_id} هبط من {before_score:.3f} إلى {after_score:.3f} "
                            f"(delta={score_delta:+.3f})."
                        ),
                    }
                )

        before_fatal_case = bool(before_diag.get("fatal_core_doc_miss"))
        after_fatal_case = bool(after_diag.get("fatal_core_doc_miss"))
        if not before_fatal_case and after_fatal_case:
            case_regressions.append(
                {
                    "kind": "new_fatal_case",
                    "question_id": question_id,
                    "message": f"{question_id} اكتسب fatal_core_doc_miss جديدًا بعد التعديل.",
                }
            )

        before_contam_case = bool(before_row.get("contamination_trap_hits"))
        after_contam_case = bool(after_row.get("contamination_trap_hits"))
        if not before_contam_case and after_contam_case:
            case_regressions.append(
                {
                    "kind": "new_contamination_case",
                    "question_id": question_id,
                    "message": f"{question_id} أصبح يحتوي contamination trap hits جديدة.",
                }
            )

        if taxonomy_severity(after_tax) > taxonomy_severity(before_tax) and after_tax in {
            "core_doc_miss",
            "cross_domain_noise",
            "retrieval_domain_miss",
        }:
            case_regressions.append(
                {
                    "kind": "taxonomy_worsened",
                    "question_id": question_id,
                    "message": f"{question_id} انتقل من taxonomy={before_tax} إلى taxonomy={after_tax}.",
                }
            )
        elif score_delta < 0 and taxonomy_severity(after_tax) > taxonomy_severity(before_tax):
            severe_case_warnings.append(
                {
                    "kind": "taxonomy_warning",
                    "question_id": question_id,
                    "message": (
                        f"{question_id} تراجع taxonomy من {before_tax} إلى {after_tax} "
                        f"مع score delta {score_delta:+.3f}."
                    ),
                }
            )

    checks.append(
        make_check(
            "protected_case_guard",
            not case_regressions,
            len(overlap_question_ids),
            len(case_regressions),
            "لا نسمح بتراجع القضايا الخضراء أو بظهور fatal/contamination جديد.",
        )
    )
    blocking_findings.extend(case_regressions)
    warnings.extend(severe_case_warnings)

    decision = "pass" if not blocking_findings else "fail"
    return {
        "decision": decision,
        "before": {
            "path": before_report["path"],
            "cases_total": before_summary.get("cases_total"),
            "cases_completed": before_summary.get("cases_completed"),
        },
        "after": {
            "path": after_report["path"],
            "cases_total": after_summary.get("cases_total"),
            "cases_completed": after_summary.get("cases_completed"),
        },
        "config": asdict(config),
        "overlap": {
            "question_ids": len(overlap_question_ids),
            "benchmark_categories": len(overlapping_categories),
        },
        "checks": checks,
        "blocking_findings": blocking_findings,
        "warnings": warnings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Regression gate for legal RAG eval reports.")
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    parser.add_argument("--allow-different-question-set", action="store_true")
    parser.add_argument("--max-average-score-drop", type=float, default=0.02)
    parser.add_argument("--max-domain-purity-drop", type=float, default=0.01)
    parser.add_argument("--max-package-completeness-drop", type=float, default=0.02)
    parser.add_argument("--max-category-average-score-drop", type=float, default=0.03)
    parser.add_argument("--max-case-score-drop", type=float, default=0.05)
    parser.add_argument("--protected-case-floor", type=float, default=0.90)
    parser.add_argument("--protected-case-min-after", type=float, default=0.85)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = GateConfig(
        require_identical_question_set=not args.allow_different_question_set,
        max_average_score_drop=args.max_average_score_drop,
        max_domain_purity_drop=args.max_domain_purity_drop,
        max_package_completeness_drop=args.max_package_completeness_drop,
        max_category_average_score_drop=args.max_category_average_score_drop,
        max_case_score_drop=args.max_case_score_drop,
        protected_case_floor=args.protected_case_floor,
        protected_case_min_after=args.protected_case_min_after,
    )
    result = run_gate(load_report(args.before), load_report(args.after), config)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(build_markdown(result), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["decision"] != "pass":
        sys.exit(1)


if __name__ == "__main__":
    main()
