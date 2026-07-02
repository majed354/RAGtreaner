"""Run a local MLX model against frozen legal-mode contexts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mode_output_guard import (
    build_completion_repair_user_prompt,
    build_repair_user_prompt,
    choose_best_candidate,
    sanitize_output,
    should_attempt_completion_repair,
    should_attempt_repair,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTEXTS = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "current_reference"
    / "legal_opinion_active_runtime.contexts.jsonl"
)
DEFAULT_PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "prompt_templates"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "gemma4_e2b_raw"
    / "legal_opinion_mlx.json"
)
DEFAULT_BUDGET_POLICY = ROOT / "data" / "benchmarks" / "legal_modes_v1" / "generation_budget_policy.json"

PROMPT_TEMPLATE_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}


def load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def filter_context_rows(rows: list[dict[str, Any]], benchmark_ids: set[str] | None) -> list[dict[str, Any]]:
    if not benchmark_ids:
        return rows
    return [row for row in rows if row.get("benchmark_id") in benchmark_ids]


def load_budget_policy(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_max_tokens(
    *,
    cli_max_tokens: int,
    budget_policy: dict[str, Any],
    mode: str,
) -> int:
    policies = budget_policy.get("policies", {})
    mode_policy = policies.get(mode, {})
    try:
        return int(mode_policy.get("max_tokens", cli_max_tokens))
    except (TypeError, ValueError):
        return cli_max_tokens


def load_system_prompt(prompt_dir: Path, mode: str) -> str:
    prompt_name = PROMPT_TEMPLATE_BY_MODE.get(mode)
    if not prompt_name:
        raise ValueError(f"Unsupported mode: {mode}")
    prompt_path = prompt_dir / prompt_name
    return prompt_path.read_text(encoding="utf-8").strip()


def build_user_prompt(question: str, context: str) -> str:
    return f"القضية:\n{question}\n\nالنصوص المسترجعة:\n{context}"


def estimate_confidence(answer: str, error: bool = False) -> str:
    if error or not answer.strip():
        return "low"
    if len(answer.strip()) < 180:
        return "medium"
    return "medium"


def generate_answer(
    *,
    model: Any,
    tokenizer: Any,
    sampler: Any,
    messages: list[dict[str, str]],
    max_tokens: int,
) -> str:
    from mlx_lm import generate

    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    return generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=max_tokens,
        sampler=sampler,
        verbose=False,
    ).strip()


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    guard_rows = [row for row in rows if row.get("output_guard", {}).get("enabled")]
    return {
        "cases_total": len(rows),
        "cases_completed": sum(1 for row in rows if row.get("status") == "completed"),
        "cases_failed": sum(1 for row in rows if row.get("status") == "failed"),
        "mode_counts": dict(Counter(row.get("mode", "") for row in rows)),
        "status_counts": dict(Counter(row.get("status", "") for row in rows)),
        "confidence_counts": dict(Counter(row.get("confidence", "") for row in rows)),
        "average_displayed_source_count": round(
            sum(int(row.get("displayed_source_count", 0) or 0) for row in rows) / max(1, len(rows)),
            2,
        ),
        "average_retrieved_source_count": round(
            sum(int(row.get("retrieved_source_count", 0) or 0) for row in rows) / max(1, len(rows)),
            2,
        ),
        "guarded_cases": len(guard_rows),
        "repair_attempted_cases": sum(
            1 for row in guard_rows if row.get("output_guard", {}).get("repair_attempted")
        ),
        "repair_selected_cases": sum(
            1 for row in guard_rows if row.get("output_guard", {}).get("selected_candidate") == "repair_guarded"
        ),
        "completion_attempted_cases": sum(
            1 for row in guard_rows if row.get("output_guard", {}).get("completion_attempted")
        ),
        "completion_selected_cases": sum(
            1 for row in guard_rows if row.get("output_guard", {}).get("selected_candidate") == "completion_guarded"
        ),
    }


def write_report(
    output_path: Path,
    rows: list[dict[str, Any]],
    model_path: str,
    adapter_path: str | None,
    prompt_dir: Path,
    max_tokens: int,
    temperature: float,
    budget_policy_path: Path | None,
    budget_policy: dict[str, Any],
    generation_options: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "mlx_local",
        "generation_status": {
            "provider": "mlx_local",
            "provider_label": "MLX Local",
            "model": model_path,
            "adapter_path": adapter_path,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "prompt_dir": str(prompt_dir),
            "budget_policy_path": str(budget_policy_path) if budget_policy_path else None,
            "budget_policy_applied": bool(budget_policy),
            "output_guard_enabled": generation_options.get("output_guard_enabled", False),
            "repair_on_fail": generation_options.get("repair_on_fail", False),
        },
        "summary": build_summary(rows),
        "rows": rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--contexts", type=Path, default=DEFAULT_CONTEXTS)
    parser.add_argument("--prompt-dir", type=Path, default=DEFAULT_PROMPT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--adapter-path", type=str, default=None)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--budget-policy", type=Path, default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply-output-guard", action="store_true")
    parser.add_argument("--repair-on-fail", action="store_true")
    parser.add_argument("--repair-temperature", type=float, default=0.1)
    parser.add_argument("--repair-max-tokens", type=int, default=0)
    parser.add_argument("--only-benchmark-id", action="append", default=None)
    args = parser.parse_args()

    from mlx_lm import load
    from mlx_lm.sample_utils import make_sampler

    rows: list[dict[str, Any]] = []
    contexts = filter_context_rows(
        load_jsonl(args.contexts, args.limit),
        set(args.only_benchmark_id or []),
    )
    budget_policy_path = args.budget_policy
    if budget_policy_path is not None and not budget_policy_path.is_absolute():
        budget_policy_path = (ROOT / budget_policy_path).resolve()
    budget_policy = load_budget_policy(budget_policy_path or DEFAULT_BUDGET_POLICY)
    model, tokenizer = load(args.model, adapter_path=args.adapter_path)
    sampler = make_sampler(temp=args.temperature)
    repair_sampler = make_sampler(temp=args.repair_temperature)
    guard_enabled = args.apply_output_guard or args.repair_on_fail

    for item in contexts:
        mode = item.get("mode", "legal_opinion")
        question = item.get("question", "")
        context = item.get("context", "")
        source_catalog = item.get("source_catalog", []) or []
        quality_report = item.get("quality_report", {}) or {}
        system_prompt = load_system_prompt(args.prompt_dir, mode)
        user_prompt = build_user_prompt(question, context)
        case_max_tokens = resolve_max_tokens(
            cli_max_tokens=args.max_tokens,
            budget_policy=budget_policy,
            mode=mode,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            raw_answer = generate_answer(
                model=model,
                tokenizer=tokenizer,
                sampler=sampler,
                messages=messages,
                max_tokens=case_max_tokens,
            )
            answer = raw_answer
            status = "completed"
            error_message = None
        except Exception as exc:  # pragma: no cover - runtime-dependent path
            raw_answer = ""
            answer = f"تعذر تشغيل النموذج المحلي لهذه الحالة: {exc}"
            status = "failed"
            error_message = str(exc)

        output_guard: dict[str, Any] | None = None
        if status == "completed" and guard_enabled:
            guarded_answer, initial_report = sanitize_output(mode, answer)
            answer = guarded_answer
            selected_candidate = "initial_guarded"
            repair_attempted = False
            completion_attempted = False
            repair_report: dict[str, Any] | None = None
            completion_report: dict[str, Any] | None = None
            repair_error: str | None = None
            completion_error: str | None = None
            selected_report = initial_report

            if args.repair_on_fail and should_attempt_repair(initial_report):
                repair_attempted = True
                repair_messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": build_repair_user_prompt(
                            mode=mode,
                            question=question,
                            context=context,
                            draft=guarded_answer,
                            report=initial_report,
                        ),
                    },
                ]
                try:
                    repair_raw_answer = generate_answer(
                        model=model,
                        tokenizer=tokenizer,
                        sampler=repair_sampler,
                        messages=repair_messages,
                        max_tokens=args.repair_max_tokens or case_max_tokens,
                    )
                    repaired_answer, repair_report = sanitize_output(mode, repair_raw_answer)
                    selected = choose_best_candidate(
                        mode,
                        [
                            {"name": "initial_guarded", "text": guarded_answer, "report": initial_report},
                            {"name": "repair_guarded", "text": repaired_answer, "report": repair_report},
                        ],
                    )
                    answer = selected["text"]
                    selected_candidate = selected["name"]
                    selected_report = selected["report"]
                except Exception as exc:  # pragma: no cover - runtime-dependent path
                    repair_error = str(exc)

            if args.repair_on_fail and should_attempt_completion_repair(selected_report):
                completion_attempted = True
                completion_messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": build_completion_repair_user_prompt(
                            mode=mode,
                            question=question,
                            context=context,
                            draft=answer,
                            report=selected_report,
                        ),
                    },
                ]
                try:
                    completion_raw_answer = generate_answer(
                        model=model,
                        tokenizer=tokenizer,
                        sampler=repair_sampler,
                        messages=completion_messages,
                        max_tokens=args.repair_max_tokens or case_max_tokens,
                    )
                    completion_answer, completion_report = sanitize_output(mode, completion_raw_answer)
                    selected = choose_best_candidate(
                        mode,
                        [
                            {
                                "name": selected_candidate,
                                "text": answer,
                                "report": selected_report,
                            },
                            {
                                "name": "completion_guarded",
                                "text": completion_answer,
                                "report": completion_report,
                            },
                        ],
                    )
                    answer = selected["text"]
                    selected_candidate = selected["name"]
                    selected_report = selected["report"]
                except Exception as exc:  # pragma: no cover - runtime-dependent path
                    completion_error = str(exc)

            output_guard = {
                "enabled": True,
                "repair_attempted": repair_attempted,
                "completion_attempted": completion_attempted,
                "selected_candidate": selected_candidate,
                "initial_report": initial_report,
                "repair_report": repair_report,
                "completion_report": completion_report,
                "initial_answer_char_count": len(raw_answer.strip()),
                "final_answer_char_count": len(answer.strip()),
            }
            if repair_error:
                output_guard["repair_error"] = repair_error
            if completion_error:
                output_guard["completion_error"] = completion_error

        row = {
            "benchmark_id": item.get("benchmark_id"),
            "mode": mode,
            "status": status,
            "question": question,
            "answer": answer,
            "confidence": estimate_confidence(answer, error=status != "completed"),
            "needs_escalation": False,
            "retrieved_source_count": len(source_catalog),
            "displayed_source_count": min(4, len(source_catalog)),
            "displayed_sources": [
                "\n".join(part for part in [source.get("citation", ""), source.get("text", "")] if part).strip()
                for source in source_catalog[:4]
            ],
            "similarity_scores": item.get("similarity_scores", []),
            "diagnostics": {
                "status": quality_report.get("status"),
                "issue_flags": quality_report.get("issue_flags", []),
                "dominant_domain": quality_report.get("dominant_domain"),
                "top_regulations": quality_report.get("top_regulations", []),
                "top_articles": quality_report.get("top_articles", []),
                "query_roles": quality_report.get("query_roles", []),
                "covered_roles": quality_report.get("covered_roles", []),
                "missing_roles": quality_report.get("missing_roles", []),
                "issue_count": quality_report.get("issue_count"),
                "covered_issue_ids": quality_report.get("covered_issue_ids", []),
                "missing_issue_ids": quality_report.get("missing_issue_ids", []),
                "missing_issue_domains": quality_report.get("missing_issue_domains", []),
                "helper_failures": quality_report.get("helper_failures", []),
                "primary_ratio": quality_report.get("primary_ratio"),
                "dominant_concentration": quality_report.get("dominant_concentration"),
                "unique_article_count": quality_report.get("unique_article_count"),
            },
            "provider": "mlx_local",
            "max_tokens_used": case_max_tokens,
        }
        if error_message:
            row["error"] = error_message
        if output_guard:
            row["output_guard"] = output_guard

        rows.append(row)
        write_report(
            args.output,
            rows,
            args.model,
            args.adapter_path,
            args.prompt_dir,
            args.max_tokens,
            args.temperature,
            budget_policy_path or DEFAULT_BUDGET_POLICY,
            budget_policy,
            {
                "output_guard_enabled": guard_enabled,
                "repair_on_fail": args.repair_on_fail,
            },
        )

    print(json.dumps(build_summary(rows), ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")


if __name__ == "__main__":
    main()
