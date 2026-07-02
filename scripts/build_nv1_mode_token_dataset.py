"""Build the nV1 high-quality mode-token dataset from raw sources.

nV1 is the first clean restart from the raw model under the new strategy:
- one adapter only
- explicit mode tokens in every example
- smaller, behavior-focused dataset
- dedicated slices for abstention / insufficiency / noisy retrieval

The resulting dataset stays fully separate from the legacy v* series.
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
BENCHMARK_CASES = ROOT / "data" / "eval" / "legal_eval_advanced_set.jsonl"
BENCHMARK_CONTEXTS = (
    ROOT
    / "data"
    / "benchmarks"
    / "legal_modes_v1"
    / "results"
    / "current_reference"
    / "legal_memo_frozen.contexts.jsonl"
)
OUTPUT_DIR = ROOT / "data" / "training" / "final_legal_modes_nV1"

TARGET_BASE_COUNTS = {
    "legal_opinion": 180,
    "legal_memo": 120,
    "legal_analysis": 120,
}

BASE_REGULATION_CAPS = {
    "legal_opinion": 24,
    "legal_memo": 18,
    "legal_analysis": 18,
}

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
    ],
    "legal_memo": [
        "التزم بمسار المذكرة القانونية فقط.",
        "حافظ على ترتيب الأقسام كاملًا.",
        "إذا لم تكف النصوص أو الوقائع فاذكر ذلك بوضوح.",
    ],
    "legal_analysis": [
        "التزم بمسار التحليل القانوني فقط.",
        "وازن بين ما يدعم كل طرف دون الجزم بما لم يثبته النص.",
        "إذا كانت النصوص غير كافية فصرح بذلك صراحة.",
    ],
}


@dataclass
class Candidate:
    mode: str
    messages: list[dict[str, str]]
    source_label: str
    source_split: str
    question: str
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


def load_benchmark_case_lookup(path: Path) -> dict[str, dict[str, Any]]:
    rows = load_jsonl(path)
    return {f"memo::{row['question_id']}": row for row in rows}


def load_benchmark_contexts(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path)


def render_source(index: int, source: dict[str, Any]) -> str:
    return (
        f"[المصدر {index} | النظام: {source.get('regulation_title', '')} | "
        f"رقم المادة: {source.get('article_index', '')} | "
        f"تسمية المادة: {source.get('article_label', '')} | "
        f"نوع المادة: {source.get('article_type_label', '')} | "
        f"الإحالة الرسمية: {source.get('citation', '')}]\n"
        f"{str(source.get('text', '')).strip()}"
    ).strip()


def render_context(sources: list[dict[str, Any]]) -> str:
    return "\n\n---\n\n".join(render_source(index, source) for index, source in enumerate(sources, start=1))


def list_citations(sources: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    citations: list[str] = []
    for source in sources:
        citation = str(source.get("citation", "")).strip()
        if citation and citation not in seen:
            seen.add(citation)
            citations.append(citation)
    return citations


def format_article_list(values: list[int]) -> str:
    if not values:
        return ""
    return "، ".join(str(value) for value in sorted(values))


def split_sources_by_relevance(
    case: dict[str, Any],
    context_row: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected_regs = set(case.get("expected_regulations", []))
    expected_articles = set(case.get("expected_articles", []))
    relevant: list[dict[str, Any]] = []
    tangential: list[dict[str, Any]] = []
    for source in context_row.get("source_catalog", []) or []:
        regulation_slug = str(source.get("regulation_slug", ""))
        article_index = int(source.get("article_index", 0) or 0)
        if regulation_slug in expected_regs and article_index in expected_articles:
            relevant.append(source)
        else:
            tangential.append(source)
    return relevant, tangential


def build_abstain_variant_sources(
    *,
    case: dict[str, Any],
    context_row: dict[str, Any],
    variant_name: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    relevant, tangential = split_sources_by_relevance(case, context_row)
    expected_articles = [int(value) for value in case.get("expected_articles", [])]
    if variant_name == "partial":
        if len(relevant) >= 2:
            chosen = relevant[: max(1, len(relevant) // 2)]
        elif relevant:
            chosen = relevant[:1]
        else:
            chosen = tangential[:1]
    else:
        if tangential:
            chosen = tangential[: min(2, len(tangential))]
        elif relevant:
            chosen = relevant[:1]
        else:
            chosen = (context_row.get("source_catalog", []) or [])[:1]
    visible_articles = {int(source.get("article_index", 0) or 0) for source in chosen}
    missing_articles = [article for article in expected_articles if article not in visible_articles]
    return chosen, missing_articles


def build_noisy_variant_sources(
    *,
    case: dict[str, Any],
    context_row: dict[str, Any],
    distractor_row: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[int]]:
    relevant, tangential = split_sources_by_relevance(case, context_row)
    expected_articles = [int(value) for value in case.get("expected_articles", [])]
    kept_relevant = relevant[:2] if relevant else (context_row.get("source_catalog", []) or [])[:1]
    distractors = []
    for source in distractor_row.get("source_catalog", []) or []:
        if source.get("regulation_slug") not in case.get("expected_regulations", []):
            distractors.append(source)
        if len(distractors) >= 2:
            break
    chosen = kept_relevant + distractors
    visible_articles = {int(source.get("article_index", 0) or 0) for source in kept_relevant}
    missing_articles = [article for article in expected_articles if article not in visible_articles]
    distractor_regs = sorted({str(source.get("regulation_title", "")).strip() for source in distractors if str(source.get("regulation_title", "")).strip()})
    return chosen, distractor_regs, missing_articles


def qualified_summary(
    case: dict[str, Any],
    visible_sources: list[dict[str, Any]],
    missing_articles: list[int],
    distractor_regs: list[str] | None = None,
) -> tuple[str, str]:
    visible_reg_titles = sorted(
        {
            str(source.get("regulation_title", "")).strip()
            for source in visible_sources
            if str(source.get("regulation_title", "")).strip()
        }
    )
    regulation_text = " و ".join(visible_reg_titles) if visible_reg_titles else "النظام ذي الصلة"
    if missing_articles:
        missing_text = f"وتغيب من الاسترجاع الحالي مواد مهمة مثل: {format_article_list(missing_articles)}."
    else:
        missing_text = "لكن الاسترجاع الحالي لا يغطي كل عناصر السؤال بالقدر الكافي."
    if distractor_regs:
        noise_text = f" كما ظهرت نصوص من أنظمة أخرى مثل: {', '.join(distractor_regs)}، ولا يصح بناء الجواب عليها مباشرة."
    else:
        noise_text = ""
    main = f"يظهر من السؤال أن المسألة تتصل أساسًا بـ {regulation_text}، إلا أن النصوص المسترجعة الحالية لا تكفي للجزم الكامل.{noise_text}"
    return main, missing_text


def build_synthetic_opinion_answer(
    *,
    case: dict[str, Any],
    visible_sources: list[dict[str, Any]],
    missing_articles: list[int],
    distractor_regs: list[str] | None = None,
) -> str:
    main, missing_text = qualified_summary(case, visible_sources, missing_articles, distractor_regs)
    citations = list_citations(visible_sources)
    citation_lines = [f"- {citation}" for citation in citations] or ["- لا توجد مواد كافية ظاهرة للجزم."]
    return "\n".join(
        [
            "1) النظام المنطبق",
            f"- {main}",
            "",
            "2) الحكم المباشر",
            "- لا يكفي ما استُرجع للجزم بحكم مباشر نهائي في هذه المسألة.",
            "- يقتصر الجواب الحالي على الإشارة إلى أن النصوص الظاهرة تعالج بعض جوانب السؤال فقط.",
            "",
            "3) المواد المستند إليها",
            *citation_lines,
            "",
            "4) القيود أو الاستثناءات",
            f"- {missing_text}",
            "",
            "5) ما لم يثبته النص",
            "- لم يثبت من النصوص المسترجعة الحالية الجواب الكامل على جميع عناصر السؤال.",
            "",
            "6) الخلاصة العملية",
            "- يلزم استكمال الاسترجاع قبل إصدار رأي جازم، ويقتصر الموقف الحالي على الجواب المقيّد أعلاه.",
        ]
    ).strip()


def build_synthetic_memo_answer(
    *,
    case: dict[str, Any],
    visible_sources: list[dict[str, Any]],
    missing_articles: list[int],
    distractor_regs: list[str] | None = None,
) -> str:
    main, missing_text = qualified_summary(case, visible_sources, missing_articles, distractor_regs)
    citations = list_citations(visible_sources)
    citation_lines = [f"- {citation}" for citation in citations] or ["- لم تظهر مواد كافية للاعتماد النهائي."]
    return "\n".join(
        [
            "- عنوان المذكرة",
            "مذكرة داخلية أولية - كفاية النصوص غير مكتملة",
            "",
            "- السؤال محل الرأي",
            case.get("question", ""),
            "",
            "- الجواب المختصر",
            "النصوص المسترجعة الحالية لا تكفي لإصدار مذكرة جازمة، ويجب التعامل مع النتيجة بوصفها أولية ومقيدة.",
            "",
            "- الوقائع ذات الأثر القانوني",
            "الوقائع المطروحة في السؤال تتطلب نصوصًا إضافية أو استرجاعًا أنظف قبل الجزم.",
            "",
            "- النظام أو النصوص المنطبقة",
            *citation_lines,
            "",
            "- المسائل القانونية",
            f"- {main}",
            "",
            "- التحليل",
            f"- {missing_text}",
            "- لا يجوز استكمال العناصر الناقصة بافتراضات غير ثابتة في النصوص المعروضة.",
            "",
            "- الدفوع أو الاحتمالات المقابلة",
            "- قد تتغير النتيجة إذا ظهرت نصوص إضافية أو زالت ضوضاء الاسترجاع الحالي.",
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
    case: dict[str, Any],
    visible_sources: list[dict[str, Any]],
    missing_articles: list[int],
    distractor_regs: list[str] | None = None,
) -> str:
    main, missing_text = qualified_summary(case, visible_sources, missing_articles, distractor_regs)
    citations = list_citations(visible_sources)
    regs_text = ", ".join(citations) if citations else "لا توجد نصوص كافية ظاهرة"
    return "\n".join(
        [
            "1) التكييف الأولي للقضية",
            "القضية تحتاج إلى تحليل منضبط، لكن النصوص المسترجعة الحالية لا تغطي عناصرها جميعًا بالقدر الكافي.",
            "",
            "2) الأنظمة المحتملة الانطباق",
            f"- {main}",
            "",
            "3) المسائل القانونية الأساسية",
            "- هل النصوص المعروضة تكفي أصلًا للجزم؟",
            "- ما النصوص أو المواد غير الظاهرة التي يتوقف عليها الجواب؟",
            "",
            "4) ما يدعم الطرف الأول",
            f"- النصوص الظاهرة حاليًا: {regs_text}",
            "",
            "5) ما يدعم الطرف الثاني",
            "- يمكن الدفع بأن الاسترجاع ناقص أو مشوش، فلا يصح الاستناد إلى نتيجته النهائية وحدها.",
            "",
            "6) نقاط الضعف",
            f"- {missing_text}",
            "",
            "7) ما قد يغير النتيجة",
            "- ظهور المواد الناقصة أو تنقية النصوص غير المرتبطة مباشرة بالسؤال.",
            "",
            "8) ما لم يثبته النص",
            "- لم يثبت من النصوص الحالية الجواب الكامل على جميع عناصر النزاع أو المسألة.",
            "",
            "9) التقدير الأولي",
            "- التقدير الأولي يظل مقيدًا ومؤقتًا، ويستلزم استكمال الاسترجاع قبل الجزم.",
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
                style_hints=MODE_STYLE_HINTS[mode] + ["إذا كان الاسترجاع مشوشًا أو ناقصًا فلا تجزم."],
            ),
        },
        {"role": "assistant", "content": answer},
    ]


def build_behavioral_slices(
    contexts: list[dict[str, Any]],
    case_lookup: dict[str, dict[str, Any]],
) -> tuple[list[Candidate], list[Candidate]]:
    abstain_cases: list[Candidate] = []
    noisy_cases: list[Candidate] = []

    usable_rows = [row for row in contexts if row.get("benchmark_id") in case_lookup]
    if len(usable_rows) < 20:
        raise ValueError("Expected at least 20 benchmark context rows for behavioral slices.")

    selected_rows = usable_rows[:20]
    for index, context_row in enumerate(selected_rows):
        case = case_lookup[context_row["benchmark_id"]]
        for variant_name in ("partial", "insufficient"):
            visible_sources, missing_articles = build_abstain_variant_sources(
                case=case,
                context_row=context_row,
                variant_name=variant_name,
            )
            context_text = render_context(visible_sources)
            family_signature = source_signature("abstain", variant_name, case["question"])
            for mode in ("legal_opinion", "legal_memo", "legal_analysis"):
                if mode == "legal_opinion":
                    answer = build_synthetic_opinion_answer(case=case, visible_sources=visible_sources, missing_articles=missing_articles)
                elif mode == "legal_memo":
                    answer = build_synthetic_memo_answer(case=case, visible_sources=visible_sources, missing_articles=missing_articles)
                else:
                    answer = build_synthetic_analysis_answer(case=case, visible_sources=visible_sources, missing_articles=missing_articles)
                abstain_cases.append(
                    Candidate(
                        mode=mode,
                        messages=build_synthetic_messages(
                            mode=mode,
                            question=case["question"],
                            context=context_text,
                            answer=answer,
                            evidence_sufficiency="partial" if variant_name == "partial" else "insufficient",
                        ),
                        source_label="behavioral_abstain",
                        source_split="synthetic",
                        question=case["question"],
                        signature=source_signature(family_signature, mode),
                        regulation_slug="__behavioral__",
                        assistant_chars=len(answer),
                        quality_score=0.95,
                        difficulty_tag="hard",
                        metadata={
                            "benchmark_id": context_row["benchmark_id"],
                            "case_family": "abstain",
                            "case_variant": variant_name,
                            "evidence_sufficiency": "partial" if variant_name == "partial" else "insufficient",
                            "should_abstain_or_qualify": "yes",
                            "quality_tag": "gold_behavioral",
                            "difficulty_tag": "hard",
                            "missing_expected_articles": missing_articles,
                            "visible_citations": list_citations(visible_sources),
                        },
                    )
                )

        distractor_row = selected_rows[(index + 1) % len(selected_rows)]
        visible_sources, distractor_regs, missing_articles = build_noisy_variant_sources(
            case=case,
            context_row=context_row,
            distractor_row=distractor_row,
        )
        context_text = render_context(visible_sources)
        family_signature = source_signature("noise", case["question"])
        for mode in ("legal_opinion", "legal_memo", "legal_analysis"):
            if mode == "legal_opinion":
                answer = build_synthetic_opinion_answer(
                    case=case,
                    visible_sources=visible_sources,
                    missing_articles=missing_articles,
                    distractor_regs=distractor_regs,
                )
            elif mode == "legal_memo":
                answer = build_synthetic_memo_answer(
                    case=case,
                    visible_sources=visible_sources,
                    missing_articles=missing_articles,
                    distractor_regs=distractor_regs,
                )
            else:
                answer = build_synthetic_analysis_answer(
                    case=case,
                    visible_sources=visible_sources,
                    missing_articles=missing_articles,
                    distractor_regs=distractor_regs,
                )
            noisy_cases.append(
                Candidate(
                    mode=mode,
                    messages=build_synthetic_messages(
                        mode=mode,
                        question=case["question"],
                        context=context_text,
                        answer=answer,
                        evidence_sufficiency="partial",
                    ),
                    source_label="behavioral_noise",
                    source_split="synthetic",
                    question=case["question"],
                    signature=source_signature(family_signature, mode),
                    regulation_slug="__behavioral__",
                    assistant_chars=len(answer),
                    quality_score=0.93,
                    difficulty_tag="hard",
                    metadata={
                        "benchmark_id": context_row["benchmark_id"],
                        "case_family": "noise",
                        "case_variant": "mixed_retrieval",
                        "evidence_sufficiency": "partial",
                        "should_abstain_or_qualify": "yes",
                        "quality_tag": "gold_behavioral",
                        "difficulty_tag": "hard",
                        "missing_expected_articles": missing_articles,
                        "visible_citations": list_citations(visible_sources),
                        "distractor_regulations": distractor_regs,
                    },
                )
            )

    return abstain_cases, noisy_cases


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

    for label, items in (("abstain", abstain_cases), ("noise", noisy_cases)):
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
    return {
        "dataset_version": "final_legal_modes_nV1",
        "strategy_line": "nV",
        "examples_total": len(all_rows),
        "splits": {split: len(rows) for split, rows in split_manifest_rows.items()},
        "modes_total": dict(Counter(row["mode"] for row in all_rows)),
        "source_total": dict(Counter(row["source_label"] for row in all_rows)),
        "case_groups_total": dict(Counter(row.get("case_group", "base") for row in all_rows)),
        "quality_tags_total": dict(Counter(row.get("quality_tag", "unknown") for row in all_rows)),
        "difficulty_tags_total": dict(Counter(row.get("difficulty_tag", "unknown") for row in all_rows)),
        "evidence_sufficiency_total": dict(Counter(row.get("evidence_sufficiency", "unknown") for row in all_rows)),
        "selection_policy": {
            "base_targets": TARGET_BASE_COUNTS,
            "behavioral_abstain_examples": 120,
            "behavioral_noise_examples": 60,
            "mode_tokens": MODE_TOKEN_BY_MODE,
            "raw_start": True,
            "legacy_adapter_reuse": False,
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
    parser.add_argument("--benchmark-cases", type=Path, default=BENCHMARK_CASES)
    parser.add_argument("--benchmark-contexts", type=Path, default=BENCHMARK_CONTEXTS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    base_candidates = []
    base_candidates.extend(load_source_candidates(args.seed_dir, "seed_v1"))
    base_candidates.extend(load_source_candidates(args.structured_dir, "structured_v9"))
    selected_base = pick_base_examples(base_candidates, TARGET_BASE_COUNTS)

    case_lookup = load_benchmark_case_lookup(args.benchmark_cases)
    contexts = load_benchmark_contexts(args.benchmark_contexts)
    abstain_cases, noisy_cases = build_behavioral_slices(contexts, case_lookup)

    split_rows, split_manifest_rows = build_split_payload(selected_base, abstain_cases, noisy_cases)
    write_split_artifacts(args.output_dir, split_rows, split_manifest_rows)

    summary = summarize_manifest(split_manifest_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
