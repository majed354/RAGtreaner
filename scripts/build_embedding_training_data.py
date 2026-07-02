"""Build Saudi legal embedding/reranker training data from gold recall cases.

The output is deliberately model-agnostic:
- query/corpus/qrels files for retrieval evaluation
- binary pair files for rerankers
- triplets for embedding/reranker contrastive training

It does not touch the production Chroma index.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v4_7000" / "gold_package_recall_7000_v4.jsonl"
DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_DENSE_REPORT = (
    ROOT
    / "data"
    / "eval"
    / "embedding_experiments"
    / "qwen3_embedding_0_6b"
    / "dense_eval_full7000_report.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "data" / "eval" / "embedding_training" / "qwen3_saudi_legal_synonyms_v1"


TOKEN_RE = re.compile(r"[\u0621-\u064A\u0660-\u0669A-Za-z0-9]+")
ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670]")
STOPWORDS = {
    "في",
    "من",
    "على",
    "عن",
    "الى",
    "إلى",
    "او",
    "أو",
    "و",
    "ثم",
    "مع",
    "ما",
    "هل",
    "كل",
    "هذه",
    "هذا",
    "ذلك",
    "تلك",
    "التي",
    "الذي",
    "الذين",
    "ذات",
    "ذو",
    "ذ",
    "م",
    "رقم",
    "سنة",
    "عام",
    "2026",
    "السعودية",
    "السعودي",
    "النظام",
    "اللائحة",
    "المادة",
    "المواد",
    "استرجع",
    "اجمع",
    "اذكر",
    "الحزمة",
    "القانونية",
    "النظامية",
    "المراجع",
}


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


def normalize(text: str) -> str:
    text = ARABIC_DIACRITICS_RE.sub("", text)
    return (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
    )


def tokens(text: str) -> set[str]:
    return {
        tok
        for tok in TOKEN_RE.findall(normalize(text))
        if len(tok) > 1 and tok not in STOPWORDS
    }


def chunk_text(row: dict[str, Any]) -> str:
    return str(row.get("index_text") or row.get("text") or row.get("text_verbatim") or "")


def compact_chunk(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "corpus_id": row["corpus_id"],
        "original_chunk_id": row.get("chunk_id"),
        "regulation_slug": row.get("regulation_slug"),
        "regulation_title_ar": row.get("regulation_title_ar"),
        "article_label": row.get("article_label"),
        "article_heading": row.get("article_heading"),
        "article_type": row.get("article_type"),
        "legal_function_tags": row.get("legal_function_tags") or [],
        "topic_tags": row.get("topic_tags") or [],
        "text": chunk_text(row),
    }


def load_chunks(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    chunks: list[dict[str, Any]] = []
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["corpus_id"] = f"{row.get('chunk_id')}::row-{index:05d}"
            row["_text_tokens"] = tokens(chunk_text(row))
            row["_title_tokens"] = tokens(str(row.get("regulation_title_ar") or ""))
            chunks.append(row)
            slug = str(row.get("regulation_slug") or "")
            if slug:
                by_slug[slug].append(row)
    return chunks, by_slug


def expected_article_bonus(case: dict[str, Any], row: dict[str, Any]) -> float:
    expected = case.get("expected_articles") or []
    if not expected:
        return 0.0
    row_slug = str(row.get("regulation_slug") or "")
    row_label = normalize(str(row.get("article_label") or row.get("article_heading") or ""))
    bonus = 0.0
    for item in expected:
        if str(item.get("regulation_slug") or "") != row_slug:
            continue
        article_label = normalize(str(item.get("article_label") or item.get("citation") or ""))
        if article_label and article_label in row_label:
            bonus = max(bonus, 5.0)
    return bonus


def score_chunk_for_case(case: dict[str, Any], row: dict[str, Any]) -> float:
    q_tokens = tokens(str(case.get("question") or ""))
    summary_tokens = tokens(str(case.get("gold_answer_summary") or ""))
    query_tokens = q_tokens | summary_tokens
    text_tokens = row.get("_text_tokens") or set()
    title_tokens = row.get("_title_tokens") or set()
    overlap = len(query_tokens & text_tokens)
    title_overlap = len(query_tokens & title_tokens)
    legal_tags = set(row.get("legal_function_tags") or [])
    topic_tags = set(row.get("topic_tags") or [])
    legal_bonus = 0.25 * len(legal_tags & {"condition", "prohibition", "obligation", "liability", "remedy", "procedure"})
    topic_bonus = 0.20 * len(topic_tags & {"liability", "enforcement", "employment", "commerce", "privacy"})
    article_bonus = expected_article_bonus(case, row)
    definition_penalty = 0.35 if str(row.get("article_type") or "") in {"definition", "general"} else 0.0
    early_penalty = 0.15 if int(row.get("article_index") or 0) == 1 and len(query_tokens) > 5 else 0.0
    return (overlap * 1.0) + (title_overlap * 0.65) + legal_bonus + topic_bonus + article_bonus - definition_penalty - early_penalty


def select_chunks_for_slug(
    case: dict[str, Any],
    by_slug: dict[str, list[dict[str, Any]]],
    slug: str,
    limit: int,
) -> list[dict[str, Any]]:
    candidates = by_slug.get(slug, [])
    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda row: score_chunk_for_case(case, row), reverse=True)
    return ranked[:limit]


def load_dense_hard_slugs(path: Path | None, k: str) -> dict[str, list[str]]:
    if not path or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = (((payload.get("evaluation") or {}).get("metrics_by_k") or {}).get(str(k)) or {})
    out: dict[str, list[str]] = {}
    for failure in metrics.get("worst_failures") or []:
        qid = str(failure.get("question_id") or "")
        slugs = [str(slug) for slug in failure.get("top_regulations") or [] if slug]
        if qid and slugs:
            out[qid] = slugs
    return out


def case_in_training_slice(case: dict[str, Any], index: int, total: int, args: argparse.Namespace) -> bool:
    if args.training_source_note:
        return str(case.get("source_note") or "") == args.training_source_note
    if args.training_final_n:
        return index >= max(0, total - args.training_final_n)
    return True


def build_dataset(args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(args.seed)
    cases = load_jsonl(args.cases)
    chunks, by_slug = load_chunks(args.chunks)
    dense_hard_slugs = load_dense_hard_slugs(args.dense_report, args.dense_report_k)

    selected_corpus: dict[str, dict[str, Any]] = {}
    queries: list[dict[str, Any]] = []
    qrels_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pairs_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    triplets_by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)

    all_slugs = sorted(by_slug)
    missing_slug_counts: Counter[str] = Counter()

    training_candidate_cases = 0
    pair_training_cases = 0
    training_source_notes: Counter[str] = Counter()
    training_split_counts: Counter[str] = Counter()

    for case_index, case in enumerate(cases):
        qid = str(case.get("question_id") or "")
        split = str(case.get("split") or "train")
        question = str(case.get("question") or "")
        in_training_slice = case_in_training_slice(case, case_index, len(cases), args)
        build_training_pairs = in_training_slice and split in set(args.training_pair_splits)
        if in_training_slice:
            training_candidate_cases += 1
            training_source_notes[str(case.get("source_note") or "")] += 1
            training_split_counts[split] += 1
        if build_training_pairs:
            pair_training_cases += 1
        core = [str(slug) for slug in case.get("required_core_regulations") or []]
        companions = [str(slug) for slug in case.get("required_companion_regulations") or []]
        optional = [str(slug) for slug in case.get("optional_regulations") or []]
        excluded = [str(slug) for slug in case.get("excluded_regulations") or []]
        required = list(dict.fromkeys(core + companions))
        allowed = set(required + optional + [str(slug) for slug in case.get("allowed_regulations") or []])

        queries.append(
            {
                "query_id": qid,
                "question": question,
                "split": split,
                "domain": case.get("domain"),
                "scenario_family_id": case.get("scenario_family_id"),
                "required_core_regulations": core,
                "required_companion_regulations": companions,
                "optional_regulations": optional,
                "excluded_regulations": excluded,
            }
        )

        positive_rows: list[dict[str, Any]] = []
        for slug in required:
            selected = select_chunks_for_slug(case, by_slug, slug, args.positive_chunks_per_slug)
            if not selected:
                missing_slug_counts[slug] += 1
            for row in selected:
                role = "core" if slug in core else "companion"
                selected_corpus[row["corpus_id"]] = compact_chunk(row)
                positive_rows.append(row)
                qrels_by_split[split].append(
                    {
                        "query_id": qid,
                        "corpus_id": row["corpus_id"],
                        "score": 2,
                        "role": role,
                        "regulation_slug": slug,
                    }
                )
                if build_training_pairs:
                    pairs_by_split[split].append(
                        {
                            "query_id": qid,
                            "corpus_id": row["corpus_id"],
                            "label": 1,
                            "role": role,
                            "regulation_slug": slug,
                            "query": question,
                            "passage": chunk_text(row),
                        }
                    )

        negative_rows: list[dict[str, Any]] = []
        negative_slugs: list[str] = []
        negative_slugs.extend(excluded)
        for slug in dense_hard_slugs.get(qid, []):
            if slug not in allowed and slug not in negative_slugs:
                negative_slugs.append(slug)
            if len(negative_slugs) >= len(excluded) + args.dense_hard_negative_slugs:
                break
        random_pool = [slug for slug in all_slugs if slug not in allowed and slug not in negative_slugs]
        rng.shuffle(random_pool)
        negative_slugs.extend(random_pool[: args.random_negative_slugs])

        for slug in negative_slugs:
            selected = select_chunks_for_slug(case, by_slug, slug, args.negative_chunks_per_slug)
            for row in selected:
                selected_corpus[row["corpus_id"]] = compact_chunk(row)
                negative_rows.append(row)
                qrels_by_split[split].append(
                    {
                        "query_id": qid,
                        "corpus_id": row["corpus_id"],
                        "score": 0,
                        "role": "negative",
                        "regulation_slug": slug,
                    }
                )
                if build_training_pairs:
                    pairs_by_split[split].append(
                        {
                            "query_id": qid,
                            "corpus_id": row["corpus_id"],
                            "label": 0,
                            "role": "negative",
                            "regulation_slug": slug,
                            "query": question,
                            "passage": chunk_text(row),
                        }
                    )

        if build_training_pairs and positive_rows and negative_rows:
            for positive in positive_rows:
                sampled_negatives = rng.sample(
                    negative_rows,
                    k=min(args.triplets_per_positive, len(negative_rows)),
                )
                for negative in sampled_negatives:
                    triplets_by_split[split].append(
                        {
                            "query_id": qid,
                            "positive_corpus_id": positive["corpus_id"],
                            "negative_corpus_id": negative["corpus_id"],
                            "positive_slug": positive.get("regulation_slug"),
                            "negative_slug": negative.get("regulation_slug"),
                            "query": question,
                            "positive": chunk_text(positive),
                            "negative": chunk_text(negative),
                        }
                    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "queries.jsonl", queries)
    write_jsonl(args.output_dir / "corpus_selected.jsonl", list(selected_corpus.values()))

    for split, rows in qrels_by_split.items():
        with (args.output_dir / f"qrels_{split}.tsv").open("w", encoding="utf-8") as handle:
            handle.write("query_id\tcorpus_id\tscore\trole\tregulation_slug\n")
            for row in rows:
                handle.write(
                    f"{row['query_id']}\t{row['corpus_id']}\t{row['score']}\t{row['role']}\t{row['regulation_slug']}\n"
                )
    for split, rows in pairs_by_split.items():
        write_jsonl(args.output_dir / f"pairs_{split}.jsonl", rows)
    for split, rows in triplets_by_split.items():
        write_jsonl(args.output_dir / f"triplets_{split}.jsonl", rows)

    manifest = {
        "dataset": args.dataset_name,
        "cases_path": str(args.cases),
        "chunks_path": str(args.chunks),
        "dense_report_path": str(args.dense_report) if args.dense_report else None,
        "all_eval_cases": len(cases),
        "training_candidate_cases": training_candidate_cases,
        "pair_training_cases": pair_training_cases,
        "training_source_notes": dict(training_source_notes),
        "training_split_counts": dict(training_split_counts),
        "selected_corpus_chunks": len(selected_corpus),
        "splits": {
            split: {
                "qrels": len(qrels_by_split.get(split, [])),
                "pairs": len(pairs_by_split.get(split, [])),
                "triplets": len(triplets_by_split.get(split, [])),
            }
            for split in sorted({*qrels_by_split, *pairs_by_split, *triplets_by_split})
        },
        "params": {
            "positive_chunks_per_slug": args.positive_chunks_per_slug,
            "negative_chunks_per_slug": args.negative_chunks_per_slug,
            "dense_hard_negative_slugs": args.dense_hard_negative_slugs,
            "random_negative_slugs": args.random_negative_slugs,
            "triplets_per_positive": args.triplets_per_positive,
            "dense_report_k": args.dense_report_k,
            "training_source_note": args.training_source_note,
            "training_final_n": args.training_final_n,
            "training_pair_splits": args.training_pair_splits,
            "seed": args.seed,
        },
        "missing_required_slugs": missing_slug_counts.most_common(),
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--dense-report", type=Path, default=DEFAULT_DENSE_REPORT)
    parser.add_argument("--dense-report-k", default="180")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dataset-name", default="qwen3_saudi_legal_synonyms_v1")
    parser.add_argument(
        "--training-source-note",
        default="synonym_surface_stress_v4",
        help="Default targets the final 1000 synonym/colloquial cases.",
    )
    parser.add_argument(
        "--training-final-n",
        type=int,
        default=0,
        help="Fallback slice when --training-source-note is empty.",
    )
    parser.add_argument(
        "--training-pair-splits",
        nargs="+",
        default=["dev", "regression"],
        help="Build trainable pairs/triplets only for these splits; keep heldout for honest evaluation.",
    )
    parser.add_argument("--positive-chunks-per-slug", type=int, default=4)
    parser.add_argument("--negative-chunks-per-slug", type=int, default=2)
    parser.add_argument("--dense-hard-negative-slugs", type=int, default=4)
    parser.add_argument("--random-negative-slugs", type=int, default=2)
    parser.add_argument("--triplets-per-positive", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260521)
    return parser.parse_args()


def main() -> None:
    manifest = build_dataset(parse_args())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
