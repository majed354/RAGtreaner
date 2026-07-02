"""Build and evaluate an isolated dense-retrieval embedding experiment.

This script is intentionally outside the production RAG path. It builds a
separate Chroma collection for a candidate embedding model, then evaluates
package recall against the frozen gold benchmark using dense retrieval only.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import chromadb
import requests
from tqdm import tqdm


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNKS = ROOT / "data" / "structured" / "chunks.jsonl"
DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v4_7000" / "gold_package_recall_7000_v4.jsonl"
DEFAULT_OUT_DIR = ROOT / "data" / "eval" / "embedding_experiments" / "qwen3_embedding_0_6b"
DEFAULT_PERSIST_DIR = DEFAULT_OUT_DIR / "chromadb"
DEFAULT_OUTPUT = DEFAULT_OUT_DIR / "dense_eval_report.json"


def load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def primitive_metadata(row: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    allowed = {
        "chunk_id",
        "regulation_slug",
        "regulation_title_ar",
        "article_label",
        "article_heading",
        "article_type",
        "article_type_label_ar",
        "citation_short_ar",
        "article_index",
        "chunk_index",
        "version_status",
        "version_status_label_ar",
        "official_source_url_primary",
    }
    out: dict[str, str | int | float | bool | None] = {}
    for key in allowed:
        value = row.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    for key in ("legal_function_tags", "topic_tags", "official_source_urls"):
        value = row.get(key)
        if isinstance(value, list):
            out[key] = "|".join(str(item) for item in value)
    return out


class OllamaEmbedder:
    def __init__(self, model: str, base_url: str, timeout: int = 240) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.session.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError(f"Unexpected Ollama embedding response for batch size {len(texts)}")
        return embeddings


def build_collection(args: argparse.Namespace) -> dict[str, Any]:
    chunks = load_jsonl(args.chunks, limit=args.max_chunks)
    expected_count = len(chunks)
    args.persist_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(args.persist_dir))
    existing_names = {
        item if isinstance(item, str) else item.name
        for item in client.list_collections()
    }
    if args.collection_name in existing_names and args.force_rebuild:
        client.delete_collection(args.collection_name)
        existing_names.remove(args.collection_name)

    collection = client.get_or_create_collection(
        args.collection_name,
        metadata={"hnsw:space": "cosine", "embedding_model": args.model},
    )
    current_count = collection.count()
    if current_count == expected_count and not args.force_rebuild:
        return {
            "status": "skipped_existing_complete",
            "collection_count": current_count,
            "expected_count": expected_count,
        }
    if current_count and current_count != expected_count:
        if not args.force_rebuild:
            raise RuntimeError(
                f"Collection has {current_count} rows, expected {expected_count}. "
                "Pass --force-rebuild to rebuild it."
            )
        client.delete_collection(args.collection_name)
        collection = client.create_collection(
            args.collection_name,
            metadata={"hnsw:space": "cosine", "embedding_model": args.model},
        )

    embedder = OllamaEmbedder(args.model, args.ollama_base_url, timeout=args.timeout)
    first_vector = embedder.embed(["اختبار أبعاد نموذج التضمين"])[0]
    dimension = len(first_vector)

    started = time.time()
    for offset in tqdm(range(0, expected_count, args.batch_size), desc="embedding chunks"):
        batch = chunks[offset : offset + args.batch_size]
        texts = [str(row.get("index_text") or row.get("text") or "") for row in batch]
        embeddings = embedder.embed(texts)
        ids = [f"{row.get('chunk_id')}::row-{offset + index + 1:05d}" for index, row in enumerate(batch)]
        metadatas = [primitive_metadata(row) for row in batch]
        collection.add(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)

    return {
        "status": "built",
        "collection_count": collection.count(),
        "expected_count": expected_count,
        "embedding_dimension": dimension,
        "elapsed_seconds": round(time.time() - started, 3),
    }


def slug_first_ranks(metadatas: list[dict[str, Any]]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for index, metadata in enumerate(metadatas, start=1):
        slug = str(metadata.get("regulation_slug") or "").strip()
        if slug and slug not in ranks:
            ranks[slug] = index
    return ranks


def safe_div(num: float, den: float) -> float:
    return num / den if den else 1.0


def evaluate_collection(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.cases)
    if args.split:
        cases = [case for case in cases if case.get("split") == args.split]
    if args.source_note:
        cases = [case for case in cases if case.get("source_note") == args.source_note]
    if args.question_id_min or args.question_id_max:
        filtered_cases = []
        for case in cases:
            qid = str(case.get("question_id") or "")
            try:
                numeric_id = int(qid.rsplit("_", 1)[-1])
            except ValueError:
                filtered_cases.append(case)
                continue
            if args.question_id_min and numeric_id < args.question_id_min:
                continue
            if args.question_id_max and numeric_id > args.question_id_max:
                continue
            filtered_cases.append(case)
        cases = filtered_cases
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise RuntimeError("No cases selected for evaluation.")

    client = chromadb.PersistentClient(path=str(args.persist_dir))
    collection = client.get_collection(args.collection_name)
    embedder = OllamaEmbedder(args.model, args.ollama_base_url, timeout=args.timeout)

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
    for case in tqdm(cases, desc="evaluating questions"):
        question = str(case.get("question") or "")
        query_embedding = embedder.embed([question])[0]
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=max_k,
            include=["metadatas", "distances"],
        )
        metadatas = list(result.get("metadatas", [[]])[0])
        ranks = slug_first_ranks(metadatas)

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

    summarized: dict[str, Any] = {}
    for k, bucket in stats_by_k.items():
        ranks = bucket.pop("required_ranks")
        summarized[str(k)] = {
            "core_recall": round(safe_div(bucket["core_hits"], bucket["core_total"]), 6),
            "companion_recall": round(safe_div(bucket["companion_hits"], bucket["companion_total"]), 6),
            "full_package_rate": round(bucket["full_package_cases"] / len(cases), 6),
            "fatal_core_miss_cases": bucket["fatal_core_miss_cases"],
            "excluded_hit_cases": bucket["excluded_hit_cases"],
            "median_required_rank": statistics.median(ranks) if ranks else None,
            "mean_required_rank": round(statistics.mean(ranks), 3) if ranks else None,
            "worst_failures": bucket["worst_failures"],
        }

    return {
        "status": "ok",
        "model": args.model,
        "collection": args.collection_name,
        "collection_count": collection.count(),
        "cases_path": str(args.cases),
        "cases_evaluated": len(cases),
        "split": args.split or "all",
        "source_note": args.source_note or "all",
        "question_id_min": args.question_id_min,
        "question_id_max": args.question_id_max,
        "k_values": k_values,
        "elapsed_seconds": round(time.time() - started, 3),
        "metrics_by_k": summarized,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated dense embedding experiment for Saudi legal RAG.")
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--persist-dir", type=Path, default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--collection-name", default="qwen3_embedding_0_6b_saudi_legal_chunks")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-chunks", type=int, default=0)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--split", default="")
    parser.add_argument("--source-note", default="")
    parser.add_argument("--question-id-min", type=int, default=0)
    parser.add_argument("--question-id-max", type=int, default=0)
    parser.add_argument("--k-values", type=int, nargs="+", default=[24, 42, 90, 180])
    parser.add_argument("--failure-samples", type=int, default=20)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()
    args.max_chunks = args.max_chunks or None
    args.max_cases = args.max_cases or None
    if not args.build and not args.evaluate:
        args.build = True
        args.evaluate = True
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if any(k <= 0 for k in args.k_values):
        raise SystemExit("--k-values must be positive")
    return args


def main() -> None:
    args = parse_args()
    report: dict[str, Any] = {
        "experiment": "isolated_dense_embedding_candidate",
        "model": args.model,
        "created_at_unix": math.floor(time.time()),
        "production_index_touched": False,
        "build": None,
        "evaluation": None,
    }
    if args.build:
        report["build"] = build_collection(args)
    if args.evaluate:
        report["evaluation"] = evaluate_collection(args)
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
