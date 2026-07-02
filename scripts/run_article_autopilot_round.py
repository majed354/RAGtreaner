"""Run one automated article-precision expansion round.

This orchestrates local teacher generation, deterministic retrieval gating,
gap classification, and promotion of high-confidence passing candidates into a
separate candidate bank. It does not edit retrieval code.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_BANK = DEFAULT_OUTPUT_DIR / "autopilot_article_precision_bank.jsonl"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"
DEFAULT_TEACHER_MODELS = ["qwen3.6:35b"]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
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


def is_holdout_probe(probe: dict[str, Any]) -> bool:
    return (
        str(probe.get("synthetic_bank") or "").lower() == "holdout"
        or bool(probe.get("holdout_locked"))
        or str(probe.get("split") or "").lower() == "autopilot_holdout"
    )


def promote_candidates(probes_path: Path, gate_path: Path, bank_path: Path) -> dict[str, Any]:
    probes = {row["question_id"]: row for row in load_jsonl(probes_path)}
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else {}
    rows = gate.get("rows") or []
    existing = load_jsonl(bank_path)
    existing_by_id = {row.get("question_id"): row for row in existing}
    promoted = []
    held_for_review = []
    failed = []
    holdout_skipped = []

    for row in rows:
        qid = row.get("question_id")
        probe = probes.get(qid)
        if not probe:
            continue
        if is_holdout_probe(probe):
            holdout_skipped.append(qid)
            continue
        auto_review = probe.get("auto_review") or {}
        promotion_ready = auto_review.get("status") in {"model_agreement_ready", "trusted_single_teacher_ready"}
        passed = bool(row.get("passed")) and not row.get("transport_error")
        if passed and promotion_ready:
            promoted_probe = dict(probe)
            promoted_probe["auto_review"] = {
                **auto_review,
                "status": "auto_promoted_after_gate",
                "promoted_at": datetime.now(timezone.utc).isoformat(),
                "gate_article_points": row.get("article_points"),
            }
            existing_by_id[qid] = promoted_probe
            promoted.append(qid)
        elif passed:
            held_for_review.append(qid)
        else:
            failed.append(qid)

    write_jsonl(bank_path, [existing_by_id[key] for key in sorted(existing_by_id)])
    return {
        "promoted_count": len(promoted),
        "held_for_review_count": len(held_for_review),
        "failed_count": len(failed),
        "holdout_skipped_count": len(holdout_skipped),
        "promoted_question_ids": promoted,
        "held_for_review_question_ids": held_for_review,
        "failed_question_ids": failed,
        "holdout_skipped_question_ids": holdout_skipped,
        "bank_path": str(bank_path),
        "bank_case_count": len(existing_by_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--candidate-count", type=int, default=2)
    parser.add_argument("--max-articles-per-case", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260531)
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--bank-output", type=Path, default=DEFAULT_BANK)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--teacher-timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / f"article_autopilot_candidates_{timestamp}.jsonl"
    probes_path = output_dir / f"article_autopilot_probes_{timestamp}.jsonl"
    gate_path = output_dir / f"article_autopilot_gate_{timestamp}.json"
    summary_path = output_dir / f"article_autopilot_gap_summary_{timestamp}.json"
    manifest_path = output_dir / f"article_autopilot_manifest_{timestamp}.json"

    generate_command = [
        sys.executable,
        "scripts/generate_article_precision_candidates.py",
        "--output",
        str(candidates_path),
        "--probes-output",
        str(probes_path),
        "--candidate-count",
        str(args.candidate_count),
        "--max-articles-per-case",
        str(args.max_articles_per_case),
        "--seed",
        str(args.seed),
        "--timeout",
        str(args.teacher_timeout_seconds),
    ]
    models = args.model or DEFAULT_TEACHER_MODELS
    for model in models:
        generate_command.extend(["--model", model])
    run_command(
        generate_command,
        timeout=max(args.teacher_timeout_seconds * max(1, args.candidate_count) * max(1, len(models)), 900),
    )

    run_command(
        [
            sys.executable,
            "scripts/run_article_precision_gate.py",
            "--cases",
            str(probes_path),
            "--output",
            str(gate_path),
            "--benchmark-id",
            f"article_autopilot_{timestamp}",
            "--retrieval-profile",
            "jamia_recall",
            "--service-url",
            args.service_url,
            "--timeout-seconds",
            str(args.timeout_seconds),
        ],
        timeout=max(args.timeout_seconds * max(1, args.candidate_count + 1), 600),
    )

    run_command(
        [
            sys.executable,
            "scripts/summarize_article_precision_gaps.py",
            "--report",
            str(gate_path),
            "--matrix",
            str(ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"),
            "--output",
            str(summary_path),
        ],
        timeout=120,
    )

    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gap_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    promotion = promote_candidates(probes_path, gate_path, args.bank_output)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(load_jsonl(probes_path)),
        "models": models,
        "paths": {
            "candidates": str(candidates_path),
            "probes": str(probes_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
            "bank": str(args.bank_output),
        },
        "gate_summary": gate.get("summary", {}),
        "gap_decision": gap_summary.get("decision"),
        "classification_counts": gap_summary.get("classification_counts", {}),
        "reason_counts": gap_summary.get("reason_counts", {}),
        "promotion": promotion,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
