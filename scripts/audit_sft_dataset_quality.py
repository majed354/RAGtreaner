"""Audit SFT datasets for issues that commonly hurt small-model fine-tuning."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SUSPICIOUS_PATTERNS = {
    "teacher_waiting_for_context": [
        "لم ترفق",
        "بانتظار النصوص",
        "انتظار البيانات",
        "يرجى تزويدي",
    ],
    "filler_phrase": [
        "من ظاهرها",
        "من المحتمل أن يثبت",
        "قد يثبت ما إذا كان",
    ],
    "thought_leak": [
        "<|channel>thought",
        "Thinking Process",
        "Here's a thinking process",
    ],
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def detect_mode(messages: list[dict[str, Any]]) -> str:
    combined = "\n".join(str(msg.get("content", "")) for msg in messages if msg.get("role") in {"system", "assistant"})
    if "عنوان المذكرة" in combined and "الخلاصة والتوصية العملية" in combined:
        return "legal_memo"
    if "التكييف الأولي للقضية" in combined and "التقدير الأولي" in combined:
        return "legal_analysis"
    if "النظام المنطبق" in combined and "الخلاصة العملية" in combined:
        return "legal_opinion"
    return "unknown"


def count_hits(text: str, patterns: list[str]) -> int:
    return sum(text.count(pattern) for pattern in patterns)


def repeated_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    return [line for line, count in counts.items() if count >= 3]


def article_mentions(text: str) -> list[str]:
    return re.findall(r"المادة\s*\(?\d+\)?|المادة\s+[^\n:]+", text)


def analyze_example(example: dict[str, Any]) -> dict[str, Any]:
    messages = example.get("messages", [])
    assistant = next((msg.get("content", "") for msg in messages if msg.get("role") == "assistant"), "")
    mode = detect_mode(messages)
    repeated = repeated_lines(assistant)
    suspicious = {
        name: count_hits(assistant, patterns)
        for name, patterns in SUSPICIOUS_PATTERNS.items()
    }
    article_refs = article_mentions(assistant)
    unique_article_refs = len(set(article_refs))
    return {
        "mode": mode,
        "assistant_chars": len(assistant),
        "assistant_lines": len([line for line in assistant.splitlines() if line.strip()]),
        "suspicious": suspicious,
        "repeated_lines": repeated,
        "repeated_line_count": len(repeated),
        "article_ref_count": len(article_refs),
        "unique_article_ref_count": unique_article_refs,
        "low_citation_density": unique_article_refs == 0,
        "very_long_example": len(assistant) > 8000,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_mode[row["mode"]].append(row)

    result: dict[str, Any] = {
        "examples_total": len(rows),
        "by_mode": {},
        "flags": {
            "teacher_waiting_for_context": 0,
            "filler_phrase": 0,
            "thought_leak": 0,
            "repeated_lines": 0,
            "low_citation_density": 0,
            "very_long_example": 0,
        },
    }

    for row in rows:
        for name in ("teacher_waiting_for_context", "filler_phrase", "thought_leak"):
            if row["suspicious"][name] > 0:
                result["flags"][name] += 1
        if row["repeated_line_count"] > 0:
            result["flags"]["repeated_lines"] += 1
        if row["low_citation_density"]:
            result["flags"]["low_citation_density"] += 1
        if row["very_long_example"]:
            result["flags"]["very_long_example"] += 1

    for mode, items in sorted(by_mode.items()):
        result["by_mode"][mode] = {
            "examples": len(items),
            "avg_chars": round(sum(item["assistant_chars"] for item in items) / max(1, len(items)), 1),
            "avg_unique_article_refs": round(sum(item["unique_article_ref_count"] for item in items) / max(1, len(items)), 2),
            "teacher_waiting_for_context": sum(1 for item in items if item["suspicious"]["teacher_waiting_for_context"] > 0),
            "filler_phrase": sum(1 for item in items if item["suspicious"]["filler_phrase"] > 0),
            "thought_leak": sum(1 for item in items if item["suspicious"]["thought_leak"] > 0),
            "repeated_lines": sum(1 for item in items if item["repeated_line_count"] > 0),
            "low_citation_density": sum(1 for item in items if item["low_citation_density"]),
            "very_long_example": sum(1 for item in items if item["very_long_example"]),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    examples = load_jsonl(args.input)
    analyzed = [analyze_example(example) for example in examples]
    payload = {
        "input": str(args.input),
        "summary": summarize(analyzed),
        "examples": analyzed,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
