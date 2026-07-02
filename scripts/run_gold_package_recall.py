"""Run the frozen 100-case package-recall benchmark against the local RAG API.

This runner evaluates collection only by default:
- required core regulations must appear;
- required companion regulations should appear;
- extra regulations are recorded but not penalized in the collection score.

The gold package is loaded only by this evaluator after the service returns.
Only the question text is sent to the RAG endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.error
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / "data" / "eval" / "gold_package_recall_v1" / "gold_package_recall_100_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "eval" / "gold_package_recall_v1" / "gold_package_recall_report.json"
DEFAULT_RESPONSES_DIR = ROOT / "data" / "eval" / "gold_package_recall_v1" / "responses"
DEFAULT_SERVICE_URL = "http://127.0.0.1:8000/internal/rag/query"
DEFAULT_RETRIEVAL_PROBE_URL = "http://127.0.0.1:8000/internal/rag/retrieval-probe"
DEFAULT_ANSWER_MODE = "benchmark"
DEFAULT_RETRIEVAL_PROFILE = "jamia_recall"
REGULATIONS_PATH = ROOT / "data" / "structured" / "regulations.json"
REGULATIONS_BY_REGULATION_DIR = ROOT / "data" / "structured" / "by_regulation"
_LOCAL_ENGINE: Any | None = None


def normalize(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split()).strip().lower().translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    )


def load_cases(path: Path, split: str = "all", limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if split != "all" and row.get("split") != split:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def load_aliases() -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    if REGULATIONS_PATH.exists():
        rows = json.loads(REGULATIONS_PATH.read_text(encoding="utf-8"))
    else:
        rows = []
        for path in sorted(REGULATIONS_BY_REGULATION_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            metadata = payload.get("metadata") or {}
            rows.append(
                {
                    "slug": metadata.get("slug") or path.stem,
                    "title_ar": metadata.get("title_ar") or metadata.get("regulation_title_ar"),
                    "title_en": metadata.get("title_en") or metadata.get("name_en"),
                }
            )
    for row in rows:
        slug = str(row.get("slug") or row.get("regulation_slug") or "").strip()
        if not slug:
            continue
        values = {slug, slug.replace("-", " ")}
        for key in ("title_ar", "regulation_title_ar", "name_ar", "title_en", "name_en"):
            title = str(row.get(key) or "").strip()
            if not title:
                continue
            values.add(title)
            for prefix in ("اللائحة التنفيذية لنظام ", "لائحة تنظيم ", "لائحة ", "نظام "):
                if title.startswith(prefix):
                    values.add(title[len(prefix) :].strip())
        aliases[slug] = {normalize(value) for value in values if value}
    return aliases


async def query_service(
    case: dict[str, Any],
    service_url: str,
    answer_mode: str,
    retrieval_profile: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "question": case["question"],
            "answer_mode": answer_mode,
            "retrieval_profile": retrieval_profile,
        },
        ensure_ascii=False,
    )

    def _send() -> dict[str, Any]:
        completed = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                str(int(timeout_seconds)),
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                "@-",
                service_url,
            ],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return json.loads(completed.stdout.decode("utf-8"))

    return await asyncio.to_thread(_send)


async def retrieval_probe_service(
    case: dict[str, Any],
    service_url: str,
    retrieval_profile: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "question": case["question"],
            "retrieval_profile": retrieval_profile,
        },
        ensure_ascii=False,
    )

    def _send() -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(int(timeout_seconds)),
                    "-H",
                    "Content-Type: application/json",
                    "--data-binary",
                    "@-",
                    service_url,
                ],
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError:
            # The app exposes a GET probe specifically for local environments
            # where terminal POST requests intermittently fail before reaching
            # uvicorn. Treat this as transport fallback only.
            query = urllib.parse.urlencode(
                {"question": case["question"], "retrieval_profile": retrieval_profile}
            )
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(int(timeout_seconds)),
                    f"{service_url}?{query}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        return json.loads(completed.stdout.decode("utf-8"))

    return await asyncio.to_thread(_send)


async def local_retrieval_probe(case: dict[str, Any], retrieval_profile: str, no_dense: bool = False) -> dict[str, Any]:
    from app.rag.engine import LegalRAGEngine

    global _LOCAL_ENGINE
    if _LOCAL_ENGINE is None:
        _LOCAL_ENGINE = LegalRAGEngine()
        if no_dense:
            async def _no_dense(question: str, query_data: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            _LOCAL_ENGINE._dense_candidates = _no_dense
    engine = _LOCAL_ENGINE
    retrieval_result = await engine._hybrid_retrieve(
        case["question"],
        answer_mode="benchmark",
        retrieval_profile=retrieval_profile,
    )
    selected_candidates = retrieval_result.get("selected_candidates") or []
    query_data = retrieval_result.get("query_data") or {}
    selected_regulations: list[str] = []
    top_selected: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        entry = candidate.get("entry") or {}
        slug = str(entry.get("regulation_slug") or "").strip()
        if slug and slug not in selected_regulations:
            selected_regulations.append(slug)
    for index, candidate in enumerate(selected_candidates[:24], start=1):
        entry = candidate.get("entry") or {}
        slug = str(entry.get("regulation_slug") or "").strip()
        top_selected.append(
            {
                "rank": index,
                "citation": entry.get("citation_short_ar", ""),
                "regulation_slug": slug,
                "article_index": entry.get("article_index"),
            }
        )
    return {
        "status": "ok",
        "retrieval_profile": query_data.get("retrieval_profile", retrieval_profile),
        "selected_regulations": selected_regulations,
        "required_core_regulations": query_data.get("required_core_regulations", []),
        "required_companion_regulations": query_data.get("required_companion_regulations", []),
        "matched_document_bundles": query_data.get("matched_document_bundles", []),
        "matched_issue_axis_bundles": query_data.get("matched_issue_axis_bundles", []),
        "top_selected": top_selected,
    }


def observed_regulations(result: dict[str, Any], aliases: dict[str, set[str]]) -> list[str]:
    diagnostics = result.get("diagnostics") or {}
    observed = set(diagnostics.get("top_regulations") or [])
    observed.update((diagnostics.get("document_class_counts") or {}).keys())
    dominant = diagnostics.get("dominant_domain")
    if dominant:
        observed.add(dominant)

    answer = normalize(str(result.get("answer") or ""))
    sources = normalize("\n".join(str(source) for source in (result.get("sources") or [])))
    haystack = f"{answer}\n{sources}"
    for slug, slug_aliases in aliases.items():
        if slug in observed:
            continue
        if any(alias and alias in haystack for alias in slug_aliases):
            observed.add(slug)
    return sorted(slug for slug in observed if slug)


def observed_regulations_from_probe(response: dict[str, Any]) -> list[str]:
    observed = set()
    for slug in response.get("selected_regulations") or []:
        normalized_slug = str(slug or "").strip()
        if normalized_slug:
            observed.add(normalized_slug)
    if not observed:
        for item in response.get("top_selected") or []:
            slug = str(item.get("regulation_slug") or "").strip()
            if slug:
                observed.add(slug)
    dominant = str(response.get("dominant_domain") or "").strip()
    if dominant:
        observed.add(dominant)
    return sorted(observed)


def score_case(case: dict[str, Any], observed: list[str], transport_error: str | None = None) -> dict[str, Any]:
    observed_set = set(observed)
    core = case.get("required_core_regulations", [])
    companions = case.get("required_companion_regulations", [])
    optional = case.get("optional_regulations", [])
    excluded = case.get("excluded_regulations", [])

    matched_core = [slug for slug in core if slug in observed_set]
    matched_companions = [slug for slug in companions if slug in observed_set]
    matched_optional = [slug for slug in optional if slug in observed_set]
    excluded_hits = [slug for slug in excluded if slug in observed_set]

    core_recall = len(matched_core) / max(1, len(core))
    companion_recall = len(matched_companions) / len(companions) if companions else 1.0
    if companions:
        collection_score = (core_recall * 0.65) + (companion_recall * 0.35)
    else:
        collection_score = core_recall

    expected = set(core) | set(companions)
    full_package = bool(expected) and expected.issubset(observed_set)
    fatal_core_miss = bool(core) and not set(core).issubset(observed_set)

    return {
        "question_id": case["question_id"],
        "split": case.get("split"),
        "domain": case.get("domain"),
        "question": case["question"],
        "required_core_regulations": core,
        "required_companion_regulations": companions,
        "optional_regulations": optional,
        "excluded_regulations": excluded,
        "observed_regulations": observed,
        "matched_core_regulations": matched_core,
        "missing_core_regulations": [slug for slug in core if slug not in observed_set],
        "matched_companion_regulations": matched_companions,
        "missing_companion_regulations": [slug for slug in companions if slug not in observed_set],
        "matched_optional_regulations": matched_optional,
        "excluded_regulation_hits": excluded_hits,
        "core_recall": round(core_recall, 3),
        "companion_recall": round(companion_recall, 3),
        "collection_score": round(collection_score, 3),
        "collection_points": round(collection_score * 100, 1),
        "full_package_collected": full_package,
        "fatal_core_miss": fatal_core_miss,
        "transport_error": transport_error,
    }


def summarize(rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    completed = [row for row in rows if not row.get("transport_error")]
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_domain[str(row.get("domain") or "uncategorized")].append(row)
        by_split[str(row.get("split") or "uncategorized")].append(row)

    def group_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "cases": len(items),
            "collection_score_100": round(mean(float(row["collection_points"]) for row in items), 1) if items else 0.0,
            "core_recall": round(mean(float(row["core_recall"]) for row in items), 3) if items else 0.0,
            "companion_recall": round(mean(float(row["companion_recall"]) for row in items), 3) if items else 0.0,
            "full_package_rate": round(sum(1 for row in items if row["full_package_collected"]) / max(1, len(items)), 3),
            "fatal_core_miss_cases": sum(1 for row in items if row["fatal_core_miss"]),
            "excluded_hit_cases_recorded_only": sum(1 for row in items if row["excluded_regulation_hits"]),
        }

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_id": args.benchmark_id,
        "split": args.split,
        "answer_mode": args.answer_mode,
        "retrieval_profile": args.retrieval_profile,
        "service_url": args.service_url,
        "cases_total": len(rows),
        "cases_completed": len(completed),
        "collection_score_100": round(mean(float(row["collection_points"]) for row in rows), 1) if rows else 0.0,
        "core_recall": round(mean(float(row["core_recall"]) for row in rows), 3) if rows else 0.0,
        "companion_recall": round(mean(float(row["companion_recall"]) for row in rows), 3) if rows else 0.0,
        "full_package_rate": round(sum(1 for row in rows if row["full_package_collected"]) / max(1, len(rows)), 3),
        "fatal_core_miss_cases": sum(1 for row in rows if row["fatal_core_miss"]),
        "transport_error_cases": sum(1 for row in rows if row.get("transport_error")),
        "excluded_hit_cases_recorded_only": sum(1 for row in rows if row["excluded_regulation_hits"]),
        "split_counts": dict(Counter(row.get("split") for row in rows)),
        "domain_counts": dict(Counter(row.get("domain") for row in rows)),
        "by_split": {key: group_summary(items) for key, items in sorted(by_split.items())},
        "by_domain": {key: group_summary(items) for key, items in sorted(by_domain.items())},
        "worst_cases": [
            {
                "question_id": row["question_id"],
                "domain": row["domain"],
                "collection_points": row["collection_points"],
                "missing_core_regulations": row["missing_core_regulations"],
                "missing_companion_regulations": row["missing_companion_regulations"],
            }
            for row in sorted(rows, key=lambda item: (item["collection_points"], item["question_id"]))[:12]
        ],
    }


def markdown_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {summary.get('benchmark_id', 'Gold Package Recall')}",
        "",
        f"- score: `{summary['collection_score_100']}/100`",
        f"- cases: `{summary['cases_completed']}/{summary['cases_total']}`",
        f"- core recall: `{summary['core_recall']}`",
        f"- companion recall: `{summary['companion_recall']}`",
        f"- full package rate: `{summary['full_package_rate']}`",
        f"- fatal core miss cases: `{summary['fatal_core_miss_cases']}`",
        f"- excluded hits recorded only: `{summary['excluded_hit_cases_recorded_only']}`",
        "",
        "## By Domain",
        "",
    ]
    for domain, item in summary["by_domain"].items():
        lines.append(
            f"- `{domain}`: `{item['collection_score_100']}/100`, "
            f"core `{item['core_recall']}`, companion `{item['companion_recall']}`, "
            f"fatal `{item['fatal_core_miss_cases']}`"
        )
    lines.extend(["", "## Worst Cases", ""])
    for row in summary["worst_cases"]:
        lines.append(
            f"- `{row['question_id']}` `{row['domain']}`: `{row['collection_points']}/100`; "
            f"missing core={row['missing_core_regulations']} companions={row['missing_companion_regulations']}"
        )
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, split=args.split, limit=args.limit)
    aliases = load_aliases()
    rows_by_index: list[dict[str, Any] | None] = [None] * len(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))

    async def _evaluate(index: int, case: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        async with semaphore:
            return index, await _evaluate_case(case, aliases, args)

    async def _write_partial(completed: int) -> None:
        rows = [row for row in rows_by_index if row is not None]
        summary = summarize(rows, args)
        args.output.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.progress:
            latest = rows[-1] if rows else {}
            print(
                f"[{completed}/{len(cases)}] {latest.get('question_id')} {latest.get('collection_points')}/100",
                flush=True,
            )

    tasks = [asyncio.create_task(_evaluate(index, case)) for index, case in enumerate(cases, start=1)]
    completed_count = 0
    for task in asyncio.as_completed(tasks):
        index, row = await task
        rows_by_index[index - 1] = row
        completed_count += 1
        if args.write_partial:
            await _write_partial(completed_count)
        elif args.progress:
            print(f"[{completed_count}/{len(cases)}] {row['question_id']} {row['collection_points']}/100", flush=True)

    rows = [row for row in rows_by_index if row is not None]
    summary = summarize(rows, args)
    args.output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


async def _evaluate_case(case: dict[str, Any], aliases: dict[str, set[str]], args: argparse.Namespace) -> dict[str, Any]:
    try:
        if args.score_only:
            if not args.responses_dir:
                raise FileNotFoundError("--responses-dir is required with --score-only")
            response_path = args.responses_dir / f"{case['question_id']}.json"
            response = json.loads(response_path.read_text(encoding="utf-8"))
        elif args.retrieval_only:
            if args.local_engine:
                response = await asyncio.wait_for(
                    local_retrieval_probe(case, args.retrieval_profile, args.local_no_dense),
                    timeout=args.per_case_timeout + 5,
                )
            else:
                response = await asyncio.wait_for(
                    retrieval_probe_service(
                        case,
                        service_url=args.retrieval_probe_url,
                        retrieval_profile=args.retrieval_profile,
                        timeout_seconds=args.per_case_timeout,
                    ),
                    timeout=args.per_case_timeout + 5,
                )
        else:
            response = await asyncio.wait_for(
                query_service(
                    case,
                    service_url=args.service_url,
                    answer_mode=args.answer_mode,
                    retrieval_profile=args.retrieval_profile,
                    timeout_seconds=args.per_case_timeout,
                ),
                timeout=args.per_case_timeout + 5,
            )
        result = (response or {}).get("result") or {}
        if args.retrieval_only:
            observed = observed_regulations_from_probe(response or {})
        else:
            observed = observed_regulations(result, aliases)
        row = score_case(case, observed)
        if args.retrieval_only:
            selected = response.get("top_selected") or []
            row["confidence"] = None
            row["diagnostics_status"] = response.get("status")
            row["top_regulations"] = observed
            row["document_class_counts"] = dict(Counter(item.get("regulation_slug") for item in selected if item.get("regulation_slug")))
        else:
            row["confidence"] = result.get("confidence")
            row["diagnostics_status"] = (result.get("diagnostics") or {}).get("status")
            row["top_regulations"] = (result.get("diagnostics") or {}).get("top_regulations", [])
            row["document_class_counts"] = (result.get("diagnostics") or {}).get("document_class_counts", {})
    except (
        asyncio.TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        FileNotFoundError,
    ) as exc:
        row = score_case(case, [], transport_error=f"{type(exc).__name__}: {exc}")
    return row


async def _run_legacy_sequential(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases, split=args.split, limit=args.limit)
    aliases = load_aliases()
    rows: list[dict[str, Any]] = []
    args.output.parent.mkdir(parents=True, exist_ok=True)

    for index, case in enumerate(cases, start=1):
        try:
            if args.score_only:
                if not args.responses_dir:
                    raise FileNotFoundError("--responses-dir is required with --score-only")
                response_path = args.responses_dir / f"{case['question_id']}.json"
                response = json.loads(response_path.read_text(encoding="utf-8"))
            elif args.retrieval_only:
                if args.local_engine:
                    response = await asyncio.wait_for(
                        local_retrieval_probe(case, args.retrieval_profile, args.local_no_dense),
                        timeout=args.per_case_timeout + 5,
                    )
                else:
                    response = await asyncio.wait_for(
                        retrieval_probe_service(
                            case,
                            service_url=args.retrieval_probe_url,
                            retrieval_profile=args.retrieval_profile,
                            timeout_seconds=args.per_case_timeout,
                        ),
                        timeout=args.per_case_timeout + 5,
                    )
            else:
                response = await asyncio.wait_for(
                    query_service(
                        case,
                        service_url=args.service_url,
                        answer_mode=args.answer_mode,
                        retrieval_profile=args.retrieval_profile,
                        timeout_seconds=args.per_case_timeout,
                    ),
                    timeout=args.per_case_timeout + 5,
                )
            result = (response or {}).get("result") or {}
            if args.retrieval_only:
                observed = observed_regulations_from_probe(response or {})
            else:
                observed = observed_regulations(result, aliases)
            row = score_case(case, observed)
            if args.retrieval_only:
                selected = response.get("top_selected") or []
                row["confidence"] = None
                row["diagnostics_status"] = response.get("status")
                row["top_regulations"] = observed
                row["document_class_counts"] = dict(Counter(item.get("regulation_slug") for item in selected if item.get("regulation_slug")))
            else:
                row["confidence"] = result.get("confidence")
                row["diagnostics_status"] = (result.get("diagnostics") or {}).get("status")
                row["top_regulations"] = (result.get("diagnostics") or {}).get("top_regulations", [])
                row["document_class_counts"] = (result.get("diagnostics") or {}).get("document_class_counts", {})
        except (
            asyncio.TimeoutError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
            FileNotFoundError,
        ) as exc:
            row = score_case(case, [], transport_error=f"{type(exc).__name__}: {exc}")
        rows.append(row)

        if args.write_partial:
            summary = summarize(rows, args)
            args.output.write_text(
                json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if args.progress:
            print(f"[{index}/{len(cases)}] {row['question_id']} {row['collection_points']}/100", flush=True)

    summary = summarize(rows, args)
    args.output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path = args.output.with_suffix(".md")
    md_path.write_text(markdown_report(summary, rows), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved report to: {args.output}")
    print(f"Saved markdown to: {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--split", choices=["all", "dev", "regression", "heldout"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--retrieval-probe-url", default=DEFAULT_RETRIEVAL_PROBE_URL)
    parser.add_argument("--answer-mode", default=DEFAULT_ANSWER_MODE)
    parser.add_argument("--retrieval-profile", default=DEFAULT_RETRIEVAL_PROFILE)
    parser.add_argument("--benchmark-id", default="gold_package_recall_100_v1")
    parser.add_argument("--responses-dir", type=Path, default=DEFAULT_RESPONSES_DIR)
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--local-engine", action="store_true")
    parser.add_argument("--local-no-dense", action="store_true")
    parser.add_argument("--per-case-timeout", type=float, default=120.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--write-partial", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
