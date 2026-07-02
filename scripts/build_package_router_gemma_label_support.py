"""Use local Gemma to generate user-like training questions for rare package labels."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"
DEFAULT_TRAIN = DEFAULT_DATASET_DIR / "train.jsonl"
DEFAULT_CATALOG = DEFAULT_DATASET_DIR / "label_catalog.json"
DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_OUTPUT = DEFAULT_DATASET_DIR / "gemma_rare_label_support_train.jsonl"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
QUESTION_LINE_RE = re.compile(r"^(?:سؤال|Q)\s*[:：]\s*(.+)$", flags=re.IGNORECASE)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def compact(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def load_titles(path: Path) -> dict[str, str]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["slug"]): str(item.get("title_ar") or item["slug"]) for item in items}


def load_chunk_excerpts(path: Path, wanted: set[str], per_label: int) -> dict[str, list[str]]:
    excerpts: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            slug = str(row.get("regulation_slug") or "")
            text = compact(str(row.get("text") or ""), 520)
            if slug in wanted and text and len(excerpts[slug]) < per_label:
                excerpts[slug].append(text)
    return excerpts


def rare_labels(rows: list[dict[str, Any]], threshold: int, limit: int) -> list[str]:
    counts = Counter(str(label) for row in rows for label in row.get("all_labels") or [])
    labels = [label for label, count in sorted(counts.items(), key=lambda item: (item[1], item[0])) if count < threshold]
    return labels[:limit] if limit else labels


def example_questions(rows: list[dict[str, Any]], labels: set[str], per_label: int) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        question = compact(str(row.get("question") or ""), 420)
        if not question:
            continue
        for label in row.get("all_labels") or []:
            slug = str(label)
            if slug in labels and len(examples[slug]) < per_label:
                examples[slug].append(question)
    return examples


def prompt_for(slug: str, title: str, examples: list[str], excerpts: list[str], count: int) -> str:
    example_block = "\n".join(f"- {item}" for item in examples) or "- لا توجد أمثلة كافية."
    excerpt_block = "\n".join(f"- {item}" for item in excerpts) or "- لا يوجد مقتطف موجز."
    return f"""أنت مولد بيانات تدريب لراوتر حزم في RAG قانوني سعودي.
اكتب {count} أسئلة مستخدم واقعية متنوعة تدل على الموضوع النظامي التالي دون أن تذكر اسم النظام أو slug.
لا تجب عن السؤال ولا تذكر المرجع القانوني. اجعل كل سؤال يصف وقائع أو طلب جمع نصوص.
اكتب كل سؤال في سطر واحد فقط بهذا الشكل:
سؤال: ...

slug الداخلي: {slug}
عنوان المرجع للاسترشاد فقط: {title}

أمثلة تدريب موجودة:
{example_block}

مقتطفات موجزة من النصوص:
{excerpt_block}
"""


def generate_text(args: argparse.Namespace, prompt: str) -> str:
    payload = json.dumps(
        {
            "model": args.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": args.temperature},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        args.ollama_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=args.timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return str(body.get("response") or "")


def parse_questions(text: str, title: str, limit: int) -> list[str]:
    questions: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-*0123456789. ").strip()
        match = QUESTION_LINE_RE.match(line)
        candidate = compact(match.group(1) if match else "", 900)
        if len(candidate) < 24 or title in candidate or candidate in questions:
            continue
        questions.append(candidate)
        if len(questions) >= limit:
            break
    return questions


def support_row(index: int, slug: str, title: str, question: str, raw_output: str) -> dict[str, Any]:
    return {
        "question_id": f"router_gemma_support_v1_{index:05d}",
        "question": question,
        "split": "train",
        "router_role": "train_gemma_label_support",
        "domain": "router_gemma_label_support",
        "benchmark_category": "rare_label_semantic_support",
        "scenario_family_id": f"gemma_support::{slug}",
        "source_note": "package_router_gemma_rare_label_support_v1",
        "generated_for_slug": slug,
        "generated_for_title": title,
        "generator_raw_output": raw_output,
        "core_labels": [slug],
        "companion_labels": [],
        "all_labels": [slug],
        "optional_labels": [],
        "excluded_labels": [],
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    train_rows = load_jsonl(args.train)
    titles = load_titles(args.catalog)
    labels = list(dict.fromkeys(args.only_labels)) if args.only_labels else rare_labels(
        train_rows,
        args.support_threshold,
        args.label_limit,
    )
    examples = example_questions(train_rows, set(labels), args.examples_per_label)
    excerpts = load_chunk_excerpts(args.chunks, set(labels), args.excerpts_per_label)

    out: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for slug in labels:
        title = titles.get(slug, slug)
        raw_output = generate_text(args, prompt_for(slug, title, examples[slug], excerpts[slug], args.questions_per_label))
        questions = parse_questions(raw_output, title, args.questions_per_label)
        if not questions:
            failures.append({"slug": slug, "title": title, "raw_output": raw_output})
            continue
        for question in questions:
            out.append(support_row(len(out) + 1, slug, title, question, raw_output))
        print(json.dumps({"slug": slug, "generated": len(questions)}, ensure_ascii=False))

    write_jsonl(args.output, out)
    (args.output.with_suffix(".failures.json")).write_text(
        json.dumps(failures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "model": args.model,
        "labels_requested": len(labels),
        "labels_generated": len({row["generated_for_slug"] for row in out}),
        "rows_generated": len(out),
        "failures": len(failures),
        "support_threshold": args.support_threshold,
        "questions_per_label": args.questions_per_label,
        "output": str(args.output),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="gemma4:31b")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--support-threshold", type=int, default=15)
    parser.add_argument("--label-limit", type=int, default=0)
    parser.add_argument("--only-labels", nargs="*", default=[])
    parser.add_argument("--questions-per-label", type=int, default=4)
    parser.add_argument("--examples-per-label", type=int, default=3)
    parser.add_argument("--excerpts-per-label", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--temperature", type=float, default=0.3)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
