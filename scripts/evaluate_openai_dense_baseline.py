"""Evaluate the production OpenAI dense index on gold package recall cases.

This is read-only against the production Chroma collection: it embeds queries
with the configured OpenAI embedding model and queries the existing collection.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import chromadb
from langchain_openai import OpenAIEmbeddings
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.rag.ingest import get_embedding_api_key


DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v4_7000" / "gold_package_recall_7000_v4.jsonl"
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "eval"
    / "embedding_experiments"
    / "openai_text_embedding_3_small"
    / "dense_eval_synonym_heldout375_report.json"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def slug_first_ranks(metadatas: list[dict[str, Any]]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for index, metadata in enumerate(metadatas, start=1):
        slug = str(metadata.get("regulation_slug") or "").strip()
        if slug and slug not in ranks:
            ranks[slug] = index
    return ranks


def safe_div(num: float, den: float) -> float:
    return num / den if den else 1.0


def select_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_jsonl(args.cases)
    if args.split:
        cases = [case for case in cases if case.get("split") == args.split]
    if args.source_note:
        cases = [case for case in cases if case.get("source_note") == args.source_note]
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise RuntimeError("No cases selected for evaluation.")
    return cases


def summarize(stats_by_k: dict[int, dict[str, Any]], case_count: int) -> dict[str, Any]:
    summarized: dict[str, Any] = {}
    for k, bucket in stats_by_k.items():
        ranks = bucket.pop("required_ranks")
        summarized[str(k)] = {
            "core_recall": round(safe_div(bucket["core_hits"], bucket["core_total"]), 6),
            "companion_recall": round(safe_div(bucket["companion_hits"], bucket["companion_total"]), 6),
            "full_package_rate": round(bucket["full_package_cases"] / case_count, 6),
            "fatal_core_miss_cases": bucket["fatal_core_miss_cases"],
            "excluded_hit_cases": bucket["excluded_hit_cases"],
            "median_required_rank": statistics.median(ranks) if ranks else None,
            "mean_required_rank": round(statistics.mean(ranks), 3) if ranks else None,
            "worst_failures": bucket["worst_failures"],
        }
    return summarized


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    cases = select_cases(args)
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_collection(settings.chroma_collection)
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=get_embedding_api_key(),
        chunk_size=settings.embedding_batch_size,
    )

    k_values = sorted(set(args.k_values))
    max_k = max(k_values)
    stats_by_k: dict[int, dict[str, Any]] = {
        k: {
            "core_hits": 0,
            "core_total": 0,
            "companion_hits": 0,
            "companion_total": 0,
            "full_package_cases": 0,
            "fatal_core_miss_cases": 0,
            "excluded_hit_cases": 0,
            "required_ranks": [],
            "worst_failures": [],
        }
        for k in k_values
    }

    started = time.time()
    for offset in tqdm(range(0, len(cases), args.batch_size), desc="evaluating openai dense"):
        batch = cases[offset : offset + args.batch_size]
        query_embeddings = embeddings.embed_documents([str(case.get("question") or "") for case in batch])
        result = collection.query(
            query_embeddings=query_embeddings,
            n_results=max_k,
            include=["metadatas", "distances"],
        )
        batch_metadatas = result.get("metadatas") or []
        for case, metadatas in zip(batch, batch_metadatas):
            ranks = slug_first_ranks(list(metadatas))
            core = [str(item) for item in case.get("required_core_regulations") or []]
            companions = [str(item) for item in case.get("required_companion_regulations") or []]
            excluded = set(str(item) for item in case.get("excluded_regulations") or [])
            for k in k_values:
                top_slugs = {slug for slug, rank in ranks.items() if rank <= k}
                required = core + companions
                missing_core = [slug for slug in core if slug not in top_slugs]
                missing_companions = [slug for slug in companions if slug not in top_slugs]
                present_required_ranks = [ranks[slug] for slug in required if slug in ranks and ranks[slug] <= k]

                bucket = stats_by_k[k]
                bucket["core_total"] += len(core)
                bucket["core_hits"] += len(core) - len(missing_core)
                bucket["companion_total"] += len(companions)
                bucket["companion_hits"] += len(companions) - len(missing_companions)
                bucket["required_ranks"].extend(present_required_ranks)
                if not missing_core and not missing_companions:
                    bucket["full_package_cases"] += 1
                if missing_core:
                    bucket["fatal_core_miss_cases"] += 1
                if excluded.intersection(top_slugs):
                    bucket["excluded_hit_cases"] += 1
                if (missing_core or missing_companions) and len(bucket["worst_failures"]) < args.failure_samples:
                    bucket["worst_failures"].append(
                        {
                            "question_id": case.get("question_id"),
                            "domain": case.get("domain"),
                            "scenario_family_id": case.get("scenario_family_id"),
                            "missing_core": missing_core,
                            "missing_companions": missing_companions,
                            "top_regulations": list(ranks.keys())[:12],
                        }
                    )

    return {
        "experiment": "production_openai_dense_baseline",
        "production_index_touched": False,
        "evaluation": {
            "status": "ok",
            "model": settings.embedding_model,
            "collection": settings.chroma_collection,
            "collection_count": collection.count(),
            "cases_path": str(args.cases),
            "cases_evaluated": len(cases),
            "split": args.split or "all",
            "source_note": args.source_note or "all",
            "k_values": k_values,
            "elapsed_seconds": round(time.time() - started, 3),
            "metrics_by_k": summarize(stats_by_k, len(cases)),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", default="")
    parser.add_argument("--source-note", default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--k-values", type=int, nargs="+", default=[24, 42, 90, 180])
    parser.add_argument("--failure-samples", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
