"""Ask a local Ollama Gemma model to decompose legal questions into packages."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib import request

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = ROOT / "data" / "eval" / "package_router" / "saudi_legal_package_router_v1"
DEFAULT_CASES = ROOT / "data" / "eval" / "manual_collection_external_audit_20260522.jsonl"
DEFAULT_OUTPUT = DEFAULT_DATASET_DIR / "gemma4_31b_teacher_manual_external.jsonl"
DEFAULT_ROUTER_MODEL = DEFAULT_DATASET_DIR / "package_router_tfidf_ovr_baseline.joblib"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_catalog(path: Path) -> dict[str, str]:
    labels = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["slug"]): str(item["title_ar"]) for item in labels}


def compact_catalog(catalog: dict[str, str], slugs: list[str] | None = None) -> str:
    selected = slugs or list(catalog)
    return "\n".join(f"- {slug}: {catalog[slug]}" for slug in selected if slug in catalog)


def router_candidates(router: dict[str, Any] | None, question: str, k: int) -> list[str]:
    if not router or k <= 0:
        return []
    features = router["features"].transform([question])
    scores = router["classifier"].predict_proba(features)
    classes = router["label_binarizer"].classes_
    row = scores[0] if getattr(scores, "ndim", 1) > 1 else scores
    indexes = np.argsort(row)[::-1][: min(k, len(classes))]
    return [str(classes[index]) for index in indexes]


def prompt_for(question: str, catalog: str) -> str:
    return f"""أنت محلل حزم لنظام RAG قانوني سعودي.
مهمتك اختيار الحزم النظامية التي ينبغي جمعها لهذا السؤال من slugs الكتالوج فقط.
لا تجب عن النزاع ولا تذكر فتوى. اسمح بالزيادة عند الشك لأن المرحلة مرحلة جمع.
أعد JSON صالحا فقط بهذا الشكل:
{{"core_regulations":["slug"], "companion_regulations":["slug"], "uncertain_regulations":["slug"]}}

كتالوج slugs المسموح:
{catalog}

السؤال:
{question}
"""


def parse_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = value.strip("`")
        value = value.removeprefix("json").strip()
    start = value.find("{")
    if start < 0:
        raise json.JSONDecodeError("missing JSON object", value, 0)
    parsed, _ = json.JSONDecoder().raw_decode(value[start:])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("teacher JSON must be an object", value, start)
    return parsed


def parse_partial_labels(text: str) -> dict[str, list[str]]:
    recovered: dict[str, list[str]] = {}
    for field in ("core_regulations", "companion_regulations", "uncertain_regulations"):
        match = re.search(rf'"{field}"\s*:\s*\[(.*?)\]', text, flags=re.DOTALL)
        recovered[field] = re.findall(r'"([^"]+)"', match.group(1)) if match else []
    return recovered


def run_gemma(model: str, prompt: str, timeout: int, ollama_url: str) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        ollama_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    model_output = str(body.get("response") or "")
    try:
        return parse_json(model_output)
    except json.JSONDecodeError as exc:
        recovered = parse_partial_labels(model_output)
        return {
            **recovered,
            "teacher_parse_error": str(exc),
            "teacher_raw_output": model_output,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="gemma4:31b")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--router-model", type=Path, default=DEFAULT_ROUTER_MODEL)
    parser.add_argument("--catalog-k", type=int, default=96)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    catalog = load_catalog(args.dataset_dir / "label_catalog.json")
    router = joblib.load(args.router_model) if args.router_model.exists() and args.catalog_k > 0 else None
    rows = load_jsonl(args.cases)
    if args.limit:
        rows = rows[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            question = str(row.get("question") or "")
            candidate_slugs = router_candidates(router, question, args.catalog_k)
            teacher = run_gemma(
                args.model,
                prompt_for(question, compact_catalog(catalog, candidate_slugs)),
                args.timeout,
                args.ollama_url,
            )
            out = {
                "question_id": row.get("question_id"),
                "question": row.get("question"),
                "teacher_model": args.model,
                "teacher_catalog_slugs": candidate_slugs or list(catalog),
                "teacher": teacher,
                "required_core_regulations": row.get("required_core_regulations") or row.get("core_labels") or [],
                "required_companion_regulations": row.get("required_companion_regulations") or row.get("companion_labels") or [],
            }
            handle.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
            print(json.dumps({"question_id": row.get("question_id"), "teacher": teacher}, ensure_ascii=False))


if __name__ == "__main__":
    main()
