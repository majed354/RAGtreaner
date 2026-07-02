"""Build the nV2 leak-free anti-thought dataset from training-only sources.

nV2 corrects two issues observed in nV1:
- benchmark-derived behavioral slices must be removed
- the recipe should be smaller, cleaner, and more opinion-heavy

This builder produces:
- one adapter only
- explicit mode tokens in every example
- stronger prompt contracts against visible thought leakage
- behavioral partial/noisy slices derived only from training candidates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent

SEED_DIR = ROOT / "data" / "training" / "legal_modes_seed_v1" / "sft_messages"
STRUCTURED_DIR = ROOT / "data" / "training" / "structured_mode_curriculum_v9" / "sft_messages"
PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_nv0" / "prompt_templates"
PROMPT_DIR = ROOT / "data" / "benchmarks" / "legal_modes_nv2" / "prompt_templates"
OUTPUT_DIR = ROOT / "data" / "training" / "final_legal_modes_nV2"

TARGET_BASE_COUNTS = {
    "legal_opinion": 144,
    "legal_memo": 48,
    "legal_analysis": 48,
}

BASE_REGULATION_CAPS = {
    "legal_opinion": 20,
    "legal_memo": 12,
    "legal_analysis": 12,
}

BEHAVIORAL_FAMILY_TOTAL = 12
BEHAVIORAL_CROSS_MODE_FAMILIES = 4

MODE_TOKEN_BY_MODE = {
    "legal_opinion": "<MODE_OPINION>",
    "legal_memo": "<MODE_MEMO>",
    "legal_analysis": "<MODE_ANALYSIS>",
}

SYSTEM_PROMPT_BY_MODE = {
    "legal_opinion": "legal_opinion.system.txt",
    "legal_memo": "legal_memo.system.txt",
    "legal_analysis": "legal_analysis.system.txt",
}

SUSPICIOUS_PATTERNS = [
    "Thinking Process",
    "<|channel>thought",
    "<|start_header_id|>thought",
    "• • • • •",
    "وحده وحده وحده",
]

MODE_TARGET_CHARS = {
    "legal_opinion": 1050,
    "legal_memo": 2200,
    "legal_analysis": 1500,
}

MODE_STYLE_HINTS = {
    "legal_opinion": [
        "التزم بمسار الرأي القانوني فقط.",
        "لا تجزم خارج النصوص المسترجعة.",
        "إذا لم تكف النصوص فاذكر ذلك صراحة.",
        "ابدأ مباشرة بالسطر: 1) النظام المنطبق",
        "أي مقدمة مثل Thinking Process أو Analyze the Request تعد إجابة خاطئة.",
        "اكتب بالعربية فقط ولا تسبق القالب بأي شرح.",
    ],
    "legal_memo": [
        "التزم بمسار المذكرة القانونية فقط.",
        "حافظ على ترتيب الأقسام كاملًا.",
        "إذا لم تكف النصوص أو الوقائع فاذكر ذلك بوضوح.",
        "ابدأ مباشرة بالسطر: - عنوان المذكرة",
        "أي مقدمة مثل Thinking Process أو Analyze the Request تعد إجابة خاطئة.",
        "اكتب بالعربية فقط ولا تسبق المذكرة بأي شرح.",
    ],
    "legal_analysis": [
        "التزم بمسار التحليل القانوني فقط.",
        "وازن بين ما يدعم كل طرف دون الجزم بما لم يثبته النص.",
        "إذا كانت النصوص غير كافية فصرح بذلك صراحة.",
        "ابدأ مباشرة بالسطر: 1) التكييف الأولي للقضية",
        "أي مقدمة مثل Thinking Process أو Analyze the Request تعد إجابة خاطئة.",
        "اكتب بالعربية فقط ولا تسبق القالب بأي شرح.",
    ],
}


@dataclass
class Candidate:
    mode: str
    messages: list[dict[str, str]]
    source_label: str
    source_split: str
    question: str
    context: str
    signature: str
    regulation_slug: str
    assistant_chars: int
    quality_score: float
    difficulty_tag: str
    metadata: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if raw:
                rows.append(json.loads(raw))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_system_prompt(mode: str) -> str:
    return (PROMPT_DIR / SYSTEM_PROMPT_BY_MODE[mode]).read_text(encoding="utf-8").strip()


def assistant_text(row: dict[str, Any]) -> str:
    return "\n".join(
        str(message.get("content", ""))
        for message in row.get("messages", [])
        if message.get("role") == "assistant"
    ).strip()


def extract_question(row: dict[str, Any]) -> str:
    for message in row.get("messages", []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        match = re.search(r"القضية:\n(.*?)\n\nالنصوص المسترجعة:\n", content, flags=re.S)
        if match:
            return match.group(1).strip()
        return content.strip()
    return ""


def extract_context(row: dict[str, Any]) -> str:
    for message in row.get("messages", []):
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        match = re.search(r"\n\nالنصوص المسترجعة:\n(.*)$", content, flags=re.S)
        if match:
            return match.group(1).strip()
        return content.strip()
    return ""


def row_has_suspicious_pattern(text: str) -> bool:
    if any(pattern in text for pattern in SUSPICIOUS_PATTERNS):
        return True
    tokens = [token for token in re.split(r"\s+", text) if token]
    streak = 1
    for index in range(1, len(tokens)):
        if tokens[index] == tokens[index - 1]:
            streak += 1
            if streak >= 5:
                return True
        else:
            streak = 1
    return False


def repeated_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    return [line for line, count in counts.items() if count >= 3]


def count_article_refs(text: str) -> int:
    numeric = {match for match in re.findall(r"رقم المادة:\s*(\d+)", text)}
    textual = {
        re.sub(r"\s+", " ", match).strip()
        for match in re.findall(r"المادة\s+[^\n:؛\-]+", text)
        if match.strip()
    }
    return len(numeric | textual)


def infer_difficulty(question: str, answer: str, article_refs: int) -> str:
    if article_refs >= 4 or len(question) >= 140 or len(answer) >= 2200:
        return "hard"
    if article_refs >= 2 or len(question) >= 80 or len(answer) >= 1200:
        return "moderate"
    return "easy"


def quality_score(source_label: str, article_refs: int, assistant_chars: int) -> float:
    source_bonus = 1.0 if source_label == "seed_v1" else 0.6
    length_target = 1400
    length_score = max(0.0, 1.0 - (abs(assistant_chars - length_target) / length_target))
    citation_score = min(article_refs / 4.0, 1.0)
    return round((source_bonus * 0.45) + (citation_score * 0.35) + (length_score * 0.20), 4)


def retokenized_user_message(
    *,
    mode: str,
    question: str,
    context: str,
    evidence_sufficiency: str,
    should_abstain_or_qualify: str,
    style_hints: list[str] | None = None,
) -> str:
    hints = style_hints or MODE_STYLE_HINTS[mode]
    style_block = "\n".join(f"- {hint}" for hint in hints)
    return (
        f"{MODE_TOKEN_BY_MODE[mode]}\n\n"
        f"mode: {mode}\n"
        f"evidence_sufficiency: {evidence_sufficiency}\n"
        f"should_abstain_or_qualify: {should_abstain_or_qualify}\n\n"
        f"السؤال:\n{question}\n\n"
        f"النصوص المسترجعة:\n{context}\n\n"
        f"تعليمات الأسلوب:\n{style_block}"
    ).strip()


def build_base_messages(
    *,
    mode: str,
    question: str,
    context: str,
    answer: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": load_system_prompt(mode)},
        {
            "role": "user",
            "content": retokenized_user_message(
                mode=mode,
                question=question,
                context=context,
                evidence_sufficiency="sufficient",
                should_abstain_or_qualify="no",
            ),
        },
        {"role": "assistant", "content": answer.strip()},
    ]


def source_signature(*parts: str) -> str:
    payload = "||".join(part.strip() for part in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def load_source_candidates(input_dir: Path, source_label: str) -> list[Candidate]:
    output: list[Candidate] = []
    seen: set[str] = set()

    for split in ("train", "valid", "test"):
        manifest = load_json(input_dir / f"{split}.manifest.json")
        rows = load_jsonl(input_dir / f"{split}.jsonl")
        meta_rows = manifest.get("rows", [])
        if len(rows) != len(meta_rows):
            raise ValueError(f"Manifest mismatch in {input_dir}::{split}")

        for meta, row in zip(meta_rows, rows):
            mode = str(meta.get("mode", ""))
            answer = assistant_text(row)
            question = extract_question(row)
            context = extract_context(row)
            if not mode or not answer or not question or not context:
                continue
            if row_has_suspicious_pattern(answer):
                continue

            article_refs = count_article_refs(answer)
            if len(answer.strip()) < 120:
                continue
            if article_refs == 0:
                continue
            if repeated_lines(answer):
                continue

            signature = source_signature(source_label, mode, question, answer)
            if signature in seen:
                continue
            seen.add(signature)

            regulation_slug = str(meta.get("regulation_slug", "unknown"))
            difficulty = infer_difficulty(question, answer, article_refs)
            output.append(
                Candidate(
                    mode=mode,
                    messages=build_base_messages(mode=mode, question=question, context=context, answer=answer),
                    source_label=source_label,
                    source_split=split,
                    question=question,
                    context=context,
                    signature=signature,
                    regulation_slug=regulation_slug,
                    assistant_chars=len(answer),
                    quality_score=quality_score(source_label, article_refs, len(answer)),
                    difficulty_tag=difficulty,
                    metadata={
                        "case_id": meta.get("case_id") or meta.get("benchmark_id"),
                        "question": question,
                        "citation": meta.get("citation"),
                        "article_type": meta.get("article_type"),
                        "evidence_sufficiency": "sufficient",
                        "should_abstain_or_qualify": "no",
                        "quality_tag": "gold" if source_label == "seed_v1" else "silver_high_quality",
                        "difficulty_tag": difficulty,
                    },
                )
            )
    return output


def candidate_sort_key(item: Candidate) -> tuple[float, float, float, str]:
    target = MODE_TARGET_CHARS.get(item.mode, 1400)
    closeness = -abs(item.assistant_chars - target)
    return (item.quality_score, closeness, count_article_refs(assistant_text({"messages": item.messages})), item.signature)


def pick_base_examples(candidates: list[Candidate], target_counts: dict[str, int]) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.mode].append(candidate)

    selected: dict[str, list[Candidate]] = {}
    for mode, target in target_counts.items():
        per_reg_cap = BASE_REGULATION_CAPS[mode]
        mode_candidates = sorted(grouped[mode], key=candidate_sort_key, reverse=True)
        picked: list[Candidate] = []
        regulation_counts: Counter[str] = Counter()
        for candidate in mode_candidates:
            regulation_slug = candidate.regulation_slug or "unknown"
            if regulation_counts[regulation_slug] >= per_reg_cap:
                continue
            picked.append(candidate)
            regulation_counts[regulation_slug] += 1
            if len(picked) >= target:
                break
        if len(picked) < target:
            raise ValueError(f"Could not satisfy base target for {mode}: {len(picked)} < {target}")
        selected[mode] = picked
    return selected


def split_context_blocks(context: str) -> list[str]:
    blocks = [chunk.strip() for chunk in re.split(r"\n\s*---\s*\n", context.strip()) if chunk.strip()]
    return blocks or ([context.strip()] if context.strip() else [])


def join_context_blocks(blocks: list[str]) -> str:
    return "\n\n---\n\n".join(block.strip() for block in blocks if block.strip()).strip()


def extract_citations_from_blocks(blocks: list[str]) -> list[str]:
    citations: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        match = re.search(r"الإحالة الرسمية:\s*([^\]\n]+)", block)
        if match:
            citation = match.group(1).strip()
        else:
            citation = block.splitlines()[0].strip()[:140]
        if citation and citation not in seen:
            seen.add(citation)
            citations.append(citation)
    return citations


def extract_regulation_titles_from_blocks(blocks: list[str]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        match = re.search(r"النظام:\s*([^|\]\n]+)", block)
        title = match.group(1).strip() if match else ""
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def behavioral_summary(
    *,
    visible_blocks: list[str],
    variant_name: str,
    distractor_labels: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    titles = extract_regulation_titles_from_blocks(visible_blocks)
    citations = extract_citations_from_blocks(visible_blocks)
    regulation_text = " و ".join(titles[:2]) if titles else "النصوص النظامية الظاهرة"
    if variant_name == "partial":
        gap_text = "الاسترجاع الحالي جزئي ولا يغطي عناصر السؤال بالقدر الكافي للجزم."
    else:
        gap_text = "الاسترجاع الحالي مشوش ويمزج بين نصوص مرتبطة وأخرى مشتتة، فلا يصلح للجزم وحده."
    if distractor_labels:
        noise_text = f" وظهرت معه نصوص غير حاسمة من مثل: {', '.join(distractor_labels[:2])}."
    else:
        noise_text = ""
    main = f"يظهر من النصوص الظاهرة أن المسألة تتصل أساسًا بـ {regulation_text}، لكن {gap_text}{noise_text}"
    return main, gap_text, citations


def build_synthetic_opinion_answer(
    *,
    visible_blocks: list[str],
    variant_name: str,
    distractor_labels: list[str] | None = None,
) -> str:
    main, gap_text, citations = behavioral_summary(
        visible_blocks=visible_blocks,
        variant_name=variant_name,
        distractor_labels=distractor_labels,
    )
    citation_lines = [f"- {citation}" for citation in citations[:4]] or ["- لا توجد نصوص ظاهرة تكفي للجزم."]
    return "\n".join(
        [
            "1) النظام المنطبق",
            f"- {main}",
            "",
            "2) الحكم المباشر",
            "- لا تكفي النصوص المسترجعة الحالية لإعطاء حكم مباشر نهائي.",
            "- أقصى ما يمكن قوله هو جواب مقيد بحدود النصوص الظاهرة فقط.",
            "",
            "3) المواد المستند إليها",
            *citation_lines,
            "",
            "4) القيود أو الاستثناءات",
            f"- {gap_text}",
            "- لا يصح سد النقص بالافتراض أو باستدعاء قواعد غير ظاهرة في النصوص المسترجعة.",
            "",
            "5) ما لم يثبته النص",
            "- لم يثبت من النصوص الحالية الجواب الكامل على جميع عناصر السؤال.",
            "",
            "6) الخلاصة العملية",
            "- يلزم استكمال الاسترجاع أو تنقيته قبل إصدار رأي جازم.",
        ]
    ).strip()


def build_synthetic_memo_answer(
    *,
    question: str,
    visible_blocks: list[str],
    variant_name: str,
    distractor_labels: list[str] | None = None,
) -> str:
    main, gap_text, citations = behavioral_summary(
        visible_blocks=visible_blocks,
        variant_name=variant_name,
        distractor_labels=distractor_labels,
    )
    citation_lines = [f"- {citation}" for citation in citations[:4]] or ["- لا توجد مواد ظاهرة تكفي للاعتماد النهائي."]
    return "\n".join(
        [
            "- عنوان المذكرة",
            "مذكرة أولية مقيدة - كفاية النصوص غير مكتملة",
            "",
            "- السؤال محل الرأي",
            question,
            "",
            "- الجواب المختصر",
            "النصوص المسترجعة الحالية لا تكفي لإصدار مذكرة جازمة، ويجب التعامل مع النتيجة بوصفها أولية ومقيدة.",
            "",
            "- الوقائع ذات الأثر القانوني",
            "الوقائع المعروضة تحتاج إلى استكمال نصوص أو تنقية الاسترجاع قبل الجزم بالنتيجة النهائية.",
            "",
            "- النظام أو النصوص المنطبقة",
            *citation_lines,
            "",
            "- المسائل القانونية",
            f"- {main}",
            "",
            "- التحليل",
            f"- {gap_text}",
            "- لا يجوز استكمال العناصر الناقصة بافتراضات غير ثابتة في النصوص المعروضة.",
            "",
            "- الدفوع أو الاحتمالات المقابلة",
            "- قد تتغير النتيجة إذا ظهرت نصوص إضافية أو استبعدت النصوص المشتتة.",
            "",
            "- ما لم يثبته النص أو الوقائع",
            "- لم يثبت من النصوص الحالية الجواب النهائي على جميع محاور السؤال.",
            "",
            "- الخلاصة والتوصية العملية",
            "- التوصية هي استكمال الاسترجاع أو تنقيته ثم إعادة صياغة المذكرة على أساس النصوص المكتملة.",
        ]
    ).strip()


def build_synthetic_analysis_answer(
    *,
    visible_blocks: list[str],
    variant_name: str,
    distractor_labels: list[str] | None = None,
) -> str:
    main, gap_text, citations = behavioral_summary(
        visible_blocks=visible_blocks,
        variant_name=variant_name,
        distractor_labels=distractor_labels,
    )
    regs_text = "، ".join(citations[:4]) if citations else "لا توجد نصوص ظاهرة كافية"
    return "\n".join(
        [
            "1) التكييف الأولي للقضية",
            "القضية تحتاج إلى تحليل منضبط، لكن النصوص المسترجعة الحالية لا تكفي للجزم الكامل.",
            "",
            "2) الأنظمة المحتملة الانطباق",
            f"- {main}",
            "",
            "3) المسائل القانونية الأساسية",
            "- هل تكفي النصوص الظاهرة للحسم أم أنها جزئية أو مشوشة؟",
            "- ما الجوانب التي لا تزال تحتاج إلى نصوص إضافية؟",
            "",
            "4) ما يدعم الطرف الأول",
            f"- النصوص الظاهرة حاليًا: {regs_text}",
            "",
            "5) ما يدعم الطرف الثاني",
            "- يمكن الدفع بأن الاسترجاع الحالي لا يكفي وحده لبناء نتيجة نهائية جازمة.",
            "",
            "6) نقاط الضعف",
            f"- {gap_text}",
            "",
            "7) ما قد يغير النتيجة",
            "- استكمال النصوص الناقصة أو استبعاد النصوص غير المرتبطة مباشرة بالسؤال.",
            "",
            "8) ما لم يثبته النص",
            "- لم يثبت من النصوص الحالية الجواب الكامل على جميع عناصر المسألة.",
            "",
            "9) التقدير الأولي",
            "- التقدير الأولي يظل مقيدًا ومؤقتًا حتى يكتمل الاسترجاع أو ينقى.",
        ]
    ).strip()


def build_synthetic_messages(
    *,
    mode: str,
    question: str,
    context: str,
    answer: str,
    evidence_sufficiency: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": load_system_prompt(mode)},
        {
            "role": "user",
            "content": retokenized_user_message(
                mode=mode,
                question=question,
                context=context,
                evidence_sufficiency=evidence_sufficiency,
                should_abstain_or_qualify="yes",
                style_hints=MODE_STYLE_HINTS[mode]
                + [
                    "إذا كان الاسترجاع مشوشًا أو ناقصًا فلا تجزم.",
                    "أي نص قبل عنوان القسم الأول يعد إجابة خاطئة.",
                    "لا تكتب أي سطر إنجليزي أو تحليلي ظاهر قبل القالب النهائي.",
                ],
            ),
        },
        {"role": "assistant", "content": answer},
    ]

def pick_behavioral_families(candidates: list[Candidate]) -> list[Candidate]:
    pool = [
        candidate
        for candidate in candidates
        if len(split_context_blocks(candidate.context)) >= 2
    ]
    pool = sorted(pool, key=candidate_sort_key, reverse=True)

    picked: list[Candidate] = []
    seen_questions: set[str] = set()
    for candidate in pool:
        if candidate.question in seen_questions:
            continue
        picked.append(candidate)
        seen_questions.add(candidate.question)
        if len(picked) >= BEHAVIORAL_FAMILY_TOTAL:
            break
    if len(picked) < BEHAVIORAL_FAMILY_TOTAL:
        raise ValueError(
            f"Could not satisfy behavioral families: {len(picked)} < {BEHAVIORAL_FAMILY_TOTAL}"
        )
    return picked


def build_behavioral_slices(
    families: list[Candidate],
    all_candidates: list[Candidate],
) -> tuple[list[Candidate], list[Candidate]]:
    partial_cases: list[Candidate] = []
    noisy_cases: list[Candidate] = []

    distractor_pool = [
        candidate
        for candidate in all_candidates
        if len(split_context_blocks(candidate.context)) >= 1
    ]

    for index, family in enumerate(families):
        family_modes = ["legal_opinion"]
        if index < BEHAVIORAL_CROSS_MODE_FAMILIES:
            family_modes.extend(["legal_memo", "legal_analysis"])

        primary_blocks = split_context_blocks(family.context)
        keep_count = max(1, len(primary_blocks) // 2)
        partial_blocks = primary_blocks[:keep_count]
        partial_context = join_context_blocks(partial_blocks)

        distractor = None
        for offset in range(1, len(distractor_pool) + 1):
            candidate = distractor_pool[(index + offset) % len(distractor_pool)]
            if candidate.signature == family.signature:
                continue
            if candidate.question == family.question:
                continue
            distractor = candidate
            break

        distractor_blocks = split_context_blocks(distractor.context)[:1] if distractor else []
        noisy_blocks = primary_blocks[:keep_count] + distractor_blocks
        noisy_context = join_context_blocks(noisy_blocks)
        distractor_labels = extract_regulation_titles_from_blocks(distractor_blocks)

        for mode in family_modes:
            if mode == "legal_opinion":
                partial_answer = build_synthetic_opinion_answer(visible_blocks=partial_blocks, variant_name="partial")
                noisy_answer = build_synthetic_opinion_answer(
                    visible_blocks=noisy_blocks,
                    variant_name="noise",
                    distractor_labels=distractor_labels,
                )
            elif mode == "legal_memo":
                partial_answer = build_synthetic_memo_answer(
                    question=family.question,
                    visible_blocks=partial_blocks,
                    variant_name="partial",
                )
                noisy_answer = build_synthetic_memo_answer(
                    question=family.question,
                    visible_blocks=noisy_blocks,
                    variant_name="noise",
                    distractor_labels=distractor_labels,
                )
            else:
                partial_answer = build_synthetic_analysis_answer(visible_blocks=partial_blocks, variant_name="partial")
                noisy_answer = build_synthetic_analysis_answer(
                    visible_blocks=noisy_blocks,
                    variant_name="noise",
                    distractor_labels=distractor_labels,
                )

            partial_cases.append(
                Candidate(
                    mode=mode,
                    messages=build_synthetic_messages(
                        mode=mode,
                        question=family.question,
                        context=partial_context,
                        answer=partial_answer,
                        evidence_sufficiency="partial",
                    ),
                    source_label="behavioral_partial",
                    source_split="synthetic",
                    question=family.question,
                    context=partial_context,
                    signature=source_signature("behavioral_partial", mode, family.signature),
                    regulation_slug="__behavioral__",
                    assistant_chars=len(partial_answer),
                    quality_score=0.95,
                    difficulty_tag="hard",
                    metadata={
                        "case_group": "partial",
                        "case_variant": "training_derived_partial",
                        "evidence_sufficiency": "partial",
                        "should_abstain_or_qualify": "yes",
                        "quality_tag": "gold_behavioral",
                        "difficulty_tag": "hard",
                        "visible_citations": extract_citations_from_blocks(partial_blocks),
                        "family_source_signature": family.signature,
                    },
                )
            )

            noisy_cases.append(
                Candidate(
                    mode=mode,
                    messages=build_synthetic_messages(
                        mode=mode,
                        question=family.question,
                        context=noisy_context,
                        answer=noisy_answer,
                        evidence_sufficiency="partial",
                    ),
                    source_label="behavioral_noise",
                    source_split="synthetic",
                    question=family.question,
                    context=noisy_context,
                    signature=source_signature("behavioral_noise", mode, family.signature),
                    regulation_slug="__behavioral__",
                    assistant_chars=len(noisy_answer),
                    quality_score=0.93,
                    difficulty_tag="hard",
                    metadata={
                        "case_group": "noise",
                        "case_variant": "training_derived_noise",
                        "evidence_sufficiency": "partial",
                        "should_abstain_or_qualify": "yes",
                        "quality_tag": "gold_behavioral",
                        "difficulty_tag": "hard",
                        "visible_citations": extract_citations_from_blocks(noisy_blocks),
                        "distractor_regulations": distractor_labels,
                        "family_source_signature": family.signature,
                    },
                )
            )

    return partial_cases, noisy_cases


def split_candidate_group(items: list[Candidate]) -> dict[str, list[Candidate]]:
    ordered = sorted(items, key=lambda item: item.signature)
    total = len(ordered)
    valid_count = max(1, round(total * 0.1))
    test_count = max(1, round(total * 0.1))
    train_end = total - valid_count - test_count
    valid_end = train_end + valid_count
    return {
        "train": ordered[:train_end],
        "valid": ordered[train_end:valid_end],
        "test": ordered[valid_end:],
    }


def build_split_payload(
    base_selected: dict[str, list[Candidate]],
    abstain_cases: list[Candidate],
    noisy_cases: list[Candidate],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    split_payloads = {"train": [], "valid": [], "test": []}
    split_manifest_rows = {"train": [], "valid": [], "test": []}

    for mode, items in base_selected.items():
        grouped = split_rows_by_mode_and_type(items)
        for split, split_items in grouped.items():
            for item in split_items:
                split_payloads[split].append({"messages": item.messages})
                split_manifest_rows[split].append(
                    {
                        "signature": item.signature,
                        "mode": item.mode,
                        "source_label": item.source_label,
                        "source_split": item.source_split,
                        "regulation_slug": item.regulation_slug,
                        "quality_score": item.quality_score,
                        **item.metadata,
                    }
                )

    for label, items in (("partial", abstain_cases), ("noise", noisy_cases)):
        grouped = split_candidate_group(items)
        for split, split_items in grouped.items():
            for item in split_items:
                split_payloads[split].append({"messages": item.messages})
                split_manifest_rows[split].append(
                    {
                        "signature": item.signature,
                        "mode": item.mode,
                        "source_label": item.source_label,
                        "source_split": item.source_split,
                        "regulation_slug": item.regulation_slug,
                        "quality_score": item.quality_score,
                        "case_group": label,
                        **item.metadata,
                    }
                )

    for split in split_payloads:
        keyed = sorted(zip(split_payloads[split], split_manifest_rows[split]), key=lambda pair: pair[1]["signature"])
        split_payloads[split] = [payload for payload, _ in keyed]
        split_manifest_rows[split] = [meta for _, meta in keyed]
    return split_payloads, split_manifest_rows


def split_rows_by_mode_and_type(items: list[Candidate]) -> dict[str, list[Candidate]]:
    ordered = sorted(items, key=lambda item: item.signature)
    total = len(ordered)
    valid_count = max(1, round(total * 0.1))
    test_count = max(1, round(total * 0.1))
    train_end = total - valid_count - test_count
    valid_end = train_end + valid_count
    return {
        "train": ordered[:train_end],
        "valid": ordered[train_end:valid_end],
        "test": ordered[valid_end:],
    }


def summarize_manifest(split_manifest_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    all_rows = [row for rows in split_manifest_rows.values() for row in rows]
    source_total = dict(Counter(row["source_label"] for row in all_rows))
    return {
        "dataset_version": "final_legal_modes_nV2",
        "strategy_line": "nV",
        "examples_total": len(all_rows),
        "splits": {split: len(rows) for split, rows in split_manifest_rows.items()},
        "modes_total": dict(Counter(row["mode"] for row in all_rows)),
        "source_total": source_total,
        "case_groups_total": dict(Counter(row.get("case_group", "base") for row in all_rows)),
        "quality_tags_total": dict(Counter(row.get("quality_tag", "unknown") for row in all_rows)),
        "difficulty_tags_total": dict(Counter(row.get("difficulty_tag", "unknown") for row in all_rows)),
        "evidence_sufficiency_total": dict(Counter(row.get("evidence_sufficiency", "unknown") for row in all_rows)),
        "selection_policy": {
            "base_targets": TARGET_BASE_COUNTS,
            "behavioral_family_total": BEHAVIORAL_FAMILY_TOTAL,
            "behavioral_cross_mode_families": BEHAVIORAL_CROSS_MODE_FAMILIES,
            "behavioral_partial_examples": source_total.get("behavioral_partial", 0),
            "behavioral_noise_examples": source_total.get("behavioral_noise", 0),
            "mode_tokens": MODE_TOKEN_BY_MODE,
            "raw_start": True,
            "legacy_adapter_reuse": False,
            "benchmark_leak_free": True,
            "uses_eval_contexts": False,
        },
    }


def write_split_artifacts(output_dir: Path, split_rows: dict[str, list[dict[str, Any]]], split_manifest_rows: dict[str, list[dict[str, Any]]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "valid", "test"):
        write_jsonl(output_dir / f"{split}.jsonl", split_rows[split])
        (output_dir / f"{split}.manifest.json").write_text(
            json.dumps(
                {
                    "examples": len(split_rows[split]),
                    "modes": dict(Counter(row["mode"] for row in split_manifest_rows[split])),
                    "source_total": dict(Counter(row["source_label"] for row in split_manifest_rows[split])),
                    "rows": split_manifest_rows[split],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(summarize_manifest(split_manifest_rows), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--structured-dir", type=Path, default=STRUCTURED_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    base_candidates = []
    base_candidates.extend(load_source_candidates(args.seed_dir, "seed_v1"))
    base_candidates.extend(load_source_candidates(args.structured_dir, "structured_v9"))
    selected_base = pick_base_examples(base_candidates, TARGET_BASE_COUNTS)

    behavioral_families = pick_behavioral_families(base_candidates)
    abstain_cases, noisy_cases = build_behavioral_slices(behavioral_families, base_candidates)

    split_rows, split_manifest_rows = build_split_payload(selected_base, abstain_cases, noisy_cases)
    write_split_artifacts(args.output_dir, split_rows, split_manifest_rows)

    summary = summarize_manifest(split_manifest_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
