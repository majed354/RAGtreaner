"""Build package-router support rows from saved article-autopilot rounds.

The autopilot probes are teacher-generated candidate cases.  This script does
not promote them into the article gate; it only reuses their expected regulation
labels as a recall support surface for the package router, excluding operational
transport failures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTOPILOT_DIR = ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "article_autopilot_router_support_train.jsonl"
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def support_training_allowed(probe: dict[str, Any]) -> bool:
    if str(probe.get("synthetic_bank") or "").lower() == "holdout":
        return False
    if bool(probe.get("holdout_locked")):
        return False
    if str(probe.get("split") or "").lower() == "autopilot_holdout":
        return False
    return bool(probe.get("support_training_allowed", True))


def expected_labels(probe: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    core = [str(slug) for slug in probe.get("expected_core_regulations") or [] if slug]
    implementing = [str(slug) for slug in probe.get("expected_implementing_regulations") or [] if slug]
    companions = [str(slug) for slug in probe.get("expected_companion_regulations") or [] if slug]
    article_slugs = [str(slug) for slug in (probe.get("expected_articles_by_slug") or {}).keys() if slug]
    if not core and not implementing:
        core = article_slugs
    all_labels = list(dict.fromkeys([*core, *implementing, *companions, *article_slugs]))
    if not core:
        core = [slug for slug in all_labels if slug not in set(companions)]
    return list(dict.fromkeys(core)), list(dict.fromkeys(implementing)), list(dict.fromkeys(companions))


def build(args: argparse.Namespace) -> dict[str, Any]:
    probes_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(args.autopilot_dir.glob("article_autopilot_probes_*.jsonl")):
        for row in load_jsonl(path):
            qid = str(row.get("question_id") or "")
            if qid:
                probes_by_id[qid] = row

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    counters = {
        "gate_rows": 0,
        "transport_skipped": 0,
        "missing_probe_skipped": 0,
        "holdout_skipped": 0,
        "label_skipped": 0,
    }
    for gate_path in sorted(args.autopilot_dir.glob("article_autopilot_gate_*.json")):
        gate = load_json(gate_path)
        for gate_row in gate.get("rows") or []:
            counters["gate_rows"] += 1
            if gate_row.get("transport_error"):
                counters["transport_skipped"] += 1
                continue
            qid = str(gate_row.get("question_id") or "")
            probe = probes_by_id.get(qid)
            if not probe:
                counters["missing_probe_skipped"] += 1
                continue
            if not support_training_allowed(probe):
                counters["holdout_skipped"] += 1
                continue
            core, implementing, companions = expected_labels(probe)
            labels = list(dict.fromkeys([*core, *implementing, *companions]))
            if not labels:
                counters["label_skipped"] += 1
                continue
            question = " ".join(str(probe.get("question") or gate_row.get("question") or "").split())
            key = (question, tuple(labels))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "question_id": f"article_autopilot_router_support_{len(rows) + 1:05d}",
                    "source_question_id": qid,
                    "question": question,
                    "split": "train",
                    "router_role": "article_autopilot_router_support",
                    "domain": probe.get("domain") or gate_row.get("domain") or "article_autopilot",
                    "benchmark_category": "article_autopilot_router_support",
                    "scenario_family_id": probe.get("scenario_family_id") or f"article_autopilot::{probe.get('domain') or 'uncategorized'}",
                    "source_note": "article_autopilot_non_operational_gate_row",
                    "source_gate": str(gate_path.relative_to(ROOT)),
                    "gate_passed": bool(gate_row.get("passed")),
                    "gate_article_points": gate_row.get("article_points"),
                    "core_labels": core,
                    "companion_labels": list(dict.fromkeys([*implementing, *companions])),
                    "all_labels": labels,
                    "expected_articles_by_slug": probe.get("expected_articles_by_slug") or {},
                }
            )

    write_jsonl(args.output, rows)
    manifest = {
        "status": "ok",
        "output": str(args.output),
        "rows": len(rows),
        "unique_labels": len({label for row in rows for label in row["all_labels"]}),
        **counters,
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autopilot-dir", type=Path, default=DEFAULT_AUTOPILOT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
