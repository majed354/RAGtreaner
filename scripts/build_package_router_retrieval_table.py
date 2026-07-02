"""Build a fast TF-IDF retrieval table for package routing.

Unlike the OVR classifier, this artifact is fitted once over training rows and
returns labels from the nearest package-training examples.  It is designed as a
general coverage layer above semantic/lexical retrieval, especially for unseen
compound fact patterns.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
from scipy.sparse import vstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"
DEFAULT_INPUTS = [
    DATASET_DIR / "train.jsonl",
    DATASET_DIR / "composite_mixup_train.jsonl",
    DATASET_DIR / "gemma_gap_label_support_train.jsonl",
    DATASET_DIR / "package_router_generalization_table_v1.jsonl",
    DATASET_DIR / "package_router_article_surface_table_v1.jsonl",
    DATASET_DIR / "article_autopilot_router_support_train.jsonl",
]
DEFAULT_OUTPUT = DATASET_DIR / "package_router_retrieval_table_v1.joblib"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def compact_row(row: dict[str, Any]) -> dict[str, Any] | None:
    question = " ".join(str(row.get("question") or "").split())
    labels = [str(label) for label in row.get("all_labels") or [] if label]
    if not question or not labels:
        return None
    return {
        "question_id": row.get("question_id"),
        "question": question,
        "all_labels": list(dict.fromkeys(labels)),
        "core_labels": list(dict.fromkeys(str(label) for label in row.get("core_labels") or [] if label)),
        "companion_labels": list(dict.fromkeys(str(label) for label in row.get("companion_labels") or [] if label)),
        "source_note": row.get("source_note"),
        "scenario_family_id": row.get("scenario_family_id"),
    }


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
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path in args.inputs:
        for raw_row in load_jsonl(path):
            row = compact_row(raw_row)
            if row is None:
                continue
            key = (row["question"], tuple(row["all_labels"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    if not rows:
        raise SystemExit("no router rows loaded")

    vectorizer = make_vectorizer(args)
    matrix = vectorizer.fit_transform([row["question"] for row in rows])
    # Ensure CSR after FeatureUnion so runtime cosine checks are cheap.
    matrix = vstack([matrix]).tocsr()

    artifact = {
        "kind": "package_router_retrieval_table_v1",
        "vectorizer": vectorizer,
        "matrix": matrix,
        "rows": rows,
        "input_paths": [str(path) for path in args.inputs],
        "params": {
            "min_df": args.min_df,
            "word_ngram_max": args.word_ngram_max,
            "char_ngram_max": args.char_ngram_max,
            "word_max_features": args.word_max_features,
            "char_max_features": args.char_max_features,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.output)
    labels = sorted({label for row in rows for label in row["all_labels"]})
    manifest = {
        "status": "ok",
        "output": str(args.output),
        "rows": len(rows),
        "label_count": len(labels),
        "inputs": [str(path) for path in args.inputs],
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-df", type=int, default=1)
    parser.add_argument("--word-ngram-max", type=int, default=2)
    parser.add_argument("--char-ngram-max", type=int, default=5)
    parser.add_argument("--word-max-features", type=int, default=90000)
    parser.add_argument("--char-max-features", type=int, default=130000)
    return parser.parse_args()


def main() -> None:
    print(json.dumps(build(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
