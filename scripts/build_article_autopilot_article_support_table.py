"""Build a TF-IDF article-pair support table from article-autopilot probes.

This is the article-level counterpart to the package router support table.  It
lets completed non-operational autopilot batches become a conservative recall
memory for future collection rounds: similar fact patterns can seed exact
article pairs before the context budget is filled by broader package material.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
from scipy.sparse import vstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTOPILOT_DIR = ROOT / "data" / "eval" / "article_autopilot"
DEFAULT_OUTPUT = DEFAULT_AUTOPILOT_DIR / "article_autopilot_article_support_table_v1.joblib"
DEFAULT_ARTICLE_SURFACE_ROWS = (
    ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_article_surface_table_v1.jsonl"
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_target_pairs(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    pairs: set[str] = set()
    if path.suffix.lower() == ".json":
        payload = load_json(path)
        rows = payload.get("target_pairs") or payload.get("pairs") or []
    else:
        rows = load_jsonl(path)
    for row in rows:
        if isinstance(row, str):
            pair = row
        elif isinstance(row, dict):
            pair = str(row.get("pair") or "")
            if not pair and row.get("slug") and row.get("article"):
                pair = f"{row.get('slug')}:{row.get('article')}"
        else:
            continue
        pair = pair.strip()
        if ":" in pair:
            pairs.add(pair)
    return pairs


def compact_expected_articles(row: dict[str, Any]) -> dict[str, list[int]]:
    expected = row.get("expected_articles_by_slug") or row.get("required_articles_by_slug") or {}
    if not isinstance(expected, dict):
        return {}
    out: dict[str, list[int]] = {}
    for slug, articles in expected.items():
        clean: list[int] = []
        for article in articles or []:
            try:
                value = int(article)
            except Exception:
                continue
            if value > 0 and value not in clean:
                clean.append(value)
        if clean:
            out[str(slug)] = sorted(clean)
    return out


def support_training_allowed(probe: dict[str, Any]) -> bool:
    if str(probe.get("synthetic_bank") or "").lower() == "holdout":
        return False
    if bool(probe.get("holdout_locked")):
        return False
    if str(probe.get("split") or "").lower() == "autopilot_holdout":
        return False
    return bool(probe.get("support_training_allowed", True))


def compact_surface_article(row: dict[str, Any]) -> tuple[str, int] | None:
    core_labels = [str(item) for item in row.get("core_labels") or [] if item]
    if not core_labels:
        return None
    try:
        article = int(row.get("source_article_index") or 0)
    except Exception:
        return None
    if article <= 0:
        return None
    return core_labels[0], article


def make_vectorizer(args: argparse.Namespace) -> FeatureUnion:
    return FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    analyzer="word",
                    ngram_range=(1, args.word_ngram_max),
                    min_df=args.min_df,
                    max_features=args.word_max_features,
                    sublinear_tf=True,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, args.char_ngram_max),
                    min_df=args.min_df,
                    max_features=args.char_max_features,
                    sublinear_tf=True,
                ),
            ),
        ]
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    probes_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted(args.autopilot_dir.glob("article_autopilot_probes_*.jsonl")):
        for probe in load_jsonl(path):
            qid = str(probe.get("question_id") or "")
            if qid:
                probes_by_id[qid] = probe

    non_operational_qids: set[str] = set()
    transport_skipped = 0
    holdout_skipped = 0
    for gate_path in sorted(args.autopilot_dir.glob("article_autopilot_gate_*.json")):
        gate = load_json(gate_path)
        for gate_row in gate.get("rows") or []:
            qid = str(gate_row.get("question_id") or "")
            if not qid:
                continue
            if gate_row.get("transport_error"):
                transport_skipped += 1
                continue
            probe = probes_by_id.get(qid)
            if probe and not support_training_allowed(probe):
                holdout_skipped += 1
                continue
            non_operational_qids.add(qid)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[tuple[str, tuple[int, ...]], ...]]] = set()
    for qid in sorted(non_operational_qids):
        probe = probes_by_id.get(qid)
        if not probe:
            continue
        expected = compact_expected_articles(probe)
        if not expected:
            continue
        question = " ".join(str(probe.get("question") or "").split())
        if not question:
            continue
        expected_key = tuple((slug, tuple(articles)) for slug, articles in sorted(expected.items()))
        key = (question, expected_key)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "question_id": qid,
                "question": question,
                "domain": probe.get("domain"),
                "expected_articles_by_slug": expected,
                "expected_core_regulations": probe.get("expected_core_regulations") or [],
                "expected_implementing_regulations": probe.get("expected_implementing_regulations") or [],
                "expected_companion_regulations": probe.get("expected_companion_regulations") or [],
                "source_note": "article_autopilot_non_operational_article_support",
            }
        )

    article_surface_rows_used = 0
    article_surface_skipped = 0
    target_pairs = load_target_pairs(args.article_surface_target_pairs)
    per_pair: Counter[str] = Counter()
    if args.include_article_surface_rows:
        per_slug: Counter[str] = Counter()
        for surface in load_jsonl(args.article_surface_rows):
            pair = compact_surface_article(surface)
            if pair is None:
                article_surface_skipped += 1
                continue
            slug, article = pair
            pair_key = f"{slug}:{article}"
            if target_pairs and pair_key not in target_pairs:
                article_surface_skipped += 1
                continue
            if args.article_surface_max_per_pair and per_pair[pair_key] >= args.article_surface_max_per_pair:
                article_surface_skipped += 1
                continue
            if per_slug[slug] >= args.article_surface_max_per_regulation:
                article_surface_skipped += 1
                continue
            if args.article_surface_max_total and article_surface_rows_used >= args.article_surface_max_total:
                article_surface_skipped += 1
                continue
            question = " ".join(str(surface.get("question") or "").split())
            if not question:
                article_surface_skipped += 1
                continue
            expected = {slug: [article]}
            expected_key = tuple((item_slug, tuple(articles)) for item_slug, articles in sorted(expected.items()))
            key = (question, expected_key)
            if key in seen:
                article_surface_skipped += 1
                continue
            seen.add(key)
            rows.append(
                {
                    "question_id": surface.get("question_id"),
                    "question": question,
                    "domain": surface.get("domain") or "structured_article_surface",
                    "expected_articles_by_slug": expected,
                    "expected_core_regulations": surface.get("core_labels") or [slug],
                    "expected_implementing_regulations": [],
                    "expected_companion_regulations": surface.get("companion_labels") or [],
                    "source_note": "structured_corpus_article_surface_support",
                    "source_chunk_id": surface.get("source_chunk_id"),
                }
            )
            per_slug[slug] += 1
            per_pair[pair_key] += 1
            article_surface_rows_used += 1

    if not rows:
        raise SystemExit("no article support rows built")

    vectorizer = make_vectorizer(args)
    matrix = vectorizer.fit_transform([row["question"] for row in rows])
    matrix = vstack([matrix]).tocsr()
    artifact = {
        "kind": "article_autopilot_article_support_table_v1",
        "vectorizer": vectorizer,
        "matrix": matrix,
        "rows": rows,
        "params": {
            "min_df": args.min_df,
            "word_ngram_max": args.word_ngram_max,
            "char_ngram_max": args.char_ngram_max,
            "word_max_features": args.word_max_features,
            "char_max_features": args.char_max_features,
            "inference_min_score": args.inference_min_score,
            "inference_top_rows": args.inference_top_rows,
            "inference_max_article_pairs": args.inference_max_article_pairs,
            "article_surface_score_weight": args.article_surface_score_weight,
            "article_surface_min_score": args.article_surface_min_score,
            "article_surface_max_article_pairs": args.article_surface_max_article_pairs,
            "article_surface_max_slugs": args.article_surface_max_slugs,
            "article_surface_require_package_match": args.article_surface_require_package_match,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.output)

    article_pair_count = sum(len(articles) for row in rows for articles in row["expected_articles_by_slug"].values())
    manifest = {
        "status": "ok",
        "output": str(args.output),
        "rows": len(rows),
        "article_pair_count": article_pair_count,
        "transport_skipped": transport_skipped,
        "holdout_skipped": holdout_skipped,
        "article_surface_rows_used": article_surface_rows_used,
        "article_surface_skipped": article_surface_skipped,
        "include_article_surface_rows": bool(args.include_article_surface_rows),
        "article_surface_target_pairs": str(args.article_surface_target_pairs or ""),
        "article_surface_target_pair_count": len(target_pairs),
        "article_surface_targeted": bool(target_pairs),
        "article_surface_max_per_pair": args.article_surface_max_per_pair,
        "unique_article_slugs": len({slug for row in rows for slug in row["expected_articles_by_slug"]}),
        "inference_min_score": args.inference_min_score,
        "inference_top_rows": args.inference_top_rows,
        "inference_max_article_pairs": args.inference_max_article_pairs,
        "article_surface_score_weight": args.article_surface_score_weight,
        "article_surface_min_score": args.article_surface_min_score,
        "article_surface_max_article_pairs": args.article_surface_max_article_pairs,
        "article_surface_max_slugs": args.article_surface_max_slugs,
        "article_surface_require_package_match": args.article_surface_require_package_match,
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autopilot-dir", type=Path, default=DEFAULT_AUTOPILOT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-df", type=int, default=1)
    parser.add_argument("--word-ngram-max", type=int, default=2)
    parser.add_argument("--char-ngram-max", type=int, default=5)
    parser.add_argument("--word-max-features", type=int, default=60000)
    parser.add_argument("--char-max-features", type=int, default=90000)
    parser.add_argument("--inference-min-score", type=float, default=0.45)
    parser.add_argument("--inference-top-rows", type=int, default=8)
    parser.add_argument("--inference-max-article-pairs", type=int, default=24)
    parser.add_argument("--include-article-surface-rows", action="store_true")
    parser.add_argument("--article-surface-rows", type=Path, default=DEFAULT_ARTICLE_SURFACE_ROWS)
    parser.add_argument("--article-surface-target-pairs", type=Path, default=None)
    parser.add_argument("--article-surface-max-per-pair", type=int, default=4)
    parser.add_argument("--article-surface-max-per-regulation", type=int, default=120)
    parser.add_argument("--article-surface-max-total", type=int, default=30000)
    parser.add_argument("--article-surface-score-weight", type=float, default=0.35)
    parser.add_argument("--article-surface-min-score", type=float, default=0.28)
    parser.add_argument("--article-surface-max-article-pairs", type=int, default=18)
    parser.add_argument("--article-surface-max-slugs", type=int, default=6)
    parser.add_argument("--article-surface-require-package-match", action="store_true")
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
