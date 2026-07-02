"""Runtime guard helpers for legal-mode generation outputs.

This module is intentionally lightweight. It does not try to "fix" the legal
reasoning itself; it focuses on runtime output quality:

- remove thought-leak preambles
- trim obvious loop tails and filler
- measure structural completeness
- decide whether a repair pass is worth trying
"""

from __future__ import annotations

import re
from typing import Any


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
    "Analyze the Request",
    "<|channel|>thought",
    "<|start_header_id|>thought",
]

MODE_CHAR_SOFT_LIMITS = {
    "legal_opinion": 1800,
    "legal_memo": 3200,
    "legal_analysis": 3200,
}

MIN_SECTION_BODY_CHARS = {
    "legal_opinion": 35,
    "legal_memo": 45,
    "legal_analysis": 40,
}

LINE_PREFIX_RE = re.compile(r"^[\s\-\*\u2022\d\.\)\(]+")
PUNCT_LINE_RE = re.compile(r"^[\s\-–—_\.\u2022:;،,]{24,}$")
ARTICLE_RE = re.compile(r"(?:المادة|مادة|رقم المادة)\s*[:：]?\s*[()]*\s*(\d+)")
REPEATED_WORD_RE = re.compile(r"(?P<word>[\u0600-\u06FFA-Za-z]{3,})(?:\s+(?P=word)){5,}")
REPEATED_PHRASE_RE = re.compile(
    r"(?P<phrase>[\u0600-\u06FFA-Za-z]{3,}(?:\s+[\u0600-\u06FFA-Za-z]{3,}){1,3})(?:\s+(?P=phrase)){3,}"
)


def section_hits(mode: str, text: str) -> list[str]:
    return [section for section in REQUIRED_SECTIONS[mode] if section in text]


def section_coverage(mode: str, text: str) -> float:
    required = REQUIRED_SECTIONS[mode]
    if not required:
        return 1.0
    return round(len(section_hits(mode, text)) / len(required), 3)


def section_body_lengths(mode: str, text: str) -> dict[str, int]:
    indexed_sections: list[tuple[int, str]] = []
    for section in REQUIRED_SECTIONS[mode]:
        index = text.find(section)
        if index >= 0:
            indexed_sections.append((index, section))
    indexed_sections.sort()

    lengths: dict[str, int] = {}
    for position, (index, section) in enumerate(indexed_sections):
        body_start = index + len(section)
        body_end = indexed_sections[position + 1][0] if position + 1 < len(indexed_sections) else len(text)
        body = text[body_start:body_end]
        body = re.sub(r"^[\s:：\-\u2022\.\)\(]+", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        lengths[section] = len(body)
    return lengths


def first_section_index(mode: str, text: str) -> int | None:
    positions = [text.find(section) for section in REQUIRED_SECTIONS[mode] if section in text]
    if not positions:
        return None
    return min(positions)


def normalize_loop_line(line: str) -> str:
    stripped = LINE_PREFIX_RE.sub("", line.strip())
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped


def detect_repeated_line_tail(text: str, threshold: int = 3) -> dict[str, Any] | None:
    raw_lines = text.splitlines()
    previous = ""
    streak = 1
    for index, line in enumerate(raw_lines):
        normalized = normalize_loop_line(line)
        if not normalized:
            previous = ""
            streak = 1
            continue
        if normalized == previous:
            streak += 1
            if streak >= threshold:
                cut_at = index - streak + 1
                return {
                    "reason": "repeated_line_tail",
                    "line": normalized,
                    "cut_line_index": cut_at,
                    "streak": streak,
                }
        else:
            streak = 1
        previous = normalized
    return None


def detect_punctuation_tail(text: str) -> dict[str, Any] | None:
    raw_lines = text.splitlines()
    if not raw_lines:
        return None
    half_point = max(1, len(raw_lines) // 2)
    for index, line in enumerate(raw_lines):
        if index < half_point:
            continue
        if PUNCT_LINE_RE.fullmatch(line.strip()):
            return {
                "reason": "punctuation_tail",
                "line": line.strip()[:80],
                "cut_line_index": index,
            }
    return None


def detect_repeated_phrase_tail(text: str) -> dict[str, Any] | None:
    if len(text) < 600:
        return None
    tail_start = int(len(text) * 0.6)
    tail = text[tail_start:]
    for pattern, label in ((REPEATED_PHRASE_RE, "repeated_phrase_tail"), (REPEATED_WORD_RE, "repeated_word_tail")):
        match = pattern.search(tail)
        if match:
            return {
                "reason": label,
                "phrase": match.group(0)[:120],
                "cut_char_index": tail_start + match.start(),
            }
    return None


def trim_by_line_index(text: str, cut_line_index: int) -> str:
    raw_lines = text.splitlines()
    if cut_line_index <= 0:
        return text.strip()
    return "\n".join(raw_lines[:cut_line_index]).rstrip()


def strip_thought_prelude(mode: str, text: str) -> tuple[str, dict[str, Any] | None]:
    first_index = first_section_index(mode, text)
    if first_index is None or first_index <= 0:
        return text.strip(), None
    prefix = text[:first_index]
    if any(pattern.lower() in prefix.lower() for pattern in THOUGHT_PATTERNS):
        return text[first_index:].lstrip(), {
            "reason": "thought_prelude_removed",
            "removed_chars": first_index,
        }
    return text.strip(), None


def validate_output(mode: str, text: str) -> dict[str, Any]:
    missing_sections = [section for section in REQUIRED_SECTIONS[mode] if section not in text]
    repeated_line_issue = detect_repeated_line_tail(text)
    punctuation_issue = detect_punctuation_tail(text)
    repeated_phrase_issue = detect_repeated_phrase_tail(text)
    thought_leak = any(pattern.lower() in text.lower() for pattern in THOUGHT_PATTERNS)
    char_count = len(text.strip())
    article_mentions = sorted({int(match) for match in ARTICLE_RE.findall(text)})
    coverage = section_coverage(mode, text)
    body_lengths = section_body_lengths(mode, text)
    thin_sections = [
        section for section, length in body_lengths.items() if length < MIN_SECTION_BODY_CHARS[mode]
    ]
    heading_only_flag = len(thin_sections) >= max(2, len(body_lengths) // 2) if body_lengths else False
    soft_limit = MODE_CHAR_SOFT_LIMITS[mode]
    over_limit_chars = max(char_count - soft_limit, 0)
    quality_score = (
        (coverage * 100)
        - (8 * len(missing_sections))
        - (5 * len(thin_sections))
        - (35 if thought_leak else 0)
        - (20 if repeated_line_issue else 0)
        - (20 if punctuation_issue else 0)
        - (15 if repeated_phrase_issue else 0)
        - (25 if heading_only_flag else 0)
        - min(over_limit_chars / 120, 20)
    )
    return {
        "mode": mode,
        "char_count": char_count,
        "soft_limit": soft_limit,
        "over_limit_chars": over_limit_chars,
        "section_coverage": coverage,
        "missing_sections": missing_sections,
        "thought_leak": thought_leak,
        "repeated_line_flag": repeated_line_issue is not None,
        "repeated_line_issue": repeated_line_issue,
        "filler_flag": punctuation_issue is not None,
        "filler_issue": punctuation_issue,
        "repeated_phrase_tail_flag": repeated_phrase_issue is not None,
        "repeated_phrase_issue": repeated_phrase_issue,
        "article_mentions": article_mentions,
        "section_body_lengths": body_lengths,
        "thin_sections": thin_sections,
        "heading_only_flag": heading_only_flag,
        "quality_score": round(quality_score, 3),
    }


def sanitize_output(mode: str, text: str) -> tuple[str, dict[str, Any]]:
    current = text.replace("\r\n", "\n").strip()
    transformations: list[dict[str, Any]] = []

    current, detail = strip_thought_prelude(mode, current)
    if detail:
        transformations.append(detail)

    punctuation_issue = detect_punctuation_tail(current)
    if punctuation_issue:
        trimmed = trim_by_line_index(current, int(punctuation_issue["cut_line_index"]))
        if len(trimmed.strip()) >= 120:
            current = trimmed
            transformations.append(punctuation_issue)

    repeated_line_issue = detect_repeated_line_tail(current)
    if repeated_line_issue:
        trimmed = trim_by_line_index(current, int(repeated_line_issue["cut_line_index"]))
        if len(trimmed.strip()) >= 120:
            current = trimmed
            transformations.append(repeated_line_issue)

    repeated_phrase_issue = detect_repeated_phrase_tail(current)
    if repeated_phrase_issue:
        cut_index = int(repeated_phrase_issue["cut_char_index"])
        trimmed = current[:cut_index].rstrip()
        if len(trimmed.strip()) >= 120:
            current = trimmed
            transformations.append(repeated_phrase_issue)

    report = validate_output(mode, current)
    report["transformations"] = transformations
    return current, report


def should_attempt_repair(report: dict[str, Any]) -> bool:
    return bool(
        report.get("thought_leak")
        or report.get("repeated_line_flag")
        or report.get("filler_flag")
        or report.get("repeated_phrase_tail_flag")
        or report.get("missing_sections")
        or report.get("thin_sections")
        or report.get("heading_only_flag")
        or report.get("over_limit_chars", 0) > 600
    )


def should_attempt_completion_repair(report: dict[str, Any]) -> bool:
    return bool(
        report.get("missing_sections")
        or report.get("thin_sections")
        or report.get("heading_only_flag")
        or float(report.get("section_coverage", 0.0)) < 1.0
    )


def condensed_draft(draft: str, max_chars: int = 2400) -> str:
    draft = draft.strip()
    if len(draft) <= max_chars:
        return draft
    head = draft[:1400].rstrip()
    tail = draft[-800:].lstrip()
    return f"{head}\n...\n[تم اختصار جزء من المسودة لتقليل الضجيج]\n...\n{tail}"


def build_repair_user_prompt(
    *,
    mode: str,
    question: str,
    context: str,
    draft: str,
    report: dict[str, Any],
) -> str:
    reasons: list[str] = []
    if report.get("thought_leak"):
        reasons.append("ظهرت مقدمة تفكير أو كلام داخلي غير مسموح به.")
    if report.get("missing_sections"):
        reasons.append("سقطت بعض أقسام القالب المطلوبة: " + "، ".join(report["missing_sections"]))
    if report.get("repeated_line_flag"):
        issue = report.get("repeated_line_issue") or {}
        line = issue.get("line", "سطر متكرر")
        reasons.append(f"ظهر تكرار سطري في الذيل مثل: {line}")
    if report.get("filler_flag"):
        reasons.append("ظهر filler أو ذيل من الشرطات أو الرموز.")
    if report.get("repeated_phrase_tail_flag"):
        reasons.append("ظهر تكرار عباري في الذيل.")
    if not reasons:
        reasons.append("المسودة الحالية تحتاج إعادة صياغة أكثر انضباطًا.")

    reason_block = "\n".join(f"- {reason}" for reason in reasons)
    required_sections = "\n".join(f"- {section}" for section in REQUIRED_SECTIONS[mode])

    return (
        f"القضية:\n{question}\n\n"
        f"النصوص المسترجعة:\n{context}\n\n"
        "المسودة السابقة غير صالحة للاعتماد للأسباب التالية:\n"
        f"{reason_block}\n\n"
        "أعد كتابة الجواب كاملًا من الصفر، لا على شكل ملاحظات إصلاح.\n"
        "اكتب الجواب النهائي فقط وفق القالب المطلوب لهذا المسار، وبالترتيب نفسه، مرة واحدة فقط لكل عنوان.\n"
        "شروط إلزامية:\n"
        "1. لا تكتب أي مقدمة مثل Thinking Process أو Analyze the Request.\n"
        "2. لا تكرر أي سطر أو عنوان أو قائمة تكرارًا آليًا.\n"
        "3. لا تكتب عنوانًا فارغًا أو هيكلًا بلا متن؛ كل قسم يجب أن يتضمن جملة واحدة مفيدة على الأقل.\n"
        "4. إذا لم تكف النصوص أو الوقائع فصرح بذلك داخل القسم المناسب بدل التخمين.\n"
        "5. لا تضف نصوصًا قانونية غير موجودة في النصوص المسترجعة.\n"
        "6. إذا وردت المادة أو النظام في النصوص المسترجعة فاذكرها بوضوح وباختصار.\n\n"
        f"العناوين المطلوبة:\n{required_sections}\n\n"
        f"المسودة السابقة:\n{condensed_draft(draft)}"
    )


def build_completion_repair_user_prompt(
    *,
    mode: str,
    question: str,
    context: str,
    draft: str,
    report: dict[str, Any],
) -> str:
    missing_sections = report.get("missing_sections", []) or []
    thin_sections = report.get("thin_sections", []) or []
    focus_points: list[str] = []

    if missing_sections:
        focus_points.append("الأقسام الناقصة التي يجب استكمالها كاملة: " + "، ".join(missing_sections))
    if thin_sections:
        focus_points.append("الأقسام الضعيفة التي تحتاج متنًا فعليًا لا مجرد عنوان: " + "، ".join(thin_sections))
    if not focus_points:
        focus_points.append("أعد كتابة الجواب بصورة أكمل وأشد انضباطًا.")

    required_sections = "\n".join(f"- {section}" for section in REQUIRED_SECTIONS[mode])

    return (
        f"القضية:\n{question}\n\n"
        f"النصوص المسترجعة:\n{context}\n\n"
        "هذه محاولة إكمال ثانية لأن النسخة السابقة ما زالت غير مكتملة.\n"
        f"{chr(10).join(f'- {point}' for point in focus_points)}\n\n"
        "أعد كتابة الجواب كاملًا من الصفر، لا على شكل إلحاق أو تكملة جزئية.\n"
        "اكتب الجواب النهائي فقط، وكل عنوان مرة واحدة فقط، وبترتيب القالب نفسه.\n"
        "قواعد إلزامية:\n"
        "1. كل عنوان يجب أن يتبعه متن فعلي من جملة أو جملتين على الأقل.\n"
        "2. لا تترك أي قسم فارغًا، وإذا لم تكف النصوص فاكتب ذلك صراحة داخل القسم نفسه.\n"
        "3. لا تكرر أي سطر أو عبارة في الذيل.\n"
        "4. لا تضف أنظمة أو مواد غير موجودة في النصوص المسترجعة.\n"
        "5. اجعل الجواب مكتملًا لكن مقتصدًا، ولا تبالغ في التطويل.\n\n"
        f"العناوين المطلوبة:\n{required_sections}\n\n"
        f"النسخة السابقة غير المكتملة:\n{condensed_draft(draft)}"
    )


def choose_best_candidate(mode: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    def key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        report = candidate["report"]
        soft_limit = MODE_CHAR_SOFT_LIMITS[mode]
        over_limit_chars = max(int(report.get("char_count", 0)) - soft_limit, 0)
        return (
            0 if report.get("thought_leak") else 1,
            0 if report.get("repeated_line_flag") else 1,
            0 if report.get("filler_flag") else 1,
            0 if report.get("repeated_phrase_tail_flag") else 1,
            0 if report.get("heading_only_flag") else 1,
            float(report.get("section_coverage", 0.0)),
            -len(report.get("thin_sections", [])),
            -len(report.get("missing_sections", [])),
            -over_limit_chars,
            float(report.get("quality_score", 0.0)),
        )

    return max(candidates, key=key)
