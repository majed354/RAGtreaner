"""Fine-tune a trainable Qwen embedding model on Saudi legal synonym cases.

This script expects a HuggingFace/SentenceTransformers-compatible model path.
The Ollama Q8_0 blob is useful for inference, but it is not the training input
for this script.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import torch
from sentence_transformers import InputExample, SentenceTransformer, losses
from torch.utils.data import DataLoader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = ROOT / "data" / "eval" / "embedding_training" / "qwen3_saudi_legal_synonyms_v1"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "models" / "qwen3_embedding_saudi_legal_synonyms_v1"


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


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_triplets(dataset_dir: Path, splits: list[str], max_triplets: int, seed: int) -> list[InputExample]:
    rows: list[dict[str, Any]] = []
    for split in splits:
        path = dataset_dir / f"triplets_{split}.jsonl"
        if path.exists():
            rows.extend(load_jsonl(path))
    rng = random.Random(seed)
    rng.shuffle(rows)
    if max_triplets:
        rows = rows[:max_triplets]
    return [
        InputExample(texts=[str(row["query"]), str(row["positive"]), str(row["negative"])])
        for row in rows
    ]


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-name-or-path",
        default="Qwen/Qwen3-Embedding-0.6B",
        help="Use a local HF path when available to avoid downloading.",
    )
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--splits", nargs="+", default=["dev", "regression"])
    parser.add_argument("--max-triplets", type=int, default=12000)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--warmup-steps", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=20260521)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    triplets = load_triplets(args.dataset_dir, args.splits, args.max_triplets, args.seed)
    device = resolve_device(args.device)
    manifest = {
        "status": "prepared",
        "model_name_or_path": args.model_name_or_path,
        "dataset_dir": str(args.dataset_dir),
        "output_dir": str(args.output_dir),
        "splits": args.splits,
        "triplets": len(triplets),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "warmup_steps": args.warmup_steps,
        "learning_rate": args.learning_rate,
        "device": device,
        "note": "Training uses only synonym/colloquial final-1000-derived dev/regression triplets; heldout is reserved.",
    }
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    started = time.time()
    torch.manual_seed(args.seed)
    model = SentenceTransformer(
        args.model_name_or_path,
        device=device,
        trust_remote_code=True,
    )
    train_loader = DataLoader(triplets, shuffle=True, batch_size=args.batch_size)
    train_loss = losses.TripletLoss(model=model)
    model.fit(
        train_objectives=[(train_loader, train_loss)],
        epochs=args.epochs,
        warmup_steps=args.warmup_steps,
        optimizer_params={"lr": args.learning_rate},
        output_path=str(args.output_dir),
        show_progress_bar=True,
    )
    manifest.update(
        {
            "status": "trained",
            "elapsed_seconds": round(time.time() - started, 3),
        }
    )
    write_manifest(args.output_dir / "training_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
