"""Export a CSV summary of all local training experiments."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path("/Users/majd/Desktop/codex/شات الاستشارات")
CONFIG_ROOT = Path("/Users/majd/Desktop/codex/qlora-m3-ultra/configs")
RESULTS_ROOT = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "results"

ALL_MODES = ["legal_opinion", "legal_memo", "legal_analysis"]
THOUGHT_MARKERS = [
    "<|channel|>thought",
    "Thinking Process",
    "<|start_header_id|>thought",
    "<channel|>thought",
]


EXPERIMENTS: list[dict[str, Any]] = [
    {
        "version": "raw",
        "status": "completed",
        "label": "الخام",
        "experiment_recipe": "بدون تدريب؛ تقييم Gemma 4 E2B الخام على benchmark مجمد.",
        "primary_target": "baseline",
        "comparison_basis": "baseline",
        "impact_label": "baseline",
        "impact_summary": "خط الأساس قبل أي تدريب؛ أظهر التزامًا محدودًا بالقالب وتسرب تفكير واضحًا.",
        "deployment_decision": "غير معتمد",
        "dataset_dir": None,
        "config_path": None,
        "results_dir": RESULTS_ROOT / "gemma4_e2b_raw",
        "scored_pattern": "{mode}_mlx.scored.json",
        "raw_pattern": "{mode}_mlx.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v1",
        "status": "completed",
        "label": "v1",
        "experiment_recipe": "تدريب عام متعدد المسارات على dataset مدمج من seed + structured curriculum.",
        "primary_target": "general",
        "comparison_basis": "raw",
        "impact_label": "تقدم متوسط",
        "impact_summary": "أحدث تقدمًا متوسطًا عامًا، وتقدمًا كبيرًا في اكتمال القالب ومنع تسرب التفكير.",
        "deployment_decision": "استُخدم كأساس مبكر ثم تجاوزه v4/v5",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_modes_v1",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v1.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v1",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v2",
        "status": "completed",
        "label": "v2 refined",
        "experiment_recipe": "تنقية dataset + oversample للمذكرات teacher مع إزالة أمثلة منخفضة الاستشهاد والتكرار.",
        "primary_target": "general_refined",
        "comparison_basis": "v1",
        "impact_label": "تراجع كبير",
        "impact_summary": "أحدث تراجعًا كبيرًا في الأداء العام مقارنة بـ v1، خاصة في الالتزام البنيوي وجودة الإجابة.",
        "deployment_decision": "مرفوض",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_modes_v2_refined",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v2-refined.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v2_refined",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v3",
        "status": "completed",
        "label": "v3 resume v1",
        "experiment_recipe": "استئناف من v1 على dataset v2 refined لاستعادة السلوك الجيد بعد انحراف v2.",
        "primary_target": "general_recovery",
        "comparison_basis": "v2",
        "impact_label": "تقدم متوسط",
        "impact_summary": "أحدث تقدمًا متوسطًا مقارنة بـ v2 واستعاد الثبات ومنع التسرب، لكنه لم يصبح أفضل نموذج عام من v4 لاحقًا.",
        "deployment_decision": "مرحلة انتقالية ناجحة",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_modes_v2_refined",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v3-resume-v1.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v3_resume_v1",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v4",
        "status": "completed",
        "label": "v4 memo boost",
        "experiment_recipe": "Memo-boost من v3 مع replay خفيف من الرأي والتحليل.",
        "primary_target": "legal_memo",
        "comparison_basis": "v3",
        "impact_label": "تقدم متوسط إلى كبير",
        "impact_summary": "أحدث تقدمًا متوسطًا إلى كبير، خاصة في المذكرة والتحليل، وأصبح أفضل fallback عام للمذكرات والتحليل.",
        "deployment_decision": "معتمد للمذكرة والتحليل",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_memo_boost_v1",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v4-memo-boost.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v4_memo_boost",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v5",
        "status": "completed",
        "label": "v5 opinion recovery",
        "experiment_recipe": "Opinion-recovery من v4 مع replay صغير من memo/analysis.",
        "primary_target": "legal_opinion",
        "comparison_basis": "v4",
        "impact_label": "تقدم كبير متخصص / تراجع متوسط خارج الهدف",
        "impact_summary": "أحدث تقدمًا كبيرًا في الرأي القانوني، لكنه سبب تراجعًا متوسطًا في المذكرة والتحليل؛ لذلك صار adapter متخصصًا.",
        "deployment_decision": "معتمد للرأي القانوني فقط",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_opinion_boost_v1",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v5-opinion-recovery.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v5_opinion_recovery",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ALL_MODES,
    },
    {
        "version": "v6",
        "status": "completed",
        "label": "v6 memo fit",
        "experiment_recipe": "Memo-fit specialist: seed memo نظيف + structured memos منتقاة + replay صغير جدًا.",
        "primary_target": "legal_memo",
        "comparison_basis": "v4",
        "impact_label": "تراجع بسيط إلى متوسط",
        "impact_summary": "حسّن citation clarity قليلًا، لكنه أحدث تراجعًا بسيطًا إلى متوسط في اكتمال المذكرة وجودتها مقابل v4.",
        "deployment_decision": "مرفوض",
        "dataset_dir": ROOT / "data" / "training" / "final_legal_memo_fit_v6",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v6-memo-fit.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v6_memo_fit",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ["legal_memo"],
    },
    {
        "version": "v7",
        "status": "completed",
        "label": "v7 failure cluster",
        "experiment_recipe": "Failure-cluster balanced: full refined base + clean seed + memo late-section focus.",
        "primary_target": "balanced_memo_repair",
        "comparison_basis": "v6_and_v4",
        "impact_label": "تقدم بسيط مقابل v6 / تراجع بسيط إلى متوسط مقابل v4",
        "impact_summary": "كان أفضل من v6 وأكثر توازنًا، لكنه لم ينتزع أي مسار من v4/v5 وظل أضعف من v4 في المذكرة والتحليل.",
        "deployment_decision": "غير معتمد تشغيليًا",
        "dataset_dir": ROOT / "data" / "training" / "final_failure_cluster_v7",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v7-failure-cluster.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v7_failure_cluster",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ["legal_memo", "legal_analysis"],
    },
    {
        "version": "v8",
        "status": "completed",
        "label": "v8 section repair",
        "experiment_recipe": "Section-repair with normalized supervision: إزالة filler + تطبيع العناوين + oversample لإصلاح أقسام المذكرة المتأخرة.",
        "primary_target": "legal_memo_section_repair",
        "comparison_basis": "v4",
        "impact_label": "تقدم بسيط جزئي / تراجع بنيوي بسيط",
        "impact_summary": "حسّن answer_only للمذكرة مقابل v4، لكنه خفّض section_coverage وعدد الحالات مكتملة القالب؛ لذلك لا يكفي لاعتماده بدل v4.",
        "deployment_decision": "غير معتمد تشغيليًا",
        "dataset_dir": ROOT / "data" / "training" / "final_section_repair_v8",
        "config_path": CONFIG_ROOT / "gemma4-e2b-legal-modes-v8-section-repair.yaml",
        "results_dir": RESULTS_ROOT / "gemma4_e2b_legal_v8_section_repair",
        "scored_pattern": "{mode}_mlx_adapter.scored.json",
        "raw_pattern": "{mode}_mlx_adapter.json",
        "expected_modes": ["legal_memo"],
    },
]


def parse_simple_yaml(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    payload: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_]+):\s*(.+?)\s*$")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key, value = match.groups()
        payload[key] = value.strip().strip('"')
    return payload


def detect_mode(example: dict[str, Any]) -> str:
    system = "\n".join(
        str(msg.get("content", ""))
        for msg in example.get("messages", [])
        if msg.get("role") == "system"
    )
    if "عنوان المذكرة" in system:
        return "legal_memo"
    if "التكييف الأولي للقضية" in system:
        return "legal_analysis"
    if "النظام المنطبق" in system:
        return "legal_opinion"
    return "unknown"


def dataset_stats(dataset_dir: Path | None) -> dict[str, Any]:
    if dataset_dir is None or not dataset_dir.exists():
        return {}
    stats: dict[str, Any] = {}
    total_modes = Counter()
    for split in ("train", "valid", "test"):
        split_path = dataset_dir / f"{split}.jsonl"
        if not split_path.exists():
            continue
        split_count = 0
        split_modes = Counter()
        with split_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                split_count += 1
                mode = detect_mode(json.loads(line))
                split_modes[mode] += 1
                total_modes[mode] += 1
        stats[f"{split}_examples"] = split_count
        stats[f"{split}_legal_opinion"] = split_modes.get("legal_opinion", 0)
        stats[f"{split}_legal_memo"] = split_modes.get("legal_memo", 0)
        stats[f"{split}_legal_analysis"] = split_modes.get("legal_analysis", 0)
    stats["examples_total"] = sum(
        stats.get(f"{split}_examples", 0) for split in ("train", "valid", "test")
    )
    stats["all_legal_opinion"] = total_modes.get("legal_opinion", 0)
    stats["all_legal_memo"] = total_modes.get("legal_memo", 0)
    stats["all_legal_analysis"] = total_modes.get("legal_analysis", 0)
    return stats


def load_scored_summary(results_dir: Path, pattern: str, mode: str) -> dict[str, Any] | None:
    path = results_dir / pattern.format(mode=mode)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("summary", {})


def load_partial_summary(results_dir: Path, raw_pattern: str, mode: str) -> dict[str, Any] | None:
    path = results_dir / raw_pattern.format(mode=mode)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("summary", {})


def thought_leak_stats(results_dir: Path, raw_pattern: str, modes: list[str]) -> tuple[int | None, int | None]:
    checked = 0
    hits = 0
    found_any = False
    for mode in modes:
        path = results_dir / raw_pattern.format(mode=mode)
        if not path.exists():
            continue
        found_any = True
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for row in payload.get("rows", []):
            checked += 1
            text = str(row.get("answer", ""))
            if any(marker in text for marker in THOUGHT_MARKERS):
                hits += 1
    if not found_any:
        return None, None
    return hits, checked


def row_for_experiment(meta: dict[str, Any]) -> dict[str, Any]:
    config = parse_simple_yaml(meta.get("config_path"))
    stats = dataset_stats(meta.get("dataset_dir"))

    row: dict[str, Any] = {
        "version": meta["version"],
        "status": meta["status"],
        "label": meta["label"],
        "experiment_recipe": meta["experiment_recipe"],
        "primary_target": meta["primary_target"],
        "comparison_basis": meta["comparison_basis"],
        "impact_label": meta["impact_label"],
        "impact_summary": meta["impact_summary"],
        "deployment_decision": meta["deployment_decision"],
        "dataset_dir": str(meta["dataset_dir"]) if meta.get("dataset_dir") else "",
        "config_path": str(meta["config_path"]) if meta.get("config_path") else "",
        "results_dir": str(meta["results_dir"]),
        "resume_adapter_file": config.get("resume_adapter_file", ""),
        "iters": config.get("iters", ""),
        "learning_rate": config.get("learning_rate", ""),
        "max_seq_length": config.get("max_seq_length", ""),
        "train_examples": stats.get("train_examples", ""),
        "valid_examples": stats.get("valid_examples", ""),
        "test_examples": stats.get("test_examples", ""),
        "examples_total": stats.get("examples_total", ""),
        "train_legal_opinion": stats.get("train_legal_opinion", ""),
        "train_legal_memo": stats.get("train_legal_memo", ""),
        "train_legal_analysis": stats.get("train_legal_analysis", ""),
        "all_legal_opinion": stats.get("all_legal_opinion", ""),
        "all_legal_memo": stats.get("all_legal_memo", ""),
        "all_legal_analysis": stats.get("all_legal_analysis", ""),
        "eval_status": "complete",
        "eval_partial_cases_completed": "",
        "eval_partial_cases_total": "",
    }

    macro_keys = [
        "average_score",
        "average_answer_only_score",
        "average_section_coverage",
        "cases_with_full_section_coverage",
        "average_citation_clarity",
    ]
    macro = {key: 0.0 for key in macro_keys}
    macro_count = 0

    for mode in ALL_MODES:
        summary = load_scored_summary(meta["results_dir"], meta["scored_pattern"], mode)
        prefix = mode
        if summary:
            row[f"{prefix}_average_score"] = summary.get("average_score", "")
            row[f"{prefix}_average_answer_only_score"] = summary.get(
                "average_answer_only_score", ""
            )
            row[f"{prefix}_average_section_coverage"] = summary.get(
                "average_section_coverage", ""
            )
            row[f"{prefix}_cases_with_full_section_coverage"] = summary.get(
                "cases_with_full_section_coverage", ""
            )
            row[f"{prefix}_average_citation_clarity"] = summary.get(
                "average_citation_clarity", ""
            )
            for key in macro_keys:
                value = summary.get(key)
                if value is not None:
                    macro[key] += value
            macro_count += 1
        else:
            row[f"{prefix}_average_score"] = ""
            row[f"{prefix}_average_answer_only_score"] = ""
            row[f"{prefix}_average_section_coverage"] = ""
            row[f"{prefix}_cases_with_full_section_coverage"] = ""
            row[f"{prefix}_average_citation_clarity"] = ""

    if macro_count:
        for key in macro_keys:
            row[f"macro_{key}"] = round(macro[key] / macro_count, 6)
    else:
        for key in macro_keys:
            row[f"macro_{key}"] = ""

    partial_summary = None
    if meta["status"] != "completed":
        expected_modes = meta.get("expected_modes", [])
        if expected_modes:
            partial_summary = load_partial_summary(
                meta["results_dir"], meta["raw_pattern"], expected_modes[0]
            )
        if partial_summary:
            row["eval_status"] = "pending_partial"
            row["eval_partial_cases_completed"] = partial_summary.get("cases_completed", "")
            row["eval_partial_cases_total"] = partial_summary.get("cases_total", "")
        else:
            row["eval_status"] = "pending_not_started"

    thought_hits, thought_checked = thought_leak_stats(
        meta["results_dir"], meta["raw_pattern"], meta.get("expected_modes", [])
    )
    row["thought_leak_cases"] = thought_hits if thought_hits is not None else ""
    row["thought_cases_checked"] = thought_checked if thought_checked is not None else ""

    return row


def main() -> None:
    output_path = ROOT / "TRAINING_EXPERIMENTS_SUMMARY.csv"
    rows = [row_for_experiment(meta) for meta in EXPERIMENTS]
    fieldnames = list(rows[0].keys())
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(output_path)


if __name__ == "__main__":
    main()
