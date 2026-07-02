"""Generate article-precision candidate probes with local Ollama teachers.

The teachers do not decide the gate result. They only turn supplied article
snippets into realistic legal fact patterns. Expected slug/article pairs are
anchored by the script from the local structured corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


ROOT = Path(__file__).resolve().parent.parent
STRUCTURED_BY_REGULATION_DIR = ROOT / "data" / "structured" / "by_regulation"
DEFAULT_MATRIX = ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODELS = ["qwen3.6:35b"]
TRUSTED_SINGLE_TEACHER_MODELS = {"qwen3.6:35b"}
CORE_FUNCTION_TAGS = {
    "condition",
    "obligation",
    "right",
    "procedure",
    "penalty",
    "deadline",
    "exception",
    "jurisdiction",
    "evidence",
    "liability",
}


def pair_key(slug: str, article: int) -> str:
    return f"{slug}:{article}"


def parse_pair(value: str) -> tuple[str, int] | None:
    if ":" not in str(value):
        return None
    slug, raw_article = str(value).rsplit(":", 1)
    try:
        return slug, int(raw_article)
    except Exception:
        return None


def load_matrix_pairs(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item.get("pair")) for item in data.get("article_pairs", []) if item.get("pair")}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def probe_article_pairs(probe: dict[str, Any]) -> list[str]:
    pairs = []
    for slug, articles in (probe.get("expected_articles_by_slug") or {}).items():
        for article in articles or []:
            try:
                pairs.append(pair_key(str(slug), int(article)))
            except Exception:
                continue
    return pairs


def load_autopilot_history(history_dir: Path, *, recent_files: int) -> dict[str, Any]:
    probe_files = []
    if history_dir.exists():
        probe_files = sorted(
            history_dir.glob("article_autopilot_probes_*.jsonl"),
            key=lambda item: item.stat().st_mtime,
        )
    recent_probe_files = set(probe_files[-max(0, recent_files):])
    used_pairs: set[str] = set()
    recent_pairs: set[str] = set()
    pair_counts: Counter[str] = Counter()
    slug_counts: Counter[str] = Counter()
    recent_slug_counts: Counter[str] = Counter()
    question_hashes: Counter[str] = Counter()

    for path in probe_files:
        is_recent = path in recent_probe_files
        for probe in read_jsonl(path):
            question = " ".join(str(probe.get("question") or "").split())
            if question:
                question_hashes[hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]] += 1
            for pair in probe_article_pairs(probe):
                parsed = parse_pair(pair)
                if not parsed:
                    continue
                slug, _article = parsed
                used_pairs.add(pair)
                pair_counts[pair] += 1
                slug_counts[slug] += 1
                if is_recent:
                    recent_pairs.add(pair)
                    recent_slug_counts[slug] += 1

    bank_path = history_dir / "autopilot_article_precision_bank.jsonl"
    for probe in read_jsonl(bank_path):
        for pair in probe_article_pairs(probe):
            parsed = parse_pair(pair)
            if not parsed:
                continue
            slug, _article = parsed
            used_pairs.add(pair)
            pair_counts[pair] += 1
            slug_counts[slug] += 1

    return {
        "probe_files": len(probe_files),
        "used_pairs": used_pairs,
        "recent_pairs": recent_pairs,
        "pair_counts": pair_counts,
        "slug_counts": slug_counts,
        "recent_slug_counts": recent_slug_counts,
        "unique_question_count": len(question_hashes),
    }


def load_article_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(STRUCTURED_BY_REGULATION_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metadata = data.get("metadata") or {}
        slug = str(metadata.get("slug") or path.stem)
        title = str(metadata.get("title_ar") or slug)
        for article in data.get("articles") or []:
            try:
                article_index = int(article.get("article_index") or 0)
            except Exception:
                continue
            text = str(article.get("text_for_index") or article.get("text_verbatim") or "").strip()
            if not article_index or len(text) < 80:
                continue
            rows.append(
                {
                    "slug": slug,
                    "title_ar": title,
                    "article_index": article_index,
                    "pair": pair_key(slug, article_index),
                    "citation_short_ar": article.get("citation_short_ar")
                    or f"{title}، المادة {article_index}",
                    "article_type": article.get("article_type") or "",
                    "legal_function_tags": article.get("legal_function_tags") or [],
                    "topic_tags": article.get("topic_tags") or [],
                    "text": " ".join(text.split()),
                }
            )
    return rows


def article_score(article: dict[str, Any]) -> int:
    tags = set(str(item) for item in article.get("legal_function_tags") or [])
    score = 0
    score += 8 if tags & CORE_FUNCTION_TAGS else 0
    score += 4 if article.get("article_type") not in {"definition", "intro"} else -4
    score += min(len(article.get("text", "")) // 350, 5)
    score += 2 if article.get("topic_tags") else 0
    return score


def choose_article_packets(
    articles: list[dict[str, Any]],
    covered_pairs: set[str],
    *,
    count: int,
    max_articles_per_case: int,
    seed: int,
    history: dict[str, Any] | None = None,
    max_recent_slug_count: int = 1,
    exclude_used_pairs: bool = True,
) -> list[list[dict[str, Any]]]:
    randomizer = random.Random(seed)
    history = history or {}
    used_pairs = set(history.get("used_pairs") or set())
    recent_pairs = set(history.get("recent_pairs") or set())
    pair_counts: Counter[str] = history.get("pair_counts") or Counter()
    slug_counts: Counter[str] = history.get("slug_counts") or Counter()
    recent_slug_counts: Counter[str] = history.get("recent_slug_counts") or Counter()

    def build_slug_rows(blocked_pairs: set[str]) -> list[dict[str, Any]]:
        by_slug: dict[str, list[dict[str, Any]]] = {}
        for article in articles:
            if article["pair"] in blocked_pairs:
                continue
            by_slug.setdefault(article["slug"], []).append(article)

        rows = []
        for slug, items in by_slug.items():
            scored = sorted(
                items,
                key=lambda item: (
                    pair_counts.get(item["pair"], 0),
                    -article_score(item),
                    item["article_index"],
                ),
            )
            if not scored:
                continue
            rows.append(
                {
                    "slug": slug,
                    "score": sum(article_score(item) for item in scored[:6]),
                    "items": scored,
                    "min_pair_count": int(pair_counts.get(scored[0]["pair"], 0)),
                    "total_count": int(slug_counts.get(slug, 0)),
                    "recent_count": int(recent_slug_counts.get(slug, 0)),
                }
            )
        return rows

    blocked_pairs = set(covered_pairs)
    if exclude_used_pairs:
        blocked_pairs.update(used_pairs)
    else:
        blocked_pairs.update(recent_pairs)
    slug_rows = build_slug_rows(blocked_pairs)

    if len(slug_rows) < count and exclude_used_pairs:
        blocked_pairs = set(covered_pairs) | recent_pairs
        slug_rows = build_slug_rows(blocked_pairs)

    global_min_pair_count = min((row["min_pair_count"] for row in slug_rows), default=0)
    preferred_rows = [
        row
        for row in slug_rows
        if row["min_pair_count"] == global_min_pair_count
        or row["recent_count"] <= max(0, max_recent_slug_count)
    ]
    if len(preferred_rows) >= count:
        slug_rows = preferred_rows

    randomizer.shuffle(slug_rows)
    slug_rows.sort(
        key=lambda item: (
            item["min_pair_count"],
            item["recent_count"],
            item["total_count"],
            -item["score"],
            item["slug"],
        )
    )

    packets: list[list[dict[str, Any]]] = []
    used_pairs: set[str] = set()
    used_slugs: set[str] = set()
    for row in slug_rows:
        if row["slug"] in used_slugs:
            continue
        packet = []
        for item in row["items"]:
            if item["pair"] in used_pairs:
                continue
            packet.append(item)
            used_pairs.add(item["pair"])
            if len(packet) >= max_articles_per_case:
                break
        if packet:
            packets.append(packet)
            used_slugs.add(row["slug"])
        if len(packets) >= count:
            break
    return packets


def compact_article_packet(packet: list[dict[str, Any]]) -> str:
    blocks = []
    for item in packet:
        preview = item["text"][:900]
        blocks.append(
            "\n".join(
                [
                    f"- pair: {item['pair']}",
                    f"  title: {item['title_ar']}",
                    f"  citation: {item['citation_short_ar']}",
                    f"  tags: {', '.join(item.get('legal_function_tags') or [])}",
                    f"  text: {preview}",
                ]
            )
        )
    return "\n\n".join(blocks)


def prompt_for(packet: list[dict[str, Any]]) -> str:
    allowed_pairs = [item["pair"] for item in packet]
    return f"""أنت تبني حالات اختبار لجمع مواد RAG قانوني سعودي.
المواد التالية موجودة في القاعدة. أنشئ واقعة عربية واقعية تجعل هذه المواد مطلوبة في الجمع.

قيود صارمة:
- لا تخترع slug أو رقم مادة خارج allowed_pairs.
- لا تجب على النزاع.
- لا تجعل النص طويلًا؛ المطلوب سؤال/واقعة اختبار فقط.
- أعد JSON صالحًا فقط.

allowed_pairs:
{json.dumps(allowed_pairs, ensure_ascii=False)}

article_packet:
{compact_article_packet(packet)}

الشكل المطلوب:
{{
  "domain": "snake_case_domain",
  "question": "نص واقعة قانونية عربية واحدة تتطلب جمع هذه المواد",
  "axis_name": "snake_case_axis",
  "review_note": "لماذا هذه المواد مناسبة لهذه الواقعة باختصار"
}}
"""


def parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.strip("`")
        value = value.removeprefix("json").strip()
    start = value.find("{")
    if start < 0:
        raise json.JSONDecodeError("missing JSON object", value, 0)
    parsed, _ = json.JSONDecoder().raw_decode(value[start:])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("expected JSON object", value, start)
    return parsed


def run_ollama(model: str, prompt: str, ollama_url: str, timeout: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.15},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        ollama_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    raw = str(body.get("response") or body.get("thinking") or "")
    try:
        parsed = parse_json_object(raw)
        return {"ok": True, "model": model, "parsed": parsed, "raw": raw}
    except json.JSONDecodeError as exc:
        return {"ok": False, "model": model, "error": str(exc), "raw": raw}


def safe_slug(value: str, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return candidate or fallback


def is_implementing_regulation(title: str) -> bool:
    return "لائحة" in title or "اللائحة" in title or "قواعد" in title or "ضوابط" in title


def synthetic_bank_for_packet(packet_pairs: list[str], holdout_ratio: float) -> str:
    if holdout_ratio <= 0:
        return "training"
    ratio = min(0.9, max(0.0, holdout_ratio))
    digest = hashlib.sha1("|".join(sorted(packet_pairs)).encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < ratio else "training"


def build_probe(
    packet: list[dict[str, Any]],
    teacher_outputs: list[dict[str, Any]],
    index: int,
    synthetic_bank: str,
) -> dict[str, Any]:
    valid_outputs = [item for item in teacher_outputs if item.get("ok") and isinstance(item.get("parsed"), dict)]
    selected = valid_outputs[0]["parsed"] if valid_outputs else {}
    valid_teacher_models = [str(item["model"]) for item in valid_outputs]
    if len(valid_outputs) >= 2:
        review_status = "model_agreement_ready"
    elif valid_teacher_models and set(valid_teacher_models).issubset(TRUSTED_SINGLE_TEACHER_MODELS):
        review_status = "trusted_single_teacher_ready"
    else:
        review_status = "needs_human_review"
    packet_pairs = [item["pair"] for item in packet]
    digest = hashlib.sha1("|".join(packet_pairs).encode("utf-8")).hexdigest()[:12]
    articles_by_slug: dict[str, list[int]] = {}
    titles_by_slug: dict[str, str] = {}
    for item in packet:
        articles_by_slug.setdefault(item["slug"], []).append(int(item["article_index"]))
        titles_by_slug[item["slug"]] = item["title_ar"]

    core_slugs = [slug for slug, title in titles_by_slug.items() if not is_implementing_regulation(title)]
    implementing_slugs = [slug for slug, title in titles_by_slug.items() if is_implementing_regulation(title)]
    domain = safe_slug(str(selected.get("domain") or ""), f"autopilot_article_domain_{index:03d}")
    axis = safe_slug(str(selected.get("axis_name") or ""), "generated_material_axis")
    question = str(selected.get("question") or "").strip()
    if not question:
        citations = "، ".join(item["citation_short_ar"] for item in packet)
        question = f"واقعة اختبار آلية تتطلب جمع المواد الآتية بدقة: {citations}. المطلوب جمع النصوص النظامية ذات الصلة."
    is_holdout = synthetic_bank == "holdout"

    return {
        "question_id": f"autopilot_article_precision_{digest}",
        "split": "autopilot_holdout" if is_holdout else "autopilot_train",
        "synthetic_bank": synthetic_bank,
        "holdout_locked": is_holdout,
        "support_training_allowed": not is_holdout,
        "domain": domain,
        "benchmark_category": "article_autopilot_holdout" if is_holdout else "article_autopilot_training",
        "question": question,
        "expected_core_regulations": sorted(core_slugs),
        "expected_companion_regulations": [],
        "expected_implementing_regulations": sorted(implementing_slugs),
        "expected_articles_by_slug": {slug: sorted(values) for slug, values in sorted(articles_by_slug.items())},
        "axis_article_pairs": {axis: packet_pairs},
        "min_article_recall": 1.0,
        "auto_review": {
            "status": review_status,
            "teacher_models": [item["model"] for item in teacher_outputs],
            "valid_teacher_models": valid_teacher_models,
            "review_note": str(selected.get("review_note") or ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR / "article_autopilot_candidates.jsonl")
    parser.add_argument("--probes-output", type=Path, default=DEFAULT_OUTPUT_DIR / "article_autopilot_probes.jsonl")
    parser.add_argument("--model", action="append", default=None)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--candidate-count", type=int, default=3)
    parser.add_argument("--max-articles-per-case", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260531)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--history-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recent-history-files", type=int, default=24)
    parser.add_argument("--max-recent-slug-count", type=int, default=1)
    parser.add_argument("--allow-used-pairs", action="store_true")
    parser.add_argument(
        "--holdout-ratio",
        type=float,
        default=0.20,
        help="نسبة قضايا synthetic التي تبقى holdout ولا تدخل في تدريب الدعم.",
    )
    args = parser.parse_args()

    models = args.model or DEFAULT_MODELS
    covered_pairs = load_matrix_pairs(args.matrix)
    articles = load_article_catalog()
    history = load_autopilot_history(
        args.history_dir,
        recent_files=max(0, args.recent_history_files),
    )
    packets = choose_article_packets(
        articles,
        covered_pairs,
        count=args.candidate_count,
        max_articles_per_case=max(1, args.max_articles_per_case),
        seed=args.seed,
        history=history,
        max_recent_slug_count=max(0, args.max_recent_slug_count),
        exclude_used_pairs=not args.allow_used_pairs,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.probes_output.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    for index, packet in enumerate(packets, start=1):
        prompt = prompt_for(packet)
        teacher_outputs = []
        for model in models:
            try:
                teacher_outputs.append(run_ollama(model, prompt, args.ollama_url, args.timeout))
            except Exception as exc:
                teacher_outputs.append({"ok": False, "model": model, "error": str(exc), "raw": ""})
        packet_pairs = [item["pair"] for item in packet]
        synthetic_bank = synthetic_bank_for_packet(packet_pairs, args.holdout_ratio)
        probe = build_probe(packet, teacher_outputs, index, synthetic_bank)
        probes.append(probe)
        candidates.append(
            {
                "question_id": probe["question_id"],
                "article_pairs": packet_pairs,
                "synthetic_bank": synthetic_bank,
                "support_training_allowed": probe["support_training_allowed"],
                "article_packet": [
                    {
                        "pair": item["pair"],
                        "citation_short_ar": item["citation_short_ar"],
                        "text_preview": item["text"][:420],
                    }
                    for item in packet
                ],
                "probe": probe,
                "teacher_outputs": teacher_outputs,
            }
        )

    args.output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in candidates) + ("\n" if candidates else ""),
        encoding="utf-8",
    )
    args.probes_output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in probes) + ("\n" if probes else ""),
        encoding="utf-8",
    )
    selected_pair_counts = Counter(
        int((history.get("pair_counts") or Counter()).get(item["pair"], 0))
        for packet in packets
        for item in packet
    )
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "probe_count": len(probes),
                "models": models,
                "output": str(args.output),
                "probes_output": str(args.probes_output),
                "diversity": {
                    "history_probe_files": history.get("probe_files", 0),
                    "history_used_pairs": len(history.get("used_pairs") or []),
                    "history_recent_pairs": len(history.get("recent_pairs") or []),
                    "history_unique_questions": history.get("unique_question_count", 0),
                    "selected_slugs": sorted({item["slug"] for packet in packets for item in packet}),
                    "selected_untested_pairs": int(selected_pair_counts.get(0, 0)),
                    "selected_pair_count_distribution": {
                        str(key): value for key, value in sorted(selected_pair_counts.items())
                    },
                    "exclude_used_pairs": not args.allow_used_pairs,
                    "recent_history_files": args.recent_history_files,
                    "max_recent_slug_count": args.max_recent_slug_count,
                    "holdout_ratio": args.holdout_ratio,
                    "training_probes": sum(1 for item in probes if item.get("synthetic_bank") == "training"),
                    "holdout_probes": sum(1 for item in probes if item.get("synthetic_bank") == "holdout"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
