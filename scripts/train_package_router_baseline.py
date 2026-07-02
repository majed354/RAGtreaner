"""Train and evaluate a recall-first multi-label package router baseline."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import re
import sys
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion
from sklearn.linear_model import LogisticRegression
from sklearn.linear_model import SGDClassifier
from sklearn.multiclass import OneVsRestClassifier
from sklearn.preprocessing import MultiLabelBinarizer


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DATASET_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"
DEFAULT_OUTPUT = DEFAULT_DATASET_DIR / "package_router_tfidf_ovr_baseline_report.json"
DEFAULT_MODEL = DEFAULT_DATASET_DIR / "package_router_tfidf_ovr_baseline.joblib"
DEFAULT_MANUAL = ROOT / "data" / "eval" / "manual_collection_external_audit_20260522.jsonl"
SEGMENT_SPLIT_RE = re.compile(
    r"(?:[.!؟?؛;\n]+|،\s*|(?:\s+ثم\s+)|(?:\s+كما\s+)|(?:\s+وفي الوقت نفسه\s+)|"
    r"(?:\s+وفي المقابل\s+)|(?:\s+في المقابل\s+)|(?:\s+بينما\s+))"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def manual_row(case: dict[str, Any]) -> dict[str, Any]:
    core = [str(slug) for slug in case.get("required_core_regulations") or [] if slug]
    companions = [str(slug) for slug in case.get("required_companion_regulations") or [] if slug]
    return {
        "question_id": str(case.get("question_id") or ""),
        "question": str(case.get("question") or ""),
        "router_role": "manual_external",
        "domain": case.get("domain"),
        "scenario_family_id": case.get("scenario_family_id"),
        "core_labels": list(dict.fromkeys(core)),
        "companion_labels": list(dict.fromkeys(companions)),
        "all_labels": list(dict.fromkeys([*core, *companions])),
    }


def make_features(args: argparse.Namespace) -> FeatureUnion:
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


def top_predictions(scores: np.ndarray, classes: np.ndarray, k: int) -> list[set[str]]:
    if scores.ndim == 1:
        scores = scores.reshape(-1, 1)
    out: list[set[str]] = []
    for row in scores:
        indexes = np.argsort(row)[::-1][: min(k, len(classes))]
        out.append({str(classes[index]) for index in indexes})
    return out


def query_segments(question: str, max_segments: int = 10) -> list[str]:
    parts = [question.strip()]
    for part in SEGMENT_SPLIT_RE.split(question):
        normalized = " ".join(part.split()).strip(" :،؛.")
        if len(normalized) >= 18 and normalized not in parts:
            parts.append(normalized)
        if len(parts) >= max_segments:
            break
    return parts


def predict_scores(features: FeatureUnion, classifier: OneVsRestClassifier, rows: list[dict[str, Any]]) -> np.ndarray:
    return classifier.predict_proba(features.transform([row["question"] for row in rows]))


def predict_segment_max_scores(
    features: FeatureUnion,
    classifier: OneVsRestClassifier,
    rows: list[dict[str, Any]],
) -> np.ndarray:
    texts: list[str] = []
    offsets: list[tuple[int, int]] = []
    for row in rows:
        start = len(texts)
        texts.extend(query_segments(str(row.get("question") or "")))
        offsets.append((start, len(texts)))
    scores = classifier.predict_proba(features.transform(texts))
    return np.asarray([scores[start:end].max(axis=0) for start, end in offsets])


def evaluate_rows(rows: list[dict[str, Any]], predicted: list[set[str]], known_labels: set[str]) -> dict[str, Any]:
    totals = Counter()
    failures: list[dict[str, Any]] = []
    unknown = Counter()
    for row, labels in zip(rows, predicted):
        core = [str(slug) for slug in row.get("core_labels") or []]
        companions = [str(slug) for slug in row.get("companion_labels") or []]
        required = list(dict.fromkeys([*core, *companions]))
        unknown.update(slug for slug in required if slug not in known_labels)
        missing_core = [slug for slug in core if slug not in labels]
        missing_companions = [slug for slug in companions if slug not in labels]
        totals["cases"] += 1
        totals["core_total"] += len(core)
        totals["core_hits"] += len(core) - len(missing_core)
        totals["companion_total"] += len(companions)
        totals["companion_hits"] += len(companions) - len(missing_companions)
        totals["required_total"] += len(required)
        totals["required_hits"] += len(required) - len(missing_core) - len(missing_companions)
        totals["full_package_cases"] += int(not missing_core and not missing_companions)
        totals["fatal_core_miss_cases"] += int(bool(missing_core))
        if (missing_core or missing_companions) and len(failures) < 12:
            failures.append(
                {
                    "question_id": row.get("question_id"),
                    "domain": row.get("domain"),
                    "scenario_family_id": row.get("scenario_family_id"),
                    "missing_core": missing_core,
                    "missing_companions": missing_companions,
                    "predicted": sorted(labels),
                }
            )
    cases = max(1, totals["cases"])
    return {
        "cases": totals["cases"],
        "core_recall": round(totals["core_hits"] / max(1, totals["core_total"]), 6),
        "companion_recall": round(totals["companion_hits"] / max(1, totals["companion_total"]), 6),
        "required_recall": round(totals["required_hits"] / max(1, totals["required_total"]), 6),
        "full_package_rate": round(totals["full_package_cases"] / cases, 6),
        "fatal_core_miss_cases": totals["fatal_core_miss_cases"],
        "unknown_required_labels": unknown.most_common(),
        "worst_failures": failures,
    }


def evaluate_analyzer(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from app.rag.engine import LegalRAGEngine

    engine = LegalRAGEngine()
    predicted: list[set[str]] = []
    all_labels: set[str] = set()
    for row in rows:
        query = engine._analyze_query(str(row.get("question") or ""), "jamia_recall")
        labels = {
            *[str(slug) for slug in query.get("required_core_regulations") or []],
            *[str(slug) for slug in query.get("required_companion_regulations") or []],
        }
        predicted.append(labels)
        all_labels.update(labels)
    return evaluate_rows(rows, predicted, all_labels)


def train(args: argparse.Namespace) -> dict[str, Any]:
    train_rows = load_jsonl(args.dataset_dir / "train.jsonl")
    for path in args.extra_train:
        train_rows.extend(load_jsonl(path))
    heldout_rows = load_jsonl(args.dataset_dir / "heldout.jsonl")
    manual_rows = [manual_row(row) for row in load_jsonl(args.manual_cases)]
    if not train_rows:
        raise SystemExit(f"missing train rows under {args.dataset_dir}")

    mlb = MultiLabelBinarizer()
    y_train = mlb.fit_transform([row["all_labels"] for row in train_rows])
    labels = set(str(slug) for slug in mlb.classes_)
    features = make_features(args)
    x_train = features.fit_transform([row["question"] for row in train_rows])
    if args.classifier == "sgd":
        base_classifier = SGDClassifier(
            loss="log_loss",
            alpha=args.alpha,
            class_weight="balanced",
            max_iter=args.max_iter,
            tol=args.tol,
            random_state=args.random_state,
        )
    else:
        base_classifier = LogisticRegression(
            C=args.c,
            class_weight="balanced",
            max_iter=args.max_iter,
            solver="liblinear",
        )
    classifier = OneVsRestClassifier(base_classifier, n_jobs=args.n_jobs)
    classifier.fit(x_train, y_train)

    outputs: dict[str, Any] = {}
    for name, rows in (("train", train_rows), ("heldout", heldout_rows), ("manual_external", manual_rows)):
        if not rows:
            outputs[name] = {"cases": 0}
            continue
        scores = predict_scores(features, classifier, rows)
        outputs[name] = {
            f"top_{k}": evaluate_rows(rows, top_predictions(scores, mlb.classes_, k), labels)
            for k in args.k_values
        }
        segment_scores = predict_segment_max_scores(features, classifier, rows)
        outputs[f"{name}_segment_max"] = {
            f"top_{k}": evaluate_rows(rows, top_predictions(segment_scores, mlb.classes_, k), labels)
            for k in args.k_values
        }

    analyzer_outputs = {
        "heldout": evaluate_analyzer(heldout_rows) if args.evaluate_analyzer else None,
        "manual_external": evaluate_analyzer(manual_rows) if args.evaluate_analyzer and manual_rows else None,
    }
    report = {
        "status": "ok",
        "model": f"tfidf_word_char_ovr_{args.classifier}",
        "dataset_dir": str(args.dataset_dir),
        "train_cases": len(train_rows),
        "extra_train_paths": [str(path) for path in args.extra_train],
        "heldout_cases": len(heldout_rows),
        "manual_external_cases": len(manual_rows),
        "train_label_count": len(labels),
        "top_k_values": args.k_values,
        "router_metrics": outputs,
        "current_analyzer_metrics": analyzer_outputs,
        "params": {
            "min_df": args.min_df,
            "word_ngram_max": args.word_ngram_max,
            "char_ngram_max": args.char_ngram_max,
            "word_max_features": args.word_max_features,
            "char_max_features": args.char_max_features,
            "c": args.c,
            "alpha": args.alpha,
            "tol": args.tol,
            "max_iter": args.max_iter,
            "classifier": args.classifier,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.model_output:
        joblib.dump({"features": features, "classifier": classifier, "label_binarizer": mlb}, args.model_output)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--manual-cases", type=Path, default=DEFAULT_MANUAL)
    parser.add_argument("--extra-train", nargs="*", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-output", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--k-values", nargs="+", type=int, default=[8, 12, 16, 24])
    parser.add_argument("--min-df", type=int, default=2)
    parser.add_argument("--word-ngram-max", type=int, default=2)
    parser.add_argument("--char-ngram-max", type=int, default=5)
    parser.add_argument("--word-max-features", type=int, default=120000)
    parser.add_argument("--char-max-features", type=int, default=160000)
    parser.add_argument("--c", type=float, default=3.0)
    parser.add_argument("--alpha", type=float, default=0.0001)
    parser.add_argument("--tol", type=float, default=0.001)
    parser.add_argument("--max-iter", type=int, default=600)
    parser.add_argument("--n-jobs", type=int, default=1)
    parser.add_argument("--classifier", choices=["logistic", "sgd"], default="logistic")
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--evaluate-analyzer", action="store_true")
    return parser.parse_args()


def main() -> None:
    print(json.dumps(train(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
