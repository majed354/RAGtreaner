"""Validate the structural contract of a legal-mode answer.

This is the nV0 lightweight validator layer that sits above prompting and below
benchmark scoring. It is intentionally simple: it checks section presence,
thought leakage, repeated filler, and basic honesty signals for insufficient
answers.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_SECTIONS = {
    "legal_opinion": [
        "النظام المنطبق",
        "الحكم المباشر",
        "المواد المستند إليها",
        "القيود أو الاستثناءات",
        "ما لم يثبته النص",
        "الخلاصة العملية",
    ],
    "legal_memo": [
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

THOUGHT_PATTERNS = [
    "Thinking Process",
    "<|channel>thought",
    "<|start_header_id|>thought",
]

QUALIFICATION_MARKERS = [
    "لا يكفي",
    "لا تكفي",
    "بحاجة إلى نصوص إضافية",
    "يلزم استكمال",
    "الاسترجاع الحالي",
    "ما لم يثبته النص",
]


def load_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input_file:
        return args.input_file.read_text(encoding="utf-8")
    raise ValueError("Either --text or --input-file is required.")


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


def filler_flag(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text)
    return "• • • •" in normalized or "..." * 3 in normalized


def validate(mode: str, text: str) -> dict[str, object]:
    required = REQUIRED_SECTIONS[mode]
    missing = [section for section in required if section not in text]
    thought_leak = any(pattern in text for pattern in THOUGHT_PATTERNS)
    qualification_present = any(marker in text for marker in QUALIFICATION_MARKERS)
    article_mentions = sorted(set(int(match) for match in re.findall(r"رقم المادة:\s*(\d+)", text)))
    return {
        "mode": mode,
        "contract_ok": not missing and not thought_leak,
        "missing_sections": missing,
        "thought_leak": thought_leak,
        "repeated_line_flag": repeated_line_flag(text),
        "filler_flag": filler_flag(text),
        "qualification_marker_present": qualification_present,
        "article_mentions": article_mentions,
        "char_count": len(text.strip()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(REQUIRED_SECTIONS), required=True)
    parser.add_argument("--text", type=str)
    parser.add_argument("--input-file", type=Path)
    args = parser.parse_args()

    payload = validate(args.mode, load_text(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
