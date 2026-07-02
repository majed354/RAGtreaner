#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES="$ROOT/data/eval/gold_package_recall_v1/gold_package_recall_100_v1.jsonl"
RESPONSES_DIR="$ROOT/data/eval/gold_package_recall_v1/responses"
OUTPUT="$ROOT/data/eval/gold_package_recall_v1/gold_package_recall_report.json"
SERVICE_URL="http://127.0.0.1:8000/internal/rag/query"
ANSWER_MODE="benchmark"
RETRIEVAL_PROFILE="jamia_recall"
SPLIT="all"
LIMIT=""
TIMEOUT="120"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --split)
      SPLIT="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    --responses-dir)
      RESPONSES_DIR="$2"
      shift 2
      ;;
    --service-url)
      SERVICE_URL="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$RESPONSES_DIR"
REQUESTS_FILE="$RESPONSES_DIR/requests.tsv"

python3 - "$CASES" "$REQUESTS_FILE" "$SPLIT" "$LIMIT" "$ANSWER_MODE" "$RETRIEVAL_PROFILE" <<'PY'
import json
import sys
from pathlib import Path

cases_path = Path(sys.argv[1])
requests_path = Path(sys.argv[2])
split = sys.argv[3]
limit = int(sys.argv[4]) if sys.argv[4] else None
answer_mode = sys.argv[5]
retrieval_profile = sys.argv[6]

count = 0
with cases_path.open("r", encoding="utf-8") as src, requests_path.open("w", encoding="utf-8") as out:
    for line in src:
        if not line.strip():
            continue
        row = json.loads(line)
        if split != "all" and row.get("split") != split:
            continue
        payload = {
            "question": row["question"],
            "answer_mode": answer_mode,
            "retrieval_profile": retrieval_profile,
        }
        out.write(row["question_id"] + "\t" + json.dumps(payload, ensure_ascii=False) + "\n")
        count += 1
        if limit and count >= limit:
            break
print(count)
PY

TOTAL="$(wc -l < "$REQUESTS_FILE" | tr -d ' ')"
INDEX=0
while IFS=$'\t' read -r QUESTION_ID PAYLOAD; do
  INDEX=$((INDEX + 1))
  printf '[%s/%s] %s\n' "$INDEX" "$TOTAL" "$QUESTION_ID"
  printf '%s' "$PAYLOAD" | curl -sS --max-time "$TIMEOUT" \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "$SERVICE_URL" > "$RESPONSES_DIR/$QUESTION_ID.json"
done < "$REQUESTS_FILE"

python3 "$ROOT/scripts/run_gold_package_recall.py" \
  --score-only \
  --split "$SPLIT" \
  ${LIMIT:+--limit "$LIMIT"} \
  --responses-dir "$RESPONSES_DIR" \
  --output "$OUTPUT"
