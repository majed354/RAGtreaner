"""Apply a guarded RAG improvement from accumulated article-autopilot gaps.

This is intentionally a batch-level operation, not a one-case patcher.  It
diagnoses the latest autopilot rounds, rebuilds the general support artifacts
from all non-operational autopilot evidence, temporarily installs the candidate
artifacts, validates them, and rolls back if the acceptance gates fail.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUTOPILOT_DIR = ROOT / "data" / "eval" / "article_autopilot"
PACKAGE_ROUTER_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"
ROUTER_SUPPORT_PATH = PACKAGE_ROUTER_DIR / "article_autopilot_router_support_train.jsonl"
ROUTER_TABLE_PATH = PACKAGE_ROUTER_DIR / "package_router_retrieval_table_v1.joblib"
ARTICLE_SUPPORT_PATH = AUTOPILOT_DIR / "article_autopilot_article_support_table_v1.joblib"
MANUAL_SLICE_PATH = ROOT / "data" / "eval" / "manual_article_precision_gate_20260526.jsonl"
FIXED_HOLDOUT_CASES_PATH = AUTOPILOT_DIR / "fixed_holdout_bank_v1.jsonl"
FIXED_HOLDOUT_BASELINE_PATH = AUTOPILOT_DIR / "fixed_holdout_baseline_v1.json"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"
DEFAULT_DEFERRED_BACKLOG_PATH = AUTOPILOT_DIR / "deferred_improvement_backlog.jsonl"
DEFERRED_BACKLOG_MINER = ROOT / "scripts" / "mine_article_autopilot_deferred_backlog.py"


def load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def is_holdout_probe(probe: dict[str, Any] | None) -> bool:
    if not isinstance(probe, dict):
        return False
    return (
        str(probe.get("synthetic_bank") or "").lower() == "holdout"
        or bool(probe.get("holdout_locked"))
        or str(probe.get("split") or "").lower() == "autopilot_holdout"
    )


def is_retry_focus_case(row: dict[str, Any]) -> bool:
    split = str(row.get("split") or "").lower()
    category = str(row.get("benchmark_category") or "").lower()
    auto_review = row.get("auto_review") or {}
    return (
        bool(row.get("retry_focus"))
        or "retry" in split
        or "retry_focus" in category
        or str(auto_review.get("status") or "").lower() == "retry_focus_support"
    )


def run_command(command: list[str], timeout: int) -> None:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()[-3000:]
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{detail}")


def latest_manifests(limit: int) -> list[Path]:
    return sorted(
        AUTOPILOT_DIR.glob("article_autopilot_manifest_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]


def path_from_manifest(manifest: dict[str, Any], key: str) -> Path | None:
    value = str((manifest.get("paths") or {}).get(key) or "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def classify_root_cause(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("transport_error"):
        return "operational issue", "transport_error"
    if row.get("missing_core_regulations"):
        return "retrieval/package issue", "package_router_missing_core"
    if row.get("missing_implementing_regulations"):
        if row.get("missing_article_pairs") and not row.get("unrouted_expected_article_pairs"):
            return "retrieval/package issue", "context_selection_guard_blocked_expected_material"
        return "retrieval/package issue", "package_router_missing_implementing"
    if row.get("unrouted_expected_article_pairs"):
        return "retrieval/package issue", "article_route_surface_gap"
    if row.get("missing_article_pairs"):
        selected_count = row.get("selected_context_count")
        context_limit = None
        profile = row.get("semantic_profile") or {}
        if isinstance(profile, dict):
            context_limit = profile.get("context_limit")
        try:
            at_limit = int(selected_count or 0) >= int(context_limit or 10**9)
        except Exception:
            at_limit = False
        if at_limit:
            return "retrieval/package issue", "context_budget_displacement"
        return "retrieval/package issue", "article_seed_or_ranking_gap"
    if row.get("failed_axes"):
        return "retrieval/package issue", "axis_material_gap"
    if not row.get("passed", True):
        return "answer-level issue", "gate_surface_gap"
    return "ok", "ok"


def collect_batch(batch_round_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    manifests = latest_manifests(batch_round_limit)
    gate_rows: list[dict[str, Any]] = []
    probes_by_id: dict[str, dict[str, Any]] = {}
    for manifest_path in reversed(manifests):
        manifest = load_json(manifest_path)
        gate = load_json(path_from_manifest(manifest, "gate"))
        probes = load_jsonl(path_from_manifest(manifest, "probes"))
        for probe in probes:
            qid = str(probe.get("question_id") or "")
            if qid:
                probes_by_id[qid] = probe
        for row in gate.get("rows") or []:
            probe = probes_by_id.get(str(row.get("question_id") or ""))
            if is_holdout_probe(probe):
                continue
            enriched = dict(row)
            enriched["_source_manifest"] = str(manifest_path.relative_to(ROOT))
            gate_rows.append(enriched)
    probes = [probe for key, probe in sorted(probes_by_id.items()) if not is_holdout_probe(probe)]
    return gate_rows, probes, manifests


def collect_holdout_probes(limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    probes_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(
        AUTOPILOT_DIR.glob("article_autopilot_probes_*.jsonl"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        for probe in load_jsonl(path):
            qid = str(probe.get("question_id") or "")
            if qid and qid not in probes_by_id and is_holdout_probe(probe):
                probes_by_id[qid] = probe
                if limit and len(probes_by_id) >= limit:
                    return list(probes_by_id.values())
    return list(probes_by_id.values())


def _probe_stable_key(probe: dict[str, Any]) -> str:
    qid = str(probe.get("question_id") or probe.get("id") or "").strip()
    if qid:
        return qid
    return json.dumps(probe, ensure_ascii=False, sort_keys=True)[:500]


def stratified_probe_sample(rows: list[dict[str, Any]], limit: int, *, offset: int = 0) -> list[dict[str, Any]]:
    """Select a deterministic, domain-balanced sample for fast guard gates."""
    if limit <= 0 or limit >= len(rows):
        return list(rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        domain = str(row.get("domain") or row.get("benchmark_category") or "uncategorized")
        buckets[domain].append(row)
    for bucket in buckets.values():
        bucket.sort(key=_probe_stable_key)
    domains = sorted(buckets)
    if not domains:
        return list(rows)[:limit]
    rotation = int(offset or 0) % len(domains)
    domains = domains[rotation:] + domains[:rotation]
    indices = {domain: 0 for domain in domains}
    sampled: list[dict[str, Any]] = []
    while len(sampled) < limit:
        progressed = False
        for domain in domains:
            index = indices[domain]
            bucket = buckets[domain]
            if index >= len(bucket):
                continue
            sampled.append(bucket[index])
            indices[domain] = index + 1
            progressed = True
            if len(sampled) >= limit:
                break
        if not progressed:
            break
    return sampled


def deep_diagnose(rows: list[dict[str, Any]], manifests: list[Path]) -> dict[str, Any]:
    classification_counts: Counter[str] = Counter()
    cause_counts: Counter[str] = Counter()
    domain_cause_counts: dict[str, Counter[str]] = defaultdict(Counter)
    missing_pairs: Counter[str] = Counter()
    missing_regulations: Counter[str] = Counter()
    weak_cases: list[dict[str, Any]] = []

    for row in rows:
        classification, cause = classify_root_cause(row)
        classification_counts[classification] += 1
        cause_counts[cause] += 1
        domain = str(row.get("domain") or "uncategorized")
        domain_cause_counts[domain][cause] += 1
        if classification != "ok":
            for pair in (row.get("missing_article_pairs") or []) + (row.get("unrouted_expected_article_pairs") or []):
                missing_pairs[str(pair)] += 1
            for slug in (row.get("missing_core_regulations") or []) + (row.get("missing_implementing_regulations") or []):
                missing_regulations[str(slug)] += 1
            weak_cases.append(
                {
                    "question_id": row.get("question_id"),
                    "domain": domain,
                    "classification": classification,
                    "root_cause": cause,
                    "article_points": row.get("article_points"),
                    "missing_article_pairs": row.get("missing_article_pairs") or [],
                    "unrouted_expected_article_pairs": row.get("unrouted_expected_article_pairs") or [],
                    "missing_core_regulations": row.get("missing_core_regulations") or [],
                    "missing_implementing_regulations": row.get("missing_implementing_regulations") or [],
                    "selected_context_count": row.get("selected_context_count"),
                }
            )

    retrieval_failures = sum(count for cause, count in cause_counts.items() if cause not in {"ok", "transport_error", "gate_surface_gap"})
    interventions: list[str] = []
    if cause_counts.get("package_router_missing_core") or cause_counts.get("package_router_missing_implementing"):
        interventions.append("refresh_package_router_support")
    if (
        cause_counts.get("article_route_surface_gap")
        or cause_counts.get("article_seed_or_ranking_gap")
        or cause_counts.get("context_budget_displacement")
        or cause_counts.get("axis_material_gap")
    ):
        interventions.append("refresh_article_support")
    if "refresh_package_router_support" in interventions:
        interventions.append("refresh_package_router_retrieval_table")

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "batch_rounds": len(manifests),
        "batch_manifests": [str(path.relative_to(ROOT)) for path in manifests],
        "rows": len(rows),
        "classification_counts": dict(classification_counts),
        "root_cause_counts": dict(cause_counts),
        "domain_root_cause_counts": {domain: dict(counter) for domain, counter in sorted(domain_cause_counts.items())},
        "top_missing_article_pairs": [{"pair": pair, "count": count} for pair, count in missing_pairs.most_common(30)],
        "top_missing_regulations": [{"regulation_slug": slug, "count": count} for slug, count in missing_regulations.most_common(30)],
        "weak_cases": sorted(weak_cases, key=lambda item: (float(item.get("article_points") or 0.0), item.get("question_id") or ""))[:100],
        "retrieval_failures": retrieval_failures,
        "operational_failures": cause_counts.get("transport_error", 0),
        "answer_level_failures": cause_counts.get("gate_surface_gap", 0),
        "recommended_interventions": interventions,
    }


def backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    shutil.copy2(path, backup_path)
    manifest = artifact_manifest_path(path)
    if manifest.exists():
        shutil.copy2(manifest, backup_dir / manifest.name)
    return backup_path


def artifact_manifest_path(path: Path) -> Path:
    return path.with_suffix(".manifest.json")


def restore_file(path: Path, backup_path: Path | None, backup_dir: Path) -> None:
    if backup_path and backup_path.exists():
        shutil.copy2(backup_path, path)
    elif path.exists():
        path.unlink()
    manifest = artifact_manifest_path(path)
    backup_manifest = backup_dir / manifest.name
    if backup_manifest.exists():
        shutil.copy2(backup_manifest, manifest)
    elif manifest.exists():
        manifest.unlink()


def cleanup_retained_artifact_dirs(output_dir: Path, keep_count: int) -> dict[str, Any]:
    keep_count = max(0, int(keep_count or 0))
    result: dict[str, Any] = {"keep_count": keep_count, "groups": {}}
    for prefix in ("improvement_backup_", "improvement_staging_"):
        dirs = sorted(
            [path for path in output_dir.iterdir() if path.is_dir() and path.name.startswith(prefix)],
            key=lambda path: path.name,
        )
        to_delete = dirs[:-keep_count] if keep_count else dirs
        removed = 0
        failures: list[dict[str, str]] = []
        for path in to_delete:
            try:
                shutil.rmtree(path)
                removed += 1
            except Exception as exc:
                failures.append({"path": str(path), "error": str(exc)})
        kept = dirs[-keep_count:] if keep_count else []
        result["groups"][prefix] = {
            "before": len(dirs),
            "removed": removed,
            "kept": len(kept),
            "kept_names": [path.name for path in kept],
            "failures": failures[:10],
        }
    return result


def copy_candidate(candidate: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, target)
    candidate_manifest = artifact_manifest_path(candidate)
    target_manifest = artifact_manifest_path(target)
    if candidate_manifest.exists():
        shutil.copy2(candidate_manifest, target_manifest)


def load_summary(path: Path) -> dict[str, Any]:
    return (load_json(path).get("summary") or {})


def accepted(summary: dict[str, Any], min_pass_rate: float) -> bool:
    return (
        int(summary.get("transport_error_cases", 0) or 0) == 0
        and float(summary.get("pass_rate", 0.0) or 0.0) >= min_pass_rate
        and int(summary.get("failed_cases", 0) or 0) == 0
    )


def threshold_accepted(summary: dict[str, Any], min_pass_rate: float) -> bool:
    if int(summary.get("cases_total", 0) or 0) == 0:
        return True
    return (
        int(summary.get("transport_error_cases", 0) or 0) == 0
        and float(summary.get("pass_rate", 0.0) or 0.0) >= min_pass_rate
    )


def fixed_holdout_guard(
    summary: dict[str, Any],
    baseline_manifest: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    baseline = baseline_manifest.get("summary") or {}
    failures: list[dict[str, Any]] = []
    cases_total = int(summary.get("cases_total", 0) or 0)
    baseline_cases = int(baseline.get("cases_total", 0) or 0)
    transport_errors = int(summary.get("transport_error_cases", 0) or 0)
    sampled_fixed_holdout = bool(getattr(args, "allow_sampled_fixed_holdout", False)) and (
        bool(cases_total) and bool(baseline_cases) and cases_total != baseline_cases
    )
    if not baseline_cases:
        failures.append({"metric": "baseline", "reason": "missing_fixed_holdout_baseline"})
    if not cases_total:
        failures.append({"metric": "cases_total", "reason": "missing_fixed_holdout_cases"})
    elif baseline_cases and cases_total != baseline_cases and not sampled_fixed_holdout:
        failures.append(
            {
                "metric": "cases_total",
                "baseline": baseline_cases,
                "actual": cases_total,
                "reason": "incomplete_fixed_holdout_run",
            }
        )
    if transport_errors:
        failures.append(
            {
                "metric": "transport_error_cases",
                "actual": transport_errors,
                "maximum": 0,
                "reason": "operational_fixed_holdout_failure",
            }
        )

    if sampled_fixed_holdout:
        tolerances = {
            "article_score_100": float(args.sampled_fixed_holdout_score_drop),
            "pass_rate": float(args.sampled_fixed_holdout_pass_rate_drop),
            "axis_coverage_rate": float(args.sampled_fixed_holdout_axis_drop),
            "governing_system_rate": float(args.sampled_fixed_holdout_governing_drop),
            "context_entry_rate": float(args.sampled_fixed_holdout_context_drop),
        }
    else:
        tolerances = {
            "article_score_100": float(args.max_fixed_holdout_score_drop),
            "pass_rate": float(args.max_fixed_holdout_pass_rate_drop),
            "axis_coverage_rate": float(args.max_fixed_holdout_axis_drop),
            "governing_system_rate": float(args.max_fixed_holdout_governing_drop),
            "context_entry_rate": float(args.max_fixed_holdout_context_drop),
        }
    comparisons: dict[str, dict[str, Any]] = {}
    for metric, tolerance in tolerances.items():
        if baseline.get(metric) is None:
            continue
        if summary.get(metric) is None:
            failures.append({"metric": metric, "reason": "missing_fixed_holdout_metric"})
            continue
        baseline_value = float(baseline.get(metric) or 0.0)
        actual_value = float(summary.get(metric) or 0.0)
        floor = baseline_value - tolerance
        comparisons[metric] = {
            "baseline": baseline_value,
            "actual": actual_value,
            "allowed_drop": tolerance,
            "floor": floor,
            "delta": round(actual_value - baseline_value, 4),
        }
        if actual_value < floor:
            failures.append(
                {
                    "metric": metric,
                    "baseline": baseline_value,
                    "actual": actual_value,
                    "floor": floor,
                    "reason": "fixed_holdout_regression",
                }
            )
    return {
        "accepted": not failures,
        "baseline_version": baseline_manifest.get("version") or "unknown",
        "baseline_cases": baseline_cases,
        "cases_total": cases_total,
        "sampled": sampled_fixed_holdout,
        "guard_mode": "fast_sample_no_severe_regression" if sampled_fixed_holdout else "full_no_regression",
        "comparisons": comparisons,
        "failures": failures,
    }


def attempt_has_transport_errors(attempt: dict[str, Any]) -> bool:
    for key in ("validation_summary", "manual_summary", "fixed_holdout_summary", "holdout_summary"):
        summary = attempt.get(key) or {}
        if int(summary.get("transport_error_cases", 0) or 0) > 0:
            return True
    return False


def deferred_accepted(
    validation_summary: dict[str, Any],
    manual_summary: dict[str, Any],
    fixed_guard: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    if not args.allow_deferred_failures:
        return False
    return (
        bool(fixed_guard.get("accepted"))
        and int(validation_summary.get("transport_error_cases", 0) or 0) == 0
        and float(validation_summary.get("pass_rate", 0.0) or 0.0) >= float(args.deferred_min_validation_pass_rate)
        and int(validation_summary.get("failed_cases", 0) or 0) > 0
        and accepted(manual_summary, args.min_manual_pass_rate)
    )


def moving_holdout_backlog_accepted(
    *,
    validation_summary: dict[str, Any],
    manual_summary: dict[str, Any],
    moving_holdout_summary: dict[str, Any],
    fixed_guard: dict[str, Any],
    article_support_manifest: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    """Accept exploration gaps only after the frozen no-regression gate passes."""
    if not args.allow_deferred_failures:
        return False
    if not bool(fixed_guard.get("accepted")):
        return False
    if int(moving_holdout_summary.get("transport_error_cases", 0) or 0):
        return False
    if bool(article_support_manifest.get("include_article_surface_rows")) and not bool(
        article_support_manifest.get("article_surface_targeted")
    ):
        return False
    return accepted(validation_summary, args.min_validation_pass_rate) and accepted(
        manual_summary,
        args.min_manual_pass_rate,
    )


def append_deferred_failures(
    *,
    validation_gate: Path,
    manifest_path: Path,
    backlog_path: Path,
    reason: str,
) -> int:
    gate = load_json(validation_gate)
    failed_rows = [
        row for row in gate.get("rows") or []
        if not row.get("passed") and not row.get("transport_error")
    ]
    if not failed_rows:
        return 0
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    with backlog_path.open("a", encoding="utf-8") as handle:
        for row in failed_rows:
            handle.write(
                json.dumps(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source_manifest": str(manifest_path),
                        "reason": reason,
                        "question_id": row.get("question_id"),
                        "domain": row.get("domain"),
                        "article_points": row.get("article_points"),
                        "missing_article_pairs": row.get("missing_article_pairs") or [],
                        "missing_core_regulations": row.get("missing_core_regulations") or [],
                        "missing_implementing_regulations": row.get("missing_implementing_regulations") or [],
                        "failed_axes": row.get("failed_axes") or [],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return len(failed_rows)


def append_rejected_improvement_cycle(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    backlog_path: Path,
    reason: str,
) -> int:
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    gates = {
        "validation": Path(str(manifest.get("validation_gate_path") or "")),
        "manual": Path(str(manifest.get("manual_gate_path") or "")),
        "holdout": Path(str(manifest.get("holdout_gate_path") or "")),
    }
    auto_diagnostics = manifest.get("auto_failure_diagnostics") or []
    last_diagnostic = auto_diagnostics[-1] if auto_diagnostics else {}
    selected_recipe = last_diagnostic.get("selected_recipe") or {}
    records: list[dict[str, Any]] = [
        {
            "record_type": "rejected_improvement_cycle",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_manifest": str(manifest_path),
            "reason": reason,
            "decision": manifest.get("decision"),
            "rollback_reason": manifest.get("rollback_reason"),
            "batch_rounds": manifest.get("batch_rounds"),
            "validation_summary": manifest.get("validation_summary") or {},
            "manual_summary": manifest.get("manual_summary") or {},
            "holdout_summary": manifest.get("holdout_summary") or {},
            "auto_failure_gate": last_diagnostic.get("failure_gate"),
            "auto_root_cause": last_diagnostic.get("top_root_cause"),
            "auto_deep_failure_mode": last_diagnostic.get("deep_failure_mode"),
            "auto_recipe": selected_recipe.get("id"),
            "auto_recipe_escalation_reason": selected_recipe.get("escalation_reason"),
            "auto_same_failure_count": (last_diagnostic.get("history") or {}).get("same_gate_cause_count"),
            "uses_holdout_for_training": bool(selected_recipe.get("uses_holdout_for_training")),
        }
    ]
    for gate_name, gate_path in gates.items():
        gate = load_json(gate_path)
        for row in gate.get("rows") or []:
            if row.get("passed") or row.get("transport_error"):
                continue
            _classification, cause = classify_root_cause(row)
            records.append(
                {
                    "record_type": "rejected_improvement_case",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_manifest": str(manifest_path),
                    "reason": reason,
                    "failure_gate": gate_name,
                    "root_cause": cause,
                    "question_id": row.get("question_id"),
                    "domain": row.get("domain"),
                    "article_points": row.get("article_points"),
                    "missing_article_pairs": row.get("missing_article_pairs") or [],
                    "unrouted_expected_article_pairs": row.get("unrouted_expected_article_pairs") or [],
                    "missing_core_regulations": row.get("missing_core_regulations") or [],
                    "missing_implementing_regulations": row.get("missing_implementing_regulations") or [],
                    "failed_axes": row.get("failed_axes") or [],
                    "expected_article_mean_rank": row.get("expected_article_mean_rank"),
                    "expected_article_mean_context_position": row.get("expected_article_mean_context_position"),
                    "expected_article_mrr": row.get("expected_article_mrr"),
                    "pollution_rate": row.get("pollution_rate"),
                }
            )
    with backlog_path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return len(records)


def build_router_inputs(router_support_path: Path) -> list[str]:
    return [
        str(PACKAGE_ROUTER_DIR / "train.jsonl"),
        str(PACKAGE_ROUTER_DIR / "composite_mixup_train.jsonl"),
        str(PACKAGE_ROUTER_DIR / "gemma_gap_label_support_train.jsonl"),
        str(PACKAGE_ROUTER_DIR / "package_router_generalization_table_v1.jsonl"),
        str(PACKAGE_ROUTER_DIR / "package_router_article_surface_table_v1.jsonl"),
        str(router_support_path),
    ]


def build_candidate_artifacts(
    autopilot_dir: Path,
    staging_dir: Path,
    strategy: dict[str, Any] | None = None,
) -> dict[str, Path]:
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_router_support = staging_dir / ROUTER_SUPPORT_PATH.name
    staged_router_table = staging_dir / ROUTER_TABLE_PATH.name
    staged_article_support = staging_dir / ARTICLE_SUPPORT_PATH.name
    strategy = strategy or {}

    run_command(
        [
            sys.executable,
            "scripts/build_article_autopilot_router_support.py",
            "--autopilot-dir",
            str(autopilot_dir),
            "--output",
            str(staged_router_support),
        ],
        timeout=180,
    )
    article_support_command = [
        sys.executable,
        "scripts/build_article_autopilot_article_support_table.py",
        "--autopilot-dir",
        str(autopilot_dir),
        "--output",
        str(staged_article_support),
        "--inference-min-score",
        str(strategy.get("article_support_min_score", 0.45)),
        "--inference-top-rows",
        str(strategy.get("article_support_top_rows", 8)),
        "--inference-max-article-pairs",
        str(strategy.get("article_support_max_article_pairs", 24)),
    ]
    if strategy.get("include_article_surface_rows"):
        article_support_command.append("--include-article-surface-rows")
        if strategy.get("article_surface_rows_path"):
            article_support_command.extend(
                [
                    "--article-surface-rows",
                    str(strategy.get("article_surface_rows_path")),
                ]
            )
        if strategy.get("article_surface_target_pairs_path"):
            article_support_command.extend(
                [
                    "--article-surface-target-pairs",
                    str(strategy.get("article_surface_target_pairs_path")),
                ]
            )
        if strategy.get("article_surface_max_per_pair") is not None:
            article_support_command.extend(
                [
                    "--article-surface-max-per-pair",
                    str(strategy.get("article_surface_max_per_pair")),
                ]
            )
        if strategy.get("article_surface_max_per_regulation"):
            article_support_command.extend(
                [
                    "--article-surface-max-per-regulation",
                    str(strategy.get("article_surface_max_per_regulation")),
                ]
            )
        if strategy.get("article_surface_max_total"):
            article_support_command.extend(
                [
                    "--article-surface-max-total",
                    str(strategy.get("article_surface_max_total")),
                ]
            )
        if strategy.get("article_surface_score_weight"):
            article_support_command.extend(
                [
                    "--article-surface-score-weight",
                    str(strategy.get("article_surface_score_weight")),
                ]
            )
        if strategy.get("article_surface_min_score"):
            article_support_command.extend(
                [
                    "--article-surface-min-score",
                    str(strategy.get("article_surface_min_score")),
                ]
            )
        if strategy.get("article_surface_max_article_pairs") is not None:
            article_support_command.extend(
                [
                    "--article-surface-max-article-pairs",
                    str(strategy.get("article_surface_max_article_pairs")),
                ]
            )
        if strategy.get("article_surface_max_slugs") is not None:
            article_support_command.extend(
                [
                    "--article-surface-max-slugs",
                    str(strategy.get("article_surface_max_slugs")),
                ]
            )
        if strategy.get("article_surface_require_package_match"):
            article_support_command.append("--article-surface-require-package-match")
    run_command(
        article_support_command,
        timeout=600 if strategy.get("include_article_surface_rows") else 300,
    )
    run_command(
        [
            sys.executable,
            "scripts/build_package_router_retrieval_table.py",
            "--inputs",
            *build_router_inputs(staged_router_support),
            "--output",
            str(staged_router_table),
        ],
        timeout=420,
    )
    return {
        "router_support": staged_router_support,
        "router_table": staged_router_table,
        "article_support": staged_article_support,
    }


def mine_deferred_backlog_support(args: argparse.Namespace, timestamp: str) -> dict[str, Any]:
    if args.disable_deferred_backlog_mining:
        return {"status": "disabled"}
    if not args.deferred_backlog.exists():
        return {"status": "missing_backlog", "backlog_path": str(args.deferred_backlog)}
    report_path = args.output_dir / f"deferred_backlog_mining_report_{timestamp}.json"
    target_pairs_path = args.output_dir / f"deferred_backlog_target_pairs_{timestamp}.jsonl"
    target_surface_path = args.output_dir / f"deferred_backlog_target_surface_rows_{timestamp}.jsonl"
    try:
        run_command(
            [
                sys.executable,
                str(DEFERRED_BACKLOG_MINER),
                "--backlog",
                str(args.deferred_backlog),
                "--output-dir",
                str(args.output_dir),
                "--target-pairs-output",
                str(target_pairs_path),
                "--target-surface-output",
                str(target_surface_path),
                "--report-output",
                str(report_path),
                "--recent-limit",
                str(args.deferred_backlog_recent_limit),
                "--min-unique-questions",
                str(args.deferred_backlog_min_unique_questions),
                "--min-records",
                str(args.deferred_backlog_min_records),
                "--max-pairs",
                str(args.deferred_backlog_max_pairs),
                "--max-surface-rows",
                str(args.deferred_backlog_max_surface_rows),
                "--max-surface-rows-per-pair",
                str(args.deferred_backlog_max_surface_rows_per_pair),
            ],
            timeout=240,
        )
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "backlog_path": str(args.deferred_backlog),
        }
    report = load_json(report_path)
    report["report_path"] = str(report_path)
    report["target_pairs_path"] = str(target_pairs_path)
    report["target_surface_rows_path"] = str(target_surface_path)
    return report


def deferred_backlog_strategy(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if str(report.get("status") or "") != "ok":
        return {}
    if int(report.get("target_surface_rows") or 0) < int(args.deferred_backlog_min_surface_rows):
        return {}
    return {
        "include_article_surface_rows": True,
        "article_surface_rows_path": report.get("target_surface_rows_path"),
        "article_surface_target_pairs_path": report.get("target_pairs_path"),
        "article_surface_max_per_pair": args.deferred_backlog_max_surface_rows_per_pair,
        "article_surface_max_per_regulation": 40,
        "article_surface_max_total": args.deferred_backlog_max_surface_rows,
        "article_surface_score_weight": 0.22,
        "article_surface_min_score": 0.34,
        "article_surface_max_article_pairs": 8,
        "article_surface_max_slugs": 3,
        "article_surface_require_package_match": True,
        "article_support_min_score": 0.43,
        "article_support_top_rows": 10,
        "article_support_max_article_pairs": 26,
        "strategy_id": "targeted_deferred_backlog_surface_support",
    }


def install_candidate_artifacts(artifacts: dict[str, Path]) -> None:
    copy_candidate(artifacts["router_support"], ROUTER_SUPPORT_PATH)
    copy_candidate(artifacts["router_table"], ROUTER_TABLE_PATH)
    copy_candidate(artifacts["article_support"], ARTICLE_SUPPORT_PATH)


def run_gate(
    *,
    cases_path: Path,
    output_path: Path,
    benchmark_id: str,
    args: argparse.Namespace,
    timeout: int,
) -> None:
    run_command(
        [
            sys.executable,
            "scripts/run_article_precision_gate.py",
            "--cases",
            str(cases_path),
            "--output",
            str(output_path),
            "--benchmark-id",
            benchmark_id,
            "--retrieval-profile",
            args.retrieval_profile,
            "--service-url",
            args.service_url,
            "--timeout-seconds",
            str(args.timeout_seconds),
        ],
        timeout=timeout,
    )


def cases_path_contains_retry_focus(path: Path) -> bool:
    return any(is_retry_focus_case(row) for row in load_jsonl(path))


def validate_candidate(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    timestamp: str,
    attempt_label: str,
    batch_cases_path: Path,
    batch_case_count: int,
    fixed_holdout_cases_path: Path,
    fixed_holdout_case_count: int,
    holdout_cases_path: Path,
    holdout_case_count: int,
) -> dict[str, Any]:
    validation_gate = output_dir / f"article_autopilot_improvement_validation_gate_{timestamp}_{attempt_label}.json"
    manual_gate = output_dir / f"manual_article_precision_after_autopilot_improvement_{timestamp}_{attempt_label}.json"
    fixed_holdout_gate = output_dir / f"article_autopilot_fixed_holdout_gate_{timestamp}_{attempt_label}.json"
    holdout_gate = output_dir / f"article_autopilot_improvement_holdout_gate_{timestamp}_{attempt_label}.json"
    result: dict[str, Any] = {
        "attempt": attempt_label,
        "validation_gate_path": str(validation_gate),
        "manual_gate_path": str(manual_gate),
        "fixed_holdout_gate_path": str(fixed_holdout_gate),
        "holdout_gate_path": str(holdout_gate),
        "validation_summary": {},
        "manual_summary": {},
        "fixed_holdout_summary": {},
        "holdout_summary": {},
        "error": "",
        "retry_focus_validation_blocked": False,
    }
    try:
        if cases_path_contains_retry_focus(batch_cases_path):
            result["retry_focus_validation_blocked"] = True
            raise RuntimeError("retry-focus cases cannot be used as validation acceptance cases")
        run_gate(
            cases_path=batch_cases_path,
            output_path=validation_gate,
            benchmark_id=f"article_autopilot_improvement_batch_{timestamp}_{attempt_label}",
            args=args,
            timeout=max(600, int(args.timeout_seconds * max(1, batch_case_count + 1))),
        )
        run_gate(
            cases_path=args.manual_cases,
            output_path=manual_gate,
            benchmark_id=f"article_autopilot_improvement_manual_{timestamp}_{attempt_label}",
            args=args,
            timeout=max(600, int(args.timeout_seconds * 12)),
        )
        if fixed_holdout_case_count:
            run_gate(
                cases_path=fixed_holdout_cases_path,
                output_path=fixed_holdout_gate,
                benchmark_id=f"article_autopilot_fixed_holdout_{timestamp}_{attempt_label}",
                args=args,
                timeout=max(600, int(args.timeout_seconds * max(1, fixed_holdout_case_count + 1))),
            )
        if holdout_case_count:
            run_gate(
                cases_path=holdout_cases_path,
                output_path=holdout_gate,
                benchmark_id=f"article_autopilot_improvement_holdout_{timestamp}_{attempt_label}",
                args=args,
                timeout=max(600, int(args.timeout_seconds * max(1, holdout_case_count + 1))),
            )
    except Exception as exc:
        result["error"] = str(exc)
    result["validation_summary"] = load_summary(validation_gate)
    result["manual_summary"] = load_summary(manual_gate)
    result["fixed_holdout_summary"] = load_summary(fixed_holdout_gate) if fixed_holdout_case_count else {
        "cases_total": 0,
        "article_score_100": 0.0,
        "pass_rate": 0.0,
        "failed_cases": 0,
        "transport_error_cases": 0,
    }
    result["holdout_summary"] = load_summary(holdout_gate) if holdout_case_count else {
        "cases_total": 0,
        "article_score_100": 100.0,
        "pass_rate": 1.0,
        "failed_cases": 0,
        "transport_error_cases": 0,
    }
    return result


def failed_rows_from_gate(path: Path) -> list[dict[str, Any]]:
    gate = load_json(path)
    return [row for row in gate.get("rows") or [] if not row.get("passed")]


def retryable_retrieval_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in failed_rows_from_gate(path):
        classification, _cause = classify_root_cause(row)
        if classification == "retrieval/package issue":
            rows.append(row)
    return rows


def gate_failure_profile(gate_path: Path) -> dict[str, Any]:
    if not gate_path or not gate_path.exists() or not gate_path.is_file():
        return {
            "gate_path": str(gate_path),
            "summary": {},
            "rows": 0,
            "failed_rows": 0,
            "classification_counts": {},
            "root_cause_counts": {},
            "top_root_cause": "missing_gate",
            "top_missing_article_pairs": [],
            "top_missing_regulations": [],
            "mean_rank": None,
            "mean_context_position": None,
            "mean_mrr": None,
            "mean_pollution": None,
            "sample_failed_question_ids": [],
        }
    gate = load_json(gate_path)
    rows = gate.get("rows") or []
    cause_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    missing_pairs: Counter[str] = Counter()
    missing_regulations: Counter[str] = Counter()
    rank_values: list[float] = []
    context_values: list[float] = []
    mrr_values: list[float] = []
    pollution_values: list[float] = []
    failed_rows: list[dict[str, Any]] = []
    for row in rows:
        classification, cause = classify_root_cause(row)
        classification_counts[classification] += 1
        cause_counts[cause] += 1
        if row.get("passed"):
            continue
        failed_rows.append(row)
        for pair in (row.get("missing_article_pairs") or []) + (row.get("unrouted_expected_article_pairs") or []):
            missing_pairs[str(pair)] += 1
        for slug in (row.get("missing_core_regulations") or []) + (row.get("missing_implementing_regulations") or []):
            missing_regulations[str(slug)] += 1
        for key, target in (
            ("expected_article_mean_rank", rank_values),
            ("expected_article_mean_context_position", context_values),
            ("expected_article_mrr", mrr_values),
            ("pollution_rate", pollution_values),
        ):
            value = row.get(key)
            if value is None:
                continue
            try:
                target.append(float(value))
            except Exception:
                continue
    top_cause = cause_counts.most_common(1)[0][0] if cause_counts else "unknown"
    return {
        "gate_path": str(gate_path),
        "summary": gate.get("summary") or {},
        "rows": len(rows),
        "failed_rows": len(failed_rows),
        "classification_counts": dict(classification_counts),
        "root_cause_counts": dict(cause_counts),
        "top_root_cause": top_cause,
        "top_missing_article_pairs": [{"pair": pair, "count": count} for pair, count in missing_pairs.most_common(20)],
        "top_missing_regulations": [{"regulation_slug": slug, "count": count} for slug, count in missing_regulations.most_common(20)],
        "mean_rank": round(sum(rank_values) / len(rank_values), 1) if rank_values else None,
        "mean_context_position": round(sum(context_values) / len(context_values), 1) if context_values else None,
        "mean_mrr": round(sum(mrr_values) / len(mrr_values), 4) if mrr_values else None,
        "mean_pollution": round(sum(pollution_values) / len(pollution_values), 3) if pollution_values else None,
        "sample_failed_question_ids": [str(row.get("question_id") or "") for row in failed_rows[:12]],
    }


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def recent_failure_history(
    *,
    failure_gate: str,
    top_root_cause: str,
    limit: int,
) -> dict[str, Any]:
    manifests = sorted(
        AUTOPILOT_DIR.glob("article_autopilot_improvement_manifest_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: max(0, limit)]
    same_gate_cause_count = 0
    same_root_cause_count = 0
    same_recipe_counts: Counter[str] = Counter()
    rejected_count = 0
    recent: list[dict[str, Any]] = []
    for path in manifests:
        manifest = load_json(path)
        decision = str(manifest.get("decision") or "")
        auto_diagnostics = manifest.get("auto_failure_diagnostics") or []
        last = auto_diagnostics[-1] if auto_diagnostics else {}
        recipe = last.get("selected_recipe") or {}
        gate = str(last.get("failure_gate") or "")
        cause = str(last.get("top_root_cause") or "")
        recipe_id = str(recipe.get("id") or "none")
        if decision == "REJECTED_ROLLED_BACK":
            rejected_count += 1
        if cause == top_root_cause:
            same_root_cause_count += 1
        if gate == failure_gate and cause == top_root_cause:
            same_gate_cause_count += 1
            same_recipe_counts[recipe_id] += 1
        recent.append(
            {
                "manifest": str(path.relative_to(ROOT)),
                "decision": decision,
                "failure_gate": gate,
                "top_root_cause": cause,
                "recipe": recipe_id,
            }
        )
    return {
        "window": len(manifests),
        "rejected_count": rejected_count,
        "same_root_cause_count": same_root_cause_count,
        "same_gate_cause_count": same_gate_cause_count,
        "same_recipe_counts": dict(same_recipe_counts),
        "recent": recent[:8],
    }


def infer_deep_failure_mode(
    *,
    failure_gate: str,
    top_cause: str,
    validation_ok: bool,
    manual_ok: bool,
    holdout_ok: bool,
    profiles: dict[str, dict[str, Any]],
) -> str:
    profile = profiles.get(failure_gate) or {}
    summary = profile.get("summary") or {}
    governing_rate = _float_value(summary.get("governing_system_rate"), 0.0)
    implementing_rate = _float_value(summary.get("implementing_regulation_rate"), 0.0)
    axis_rate = _float_value(summary.get("axis_coverage_rate"), 0.0)
    context_entry_rate = _float_value(summary.get("context_entry_rate"), 1.0)
    pollution_rate = _float_value(summary.get("pollution_rate"), 0.0)
    mean_rank = _float_value(profile.get("mean_rank"), 0.0)
    mean_context_position = _float_value(profile.get("mean_context_position"), 0.0)
    mean_mrr = _float_value(profile.get("mean_mrr"), 1.0)

    if failure_gate == "operational":
        return "operational_validation_error"
    if top_cause in {"package_router_missing_core", "package_router_missing_implementing"}:
        return "package_routing_gap"
    if pollution_rate >= 0.35:
        return "context_purity_gap"
    if top_cause == "context_budget_displacement" or context_entry_rate < 0.80:
        return "context_entry_or_budget_gap"
    if failure_gate in {"holdout", "fixed_holdout"} and validation_ok and not holdout_ok:
        if governing_rate >= 0.90 and implementing_rate >= 0.90:
            if axis_rate < 0.45:
                return f"{failure_gate}_axis_article_surface_generalization_gap"
            if mean_rank >= 25 or mean_context_position >= 25 or mean_mrr < 0.12:
                return f"{failure_gate}_late_context_article_ranking_gap"
            return f"{failure_gate}_article_surface_generalization_gap"
        return f"{failure_gate}_package_or_regulation_generalization_gap"
    if failure_gate == "manual" and not manual_ok:
        return "manual_gold_regression_gap"
    if failure_gate == "validation" and top_cause in {
        "article_route_surface_gap",
        "article_seed_or_ranking_gap",
        "axis_material_gap",
    }:
        return "batch_article_surface_gap"
    return f"{failure_gate}_{top_cause}"


def select_deep_recipe(
    *,
    failure_gate: str,
    top_cause: str,
    deep_failure_mode: str,
    history: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    base_recipe: dict[str, Any] = {
        "id": "none",
        "description": "لا توجد وصفة آلية مناسبة.",
        "uses_holdout_for_training": False,
        "params": {},
        "escalation_reason": "",
    }
    repeated_same_failure = int(history.get("same_gate_cause_count") or 0) >= int(args.force_corpus_surface_after_repeats)
    route_like = top_cause in {
        "article_route_surface_gap",
        "article_seed_or_ranking_gap",
        "axis_material_gap",
        "context_budget_displacement",
    }
    if failure_gate in {"holdout", "fixed_holdout"} and route_like:
        return {
            "id": "article_support_broad_generalization",
            "description": "توسيع أفقي محافظ من بيانات التدريب فقط؛ corpus-surface محجور لأنه أزاح الشريحة اليدوية أو معيار عدم التراجع.",
            "uses_holdout_for_training": False,
            "params": {
                "article_support_min_score": 0.32,
                "article_support_top_rows": 12,
                "article_support_max_article_pairs": 36,
            },
            "escalation_reason": (
                "corpus_surface_quarantined_after_regression"
                if repeated_same_failure or deep_failure_mode
                else "first_fixed_or_moving_holdout_surface_attempt"
            ),
        }
    if failure_gate == "validation" and top_cause in {
        "article_route_surface_gap",
        "article_seed_or_ranking_gap",
        "axis_material_gap",
        "context_budget_displacement",
        "package_router_missing_core",
        "package_router_missing_implementing",
    }:
        return {
            "id": "validation_retry_focus_support",
            "description": "استخدام أسئلة تركيز للفشل في دفعة التدريب كدعم فقط، ثم التحقق على السؤال الطبيعي.",
            "uses_holdout_for_training": False,
            "params": {},
            "escalation_reason": "batch_validation_failure",
        }
    if failure_gate == "manual" and route_like and repeated_same_failure:
        return {
            "id": "none",
            "description": "تكرر فشل manual بعد توسيع دعم المواد؛ لا توجد وصفة آمنة تلقائيًا، فيتم rollback وجمع دفعات أفقية إضافية.",
            "uses_holdout_for_training": False,
            "params": {},
            "escalation_reason": "manual_regression_blocks_corpus_surface",
        }
    return base_recipe


def alternate_recipe_after_in_run_repeat(
    recipe: dict[str, Any],
    failure_diagnosis: dict[str, Any],
) -> dict[str, Any]:
    recipe_id = str(recipe.get("id") or "none")
    top_cause = str(failure_diagnosis.get("top_root_cause") or "")
    failure_gate = str(failure_diagnosis.get("failure_gate") or "")
    route_like = top_cause in {
        "article_route_surface_gap",
        "article_seed_or_ranking_gap",
        "axis_material_gap",
        "context_budget_displacement",
    }
    if recipe_id == "corpus_article_surface_support" and failure_gate in {"holdout", "fixed_holdout"} and route_like:
        return {
            "id": "none",
            "description": "corpus-surface محجور بعد ثبوت إزاحة المواد في manual/holdout؛ لا توجد محاولة آمنة داخل الدورة نفسها.",
            "uses_holdout_for_training": False,
            "params": {},
            "escalation_reason": "corpus_surface_quarantined",
        }
    return {
        "id": "none",
        "description": "لا توجد وصفة بديلة آمنة بعد تكرار الوصفة داخل نفس دورة التحسين.",
        "uses_holdout_for_training": False,
        "params": {},
        "escalation_reason": "in_run_repeat_without_safe_alternative",
    }


def build_attempt_failure_diagnosis(attempt_result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    validation_summary = attempt_result.get("validation_summary") or {}
    manual_summary = attempt_result.get("manual_summary") or {}
    fixed_holdout_summary = attempt_result.get("fixed_holdout_summary") or {}
    holdout_summary = attempt_result.get("holdout_summary") or {}
    fixed_guard = attempt_result.get("fixed_holdout_guard") or fixed_holdout_guard(
        fixed_holdout_summary,
        load_json(args.fixed_holdout_baseline),
        args,
    )
    validation_ok = accepted(validation_summary, args.min_validation_pass_rate)
    validation_near_ok = (
        int(validation_summary.get("transport_error_cases", 0) or 0) == 0
        and float(validation_summary.get("pass_rate", 0.0) or 0.0) >= float(args.deferred_min_validation_pass_rate)
    )
    manual_ok = accepted(manual_summary, args.min_manual_pass_rate)
    fixed_ok = bool(fixed_guard.get("accepted"))
    holdout_ok = threshold_accepted(holdout_summary, args.min_holdout_pass_rate)
    failed_gates: list[str] = []
    if not validation_ok:
        failed_gates.append("validation")
    if not manual_ok:
        failed_gates.append("manual")
    if not fixed_ok:
        failed_gates.append("fixed_holdout")
    if not holdout_ok:
        failed_gates.append("holdout")
    profiles = {
        "validation": gate_failure_profile(Path(str(attempt_result.get("validation_gate_path") or ""))),
        "manual": gate_failure_profile(Path(str(attempt_result.get("manual_gate_path") or ""))),
        "fixed_holdout": gate_failure_profile(Path(str(attempt_result.get("fixed_holdout_gate_path") or ""))),
        "holdout": gate_failure_profile(Path(str(attempt_result.get("holdout_gate_path") or ""))),
    }
    if attempt_result.get("error"):
        failure_gate = "operational"
        top_cause = "validation_error"
        issue_class = "operational issue"
    elif not fixed_ok and validation_near_ok:
        failure_gate = "fixed_holdout"
        top_cause = profiles["fixed_holdout"].get("top_root_cause") or "fixed_holdout_regression"
        issue_class = "retrieval/package issue"
    elif not holdout_ok and validation_near_ok:
        failure_gate = "holdout"
        top_cause = profiles["holdout"].get("top_root_cause") or "unknown"
        issue_class = "retrieval/package issue"
    elif not validation_ok:
        failure_gate = "validation"
        top_cause = profiles["validation"].get("top_root_cause") or "unknown"
        issue_class = "retrieval/package issue"
    elif not manual_ok:
        failure_gate = "manual"
        top_cause = profiles["manual"].get("top_root_cause") or "unknown"
        issue_class = "retrieval/package issue"
    elif not holdout_ok:
        failure_gate = "holdout"
        top_cause = profiles["holdout"].get("top_root_cause") or "unknown"
        issue_class = "retrieval/package issue"
    else:
        failure_gate = "none"
        top_cause = "ok"
        issue_class = "ok"

    deep_failure_mode = infer_deep_failure_mode(
        failure_gate=failure_gate,
        top_cause=top_cause,
        validation_ok=validation_ok,
        manual_ok=manual_ok,
        holdout_ok=fixed_ok and holdout_ok,
        profiles=profiles,
    )
    history = recent_failure_history(
        failure_gate=failure_gate,
        top_root_cause=top_cause,
        limit=args.deep_diagnosis_history_limit,
    )
    recipe = select_deep_recipe(
        failure_gate=failure_gate,
        top_cause=top_cause,
        deep_failure_mode=deep_failure_mode,
        history=history,
        args=args,
    )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt_result.get("attempt"),
        "failure_gate": failure_gate,
        "issue_class": issue_class,
        "top_root_cause": top_cause,
        "deep_failure_mode": deep_failure_mode,
        "failed_gates": failed_gates,
        "secondary_failed_gates": [gate for gate in failed_gates if gate != failure_gate],
        "history": history,
        "validation_ok": validation_ok,
        "manual_ok": manual_ok,
        "fixed_holdout_ok": fixed_ok,
        "fixed_holdout_guard": fixed_guard,
        "holdout_ok": holdout_ok,
        "profiles": profiles,
        "selected_recipe": recipe,
    }


def parse_article_pairs(pairs: list[Any]) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for item in pairs or []:
        raw = str(item)
        if ":" not in raw:
            continue
        slug, article_raw = raw.rsplit(":", 1)
        try:
            article = int(article_raw)
        except Exception:
            continue
        if slug and article > 0 and article not in grouped[slug]:
            grouped[slug].append(article)
    return {slug: sorted(values) for slug, values in grouped.items()}


def merge_expected_articles(*maps: dict[str, Any]) -> dict[str, list[int]]:
    merged: dict[str, list[int]] = defaultdict(list)
    for mapping in maps:
        if not isinstance(mapping, dict):
            continue
        for slug, articles in mapping.items():
            for article in articles or []:
                try:
                    value = int(article)
                except Exception:
                    continue
                if value > 0 and value not in merged[str(slug)]:
                    merged[str(slug)].append(value)
    return {slug: sorted(values) for slug, values in merged.items()}


def regulation_title(slug: str) -> str:
    try:
        from app.rag.engine import REGULATION_TITLE_OVERRIDES

        return str(REGULATION_TITLE_OVERRIDES.get(slug) or slug)
    except Exception:
        return slug


def retry_question_variants(
    *,
    base_question: str,
    domain: str,
    slugs: list[str],
    article_pairs: dict[str, list[int]],
    failed_axes: list[str],
) -> list[str]:
    titles = [regulation_title(slug) for slug in slugs]
    articles_text = "، ".join(
        f"{regulation_title(slug)} المواد {', '.join(str(item) for item in articles)}"
        for slug, articles in article_pairs.items()
    )
    axis_text = "، ".join(str(axis) for axis in failed_axes if axis) or "محاور الواقعة"
    variants = [base_question]
    if titles:
        variants.append(
            f"{base_question}\nاجمع الحزمة ذات الصلة بمحور {axis_text} مع التركيز على: {'، '.join(titles)}."
        )
    if articles_text:
        variants.append(
            f"واقعة في مجال {domain}: {base_question}\nالمطلوب جمع المواد الدقيقة التالية عند انطباقها: {articles_text}."
        )
    return list(dict.fromkeys(" ".join(variant.split()) for variant in variants if variant.strip()))


def prepare_retry_autopilot_dir(
    *,
    source_dir: Path,
    retry_dir: Path,
    failed_rows: list[dict[str, Any]],
    batch_probes: list[dict[str, Any]],
    timestamp: str,
    attempt: int,
) -> dict[str, Any]:
    retry_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("article_autopilot_probes_*.jsonl", "article_autopilot_gate_*.json"):
        for path in source_dir.glob(pattern):
            shutil.copy2(path, retry_dir / path.name)

    probes_by_id = {str(probe.get("question_id") or ""): probe for probe in batch_probes if probe.get("question_id")}
    retry_probes: list[dict[str, Any]] = []
    retry_gate_rows: list[dict[str, Any]] = []
    for row in failed_rows:
        qid = str(row.get("question_id") or "")
        probe = probes_by_id.get(qid, {})
        missing_pairs = parse_article_pairs(
            (row.get("missing_article_pairs") or [])
            + (row.get("missing_direct_article_pairs") or [])
            + (row.get("missing_bundle_article_pairs") or [])
            + (row.get("unrouted_expected_article_pairs") or [])
        )
        expected_articles = merge_expected_articles(probe.get("expected_articles_by_slug") or {}, missing_pairs)
        if not expected_articles:
            continue
        missing_core = [str(item) for item in row.get("missing_core_regulations") or [] if item]
        missing_implementing = [str(item) for item in row.get("missing_implementing_regulations") or [] if item]
        expected_core = list(dict.fromkeys([*(probe.get("expected_core_regulations") or []), *missing_core]))
        expected_implementing = list(
            dict.fromkeys([*(probe.get("expected_implementing_regulations") or []), *missing_implementing])
        )
        if not expected_core and not expected_implementing:
            expected_implementing = list(expected_articles)
        expected_companions = list(probe.get("expected_companion_regulations") or [])
        slugs = list(dict.fromkeys([*expected_core, *expected_implementing, *expected_companions, *expected_articles.keys()]))
        base_question = str(probe.get("question") or row.get("question") or "")
        domain = str(probe.get("domain") or row.get("domain") or "article_autopilot_retry")
        failed_axes = [str(item) for item in row.get("failed_axes") or [] if item]
        variants = retry_question_variants(
            base_question=base_question,
            domain=domain,
            slugs=slugs,
            article_pairs=expected_articles,
            failed_axes=failed_axes,
        )
        for index, question in enumerate(variants, start=1):
            retry_qid = f"{qid}_retry{attempt}_{index}"
            retry_probe = dict(probe)
            retry_probe.update(
                {
                    "question_id": retry_qid,
                    "question": question,
                    "split": "autopilot_retry",
                    "domain": domain,
                    "benchmark_category": "article_autopilot_retry_focus",
                    "scenario_family_id": probe.get("scenario_family_id") or f"retry::{domain}",
                    "expected_articles_by_slug": expected_articles,
                    "expected_core_regulations": expected_core,
                    "expected_implementing_regulations": expected_implementing,
                    "expected_companion_regulations": expected_companions,
                    "axis_article_pairs": {
                        failed_axes[0] if failed_axes else "retry_focus": [
                            f"{slug}:{article}" for slug, articles in expected_articles.items() for article in articles
                        ]
                    },
                    "auto_review": {
                        **(probe.get("auto_review") or {}),
                        "status": "retry_focus_support",
                        "source_question_id": qid,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            retry_probes.append(retry_probe)
            retry_gate_rows.append(
                {
                    "question_id": retry_qid,
                    "domain": domain,
                    "passed": False,
                    "transport_error": False,
                    "article_points": row.get("article_points", 0.0),
                    "missing_article_pairs": [
                        f"{slug}:{article}" for slug, articles in expected_articles.items() for article in articles
                    ],
                    "missing_core_regulations": missing_core,
                    "missing_implementing_regulations": missing_implementing,
                    "failed_axes": failed_axes,
                    "source_question_id": qid,
                    "retry_focus": True,
                }
            )

    retry_probes_path = retry_dir / f"article_autopilot_probes_retry_focus_{timestamp}_attempt{attempt}.jsonl"
    retry_gate_path = retry_dir / f"article_autopilot_gate_retry_focus_{timestamp}_attempt{attempt}.json"
    write_jsonl(retry_probes_path, retry_probes)
    retry_gate_path.write_text(
        json.dumps(
            {
                "summary": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "cases_total": len(retry_gate_rows),
                    "retry_focus_attempt": attempt,
                },
                "rows": retry_gate_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "retry_dir": str(retry_dir),
        "retry_probe_rows": len(retry_probes),
        "retry_gate_rows": len(retry_gate_rows),
        "failed_source_rows": len(failed_rows),
        "retry_probes_path": str(retry_probes_path),
        "retry_gate_path": str(retry_gate_path),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    batch_rows, batch_probes, manifests = collect_batch(args.batch_round_limit)
    holdout_probes = collect_holdout_probes(max(0, args.holdout_limit))
    fixed_holdout_all_probes = load_jsonl(args.fixed_holdout_cases)
    fixed_holdout_limit = int(args.fixed_holdout_limit or 0)
    fixed_holdout_probes = stratified_probe_sample(
        fixed_holdout_all_probes,
        fixed_holdout_limit,
        offset=int(args.fixed_holdout_sample_offset or 0),
    )
    fixed_holdout_baseline = load_json(args.fixed_holdout_baseline)
    batch_cases_path = output_dir / f"article_autopilot_improvement_batch_cases_{timestamp}.jsonl"
    fixed_holdout_sample_path = output_dir / f"article_autopilot_fixed_holdout_sample_cases_{timestamp}.jsonl"
    holdout_cases_path = output_dir / f"article_autopilot_improvement_holdout_cases_{timestamp}.jsonl"
    diagnosis_path = output_dir / f"article_autopilot_improvement_deep_diagnosis_{timestamp}.json"
    manifest_path = output_dir / f"article_autopilot_improvement_manifest_{timestamp}.json"
    write_jsonl(batch_cases_path, batch_probes)
    active_fixed_holdout_cases_path = args.fixed_holdout_cases
    if len(fixed_holdout_probes) != len(fixed_holdout_all_probes):
        write_jsonl(fixed_holdout_sample_path, fixed_holdout_probes)
        active_fixed_holdout_cases_path = fixed_holdout_sample_path
    write_jsonl(holdout_cases_path, holdout_probes)

    diagnosis = deep_diagnose(batch_rows, manifests)
    diagnosis_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not batch_rows:
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision": "NO_BATCH",
            "validation_mode": args.validation_mode,
            "batch_rounds": 0,
            "diagnosis_path": str(diagnosis_path),
            "fixed_holdout_cases_path": str(active_fixed_holdout_cases_path),
            "fixed_holdout_source_cases_path": str(args.fixed_holdout_cases),
            "fixed_holdout_case_count": len(fixed_holdout_probes),
            "fixed_holdout_total_case_count": len(fixed_holdout_all_probes),
            "fixed_holdout_sampled": len(fixed_holdout_probes) != len(fixed_holdout_all_probes),
            "holdout_cases_path": str(holdout_cases_path),
            "holdout_case_count": len(holdout_probes),
            "message": "لا توجد جولات محفوظة لتحسينها.",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    if diagnosis["retrieval_failures"] == 0 and diagnosis["operational_failures"] > 0:
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision": "OPERATIONAL_ONLY_NO_RAG_CHANGE",
            "batch_rounds": len(manifests),
            "diagnosis_path": str(diagnosis_path),
            "diagnosis": diagnosis,
            "message": "الفشل تشغيلي فقط؛ لم يتم تعديل RAG.",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    if diagnosis["retrieval_failures"] == 0:
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision": "NO_RAG_CHANGE_NEEDED",
            "batch_rounds": len(manifests),
            "diagnosis_path": str(diagnosis_path),
            "diagnosis": diagnosis,
            "message": (
                "لا توجد فجوات retrieval/package في آخر دفعة تدريب؛ "
                "هذا لا يعني اكتمال holdout، وسيواصل التطوير الاستكشاف الأفقي."
            ),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    deferred_backlog_mining = mine_deferred_backlog_support(args, timestamp)
    initial_strategy = deferred_backlog_strategy(deferred_backlog_mining, args)

    staging_dir = output_dir / f"improvement_staging_{timestamp}"
    backup_dir = output_dir / f"improvement_backup_{timestamp}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    backup_router_support = backup_file(ROUTER_SUPPORT_PATH, backup_dir)
    backup_router_table = backup_file(ROUTER_TABLE_PATH, backup_dir)
    backup_article_support = backup_file(ARTICLE_SUPPORT_PATH, backup_dir)

    attempts: list[dict[str, Any]] = []
    retry_preparations: list[dict[str, Any]] = []
    auto_failure_diagnostics: list[dict[str, Any]] = []
    candidate_artifacts = build_candidate_artifacts(AUTOPILOT_DIR, staging_dir / "attempt_1", strategy=initial_strategy)
    install_candidate_artifacts(candidate_artifacts)

    validation_gate = Path("")
    manual_gate = Path("")
    validation_summary: dict[str, Any] = {}
    manual_summary: dict[str, Any] = {}
    fixed_holdout_summary: dict[str, Any] = {}
    fixed_guard: dict[str, Any] = {}
    holdout_summary: dict[str, Any] = {}
    decision = "REJECTED_ROLLED_BACK"
    rollback_reason = ""
    accepted_after_attempt = 0
    deferred_failure_count = 0

    for attempt in range(1, max(1, args.retry_attempts + 1) + 1):
        attempt_label = f"attempt{attempt}"
        if attempt > 1:
            previous_attempt = attempts[-1]
            failure_diagnosis = build_attempt_failure_diagnosis(previous_attempt, args)
            if args.disable_auto_deep_diagnosis:
                failure_diagnosis["selected_recipe"] = {
                    "id": "validation_retry_focus_support",
                    "description": "تشخيص الفشل الآلي معطل؛ استخدام مسار retry-focus القديم عند توفر فشل في دفعة التدريب.",
                    "uses_holdout_for_training": False,
                    "params": {},
                }
            auto_failure_diagnostics.append(failure_diagnosis)
            recipe = failure_diagnosis.get("selected_recipe") or {}
            used_recipe_ids = {
                str((item.get("selected_recipe") or {}).get("id") or "none")
                for item in auto_failure_diagnostics[:-1]
            }
            if str(recipe.get("id") or "none") in used_recipe_ids:
                recipe = alternate_recipe_after_in_run_repeat(recipe, failure_diagnosis)
                failure_diagnosis["selected_recipe"] = recipe
                failure_diagnosis["in_run_recipe_repeat"] = True
            recipe_id = str(recipe.get("id") or "none")
            if recipe_id in {
                "article_support_broad_generalization",
                "corpus_article_surface_support",
                "article_surface_rank_rescue",
            }:
                retry_info = {
                    "retry_dir": str(AUTOPILOT_DIR),
                    "attempt": attempt,
                    "source": "auto_deep_failure_diagnosis",
                    "failure_gate": failure_diagnosis.get("failure_gate"),
                    "top_root_cause": failure_diagnosis.get("top_root_cause"),
                    "deep_failure_mode": failure_diagnosis.get("deep_failure_mode"),
                    "recipe": recipe,
                }
                retry_preparations.append(retry_info)
                candidate_artifacts = build_candidate_artifacts(
                    AUTOPILOT_DIR,
                    staging_dir / f"attempt_{attempt}",
                    strategy=recipe.get("params") or {},
                )
                install_candidate_artifacts(candidate_artifacts)
            elif recipe_id == "validation_retry_focus_support":
                previous_gate = Path(str(previous_attempt.get("validation_gate_path") or ""))
                failed_rows = retryable_retrieval_rows(previous_gate)
                if not failed_rows:
                    rollback_reason = "batch validation gate failed; no retryable retrieval rows"
                    break
                retry_dir = staging_dir / f"retry_autopilot_attempt_{attempt}"
                retry_info = prepare_retry_autopilot_dir(
                    source_dir=AUTOPILOT_DIR,
                    retry_dir=retry_dir,
                    failed_rows=failed_rows,
                    batch_probes=batch_probes,
                    timestamp=timestamp,
                    attempt=attempt,
                )
                retry_info["source"] = "auto_deep_failure_diagnosis"
                retry_info["failure_gate"] = failure_diagnosis.get("failure_gate")
                retry_info["top_root_cause"] = failure_diagnosis.get("top_root_cause")
                retry_info["recipe"] = recipe
                retry_preparations.append(retry_info)
                if not retry_info.get("retry_probe_rows"):
                    rollback_reason = "batch validation gate failed; retry focus produced no support rows"
                    break
                candidate_artifacts = build_candidate_artifacts(retry_dir, staging_dir / f"attempt_{attempt}")
                install_candidate_artifacts(candidate_artifacts)
            else:
                rollback_reason = (
                    f"auto deep diagnosis found no safe recipe for "
                    f"{failure_diagnosis.get('failure_gate')}:{failure_diagnosis.get('top_root_cause')}"
                )
                break

        attempt_result = validate_candidate(
            args=args,
            output_dir=output_dir,
            timestamp=timestamp,
            attempt_label=attempt_label,
            batch_cases_path=batch_cases_path,
            batch_case_count=len(batch_probes),
            fixed_holdout_cases_path=active_fixed_holdout_cases_path,
            fixed_holdout_case_count=len(fixed_holdout_probes),
            holdout_cases_path=holdout_cases_path,
            holdout_case_count=len(holdout_probes),
        )
        attempt_result["candidate_manifests"] = {
            "router_support": load_json(artifact_manifest_path(candidate_artifacts["router_support"])),
            "article_support": load_json(artifact_manifest_path(candidate_artifacts["article_support"])),
            "router_table": load_json(artifact_manifest_path(candidate_artifacts["router_table"])),
        }
        if attempt > 1 and auto_failure_diagnostics:
            attempt_result["auto_failure_diagnosis"] = auto_failure_diagnostics[-1]
        attempts.append(attempt_result)
        validation_gate = Path(str(attempt_result["validation_gate_path"]))
        manual_gate = Path(str(attempt_result["manual_gate_path"]))
        validation_summary = attempt_result.get("validation_summary") or {}
        manual_summary = attempt_result.get("manual_summary") or {}
        fixed_holdout_summary = attempt_result.get("fixed_holdout_summary") or {}
        holdout_summary = attempt_result.get("holdout_summary") or {}
        fixed_guard = fixed_holdout_guard(fixed_holdout_summary, fixed_holdout_baseline, args)
        attempt_result["fixed_holdout_guard"] = fixed_guard

        if attempt_result.get("error"):
            rollback_reason = f"validation error: {attempt_result['error']}"
            continue
        if not fixed_guard.get("accepted"):
            rollback_reason = "fixed holdout no-regression gate failed"
            continue
        if not accepted(validation_summary, args.min_validation_pass_rate):
            if deferred_accepted(validation_summary, manual_summary, fixed_guard, args):
                accepted_after_attempt = attempt
                decision = "ACCEPTED_WITH_DEFERRED_FAILURES"
                rollback_reason = ""
                deferred_failure_count = append_deferred_failures(
                    validation_gate=validation_gate,
                    manifest_path=manifest_path,
                    backlog_path=args.deferred_backlog,
                    reason="continuous_development_auto_accept_threshold",
                )
                break
            rollback_reason = "batch validation gate failed"
            continue
        if not accepted(manual_summary, args.min_manual_pass_rate):
            rollback_reason = "manual slice gate failed"
            break
        if not threshold_accepted(holdout_summary, args.min_holdout_pass_rate):
            article_support_manifest = (attempt_result.get("candidate_manifests") or {}).get("article_support") or {}
            if moving_holdout_backlog_accepted(
                validation_summary=validation_summary,
                manual_summary=manual_summary,
                moving_holdout_summary=holdout_summary,
                fixed_guard=fixed_guard,
                article_support_manifest=article_support_manifest,
                args=args,
            ):
                accepted_after_attempt = attempt
                decision = "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG"
                rollback_reason = ""
                deferred_failure_count = append_deferred_failures(
                    validation_gate=Path(str(attempt_result.get("holdout_gate_path") or "")),
                    manifest_path=manifest_path,
                    backlog_path=args.deferred_backlog,
                    reason="continuous_development_moving_holdout_backlog_after_fixed_pass",
                )
                break
            rollback_reason = "synthetic holdout gate failed"
            continue
        accepted_after_attempt = attempt
        decision = "ACCEPTED" if attempt == 1 else "ACCEPTED_AFTER_RETRY"
        rollback_reason = ""
        break

    if decision == "REJECTED_ROLLED_BACK" and any(attempt_has_transport_errors(attempt) for attempt in attempts):
        decision = "OPERATIONAL_ONLY_NO_RAG_CHANGE"
        rollback_reason = "operational transport error during validation; no RAG change accepted"

    if decision not in {
        "ACCEPTED",
        "ACCEPTED_AFTER_RETRY",
        "ACCEPTED_WITH_DEFERRED_FAILURES",
        "ACCEPTED_WITH_HOLDOUT_BACKLOG",
        "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG",
    }:
        restore_file(ROUTER_SUPPORT_PATH, backup_router_support, backup_dir)
        restore_file(ROUTER_TABLE_PATH, backup_router_table, backup_dir)
        restore_file(ARTICLE_SUPPORT_PATH, backup_article_support, backup_dir)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "validation_mode": args.validation_mode,
        "rollback_reason": rollback_reason,
        "batch_rounds": len(manifests),
        "batch_cases_path": str(batch_cases_path),
        "fixed_holdout_cases_path": str(active_fixed_holdout_cases_path),
        "fixed_holdout_source_cases_path": str(args.fixed_holdout_cases),
        "fixed_holdout_baseline_path": str(args.fixed_holdout_baseline),
        "fixed_holdout_case_count": len(fixed_holdout_probes),
        "fixed_holdout_total_case_count": len(fixed_holdout_all_probes),
        "fixed_holdout_sampled": len(fixed_holdout_probes) != len(fixed_holdout_all_probes),
        "fixed_holdout_sample_offset": int(args.fixed_holdout_sample_offset or 0),
        "holdout_cases_path": str(holdout_cases_path),
        "holdout_case_count": len(holdout_probes),
        "diagnosis_path": str(diagnosis_path),
        "validation_gate_path": str(validation_gate),
        "manual_gate_path": str(manual_gate),
        "fixed_holdout_gate_path": str(Path(str((attempts[-1] or {}).get("fixed_holdout_gate_path") or ""))) if attempts else "",
        "holdout_gate_path": str(Path(str((attempts[-1] or {}).get("holdout_gate_path") or ""))) if attempts else "",
        "diagnosis": diagnosis,
        "router_support": load_json(artifact_manifest_path(candidate_artifacts["router_support"])),
        "article_support": load_json(artifact_manifest_path(candidate_artifacts["article_support"])),
        "router_table": load_json(artifact_manifest_path(candidate_artifacts["router_table"])),
        "validation_summary": validation_summary,
        "manual_summary": manual_summary,
        "fixed_holdout_summary": fixed_holdout_summary,
        "fixed_holdout_guard": fixed_guard,
        "moving_holdout_summary": holdout_summary,
        "holdout_summary": holdout_summary,
        "attempts": attempts,
        "retry_preparations": retry_preparations,
        "deferred_backlog_mining": deferred_backlog_mining,
        "initial_strategy": initial_strategy,
        "auto_failure_diagnostics": auto_failure_diagnostics,
        "auto_deep_diagnosis_enabled": not args.disable_auto_deep_diagnosis,
        "retry_focus_support_only": bool(args.disable_auto_deep_diagnosis),
        "holdout_training_blocked": True,
        "fixed_holdout_training_blocked": True,
        "moving_holdout_role": "exploratory_backlog_only",
        "accepted_after_attempt": accepted_after_attempt,
        "deferred_failure_count": deferred_failure_count,
        "deferred_backlog_path": str(args.deferred_backlog),
        "installed_artifacts": {
            "router_support": str(ROUTER_SUPPORT_PATH),
            "router_table": str(ROUTER_TABLE_PATH),
            "article_support": str(ARTICLE_SUPPORT_PATH),
        },
        "backup_dir": str(backup_dir),
        "staging_dir": str(staging_dir),
    }
    if decision == "REJECTED_ROLLED_BACK":
        manifest["deferred_rejected_cycle_count"] = append_rejected_improvement_cycle(
            manifest=manifest,
            manifest_path=manifest_path,
            backlog_path=args.deferred_backlog,
            reason="rejected_rolled_back_continue_collecting",
        )
        manifest["deferred_backlog_path"] = str(args.deferred_backlog)
    manifest["artifact_retention"] = cleanup_retained_artifact_dirs(
        output_dir,
        args.artifact_retention_count,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=AUTOPILOT_DIR)
    parser.add_argument("--batch-round-limit", type=int, default=20)
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--manual-cases", type=Path, default=MANUAL_SLICE_PATH)
    parser.add_argument("--fixed-holdout-cases", type=Path, default=FIXED_HOLDOUT_CASES_PATH)
    parser.add_argument("--fixed-holdout-baseline", type=Path, default=FIXED_HOLDOUT_BASELINE_PATH)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--min-validation-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-manual-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-holdout-pass-rate", type=float, default=0.90)
    parser.add_argument("--holdout-limit", type=int, default=200)
    parser.add_argument(
        "--fixed-holdout-limit",
        type=int,
        default=0,
        help="0 means full fixed holdout; positive values run a stratified fixed-holdout sample.",
    )
    parser.add_argument("--fixed-holdout-sample-offset", type=int, default=0)
    parser.add_argument("--allow-sampled-fixed-holdout", action="store_true")
    parser.add_argument("--validation-mode", default="full")
    parser.add_argument("--max-fixed-holdout-score-drop", type=float, default=1.0)
    parser.add_argument("--max-fixed-holdout-pass-rate-drop", type=float, default=0.02)
    parser.add_argument("--max-fixed-holdout-axis-drop", type=float, default=0.02)
    parser.add_argument("--max-fixed-holdout-governing-drop", type=float, default=0.01)
    parser.add_argument("--max-fixed-holdout-context-drop", type=float, default=0.01)
    parser.add_argument("--sampled-fixed-holdout-score-drop", type=float, default=8.0)
    parser.add_argument("--sampled-fixed-holdout-pass-rate-drop", type=float, default=0.12)
    parser.add_argument("--sampled-fixed-holdout-axis-drop", type=float, default=0.12)
    parser.add_argument("--sampled-fixed-holdout-governing-drop", type=float, default=0.05)
    parser.add_argument("--sampled-fixed-holdout-context-drop", type=float, default=0.04)
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=2,
        help="عدد محاولات التحسين الذكية الإضافية بعد فشل تحقق الدفعة بسبب retrieval/package.",
    )
    parser.add_argument("--deep-diagnosis-history-limit", type=int, default=8)
    parser.add_argument("--force-corpus-surface-after-repeats", type=int, default=2)
    parser.add_argument("--allow-deferred-failures", action="store_true")
    parser.add_argument("--deferred-min-validation-pass-rate", type=float, default=0.90)
    parser.add_argument("--deferred-backlog", type=Path, default=DEFAULT_DEFERRED_BACKLOG_PATH)
    parser.add_argument("--disable-deferred-backlog-mining", action="store_true")
    parser.add_argument("--deferred-backlog-recent-limit", type=int, default=50000)
    parser.add_argument("--deferred-backlog-min-unique-questions", type=int, default=2)
    parser.add_argument("--deferred-backlog-min-records", type=int, default=8)
    parser.add_argument("--deferred-backlog-max-pairs", type=int, default=300)
    parser.add_argument("--deferred-backlog-max-surface-rows", type=int, default=1200)
    parser.add_argument("--deferred-backlog-max-surface-rows-per-pair", type=int, default=4)
    parser.add_argument("--deferred-backlog-min-surface-rows", type=int, default=80)
    parser.add_argument("--artifact-retention-count", type=int, default=10)
    parser.add_argument("--disable-auto-deep-diagnosis", action="store_true")
    return parser.parse_args()


def main() -> None:
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
