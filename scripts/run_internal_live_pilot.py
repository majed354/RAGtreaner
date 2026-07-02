"""Run a small internal pilot through the real app path."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.engine import (  # noqa: E402
    ANSWER_MODE_CONSULTATION,
    ANSWER_MODE_LABELS,
    ANSWER_MODE_LEGAL_MEMO,
    get_engine,
)


DEFAULT_CASES = PROJECT_ROOT / "data" / "eval" / "internal_live_pilot_v1.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "internal_live_pilot_v1.results.json"

REQUIRED_SECTIONS = {
    ANSWER_MODE_CONSULTATION: [
        "النظام المنطبق",
        "الحكم المباشر",
        "المواد المستند إليها",
        "القيود أو الاستثناءات",
        "ما لم يثبته النص",
        "الخلاصة العملية",
    ],
    ANSWER_MODE_LEGAL_MEMO: [
        "عنوان المذكرة",
        "السؤال محل الرأي",
        "الجواب المختصر",
        "الوقائع ذات الأثر القانوني",
        "النظام أو النصوص المنطبقة",
        "المسائل القانونية",
        "التحليل",
        "الدفوع أو الاحتمالات المقابلة",
        "ما لم يثبته النص أو الوقائع",
        "الخلاصة والتوصية العملية",
    ],
    "legal_analysis": [
        "التكييف الأولي للقضية",
        "الأنظمة المحتملة الانطباق",
        "المسائل القانونية الأساسية",
        "ما يدعم الطرف الأول",
        "ما يدعم الطرف الثاني",
        "نقاط الضعف",
        "ما قد يغير النتيجة",
        "ما لم يثبته النص",
        "التقدير الأولي",
    ],
}

THOUGHT_MARKERS = ("Thinking Process", "<|channel|>thought", "<|start_header_id|>thought")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def section_coverage(answer_mode: str, text: str) -> tuple[float, list[str]]:
    required = REQUIRED_SECTIONS[answer_mode]
    hits = [section for section in required if section in text]
    return round(len(hits) / len(required), 3), [section for section in required if section not in text]


def repeated_line_flag(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    streak = 1
    for index in range(1, len(lines)):
        if lines[index] == lines[index - 1]:
            streak += 1
            if streak >= 3:
                return True
        else:
            streak = 1
    return False


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed_rows = [row for row in rows if row.get("status") == "completed"]
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in completed_rows:
        by_mode.setdefault(row["answer_mode"], []).append(row)

    mode_summary: dict[str, Any] = {}
    for mode, items in by_mode.items():
        mode_summary[mode] = {
            "cases": len(items),
            "average_section_coverage": round(sum(item["section_coverage"] for item in items) / len(items), 3),
            "cases_with_full_sections": sum(1 for item in items if item["section_coverage"] >= 1.0),
            "average_char_count": round(sum(item["char_count"] for item in items) / len(items), 1),
            "thought_leak_cases": sum(1 for item in items if item["thought_leak"]),
            "repeated_line_cases": sum(1 for item in items if item["repeated_line_flag"]),
            "confidence_counts": dict(Counter(item["confidence"] for item in items)),
        }

    return {
        "cases_total": len(rows),
        "cases_completed": len(completed_rows),
        "cases_skipped": sum(1 for row in rows if row.get("status") != "completed"),
        "by_mode": mode_summary,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--provider", default="mlx_local")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    cases = load_jsonl(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    engine = get_engine()
    rows: list[dict[str, Any]] = []
    for case in cases:
        answer_mode = str(case["answer_mode"]).strip()
        if answer_mode not in {ANSWER_MODE_CONSULTATION, ANSWER_MODE_LEGAL_MEMO}:
            rows.append(
                {
                    "case_id": case["case_id"],
                    "answer_mode": answer_mode,
                    "answer_mode_label": answer_mode,
                    "question": case["question"],
                    "status": "skipped_unsupported_mode",
                    "notes": "هذا المسار غير مدعوم حاليًا داخل التطبيق الفعلي.",
                }
            )
            continue
        result = await engine.query_with_provider(
            case["question"],
            provider=args.provider,
            answer_mode=answer_mode,
        )
        answer = result.answer or ""
        coverage, missing_sections = section_coverage(answer_mode, answer)
        rows.append(
            {
                "case_id": case["case_id"],
                "answer_mode": answer_mode,
                "answer_mode_label": ANSWER_MODE_LABELS.get(answer_mode, answer_mode),
                "question": case["question"],
                "status": "completed",
                "confidence": result.confidence,
                "needs_escalation": result.needs_escalation,
                "char_count": len(answer),
                "section_coverage": coverage,
                "missing_sections": missing_sections,
                "thought_leak": any(marker in answer for marker in THOUGHT_MARKERS),
                "repeated_line_flag": repeated_line_flag(answer),
                "source_count": len(result.sources),
                "answer_preview": answer[:1600],
            }
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": args.provider,
        "summary": summarize(rows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
