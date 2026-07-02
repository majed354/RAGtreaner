"""Evaluate an isolated embedding model inside the local hybrid recall stack.

The production service remains untouched. This script reuses the local legal
query analyzer and lexical scorer, but points the dense branch at a separate
Chroma index built for a candidate embedding model.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import chromadb
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.engine import LegalRAGEngine
from scripts.run_embedding_dense_experiment import OllamaEmbedder, load_jsonl


DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v4_7000" / "gold_package_recall_7000_v4.jsonl"
DEFAULT_OUT_DIR = ROOT / "data" / "eval" / "embedding_experiments" / "qwen3_embedding_8b"
DEFAULT_PERSIST_DIR = DEFAULT_OUT_DIR / "chromadb"
DEFAULT_OUTPUT = DEFAULT_OUT_DIR / "hybrid_jamia_synonym_heldout375_report.json"


def safe_div(num: float, den: float) -> float:
    return num / den if den else 1.0


def slug_first_ranks_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for index, candidate in enumerate(candidates, start=1):
        entry = candidate.get("entry") or {}
        slug = str(entry.get("regulation_slug") or "").strip()
        if slug and slug not in ranks:
            ranks[slug] = index
    return ranks


def summarize_bucket(bucket: dict[str, Any], case_count: int) -> dict[str, Any]:
    ranks = bucket.pop("required_ranks")
    return {
        "core_recall": round(safe_div(bucket["core_hits"], bucket["core_total"]), 6),
        "companion_recall": round(safe_div(bucket["companion_hits"], bucket["companion_total"]), 6),
        "full_package_rate": round(bucket["full_package_cases"] / case_count, 6),
        "fatal_core_miss_cases": bucket["fatal_core_miss_cases"],
        "excluded_hit_cases": bucket["excluded_hit_cases"],
        "median_required_rank": statistics.median(ranks) if ranks else None,
        "mean_required_rank": round(statistics.mean(ranks), 3) if ranks else None,
        "worst_failures": bucket["worst_failures"],
    }


def update_bucket(
    bucket: dict[str, Any],
    case: dict[str, Any],
    ranks: dict[str, int],
    top_slugs: set[str],
    failure_samples: int,
) -> None:
    core = [str(item) for item in case.get("required_core_regulations") or []]
    companions = [str(item) for item in case.get("required_companion_regulations") or []]
    excluded = set(str(item) for item in case.get("excluded_regulations") or [])
    required = core + companions

    missing_core = [slug for slug in core if slug not in top_slugs]
    missing_companions = [slug for slug in companions if slug not in top_slugs]
    present_required_ranks = [ranks[slug] for slug in required if slug in ranks]

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
    if (missing_core or missing_companions) and len(bucket["worst_failures"]) < failure_samples:
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


def new_bucket() -> dict[str, Any]:
    return {
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


class IsolatedHybridEvaluator:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.engine = LegalRAGEngine()
        self.client = chromadb.PersistentClient(path=str(args.persist_dir))
        self.collection = self.client.get_collection(args.collection_name)
        if not self.engine.get_structured_chunk_count():
            self._load_engine_entries_from_candidate_collection()
        self.embedder = OllamaEmbedder(args.model, args.ollama_base_url, timeout=args.timeout)

    def _load_engine_entries_from_candidate_collection(self) -> None:
        """Fallback lexical corpus when data/structured/chunks.jsonl is absent."""
        self.engine._entries = []
        self.engine._entry_by_chunk_id = {}
        self.engine._entries_by_slug = defaultdict(list)
        self.engine._entries_by_article = defaultdict(list)
        total = self.collection.count()
        batch_size = 1000
        for offset in range(0, total, batch_size):
            batch = self.collection.get(
                include=["documents", "metadatas"],
                limit=batch_size,
                offset=offset,
            )
            ids = batch.get("ids") or []
            documents = batch.get("documents") or []
            metadatas = batch.get("metadatas") or []
            for index, item_id in enumerate(ids):
                document = documents[index] if index < len(documents) else ""
                metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
                entry = self.engine._entry_from_chroma(str(item_id), document, metadata)
                slug = str(entry.get("regulation_slug") or "")
                article = int(entry.get("article_index") or 0)
                title = str(entry.get("regulation_title_ar") or "")
                chunk_id = str(entry.get("chunk_id") or item_id)
                self.engine._entries.append(entry)
                self.engine._entry_by_chunk_id[str(item_id)] = entry
                self.engine._entry_by_chunk_id[chunk_id] = entry
                if slug:
                    self.engine._entries_by_slug[slug].append(entry)
                    if title:
                        self.engine._title_by_slug.setdefault(slug, title)
                if slug and article:
                    self.engine._entries_by_article[(slug, article)].append(entry)

    def dense_candidates(self, question: str, query_data: dict[str, Any]) -> list[dict[str, Any]]:
        n_results = int(query_data["retrieval_profile_config"].get("dense_k") or self.args.max_dense_k)
        embedding = self.embedder.embed([question])[0]
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        candidates: list[dict[str, Any]] = []
        for rank, item_id in enumerate(ids, start=1):
            distance = float(distances[rank - 1]) if rank - 1 < len(distances) else 1.0
            score = max(0.0, 1.0 - distance)
            metadata = metadatas[rank - 1] if rank - 1 < len(metadatas) and isinstance(metadatas[rank - 1], dict) else {}
            document = documents[rank - 1] if rank - 1 < len(documents) else ""
            entry = self.engine._entry_from_chroma(str(item_id), document, metadata)
            candidates.append(self.engine._make_candidate(entry, dense_score=score, dense_rank=rank))
        return candidates

    def evaluate(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        k_values = sorted(set(self.args.k_values))
        ranked_buckets = {k: new_bucket() for k in k_values}
        selected_bucket = new_bucket()
        analyzer_required_bucket = new_bucket()

        started = time.time()
        for case in tqdm(cases, desc="evaluating hybrid questions"):
            question = str(case.get("question") or "")
            query_data = self.engine._analyze_query(question, self.args.retrieval_profile)
            query_data["retrieval_profile_config"]["dense_norm_weight"] = self.args.dense_weight
            query_data["retrieval_profile_config"]["lexical_norm_weight"] = self.args.lexical_weight

            dense = self.dense_candidates(question, query_data)
            lexical = self.engine._lexical_candidates(query_data)
            forced = self.engine._forced_required_candidates(query_data) if self.args.include_forced else []
            ranked = self.engine._merge_and_rank([*forced, *dense, *lexical], query_data)
            selected = self.engine._select_context(ranked, query_data)

            ranked_ranks = slug_first_ranks_from_candidates(ranked)
            selected_ranks = slug_first_ranks_from_candidates(selected)
            analyzer_slugs = {
                *[str(item) for item in query_data.get("required_core_regulations") or []],
                *[str(item) for item in query_data.get("required_companion_regulations") or []],
            }
            analyzer_ranks = {slug: index for index, slug in enumerate(analyzer_slugs, start=1)}

            for k in k_values:
                top_slugs = {slug for slug, rank in ranked_ranks.items() if rank <= k}
                limited_ranks = {slug: rank for slug, rank in ranked_ranks.items() if rank <= k}
                update_bucket(ranked_buckets[k], case, limited_ranks, top_slugs, self.args.failure_samples)

            selected_top_slugs = set(selected_ranks)
            update_bucket(selected_bucket, case, selected_ranks, selected_top_slugs, self.args.failure_samples)
            update_bucket(analyzer_required_bucket, case, analyzer_ranks, analyzer_slugs, self.args.failure_samples)

        return {
            "status": "ok",
            "model": self.args.model,
            "collection": self.args.collection_name,
            "collection_count": self.collection.count(),
            "retrieval_profile": self.args.retrieval_profile,
            "dense_weight": self.args.dense_weight,
            "lexical_weight": self.args.lexical_weight,
            "include_forced": self.args.include_forced,
            "selected_context_limit": self.engine._profile_config(self.args.retrieval_profile)[1].get("context_limit"),
            "cases_evaluated": len(cases),
            "elapsed_seconds": round(time.time() - started, 3),
            "ranked_metrics_by_k": {
                str(k): summarize_bucket(bucket, len(cases))
                for k, bucket in ranked_buckets.items()
            },
            "selected_context_metrics": summarize_bucket(selected_bucket, len(cases)),
            "analyzer_required_only_metrics": summarize_bucket(analyzer_required_bucket, len(cases)),
        }


def select_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_jsonl(args.cases)
    if args.split:
        cases = [case for case in cases if case.get("split") == args.split]
    if args.source_note:
        cases = [case for case in cases if case.get("source_note") == args.source_note]
    if args.question_id_min or args.question_id_max:
        filtered = []
        for case in cases:
            qid = str(case.get("question_id") or "")
            try:
                numeric_id = int(qid.rsplit("_", 1)[-1])
            except ValueError:
                filtered.append(case)
                continue
            if args.question_id_min and numeric_id < args.question_id_min:
                continue
            if args.question_id_max and numeric_id > args.question_id_max:
                continue
            filtered.append(case)
        cases = filtered
    if args.max_cases:
        cases = cases[: args.max_cases]
    if not cases:
        raise RuntimeError("No cases selected for evaluation.")
    return cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate isolated candidate embeddings inside 70/30 hybrid recall.")
    parser.add_argument("--model", default="qwen3-embedding:8b")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--persist-dir", type=Path, default=DEFAULT_PERSIST_DIR)
    parser.add_argument("--collection-name", default="qwen3_embedding_8b_saudi_legal_chunks")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--dense-weight", type=float, default=0.70)
    parser.add_argument("--lexical-weight", type=float, default=0.30)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--split", default="")
    parser.add_argument("--source-note", default="")
    parser.add_argument("--question-id-min", type=int, default=0)
    parser.add_argument("--question-id-max", type=int, default=0)
    parser.add_argument("--k-values", type=int, nargs="+", default=[24, 42, 90, 180])
    parser.add_argument("--max-dense-k", type=int, default=180)
    parser.add_argument("--failure-samples", type=int, default=20)
    parser.add_argument("--include-forced", action="store_true")
    args = parser.parse_args()
    args.max_cases = args.max_cases or None
    if args.dense_weight < 0 or args.lexical_weight < 0:
        raise SystemExit("weights must be non-negative")
    if args.dense_weight == 0 and args.lexical_weight == 0:
        raise SystemExit("at least one weight must be positive")
    if any(k <= 0 for k in args.k_values):
        raise SystemExit("--k-values must be positive")
    return args


def main() -> None:
    args = parse_args()
    cases = select_cases(args)
    evaluator = IsolatedHybridEvaluator(args)
    evaluation = evaluator.evaluate(cases)
    report = {
        "experiment": "isolated_embedding_hybrid_jamia_recall",
        "created_at_unix": int(time.time()),
        "production_index_touched": False,
        "cases_path": str(args.cases),
        "split": args.split or "all",
        "source_note": args.source_note or "all",
        "evaluation": evaluation,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
