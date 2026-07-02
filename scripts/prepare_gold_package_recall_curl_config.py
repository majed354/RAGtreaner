"""Prepare payload files and a single curl config for the gold package benchmark.

This helper exists for restricted environments where direct `curl` is allowed,
but network calls from Python subprocesses are not. It does not contact the
service; it only writes payloads and a curl config file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v1" / "gold_package_recall_100_v1.jsonl"
DEFAULT_OUT_DIR = ROOT / "data" / "eval" / "gold_package_recall_v1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--split", choices=["all", "dev", "regression", "heldout"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--service-url", default="http://127.0.0.1:8000/internal/rag/query")
    parser.add_argument("--answer-mode", default="benchmark")
    parser.add_argument("--retrieval-profile", default="jamia_recall")
    parser.add_argument("--timeout", default="120")
    args = parser.parse_args()
    if not args.cases.is_absolute():
        args.cases = (ROOT / args.cases).resolve()
    if not args.out_dir.is_absolute():
        args.out_dir = (ROOT / args.out_dir).resolve()

    payload_dir = args.out_dir / f"payloads_{args.split}"
    responses_dir = args.out_dir / f"responses_{args.split}"
    config_path = args.out_dir / f"curl_{args.split}.config"
    payload_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    config_lines: list[str] = []
    count = 0
    with args.cases.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            case = json.loads(line)
            if args.split != "all" and case.get("split") != args.split:
                continue

            qid = case["question_id"]
            payload = {
                "question": case["question"],
                "answer_mode": args.answer_mode,
                "retrieval_profile": args.retrieval_profile,
            }
            payload_path = payload_dir / f"{qid}.json"
            response_path = responses_dir / f"{qid}.json"
            payload_ref = payload_path.relative_to(ROOT)
            response_ref = response_path.relative_to(ROOT)
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            if config_lines:
                config_lines.append("next")
            config_lines.extend(
                [
                    f"max-time = {args.timeout}",
                    'header = "Content-Type: application/json"',
                    f"data-binary = @{payload_ref}",
                    f"output = {response_ref}",
                    f"url = {args.service_url}",
                ]
            )
            count += 1
            if args.limit and count >= args.limit:
                break

    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "cases": count,
                "split": args.split,
                "payload_dir": str(payload_dir),
                "responses_dir": str(responses_dir),
                "curl_config": str(config_path),
                "next_command": f"curl -sS -K {config_path}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
