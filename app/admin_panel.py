"""لوحة تحكم ويب للمشرف لإدارة النماذج ومقارنة المسارات."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import get_settings
from app.gemini_file_search import get_gemini_file_search_service
from app.official_sync import get_official_sync_service
from app.rag.engine import (
    ANSWER_MODE_CONSULTATION,
    ANSWER_MODE_LABELS,
    ANSWER_MODE_LEGAL_MEMO,
    get_engine,
)
from app.runtime_settings import PROVIDER_METADATA, get_runtime_settings_store

router = APIRouter()
settings = get_settings()
SESSION_COOKIE_NAME = "legal_admin_session"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)
COMPARE_JOB_TTL_SECONDS = 60 * 60
COMPARE_JOBS: dict[str, dict] = {}
COMPARE_JOBS_LOCK = threading.RLock()
ARTICLE_AUDIT_JOB_TTL_SECONDS = 6 * 60 * 60
ARTICLE_AUDIT_JOBS: dict[str, dict] = {}
ARTICLE_AUDIT_JOBS_LOCK = threading.RLock()
ARTICLE_AUTOPILOT_JOB_TTL_SECONDS = 6 * 60 * 60
ARTICLE_AUTOPILOT_JOBS: dict[str, dict] = {}
ARTICLE_AUTOPILOT_JOBS_LOCK = threading.RLock()
ARTICLE_AUTOPILOT_STOP_EVENTS: dict[str, threading.Event] = {}
ARTICLE_AUTOPILOT_ACTIVE_RUN_LOCK = threading.Lock()
ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID = ""
ARTICLE_AUTOPILOT_SUBPROCESSES: dict[str, subprocess.Popen] = {}
ARTICLE_AUTOPILOT_SUBPROCESSES_LOCK = threading.RLock()
ARTICLE_AUTOPILOT_RUN_STATE_LOCK = threading.RLock()
ARTICLE_SUPPORT_PAIR_CACHE: dict[str, object] = {"mtime": 0.0, "pairs": set()}
ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE_LOCK = threading.RLock()
ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE: dict[str, object] = {
    "signature": None,
    "rows": [],
    "summary_signature": None,
    "summary": {},
}
ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT = 4
ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT = 3
ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT = 8
ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT = 10
ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN = 10
ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT = 60
ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT = 60
ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT = 5
ARTICLE_AUTOPILOT_WATCHDOG_INTERVAL_SECONDS = 30
ARTICLE_AUTOPILOT_MIN_FREE_BYTES = 8 * 1024 * 1024 * 1024
ARTICLE_AUTOPILOT_ARTIFACT_RETENTION_COUNT = 10
ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_INTERVAL_SECONDS = 15 * 60
ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LOCK = threading.Lock()
ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LAST_TS = 0.0
ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS = {
    "ACCEPTED",
    "ACCEPTED_AFTER_RETRY",
    "ACCEPTED_WITH_DEFERRED_FAILURES",
    "ACCEPTED_WITH_HOLDOUT_BACKLOG",
    "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG",
}
ARTICLE_AUTOPILOT_CONTINUE_DECISIONS = {
    *ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS,
    "NO_RAG_CHANGE_NEEDED",
    "OPERATIONAL_ONLY_NO_RAG_CHANGE",
}
ARTICLE_AUTOPILOT_STALE_STAGE_SECONDS = {
    "queued": 180,
    "readiness": 180,
    "generate": 45 * 60,
    "gate": 15 * 60,
    "diagnose": 5 * 60,
    "promote": 5 * 60,
    "between_rounds": 180,
    "batch_ready_for_improvement": 180,
    "improve_batch": 135 * 60,
    "accept_or_rollback": 10 * 60,
    "next_batch": 180,
    "next_batch_after_rollback": 180,
}
ARTICLE_AUTOPILOT_COLLECTION_STEPS = [
    "readiness",
    "generate",
    "gate",
    "diagnose",
    "promote",
    "between_rounds",
    "batch_ready_for_improvement",
    "improve_batch",
    "next_batch",
]
ARTICLE_AUTOPILOT_IMPROVEMENT_STEPS = [
    "deep_diagnose",
    "build_general_support",
    "retest_batch",
    "smart_retry_if_needed",
    "manual_slice",
    "accept_or_rollback",
]
ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH = (
    PROJECT_ROOT / "data" / "eval" / "article_autopilot" / "deferred_improvement_backlog.jsonl"
)
ARTICLE_AUTOPILOT_DIR = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
ARTICLE_AUTOPILOT_RUN_STATE_PATH = ARTICLE_AUTOPILOT_DIR / "article_autopilot_run_state.json"
ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH = ARTICLE_AUTOPILOT_DIR / "article_autopilot_article_support_table_v1.joblib"
ARTICLE_AUTOPILOT_SUPPORT_MANIFEST_PATH = (
    ARTICLE_AUTOPILOT_DIR / "article_autopilot_article_support_table_v1.manifest.json"
)
ARTICLE_AUTOPILOT_FIXED_HOLDOUT_BASELINE_PATH = ARTICLE_AUTOPILOT_DIR / "fixed_holdout_baseline_v1.json"
ARTICLE_COVERAGE_MATRIX_PATH = PROJECT_ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"
EVAL_DIR = PROJECT_ROOT / "data" / "eval"
STRUCTURED_BY_REGULATION_DIR = PROJECT_ROOT / "data" / "structured" / "by_regulation"
COMPARE_PROVIDER_ORDER = ("ollama", "mlx_local", "openrouter", "gemini")
COMPARE_PROVIDER_TITLES = {
    "ollama": "المسار المحلي: Ollama",
    "mlx_local": "المسار المحلي: MLX",
    "openrouter": "المسار السحابي: OpenRouter",
    "gemini": "المسار السحابي: Gemini",
}


def _escape(value) -> str:
    return html.escape("" if value is None else str(value))


def _normalize_answer_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ANSWER_MODE_LABELS:
        return normalized
    return ANSWER_MODE_CONSULTATION


def _render_answer_mode_select(selected_answer_mode: str) -> str:
    normalized = _normalize_answer_mode(selected_answer_mode)
    options = []
    for answer_mode in (ANSWER_MODE_CONSULTATION, ANSWER_MODE_LEGAL_MEMO):
        label = ANSWER_MODE_LABELS[answer_mode]
        selected_attr = " selected" if answer_mode == normalized else ""
        options.append(
            f"<option value=\"{_escape(answer_mode)}\"{selected_attr}>{_escape(label)}</option>"
        )
    return "\n".join(options)


def _render_active_provider_options(panel_state: dict) -> str:
    options = []
    for provider in panel_state["providers"]:
        provider_id = provider["id"]
        selected_attr = " selected" if panel_state["active_provider"] == provider_id else ""
        options.append(
            f"<option value=\"{_escape(provider_id)}\"{selected_attr}>{_escape(provider['label'])}</option>"
        )
    return "\n".join(options)


def _get_compare_provider_ids(panel_state: dict) -> list[str]:
    provider_ids = {provider["id"] for provider in panel_state["providers"]}
    return [provider_id for provider_id in COMPARE_PROVIDER_ORDER if provider_id in provider_ids]


def _render_compare_provider_chips(panel_state: dict, selected_provider_ids: set[str]) -> str:
    provider_map = {provider["id"]: provider for provider in panel_state["providers"]}
    chips = []
    for provider_id in _get_compare_provider_ids(panel_state):
        provider = provider_map[provider_id]
        label = COMPARE_PROVIDER_TITLES.get(provider_id, provider["label"])
        checked = "checked" if provider_id in selected_provider_ids else ""
        chips.append(
            f"<label class=\"chip\"><input type=\"checkbox\" name=\"compare_{_escape(provider_id)}\" "
            f"value=\"{_escape(provider_id)}\" {checked}> {_escape(label)}</label>"
        )
    return "".join(chips)


def _build_session_token() -> str:
    if not settings.admin_panel_password:
        return ""
    raw = f"{settings.admin_panel_password}|{settings.bot_username}|admin"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_authenticated(request: Request) -> bool:
    if not settings.admin_panel_enabled:
        return False
    if not settings.admin_panel_password:
        return True
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    return secrets.compare_digest(cookie_value, _build_session_token())


def _redirect_with_message(path: str, *, notice: Optional[str] = None, error: Optional[str] = None) -> RedirectResponse:
    params = []
    if notice:
        params.append(f"notice={quote(notice)}")
    if error:
        params.append(f"error={quote(error)}")
    target = path
    if params:
        target = f"{path}?{'&'.join(params)}"
    return RedirectResponse(target, status_code=303)


def _build_restart_command() -> list[str]:
    argv = [part.lower() for part in sys.argv]
    if any("uvicorn" in part for part in argv):
        uvicorn_args = sys.argv[1:] if len(sys.argv) > 1 else ["app.main:app"]
        return [sys.executable, "-m", "uvicorn", *uvicorn_args]
    return [sys.executable, "-m", "app.main"]


def _restart_process():
    command = _build_restart_command()
    env = os.environ.copy()
    project_root_str = str(PROJECT_ROOT)
    pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [part for part in pythonpath.split(os.pathsep) if part]
    if project_root_str not in pythonpath_parts:
        pythonpath_parts.insert(0, project_root_str)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    logger.warning("🔁 إعادة تشغيل الخدمة من لوحة التحكم...")
    os.chdir(PROJECT_ROOT)
    os.execvpe(command[0], command, env)


def _schedule_restart(delay_seconds: float = 1.0):
    timer = threading.Timer(delay_seconds, _restart_process)
    timer.daemon = True
    timer.start()


def _render_provider_cards(panel_state: dict) -> str:
    cards = []
    for provider in panel_state["providers"]:
        suggestions = "".join(
            f"<span class='chip'>{_escape(item)}</span>"
            for item in provider["model_suggestions"][:12]
        )
        extra = ""
        if provider["connection_ok"] is not None:
            status_badge = "ok" if provider["connection_ok"] else "warn"
            status_label = "متصل" if provider["connection_ok"] else "بحاجة تحقق"
            extra += (
                f"<p><strong>حالة الفهرس:</strong> "
                f"<span class='badge {status_badge}'>{status_label}</span></p>"
            )

        if provider["catalog_message"]:
            extra += f"<p>{_escape(provider['catalog_message'])}</p>"

        if provider["selected_model_available"] is True:
            extra += "<p><strong>النموذج المختار:</strong> موجود ضمن القائمة المتاحة.</p>"
        elif provider["selected_model_available"] is False:
            extra += "<p><strong>النموذج المختار:</strong> غير ظاهر في القائمة الحالية لهذا المزود.</p>"
            if provider.get("resolved_model"):
                extra += f"<p><strong>النموذج البديل المستخدم تلقائيًا:</strong> {_escape(provider['resolved_model'])}</p>"

        if provider["available_models"]:
            extra += f"<p><strong>عدد النماذج المتاحة:</strong> {_escape(len(provider['available_models']))}</p>"
        cards.append(
            f"""
            <div class="provider-card {'active' if panel_state['active_provider'] == provider['id'] else ''}">
              <div class="provider-header">
                <h3>{_escape(provider['label'])}</h3>
                <span class="badge {'ok' if provider['configured'] else 'warn'}">
                  {'مهيأ' if provider['configured'] else 'يحتاج إعداد'}
                </span>
              </div>
              <p>{_escape(provider['description'])}</p>
              <p><strong>النموذج الحالي:</strong> {_escape(provider['model'])}</p>
              <p><strong>التوكن:</strong> {_escape(provider['token_masked'])}</p>
              <p><strong>عنوان الربط:</strong> {_escape(provider['connection_target'] or '—')}</p>
              {extra}
              <div class="chips">{suggestions}</div>
            </div>
            """
        )
    return "".join(cards)


def _group_openrouter_models(model_ids: list[str]) -> list[tuple[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for model_id in model_ids:
        label = model_id.split("/", 1)[0] if "/" in model_id else "other"
        grouped.setdefault(label, []).append(model_id)
    return sorted(
        ((label, sorted(items)) for label, items in grouped.items()),
        key=lambda item: item[0],
    )


def _render_model_select(name: str, current_value: str, model_ids: list[str], *, provider_id: str) -> str:
    unique_models = []
    seen = set()
    for model_id in model_ids:
        normalized = (model_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_models.append(normalized)

    if current_value and current_value not in seen:
        unique_models.insert(0, current_value)
        seen.add(current_value)

    if provider_id == "openrouter":
        groups = _group_openrouter_models(unique_models)
        options_html = []
        if current_value and current_value not in {model for _, items in groups for model in items}:
            options_html.append(
                f"<option value='{_escape(current_value)}' selected>{_escape(current_value)} (القيمة الحالية)</option>"
            )
        for group_label, items in groups:
            group_options = "".join(
                f"<option value='{_escape(model_id)}' {'selected' if model_id == current_value else ''}>{_escape(model_id)}</option>"
                for model_id in items
            )
            options_html.append(f"<optgroup label='{_escape(group_label)}'>{group_options}</optgroup>")
        body = "".join(options_html)
    else:
        body = "".join(
            f"<option value='{_escape(model_id)}' {'selected' if model_id == current_value else ''}>{_escape(model_id)}</option>"
            for model_id in unique_models
        )

    return f"<select name='{_escape(name)}' class='model-select'>{body}</select>"


def _render_compare_sources(title: str, sources: list[dict], fallback_sources: Optional[list[str]] = None) -> str:
    if sources:
        items = []
        for item in sources[:6]:
            items.append(
                f"""
                <div class="source-box">
                  <strong>{_escape(item.get('title') or f"{title} #{item.get('index', '')}")}</strong>
                  <pre>{_escape(item.get('text', ''))}</pre>
                  <div class="muted">{_escape(item.get('uri', ''))}</div>
                </div>
                """
            )
        return "".join(items)

    fallback_sources = fallback_sources or []
    if not fallback_sources:
        return "<div class='muted'>لا توجد مصادر ظاهرة لهذه النتيجة.</div>"

    return "".join(
        f"<div class='source-box'><pre>{_escape(source[:1200])}</pre></div>"
        for source in fallback_sources[:4]
    )


def _cleanup_compare_jobs():
    cutoff = time.time() - COMPARE_JOB_TTL_SECONDS
    removable_ids = []
    for job_id, payload in COMPARE_JOBS.items():
        if payload.get("updated_at", 0) < cutoff:
            removable_ids.append(job_id)
    for job_id in removable_ids:
        COMPARE_JOBS.pop(job_id, None)


def _create_compare_job(question: str, selected_providers: list[str], answer_mode: str) -> str:
    answer_mode = _normalize_answer_mode(answer_mode)
    job_id = secrets.token_urlsafe(12)
    with COMPARE_JOBS_LOCK:
        _cleanup_compare_jobs()
        COMPARE_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "question": question,
            "selected_providers": selected_providers,
            "answer_mode": answer_mode,
            "answer_mode_label": ANSWER_MODE_LABELS[answer_mode],
            "results": [],
            "error": "",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return job_id


def _update_compare_job(job_id: str, **updates):
    with COMPARE_JOBS_LOCK:
        job = COMPARE_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _get_compare_job(job_id: str) -> Optional[dict]:
    with COMPARE_JOBS_LOCK:
        job = COMPARE_JOBS.get(job_id)
        if not job:
            return None
        return deepcopy(job)


def _relative_project_path(path: Path | str | None) -> str:
    if not path:
        return ""
    resolved = Path(path)
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _safe_read_json(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_read_jsonl(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def _path_from_manifest(manifest: dict, key: str) -> Path | None:
    value = ((manifest.get("paths") or {}).get(key) or "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    try:
        total = max(0, int(seconds))
    except Exception:
        return "—"
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}د {secs}ث"
    return f"{secs}ث"


def _format_hours_minutes(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    try:
        total_minutes = max(0, int(round(float(seconds) / 60)))
    except Exception:
        return "—"
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}س {minutes}د"
    return f"{minutes}د"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_datetime_minute(value: str | None) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value or ""
    riyadh_time = parsed.astimezone(timezone(timedelta(hours=3)))
    return riyadh_time.strftime("%Y-%m-%d %H:%M")


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _short_join(values, limit: int = 5) -> str:
    items = [str(value) for value in (values or []) if str(value or "").strip()]
    if not items:
        return "—"
    shown = items[:limit]
    suffix = f" +{len(items) - limit}" if len(items) > limit else ""
    return "، ".join(shown) + suffix


def _points(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _score_bucket_label(points: float, passed: bool, operational: bool = False) -> str:
    if operational:
        return "تشغيلي"
    if passed:
        return "مكتمل"
    if points >= 90.0:
        return "قريب جدًا"
    if points >= 50.0:
        return "جزئي"
    return "بعيد"


def _article_score_stats(rows: list[dict]) -> dict:
    non_operational = [row for row in (rows or []) if not row.get("transport_error")]
    total = len(non_operational)
    average = round(
        sum(_points(row.get("article_points")) for row in non_operational) / total,
        1,
    ) if total else 0.0
    return {
        "average": average,
        "total": total,
        "passed": sum(1 for row in non_operational if row.get("passed")),
        "near_miss": sum(
            1
            for row in non_operational
            if not row.get("passed") and 90.0 <= _points(row.get("article_points")) < 100.0
        ),
        "partial": sum(
            1
            for row in non_operational
            if not row.get("passed") and 50.0 <= _points(row.get("article_points")) < 90.0
        ),
        "low": sum(
            1
            for row in non_operational
            if not row.get("passed") and _points(row.get("article_points")) < 50.0
        ),
        "operational": sum(1 for row in (rows or []) if row.get("transport_error")),
    }


def _axis_coverage_quality(row: dict) -> float:
    axis_coverage = row.get("axis_coverage") or {}
    if not axis_coverage:
        return 100.0 if row.get("all_axes_covered", True) else 0.0

    scores = []
    for item in axis_coverage.values():
        expected = item.get("expected_article_pairs") or []
        covered = item.get("covered_article_pairs") or []
        if not expected:
            scores.append(100.0)
            continue
        scores.append(min(100.0, (len(covered) / max(1, len(expected))) * 100.0))
    if not scores:
        return 100.0 if row.get("all_axes_covered", True) else 0.0
    return round(sum(scores) / len(scores), 1)


def _pre_improvement_case_quality(row: dict) -> float | None:
    if row.get("transport_error"):
        return None
    if row.get("passed"):
        return 100.0

    article_score = _points(row.get("article_points"))
    has_core = bool(row.get("governing_system_present")) and not row.get("missing_core_regulations")
    has_implementing = (
        bool(row.get("implementing_regulation_present"))
        and not row.get("missing_implementing_regulations")
    )
    core_score = 100.0 if has_core else 0.0
    implementing_score = 100.0 if has_implementing else 0.0
    axis_score = _axis_coverage_quality(row)
    route_score = 70.0 if row.get("unrouted_expected_article_pairs") else 100.0
    score = (
        (article_score * 0.45)
        + (core_score * 0.20)
        + (implementing_score * 0.10)
        + (axis_score * 0.15)
        + (route_score * 0.10)
    )

    if row.get("missing_core_regulations"):
        score = min(score, 45.0)
    if row.get("missing_implementing_regulations"):
        score = min(score, 70.0)
    if row.get("failed_axes"):
        score = min(score, 85.0)
    if row.get("unrouted_expected_article_pairs"):
        score = min(score, 90.0)
    if row.get("missing_article_pairs"):
        score = min(score, max(article_score, 20.0))
    return round(max(0.0, min(100.0, score)), 1)


def _quality_number_class(score) -> str:
    if score is None:
        return "unknown"
    points = _points(score)
    if points >= 90.0:
        return "ok"
    if points >= 70.0:
        return "warn"
    return "danger"


def _percent_value(value) -> float | None:
    if value in {None, "", "—"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        number *= 100.0
    return round(max(0.0, min(100.0, number)), 1)


def _int_value(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _latest_eval_report(patterns: list[str]) -> Path | None:
    candidates: dict[str, Path] = {}
    for pattern in patterns:
        for path in EVAL_DIR.glob(pattern):
            if path.suffix != ".json":
                continue
            if "_failed" in path.name:
                continue
            candidates[str(path)] = path
    if not candidates:
        return None
    return max(candidates.values(), key=lambda item: item.stat().st_mtime)


def _stable_quality_component(
    *,
    key: str,
    label: str,
    patterns: list[str],
    score_field: str,
    weight: float,
) -> dict:
    path = _latest_eval_report(patterns)
    summary = (_safe_read_json(path).get("summary") or {}) if path else {}
    score = _percent_value(summary.get(score_field))
    pass_rate = _percent_value(summary.get("pass_rate"))
    if score is None:
        score = pass_rate
    cases_total = _int_value(
        summary.get("non_operational_cases"),
        _int_value(summary.get("cases_total"), 0),
    )
    failed_cases = _int_value(summary.get("failed_cases"), 0)
    transport_errors = _int_value(summary.get("transport_error_cases"), 0)
    return {
        "key": key,
        "label": label,
        "weight": weight,
        "score": score,
        "score_label": f"{score}%" if score is not None else "—",
        "pass_rate": pass_rate,
        "pass_rate_label": f"{pass_rate}%" if pass_rate is not None else "—",
        "cases_total": cases_total,
        "failed_cases": failed_cases,
        "transport_error_cases": transport_errors,
        "evaluated_at": summary.get("evaluated_at") or "",
        "report_path": _relative_project_path(path) if path else "",
    }


def _stable_quality_snapshot() -> dict:
    component_specs = [
        {
            "key": "article_precision_blind100",
            "label": "دقة المواد blind100",
            "patterns": [
                "manual_article_precision_blind100_*after_final_answer_phrase_routes.json",
                "manual_article_precision_blind100_*after_blind60_phrase_routes_patch.json",
                "manual_article_precision_blind100_*after_traffic_answer_routes_patch.json",
                "manual_article_precision_blind100_*after_answer_citation_routes_patch.json",
                "manual_article_precision_blind100_*after_phrase_router_patch.json",
                "manual_article_precision_blind100_*approved_article_gate.json",
            ],
            "score_field": "article_score_100",
            "weight": 0.40,
        },
        {
            "key": "answer_grounding_blind60",
            "label": "تجذير الإجابة blind60",
            "patterns": [
                "manual_answer_grounding_blind60_*after_phrase_routes.json",
                "manual_answer_grounding_blind60_*service_initial.json",
            ],
            "score_field": "answer_grounding_score_100",
            "weight": 0.30,
        },
        {
            "key": "answer_grounding_heldout30",
            "label": "تجذير الإجابة heldout30",
            "patterns": [
                "manual_answer_grounding_heldout30_*after_final_phrase_routes.json",
                "manual_answer_grounding_heldout30_*after_phrase_routes.json",
            ],
            "score_field": "answer_grounding_score_100",
            "weight": 0.30,
        },
    ]
    components = [_stable_quality_component(**spec) for spec in component_specs]
    scored_components = [
        component for component in components
        if component.get("score") is not None and component.get("report_path")
    ]
    weight_total = sum(float(component.get("weight") or 0.0) for component in scored_components)
    stable_score = None
    if weight_total > 0:
        stable_score = round(
            sum(float(component["score"]) * float(component.get("weight") or 0.0) for component in scored_components)
            / weight_total,
            1,
        )
    failed_cases = sum(_int_value(component.get("failed_cases"), 0) for component in scored_components)
    transport_errors = sum(_int_value(component.get("transport_error_cases"), 0) for component in scored_components)
    cases_total = sum(_int_value(component.get("cases_total"), 0) for component in scored_components)
    latest_at = ""
    parsed_dates = [
        parsed for parsed in (_parse_datetime(component.get("evaluated_at")) for component in scored_components)
        if parsed
    ]
    if parsed_dates:
        latest_at = max(parsed_dates).isoformat()
    if not scored_components:
        status = "لا توجد تقارير مستقرة كافية"
    elif transport_errors:
        status = "يتطلب فحصًا تشغيليًا"
    elif stable_score is not None and stable_score >= 99.0 and failed_cases == 0:
        status = "مغلق على آخر gates"
    elif stable_score is not None and stable_score >= 95.0:
        status = "مستقر مع فجوات محدودة"
    else:
        status = "يحتاج تحسينًا مستقرًا"
    return {
        "score": stable_score,
        "score_label": f"{stable_score}%" if stable_score is not None else "—",
        "score_class": _quality_number_class(stable_score),
        "status": status,
        "cases_total": cases_total,
        "failed_cases": failed_cases,
        "transport_error_cases": transport_errors,
        "latest_evaluated_at": latest_at,
        "components": components,
        "method": "40% دقة مواد blind100 + 30% تجذير إجابة blind60 + 30% تجذير إجابة heldout30؛ جولات الاستكشاف لا تغيّر هذا الرقم",
    }


def _pre_improvement_batch_quality(manifest: dict) -> dict:
    batch_manifests = ((manifest.get("diagnosis") or {}).get("batch_manifests") or [])
    scores: list[float] = []
    passed = 0
    operational = 0
    missing_reports = 0

    for raw_path in batch_manifests:
        round_path = Path(str(raw_path or ""))
        if not round_path.is_absolute():
            round_path = PROJECT_ROOT / round_path
        round_manifest = _safe_read_json(round_path)
        gate_path = _path_from_manifest(round_manifest, "gate")
        gate_report = _safe_read_json(gate_path)
        rows = gate_report.get("rows") or []
        if not rows:
            missing_reports += 1
            continue
        for row in rows:
            score = _pre_improvement_case_quality(row)
            if score is None:
                operational += 1
                continue
            scores.append(score)
            if row.get("passed"):
                passed += 1

    if not scores:
        return {
            "score": None,
            "label": "—",
            "cases": 0,
            "operational": operational,
            "pass_rate": None,
            "pass_rate_label": "—",
            "class": "unknown",
            "missing_reports": missing_reports,
        }

    average = round(sum(scores) / len(scores), 1)
    pass_rate = round((passed / len(scores)) * 100.0, 1)
    return {
        "score": average,
        "label": f"{average}%",
        "cases": len(scores),
        "operational": operational,
        "pass_rate": pass_rate,
        "pass_rate_label": f"{pass_rate}%",
        "class": _quality_number_class(average),
        "missing_reports": missing_reports,
    }


def _autopilot_action_for(row: dict, finding: dict, probe: dict, promoted_ids: set[str]) -> str:
    qid = str(row.get("question_id") or "")
    if qid in promoted_ids:
        return "ترقية تلقائية إلى بنك التوسعة وإدخالها في مصفوفة التدقيق التالية."
    if row.get("transport_error"):
        return "إعادة تشغيل الجولة بعد استقرار الخدمة؛ لا تُحسب كفجوة RAG."

    auto_review = probe.get("auto_review") or {}
    if auto_review.get("status") == "needs_human_review":
        return "مراجعة بشرية محدودة لاعتماد المرشح ثم تطبيق إصلاح عام إن ثبتت الفجوة."

    reason = str((finding or {}).get("reason") or "")
    if reason == "expected_article_not_routed":
        return "توسيع محور route أو حزمة المواد حتى تصل المادة المتوقعة للسياق."
    if reason == "context_budget_displacement":
        return "تحسين ترتيب السياق حتى لا تزاحم الأنظمة الزائدة المواد الدقيقة."
    if reason == "missing_article_material":
        return "تعزيز اختيار المادة داخل الحزمة أو إضافة إشارات عامة للمحور."
    if row.get("passed"):
        return "لا إجراء؛ المرشح ناجح وينتظر/دخل الترقية بعد تحقق Qwen الموثوق ونجاح gate."
    return "تشخيص الفشل ثم تطبيق إصلاح عام، لا ترقيع خاص بالقضية."


def _latest_eval_file(pattern: str) -> Path | None:
    eval_dir = PROJECT_ROOT / "data" / "eval"
    matches = sorted(eval_dir.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _load_article_audit_snapshot() -> dict:
    matrix_path = PROJECT_ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"
    probes_path = PROJECT_ROOT / "data" / "eval" / "article_coverage_matrix_v1_probes.jsonl"
    gap_path = _latest_eval_file("article_coverage_matrix_v1_probe_gap_summary_*.json")
    gate_path = None
    if gap_path:
        gate_name = gap_path.name.replace("article_coverage_matrix_v1_probe_gap_summary_", "article_coverage_matrix_v1_probe_gate_")
        gate_path = gap_path.with_name(gate_name)
        if not gate_path.exists():
            gate_path = None
    gate_path = gate_path or _latest_eval_file("article_coverage_matrix_v1_probe_gate_*.json")

    matrix = _safe_read_json(matrix_path)
    gate_report = _safe_read_json(gate_path)
    gap_summary = _safe_read_json(gap_path)
    gate_summary = gate_report.get("summary", gate_report) if gate_report else {}
    matrix_summary = matrix.get("summary", matrix) if matrix else {}
    classification_counts = gap_summary.get("classification_counts") or {}
    reason_counts = gap_summary.get("reason_counts") or {}

    operational_issues = int(classification_counts.get("operational issue", 0) or 0)
    retrieval_issues = int(classification_counts.get("retrieval/package issue", 0) or 0)
    answer_issues = int(classification_counts.get("answer-level issue", 0) or 0)
    transport_errors = int(gate_summary.get("transport_error_cases", 0) or 0)
    decision = gap_summary.get("decision")
    if not decision and gate_summary:
        decision = "PASS" if int(gate_summary.get("failed_cases", 0) or 0) == 0 and transport_errors == 0 else "FAIL"

    top_gap = "لا توجد فجوة ظاهرة."
    if operational_issues or transport_errors:
        top_gap = "مشكلة تشغيلية في الاتصال أو تشغيل الاختبار، وليست فجوة RAG."
    elif gap_summary.get("top_missing_article_pairs"):
        item = gap_summary["top_missing_article_pairs"][0]
        top_gap = f"المادة الأعلى غيابًا: {item.get('pair')} ({item.get('citation_short_ar') or 'بدون وصف'})"
    elif gap_summary.get("top_missing_regulations"):
        item = gap_summary["top_missing_regulations"][0]
        top_gap = f"النظام/اللائحة الأعلى غيابًا: {item.get('regulation_slug')}"
    elif int(gate_summary.get("failed_cases", 0) or 0) and gate_summary.get("worst_cases"):
        item = gate_summary["worst_cases"][0]
        top_gap = f"أضعف حالة: {item.get('question_id')} بدرجة {item.get('article_points')}/100"

    return {
        "matrix_summary": matrix_summary,
        "gate_summary": gate_summary,
        "gap_summary": gap_summary,
        "decision": decision or "لم يعمل بعد",
        "operational_issues": operational_issues,
        "retrieval_issues": retrieval_issues,
        "answer_issues": answer_issues,
        "transport_errors": transport_errors,
        "reason_counts": reason_counts,
        "top_gap": top_gap,
        "matrix_path": _relative_project_path(matrix_path) if matrix_path.exists() else "",
        "probes_path": _relative_project_path(probes_path) if probes_path.exists() else "",
        "gate_path": _relative_project_path(gate_path) if gate_path else "",
        "gap_path": _relative_project_path(gap_path) if gap_path else "",
    }


def _render_article_audit_card(snapshot: dict) -> str:
    gate_summary = snapshot.get("gate_summary") or {}
    matrix_summary = snapshot.get("matrix_summary") or {}
    decision = snapshot.get("decision") or "لم يعمل بعد"
    operational_issues = int(snapshot.get("operational_issues") or 0)
    retrieval_issues = int(snapshot.get("retrieval_issues") or 0)
    answer_issues = int(snapshot.get("answer_issues") or 0)
    badge_class = "ok" if decision == "PASS" else "warn" if operational_issues else "danger"
    if decision == "لم يعمل بعد":
        badge_class = "warn"
    article_score = gate_summary.get("article_score_100", "—")
    pass_rate = gate_summary.get("pass_rate", "—")
    cases_total = gate_summary.get("cases_total", matrix_summary.get("generated_probe_count", "—"))
    generated_probes = matrix_summary.get("generated_probe_count", "—")
    evaluated_at = gate_summary.get("evaluated_at") or matrix_summary.get("created_at") or "لم يعمل بعد"

    report_links = []
    for label, path in (
        ("مصفوفة التغطية", snapshot.get("matrix_path")),
        ("حالات الفحص", snapshot.get("probes_path")),
        ("تقرير الدقة", snapshot.get("gate_path")),
        ("ملخص الفجوات", snapshot.get("gap_path")),
    ):
        if path:
            report_links.append(f"<span class='chip'>{_escape(label)}: {_escape(path)}</span>")

    return f"""
      <div class="audit-panel">
        <div class="audit-summary">
          <div>
            <h3>تدقيق دقة الجمع</h3>
            <div class="muted">يقيس حضور النظام الحاكم، اللائحة التنفيذية، والمواد الدقيقة لكل محور واقعة.</div>
          </div>
          <span class="badge {badge_class}">{_escape(decision)}</span>
        </div>
        <div class="grid compact-grid">
          <div class="stat compact-stat">
            <h3>درجة المواد</h3>
            <div class="value">{_escape(article_score)}</div>
            <div class="muted">من 100</div>
          </div>
          <div class="stat compact-stat">
            <h3>نسبة المرور</h3>
            <div class="value">{_escape(pass_rate)}</div>
            <div class="muted">عدد الحالات: {_escape(cases_total)}</div>
          </div>
          <div class="stat compact-stat">
            <h3>حالات الفحص</h3>
            <div class="value">{_escape(generated_probes)}</div>
            <div class="muted">من مصفوفة التغطية</div>
          </div>
        </div>
        <div class="chips">
          <span class="chip">تشغيلي: {_escape(operational_issues)}</span>
          <span class="chip">استرجاع/حزمة: {_escape(retrieval_issues)}</span>
          <span class="chip">مستوى الجواب: {_escape(answer_issues)}</span>
          <span class="chip">أخطاء اتصال: {_escape(snapshot.get('transport_errors') or 0)}</span>
        </div>
        <div class="note">{_escape(snapshot.get("top_gap") or "")}</div>
        <div class="muted">آخر تحديث: {_escape(evaluated_at)}</div>
        <div class="chips">{''.join(report_links)}</div>
        <form id="article-audit-form" method="post" action="/admin/article-audit/start">
          <button type="submit">تشغيل تدقيق دقة الجمع الآن</button>
        </form>
        <div id="article-audit-status"></div>
      </div>
    """


def _latest_autopilot_manifest() -> Path | None:
    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    return max(
        output_dir.glob("article_autopilot_manifest_*.json"),
        key=lambda item: item.name,
        default=None,
    )


def _latest_autopilot_manifests(limit: int = 12) -> list[Path]:
    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    return sorted(
        output_dir.glob("article_autopilot_manifest_*.json"),
        key=lambda item: item.name,
        reverse=True,
    )[:limit]


def _latest_autopilot_improvement_manifest() -> Path | None:
    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    return max(
        output_dir.glob("article_autopilot_improvement_manifest_*.json"),
        key=lambda item: item.name,
        default=None,
    )


def _load_autopilot_improvement_snapshot() -> dict:
    path = _latest_autopilot_improvement_manifest()
    manifest = _safe_read_json(path)
    if not manifest:
        return {}
    validation = manifest.get("validation_summary") or {}
    manual = manifest.get("manual_summary") or {}
    fixed_holdout = manifest.get("fixed_holdout_summary") or {}
    fixed_holdout_source = "آخر دورة"
    if not fixed_holdout:
        fixed_holdout = (
            _safe_read_json(ARTICLE_AUTOPILOT_FIXED_HOLDOUT_BASELINE_PATH).get("summary") or {}
        )
        fixed_holdout_source = "خط الأساس الثابت"
    moving_holdout = manifest.get("moving_holdout_summary") or manifest.get("holdout_summary") or {}
    holdout = moving_holdout
    auto_diagnostics = manifest.get("auto_failure_diagnostics") or []
    last_auto_diagnostic = auto_diagnostics[-1] if auto_diagnostics else {}
    auto_recipe = last_auto_diagnostic.get("selected_recipe") or {}
    auto_history = last_auto_diagnostic.get("history") or {}
    return {
        "decision": manifest.get("decision") or "—",
        "created_at": manifest.get("created_at") or "",
        "batch_rounds": manifest.get("batch_rounds") or 0,
        "support_rows": ((manifest.get("router_support") or {}).get("rows")),
        "article_support_rows": ((manifest.get("article_support") or {}).get("rows")),
        "validation_score": validation.get("article_score_100", "—"),
        "validation_pass_rate": validation.get("pass_rate", "—"),
        "validation_failed_cases": validation.get("failed_cases", "—"),
        "manual_score": manual.get("article_score_100", "—"),
        "manual_pass_rate": manual.get("pass_rate", "—"),
        "manual_failed_cases": manual.get("failed_cases", "—"),
        "fixed_holdout_score": fixed_holdout.get("article_score_100", "—"),
        "fixed_holdout_pass_rate": fixed_holdout.get("pass_rate", "—"),
        "fixed_holdout_failed_cases": fixed_holdout.get("failed_cases", "—"),
        "fixed_holdout_cases": fixed_holdout.get("cases_total", 0),
        "fixed_holdout_governing_system_rate": _percent_value(fixed_holdout.get("governing_system_rate")),
        "fixed_holdout_axis_coverage_rate": _percent_value(fixed_holdout.get("axis_coverage_rate")),
        "fixed_holdout_context_entry_rate": _percent_value(fixed_holdout.get("context_entry_rate")),
        "fixed_holdout_source": fixed_holdout_source,
        "fixed_holdout_guard": manifest.get("fixed_holdout_guard") or {},
        "holdout_score": holdout.get("article_score_100", "—"),
        "holdout_pass_rate": holdout.get("pass_rate", "—"),
        "holdout_failed_cases": holdout.get("failed_cases", "—"),
        "holdout_cases": holdout.get("cases_total", 0),
        "holdout_governing_system_rate": _percent_value(holdout.get("governing_system_rate")),
        "holdout_implementing_regulation_rate": _percent_value(holdout.get("implementing_regulation_rate")),
        "holdout_axis_coverage_rate": _percent_value(holdout.get("axis_coverage_rate")),
        "holdout_context_entry_rate": _percent_value(holdout.get("context_entry_rate")),
        "holdout_article_mrr": holdout.get("article_mrr", "—"),
        "holdout_mean_expected_article_rank": holdout.get("mean_expected_article_rank", "—"),
        "holdout_mean_context_position": holdout.get("mean_context_position", "—"),
        "article_mrr": validation.get("article_mrr", "—"),
        "pollution_rate": validation.get("pollution_rate", "—"),
        "auto_failure_gate": last_auto_diagnostic.get("failure_gate", "—"),
        "auto_failure_cause": last_auto_diagnostic.get("top_root_cause", "—"),
        "auto_deep_failure_mode": last_auto_diagnostic.get("deep_failure_mode", "—"),
        "auto_recipe": auto_recipe.get("id", "—"),
        "auto_recipe_escalation_reason": auto_recipe.get("escalation_reason", "—"),
        "auto_same_failure_count": auto_history.get("same_gate_cause_count", "—"),
        "auto_diagnostics_count": len(auto_diagnostics),
        "accepted_after_attempt": manifest.get("accepted_after_attempt") or 0,
        "attempts_count": len(manifest.get("attempts") or []),
        "deferred_failure_count": manifest.get("deferred_failure_count") or 0,
        "manifest_path": _relative_project_path(path) if path else "",
        "validation_path": _relative_project_path(Path(manifest.get("validation_gate_path"))) if manifest.get("validation_gate_path") else "",
        "manual_path": _relative_project_path(Path(manifest.get("manual_gate_path"))) if manifest.get("manual_gate_path") else "",
        "fixed_holdout_path": _relative_project_path(Path(manifest.get("fixed_holdout_gate_path"))) if manifest.get("fixed_holdout_gate_path") else "",
        "holdout_path": _relative_project_path(Path(manifest.get("holdout_gate_path"))) if manifest.get("holdout_gate_path") else "",
    }


def _success_ratio(summary: dict) -> str:
    total = int(summary.get("cases_total", 0) or 0)
    failed = int(summary.get("failed_cases", 0) or 0)
    transport = int(summary.get("transport_error_cases", 0) or 0)
    success = max(0, total - failed)
    if not total:
        return "—"
    suffix = f" · تشغيلي {transport}" if transport else ""
    return f"{success} / {total}{suffix}"


def _load_development_cycle_rows(limit: int | None = 10) -> list[dict]:
    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    manifest_paths = sorted(
        output_dir.glob("article_autopilot_improvement_manifest_*.json"),
        key=lambda path: path.name,
        reverse=True,
    )
    latest_manifest_name = max((path.name for path in manifest_paths), default="")
    signature = (latest_manifest_name, len(manifest_paths), limit)

    with ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE_LOCK:
        if ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE.get("signature") == signature:
            cached_rows = list(ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE.get("rows") or [])
            return cached_rows if limit is None else cached_rows[:limit]

    if limit is not None:
        manifest_paths = manifest_paths[:limit]
    rows = []
    manifests: list[tuple[datetime, Path, dict]] = []
    for path in manifest_paths:
        manifest = _safe_read_json(path)
        if not manifest:
            continue
        created_at = _parse_datetime(manifest.get("created_at")) or datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        )
        manifests.append((created_at, path, manifest))

    sorted_manifests = sorted(manifests, key=lambda item: item[0], reverse=True)
    for _created_at_sort, path, manifest in sorted_manifests:
        validation = manifest.get("validation_summary") or {}
        manual = manifest.get("manual_summary") or {}
        fixed_holdout = manifest.get("fixed_holdout_summary") or {}
        holdout = manifest.get("moving_holdout_summary") or manifest.get("holdout_summary") or {}
        auto_diagnostics = manifest.get("auto_failure_diagnostics") or []
        last_auto_diagnostic = auto_diagnostics[-1] if auto_diagnostics else {}
        auto_recipe = last_auto_diagnostic.get("selected_recipe") or {}
        auto_history = last_auto_diagnostic.get("history") or {}
        diagnosis = manifest.get("diagnosis") or {}
        root_counts = diagnosis.get("root_cause_counts") or {}
        top_root = "—"
        if root_counts:
            top_root = max(root_counts.items(), key=lambda item: int(item[1] or 0))[0]
        total = int(validation.get("cases_total", 0) or 0)
        failed = int(validation.get("failed_cases", 0) or 0)
        transport = int(validation.get("transport_error_cases", 0) or 0)
        effective_total = max(0, total - transport)
        gap_rate = round((failed / max(1, effective_total)) * 100, 1) if effective_total else 0.0
        decision = manifest.get("decision") or "—"
        action_note = "معتمد؛ يمكن الانتقال للدفعة التالية."
        if decision == "REJECTED_ROLLED_BACK":
            if transport:
                action_note = "تشغيلي؛ أعد التحقق بعد استقرار الخدمة."
            else:
                action_note = "لم يعتمد؛ رُحّل للمراجعة العميقة ويستمر الجمع على النسخة المستقرة."
        elif decision == "ACCEPTED_WITH_DEFERRED_FAILURES":
            action_note = "اعتمد بقرار بشري مع ترحيل الإخفاقات."
        elif decision == "ACCEPTED_WITH_HOLDOUT_BACKLOG":
            action_note = "اعتماد قديم قبل فصل الثابت/المتحرك؛ إخفاقات holdout رُحّلت."
        elif decision == "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG":
            action_note = "اعتمد بعد عبور الثابت؛ رُحّلت إخفاقات الهولداوت الاستكشافي."
        elif decision == "NO_RAG_CHANGE_NEEDED":
            action_note = "لا فجوات في دفعة التدريب الحالية؛ يستمر الاستكشاف الأفقي بدل التوقف."
        pre_quality = _pre_improvement_batch_quality(manifest)
        rows.append(
            {
                "manifest_path": str(path),
                "manifest_label": _relative_project_path(path),
                "created_at": manifest.get("created_at") or "",
                "created_at_display": _format_datetime_minute(manifest.get("created_at")),
                "pre_quality_score": pre_quality.get("score"),
                "pre_quality_label": pre_quality.get("label"),
                "pre_quality_class": pre_quality.get("class"),
                "pre_quality_cases": pre_quality.get("cases"),
                "pre_quality_operational": pre_quality.get("operational"),
                "pre_quality_pass_rate": pre_quality.get("pass_rate"),
                "pre_quality_pass_rate_label": pre_quality.get("pass_rate_label"),
                "pre_quality_missing_reports": pre_quality.get("missing_reports"),
                "decision": decision,
                "validation_mode": manifest.get("validation_mode") or (
                    "fast_staged" if manifest.get("fixed_holdout_sampled") else "full"
                ),
                "fixed_holdout_sampled": bool(manifest.get("fixed_holdout_sampled")),
                "fixed_holdout_total_case_count": manifest.get("fixed_holdout_total_case_count") or fixed_holdout.get("cases_total", 0),
                "batch_rounds": manifest.get("batch_rounds") or 0,
                "success_ratio": _success_ratio(validation),
                "gap_rate": gap_rate,
                "validation_score": validation.get("article_score_100", "—"),
                "manual_score": manual.get("article_score_100", "—"),
                "fixed_holdout_score": fixed_holdout.get("article_score_100", "—"),
                "fixed_holdout_pass_rate": fixed_holdout.get("pass_rate", "—"),
                "fixed_holdout_cases": fixed_holdout.get("cases_total", 0),
                "holdout_score": holdout.get("article_score_100", "—"),
                "holdout_pass_rate": holdout.get("pass_rate", "—"),
                "holdout_cases": holdout.get("cases_total", 0),
                "article_mrr": validation.get("article_mrr", "—"),
                "pollution_rate": validation.get("pollution_rate", "—"),
                "auto_failure_gate": last_auto_diagnostic.get("failure_gate", "—"),
                "auto_failure_cause": last_auto_diagnostic.get("top_root_cause", "—"),
                "auto_deep_failure_mode": last_auto_diagnostic.get("deep_failure_mode", "—"),
                "auto_recipe": auto_recipe.get("id", "—"),
                "auto_recipe_escalation_reason": auto_recipe.get("escalation_reason", "—"),
                "auto_same_failure_count": auto_history.get("same_gate_cause_count", "—"),
                "auto_diagnostics_count": len(auto_diagnostics),
                "attempts": len(manifest.get("attempts") or []),
                "accepted_after_attempt": manifest.get("accepted_after_attempt") or 0,
                "top_root_cause": top_root,
                "action_note": action_note,
                "failed_cases": failed,
                "transport_errors": transport,
                "manual_failed_cases": manual.get("failed_cases", "—"),
            }
        )
    with ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE_LOCK:
        ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE["signature"] = signature
        ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE["rows"] = rows
    return rows if limit is None else rows[:limit]


def _load_development_cycle_summary(recent_rows: list[dict]) -> dict:
    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    manifest_paths = list(output_dir.glob("article_autopilot_improvement_manifest_*.json"))
    latest_manifest_name = max((path.name for path in manifest_paths), default="")
    signature = (latest_manifest_name, len(manifest_paths))

    with ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE_LOCK:
        if ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE.get("summary_signature") == signature:
            return dict(ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE.get("summary") or {})

    total_cycles = 0
    accepted_cycles = 0
    no_change_cycles = 0
    rejected_cycles = 0
    operational_cycles = 0
    total_batch_rounds = 0
    total_validation_cases = 0
    total_failed_cases = 0
    total_deferred = 0
    for path in manifest_paths:
        manifest = _safe_read_json(path)
        if not manifest:
            continue
        total_cycles += 1
        decision = str(manifest.get("decision") or "")
        validation = manifest.get("validation_summary") or {}
        transport = int(validation.get("transport_error_cases") or 0)
        failed = int(validation.get("failed_cases") or 0)
        if decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS:
            accepted_cycles += 1
        elif decision == "NO_RAG_CHANGE_NEEDED":
            no_change_cycles += 1
        elif decision == "REJECTED_ROLLED_BACK":
            rejected_cycles += 1
        if transport:
            operational_cycles += 1
        total_batch_rounds += int(manifest.get("batch_rounds") or 0)
        total_validation_cases += int(validation.get("cases_total") or 0)
        total_failed_cases += failed
        if decision in {
            "ACCEPTED_WITH_DEFERRED_FAILURES",
            "ACCEPTED_WITH_HOLDOUT_BACKLOG",
            "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG",
        }:
            total_deferred += failed

    pre_quality_scores = [
        _points(row.get("pre_quality_score"))
        for row in recent_rows
        if row.get("pre_quality_score") is not None
    ]
    average_pre_quality = (
        round(sum(pre_quality_scores) / len(pre_quality_scores), 1)
        if pre_quality_scores
        else None
    )
    summary = {
        "total_cycles": total_cycles,
        "accepted_cycles": accepted_cycles,
        "no_change_cycles": no_change_cycles,
        "rejected_cycles": rejected_cycles,
        "operational_cycles": operational_cycles,
        "total_batch_rounds": total_batch_rounds,
        "total_validation_cases": total_validation_cases,
        "total_failed_cases": total_failed_cases,
        "total_deferred": total_deferred,
        "acceptance_rate": round((accepted_cycles / total_cycles) * 100, 1) if total_cycles else 0.0,
        "average_pre_quality": average_pre_quality,
        "average_pre_quality_label": (
            f"{average_pre_quality}% (آخر {len(pre_quality_scores)} دورة)"
            if average_pre_quality is not None
            else "—"
        ),
    }
    with ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE_LOCK:
        ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE["summary_signature"] = signature
        ARTICLE_AUTOPILOT_DEVELOPMENT_ROWS_CACHE["summary"] = summary
    return summary


def _development_cycle_summary(rows: list[dict]) -> dict:
    total_cycles = len(rows)
    accepted_cycles = sum(
        1 for row in rows if row.get("decision") in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
    )
    no_change_cycles = sum(1 for row in rows if row.get("decision") == "NO_RAG_CHANGE_NEEDED")
    rejected_cycles = sum(1 for row in rows if row.get("decision") == "REJECTED_ROLLED_BACK")
    operational_cycles = sum(1 for row in rows if int(row.get("transport_errors") or 0) > 0)
    total_batch_rounds = sum(int(row.get("batch_rounds") or 0) for row in rows)
    pre_quality_scores = [
        _points(row.get("pre_quality_score"))
        for row in rows
        if row.get("pre_quality_score") is not None
    ]
    total_validation_cases = 0
    total_failed_cases = 0
    total_deferred = 0
    for row in rows:
        validation_total = str(row.get("success_ratio") or "")
        if "/" in validation_total:
            try:
                total_validation_cases += int(validation_total.split("/", 1)[1].split("·", 1)[0].strip())
            except Exception:
                pass
        total_failed_cases += int(row.get("failed_cases") or 0)
        if row.get("decision") in {
            "ACCEPTED_WITH_DEFERRED_FAILURES",
            "ACCEPTED_WITH_HOLDOUT_BACKLOG",
            "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG",
        }:
            total_deferred += int(row.get("failed_cases") or 0)
    acceptance_rate = round((accepted_cycles / total_cycles) * 100, 1) if total_cycles else 0.0
    average_pre_quality = (
        round(sum(pre_quality_scores) / len(pre_quality_scores), 1)
        if pre_quality_scores
        else None
    )
    return {
        "total_cycles": total_cycles,
        "accepted_cycles": accepted_cycles,
        "no_change_cycles": no_change_cycles,
        "rejected_cycles": rejected_cycles,
        "operational_cycles": operational_cycles,
        "total_batch_rounds": total_batch_rounds,
        "total_validation_cases": total_validation_cases,
        "total_failed_cases": total_failed_cases,
        "total_deferred": total_deferred,
        "acceptance_rate": acceptance_rate,
        "average_pre_quality": average_pre_quality,
        "average_pre_quality_label": f"{average_pre_quality}%" if average_pre_quality is not None else "—",
    }


def _load_matrix_article_pairs(path: Path = ARTICLE_COVERAGE_MATRIX_PATH) -> set[str]:
    matrix = _safe_read_json(path)
    return {
        str(item.get("pair"))
        for item in matrix.get("article_pairs", [])
        if item.get("pair")
    }


def _article_catalog_totals() -> dict:
    pairs: set[str] = set()
    slugs: set[str] = set()
    for path in sorted(STRUCTURED_BY_REGULATION_DIR.glob("*.json")):
        data = _safe_read_json(path)
        metadata = data.get("metadata") or {}
        slug = str(metadata.get("slug") or path.stem)
        for article in data.get("articles") or []:
            try:
                article_index = int(article.get("article_index") or 0)
            except Exception:
                continue
            text = str(article.get("text_for_index") or article.get("text_verbatim") or "").strip()
            if not article_index or len(text) < 80:
                continue
            pairs.add(f"{slug}:{article_index}")
            slugs.add(slug)
    matrix_pairs = _load_matrix_article_pairs()
    eligible_pairs = pairs - matrix_pairs
    eligible_slugs = {pair.rsplit(":", 1)[0] for pair in eligible_pairs if ":" in pair}
    return {
        "all_article_pairs": len(pairs),
        "all_slugs": len(slugs),
        "matrix_pairs": len(matrix_pairs),
        "eligible_article_pairs": len(eligible_pairs),
        "eligible_slugs": len(eligible_slugs),
    }


def _accepted_article_support_pair_counts(limit: int = 10) -> list[int]:
    manifests: list[tuple[datetime, dict]] = []
    for path in ARTICLE_AUTOPILOT_DIR.glob("article_autopilot_improvement_manifest_*.json"):
        manifest = _safe_read_json(path)
        if not manifest:
            continue
        if manifest.get("decision") not in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS:
            continue
        created_at = _parse_datetime(manifest.get("created_at")) or datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        )
        manifests.append((created_at, manifest))
    counts: list[int] = []
    for _created_at, manifest in sorted(manifests, key=lambda item: item[0])[-limit:]:
        try:
            value = int(((manifest.get("article_support") or {}).get("article_pair_count")) or 0)
        except Exception:
            value = 0
        if value > 0:
            counts.append(value)
    return counts


def _accepted_improvement_cycle_seconds(limit: int = 10) -> float | None:
    created_values: list[datetime] = []
    for path in ARTICLE_AUTOPILOT_DIR.glob("article_autopilot_improvement_manifest_*.json"):
        manifest = _safe_read_json(path)
        if not manifest:
            continue
        if manifest.get("decision") not in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS:
            continue
        created_at = _parse_datetime(manifest.get("created_at")) or datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        )
        created_values.append(created_at)
    recent = sorted(created_values)[-limit:]
    intervals = [
        (recent[index] - recent[index - 1]).total_seconds()
        for index in range(1, len(recent))
        if (recent[index] - recent[index - 1]).total_seconds() > 0
    ]
    if not intervals:
        return None
    return sum(intervals) / len(intervals)


def _horizontal_coverage_snapshot(
    development_summary: dict | None = None,
    round_support_summary: dict | None = None,
    improvement: dict | None = None,
) -> dict:
    totals = _article_catalog_totals()
    support_manifest = _safe_read_json(ARTICLE_AUTOPILOT_SUPPORT_MANIFEST_PATH)
    supported_pairs = int(support_manifest.get("article_pair_count") or 0)
    supported_slugs = int(support_manifest.get("unique_article_slugs") or 0)
    eligible_pairs = int(totals.get("eligible_article_pairs") or totals.get("all_article_pairs") or 0)
    eligible_slugs = int(totals.get("eligible_slugs") or totals.get("all_slugs") or 0)
    supported_pairs = min(supported_pairs, eligible_pairs) if eligible_pairs else supported_pairs
    supported_slugs = min(supported_slugs, eligible_slugs) if eligible_slugs else supported_slugs
    remaining_pairs = max(0, eligible_pairs - supported_pairs)
    pair_percent = round((supported_pairs / eligible_pairs) * 100, 1) if eligible_pairs else 0.0
    slug_percent = round((supported_slugs / eligible_slugs) * 100, 1) if eligible_slugs else 0.0
    round_support_summary = round_support_summary or {}
    improvement = improvement or {}
    recent_new_rate = _percent_value(round_support_summary.get("unsupported_expected_rate")) or 0.0
    recent_supported_gap_rate = _percent_value(round_support_summary.get("supported_missing_rate")) or 0.0
    recent_route_gap_rate = _percent_value(round_support_summary.get("unrouted_expected_rate")) or 0.0
    recent_signal = round(
        max(
            0.0,
            100.0
            - (recent_new_rate * 0.45)
            - (recent_supported_gap_rate * 0.35)
            - (recent_route_gap_rate * 0.20),
        ),
        1,
    )
    stable_quality = _stable_quality_snapshot()
    frontier_gap_percent = round(max(0.0, 100.0 - recent_signal), 1)
    if recent_signal >= 85.0:
        frontier_status = "استكشاف هادئ"
    elif recent_signal >= 70.0:
        frontier_status = "استكشاف يكشف فجوات جديدة"
    else:
        frontier_status = "استكشاف حاد يحتاج دفعة تحسين"
    fixed_holdout_score = _percent_value(improvement.get("fixed_holdout_score"))
    fixed_holdout_pass_rate = _percent_value(improvement.get("fixed_holdout_pass_rate"))
    fixed_holdout_axis = _percent_value(improvement.get("fixed_holdout_axis_coverage_rate"))
    fixed_holdout_governing = _percent_value(improvement.get("fixed_holdout_governing_system_rate"))
    fixed_holdout_context = _percent_value(improvement.get("fixed_holdout_context_entry_rate"))
    fixed_holdout_source = improvement.get("fixed_holdout_source") or "—"
    holdout_score = _percent_value(improvement.get("holdout_score"))
    holdout_pass_rate = _percent_value(improvement.get("holdout_pass_rate"))
    holdout_axis = _percent_value(improvement.get("holdout_axis_coverage_rate"))
    holdout_governing = _percent_value(improvement.get("holdout_governing_system_rate"))
    holdout_context = _percent_value(improvement.get("holdout_context_entry_rate"))
    weighted_components: list[tuple[float, float]] = [
        (pair_percent, 0.45),
        (recent_signal, 0.25),
    ]
    if fixed_holdout_score is not None:
        weighted_components.append((fixed_holdout_score, 0.20))
    if fixed_holdout_axis is not None:
        weighted_components.append((fixed_holdout_axis, 0.10))
    weight_total = sum(weight for _value, weight in weighted_components) or 1.0
    practical_percent = round(
        sum(value * weight for value, weight in weighted_components) / weight_total,
        1,
    )

    counts = _accepted_article_support_pair_counts()
    gains = [
        counts[index] - counts[index - 1]
        for index in range(1, len(counts))
        if counts[index] - counts[index - 1] > 0
    ]
    recent_gain = round(sum(gains) / len(gains), 1) if gains else 0.0
    phases_remaining = None
    if recent_gain > 0 and remaining_pairs > 0:
        phases_remaining = int((remaining_pairs / recent_gain) + 0.999)
    completed_phases = int((development_summary or {}).get("accepted_cycles") or 0)
    estimated_total_phases = completed_phases + phases_remaining if phases_remaining is not None else None
    recent_phase_seconds = _accepted_improvement_cycle_seconds()
    remaining_seconds = (
        phases_remaining * recent_phase_seconds
        if phases_remaining is not None and recent_phase_seconds
        else None
    )
    if practical_percent >= 90 and (fixed_holdout_axis is None or fixed_holdout_axis >= 85):
        stage = "جاهز غالبًا للتركيز العمودي"
    elif pair_percent >= 95 and practical_percent < 80:
        stage = "النظري مكتمل؛ العملي يحتاج توجيهًا أفقيًا"
    elif practical_percent >= 80:
        stage = "قريب من اكتمال الأفقي العملي"
    elif pair_percent >= 80:
        stage = "تغطية نظرية عالية مع فجوات عملية"
    else:
        stage = "تغطية أفقية نشطة"
    return {
        "supported_article_pairs": supported_pairs,
        "eligible_article_pairs": eligible_pairs,
        "remaining_article_pairs": remaining_pairs,
        "stable_quality_score": stable_quality.get("score"),
        "stable_quality_label": stable_quality.get("score_label"),
        "stable_quality_class": stable_quality.get("score_class"),
        "stable_quality_status": stable_quality.get("status"),
        "stable_quality_cases": stable_quality.get("cases_total"),
        "stable_quality_failed_cases": stable_quality.get("failed_cases"),
        "stable_quality_transport_errors": stable_quality.get("transport_error_cases"),
        "stable_quality_latest_evaluated_at": stable_quality.get("latest_evaluated_at"),
        "stable_quality_components": stable_quality.get("components") or [],
        "stable_quality_method": stable_quality.get("method"),
        "frontier_signal_percent": recent_signal,
        "frontier_signal_label": f"{recent_signal}%",
        "frontier_signal_class": _quality_number_class(recent_signal),
        "frontier_gap_percent": frontier_gap_percent,
        "frontier_gap_label": f"{frontier_gap_percent}%",
        "frontier_status": frontier_status,
        "frontier_method": "آخر الجولات تقيس حدود التوسعة فقط، وليست حكمًا على النسخة المستقرة",
        "practical_percent": practical_percent,
        "practical_percent_label": f"{practical_percent}%",
        "recent_signal_percent": recent_signal,
        "recent_signal_label": f"{recent_signal}%",
        "recent_new_rate": recent_new_rate,
        "recent_new_rate_label": f"{recent_new_rate}%",
        "recent_supported_gap_rate": recent_supported_gap_rate,
        "recent_supported_gap_label": f"{recent_supported_gap_rate}%",
        "recent_route_gap_rate": recent_route_gap_rate,
        "recent_route_gap_label": f"{recent_route_gap_rate}%",
        "fixed_holdout_score": fixed_holdout_score,
        "fixed_holdout_score_label": f"{fixed_holdout_score}%" if fixed_holdout_score is not None else "—",
        "fixed_holdout_pass_rate": fixed_holdout_pass_rate,
        "fixed_holdout_pass_rate_label": f"{fixed_holdout_pass_rate}%" if fixed_holdout_pass_rate is not None else "—",
        "fixed_holdout_axis_coverage_rate": fixed_holdout_axis,
        "fixed_holdout_axis_coverage_label": f"{fixed_holdout_axis}%" if fixed_holdout_axis is not None else "—",
        "fixed_holdout_governing_system_rate": fixed_holdout_governing,
        "fixed_holdout_governing_system_label": f"{fixed_holdout_governing}%" if fixed_holdout_governing is not None else "—",
        "fixed_holdout_context_entry_rate": fixed_holdout_context,
        "fixed_holdout_context_entry_label": f"{fixed_holdout_context}%" if fixed_holdout_context is not None else "—",
        "fixed_holdout_source": fixed_holdout_source,
        "holdout_score": holdout_score,
        "holdout_score_label": f"{holdout_score}%" if holdout_score is not None else "—",
        "holdout_pass_rate": holdout_pass_rate,
        "holdout_pass_rate_label": f"{holdout_pass_rate}%" if holdout_pass_rate is not None else "—",
        "holdout_axis_coverage_rate": holdout_axis,
        "holdout_axis_coverage_label": f"{holdout_axis}%" if holdout_axis is not None else "—",
        "holdout_governing_system_rate": holdout_governing,
        "holdout_governing_system_label": f"{holdout_governing}%" if holdout_governing is not None else "—",
        "holdout_context_entry_rate": holdout_context,
        "holdout_context_entry_label": f"{holdout_context}%" if holdout_context is not None else "—",
        "pair_percent": pair_percent,
        "pair_percent_label": f"{pair_percent}%",
        "supported_slugs": supported_slugs,
        "eligible_slugs": eligible_slugs,
        "slug_percent": slug_percent,
        "slug_percent_label": f"{slug_percent}%",
        "recent_pairs_per_phase": recent_gain,
        "recent_pairs_per_phase_label": f"{recent_gain}/دفعة" if recent_gain else "—",
        "recent_phase_seconds": round(recent_phase_seconds, 1) if recent_phase_seconds else None,
        "recent_phase_duration_label": _format_hours_minutes(recent_phase_seconds),
        "remaining_seconds_estimate": round(remaining_seconds, 1) if remaining_seconds else None,
        "remaining_time_label": _format_hours_minutes(remaining_seconds),
        "phases_completed": completed_phases,
        "phases_remaining_estimate": phases_remaining,
        "estimated_total_phases": estimated_total_phases,
        "stage": stage,
        "matrix_pairs": totals.get("matrix_pairs") or 0,
        "method": "45% تغطية نظرية + 25% آخر الجولات + 30% هولداوت ثابت عند توفره؛ المتحرك للاستكشاف فقط",
    }


def _append_deferred_improvement_cases(manifest: dict, manifest_path: Path, reason: str) -> int:
    validation_path = Path(str(manifest.get("validation_gate_path") or ""))
    gate = _safe_read_json(validation_path)
    failed_rows = [
        row for row in gate.get("rows") or []
        if not row.get("passed") and not row.get("transport_error")
    ]
    if not failed_rows:
        return 0
    ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH.open("a", encoding="utf-8") as handle:
        for row in failed_rows:
            handle.write(
                json.dumps(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "source_manifest": _relative_project_path(manifest_path),
                        "reason": reason,
                        "question_id": row.get("question_id"),
                        "domain": row.get("domain"),
                        "article_points": row.get("article_points"),
                        "missing_article_pairs": row.get("missing_article_pairs") or [],
                        "missing_core_regulations": row.get("missing_core_regulations") or [],
                        "missing_implementing_regulations": row.get("missing_implementing_regulations") or [],
                        "failed_axes": row.get("failed_axes") or [],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return len(failed_rows)


def _install_improvement_artifacts_from_manifest(manifest: dict) -> None:
    from scripts.run_article_autopilot_improvement import (
        ARTICLE_SUPPORT_PATH,
        ROUTER_SUPPORT_PATH,
        ROUTER_TABLE_PATH,
        copy_candidate,
    )

    router_support = Path(str((manifest.get("router_support") or {}).get("output") or ""))
    router_table = Path(str((manifest.get("router_table") or {}).get("output") or ""))
    article_support = Path(str((manifest.get("article_support") or {}).get("output") or ""))
    if not (router_support.exists() and router_table.exists() and article_support.exists()):
        raise RuntimeError("لا توجد artifacts مرشحة قابلة للاعتماد في سجل التحسين.")
    copy_candidate(router_support, ROUTER_SUPPORT_PATH)
    copy_candidate(router_table, ROUTER_TABLE_PATH)
    copy_candidate(article_support, ARTICLE_SUPPORT_PATH)


def _article_pairs_from_case(case: dict) -> list[str]:
    pairs = []
    for slug, articles in (case.get("expected_articles_by_slug") or {}).items():
        for article in articles or []:
            pairs.append(f"{slug}:{article}")
    return pairs


def _normalize_pair_key(value) -> str:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return ""
    slug, article = text.rsplit(":", 1)
    try:
        article_index = int(article)
    except Exception:
        return ""
    if article_index <= 0 or not slug:
        return ""
    return f"{slug}:{article_index}"


def _load_current_article_support_pairs() -> set[str]:
    try:
        mtime = ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH.stat().st_mtime
    except OSError:
        return set()
    cached_mtime = float(ARTICLE_SUPPORT_PAIR_CACHE.get("mtime") or 0.0)
    cached_pairs = ARTICLE_SUPPORT_PAIR_CACHE.get("pairs")
    if cached_mtime == mtime and isinstance(cached_pairs, set):
        return set(cached_pairs)
    try:
        import joblib

        artifact = joblib.load(ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH)
    except Exception as exc:
        logger.warning("تعذر قراءة جدول دعم المواد لحساب فجوات الجولات: %s", exc)
        return set()
    pairs: set[str] = set()
    for row in artifact.get("rows") or []:
        for slug, articles in (row.get("expected_articles_by_slug") or {}).items():
            for article in articles or []:
                pair = _normalize_pair_key(f"{slug}:{article}")
                if pair:
                    pairs.add(pair)
    ARTICLE_SUPPORT_PAIR_CACHE["mtime"] = mtime
    ARTICLE_SUPPORT_PAIR_CACHE["pairs"] = pairs
    return set(pairs)


def _round_support_gap_stats(gate_rows: list[dict], support_pairs: Optional[set[str]] = None) -> dict:
    support_pairs = support_pairs if support_pairs is not None else _load_current_article_support_pairs()
    expected_pairs: set[str] = set()
    missing_pairs: set[str] = set()
    unrouted_pairs: set[str] = set()
    for row in gate_rows or []:
        if row.get("transport_error"):
            continue
        for value in row.get("expected_article_pairs") or []:
            pair = _normalize_pair_key(value)
            if pair:
                expected_pairs.add(pair)
        for value in row.get("missing_article_pairs") or []:
            pair = _normalize_pair_key(value)
            if pair:
                missing_pairs.add(pair)
        for value in row.get("unrouted_expected_article_pairs") or []:
            pair = _normalize_pair_key(value)
            if pair:
                unrouted_pairs.add(pair)
    unsupported_expected = expected_pairs - support_pairs
    supported_missing = (missing_pairs | unrouted_pairs) & support_pairs
    supported_route_gaps = unrouted_pairs & support_pairs
    total = len(expected_pairs)
    unsupported_rate = round((len(unsupported_expected) / total) * 100, 1) if total else 0.0
    supported_missing_rate = round((len(supported_missing) / total) * 100, 1) if total else 0.0
    route_gap_rate = round((len(unrouted_pairs) / total) * 100, 1) if total else 0.0
    supported_route_gap_rate = round((len(supported_route_gaps) / total) * 100, 1) if total else 0.0
    return {
        "expected_article_pairs": total,
        "unsupported_expected_pairs": len(unsupported_expected),
        "unsupported_expected_rate": unsupported_rate,
        "unsupported_expected_label": f"{unsupported_rate}%",
        "supported_missing_pairs": len(supported_missing),
        "supported_missing_rate": supported_missing_rate,
        "supported_missing_label": f"{supported_missing_rate}%",
        "unrouted_expected_pairs": len(unrouted_pairs),
        "unrouted_expected_rate": route_gap_rate,
        "unrouted_expected_label": f"{route_gap_rate}%",
        "supported_route_gap_pairs": len(supported_route_gaps),
        "supported_route_gap_rate": supported_route_gap_rate,
        "supported_route_gap_label": f"{supported_route_gap_rate}%",
    }


def _load_autopilot_success_rows(bank_path: Path, limit: int = 8) -> list[dict]:
    rows = []
    for item in _safe_read_jsonl(bank_path):
        auto_review = item.get("auto_review") or {}
        regulations = [
            *(item.get("expected_core_regulations") or []),
            *(item.get("expected_implementing_regulations") or []),
            *(item.get("expected_companion_regulations") or []),
        ]
        article_pairs = _article_pairs_from_case(item)
        rows.append(
            {
                "question_id": item.get("question_id"),
                "domain": item.get("domain") or "غير مصنف",
                "question": str(item.get("question") or "")[:220],
                "status": "نجح",
                "article_points": auto_review.get("gate_article_points", 100.0),
                "collected": (
                    f"اللوائح/الأنظمة: {_short_join(regulations, limit=5)} | "
                    f"المواد: {_short_join(article_pairs, limit=5)}"
                ),
                "comment": (
                    "ترقّى تلقائيًا بعد تحقق Qwen الموثوق ونجاح gate. "
                    f"النماذج: {_short_join(auto_review.get('valid_teacher_models') or auto_review.get('teacher_models') or [], limit=3)}"
                ),
                "action": "أضيف إلى بنك التوسعة وسيُستخدم في مصفوفة التدقيق التالية.",
                "review_status": auto_review.get("status") or "auto_promoted",
                "promoted_at": auto_review.get("promoted_at") or auto_review.get("generated_at") or "",
            }
        )
    rows.sort(key=lambda row: row.get("promoted_at") or "", reverse=True)
    return rows[:limit]


def _load_autopilot_round_history(limit: int = 12) -> list[dict]:
    history = []
    support_pairs = _load_current_article_support_pairs()
    for path in _latest_autopilot_manifests(limit=limit):
        manifest = _safe_read_json(path)
        gate_summary = manifest.get("gate_summary") or {}
        gate_report = _safe_read_json(_path_from_manifest(manifest, "gate"))
        gate_rows = gate_report.get("rows") or []
        score_stats = _article_score_stats(gate_rows)
        support_gap_stats = _round_support_gap_stats(gate_rows, support_pairs)
        promotion = manifest.get("promotion") or {}
        failed_cases = int(gate_summary.get("failed_cases", 0) or 0)
        transport_errors = int(gate_summary.get("transport_error_cases", 0) or 0)
        promoted_count = int(promotion.get("promoted_count", 0) or 0)
        held_count = int(promotion.get("held_for_review_count", 0) or 0)
        decision = manifest.get("gap_decision")
        if not decision and gate_summary:
            decision = "PASS" if failed_cases == 0 and transport_errors == 0 else "FAIL"
        round_number = manifest.get("round_number")
        round_label = f"الجولة {round_number}" if round_number else path.stem.replace("article_autopilot_manifest_", "")
        history.append(
            {
                "round": round_label,
                "decision": decision or "—",
                "article_score": gate_summary.get("article_score_100", "—"),
                "score_average": gate_summary.get("article_score_100", score_stats["average"]),
                "pass_rate": gate_summary.get("pass_rate", "—"),
                "failed_cases": failed_cases,
                "near_miss_count": score_stats["near_miss"],
                "partial_count": score_stats["partial"],
                "low_count": score_stats["low"],
                "transport_errors": transport_errors,
                "promoted_count": promoted_count,
                "held_for_review_count": held_count,
                "expected_article_pairs": support_gap_stats["expected_article_pairs"],
                "unsupported_expected_pairs": support_gap_stats["unsupported_expected_pairs"],
                "unsupported_expected_rate": support_gap_stats["unsupported_expected_rate"],
                "unsupported_expected_label": support_gap_stats["unsupported_expected_label"],
                "supported_missing_pairs": support_gap_stats["supported_missing_pairs"],
                "supported_missing_rate": support_gap_stats["supported_missing_rate"],
                "supported_missing_label": support_gap_stats["supported_missing_label"],
                "unrouted_expected_pairs": support_gap_stats["unrouted_expected_pairs"],
                "unrouted_expected_rate": support_gap_stats["unrouted_expected_rate"],
                "unrouted_expected_label": support_gap_stats["unrouted_expected_label"],
                "supported_route_gap_pairs": support_gap_stats["supported_route_gap_pairs"],
                "supported_route_gap_rate": support_gap_stats["supported_route_gap_rate"],
                "supported_route_gap_label": support_gap_stats["supported_route_gap_label"],
                "duration_label": _format_duration(manifest.get("duration_seconds")),
                "created_at": manifest.get("created_at") or "",
                "summary": (
                    f"متوسط {gate_summary.get('article_score_100', score_stats['average'])}/100، "
                    f"قريب جدًا {score_stats['near_miss']}، فشل {failed_cases}، "
                    f"جديد خارج الدعم {support_gap_stats['unsupported_expected_label']}، "
                    f"مدعوم تعثر {support_gap_stats['supported_missing_label']}، "
                    f"غير موجه {support_gap_stats['unrouted_expected_label']}، "
                    f"مراجعة {held_count}، تشغيل {transport_errors}"
                ),
            }
        )
    return history


def _round_history_support_summary(rows: list[dict]) -> dict:
    valid = [row for row in rows if int(row.get("expected_article_pairs") or 0) > 0]
    if not valid:
        return {
            "rounds": 0,
            "unsupported_expected_rate": 0.0,
            "unsupported_expected_label": "—",
            "supported_missing_rate": 0.0,
            "supported_missing_label": "—",
            "unrouted_expected_rate": 0.0,
            "unrouted_expected_label": "—",
            "supported_route_gap_rate": 0.0,
            "supported_route_gap_label": "—",
        }
    unsupported_rate = round(
        sum(float(row.get("unsupported_expected_rate") or 0.0) for row in valid) / len(valid),
        1,
    )
    supported_missing_rate = round(
        sum(float(row.get("supported_missing_rate") or 0.0) for row in valid) / len(valid),
        1,
    )
    unrouted_expected_rate = round(
        sum(float(row.get("unrouted_expected_rate") or 0.0) for row in valid) / len(valid),
        1,
    )
    supported_route_gap_rate = round(
        sum(float(row.get("supported_route_gap_rate") or 0.0) for row in valid) / len(valid),
        1,
    )
    return {
        "rounds": len(valid),
        "unsupported_expected_rate": unsupported_rate,
        "unsupported_expected_label": f"{unsupported_rate}%",
        "supported_missing_rate": supported_missing_rate,
        "supported_missing_label": f"{supported_missing_rate}%",
        "unrouted_expected_rate": unrouted_expected_rate,
        "unrouted_expected_label": f"{unrouted_expected_rate}%",
        "supported_route_gap_rate": supported_route_gap_rate,
        "supported_route_gap_label": f"{supported_route_gap_rate}%",
    }


def _load_article_autopilot_snapshot() -> dict:
    manifest_path = _latest_autopilot_manifest()
    manifest = _safe_read_json(manifest_path)
    bank_path = PROJECT_ROOT / "data" / "eval" / "article_autopilot" / "autopilot_article_precision_bank.jsonl"
    bank_count = 0
    if bank_path.exists():
        try:
            bank_count = sum(1 for line in bank_path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            bank_count = 0
    gate_summary = manifest.get("gate_summary") or {}
    promotion = manifest.get("promotion") or {}
    classification_counts = manifest.get("classification_counts") or {}
    reason_counts = manifest.get("reason_counts") or {}
    gap_summary_path = _path_from_manifest(manifest, "summary")
    probes_path = _path_from_manifest(manifest, "probes")
    gate_path = _path_from_manifest(manifest, "gate")
    gap_summary = _safe_read_json(gap_summary_path)
    gate_report = _safe_read_json(gate_path)
    gate_rows = gate_report.get("rows") or []
    score_stats = _article_score_stats(gate_rows)
    probes_by_id = {row.get("question_id"): row for row in _safe_read_jsonl(probes_path)}
    findings_by_id = {
        item.get("question_id"): item
        for item in (gap_summary.get("blocking_findings") or [])
        if item.get("question_id")
    }
    promoted_ids = set((promotion.get("promoted_question_ids") or []))
    findings = []
    for item in (gap_summary.get("blocking_findings") or [])[:8]:
        probe = probes_by_id.get(item.get("question_id")) or {}
        question = str(probe.get("question") or "")[:240]
        auto_review = probe.get("auto_review") or {}
        findings.append(
            {
                "question_id": item.get("question_id"),
                "domain": item.get("domain"),
                "classification": item.get("classification"),
                "reason": item.get("reason"),
                "article_points": item.get("article_points"),
                "missing_article_pairs": item.get("missing_article_pairs") or [],
                "missing_core_regulations": item.get("missing_core_regulations") or [],
                "missing_implementing_regulations": item.get("missing_implementing_regulations") or [],
                "failed_axes": item.get("failed_axes") or [],
                "question": question,
                "review_status": auto_review.get("status"),
                "valid_teacher_models": auto_review.get("valid_teacher_models") or [],
            }
        )
    task_rows = []
    for row in gate_rows[:20]:
        qid = row.get("question_id")
        probe = probes_by_id.get(qid) or {}
        finding = findings_by_id.get(qid) or {}
        missing = (
            row.get("missing_article_pairs")
            or row.get("missing_implementing_regulations")
            or row.get("missing_core_regulations")
            or []
        )
        covered_regs = _short_join(
            [
                *(row.get("covered_core_regulations") or []),
                *(row.get("covered_implementing_regulations") or []),
                *(row.get("covered_companion_regulations") or []),
            ],
            limit=5,
        )
        covered_articles = _short_join(row.get("covered_article_pairs") or [], limit=5)
        auto_review = probe.get("auto_review") or {}
        is_operational = bool(row.get("transport_error"))
        article_points = _points(row.get("article_points"))
        score_label = _score_bucket_label(article_points, bool(row.get("passed")), is_operational)
        if is_operational:
            comment = "فشل تشغيلي/اتصال، لا يُحسب فجوة RAG."
        elif row.get("passed"):
            comment = "جمع مكتمل: النظام/اللائحة/المواد المطلوبة حاضرة."
        elif article_points >= 90.0:
            comment = (
                f"قريب جدًا من النجاح؛ بقيت فجوة محدودة: {_short_join(missing, limit=4)}"
            )
        else:
            comment = (
                f"{finding.get('classification') or 'retrieval/package issue'} / "
                f"{finding.get('reason') or 'missing material'}؛ المفقود: {_short_join(missing, limit=4)}"
            )
        task_rows.append(
            {
                "question_id": qid,
                "domain": row.get("domain") or probe.get("domain") or "غير مصنف",
                "question": str(probe.get("question") or row.get("question") or "")[:220],
                "status": "تشغيلي" if is_operational else "نجح" if row.get("passed") else "فشل",
                "score_label": score_label,
                "is_operational": is_operational,
                "article_points": row.get("article_points"),
                "collected": f"اللوائح/الأنظمة: {covered_regs} | المواد: {covered_articles}",
                "comment": comment,
                "action": _autopilot_action_for(row, finding, probe, promoted_ids),
                "review_status": auto_review.get("status") or "—",
            }
        )
    task_total = sum(1 for item in task_rows if not item.get("is_operational"))
    task_successes = sum(1 for item in task_rows if item.get("status") == "نجح")
    task_operational = sum(1 for item in task_rows if item.get("is_operational"))
    task_success_rate = round((task_successes / task_total) * 100, 1) if task_total else 0.0
    transport_errors = int(gate_summary.get("transport_error_cases", 0) or 0)
    failed_cases = int(gate_summary.get("failed_cases", 0) or 0)
    decision = manifest.get("gap_decision")
    if not decision and gate_summary:
        decision = "PASS" if failed_cases == 0 and transport_errors == 0 else "FAIL"
    duration_seconds = manifest.get("duration_seconds")
    round_config = manifest.get("round_config") or {}
    candidate_count = int(manifest.get("candidate_count", round_config.get("candidate_count", 0)) or 0)
    max_articles = int(round_config.get("max_articles_per_case", 3) or 3)
    development_cycle_rows = _load_development_cycle_rows(limit=50)
    development_cycle_summary = _load_development_cycle_summary(development_cycle_rows)
    round_history = _load_autopilot_round_history()
    round_support_summary = _round_history_support_summary(round_history)
    current_support_gap_stats = _round_support_gap_stats(gate_rows)
    improvement = _load_autopilot_improvement_snapshot()
    horizontal_coverage = _horizontal_coverage_snapshot(
        development_cycle_summary,
        round_support_summary=round_support_summary,
        improvement=improvement,
    )
    return {
        "manifest": manifest,
        "manifest_path": _relative_project_path(manifest_path) if manifest_path else "",
        "bank_path": _relative_project_path(bank_path) if bank_path.exists() else "",
        "gap_summary_path": _relative_project_path(gap_summary_path) if gap_summary_path else "",
        "probes_path": _relative_project_path(probes_path) if probes_path else "",
        "gate_path": _relative_project_path(gate_path) if gate_path else "",
        "bank_count": bank_count,
        "decision": decision or "لم يعمل بعد",
        "article_score": gate_summary.get("article_score_100", "—"),
        "score_average": gate_summary.get("article_score_100", score_stats["average"]),
        "near_miss_count": score_stats["near_miss"],
        "partial_count": score_stats["partial"],
        "low_count": score_stats["low"],
        "pass_rate": gate_summary.get("pass_rate", "—"),
        "cases_total": gate_summary.get("cases_total", candidate_count or "—"),
        "transport_errors": transport_errors,
        "failed_cases": failed_cases,
        "promoted_count": int(promotion.get("promoted_count", 0) or 0),
        "held_for_review_count": int(promotion.get("held_for_review_count", 0) or 0),
        "candidate_count": candidate_count,
        "max_articles_per_case": max_articles,
        "round_size_label": f"{candidate_count or 0} مرشح × حتى {max_articles} مواد لكل مرشح",
        "duration_label": _format_duration(duration_seconds),
        "classification_counts": classification_counts,
        "reason_counts": reason_counts,
        "domain_reason_counts": gap_summary.get("domain_reason_counts") or {},
        "top_failed_axes": gap_summary.get("top_failed_axes") or [],
        "top_missing_article_pairs": gap_summary.get("top_missing_article_pairs") or [],
        "blocking_findings": findings,
        "task_rows": task_rows,
        "success_rows": _load_autopilot_success_rows(bank_path),
        "round_history": round_history,
        "round_support_summary": round_support_summary,
        "current_round_support_gap": current_support_gap_stats,
        "improvement": improvement,
        "development_cycle_rows": development_cycle_rows,
        "development_cycle_summary": development_cycle_summary,
        "horizontal_coverage": horizontal_coverage,
        "task_total": task_total,
        "task_successes": task_successes,
        "task_operational": task_operational,
        "task_success_rate": task_success_rate,
        "created_at": manifest.get("created_at") or "لم يعمل بعد",
    }


def _render_article_autopilot_card(snapshot: dict) -> str:
    decision = snapshot.get("decision") or "لم يعمل بعد"
    badge_class = (
        "ok"
        if decision in {"PASS", *ARTICLE_AUTOPILOT_CONTINUE_DECISIONS}
        else "danger"
        if decision in {"FAIL", "REJECTED_ROLLED_BACK"}
        else "warn"
    )
    classification_counts = snapshot.get("classification_counts") or {}
    reason_counts = snapshot.get("reason_counts") or {}
    findings = snapshot.get("blocking_findings") or []
    task_rows = snapshot.get("task_rows") or []
    success_rows = snapshot.get("success_rows") or []
    round_history = snapshot.get("round_history") or []
    improvement = snapshot.get("improvement") or {}
    development_cycle_rows = snapshot.get("development_cycle_rows") or []
    development_summary = snapshot.get("development_cycle_summary") or {}
    horizontal_coverage = snapshot.get("horizontal_coverage") or {}
    round_support_summary = snapshot.get("round_support_summary") or {}
    current_support_gap = snapshot.get("current_round_support_gap") or {}
    horizontal_percent = _points(
        horizontal_coverage.get("stable_quality_score")
        if horizontal_coverage.get("stable_quality_score") is not None
        else horizontal_coverage.get("practical_percent", horizontal_coverage.get("pair_percent"))
    )
    horizontal_class = horizontal_coverage.get("stable_quality_class") or _quality_number_class(horizontal_percent)
    frontier_percent = _points(
        horizontal_coverage.get("frontier_signal_percent", horizontal_coverage.get("recent_signal_percent"))
    )
    frontier_class = horizontal_coverage.get("frontier_signal_class") or _quality_number_class(frontier_percent)
    horizontal_width = max(0.0, min(100.0, horizontal_percent))
    stable_components = horizontal_coverage.get("stable_quality_components") or []
    stable_component_chips = "".join(
        f"<span class='chip'>{_escape(item.get('label') or item.get('key') or 'gate')}: "
        f"{_escape(item.get('score_label') or '—')} · "
        f"{_escape(item.get('cases_total') or 0)} حالة · فشل {_escape(item.get('failed_cases') or 0)}</span>"
        for item in stable_components
    )
    failed_axes = snapshot.get("top_failed_axes") or []
    missing_pairs = snapshot.get("top_missing_article_pairs") or []
    report_links = []
    for label, path in (
        ("آخر manifest", snapshot.get("manifest_path")),
        ("تقرير gate", snapshot.get("gate_path")),
        ("ملخص الفجوات", snapshot.get("gap_summary_path")),
        ("مرشحات الجولة", snapshot.get("probes_path")),
        ("بنك التوسعة", snapshot.get("bank_path")),
    ):
        if path:
            report_links.append(f"<span class='chip'>{_escape(label)}: {_escape(path)}</span>")
    finding_items = []
    for item in findings:
        missing = ", ".join(item.get("missing_article_pairs") or item.get("missing_implementing_regulations") or item.get("missing_core_regulations") or [])
        axes = ", ".join(item.get("failed_axes") or [])
        teachers = ", ".join(item.get("valid_teacher_models") or [])
        finding_items.append(
            f"""
            <div class="finding-item">
              <div class="finding-title">{_escape(item.get("domain") or "غير مصنف")} · {_escape(item.get("classification") or "")} · {_escape(item.get("reason") or "")}</div>
              <div class="muted">النقاط: {_escape(item.get("article_points"))} · المحور: {_escape(axes or "—")} · نماذج صالحة: {_escape(teachers or "—")}</div>
              <div class="muted">المفقود: {_escape(missing or "—")}</div>
              <div>{_escape(item.get("question") or "")}</div>
            </div>
            """
        )
    if not finding_items:
        finding_items.append("<div class='finding-item'><div class='finding-title'>لا توجد فجوات حاجبة في آخر جولة.</div></div>")
    task_table_rows = []
    for item in task_rows:
        if item.get("status") == "نجح":
            status_class = "ok"
        elif item.get("status") == "تشغيلي":
            status_class = "warn"
        else:
            status_class = "danger"
        task_table_rows.append(
            f"""
            <tr>
              <td>
                <div class="task-title">{_escape(item.get("domain"))}</div>
                <div class="muted">{_escape(item.get("question"))}</div>
                <div class="chips"><span class="chip">{_escape(item.get("question_id"))}</span><span class="badge {status_class}">{_escape(item.get("status"))}</span><span class="chip">{_escape(item.get("score_label"))}</span></div>
              </td>
              <td>{_escape(item.get("collected"))}<div class="muted">درجة الاقتراب: {_escape(item.get("article_points"))}/100</div></td>
              <td>{_escape(item.get("comment"))}<div class="muted">حالة النماذج: {_escape(item.get("review_status"))}</div></td>
              <td>{_escape(item.get("action"))}</td>
            </tr>
            """
        )
    if not task_table_rows:
        task_table_rows.append(
            "<tr><td colspan='4'>لا توجد قضايا في آخر جولة بعد.</td></tr>"
        )
    task_total = int(snapshot.get("task_total", 0) or 0)
    task_success = int(snapshot.get("task_successes", 0) or 0)
    task_operational = int(snapshot.get("task_operational", 0) or 0)
    task_rate = snapshot.get("task_success_rate", 0.0)
    score_average = snapshot.get("score_average", snapshot.get("article_score", "—"))
    near_miss = int(snapshot.get("near_miss_count", 0) or 0)
    partial_count = int(snapshot.get("partial_count", 0) or 0)
    low_count = int(snapshot.get("low_count", 0) or 0)
    task_success_footer = (
        f"متوسط الاقتراب: {score_average}/100 · نجاح الجمع: {task_success} / {task_total} = {task_rate}%"
        if task_total
        else "معدل النجاح: لا توجد قضايا بعد"
    )
    if task_total:
        task_success_footer = (
            f"{task_success_footer} · قريب جدًا: {near_miss} · جزئي: {partial_count} · بعيد: {low_count}"
        )
    if task_operational:
        task_success_footer = f"{task_success_footer} · أعطال تشغيلية مستبعدة: {task_operational}"
    success_table_rows = []
    for item in success_rows:
        success_table_rows.append(
            f"""
            <tr>
              <td>
                <div class="task-title">{_escape(item.get("domain"))}</div>
                <div class="muted">{_escape(item.get("question"))}</div>
                <div class="chips"><span class="chip">{_escape(item.get("question_id"))}</span><span class="badge ok">نجح</span></div>
              </td>
              <td>{_escape(item.get("collected"))}<div class="muted">درجة الاقتراب: {_escape(item.get("article_points"))}/100</div></td>
              <td>{_escape(item.get("comment"))}<div class="muted">حالة النماذج: {_escape(item.get("review_status"))}</div></td>
              <td>{_escape(item.get("action"))}</td>
            </tr>
            """
        )
    if not success_table_rows:
        success_table_rows.append("<tr><td colspan='4'>لا توجد ترقيات ناجحة في بنك التوسعة بعد.</td></tr>")
    history_table_rows = []
    for item in round_history:
        decision = item.get("decision")
        if decision == "PASS":
            decision_class = "ok"
        elif decision == "FAIL":
            decision_class = "danger"
        else:
            decision_class = "warn"
        history_table_rows.append(
            f"""
            <tr>
              <td><div class="task-title">{_escape(item.get("round"))}</div><div class="muted">{_escape(item.get("created_at"))}</div></td>
              <td><span class="badge {decision_class}">{_escape(decision)}</span></td>
              <td>
                متوسط الاقتراب: {_escape(item.get("score_average"))}/100
                <div class="muted">نجاح: {_escape(item.get("pass_rate"))} · قريب جدًا: {_escape(item.get("near_miss_count"))} · المدة: {_escape(item.get("duration_label"))}</div>
                <div class="muted">مواد جديدة: {_escape(item.get("unsupported_expected_label") or "—")} · مدعوم تعثر: {_escape(item.get("supported_missing_label") or "—")} · غير موجه: {_escape(item.get("unrouted_expected_label") or "—")}</div>
              </td>
              <td>{_escape(item.get("summary"))}</td>
            </tr>
            """
        )
    if not history_table_rows:
        history_table_rows.append("<tr><td colspan='4'>لا توجد جولات محفوظة بعد.</td></tr>")
    def build_development_table_rows(items: list[dict], empty_message: str) -> list[str]:
        rows = []
        for item in items:
            decision = item.get("decision")
            if decision in ARTICLE_AUTOPILOT_CONTINUE_DECISIONS:
                decision_class = "ok"
            elif decision == "REJECTED_ROLLED_BACK":
                decision_class = "danger"
            else:
                decision_class = "warn"
            action_buttons = ""
            if decision == "REJECTED_ROLLED_BACK" and not int(item.get("transport_errors") or 0):
                action_buttons = f"""
                  <form class="inline-actions" method="post" action="/admin/article-autopilot/improvement-action">
                    <input type="hidden" name="manifest_path" value="{_escape(item.get('manifest_path'))}">
                    <button type="submit" name="action" value="retry">إعادة التحسين</button>
                    <button type="submit" name="action" value="carry" class="secondary">اعتماد وترحيل الإخفاق</button>
                  </form>
                """
            rows.append(
                f"""
                <tr>
                  <td>
                    <div class="task-title">{_escape(item.get("created_at_display") or item.get("created_at"))}</div>
                    <div class="muted">{_escape(item.get("manifest_label"))}</div>
                  </td>
                  <td>
                    <div class="quality-number {_escape(item.get("pre_quality_class") or "unknown")}">{_escape(item.get("pre_quality_label") or "—")}</div>
                    <div class="muted">قبل التحسين · {_escape(item.get("pre_quality_cases") or 0)} قضية</div>
                    <div class="muted">مرور أولي: {_escape(item.get("pre_quality_pass_rate_label") or "—")}</div>
                  </td>
                  <td><span class="badge {decision_class}">{_escape(decision)}</span><div class="muted">محاولات: {_escape(item.get("attempts"))} · اعتمد بعد: {_escape(item.get("accepted_after_attempt"))}</div></td>
                  <td>نجاح: {_escape(item.get("success_ratio"))}<div class="muted">الفجوات: {_escape(item.get("gap_rate"))}% · الدفعة: {_escape(item.get("batch_rounds"))} جولة</div></td>
                  <td>
                    دفعة: {_escape(item.get("validation_score"))}/100 · يدوي: {_escape(item.get("manual_score"))}/100 · ثابت: {_escape(item.get("fixed_holdout_score"))}/100 · استكشافي: {_escape(item.get("holdout_score"))}/100
                    <div class="muted">نوع التحقق: {_escape(item.get("validation_mode"))} · الثابت: {_escape(item.get("fixed_holdout_cases"))}/{_escape(item.get("fixed_holdout_total_case_count"))} · الاستكشافي: {_escape(item.get("holdout_cases"))}</div>
                    <div class="muted">MRR: {_escape(item.get("article_mrr"))} · تلوث: {_escape(item.get("pollution_rate"))}</div>
                    <div class="muted">تشخيص آلي: {_escape(item.get("auto_failure_gate"))}/{_escape(item.get("auto_failure_cause"))} · وصفة: {_escape(item.get("auto_recipe"))}</div>
                    <div class="muted">نمط عميق: {_escape(item.get("auto_deep_failure_mode"))} · تكرار: {_escape(item.get("auto_same_failure_count"))} · تصعيد: {_escape(item.get("auto_recipe_escalation_reason"))}</div>
                    <div class="muted">السبب الأعلى: {_escape(item.get("top_root_cause"))}</div>
                  </td>
                  <td>{_escape(item.get("action_note"))}{action_buttons}</td>
                </tr>
                """
            )
        if not rows:
            rows.append(f"<tr><td colspan='6'>{_escape(empty_message)}</td></tr>")
        return rows

    latest_development_rows = development_cycle_rows[:5]
    older_development_rows = development_cycle_rows[5:]
    latest_development_table_rows = build_development_table_rows(latest_development_rows, "لا توجد دورات تحسين محفوظة بعد.")
    older_development_table_rows = build_development_table_rows(older_development_rows, "لا توجد دورات أقدم من آخر خمس دورات.")
    failed_axis_chips = "".join(
        f"<span class='chip'>محور: {_escape(item.get('axis'))} × {_escape(item.get('count'))}</span>"
        for item in failed_axes[:8]
    )
    missing_pair_chips = "".join(
        f"<span class='chip'>مادة: {_escape(item.get('pair'))} × {_escape(item.get('count'))}</span>"
        for item in missing_pairs[:8]
    )
    improvement_chips = []
    if improvement:
        improvement_chips = [
            f"<span class='chip'>آخر تحسين: {_escape(improvement.get('decision'))}</span>",
            f"<span class='chip'>دفعة: {_escape(improvement.get('batch_rounds'))} جولة</span>",
            f"<span class='chip'>محاولات: {_escape(improvement.get('attempts_count'))}</span>",
            f"<span class='chip'>اعتمد بعد محاولة: {_escape(improvement.get('accepted_after_attempt'))}</span>",
            f"<span class='chip'>تحقق الدفعة: {_escape(improvement.get('validation_score'))}/100</span>",
            f"<span class='chip'>manual: {_escape(improvement.get('manual_score'))}/100</span>",
            f"<span class='chip'>ثابت: {_escape(improvement.get('fixed_holdout_score'))}/100</span>",
            f"<span class='chip'>استكشافي: {_escape(improvement.get('holdout_score'))}/100</span>",
            f"<span class='chip'>MRR: {_escape(improvement.get('article_mrr'))}</span>",
            f"<span class='chip'>تلوث: {_escape(improvement.get('pollution_rate'))}</span>",
            f"<span class='chip'>تشخيص آلي: {_escape(improvement.get('auto_failure_gate'))}/{_escape(improvement.get('auto_failure_cause'))}</span>",
            f"<span class='chip'>نمط عميق: {_escape(improvement.get('auto_deep_failure_mode'))}</span>",
            f"<span class='chip'>تكرار السبب: {_escape(improvement.get('auto_same_failure_count'))}</span>",
            f"<span class='chip'>وصفة: {_escape(improvement.get('auto_recipe'))}</span>",
            f"<span class='chip'>سبب التصعيد: {_escape(improvement.get('auto_recipe_escalation_reason'))}</span>",
            f"<span class='chip'>مرحل: {_escape(improvement.get('deferred_failure_count'))}</span>",
            f"<span class='chip'>دعم المواد: {_escape(improvement.get('article_support_rows'))}</span>",
        ]
    return f"""
      <div class="audit-panel">
        <div class="audit-summary">
          <div>
            <h3>التحسين الآلي</h3>
            <div class="muted">التركيز هنا على دورات التحسين: متى تمت، وكم نجحت، وهل اعتمدت.</div>
          </div>
          <span class="badge {badge_class}">{_escape(decision)}</span>
        </div>
        <details class="secondary-section">
          <summary>معنى الحالات</summary>
          <div class="autopilot-explain secondary-content">
            <div><strong>PASS</strong><span>كل مرشحات هذه الجولة جمعت النظام/اللائحة/المواد المطلوبة بلا أخطاء نقل، فتدخل المرشحات الموثوقة إلى بنك التوسعة.</span></div>
            <div><strong>FAIL</strong><span>الجولة وجدت فجوة. إن كانت retrieval/package فهي مادة أو محور لم يصل للسياق؛ وإن كانت operational فهي تشغيل ولا تُحسب على RAG.</span></div>
          </div>
        </details>
        <div class="grid compact-grid">
          <div class="stat compact-stat">
            <h3>دورات التحسين</h3>
            <div class="value">{_escape(development_summary.get("total_cycles") or 0)}</div>
            <div class="muted">
              معتمدة: {_escape(development_summary.get("accepted_cycles") or 0)} ·
              جولات: {_escape(development_summary.get("total_batch_rounds") or 0)} ·
              قضايا: {_escape(development_summary.get("total_validation_cases") or 0)}
            </div>
          </div>
          <div class="stat compact-stat">
            <h3>جودة ما قبل التحسين</h3>
            <div class="value">{_escape(development_summary.get("average_pre_quality_label") or "—")}</div>
            <div class="muted">متوسط اقتراب الدفعات من الصواب قبل إصلاحها</div>
          </div>
          <div class="stat compact-stat">
            <h3>حجم الجولة</h3>
            <div class="value">{_escape(snapshot.get("candidate_count") or 0)}</div>
            <div class="muted">{_escape(snapshot.get("round_size_label"))}</div>
          </div>
          <div class="stat compact-stat">
            <h3>المترقّي تلقائيًا</h3>
            <div class="value">{_escape(snapshot.get("promoted_count") or 0)}</div>
            <div class="muted">بعد تحقق Qwen ونجاح gate</div>
          </div>
          <div class="stat compact-stat">
            <h3>بنك التوسعة</h3>
            <div class="value">{_escape(snapshot.get("bank_count") or 0)}</div>
            <div class="muted">يدخل في مصفوفة التدقيق التالية</div>
          </div>
          <div class="stat compact-stat">
            <h3>مواد جديدة</h3>
            <div class="value">{_escape(round_support_summary.get("unsupported_expected_label") or "—")}</div>
            <div class="muted">متوسط آخر الجولات: خارج جدول الدعم</div>
          </div>
          <div class="stat compact-stat">
            <h3>مدعوم تعثر</h3>
            <div class="value">{_escape(round_support_summary.get("supported_missing_label") or "—")}</div>
            <div class="muted">مواد مدعومة لم تدخل أو لم تُوجّه جيدًا</div>
          </div>
          <div class="stat compact-stat">
            <h3>مدة آخر جولة</h3>
            <div class="value">{_escape(snapshot.get("duration_label"))}</div>
            <div class="muted">آخر جولة: جديد {_escape(current_support_gap.get("unsupported_expected_label") or "—")} · مدعوم تعثر {_escape(current_support_gap.get("supported_missing_label") or "—")} · غير موجه {_escape(current_support_gap.get("unrouted_expected_label") or "—")}</div>
          </div>
        </div>
        <div id="horizontal-coverage-panel" class="coverage-panel">
          <div class="coverage-header">
            <div>
              <h3>مؤشر الجودة المستقرة</h3>
              <div class="muted">{_escape(horizontal_coverage.get("stable_quality_method") or "")}</div>
            </div>
            <div class="quality-number {horizontal_class}">{_escape(horizontal_coverage.get("stable_quality_label") or horizontal_coverage.get("practical_percent_label") or "—")}</div>
          </div>
          <div class="coverage-metric-row">
            <div class="coverage-metric">
              <div class="metric-title">الجودة المقفلة</div>
              <div class="quality-number {horizontal_class}">{_escape(horizontal_coverage.get("stable_quality_label") or "—")}</div>
              <div class="muted">{_escape(horizontal_coverage.get("stable_quality_status") or "—")} · حالات مستقرة {_escape(horizontal_coverage.get("stable_quality_cases") or 0)} · فشل {_escape(horizontal_coverage.get("stable_quality_failed_cases") or 0)}</div>
            </div>
            <div class="coverage-metric">
              <div class="metric-title">الاستكشاف الجاري</div>
              <div class="quality-number {frontier_class}">{_escape(horizontal_coverage.get("frontier_signal_label") or "—")}</div>
              <div class="muted">{_escape(horizontal_coverage.get("frontier_status") or "—")} · فجوة استكشافية {_escape(horizontal_coverage.get("frontier_gap_label") or "—")}</div>
            </div>
          </div>
          <div class="progress-wrap">
            <div class="progress-track"><div class="progress-bar" style="width:{_escape(horizontal_width)}%"></div></div>
            <div class="muted">
              مواد مدعومة: {_escape(horizontal_coverage.get("supported_article_pairs") or 0)}
              / {_escape(horizontal_coverage.get("eligible_article_pairs") or 0)}
              · المتبقي: {_escape(horizontal_coverage.get("remaining_article_pairs") or 0)}
            </div>
          </div>
          <div class="chips">
            {stable_component_chips}
            <span class="chip">المؤشر المختلط القديم: {_escape(horizontal_coverage.get("practical_percent_label") or "—")}</span>
            <span class="chip">تغطية نظرية: {_escape(horizontal_coverage.get("pair_percent_label") or "—")}</span>
            <span class="chip">تغطية الأنظمة: {_escape(horizontal_coverage.get("supported_slugs") or 0)} / {_escape(horizontal_coverage.get("eligible_slugs") or 0)} = {_escape(horizontal_coverage.get("slug_percent_label") or "—")}</span>
            <span class="chip">إشارة آخر الجولات: {_escape(horizontal_coverage.get("frontier_signal_label") or horizontal_coverage.get("recent_signal_label") or "—")}</span>
            <span class="chip">مواد جديدة: {_escape(horizontal_coverage.get("recent_new_rate_label") or "—")}</span>
            <span class="chip">تعثر مدعوم: {_escape(horizontal_coverage.get("recent_supported_gap_label") or "—")}</span>
            <span class="chip">غير موجه: {_escape(horizontal_coverage.get("recent_route_gap_label") or "—")}</span>
            <span class="chip">ثابت مواد: {_escape(horizontal_coverage.get("fixed_holdout_score_label") or "—")}</span>
            <span class="chip">ثابت محاور: {_escape(horizontal_coverage.get("fixed_holdout_axis_coverage_label") or "—")}</span>
            <span class="chip">مصدر الثابت: {_escape(horizontal_coverage.get("fixed_holdout_source") or "—")}</span>
            <span class="chip">استكشافي مواد: {_escape(horizontal_coverage.get("holdout_score_label") or "—")}</span>
            <span class="chip">استكشافي محاور: {_escape(horizontal_coverage.get("holdout_axis_coverage_label") or "—")}</span>
            <span class="chip">مراحل مكتملة: {_escape(horizontal_coverage.get("phases_completed") or 0)}</span>
            <span class="chip">متبقي تقريبي: {_escape(horizontal_coverage.get("phases_remaining_estimate") if horizontal_coverage.get("phases_remaining_estimate") is not None else "—")} دفعة</span>
            <span class="chip">زمن متبقٍ تقريبي: {_escape(horizontal_coverage.get("remaining_time_label") or "—")}</span>
            <span class="chip">إجمالي تقديري: {_escape(horizontal_coverage.get("estimated_total_phases") if horizontal_coverage.get("estimated_total_phases") is not None else "—")} دفعة</span>
            <span class="chip">معدل التوسع الأخير: {_escape(horizontal_coverage.get("recent_pairs_per_phase_label") or "—")}</span>
            <span class="chip">متوسط زمن الدفعة: {_escape(horizontal_coverage.get("recent_phase_duration_label") or "—")}</span>
            <span class="chip">{_escape(horizontal_coverage.get("stage") or "—")}</span>
          </div>
        </div>
        <div id="development-cycles-panel">
          <div class="task-table-wrap priority-table">
            <h3>متابعة التطوير المستمر - آخر 5 دورات</h3>
            <table class="task-table compact-history">
              <thead>
                <tr>
                  <th>التاريخ والوقت</th>
                  <th>جودة قبل التحسين</th>
                  <th>قرار التحسين</th>
                  <th>نجاح / فجوات</th>
                  <th>التحقق</th>
                  <th>القرار التالي</th>
                </tr>
              </thead>
              <tbody>
                {''.join(latest_development_table_rows)}
              </tbody>
            </table>
          </div>
          <details class="secondary-section" data-details-key="development-cycle-archive">
            <summary>الدورات الأقدم من آخر خمس ({_escape(len(older_development_rows))})</summary>
            <div class="secondary-content">
              <div class="task-table-wrap priority-table">
                <table class="task-table compact-history">
                  <thead>
                    <tr>
                      <th>التاريخ والوقت</th>
                      <th>جودة قبل التحسين</th>
                      <th>قرار التحسين</th>
                      <th>نجاح / فجوات</th>
                      <th>التحقق</th>
                      <th>القرار التالي</th>
                    </tr>
                  </thead>
                  <tbody>
                    {''.join(older_development_table_rows)}
                  </tbody>
                </table>
              </div>
            </div>
          </details>
        </div>
        <details class="secondary-section">
          <summary>تفاصيل الجولة والمؤشرات الثانوية</summary>
          <div class="secondary-content">
        <div class="chips">
          <span class="chip">متوسط الاقتراب: {_escape(snapshot.get("score_average"))}/100</span>
          <span class="chip">نجاح: {_escape(snapshot.get("pass_rate"))}</span>
          <span class="chip">failed: {_escape(snapshot.get("failed_cases"))}</span>
          <span class="chip">قريب جدًا: {_escape(snapshot.get("near_miss_count"))}</span>
          <span class="chip">جزئي: {_escape(snapshot.get("partial_count"))}</span>
          <span class="chip">بعيد: {_escape(snapshot.get("low_count"))}</span>
          <span class="chip">transport: {_escape(snapshot.get("transport_errors"))}</span>
          <span class="chip">needs review: {_escape(snapshot.get("held_for_review_count"))}</span>
          <span class="chip">ok: {_escape(classification_counts.get("ok", 0))}</span>
          <span class="chip">retrieval: {_escape(classification_counts.get("retrieval/package issue", 0))}</span>
          <span class="chip">operational: {_escape(classification_counts.get("operational issue", 0))}</span>
          <span class="chip">missing material: {_escape(reason_counts.get("missing_article_material", 0))}</span>
          <span class="chip">not routed: {_escape(reason_counts.get("expected_article_not_routed", 0))}</span>
        </div>
        <div class="chips">{failed_axis_chips}{missing_pair_chips}</div>
        <div class="note">المراجعة البشرية تنحصر في المرشحات التي لا يولدها نموذج موثوق أو التي تفشل في gate. مثال الخلل المركب سيظهر هنا كنطاق مثل corporate_governance أو privacy_data مع المادة أو المحور المفقود.</div>
        <div class="note">
          بعد اكتمال دفعة الجولات المحددة يتوقف الجمع تلقائيًا. اضغط زر تحسين RAG ليبني النظام دعمًا عامًا من الفجوات المحفوظة ثم يتحقق من آخر دفعة ومن الشريحة اليدوية.
        </div>
        <div class="chips">{''.join(improvement_chips)}</div>
        <div class="task-table-wrap">
          <h3>مهام الجولة الأخيرة</h3>
          <table class="task-table">
            <thead>
              <tr>
                <th>القضية</th>
                <th>الجمع الذي تم</th>
                <th>التعليق</th>
                <th>إجراءات التحسين</th>
              </tr>
            </thead>
            <tbody>
              {''.join(task_table_rows)}
            </tbody>
            <tfoot>
              <tr>
                <td colspan="4">{_escape(task_success_footer)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <div class="task-table-wrap">
          <h3>آخر الترقيات الناجحة</h3>
          <table class="task-table">
            <thead>
              <tr>
                <th>القضية</th>
                <th>الجمع الذي تم</th>
                <th>التعليق</th>
                <th>إجراءات التحسين</th>
              </tr>
            </thead>
            <tbody>
              {''.join(success_table_rows)}
            </tbody>
          </table>
        </div>
        <div class="task-table-wrap">
          <h3>تاريخ آخر الجولات</h3>
          <table class="task-table compact-history">
            <thead>
              <tr>
                <th>الجولة</th>
                <th>الحالة</th>
                <th>القياس</th>
                <th>الملخص</th>
              </tr>
            </thead>
            <tbody>
              {''.join(history_table_rows)}
            </tbody>
          </table>
        </div>
          </div>
        </details>
        <div class="muted">آخر تشغيل: {_escape(snapshot.get("created_at"))}</div>
        <div class="chips">{''.join(report_links)}</div>
        <form id="article-autopilot-form" method="post" action="/admin/article-autopilot/start">
          <div class="form-grid">
            <label>
              حجم الجولة
              <input name="candidate_count" type="number" min="1" max="8" value="{ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT}">
            </label>
            <label>
              أعلى عدد مواد لكل مرشح
              <input name="max_articles_per_case" type="number" min="1" max="6" value="{ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT}">
            </label>
            <label>
              الفاصل بين الجولات بالثواني
              <input name="interval_seconds" type="number" min="{ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN}" max="3600" value="{ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT}">
            </label>
            <label>
              عدد الجولات قبل زر التحسين
              <input name="batch_round_limit" type="number" min="1" max="500" value="{ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT}">
            </label>
          </div>
          <button type="submit">تشغيل دفعة جمع الفجوات</button>
        </form>
        <form id="article-autopilot-development-form" method="post" action="/admin/article-autopilot/start">
          <input type="hidden" name="development_mode" value="1">
          <input type="hidden" name="fast_fixed_holdout_limit" value="{ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT}">
          <input type="hidden" name="fast_moving_holdout_limit" value="{ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT}">
          <input type="hidden" name="full_holdout_every_batches" value="{ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT}">
          <div class="form-grid">
            <label>
              حجم الجولة
              <input name="candidate_count" type="number" min="1" max="8" value="{ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT}">
            </label>
            <label>
              أعلى عدد مواد لكل مرشح
              <input name="max_articles_per_case" type="number" min="1" max="6" value="{ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT}">
            </label>
            <label>
              فاصل التبريد بالثواني
              <input name="interval_seconds" type="number" min="{ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN}" max="3600" value="{ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT}">
            </label>
            <label>
              حجم الدفعة قبل التحسين
              <input name="batch_round_limit" type="number" min="1" max="500" value="{ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT}">
            </label>
          </div>
          <button type="submit">تشغيل التطوير المستمر</button>
        </form>
        <form id="article-autopilot-improve-form" method="post" action="/admin/article-autopilot/improve">
          <div class="form-grid">
            <label>
              تحسين آخر كم جولة؟
              <input name="batch_round_limit" type="number" min="1" max="500" value="{ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT}">
            </label>
          </div>
          <button type="submit" class="secondary">تحسين RAG من الفجوات المحفوظة</button>
        </form>
        <form id="article-autopilot-stop-form" method="post" action="/admin/article-autopilot/stop">
          <button type="submit" class="secondary">إيقاف التصحيح الآلي</button>
        </form>
        <div id="article-autopilot-status"></div>
      </div>
    """


def _cleanup_article_audit_jobs():
    cutoff = time.time() - ARTICLE_AUDIT_JOB_TTL_SECONDS
    removable_ids = []
    for job_id, payload in ARTICLE_AUDIT_JOBS.items():
        if payload.get("updated_at", 0) < cutoff:
            removable_ids.append(job_id)
    for job_id in removable_ids:
        ARTICLE_AUDIT_JOBS.pop(job_id, None)


def _create_article_audit_job() -> str:
    job_id = secrets.token_urlsafe(12)
    with ARTICLE_AUDIT_JOBS_LOCK:
        _cleanup_article_audit_jobs()
        ARTICLE_AUDIT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "message": "بانتظار بدء تدقيق دقة الجمع.",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": {},
            "error": "",
        }
    return job_id


def _update_article_audit_job(job_id: str, **updates):
    with ARTICLE_AUDIT_JOBS_LOCK:
        job = ARTICLE_AUDIT_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _get_article_audit_job(job_id: str) -> Optional[dict]:
    with ARTICLE_AUDIT_JOBS_LOCK:
        job = ARTICLE_AUDIT_JOBS.get(job_id)
        if not job:
            return None
        return deepcopy(job)


def _get_active_article_audit_job() -> Optional[dict]:
    with ARTICLE_AUDIT_JOBS_LOCK:
        _cleanup_article_audit_jobs()
        for job in ARTICLE_AUDIT_JOBS.values():
            if job.get("status") in {"queued", "running"}:
                return deepcopy(job)
    return None


def _run_article_audit_command(job_id: str, stage: str, message: str, command: list[str]) -> None:
    _update_article_audit_job(job_id, status="running", stage=stage, message=message)
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60 * 45,
        check=False,
    )
    if completed.returncode:
        details = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise RuntimeError(f"{message} فشل برمز {completed.returncode}: {details}")


def _run_article_audit_job_sync(job_id: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    eval_dir = PROJECT_ROOT / "data" / "eval"
    matrix_path = eval_dir / "article_coverage_matrix_v1.json"
    probes_path = eval_dir / "article_coverage_matrix_v1_probes.jsonl"
    gate_path = eval_dir / f"article_coverage_matrix_v1_probe_gate_dashboard_{timestamp}.json"
    gap_path = eval_dir / f"article_coverage_matrix_v1_probe_gap_summary_dashboard_{timestamp}.json"
    benchmark_id = f"article_coverage_matrix_v1_dashboard_{timestamp}"
    service_url = f"http://127.0.0.1:{settings.server_port}/internal/rag/query"

    try:
        readiness = {
            "project_root": str(PROJECT_ROOT),
            "configured_server_port": settings.server_port,
            "knowledge_base_chunks": get_engine().get_collection_count(),
        }
        _update_article_audit_job(
            job_id,
            status="running",
            stage="readiness",
            message="تم تثبيت حالة الخدمة الحالية قبل الفحص.",
            readiness=readiness,
        )
        _run_article_audit_command(
            job_id,
            "matrix",
            "بناء مصفوفة تغطية المواد ومحاور الوقائع.",
            [
                sys.executable,
                "scripts/build_article_coverage_matrix.py",
                "--matrix-output",
                str(matrix_path),
                "--probes-output",
                str(probes_path),
            ],
        )
        _run_article_audit_command(
            job_id,
            "gate",
            "تشغيل فحص دقة الجمع على خدمة 8000 الحالية.",
            [
                sys.executable,
                "scripts/run_article_precision_gate.py",
                "--cases",
                str(probes_path),
                "--output",
                str(gate_path),
                "--benchmark-id",
                benchmark_id,
                "--retrieval-profile",
                "jamia_recall",
                "--service-url",
                service_url,
                "--timeout-seconds",
                "180",
            ],
        )
        _run_article_audit_command(
            job_id,
            "summary",
            "تصنيف الفجوات إلى تشغيلية، استرجاعية، أو مستوى جواب.",
            [
                sys.executable,
                "scripts/summarize_article_precision_gaps.py",
                "--report",
                str(gate_path),
                "--matrix",
                str(matrix_path),
                "--output",
                str(gap_path),
            ],
        )
        snapshot = _load_article_audit_snapshot()
        _update_article_audit_job(
            job_id,
            status="completed",
            stage="completed",
            message="اكتمل تدقيق دقة الجمع، وتم تحديث تقرير اللوحة.",
            result=snapshot,
            report_path=_relative_project_path(gate_path),
            gap_summary_path=_relative_project_path(gap_path),
        )
    except Exception as exc:
        logger.exception("فشل تدقيق دقة الجمع الخلفي: %s", exc)
        _update_article_audit_job(
            job_id,
            status="failed",
            stage="failed",
            message="تعذر إكمال تدقيق دقة الجمع.",
            error=str(exc),
        )


async def _run_article_audit_job(job_id: str) -> None:
    await asyncio.to_thread(_run_article_audit_job_sync, job_id)


def _cleanup_article_autopilot_jobs():
    cutoff = time.time() - ARTICLE_AUTOPILOT_JOB_TTL_SECONDS
    removable_ids = []
    for job_id, payload in ARTICLE_AUTOPILOT_JOBS.items():
        if payload.get("updated_at", 0) < cutoff:
            removable_ids.append(job_id)
    for job_id in removable_ids:
        ARTICLE_AUTOPILOT_JOBS.pop(job_id, None)
        ARTICLE_AUTOPILOT_STOP_EVENTS.pop(job_id, None)


def _article_autopilot_stale_after_seconds(job: dict) -> int:
    stage = str(job.get("stage") or "")
    return ARTICLE_AUTOPILOT_STALE_STAGE_SECONDS.get(stage, 30 * 60)


def _expire_stale_article_autopilot_jobs() -> list[str]:
    now = time.time()
    expired_ids: list[str] = []
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        for job_id, job in ARTICLE_AUTOPILOT_JOBS.items():
            if job.get("status") not in {"queued", "running"}:
                continue
            if job.get("stop_requested"):
                continue
            age = now - float(job.get("updated_at") or 0)
            stale_after = _article_autopilot_stale_after_seconds(job)
            if age <= stale_after:
                continue
            event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
            if event:
                event.set()
            job["status"] = "failed"
            job["stage"] = "stale_recoverable"
            job["message"] = "انقطع نبض مهمة التطوير المستمر؛ سيعيد المراقب تشغيلها تلقائيًا."
            job["error"] = f"autopilot heartbeat stale for {int(age)}s at stage {job.get('stage')}"
            job["updated_at"] = now
            expired_ids.append(job_id)
    return expired_ids


def _create_article_autopilot_job(config: Optional[dict] = None) -> str:
    job_id = secrets.token_urlsafe(12)
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        _cleanup_article_autopilot_jobs()
        ARTICLE_AUTOPILOT_STOP_EVENTS[job_id] = threading.Event()
        ARTICLE_AUTOPILOT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "steps": ARTICLE_AUTOPILOT_COLLECTION_STEPS,
            "message": "بانتظار بدء دفعة جمع الفجوات.",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": {},
            "error": "",
            "config": config or {},
            "completed_rounds": 0,
            "completed_batches": 0,
            "current_batch_round": 0,
            "batch_round_limit": (config or {}).get(
                "batch_round_limit",
                ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            ),
            "stop_requested": False,
        }
    return job_id


def _update_article_autopilot_job(job_id: str, **updates):
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        job = ARTICLE_AUTOPILOT_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()
        config = job.get("config") or {}
        if (
            bool(config.get("continuous"))
            and bool(config.get("development_mode"))
            and not job.get("stop_requested")
        ):
            if _article_autopilot_low_disk_space():
                event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
                if event:
                    event.set()
                job["stop_requested"] = True
                job["status"] = "paused"
                job["stage"] = "paused_low_disk_space"
                job["message"] = "أوقف التطوير المستمر مؤقتًا بسبب انخفاض مساحة التخزين."
                _set_article_autopilot_run_state(
                    enabled=False,
                    config=config,
                    job_id=job_id,
                    reason="paused_low_disk_space",
                    status="paused",
                )
                return
            status = str(job.get("status") or "")
            stage = str(job.get("stage") or "")
            now = time.time()
            last_touch = float(job.get("_run_state_heartbeat_ts") or 0.0)
            stage_changed = job.get("_run_state_heartbeat_stage") != stage
            status_changed = job.get("_run_state_heartbeat_status") != status
            if status in {"queued", "running", "failed"} and (
                stage_changed or status_changed or now - last_touch >= ARTICLE_AUTOPILOT_WATCHDOG_INTERVAL_SECONDS
            ):
                job["_run_state_heartbeat_ts"] = now
                job["_run_state_heartbeat_stage"] = stage
                job["_run_state_heartbeat_status"] = status
                _set_article_autopilot_run_state(
                    enabled=True,
                    config=config,
                    job_id=job_id,
                    reason="continuous_development_heartbeat",
                    status=status,
                )


def _get_article_autopilot_job(job_id: str) -> Optional[dict]:
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        job = ARTICLE_AUTOPILOT_JOBS.get(job_id)
        if not job:
            return None
        return deepcopy(job)


def _get_active_article_autopilot_job() -> Optional[dict]:
    _expire_stale_article_autopilot_jobs()
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        _cleanup_article_autopilot_jobs()
        for job in ARTICLE_AUTOPILOT_JOBS.values():
            if job.get("status") in {"queued", "running", "stopping"}:
                return deepcopy(job)
    return None


def _request_stop_article_autopilot_job(job_id: str) -> bool:
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
        job = ARTICLE_AUTOPILOT_JOBS.get(job_id)
        if not event or not job:
            return False
        event.set()
        _set_article_autopilot_run_state(
            enabled=False,
            config=job.get("config") or {},
            job_id=job_id,
            reason="manual_stop",
            status="stopping",
        )
        job["status"] = "stopping"
        job["stop_requested"] = True
        job["message"] = "تم طلب إيقاف التصحيح الآلي؛ سيتوقف بعد انتهاء المرحلة الحالية."
        job["updated_at"] = time.time()
        return True


def _normalize_article_autopilot_config(config: Optional[dict] = None) -> dict:
    raw = config or {}
    development_mode = bool(raw.get("development_mode", False))
    continuous = bool(raw.get("continuous", True))
    return {
        "candidate_count": _bounded_int(
            raw.get("candidate_count"),
            ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT,
            1,
            8,
        ),
        "max_articles_per_case": _bounded_int(
            raw.get("max_articles_per_case"),
            ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT,
            1,
            6,
        ),
        "interval_seconds": _bounded_int(
            raw.get("interval_seconds"),
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT,
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN,
            3600,
        ),
        "batch_round_limit": _bounded_int(
            raw.get("batch_round_limit"),
            ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            1,
            500,
        ),
        "retry_attempts": _bounded_int(raw.get("retry_attempts"), 2, 0, 5),
        "continuous": continuous,
        "development_mode": development_mode,
        "allow_deferred_failures": bool(raw.get("allow_deferred_failures", development_mode)),
        "deferred_min_validation_pass_rate": float(raw.get("deferred_min_validation_pass_rate") or 0.90),
        "fast_fixed_holdout_limit": _bounded_int(
            raw.get("fast_fixed_holdout_limit"),
            ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        ),
        "fast_moving_holdout_limit": _bounded_int(
            raw.get("fast_moving_holdout_limit"),
            ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        ),
        "full_holdout_every_batches": _bounded_int(
            raw.get("full_holdout_every_batches"),
            ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT,
            0,
            100,
        ),
    }


def _read_article_autopilot_run_state() -> dict:
    with ARTICLE_AUTOPILOT_RUN_STATE_LOCK:
        state = _safe_read_json(ARTICLE_AUTOPILOT_RUN_STATE_PATH)
        return state if isinstance(state, dict) else {}


def _article_autopilot_free_bytes() -> int:
    try:
        return int(shutil.disk_usage(PROJECT_ROOT).free)
    except Exception:
        return 0


def _article_autopilot_low_disk_space() -> bool:
    return _article_autopilot_free_bytes() < ARTICLE_AUTOPILOT_MIN_FREE_BYTES


def _cleanup_article_autopilot_artifact_dirs(
    keep_count: int = ARTICLE_AUTOPILOT_ARTIFACT_RETENTION_COUNT,
) -> dict:
    keep_count = max(0, int(keep_count or 0))
    summary = {"keep_count": keep_count, "groups": {}}
    if not ARTICLE_AUTOPILOT_DIR.exists():
        return summary
    for prefix in ("improvement_backup_", "improvement_staging_"):
        dirs = sorted(
            [
                path
                for path in ARTICLE_AUTOPILOT_DIR.iterdir()
                if path.is_dir() and path.name.startswith(prefix)
            ],
            key=lambda path: path.name,
        )
        to_delete = dirs[:-keep_count] if keep_count else dirs
        removed = 0
        failures = []
        for path in to_delete:
            try:
                shutil.rmtree(path)
                removed += 1
            except Exception as exc:
                failures.append({"path": str(path), "error": str(exc)})
        kept = dirs[-keep_count:] if keep_count else []
        summary["groups"][prefix] = {
            "before": len(dirs),
            "removed": removed,
            "kept": len(kept),
            "failures": failures[:10],
        }
    return summary


def _maybe_cleanup_article_autopilot_artifacts(reason: str, *, force: bool = False) -> dict:
    global ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LAST_TS
    now = time.time()
    if (
        not force
        and now - ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LAST_TS
        < ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_INTERVAL_SECONDS
    ):
        return {}
    if not ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LOCK.acquire(blocking=False):
        return {}
    try:
        ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LAST_TS = now
        summary = _cleanup_article_autopilot_artifact_dirs()
    finally:
        ARTICLE_AUTOPILOT_ARTIFACT_CLEANUP_LOCK.release()
    removed = sum(int(group.get("removed") or 0) for group in (summary.get("groups") or {}).values())
    failures = sum(len(group.get("failures") or []) for group in (summary.get("groups") or {}).values())
    if removed:
        logger.info(
            "Article autopilot artifact cleanup removed %s old directories (reason=%s)",
            removed,
            reason,
        )
    if failures:
        logger.warning(
            "Article autopilot artifact cleanup had %s failures (reason=%s): %s",
            failures,
            reason,
            summary,
        )
    return summary


def _set_article_autopilot_run_state(
    *,
    enabled: bool,
    config: Optional[dict] = None,
    job_id: Optional[str] = None,
    reason: str = "",
    status: str = "",
) -> None:
    with ARTICLE_AUTOPILOT_RUN_STATE_LOCK:
        ARTICLE_AUTOPILOT_RUN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        previous = _safe_read_json(ARTICLE_AUTOPILOT_RUN_STATE_PATH)
        previous = previous if isinstance(previous, dict) else {}
        payload = {
            "enabled": bool(enabled),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id or previous.get("job_id") or "",
            "reason": reason or previous.get("reason") or "",
            "status": status or previous.get("status") or "",
            "config": _normalize_article_autopilot_config(config or previous.get("config") or {}),
        }
        tmp_path = ARTICLE_AUTOPILOT_RUN_STATE_PATH.with_name(
            f"{ARTICLE_AUTOPILOT_RUN_STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(ARTICLE_AUTOPILOT_RUN_STATE_PATH)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def _terminate_article_autopilot_subprocess(process: subprocess.Popen, *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.kill() if force else process.terminate()
        except OSError:
            pass


def _run_article_autopilot_subprocess(
    job_id: str,
    command: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    with ARTICLE_AUTOPILOT_SUBPROCESSES_LOCK:
        ARTICLE_AUTOPILOT_SUBPROCESSES[job_id] = process
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_article_autopilot_subprocess(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _terminate_article_autopilot_subprocess(process, force=True)
                stdout, stderr = process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        with ARTICLE_AUTOPILOT_SUBPROCESSES_LOCK:
            if ARTICLE_AUTOPILOT_SUBPROCESSES.get(job_id) is process:
                ARTICLE_AUTOPILOT_SUBPROCESSES.pop(job_id, None)


def prepare_article_autopilot_service_shutdown() -> None:
    active_configs: list[tuple[str, dict]] = []
    with ARTICLE_AUTOPILOT_JOBS_LOCK:
        for job_id, job in ARTICLE_AUTOPILOT_JOBS.items():
            if job.get("status") not in {"queued", "running", "stopping"}:
                continue
            event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
            if event:
                event.set()
            job["stop_requested"] = True
            job["status"] = "stopping"
            job["stage"] = "service_shutdown"
            job["message"] = "تتوقف مهمة الخلفية مؤقتًا مع الخدمة وستُستأنف تلقائيًا."
            job["updated_at"] = time.time()
            active_configs.append((job_id, job.get("config") or {}))

    for job_id, config in active_configs:
        if bool(config.get("continuous")) and bool(config.get("development_mode")):
            _set_article_autopilot_run_state(
                enabled=True,
                config=config,
                job_id=job_id,
                reason="service_shutdown_resume_pending",
                status="queued",
            )

    with ARTICLE_AUTOPILOT_SUBPROCESSES_LOCK:
        processes = list(ARTICLE_AUTOPILOT_SUBPROCESSES.values())
    for process in processes:
        _terminate_article_autopilot_subprocess(process)
    deadline = time.time() + 5
    for process in processes:
        remaining = max(0.0, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_article_autopilot_subprocess(process, force=True)


def _launch_article_autopilot_job(job_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=_run_article_autopilot_job_sync, args=(job_id,), daemon=True).start()
    else:
        loop.create_task(_run_article_autopilot_job(job_id))


def ensure_article_autopilot_running(reason: str = "watchdog") -> Optional[str]:
    state = _read_article_autopilot_run_state()
    if not state.get("enabled"):
        return None
    if _article_autopilot_low_disk_space():
        _maybe_cleanup_article_autopilot_artifacts(f"{reason}_low_disk", force=True)
    if _article_autopilot_low_disk_space():
        _set_article_autopilot_run_state(
            enabled=False,
            config=state.get("config") if isinstance(state.get("config"), dict) else {},
            job_id=str(state.get("job_id") or ""),
            reason="paused_low_disk_space",
            status="paused",
        )
        logger.warning(
            "Article autopilot auto-resume paused: low disk space (%s bytes free)",
            _article_autopilot_free_bytes(),
        )
        return None
    active_job = _get_active_article_autopilot_job()
    if active_job:
        return str(active_job.get("job_id") or "")

    config = _normalize_article_autopilot_config(state.get("config") or {})
    job_id = _create_article_autopilot_job(config)
    message = (
        "استؤنف التطوير المستمر تلقائيًا بعد انقطاع مهمة الخلفية."
        if reason == "watchdog"
        else "استؤنف التطوير المستمر تلقائيًا بعد إعادة تشغيل الخدمة."
    )
    _update_article_autopilot_job(
        job_id,
        status="queued",
        stage="queued",
        message=message,
        development_mode=config.get("development_mode"),
    )
    _set_article_autopilot_run_state(
        enabled=True,
        config=config,
        job_id=job_id,
        reason=f"auto_resume:{reason}",
        status="queued",
    )
    _launch_article_autopilot_job(job_id)
    return job_id


def resume_article_autopilot_if_enabled() -> Optional[str]:
    _maybe_cleanup_article_autopilot_artifacts("service_start", force=True)
    return ensure_article_autopilot_running("service_start")


async def watch_article_autopilot_continuity() -> None:
    while True:
        await asyncio.sleep(ARTICLE_AUTOPILOT_WATCHDOG_INTERVAL_SECONDS)
        try:
            _maybe_cleanup_article_autopilot_artifacts("watchdog")
            expired_ids = _expire_stale_article_autopilot_jobs()
            job_id = ensure_article_autopilot_running("watchdog")
            if expired_ids and job_id:
                logger.warning(
                    "استؤنف التطوير المستمر بعد انقطاع نبض المهام: expired=%s new=%s",
                    ",".join(expired_ids),
                    job_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("تعذر مراقبة استمرارية التطوير المستمر: %s", exc)


def _compact_article_autopilot_result(result: dict) -> dict:
    if not isinstance(result, dict):
        return {}
    keys = {
        "decision",
        "article_score",
        "score_average",
        "near_miss_count",
        "partial_count",
        "low_count",
        "pass_rate",
        "cases_total",
        "transport_errors",
        "failed_cases",
        "promoted_count",
        "held_for_review_count",
        "candidate_count",
        "max_articles_per_case",
        "round_size_label",
        "duration_label",
        "classification_counts",
        "reason_counts",
        "top_failed_axes",
        "top_missing_article_pairs",
        "development_cycle_summary",
        "horizontal_coverage",
        "round_support_summary",
        "current_round_support_gap",
        "task_total",
        "task_successes",
        "task_operational",
        "task_success_rate",
        "created_at",
    }
    compact = {key: deepcopy(result.get(key)) for key in keys if key in result}
    compact["task_rows"] = [_compact_autopilot_task_row(row) for row in list(result.get("task_rows") or [])[:6]]
    compact["success_rows"] = [_compact_autopilot_task_row(row) for row in list(result.get("success_rows") or [])[:3]]
    compact["round_history"] = [_compact_autopilot_round_row(row) for row in list(result.get("round_history") or [])[:4]]
    compact["development_cycle_rows"] = [
        _compact_development_cycle_row(row)
        for row in list(result.get("development_cycle_rows") or [])[:50]
    ]
    return compact


def _short_text(value, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _compact_autopilot_task_row(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        "question_id": row.get("question_id"),
        "domain": row.get("domain"),
        "question": _short_text(row.get("question"), 180),
        "status": row.get("status"),
        "score_label": row.get("score_label"),
        "is_operational": row.get("is_operational"),
        "article_points": row.get("article_points"),
        "collected": _short_text(row.get("collected"), 220),
        "comment": _short_text(row.get("comment"), 220),
        "action": _short_text(row.get("action"), 180),
        "review_status": row.get("review_status"),
    }


def _compact_autopilot_round_row(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        "round": row.get("round"),
        "decision": row.get("decision"),
        "article_score": row.get("article_score"),
        "score_average": row.get("score_average"),
        "pass_rate": row.get("pass_rate"),
        "failed_cases": row.get("failed_cases"),
        "near_miss_count": row.get("near_miss_count"),
        "partial_count": row.get("partial_count"),
        "low_count": row.get("low_count"),
        "transport_errors": row.get("transport_errors"),
        "promoted_count": row.get("promoted_count"),
        "held_for_review_count": row.get("held_for_review_count"),
        "expected_article_pairs": row.get("expected_article_pairs"),
        "unsupported_expected_pairs": row.get("unsupported_expected_pairs"),
        "unsupported_expected_rate": row.get("unsupported_expected_rate"),
        "unsupported_expected_label": row.get("unsupported_expected_label"),
        "supported_missing_pairs": row.get("supported_missing_pairs"),
        "supported_missing_rate": row.get("supported_missing_rate"),
        "supported_missing_label": row.get("supported_missing_label"),
        "unrouted_expected_pairs": row.get("unrouted_expected_pairs"),
        "unrouted_expected_rate": row.get("unrouted_expected_rate"),
        "unrouted_expected_label": row.get("unrouted_expected_label"),
        "supported_route_gap_pairs": row.get("supported_route_gap_pairs"),
        "supported_route_gap_rate": row.get("supported_route_gap_rate"),
        "supported_route_gap_label": row.get("supported_route_gap_label"),
        "duration_label": row.get("duration_label"),
        "created_at": row.get("created_at"),
        "summary": row.get("summary"),
    }


def _compact_development_cycle_row(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        "manifest_label": _short_text(row.get("manifest_label"), 120),
        "created_at": row.get("created_at"),
        "created_at_display": row.get("created_at_display"),
        "pre_quality_score": row.get("pre_quality_score"),
        "pre_quality_label": row.get("pre_quality_label"),
        "pre_quality_class": row.get("pre_quality_class"),
        "pre_quality_cases": row.get("pre_quality_cases"),
        "pre_quality_pass_rate_label": row.get("pre_quality_pass_rate_label"),
        "decision": row.get("decision"),
        "validation_mode": row.get("validation_mode"),
        "batch_rounds": row.get("batch_rounds"),
        "success_ratio": row.get("success_ratio"),
        "gap_rate": row.get("gap_rate"),
        "validation_score": row.get("validation_score"),
        "manual_score": row.get("manual_score"),
        "fixed_holdout_score": row.get("fixed_holdout_score"),
        "fixed_holdout_pass_rate": row.get("fixed_holdout_pass_rate"),
        "fixed_holdout_cases": row.get("fixed_holdout_cases"),
        "fixed_holdout_total_case_count": row.get("fixed_holdout_total_case_count"),
        "fixed_holdout_sampled": row.get("fixed_holdout_sampled"),
        "holdout_score": row.get("holdout_score"),
        "holdout_pass_rate": row.get("holdout_pass_rate"),
        "holdout_cases": row.get("holdout_cases"),
        "article_mrr": row.get("article_mrr"),
        "pollution_rate": row.get("pollution_rate"),
        "auto_failure_gate": row.get("auto_failure_gate"),
        "auto_failure_cause": row.get("auto_failure_cause"),
        "auto_deep_failure_mode": row.get("auto_deep_failure_mode"),
        "auto_recipe": row.get("auto_recipe"),
        "auto_recipe_escalation_reason": row.get("auto_recipe_escalation_reason"),
        "auto_same_failure_count": row.get("auto_same_failure_count"),
        "auto_diagnostics_count": row.get("auto_diagnostics_count"),
        "attempts": row.get("attempts"),
        "accepted_after_attempt": row.get("accepted_after_attempt"),
        "top_root_cause": row.get("top_root_cause"),
        "action_note": _short_text(row.get("action_note"), 160),
    }


def _compact_validation_summary(summary: dict) -> dict:
    if not isinstance(summary, dict):
        return {}
    return {
        "cases_total": summary.get("cases_total"),
        "article_score_100": summary.get("article_score_100"),
        "pass_rate": summary.get("pass_rate"),
        "failed_cases": summary.get("failed_cases"),
        "transport_error_cases": summary.get("transport_error_cases"),
        "article_mrr": summary.get("article_mrr"),
        "mean_expected_article_rank": summary.get("mean_expected_article_rank"),
        "mean_context_position": summary.get("mean_context_position"),
        "pollution_rate": summary.get("pollution_rate"),
    }


def _compact_article_autopilot_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        return {}
    diagnosis = manifest.get("diagnosis") or {}
    validation = manifest.get("validation_summary") or manifest.get("gate_summary") or {}
    return {
        "decision": manifest.get("decision") or manifest.get("gap_decision"),
        "created_at": manifest.get("created_at"),
        "validation_mode": manifest.get("validation_mode"),
        "batch_rounds": manifest.get("batch_rounds"),
        "deferred_failure_count": manifest.get("deferred_failure_count"),
        "fixed_holdout_case_count": manifest.get("fixed_holdout_case_count"),
        "fixed_holdout_total_case_count": manifest.get("fixed_holdout_total_case_count"),
        "fixed_holdout_sampled": manifest.get("fixed_holdout_sampled"),
        "holdout_case_count": manifest.get("holdout_case_count"),
        "validation_summary": _compact_validation_summary(validation),
        "manual_summary": manifest.get("manual_summary") or {},
        "fixed_holdout_summary": _compact_validation_summary(manifest.get("fixed_holdout_summary") or {}),
        "fixed_holdout_guard": manifest.get("fixed_holdout_guard") or {},
        "moving_holdout_summary": _compact_validation_summary(manifest.get("moving_holdout_summary") or manifest.get("holdout_summary") or {}),
        "holdout_summary": _compact_validation_summary(manifest.get("holdout_summary") or {}),
        "auto_failure_diagnostics": [
            {
                "failure_gate": item.get("failure_gate"),
                "top_root_cause": item.get("top_root_cause"),
                "deep_failure_mode": item.get("deep_failure_mode"),
                "selected_recipe": (item.get("selected_recipe") or {}).get("id"),
                "escalation_reason": (item.get("selected_recipe") or {}).get("escalation_reason"),
            }
            for item in list(manifest.get("auto_failure_diagnostics") or [])[-3:]
        ],
        "diagnosis": {
            "root_cause_counts": diagnosis.get("root_cause_counts") or {},
        },
    }


def _compact_article_autopilot_job_payload(job: dict) -> dict:
    payload = deepcopy(job or {})
    if payload.get("result"):
        payload["result"] = _compact_article_autopilot_result(payload.get("result") or {})
    if payload.get("manifest"):
        payload["manifest"] = _compact_article_autopilot_manifest(payload.get("manifest") or {})
    return payload


def _run_article_autopilot_single_round(
    job_id: str,
    round_number: int,
    candidate_count: int,
    max_articles_per_case: int,
    models: list[str],
    stop_event: threading.Event,
    batch_round: int = 0,
    batch_round_limit: int = 0,
    completed_batches: int = 0,
) -> tuple[dict, dict]:
    round_started_at = time.time()
    round_label = (
        f"الدفعة {completed_batches + 1}، الجولة {batch_round} من {batch_round_limit} (الإجمالي {round_number})"
        if batch_round and batch_round_limit
        else f"الجولة {round_number}"
    )

    def _run_step(stage: str, progress: int, message: str, command: list[str], timeout: int):
        if stop_event.is_set():
            raise InterruptedError("تم طلب إيقاف التصحيح الآلي.")
        _update_article_autopilot_job(
            job_id,
            status="running",
            stage=stage,
            progress=progress,
            message=f"{round_label}: {message}",
            current_command=command[:3],
            current_round=round_number,
            current_batch_round=batch_round,
            batch_round_limit=batch_round_limit,
            completed_batches=completed_batches,
        )
        completed = _run_article_autopilot_subprocess(job_id, command, timeout=timeout)
        if stop_event.is_set():
            raise InterruptedError("تم طلب إيقاف التصحيح الآلي.")
        if completed.returncode:
            details = (completed.stderr or completed.stdout or "").strip()[-3000:]
            raise RuntimeError(f"{stage} فشل برمز {completed.returncode}: {details}")
        return completed

    output_dir = PROJECT_ROOT / "data" / "eval" / "article_autopilot"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidates_path = output_dir / f"article_autopilot_candidates_{timestamp}.jsonl"
    probes_path = output_dir / f"article_autopilot_probes_{timestamp}.jsonl"
    gate_path = output_dir / f"article_autopilot_gate_{timestamp}.json"
    summary_path = output_dir / f"article_autopilot_gap_summary_{timestamp}.json"
    manifest_path = output_dir / f"article_autopilot_manifest_{timestamp}.json"
    bank_path = output_dir / "autopilot_article_precision_bank.jsonl"
    service_url = f"http://127.0.0.1:{settings.server_port}/internal/rag/query"

    _update_article_autopilot_job(
        job_id,
        status="running",
        stage="readiness",
        progress=8,
        message=f"{round_label}: فحص جاهزية الخدمة وحجم الجولة.",
        readiness={
            "project_root": str(PROJECT_ROOT),
            "configured_server_port": settings.server_port,
            "knowledge_base_chunks": get_engine().get_collection_count(),
        },
        round_config={
            "candidate_count": candidate_count,
            "max_articles_per_case": max_articles_per_case,
            "models": models,
        },
        current_round=round_number,
        current_batch_round=batch_round,
        batch_round_limit=batch_round_limit,
        completed_batches=completed_batches,
    )

    generate_command = [
        sys.executable,
        "scripts/generate_article_precision_candidates.py",
        "--output",
        str(candidates_path),
        "--probes-output",
        str(probes_path),
        "--candidate-count",
        str(candidate_count),
        "--max-articles-per-case",
        str(max_articles_per_case),
        "--seed",
        str(int(time.time()) % 10_000_000),
        "--timeout",
        "600",
        "--history-dir",
        str(output_dir),
        "--recent-history-files",
        "24",
        "--max-recent-slug-count",
        "1",
    ]
    for model in models:
        generate_command.extend(["--model", model])
    generate_completed = _run_step(
        "generate",
        25,
        "توليد المرشحات من Qwen وبناء أسئلة مواد دقيقة.",
        generate_command,
        timeout=max(900, 600 * max(1, candidate_count) * 2),
    )
    try:
        generation_summary = json.loads((generate_completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        generation_summary = {}

    _run_step(
        "gate",
        58,
        "اختبار المرشحات على الخدمة الحالية وقياس حضور النظام واللائحة والمواد.",
        [
            sys.executable,
            "scripts/run_article_precision_gate.py",
            "--cases",
            str(probes_path),
            "--output",
            str(gate_path),
            "--benchmark-id",
            f"article_autopilot_{timestamp}",
            "--retrieval-profile",
            "jamia_recall",
            "--service-url",
            service_url,
            "--timeout-seconds",
            "180",
        ],
        timeout=max(600, 180 * max(1, candidate_count + 1)),
    )

    _run_step(
        "diagnose",
        78,
        "تصنيف الخلل إلى تشغيلي أو retrieval/package أو answer-level.",
        [
            sys.executable,
            "scripts/summarize_article_precision_gaps.py",
            "--report",
            str(gate_path),
            "--matrix",
            str(PROJECT_ROOT / "data" / "eval" / "article_coverage_matrix_v1.json"),
            "--output",
            str(summary_path),
        ],
        timeout=120,
    )

    from scripts.run_article_autopilot_round import load_jsonl, promote_candidates

    _update_article_autopilot_job(
        job_id,
        status="running",
        stage="promote",
        progress=90,
        message=f"الجولة {round_number}: ترقية المرشحات التي وثقها Qwen ونجحت في gate.",
        current_round=round_number,
    )
    gate = _safe_read_json(gate_path)
    gap_summary = _safe_read_json(summary_path)
    promotion = promote_candidates(probes_path, gate_path, bank_path)
    duration_seconds = round(time.time() - round_started_at, 1)
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "round_number": round_number,
        "round_config": {
            "candidate_count": candidate_count,
            "max_articles_per_case": max_articles_per_case,
            "models": models,
        },
        "candidate_count": len(load_jsonl(probes_path)),
        "models": models,
        "paths": {
            "candidates": str(candidates_path),
            "probes": str(probes_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
            "bank": str(bank_path),
        },
        "gate_summary": gate.get("summary", {}),
        "gap_decision": gap_summary.get("decision"),
        "classification_counts": gap_summary.get("classification_counts", {}),
        "reason_counts": gap_summary.get("reason_counts", {}),
        "generation_summary": generation_summary,
        "promotion": promotion,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return _load_article_autopilot_snapshot(), manifest


def _run_article_autopilot_job_sync(job_id: str) -> None:
    global ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID
    stop_event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
    if stop_event is None:
        stop_event = threading.Event()
        ARTICLE_AUTOPILOT_STOP_EVENTS[job_id] = stop_event
    if not ARTICLE_AUTOPILOT_ACTIVE_RUN_LOCK.acquire(blocking=False):
        _update_article_autopilot_job(
            job_id,
            status="completed",
            stage="duplicate_suppressed",
            progress=100,
            message=(
                "مُنع تشغيل مهمة تطوير مستمر مكررة لأن مهمة أخرى تعمل بالفعل: "
                f"{ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID or 'active'}."
            ),
        )
        return
    ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID = job_id
    try:
        job = _get_article_autopilot_job(job_id) or {}
        config = job.get("config") or {}
        candidate_count = _bounded_int(
            config.get("candidate_count"),
            ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT,
            1,
            8,
        )
        max_articles_per_case = _bounded_int(
            config.get("max_articles_per_case"),
            ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT,
            1,
            6,
        )
        interval_seconds = _bounded_int(
            config.get("interval_seconds"),
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT,
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN,
            3600,
        )
        batch_round_limit = _bounded_int(
            config.get("batch_round_limit"),
            ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            1,
            500,
        )
        fast_fixed_holdout_limit = _bounded_int(
            config.get("fast_fixed_holdout_limit"),
            ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        )
        fast_moving_holdout_limit = _bounded_int(
            config.get("fast_moving_holdout_limit"),
            ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        )
        full_holdout_every_batches = _bounded_int(
            config.get("full_holdout_every_batches"),
            ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT,
            0,
            100,
        )
        continuous = bool(config.get("continuous", True))
        development_mode = bool(config.get("development_mode", False))
        models = ["qwen3.6:35b"]
        round_number = int(job.get("completed_rounds") or 0)
        completed_batches = int(job.get("completed_batches") or 0)
        if _article_autopilot_low_disk_space():
            stop_event.set()
            _set_article_autopilot_run_state(
                enabled=False,
                config=config,
                job_id=job_id,
                reason="paused_low_disk_space",
                status="paused",
            )
            _update_article_autopilot_job(
                job_id,
                status="stopped",
                stage="paused_low_disk_space",
                progress=100,
                message="أوقف التطوير المستمر مؤقتًا بسبب انخفاض مساحة التخزين.",
                stop_requested=True,
            )
            return
        if bool(config.get("continuous")) and bool(config.get("development_mode")):
            _set_article_autopilot_run_state(
                enabled=True,
                config=config,
                job_id=job_id,
                reason="continuous_development_running",
                status="running",
            )

        while not stop_event.is_set():
            batch_round = 0
            while not stop_event.is_set() and batch_round < batch_round_limit:
                if _article_autopilot_low_disk_space():
                    stop_event.set()
                    _set_article_autopilot_run_state(
                        enabled=False,
                        config=config,
                        job_id=job_id,
                        reason="paused_low_disk_space",
                        status="paused",
                    )
                    break
                round_number += 1
                batch_round += 1
                snapshot, manifest = _run_article_autopilot_single_round(
                    job_id,
                    round_number,
                    candidate_count,
                    max_articles_per_case,
                    models,
                    stop_event,
                    batch_round=batch_round,
                    batch_round_limit=batch_round_limit,
                    completed_batches=completed_batches,
                )
                decision = snapshot.get("decision")
                more_rounds_in_batch = batch_round < batch_round_limit
                _update_article_autopilot_job(
                    job_id,
                    status="running" if continuous and not stop_event.is_set() else "completed",
                    stage="between_rounds" if continuous and not stop_event.is_set() and more_rounds_in_batch else "batch_ready_for_improvement",
                    progress=100,
                    message=(
                        f"اكتملت الجولة {batch_round} من دفعة {batch_round_limit} مع فجوات؛ ستبدأ الجولة التالية تلقائيًا."
                        if continuous and decision == "FAIL" and not stop_event.is_set() and more_rounds_in_batch
                        else f"اكتملت الجولة {batch_round} من دفعة {batch_round_limit} بنجاح؛ ستبدأ الجولة التالية تلقائيًا."
                        if continuous and not stop_event.is_set() and more_rounds_in_batch
                        else f"اكتملت دفعة {batch_round_limit} جولة."
                    ),
                    result=snapshot,
                    manifest=manifest,
                    completed_rounds=round_number,
                    completed_batches=completed_batches,
                    batch_round_limit=batch_round_limit,
                    current_batch_round=batch_round,
                    development_mode=development_mode,
                )
                if more_rounds_in_batch and continuous and not stop_event.is_set():
                    for remaining in range(interval_seconds, 0, -1):
                        if stop_event.is_set():
                            break
                        _update_article_autopilot_job(
                            job_id,
                            status="running",
                            stage="between_rounds",
                            progress=100,
                            message=(
                                f"اكتملت الجولة {batch_round} من دفعة {batch_round_limit} "
                                f"(الإجمالي {round_number}). الجولة التالية تبدأ خلال {remaining} ثوانٍ."
                            ),
                            completed_rounds=round_number,
                            current_batch_round=batch_round,
                            completed_batches=completed_batches,
                            batch_round_limit=batch_round_limit,
                            development_mode=development_mode,
                        )
                        time.sleep(1)

            if stop_event.is_set():
                break
            if _article_autopilot_low_disk_space():
                stop_event.set()
                _set_article_autopilot_run_state(
                    enabled=False,
                    config=config,
                    job_id=job_id,
                    reason="paused_low_disk_space",
                    status="paused",
                )
                break
            if not development_mode:
                _set_article_autopilot_run_state(
                    enabled=False,
                    config=config,
                    job_id=job_id,
                    reason="batch_ready_for_manual_improvement",
                    status="completed",
                )
                _update_article_autopilot_job(
                    job_id,
                    status="completed",
                    stage="batch_ready_for_improvement",
                    progress=100,
                    message=f"اكتملت دفعة {batch_round_limit} جولة. أوقف الجمع الآن واضغط زر تحسين RAG.",
                    result=snapshot,
                    manifest=manifest,
                    completed_rounds=round_number,
                    completed_batches=completed_batches,
                    batch_round_limit=batch_round_limit,
                )
                break

            _update_article_autopilot_job(
                job_id,
                status="running",
                stage="improve_batch",
                progress=100,
                message=f"اكتملت دفعة {batch_round_limit}. يبدأ تحسين RAG بتحقق سريع وحارس كامل دوري.",
                completed_rounds=round_number,
                completed_batches=completed_batches,
                development_mode=True,
            )
            next_batch_index = completed_batches + 1
            run_full_holdout = (
                full_holdout_every_batches > 0
                and next_batch_index % full_holdout_every_batches == 0
            )
            improvement_manifest = _run_article_autopilot_improvement_command(
                job_id=job_id,
                batch_round_limit=batch_round_limit,
                retry_attempts=2,
                allow_deferred_failures=True,
                fixed_holdout_limit=0 if run_full_holdout else fast_fixed_holdout_limit,
                moving_holdout_limit=fast_moving_holdout_limit,
                allow_sampled_fixed_holdout=not run_full_holdout,
                fixed_holdout_sample_offset=completed_batches,
                validation_mode="full_periodic" if run_full_holdout else "fast_staged",
            )
            improvement_decision = str(improvement_manifest.get("decision") or "")
            snapshot = _load_article_autopilot_snapshot()
            _update_article_autopilot_job(
                job_id,
                status="running",
                steps=ARTICLE_AUTOPILOT_COLLECTION_STEPS,
                stage=(
                    "next_batch"
                    if improvement_decision in ARTICLE_AUTOPILOT_CONTINUE_DECISIONS
                    else "next_batch_after_rollback"
                    if improvement_decision == "REJECTED_ROLLED_BACK"
                    else "needs_human_decision"
                ),
                progress=100,
                message=(
                    f"تم اعتماد التحسين ({improvement_decision}). ستبدأ دفعة جديدة بعد فاصل التبريد."
                    if improvement_decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
                    else "لا توجد فجوات retrieval/package في دفعة التدريب الحالية. نواصل الاستكشاف الأفقي بعد فاصل التبريد."
                    if improvement_decision == "NO_RAG_CHANGE_NEEDED"
                    else "تعذّر تحقق التحسين بسبب عطل تشغيلي/مهلة اتصال، ولم يُحتسب كفجوة RAG. ستبدأ دفعة جديدة بعد فاصل التبريد."
                    if improvement_decision == "OPERATIONAL_ONLY_NO_RAG_CHANGE"
                    else "لم يُعتمد التحسين وتم الرجوع للنسخة المستقرة. ستبدأ دفعة جمع جديدة بعد فاصل التبريد."
                    if improvement_decision == "REJECTED_ROLLED_BACK"
                    else f"توقف التطوير المستمر لأن التحسين لم يُقبل: {improvement_decision}."
                ),
                result=snapshot,
                manifest=improvement_manifest,
                completed_rounds=round_number,
                completed_batches=completed_batches + (1 if improvement_decision in ARTICLE_AUTOPILOT_CONTINUE_DECISIONS else 0),
                current_batch_round=0,
                batch_round_limit=batch_round_limit,
                development_mode=True,
            )
            if improvement_decision not in ARTICLE_AUTOPILOT_CONTINUE_DECISIONS:
                if continuous and development_mode and improvement_decision == "REJECTED_ROLLED_BACK":
                    _set_article_autopilot_run_state(
                        enabled=True,
                        config=config,
                        job_id=job_id,
                        reason="improvement_rejected_continue_collecting",
                        status="running",
                    )
                    for remaining in range(interval_seconds, 0, -1):
                        if stop_event.is_set():
                            break
                        _update_article_autopilot_job(
                            job_id,
                            status="running",
                            stage="next_batch_after_rollback",
                            progress=100,
                            message=(
                                "لم يُعتمد التحسين وتم rollback. "
                                f"دفعة جمع جديدة تبدأ خلال {remaining} ثوانٍ."
                            ),
                            completed_rounds=round_number,
                            completed_batches=completed_batches,
                            current_batch_round=0,
                            batch_round_limit=batch_round_limit,
                            development_mode=True,
                        )
                        time.sleep(1)
                    continue
                _set_article_autopilot_run_state(
                    enabled=False,
                    config=config,
                    job_id=job_id,
                    reason=f"improvement_not_accepted:{improvement_decision}",
                    status="completed",
                )
                _update_article_autopilot_job(job_id, status="completed")
                break
            completed_batches += 1
            for remaining in range(interval_seconds, 0, -1):
                if stop_event.is_set():
                    break
                next_message = (
                    "لا توجد فجوات RAG في الدفعة الحالية. "
                    f"دفعة استكشاف جديدة تبدأ خلال {remaining} ثوانٍ."
                    if improvement_decision == "NO_RAG_CHANGE_NEEDED"
                    else f"تم اعتماد التحسين. الدفعة التالية تبدأ خلال {remaining} ثوانٍ."
                )
                _update_article_autopilot_job(
                    job_id,
                    status="running",
                    stage="next_batch",
                    progress=100,
                    message=next_message,
                    completed_rounds=round_number,
                    completed_batches=completed_batches,
                    current_batch_round=0,
                    batch_round_limit=batch_round_limit,
                    development_mode=True,
                )
                time.sleep(1)

        if stop_event.is_set():
            _update_article_autopilot_job(
                job_id,
                status="stopped",
                stage="stopped",
                progress=100,
                message="تم إيقاف التصحيح الآلي يدويًا.",
                stop_requested=True,
                completed_rounds=round_number if round_number else int((_get_article_autopilot_job(job_id) or {}).get("completed_rounds") or 0),
                completed_batches=completed_batches,
            )
    except InterruptedError:
        _update_article_autopilot_job(
            job_id,
            status="stopped",
            stage="stopped",
            progress=100,
            message="تم إيقاف التصحيح الآلي يدويًا.",
            stop_requested=True,
        )
    except Exception as exc:
        logger.exception("فشلت جولة التحسين الآلية: %s", exc)
        job = _get_article_autopilot_job(job_id) or {}
        config = job.get("config") or {}
        if _article_autopilot_low_disk_space():
            _set_article_autopilot_run_state(
                enabled=False,
                config=config,
                job_id=job_id,
                reason="paused_low_disk_space",
                status="paused",
            )
        elif bool(config.get("continuous")) and bool(config.get("development_mode")):
            _set_article_autopilot_run_state(
                enabled=True,
                config=config,
                job_id=job_id,
                reason="failed_but_resume_enabled",
                status="failed",
            )
        _update_article_autopilot_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="تعذر إكمال التصحيح الآلي المستمر.",
            error=str(exc),
        )
    finally:
        if ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID == job_id:
            ARTICLE_AUTOPILOT_ACTIVE_RUN_JOB_ID = ""
        ARTICLE_AUTOPILOT_ACTIVE_RUN_LOCK.release()


async def _run_article_autopilot_job(job_id: str) -> None:
    await asyncio.to_thread(_run_article_autopilot_job_sync, job_id)


def _start_followup_autopilot_after_improvement(
    *,
    source_job_id: str,
    decision: str,
    fallback_config: dict,
) -> str:
    state = _read_article_autopilot_run_state()
    state_config = state.get("config") if isinstance(state.get("config"), dict) else {}
    config = _normalize_article_autopilot_config(state_config or fallback_config or {})
    if not (config.get("continuous") and config.get("development_mode")):
        return ""
    if _article_autopilot_low_disk_space():
        _set_article_autopilot_run_state(
            enabled=False,
            config=config,
            job_id=source_job_id,
            reason="paused_low_disk_space",
            status="paused",
        )
        return ""
    active_job = _get_active_article_autopilot_job()
    if active_job and str(active_job.get("job_id") or "") != source_job_id:
        return str(active_job.get("job_id") or "")
    followup_job_id = _create_article_autopilot_job(config)
    reason = (
        "resume_after_accepted_improvement"
        if decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
        else "resume_after_no_batch_gap"
        if decision == "NO_RAG_CHANGE_NEEDED"
        else "resume_after_rejected_rollback"
    )
    _set_article_autopilot_run_state(
        enabled=True,
        config=config,
        job_id=followup_job_id,
        reason=reason,
        status="queued",
    )
    _update_article_autopilot_job(
        followup_job_id,
        status="queued",
        stage="queued",
        message=(
            "استؤنف التطوير المستمر بعد اعتماد التحسين."
            if decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
            else "استؤنف التطوير المستمر لأن الدفعة الحالية لا تحتاج تعديل RAG."
            if decision == "NO_RAG_CHANGE_NEEDED"
            else "استؤنف التطوير المستمر بعد rollback وترحيل التحسين المتعطل."
        ),
        development_mode=True,
    )
    threading.Thread(target=_run_article_autopilot_job_sync, args=(followup_job_id,), daemon=True).start()
    return followup_job_id


def _run_article_autopilot_improvement_command(
    *,
    job_id: str,
    batch_round_limit: int,
    retry_attempts: int = 2,
    allow_deferred_failures: bool = False,
    fixed_holdout_limit: int = 0,
    moving_holdout_limit: int = 200,
    allow_sampled_fixed_holdout: bool = False,
    fixed_holdout_sample_offset: int = 0,
    validation_mode: str = "full",
) -> dict:
    service_url = f"http://127.0.0.1:{settings.server_port}/internal/rag/query"
    command = [
        sys.executable,
        "scripts/run_article_autopilot_improvement.py",
        "--batch-round-limit",
        str(batch_round_limit),
        "--service-url",
        service_url,
        "--timeout-seconds",
        "180",
        "--retry-attempts",
        str(retry_attempts),
        "--min-holdout-pass-rate",
        "0.90",
        "--fixed-holdout-limit",
        str(fixed_holdout_limit),
        "--fixed-holdout-sample-offset",
        str(fixed_holdout_sample_offset),
        "--holdout-limit",
        str(moving_holdout_limit),
        "--validation-mode",
        validation_mode,
    ]
    if allow_sampled_fixed_holdout:
        command.append("--allow-sampled-fixed-holdout")
    if allow_deferred_failures:
        command.extend(
            [
                "--allow-deferred-failures",
                "--deferred-min-validation-pass-rate",
                "0.90",
                "--deferred-backlog",
                str(ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH),
            ]
        )
    _update_article_autopilot_job(
        job_id,
        status="running",
        stage="improve_batch",
        progress=15,
        steps=ARTICLE_AUTOPILOT_IMPROVEMENT_STEPS,
        message=(
            f"تشخيص عميق وتحسين آخر {batch_round_limit} جولة "
            f"({ 'تحقق كامل' if validation_mode == 'full_periodic' else 'تحقق سريع متدرج' })."
        ),
        current_command=command[:3],
    )
    completed = _run_article_autopilot_subprocess(
        job_id,
        command,
        timeout=max(1200, 180 * max(2, batch_round_limit * 2)),
    )
    stop_event = ARTICLE_AUTOPILOT_STOP_EVENTS.get(job_id)
    if stop_event and stop_event.is_set():
        raise InterruptedError("تم طلب إيقاف التصحيح الآلي.")
    if completed.returncode:
        details = (completed.stderr or completed.stdout or "").strip()[-3000:]
        raise RuntimeError(f"فشل تحسين RAG برمز {completed.returncode}: {details}")
    try:
        return json.loads((completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"تعذر قراءة نتيجة تحسين RAG: {exc}") from exc


def _run_article_autopilot_improvement_job_sync(job_id: str) -> None:
    try:
        job = _get_article_autopilot_job(job_id) or {}
        config = job.get("config") or {}
        batch_round_limit = _bounded_int(
            config.get("batch_round_limit"),
            ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            1,
            500,
        )
        retry_attempts = _bounded_int(config.get("retry_attempts"), 2, 0, 5)
        manifest = _run_article_autopilot_improvement_command(
            job_id=job_id,
            batch_round_limit=batch_round_limit,
            retry_attempts=retry_attempts,
            allow_deferred_failures=bool(config.get("allow_deferred_failures", False)),
        )
        decision = str(manifest.get("decision") or "")
        snapshot = _load_article_autopilot_snapshot()
        _update_article_autopilot_job(
            job_id,
            status="completed",
            stage="accept_or_rollback",
            progress=100,
            message=(
                "تم قبول تحسين RAG بعد إعادة اختبار نفس دفعة الجولات والشريحة اليدوية."
                if decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
                else "لا توجد فجوات retrieval/package في دفعة التدريب الحالية؛ سيستمر الاستكشاف الأفقي."
                if decision == "NO_RAG_CHANGE_NEEDED"
                else "لم يُقبل تحسين RAG؛ تم rollback وترحيله للمراجعة، وسيستمر التطوير المستمر إن كان مفعّلًا."
                if decision == "REJECTED_ROLLED_BACK"
                else f"لم يُقبل تحسين RAG: {decision}. لم تُحسب أي مشكلة تشغيلية كفجوة RAG."
            ),
            result=snapshot,
            manifest=manifest,
            completed_rounds=0,
        )
        followup_job_id = ""
        if decision in {*ARTICLE_AUTOPILOT_CONTINUE_DECISIONS, "REJECTED_ROLLED_BACK"}:
            followup_job_id = _start_followup_autopilot_after_improvement(
                source_job_id=job_id,
                decision=decision,
                fallback_config=config,
            )
            if followup_job_id:
                _update_article_autopilot_job(
                    job_id,
                    followup_job_id=followup_job_id,
                    message=(
                        "انتهى التحسين، وتم تشغيل دفعة التطوير التالية تلقائيًا."
                        if decision in ARTICLE_AUTOPILOT_ACCEPTED_IMPROVEMENT_DECISIONS
                        else "لا توجد فجوات في الدفعة الحالية، وبدأت دفعة استكشاف تالية تلقائيًا."
                        if decision == "NO_RAG_CHANGE_NEEDED"
                        else "تم rollback وترحيل التحسين المتعطل، وبدأت دفعة تطوير تالية تلقائيًا."
                    ),
                )
    except Exception as exc:
        logger.exception("فشلت مهمة تحسين RAG من فجوات autopilot: %s", exc)
        _update_article_autopilot_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="تعذر إكمال تحسين RAG من الفجوات المحفوظة.",
            error=str(exc),
        )


async def _run_article_autopilot_improvement_job(job_id: str) -> None:
    await asyncio.to_thread(_run_article_autopilot_improvement_job_sync, job_id)


async def _run_compare_job(job_id: str, question: str, selected_providers: list[str], answer_mode: str):
    _update_compare_job(job_id, status="running", error="")
    try:
        engine = get_engine()
        provider_results = await engine.compare_generation_providers(
            question,
            selected_providers,
            answer_mode=answer_mode,
        )
        results = []
        for provider in selected_providers:
            item = provider_results.get(provider)
            if not item:
                continue
            results.append(
                {
                    "title": COMPARE_PROVIDER_TITLES.get(
                        provider,
                        PROVIDER_METADATA.get(provider, {}).get("label", provider),
                    ),
                    "provider_label": item["runtime"]["label"],
                    "model": item["runtime"].get("model", ""),
                    "answer": item["result"].answer,
                    "confidence": item["result"].confidence,
                    "sources": item["result"].sources,
                    "error": "",
                }
            )
        _update_compare_job(
            job_id,
            status="completed",
            answer_mode=answer_mode,
            answer_mode_label=ANSWER_MODE_LABELS[_normalize_answer_mode(answer_mode)],
            results=results,
        )
    except Exception as exc:
        logger.exception("فشلت المقارنة الخلفية للمزودات: %s", exc)
        _update_compare_job(job_id, status="failed", error=str(exc), results=[])


def _render_dashboard(
    request: Request,
    *,
    compare_payload: Optional[dict] = None,
) -> HTMLResponse:
    notice = request.query_params.get("notice", "")
    error = request.query_params.get("error", "")
    engine = get_engine()
    runtime_store = get_runtime_settings_store()
    panel_state = runtime_store.get_panel_state(refresh_catalogs=False)
    provider_map = {provider["id"]: provider for provider in panel_state["providers"]}
    active_provider = provider_map.get(panel_state["active_provider"], {})
    generation_status = {
        "provider": panel_state["active_provider"],
        "provider_label": active_provider.get("label", panel_state["active_provider"]),
        "model": active_provider.get("model", ""),
    }
    official_status = get_official_sync_service().get_status()
    file_search_status = get_gemini_file_search_service().get_status()
    ollama_provider = provider_map.get("ollama")
    open_mode_warning = ""
    request_port = request.url.port or settings.server_port
    instance_label = settings.instance_label or PROJECT_ROOT.name
    project_root_display = str(PROJECT_ROOT)

    if not settings.admin_panel_password:
        open_mode_warning = (
            "<div class='banner warning'>"
            "لوحة التحكم تعمل الآن بدون كلمة مرور لأن <code>admin_panel_password</code> غير مضبوط. "
            "هذا مناسب للتجربة فقط، واضبط كلمة مرور قبل النشر العام."
            "</div>"
        )

    message_html = ""
    if notice:
        message_html += f"<div class='banner success'>{_escape(notice)}</div>"
    if error:
        message_html += f"<div class='banner error'>{_escape(error)}</div>"

    compare_html = ""
    if compare_payload:
        compare_cards = []
        for result in compare_payload.get("results", []):
            compare_cards.append(
                f"""
                <div class="compare-card">
                  <h3>{_escape(result['title'])}</h3>
                  <div class="muted">المولد: {_escape(result['provider_label'])} / {_escape(result['model'])}</div>
                  <div class="muted">الثقة: {_escape(result['confidence'])}</div>
                  <pre>{_escape(result['answer'])}</pre>
                  {_render_compare_sources(result['title'], [], result['sources'])}
                  {f"<div class='banner error'>{_escape(result['error'])}</div>" if result.get('error') else ''}
                </div>
                """
            )
        compare_html = f"""
        <section class="section wide">
          <h2>نتيجة المقارنة على السؤال نفسه</h2>
          <div class="muted">صيغة الإخراج المختبرة: {_escape(compare_payload.get('answer_mode_label', ANSWER_MODE_LABELS[ANSWER_MODE_CONSULTATION]))}</div>
          {f"<div class='banner error'>{_escape(compare_payload.get('error'))}</div>" if compare_payload.get('error') else ''}
          <div class="compare-grid">
            {''.join(compare_cards)}
          </div>
        </section>
        """

    openrouter_provider = provider_map["openrouter"]
    gemini_provider = provider_map["gemini"]
    ollama_provider = provider_map["ollama"]
    mlx_local_provider = provider_map.get("mlx_local")
    default_compare_providers = _get_compare_provider_ids(panel_state)
    selected_compare_providers = set(
        compare_payload.get("selected_providers", default_compare_providers)
        if compare_payload
        else default_compare_providers
    )
    selected_answer_mode = _normalize_answer_mode(compare_payload.get("answer_mode") if compare_payload else ANSWER_MODE_CONSULTATION)
    selected_answer_mode_label = ANSWER_MODE_LABELS[selected_answer_mode]
    openrouter_select = _render_model_select(
        "openrouter_model",
        openrouter_provider["model"],
        openrouter_provider["model_suggestions"],
        provider_id="openrouter",
    )
    gemini_select = _render_model_select(
        "gemini_model",
        gemini_provider["model"],
        gemini_provider["model_suggestions"],
        provider_id="gemini",
    )
    ollama_select = _render_model_select(
        "ollama_model",
        ollama_provider["model"],
        ollama_provider["model_suggestions"],
        provider_id="ollama",
    )
    mlx_local_select = _render_model_select(
        "mlx_local_model",
        mlx_local_provider["model"] if mlx_local_provider else "",
        mlx_local_provider["model_suggestions"] if mlx_local_provider else [],
        provider_id="mlx_local",
    )
    article_audit_html = _render_article_audit_card(_load_article_audit_snapshot())
    active_autopilot_job = _get_active_article_autopilot_job()
    active_autopilot_snapshot = (
        active_autopilot_job.get("result") or {}
        if active_autopilot_job
        else {}
    )
    article_autopilot_html = _render_article_autopilot_card(
        active_autopilot_snapshot
        if active_autopilot_job
        else _load_article_autopilot_snapshot()
    )

    html_body = f"""
    <!doctype html>
    <html lang="ar" dir="rtl">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>لوحة تحكم الاستشارات القانونية</title>
      <style>
        :root {{
          --bg: #f2efe7;
          --card: #fffdf8;
          --ink: #1f2a24;
          --accent: #0d5c63;
          --accent-2: #f59e0b;
          --line: #ded6c7;
          --ok: #166534;
          --warn: #9a3412;
          --danger: #b42318;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", sans-serif;
          background:
            radial-gradient(circle at top right, rgba(13,92,99,.18), transparent 28%),
            linear-gradient(180deg, #f5f0e5 0%, var(--bg) 100%);
          color: var(--ink);
        }}
        .shell {{
          max-width: 1440px;
          margin: 0 auto;
          padding: 24px;
        }}
        .hero {{
          background: linear-gradient(135deg, rgba(13,92,99,.96), rgba(31,42,36,.94));
          color: #fff;
          border-radius: 24px;
          padding: 28px;
          box-shadow: 0 24px 80px rgba(0,0,0,.10);
        }}
        .hero h1 {{ margin: 0 0 8px; font-size: 2rem; }}
        .hero p {{ margin: 0; color: rgba(255,255,255,.86); }}
        .toolbar {{
          display: flex;
          gap: 12px;
          align-items: center;
          justify-content: space-between;
          margin-top: 18px;
          flex-wrap: wrap;
        }}
        .grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 18px;
          margin-top: 22px;
        }}
        .section, .stat, .provider-card, .compare-card {{
          background: rgba(255,253,248,.94);
          border: 1px solid rgba(222,214,199,.9);
          border-radius: 20px;
          box-shadow: 0 18px 50px rgba(31,42,36,.06);
        }}
        .section {{
          padding: 22px;
          margin-top: 20px;
        }}
        .section h2, .stat h3, .compare-card h3 {{
          margin: 0 0 14px;
        }}
        .wide {{ margin-top: 24px; }}
        .stat {{
          padding: 18px;
        }}
        .stat .value {{
          font-size: 1.8rem;
          font-weight: 700;
          color: var(--accent);
        }}
        .provider-grid, .compare-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
          gap: 16px;
        }}
        .provider-card {{
          padding: 18px;
        }}
        .provider-card.active {{
          border-color: rgba(13,92,99,.5);
          box-shadow: 0 0 0 3px rgba(13,92,99,.08), 0 18px 50px rgba(31,42,36,.06);
        }}
        .provider-header {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
        }}
        .badge {{
          display: inline-flex;
          align-items: center;
          padding: 6px 10px;
          border-radius: 999px;
          font-size: .85rem;
          font-weight: 700;
        }}
        .badge.ok {{ background: rgba(22,101,52,.10); color: var(--ok); }}
        .badge.warn {{ background: rgba(154,52,18,.10); color: var(--warn); }}
        .badge.danger {{ background: rgba(180,35,24,.10); color: var(--danger); }}
        .chips {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 12px;
        }}
        .chip {{
          padding: 6px 10px;
          background: rgba(13,92,99,.08);
          color: var(--accent);
          border-radius: 999px;
          font-size: .85rem;
        }}
        form {{
          display: grid;
          gap: 14px;
        }}
        .form-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
          gap: 14px;
        }}
        label {{
          display: grid;
          gap: 8px;
          font-weight: 600;
        }}
        input, select, textarea, button {{
          font: inherit;
        }}
        input, select, textarea {{
          border: 1px solid var(--line);
          border-radius: 14px;
          padding: 12px 14px;
          background: #fff;
          color: var(--ink);
        }}
        .model-select {{
          max-width: 100%;
        }}
        textarea {{
          min-height: 170px;
          resize: vertical;
        }}
        button {{
          border: 0;
          border-radius: 14px;
          padding: 12px 18px;
          background: var(--accent);
          color: #fff;
          cursor: pointer;
          font-weight: 700;
        }}
        button.secondary {{
          background: #fff;
          color: var(--accent);
          border: 1px solid rgba(13,92,99,.24);
        }}
        .toolbar form, .inline-form {{
          display: inline-flex;
          gap: 10px;
          align-items: center;
        }}
        .banner {{
          margin-top: 18px;
          padding: 14px 16px;
          border-radius: 16px;
          font-weight: 600;
        }}
        .banner.success {{ background: rgba(22,101,52,.10); color: var(--ok); }}
        .banner.error {{ background: rgba(180,35,24,.10); color: var(--danger); }}
        .banner.warning {{ background: rgba(245,158,11,.14); color: var(--warn); }}
        .muted {{ color: #5d6a62; font-size: .92rem; }}
        pre {{
          white-space: pre-wrap;
          word-break: break-word;
          background: rgba(13,92,99,.05);
          border: 1px solid rgba(13,92,99,.08);
          border-radius: 14px;
          padding: 14px;
          margin: 12px 0 0;
          line-height: 1.7;
        }}
        .source-box {{
          margin-top: 12px;
          padding: 12px;
          border: 1px dashed rgba(13,92,99,.24);
          border-radius: 14px;
          background: #fff;
        }}
        .note {{
          padding: 12px 14px;
          border-radius: 14px;
          background: rgba(245,158,11,.10);
          color: #7c2d12;
          line-height: 1.8;
        }}
        .audit-panel {{
          display: grid;
          gap: 14px;
        }}
        .audit-summary {{
          display: flex;
          align-items: start;
          justify-content: space-between;
          gap: 14px;
          flex-wrap: wrap;
        }}
        .compact-grid {{
          margin-top: 0;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        }}
        .compact-stat {{
          padding: 14px;
          box-shadow: none;
        }}
        .compact-stat .value {{
          font-size: 1.45rem;
        }}
        .coverage-panel {{
          display: grid;
          gap: 12px;
          padding: 14px;
          border: 1px solid rgba(222,214,199,.9);
          border-radius: 16px;
          background: rgba(255,253,248,.72);
        }}
        .coverage-header {{
          display: flex;
          align-items: start;
          justify-content: space-between;
          gap: 12px;
          flex-wrap: wrap;
        }}
        .coverage-header h3 {{
          margin: 0 0 6px;
        }}
        .coverage-metric-row {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 10px;
        }}
        .coverage-metric {{
          display: grid;
          gap: 6px;
          padding: 10px 0;
          border-top: 1px solid rgba(222,214,199,.72);
        }}
        .metric-title {{
          font-size: .9rem;
          font-weight: 850;
          color: var(--ink);
        }}
        .autopilot-explain {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 10px;
        }}
        .autopilot-explain div {{
          display: grid;
          gap: 6px;
          padding: 12px 14px;
          border-radius: 14px;
          background: rgba(13,92,99,.06);
          border: 1px solid rgba(13,92,99,.08);
        }}
        .autopilot-explain strong {{
          color: var(--accent);
        }}
        .finding-list {{
          display: grid;
          gap: 10px;
        }}
        .finding-list h3 {{
          margin: 0;
        }}
        .finding-item {{
          display: grid;
          gap: 6px;
          padding: 12px 14px;
          border: 1px solid rgba(222,214,199,.9);
          border-radius: 12px;
          background: rgba(255,255,255,.62);
          line-height: 1.7;
        }}
        .finding-title {{
          font-weight: 800;
          color: var(--accent);
        }}
        .task-table-wrap {{
          display: grid;
          gap: 10px;
          overflow-x: auto;
        }}
        .priority-table {{
          padding: 10px 0 4px;
        }}
        .priority-table h3 {{
          font-size: 1.15rem;
        }}
        .priority-table .task-table {{
          min-width: 760px;
          background: #fff;
          border-color: rgba(13,92,99,.28);
        }}
        .priority-table .task-table th {{
          background: rgba(13,92,99,.12);
          font-size: .86rem;
        }}
        .priority-table .task-table td {{
          padding: 10px;
          line-height: 1.55;
        }}
        .quality-number {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 86px;
          padding: 6px 10px;
          border-radius: 10px;
          font-size: 1.28rem;
          font-weight: 900;
          background: rgba(13,92,99,.08);
          color: var(--accent);
          white-space: nowrap;
        }}
        .quality-number.ok {{
          background: rgba(22,101,52,.10);
          color: var(--ok);
        }}
        .quality-number.warn {{
          background: rgba(245,158,11,.15);
          color: var(--warn);
        }}
        .quality-number.danger {{
          background: rgba(180,35,24,.10);
          color: var(--danger);
        }}
        .quality-number.unknown {{
          background: rgba(31,42,36,.07);
          color: #5d6a62;
        }}
        .secondary-section {{
          border: 1px solid rgba(222,214,199,.9);
          border-radius: 12px;
          background: rgba(255,255,255,.42);
          padding: 8px 10px;
        }}
        .secondary-section summary {{
          cursor: pointer;
          color: var(--accent);
          font-weight: 800;
          list-style: none;
        }}
        .secondary-section summary::-webkit-details-marker {{
          display: none;
        }}
        .secondary-section summary::before {{
          content: "▾";
          display: inline-block;
          margin-left: 6px;
          transition: transform .16s ease;
        }}
        .secondary-section:not([open]) summary::before {{
          transform: rotate(90deg);
        }}
        .secondary-content {{
          display: grid;
          gap: 10px;
          margin-top: 10px;
        }}
        .secondary-section .task-table {{
          min-width: 680px;
          font-size: .84rem;
        }}
        .secondary-section .task-table th,
        .secondary-section .task-table td {{
          padding: 8px;
          line-height: 1.45;
        }}
        .task-table-wrap h3 {{
          margin: 0;
        }}
        .task-table {{
          width: 100%;
          border-collapse: collapse;
          min-width: 860px;
          background: rgba(255,255,255,.64);
          border: 1px solid rgba(222,214,199,.9);
          border-radius: 12px;
          overflow: hidden;
        }}
        .compact-history {{
          min-width: 720px;
        }}
        .task-table th,
        .task-table td {{
          border-bottom: 1px solid rgba(222,214,199,.9);
          padding: 12px;
          text-align: right;
          vertical-align: top;
          line-height: 1.7;
        }}
        .task-table th {{
          color: var(--accent);
          background: rgba(13,92,99,.07);
          font-size: .9rem;
        }}
        .task-table tr:last-child td {{
          border-bottom: 0;
        }}
        .task-table tfoot td {{
          background: rgba(13,92,99,.08);
          color: var(--accent);
          font-weight: 800;
        }}
        .task-title {{
          font-weight: 800;
          color: var(--ink);
        }}
        .progress-wrap {{
          display: grid;
          gap: 8px;
          margin-top: 10px;
        }}
        .progress-track {{
          height: 10px;
          background: rgba(13,92,99,.10);
          border-radius: 999px;
          overflow: hidden;
        }}
        .progress-bar {{
          height: 100%;
          width: 0;
          background: var(--accent);
          transition: width .25s ease;
        }}
        .job-steps {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 10px;
        }}
        .job-step {{
          padding: 6px 10px;
          border-radius: 999px;
          background: rgba(31,42,36,.07);
          color: #405047;
          font-size: .82rem;
          font-weight: 700;
        }}
        .job-step.active {{
          background: rgba(13,92,99,.12);
          color: var(--accent);
        }}
        .job-step.done {{
          background: rgba(22,101,52,.10);
          color: var(--ok);
        }}
        @media (max-width: 720px) {{
          .shell {{ padding: 14px; }}
          .hero {{ padding: 20px; border-radius: 18px; }}
        }}
      </style>
    </head>
    <body>
      <div class="shell">
        <section class="hero">
          <h1>لوحة تحكم الاستشارات القانونية</h1>
          <p>إدارة المزودات والتوكونات، ومزامنة المعرفة، ثم مقارنة نتائج السؤال نفسه بين المولدات المحلية والسحابية فوق نفس سياق RAG.</p>
          <div class="chips">
            <span class="chip">النسخة: {_escape(instance_label)}</span>
            <span class="chip">المجلد: {_escape(project_root_display)}</span>
            <span class="chip">المنفذ الحالي: {_escape(request_port)}</span>
          </div>
          <div class="toolbar">
            <div class="muted">المسار الفعّال الآن: {_escape(generation_status['provider_label'])} / {_escape(generation_status['model'])}</div>
            <div class="inline-form">
              <form method="post" action="/admin/actions/local-sync">
                <button class="secondary" type="submit">إعادة فهرسة RAG الآن</button>
              </form>
              <form method="post" action="/admin/actions/file-search-sync">
                <button class="secondary" type="submit">مزامنة Gemini File Search</button>
              </form>
              <form method="post" action="/admin/actions/official-sync">
                <button class="secondary" type="submit">مزامنة الأنظمة الرسمية</button>
              </form>
              <form method="post" action="/admin/actions/restart-service">
                <button class="secondary" type="submit">إعادة تشغيل الخدمة</button>
              </form>
              <form method="post" action="/admin/logout">
                <button type="submit">خروج</button>
              </form>
            </div>
          </div>
        </section>

        {open_mode_warning}
        {message_html}

        <div class="grid">
          <div class="stat">
            <h3>قاعدة المعرفة المحلية</h3>
            <div class="value">{engine.get_collection_count()}</div>
            <div class="muted">عدد المقاطع المفهرسة عبر Chroma + RAG</div>
          </div>
          <div class="stat">
            <h3>المزامنة الرسمية</h3>
            <div class="value">{official_status['synced_entries']}/{official_status['catalog_entries']}</div>
            <div class="muted">آخر تشغيل: {_escape(official_status.get('last_run_finished_at') or 'لم يجرِ بعد')}</div>
          </div>
          <div class="stat">
            <h3>Gemini File Search</h3>
            <div class="value">{file_search_status['file_count']}</div>
            <div class="muted">عدد الملفات المتزامنة في المتجر: {_escape(file_search_status.get('store_name') or 'لم يُنشأ بعد')}</div>
          </div>
          <div class="stat">
            <h3>الـ Embeddings</h3>
            <div class="value">{_escape(panel_state['embedding_model'])}</div>
            <div class="muted">مفتاح OpenAI: {_escape(panel_state['embeddings_api_key_masked'])}</div>
          </div>
          <div class="stat">
            <h3>نماذج Ollama المحلية</h3>
            <div class="value">{len(ollama_provider['available_models']) if ollama_provider else 0}</div>
            <div class="muted">{_escape((ollama_provider or {}).get('connection_message') or 'لم يتم فحص الاتصال بعد')}</div>
          </div>
        </div>

        <section class="section">
          <h2>مستوى دقة الجمع</h2>
          {article_audit_html}
        </section>

        <section class="section">
          <h2>التحسين الآلي للخريطة</h2>
          {article_autopilot_html}
        </section>

        <section class="section">
          <h2>المزودات المتاحة</h2>
          <div class="provider-grid">
            {_render_provider_cards(panel_state)}
          </div>
        </section>

        <section class="section">
          <h2>إعدادات التبديل والتوكونات</h2>
          <div class="note">
            اترك حقل التوكن فارغًا إذا كنت تريد الاحتفاظ بالقيمة الحالية. إذا أردت مقارنة عادلة بين النماذج، اختر النموذج المناسب لكل مزود ثم نفّذ المقارنة الثلاثية من القسم السفلي.
          </div>
          <form method="post" action="/admin/runtime-settings">
            <div class="form-grid">
              <label>المسار النشط للمولد
                <select name="active_provider">
                  {_render_active_provider_options(panel_state)}
                </select>
              </label>
              <label>Temperature
                <input type="number" step="0.05" min="0" max="1.5" name="temperature" value="{_escape(panel_state['temperature'])}">
              </label>
              <label>Max Tokens
                <input type="number" min="128" max="8192" name="max_tokens" value="{_escape(panel_state['max_tokens'])}">
              </label>
              <label>نموذج OpenRouter
                {openrouter_select}
              </label>
              <label>نموذج Gemini
                {gemini_select}
              </label>
              <label>نموذج Ollama
                {ollama_select}
              </label>
              <label>نموذج MLX Local
                {mlx_local_select}
              </label>
              <label>OpenRouter API Key
                <input type="password" name="openrouter_api_key" placeholder="اتركه فارغًا للاحتفاظ بالقيمة الحالية">
              </label>
              <label>Gemini API Key
                <input type="password" name="gemini_api_key" placeholder="اتركه فارغًا للاحتفاظ بالقيمة الحالية">
              </label>
              <label>OpenAI API Key للـ Embeddings
                <input type="password" name="openai_api_key" placeholder="اتركه فارغًا للاحتفاظ بالقيمة الحالية">
              </label>
              <label>Gemini API Base URL
                <input type="text" name="gemini_api_base_url" value="{_escape(gemini_provider['connection_target'])}">
              </label>
              <label>Ollama Base URL
                <input type="text" name="ollama_base_url" value="{_escape(ollama_provider['connection_target'])}">
              </label>
              <label>ملف الإعدادات المحلية
                <input type="text" value="{_escape(panel_state['runtime_settings_path'])}" readonly>
              </label>
            </div>
            <div class="note">
              قوائم النماذج تُقرأ مباشرة من كل مزود: <code>OpenRouter</code> و<code>Gemini</code> و<code>Ollama</code>، بينما <code>MLX Local</code> يعرض مسار نموذج Gemma المحلي مع routing adapters من ملف النشر. إذا أضفت نموذجًا محليًا جديدًا عبر Ollama أو حدّثت مسار Gemma المحلي أو تغيّرت قوائم السحابة، يكفي تحديث الصفحة لتظهر التغييرات.
            </div>
            <button type="submit">حفظ الإعدادات</button>
          </form>
        </section>

        <section class="section">
          <h2>مقارنة ثلاثية بين المولدات</h2>
          <div class="note">
            هذا الاختبار يستخدم نفس سياق <strong>RAG</strong> المحلي، ثم يمرّر الجواب إلى المولدات التي تختارها هنا، بما فيها <strong>MLX Local</strong> الخاص بـ Gemma. يمكنك أيضًا تحديد <strong>صيغة الإخراج</strong> بين الاستشارة المرجعية أو الرأي القانوني/المذكرة، حتى نقارن النماذج على نفس الاسترجاع ونفس أسلوب الصياغة.
          </div>
          <form id="compare-form" method="post" action="/admin/compare">
            <label>السؤال القانوني للمقارنة
              <textarea name="question" placeholder="مثال: ما الضوابط النظامية المتعلقة بمسؤولية المدير في نظام الشركات؟">{_escape(compare_payload['question'] if compare_payload else '')}</textarea>
            </label>
            <label>صيغة الإجابة للاختبار
              <select name="answer_mode">
                {_render_answer_mode_select(selected_answer_mode)}
              </select>
            </label>
            <div class="muted">الصيغة الحالية للاختبار: {_escape(selected_answer_mode_label)}</div>
            <div class="chips">
              {_render_compare_provider_chips(panel_state, selected_compare_providers)}
            </div>
            <button type="submit">نفّذ المقارنة الآن</button>
          </form>
          <div id="compare-status"></div>
        </section>

        <div id="compare-results">{compare_html}</div>
      </div>
      <script>
        (function() {{
          const form = document.getElementById("compare-form");
          const statusBox = document.getElementById("compare-status");
          const resultsBox = document.getElementById("compare-results");
          if (!form || !statusBox || !resultsBox) return;

          const escapeHtml = (value) => {{
            return String(value ?? "")
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
          }};

          const renderSources = (sources) => {{
            if (!sources || !sources.length) {{
              return "<div class='muted'>لا توجد مصادر ظاهرة لهذه النتيجة.</div>";
            }}
            return sources.slice(0, 4).map((source) => (
              "<div class='source-box'><pre>" + escapeHtml(String(source).slice(0, 1200)) + "</pre></div>"
            )).join("");
          }};

          const renderCompareResults = (payload) => {{
            if (!payload.results || !payload.results.length) {{
              resultsBox.innerHTML = "";
              return;
            }}
            const answerModeLine = payload.answer_mode_label
              ? "<div class='muted'>صيغة الإخراج المختبرة: " + escapeHtml(payload.answer_mode_label) + "</div>"
              : "";
            const cards = payload.results.map((result) => (
              "<div class='compare-card'>"
              + "<h3>" + escapeHtml(result.title) + "</h3>"
              + "<div class='muted'>المولد: " + escapeHtml(result.provider_label) + " / " + escapeHtml(result.model) + "</div>"
              + "<div class='muted'>الثقة: " + escapeHtml(result.confidence) + "</div>"
              + "<pre>" + escapeHtml(result.answer) + "</pre>"
              + renderSources(result.sources || [])
              + (result.error ? "<div class='banner error'>" + escapeHtml(result.error) + "</div>" : "")
              + "</div>"
            )).join("");
            resultsBox.innerHTML = "<section class='section wide'><h2>نتيجة المقارنة على السؤال نفسه</h2>" + answerModeLine + "<div class='compare-grid'>" + cards + "</div></section>";
          }};

          const renderStatus = (kind, message) => {{
            statusBox.innerHTML = message
              ? "<div class='banner " + kind + "'>" + escapeHtml(message) + "</div>"
              : "";
          }};

          const renderJob = (payload) => {{
            const progress = Number(payload.progress ?? 0);
            const steps = Array.isArray(payload.steps) ? payload.steps : [];
            const stage = payload.stage || "";
            const result = payload.result || {{}};
            const stepHtml = steps.map((step) => {{
              let cls = "job-step";
              const index = steps.indexOf(step);
              const activeIndex = steps.indexOf(stage);
              if (index < activeIndex || payload.status === "completed") cls += " done";
              if (step === stage) cls += " active";
              return "<span class='" + cls + "'>" + escapeHtml(step) + "</span>";
            }}).join("");
            let details = "";
            if (result.decision) {{
              details += "<div class='chips'>"
                + "<span class='chip'>قرار الجولة: " + escapeHtml(result.decision) + "</span>"
                + "<span class='chip'>الدرجة: " + escapeHtml(result.article_score) + "</span>"
                + "<span class='chip'>failed: " + escapeHtml(result.failed_cases) + "</span>"
                + "<span class='chip'>retrieval: " + escapeHtml((result.classification_counts || {{}})["retrieval/package issue"] || 0) + "</span>"
                + "<span class='chip'>operational: " + escapeHtml((result.classification_counts || {{}})["operational issue"] || 0) + "</span>"
                + "</div>";
            }}
            const bannerKind = payload.status === "completed" ? "success" : (payload.status === "failed" ? "error" : "warning");
            statusBox.innerHTML =
              "<div class='banner " + bannerKind + "'>"
              + "<div>" + escapeHtml(payload.message || "جولة التحسين الآلية تعمل في الخلفية.") + "</div>"
              + "<div class='progress-wrap'>"
              + "<div class='progress-track'><div class='progress-bar' style='width:" + Math.max(0, Math.min(100, progress)) + "%'></div></div>"
              + "<div class='muted'>التقدم: " + escapeHtml(progress) + "% · المرحلة: " + escapeHtml(stage || "—") + "</div>"
              + "</div>"
              + "<div class='job-steps'>" + stepHtml + "</div>"
              + details
              + "</div>";
          }};

          const pollJob = async (jobId) => {{
            while (true) {{
              const response = await fetch("/admin/compare/status/" + encodeURIComponent(jobId), {{
                headers: {{ "Accept": "application/json" }}
              }});
              const payload = await response.json();
              if (!response.ok) {{
                renderStatus("error", payload.error || "تعذر قراءة حالة المقارنة.");
                return;
              }}
              if (payload.status === "completed") {{
                renderStatus("success", "اكتملت المقارنة بنجاح.");
                renderCompareResults(payload);
                return;
              }}
              if (payload.status === "failed") {{
                renderStatus("error", payload.error || "فشلت المقارنة.");
                resultsBox.innerHTML = "";
                return;
              }}
              renderStatus("warning", "المقارنة جارية الآن. قد تستغرق وقتًا أطول عند تشغيل أكثر من مزود.");
              await new Promise((resolve) => setTimeout(resolve, 1200));
            }}
          }};

          form.addEventListener("submit", async (event) => {{
            event.preventDefault();
            const submitButton = form.querySelector("button[type='submit']");
            const formData = new FormData(form);
            renderStatus("warning", "جارٍ بدء المقارنة...");
            resultsBox.innerHTML = "";
            if (submitButton) submitButton.disabled = true;
            try {{
              const response = await fetch("/admin/compare/start", {{
                method: "POST",
                body: formData,
                headers: {{ "Accept": "application/json" }}
              }});
              const payload = await response.json();
              if (!response.ok || !payload.job_id) {{
                renderStatus("error", payload.error || "تعذر بدء المقارنة.");
                return;
              }}
              await pollJob(payload.job_id);
            }} catch (error) {{
              renderStatus("error", "حدث خطأ أثناء تشغيل المقارنة.");
            }} finally {{
              if (submitButton) submitButton.disabled = false;
            }}
          }});
        }})();
        (function() {{
          const form = document.getElementById("article-audit-form");
          const statusBox = document.getElementById("article-audit-status");
          if (!form || !statusBox) return;

          const escapeHtml = (value) => {{
            return String(value ?? "")
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
          }};

          const renderStatus = (kind, message) => {{
            statusBox.innerHTML = message
              ? "<div class='banner " + kind + "'>" + escapeHtml(message) + "</div>"
              : "";
          }};

          const pollJob = async (jobId) => {{
            while (true) {{
              const response = await fetch("/admin/article-audit/status/" + encodeURIComponent(jobId), {{
                headers: {{ "Accept": "application/json" }}
              }});
              const payload = await response.json();
              if (!response.ok) {{
                renderStatus("error", payload.error || "تعذر قراءة حالة تدقيق دقة الجمع.");
                return;
              }}
              if (payload.status === "completed") {{
                renderStatus("success", payload.message || "اكتمل تدقيق دقة الجمع.");
                setTimeout(() => window.location.reload(), 900);
                return;
              }}
              if (payload.status === "failed") {{
                renderStatus("error", payload.error || payload.message || "فشل تدقيق دقة الجمع.");
                return;
              }}
              renderStatus("warning", payload.message || "تدقيق دقة الجمع يعمل في الخلفية.");
              await new Promise((resolve) => setTimeout(resolve, 1800));
            }}
          }};

          form.addEventListener("submit", async (event) => {{
            event.preventDefault();
            const submitButton = form.querySelector("button[type='submit']");
            renderStatus("warning", "جارٍ بدء تدقيق دقة الجمع...");
            if (submitButton) submitButton.disabled = true;
            try {{
              const response = await fetch("/admin/article-audit/start", {{
                method: "POST",
                headers: {{ "Accept": "application/json" }}
              }});
              const payload = await response.json();
              if (!response.ok || !payload.job_id) {{
                renderStatus("error", payload.error || "تعذر بدء تدقيق دقة الجمع.");
                return;
              }}
              await pollJob(payload.job_id);
            }} catch (error) {{
              renderStatus("error", "حدث خطأ أثناء تشغيل تدقيق دقة الجمع.");
            }} finally {{
              if (submitButton) submitButton.disabled = false;
            }}
          }});
        }})();
        (function() {{
          const form = document.getElementById("article-autopilot-form");
          const developmentForm = document.getElementById("article-autopilot-development-form");
          const stopForm = document.getElementById("article-autopilot-stop-form");
          const improveForm = document.getElementById("article-autopilot-improve-form");
          const statusBox = document.getElementById("article-autopilot-status");
          const developmentCyclesPanel = document.getElementById("development-cycles-panel");
          const horizontalCoveragePanel = document.getElementById("horizontal-coverage-panel");
          if (!form || !statusBox) return;
          let currentJobId = "";
          let pollingJobId = "";
          let pollGeneration = 0;

          const escapeHtml = (value) => {{
            return String(value ?? "")
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;");
          }};

          const autopilotDetailsStateKey = (key) => "article-autopilot:details:" + key;
          const autopilotDetailsIsOpen = (key) => {{
            try {{
              return window.localStorage.getItem(autopilotDetailsStateKey(key)) === "open";
            }} catch (error) {{
              return false;
            }}
          }};
          const rememberAutopilotDetailsState = (root = document) => {{
            root.querySelectorAll("details[data-details-key]").forEach((details) => {{
              const key = details.dataset.detailsKey || "";
              if (!key || details.dataset.detailsBound === "1") return;
              details.open = autopilotDetailsIsOpen(key);
              details.addEventListener("toggle", () => {{
                try {{
                  window.localStorage.setItem(autopilotDetailsStateKey(key), details.open ? "open" : "closed");
                }} catch (error) {{}}
              }});
              details.dataset.detailsBound = "1";
            }});
          }};

          const renderStatus = (kind, message) => {{
            statusBox.innerHTML = message
              ? "<div class='banner " + kind + "'>" + escapeHtml(message) + "</div>"
              : "";
          }};

          const renderTaskTable = (rows, summary = {{}}) => {{
            const items = Array.isArray(rows) ? rows : [];
            const successes = items.filter((item) => item.status === "نجح").length;
            const operational = items.filter((item) => item.is_operational || item.status === "تشغيلي").length;
            const total = items.length - operational;
            const rate = total ? Math.round((successes / total) * 1000) / 10 : 0;
            const scored = items.filter((item) => !(item.is_operational || item.status === "تشغيلي") && item.article_points !== undefined && item.article_points !== null);
            const rowAverage = scored.length
              ? Math.round((scored.reduce((sum, item) => sum + Number(item.article_points || 0), 0) / scored.length) * 10) / 10
              : "—";
            const average = summary.score_average ?? summary.article_score ?? rowAverage;
            const nearMiss = summary.near_miss_count ?? items.filter((item) => item.status !== "نجح" && Number(item.article_points || 0) >= 90).length;
            const partial = summary.partial_count ?? items.filter((item) => item.status !== "نجح" && Number(item.article_points || 0) >= 50 && Number(item.article_points || 0) < 90).length;
            const low = summary.low_count ?? items.filter((item) => item.status !== "نجح" && Number(item.article_points || 0) < 50 && !(item.is_operational || item.status === "تشغيلي")).length;
            const body = items.length
              ? items.map((item) => {{
                  const badge = item.status === "نجح" ? "ok" : ((item.is_operational || item.status === "تشغيلي") ? "warn" : "danger");
                  return "<tr>"
                    + "<td><div class='task-title'>" + escapeHtml(item.domain || "") + "</div>"
                    + "<div class='muted'>" + escapeHtml(item.question || "") + "</div>"
                    + "<div class='chips'><span class='chip'>" + escapeHtml(item.question_id || "") + "</span><span class='badge " + badge + "'>" + escapeHtml(item.status || "") + "</span><span class='chip'>" + escapeHtml(item.score_label || "") + "</span></div></td>"
                    + "<td>" + escapeHtml(item.collected || "") + "<div class='muted'>درجة الاقتراب: " + escapeHtml(item.article_points ?? "—") + "/100</div></td>"
                    + "<td>" + escapeHtml(item.comment || "") + "<div class='muted'>حالة النماذج: " + escapeHtml(item.review_status || "—") + "</div></td>"
                    + "<td>" + escapeHtml(item.action || "") + "</td>"
                    + "</tr>";
                }}).join("")
              : "<tr><td colspan='4'>لا توجد قضايا في هذه الجولة بعد.</td></tr>";
            const footer = total
              ? "متوسط الاقتراب: " + escapeHtml(average) + "/100 · نجاح الجمع: " + successes + " / " + total + " = " + rate + "% · قريب جدًا: " + nearMiss + " · جزئي: " + partial + " · بعيد: " + low + (operational ? " · أعطال تشغيلية مستبعدة: " + operational : "")
              : "معدل النجاح: لا توجد قضايا بعد" + (operational ? " · أعطال تشغيلية مستبعدة: " + operational : "");
            return "<div class='task-table-wrap'><h3>مهام آخر جولة مكتملة</h3>"
              + "<table class='task-table'><thead><tr><th>القضية</th><th>الجمع الذي تم</th><th>التعليق</th><th>إجراءات التحسين</th></tr></thead>"
              + "<tbody>" + body + "</tbody><tfoot><tr><td colspan='4'>" + footer + "</td></tr></tfoot></table></div>";
          }};

          const renderSuccessTable = (rows) => {{
            const items = Array.isArray(rows) ? rows : [];
            const body = items.length
              ? items.map((item) => {{
                  return "<tr>"
                    + "<td><div class='task-title'>" + escapeHtml(item.domain || "") + "</div>"
                    + "<div class='muted'>" + escapeHtml(item.question || "") + "</div>"
                    + "<div class='chips'><span class='chip'>" + escapeHtml(item.question_id || "") + "</span><span class='badge ok'>نجح</span></div></td>"
                    + "<td>" + escapeHtml(item.collected || "") + "<div class='muted'>درجة الاقتراب: " + escapeHtml(item.article_points ?? "—") + "/100</div></td>"
                    + "<td>" + escapeHtml(item.comment || "") + "<div class='muted'>حالة النماذج: " + escapeHtml(item.review_status || "—") + "</div></td>"
                    + "<td>" + escapeHtml(item.action || "") + "</td>"
                    + "</tr>";
                }}).join("")
              : "<tr><td colspan='4'>لا توجد ترقيات ناجحة في بنك التوسعة بعد.</td></tr>";
            return "<div class='task-table-wrap'><h3>آخر الترقيات الناجحة</h3>"
              + "<table class='task-table'><thead><tr><th>القضية</th><th>الجمع الذي تم</th><th>التعليق</th><th>إجراءات التحسين</th></tr></thead>"
              + "<tbody>" + body + "</tbody></table></div>";
          }};

          const renderRoundHistory = (rows) => {{
            const items = Array.isArray(rows) ? rows : [];
            const body = items.length
              ? items.map((item) => {{
                  const badge = item.decision === "PASS" ? "ok" : (item.decision === "FAIL" ? "danger" : "warn");
                  return "<tr>"
                    + "<td><div class='task-title'>" + escapeHtml(item.round || "") + "</div><div class='muted'>" + escapeHtml(item.created_at || "") + "</div></td>"
                    + "<td><span class='badge " + badge + "'>" + escapeHtml(item.decision || "") + "</span></td>"
                    + "<td>متوسط الاقتراب: " + escapeHtml(item.score_average ?? item.article_score ?? "—") + "/100"
                    + "<div class='muted'>نجاح: " + escapeHtml(item.pass_rate ?? "—") + " · قريب جدًا: " + escapeHtml(item.near_miss_count ?? 0) + " · المدة: " + escapeHtml(item.duration_label || "—") + "</div>"
                    + "<div class='muted'>مواد جديدة: " + escapeHtml(item.unsupported_expected_label || "—") + " · مدعوم تعثر: " + escapeHtml(item.supported_missing_label || "—") + " · غير موجه: " + escapeHtml(item.unrouted_expected_label || "—") + "</div></td>"
                    + "<td>" + escapeHtml(item.summary || "") + "</td>"
                    + "</tr>";
                }}).join("")
              : "<tr><td colspan='4'>لا توجد جولات محفوظة بعد.</td></tr>";
            return "<div class='task-table-wrap'><h3>تاريخ آخر الجولات</h3>"
              + "<table class='task-table compact-history'><thead><tr><th>الجولة</th><th>الحالة</th><th>القياس</th><th>الملخص</th></tr></thead>"
              + "<tbody>" + body + "</tbody></table></div>";
          }};

          const improvementDecisionClass = (decision) => {{
            if (["ACCEPTED", "ACCEPTED_AFTER_RETRY", "ACCEPTED_WITH_DEFERRED_FAILURES", "ACCEPTED_WITH_HOLDOUT_BACKLOG", "ACCEPTED_WITH_MOVING_HOLDOUT_BACKLOG", "NO_RAG_CHANGE_NEEDED", "OPERATIONAL_ONLY_NO_RAG_CHANGE"].includes(decision)) return "ok";
            if (decision === "REJECTED_ROLLED_BACK") return "danger";
            return "warn";
          }};

          const qualityNumberClass = (score) => {{
            if (score === null || score === undefined || score === "") return "unknown";
            const value = Number(score);
            if (!Number.isFinite(value)) return "unknown";
            if (value >= 90) return "ok";
            if (value >= 70) return "warn";
            return "danger";
          }};

          const renderHorizontalCoverage = (coverage) => {{
            const c = coverage || {{}};
            const stablePercentRaw = Number(c.stable_quality_score ?? c.practical_percent ?? c.pair_percent ?? 0);
            const stablePercent = Number.isFinite(stablePercentRaw) ? stablePercentRaw : 0;
            const cls = c.stable_quality_class || qualityNumberClass(stablePercent);
            const frontierPercentRaw = Number(c.frontier_signal_percent ?? c.recent_signal_percent ?? 0);
            const frontierPercent = Number.isFinite(frontierPercentRaw) ? frontierPercentRaw : 0;
            const frontierCls = c.frontier_signal_class || qualityNumberClass(frontierPercent);
            const width = Math.max(0, Math.min(100, stablePercent));
            const remaining = c.phases_remaining_estimate === null || c.phases_remaining_estimate === undefined ? "—" : c.phases_remaining_estimate;
            const estimatedTotal = c.estimated_total_phases === null || c.estimated_total_phases === undefined ? "—" : c.estimated_total_phases;
            const stableComponents = Array.isArray(c.stable_quality_components) ? c.stable_quality_components : [];
            const stableComponentChips = stableComponents.map((item) => {{
              return "<span class='chip'>" + escapeHtml(item.label || item.key || "gate")
                + ": " + escapeHtml(item.score_label || "—")
                + " · " + escapeHtml(item.cases_total ?? 0) + " حالة"
                + " · فشل " + escapeHtml(item.failed_cases ?? 0)
                + "</span>";
            }}).join("");
            return "<div class='coverage-header'>"
              + "<div><h3>مؤشر الجودة المستقرة</h3><div class='muted'>" + escapeHtml(c.stable_quality_method || "") + "</div></div>"
              + "<div class='quality-number " + cls + "'>" + escapeHtml(c.stable_quality_label || c.practical_percent_label || "—") + "</div>"
              + "</div>"
              + "<div class='coverage-metric-row'>"
              + "<div class='coverage-metric'><div class='metric-title'>الجودة المقفلة</div>"
              + "<div class='quality-number " + cls + "'>" + escapeHtml(c.stable_quality_label || "—") + "</div>"
              + "<div class='muted'>" + escapeHtml(c.stable_quality_status || "—") + " · حالات مستقرة " + escapeHtml(c.stable_quality_cases ?? 0) + " · فشل " + escapeHtml(c.stable_quality_failed_cases ?? 0) + "</div></div>"
              + "<div class='coverage-metric'><div class='metric-title'>الاستكشاف الجاري</div>"
              + "<div class='quality-number " + frontierCls + "'>" + escapeHtml(c.frontier_signal_label || "—") + "</div>"
              + "<div class='muted'>" + escapeHtml(c.frontier_status || "—") + " · فجوة استكشافية " + escapeHtml(c.frontier_gap_label || "—") + "</div></div>"
              + "</div>"
              + "<div class='progress-wrap'>"
              + "<div class='progress-track'><div class='progress-bar' style='width:" + width + "%'></div></div>"
              + "<div class='muted'>مواد مدعومة: " + escapeHtml(c.supported_article_pairs ?? 0)
              + " / " + escapeHtml(c.eligible_article_pairs ?? 0)
              + " · المتبقي: " + escapeHtml(c.remaining_article_pairs ?? 0) + "</div>"
              + "</div>"
              + "<div class='chips'>"
              + stableComponentChips
              + "<span class='chip'>المؤشر المختلط القديم: " + escapeHtml(c.practical_percent_label || "—") + "</span>"
              + "<span class='chip'>تغطية نظرية: " + escapeHtml(c.pair_percent_label || "—") + "</span>"
              + "<span class='chip'>تغطية الأنظمة: " + escapeHtml(c.supported_slugs ?? 0) + " / " + escapeHtml(c.eligible_slugs ?? 0) + " = " + escapeHtml(c.slug_percent_label || "—") + "</span>"
              + "<span class='chip'>إشارة آخر الجولات: " + escapeHtml(c.frontier_signal_label || c.recent_signal_label || "—") + "</span>"
              + "<span class='chip'>مواد جديدة: " + escapeHtml(c.recent_new_rate_label || "—") + "</span>"
              + "<span class='chip'>تعثر مدعوم: " + escapeHtml(c.recent_supported_gap_label || "—") + "</span>"
              + "<span class='chip'>غير موجه: " + escapeHtml(c.recent_route_gap_label || "—") + "</span>"
              + "<span class='chip'>ثابت مواد: " + escapeHtml(c.fixed_holdout_score_label || "—") + "</span>"
              + "<span class='chip'>ثابت محاور: " + escapeHtml(c.fixed_holdout_axis_coverage_label || "—") + "</span>"
              + "<span class='chip'>مصدر الثابت: " + escapeHtml(c.fixed_holdout_source || "—") + "</span>"
              + "<span class='chip'>استكشافي مواد: " + escapeHtml(c.holdout_score_label || "—") + "</span>"
              + "<span class='chip'>استكشافي محاور: " + escapeHtml(c.holdout_axis_coverage_label || "—") + "</span>"
              + "<span class='chip'>مراحل مكتملة: " + escapeHtml(c.phases_completed ?? 0) + "</span>"
              + "<span class='chip'>متبقي تقريبي: " + escapeHtml(remaining) + " دفعة</span>"
              + "<span class='chip'>زمن متبقٍ تقريبي: " + escapeHtml(c.remaining_time_label || "—") + "</span>"
              + "<span class='chip'>إجمالي تقديري: " + escapeHtml(estimatedTotal) + " دفعة</span>"
              + "<span class='chip'>معدل التوسع الأخير: " + escapeHtml(c.recent_pairs_per_phase_label || "—") + "</span>"
              + "<span class='chip'>متوسط زمن الدفعة: " + escapeHtml(c.recent_phase_duration_label || "—") + "</span>"
              + "<span class='chip'>" + escapeHtml(c.stage || "—") + "</span>"
              + "</div>";
          }};

          const renderDevelopmentCycles = (rows, summary) => {{
            const items = Array.isArray(rows) ? rows : [];
            const s = summary || {{}};
            const rowHtml = (item) => {{
              const badge = improvementDecisionClass(item.decision || "");
              const qualityClass = item.pre_quality_class || qualityNumberClass(item.pre_quality_score);
              return "<tr>"
                + "<td><div class='task-title'>" + escapeHtml(item.created_at_display || item.created_at || "") + "</div><div class='muted'>" + escapeHtml(item.manifest_label || "") + "</div></td>"
                + "<td><div class='quality-number " + escapeHtml(qualityClass) + "'>" + escapeHtml(item.pre_quality_label || "—") + "</div><div class='muted'>قبل التحسين · " + escapeHtml(item.pre_quality_cases ?? 0) + " قضية</div><div class='muted'>مرور أولي: " + escapeHtml(item.pre_quality_pass_rate_label || "—") + "</div></td>"
                + "<td><span class='badge " + badge + "'>" + escapeHtml(item.decision || "") + "</span><div class='muted'>محاولات: " + escapeHtml(item.attempts ?? "—") + " · اعتمد بعد: " + escapeHtml(item.accepted_after_attempt ?? "—") + "</div></td>"
                + "<td>نجاح: " + escapeHtml(item.success_ratio || "—") + "<div class='muted'>الفجوات: " + escapeHtml(item.gap_rate ?? "—") + "% · الدفعة: " + escapeHtml(item.batch_rounds ?? "—") + " جولة</div></td>"
                + "<td>دفعة: " + escapeHtml(item.validation_score ?? "—") + "/100 · يدوي: " + escapeHtml(item.manual_score ?? "—") + "/100 · ثابت: " + escapeHtml(item.fixed_holdout_score ?? "—") + "/100 · استكشافي: " + escapeHtml(item.holdout_score ?? "—") + "/100"
                + "<div class='muted'>MRR: " + escapeHtml(item.article_mrr ?? "—") + " · تلوث: " + escapeHtml(item.pollution_rate ?? "—") + "</div>"
                + "<div class='muted'>تشخيص آلي: " + escapeHtml(item.auto_failure_gate ?? "—") + "/" + escapeHtml(item.auto_failure_cause ?? "—") + " · وصفة: " + escapeHtml(item.auto_recipe ?? "—") + "</div>"
                + "<div class='muted'>نمط عميق: " + escapeHtml(item.auto_deep_failure_mode ?? "—") + " · تكرار: " + escapeHtml(item.auto_same_failure_count ?? "—") + " · تصعيد: " + escapeHtml(item.auto_recipe_escalation_reason ?? "—") + "</div>"
                + "<div class='muted'>السبب الأعلى: " + escapeHtml(item.top_root_cause || "—") + "</div></td>"
                + "<td>" + escapeHtml(item.action_note || "") + "</td>"
                + "</tr>";
            }};
            const tableHtml = (body, title) => {{
              return "<div class='task-table-wrap priority-table'>"
                + (title ? "<h3>" + escapeHtml(title) + "</h3>" : "")
                + "<table class='task-table compact-history'><thead><tr><th>التاريخ والوقت</th><th>جودة قبل التحسين</th><th>قرار التحسين</th><th>نجاح / فجوات</th><th>التحقق</th><th>القرار التالي</th></tr></thead>"
                + "<tbody>" + body + "</tbody></table></div>";
            }};
            const latest = items.slice(0, 5);
            const older = items.slice(5);
            const latestBody = latest.length
              ? latest.map(rowHtml).join("")
              : "<tr><td colspan='6'>لا توجد دورات تحسين محفوظة بعد.</td></tr>";
            const olderBody = older.length
              ? older.map(rowHtml).join("")
              : "<tr><td colspan='6'>لا توجد دورات أقدم من آخر خمس دورات.</td></tr>";
            const olderOpen = autopilotDetailsIsOpen("development-cycle-archive") ? " open" : "";
            return "<div class='chips'>"
              + "<span class='chip'>دورات التحسين: " + escapeHtml(s.total_cycles ?? items.length) + "</span>"
              + "<span class='chip'>معتمدة: " + escapeHtml(s.accepted_cycles ?? "—") + "</span>"
              + "<span class='chip'>متوسط جودة ما قبل التحسين: " + escapeHtml(s.average_pre_quality_label ?? "—") + "</span>"
              + "<span class='chip'>جولات محسّنة: " + escapeHtml(s.total_batch_rounds ?? "—") + "</span>"
              + "<span class='chip'>قضايا تحقق: " + escapeHtml(s.total_validation_cases ?? "—") + "</span>"
              + "</div>"
              + tableHtml(latestBody, "متابعة التطوير المستمر - آخر 5 دورات")
              + "<details class='secondary-section' data-details-key='development-cycle-archive'" + olderOpen + "><summary>الدورات الأقدم من آخر خمس (" + escapeHtml(older.length) + ")</summary><div class='secondary-content'>"
              + tableHtml(olderBody, "")
              + "</div></details>";
          }};

          rememberAutopilotDetailsState();

          const renderJob = (payload) => {{
            const progress = Number(payload.progress ?? 0);
            const steps = Array.isArray(payload.steps) ? payload.steps : [];
            const stage = payload.stage || "";
            const result = payload.result || {{}};
            const activeIndex = steps.indexOf(stage);
            const stepHtml = steps.map((step, index) => {{
              let cls = "job-step";
              if ((activeIndex >= 0 && index < activeIndex) || ["completed", "stopped"].includes(payload.status)) cls += " done";
              if (step === stage) cls += " active";
              return "<span class='" + cls + "'>" + escapeHtml(step) + "</span>";
            }}).join("");
            const bannerKind = payload.status === "failed" ? "error" : (["completed", "stopped"].includes(payload.status) ? "success" : "warning");
            let details = "";
            if (result.decision) {{
              if (developmentCyclesPanel) {{
                developmentCyclesPanel.innerHTML = renderDevelopmentCycles(result.development_cycle_rows || [], result.development_cycle_summary || {{}});
                rememberAutopilotDetailsState(developmentCyclesPanel);
              }}
              if (horizontalCoveragePanel && result.horizontal_coverage) {{
                horizontalCoveragePanel.innerHTML = renderHorizontalCoverage(result.horizontal_coverage);
	              }}
	              details += "<div class='chips'>"
	                + "<span class='chip'>مواد جديدة: " + escapeHtml((result.current_round_support_gap || {{}}).unsupported_expected_label || "—") + "</span>"
	                + "<span class='chip'>مدعوم تعثر: " + escapeHtml((result.current_round_support_gap || {{}}).supported_missing_label || "—") + "</span>"
	                + "<span class='chip'>غير موجه: " + escapeHtml((result.current_round_support_gap || {{}}).unrouted_expected_label || "—") + "</span>"
	                + "<span class='chip'>متوسط الجديد آخر الجولات: " + escapeHtml((result.round_support_summary || {{}}).unsupported_expected_label || "—") + "</span>"
	                + "<span class='chip'>متوسط المدعوم المتعثر: " + escapeHtml((result.round_support_summary || {{}}).supported_missing_label || "—") + "</span>"
	                + "<span class='chip'>متوسط غير الموجه: " + escapeHtml((result.round_support_summary || {{}}).unrouted_expected_label || "—") + "</span>"
	                + "<span class='chip'>الدفعات المحسّنة: " + escapeHtml(payload.completed_batches || 0) + "</span>"
                + "<span class='chip'>الدفعة الحالية: " + escapeHtml(payload.current_batch_round || 0) + " / " + escapeHtml(payload.batch_round_limit || "—") + "</span>"
                + "<span class='chip'>إجمالي الجولات: " + escapeHtml(payload.completed_rounds || 0) + "</span>"
                + "<span class='chip'>قرار آخر جولة: " + escapeHtml(result.decision) + "</span>"
                + "<span class='chip'>متوسط الاقتراب: " + escapeHtml(result.score_average ?? result.article_score) + "/100</span>"
                + "<span class='chip'>قريب جدًا: " + escapeHtml(result.near_miss_count || 0) + "</span>"
                + "<span class='chip'>failed: " + escapeHtml(result.failed_cases) + "</span>"
                + "<span class='chip'>retrieval: " + escapeHtml((result.classification_counts || {{}})["retrieval/package issue"] || 0) + "</span>"
                + "<span class='chip'>operational: " + escapeHtml((result.classification_counts || {{}})["operational issue"] || 0) + "</span>"
                + "</div>"
                + "<details class='secondary-section'><summary>تفاصيل الجولة والمؤشرات الثانوية</summary><div class='secondary-content'>"
                + renderTaskTable(result.task_rows || [], result)
                + renderSuccessTable(result.success_rows || [])
                + renderRoundHistory(result.round_history || [])
                + "</div></details>";
            }}
            if (payload.manifest && payload.manifest.decision) {{
              const manifest = payload.manifest;
              const validation = manifest.validation_summary || {{}};
              const manual = manifest.manual_summary || {{}};
              const diagnosis = manifest.diagnosis || {{}};
              details += "<div class='chips'>"
                + "<span class='chip'>قرار تحسين RAG: " + escapeHtml(manifest.decision) + "</span>"
                + "<span class='chip'>إعادة اختبار الدفعة: " + escapeHtml(validation.article_score_100 ?? "—") + "/100</span>"
                + "<span class='chip'>نجاح/إجمالي: " + escapeHtml((Number(validation.cases_total || 0) - Number(validation.failed_cases || 0)) + ' / ' + (validation.cases_total ?? '—')) + "</span>"
                + "<span class='chip'>فشل مرحّل: " + escapeHtml(manifest.deferred_failure_count ?? 0) + "</span>"
                + "<span class='chip'>الشريحة اليدوية: " + escapeHtml(manual.article_score_100 ?? "—") + "/100</span>"
                + "<span class='chip'>سبب جذري أعلى: " + escapeHtml(Object.keys(diagnosis.root_cause_counts || {{}})[0] || "—") + "</span>"
                + "</div>";
            }}
            statusBox.innerHTML =
              "<div class='banner " + bannerKind + "'>"
              + "<div>" + escapeHtml(payload.message || "مهمة الجمع/التحسين تعمل في الخلفية.") + "</div>"
              + "<div class='progress-wrap'>"
              + "<div class='progress-track'><div class='progress-bar' style='width:" + Math.max(0, Math.min(100, progress)) + "%'></div></div>"
              + "<div class='muted'>التقدم: " + escapeHtml(progress) + "% · المرحلة: " + escapeHtml(stage || "—") + "</div>"
              + "</div>"
              + "<div class='job-steps'>" + stepHtml + "</div>"
              + details
              + "</div>";
          }};

          const pollJob = async (jobId) => {{
            if (pollingJobId === jobId) return;
            pollingJobId = jobId;
            const generation = ++pollGeneration;
            let consecutiveErrors = 0;
            while (true) {{
              if (generation !== pollGeneration) return;
              try {{
                const response = await fetch("/admin/article-autopilot/status/" + encodeURIComponent(jobId), {{
                  headers: {{ "Accept": "application/json" }},
                  cache: "no-store"
                }});
                const payload = await response.json();
                if (!response.ok) {{
                  consecutiveErrors += 1;
                  renderStatus("warning", payload.error || "تعذر تحديث اللوحة لحظيًا؛ ستُعاد المحاولة تلقائيًا.");
                  await new Promise((resolve) => setTimeout(resolve, Math.min(12000, 2500 + consecutiveErrors * 1000)));
                  continue;
                }}
                consecutiveErrors = 0;
                if (payload.status === "completed") {{
                  renderJob(payload);
                  pollingJobId = "";
                  setTimeout(() => window.location.reload(), 900);
                  return;
                }}
                if (payload.status === "stopped") {{
                  renderJob(payload);
                  pollingJobId = "";
                  setTimeout(() => window.location.reload(), 900);
                  return;
                }}
                if (payload.status === "failed") {{
                  renderStatus("error", payload.error || payload.message || "فشلت جولة التحسين الآلية.");
                  pollingJobId = "";
                  return;
                }}
                renderJob(payload);
              }} catch (error) {{
                consecutiveErrors += 1;
                renderStatus("warning", "انقطع تحديث اللوحة لحظيًا؛ النظام سيعيد المزامنة تلقائيًا.");
                await new Promise((resolve) => setTimeout(resolve, Math.min(12000, 2500 + consecutiveErrors * 1000)));
                continue;
              }}
              await new Promise((resolve) => setTimeout(resolve, 2200));
            }}
          }};

          const syncCurrentAutopilotJob = async () => {{
            try {{
              const response = await fetch("/admin/article-autopilot/current", {{
                headers: {{ "Accept": "application/json" }},
                cache: "no-store"
              }});
              const payload = await response.json();
              if (!response.ok || !payload.active || !payload.job_id) return;
              currentJobId = payload.job_id;
              renderJob(payload);
              if (pollingJobId !== payload.job_id) {{
                pollJob(payload.job_id);
              }}
            }} catch (error) {{
              // Keep the page usable; the status poll will retry and the next sync will reconcile the live job.
            }}
          }};

          form.addEventListener("submit", async (event) => {{
            event.preventDefault();
            const submitButton = form.querySelector("button[type='submit']");
            renderStatus("warning", "جارٍ بدء دفعة جمع الفجوات...");
            if (submitButton) submitButton.disabled = true;
            try {{
              const response = await fetch("/admin/article-autopilot/start", {{
                method: "POST",
                headers: {{ "Accept": "application/json" }},
                body: new FormData(form)
              }});
              const payload = await response.json();
              if (!response.ok || !payload.job_id) {{
                renderStatus("error", payload.error || "تعذر بدء دفعة جمع الفجوات.");
                return;
              }}
              currentJobId = payload.job_id;
              await pollJob(payload.job_id);
            }} catch (error) {{
              renderStatus("error", "حدث خطأ أثناء تشغيل دفعة جمع الفجوات.");
            }} finally {{
              if (submitButton) submitButton.disabled = false;
            }}
          }});
          if (developmentForm) {{
            developmentForm.addEventListener("submit", async (event) => {{
              event.preventDefault();
              const submitButton = developmentForm.querySelector("button[type='submit']");
              renderStatus("warning", "جارٍ بدء التطوير المستمر...");
              if (submitButton) submitButton.disabled = true;
              try {{
                const response = await fetch("/admin/article-autopilot/start", {{
                  method: "POST",
                  headers: {{ "Accept": "application/json" }},
                  body: new FormData(developmentForm)
                }});
                const payload = await response.json();
                if (!response.ok || !payload.job_id) {{
                  renderStatus("error", payload.error || "تعذر بدء التطوير المستمر.");
                  return;
                }}
                currentJobId = payload.job_id;
                await pollJob(payload.job_id);
              }} catch (error) {{
                renderStatus("error", "حدث خطأ أثناء تشغيل التطوير المستمر.");
              }} finally {{
                if (submitButton) submitButton.disabled = false;
              }}
            }});
          }}
          if (stopForm) {{
            stopForm.addEventListener("submit", async (event) => {{
              event.preventDefault();
              renderStatus("warning", "جارٍ طلب إيقاف التصحيح الآلي...");
              try {{
                const response = await fetch("/admin/article-autopilot/stop", {{
                  method: "POST",
                  headers: {{ "Accept": "application/json" }}
                }});
                const payload = await response.json();
                if (!response.ok) {{
                  renderStatus("error", payload.error || "تعذر إيقاف التصحيح الآلي.");
                  return;
                }}
                if (payload.job_id) currentJobId = payload.job_id;
                renderStatus("warning", payload.message || "تم طلب الإيقاف.");
                if (currentJobId) await pollJob(currentJobId);
              }} catch (error) {{
                renderStatus("error", "حدث خطأ أثناء إيقاف التصحيح الآلي.");
              }}
            }});
          }}
          if (improveForm) {{
            improveForm.addEventListener("submit", async (event) => {{
              event.preventDefault();
              const submitButton = improveForm.querySelector("button[type='submit']");
              renderStatus("warning", "جارٍ بدء تحسين RAG من الفجوات المحفوظة...");
              if (submitButton) submitButton.disabled = true;
              try {{
                const response = await fetch("/admin/article-autopilot/improve", {{
                  method: "POST",
                  headers: {{ "Accept": "application/json" }},
                  body: new FormData(improveForm)
                }});
                const payload = await response.json();
                if (!response.ok || !payload.job_id) {{
                  renderStatus("error", payload.error || "تعذر بدء تحسين RAG.");
                  return;
                }}
                currentJobId = payload.job_id;
                await pollJob(payload.job_id);
              }} catch (error) {{
                renderStatus("error", "حدث خطأ أثناء تحسين RAG.");
              }} finally {{
                if (submitButton) submitButton.disabled = false;
              }}
            }});
          }}
          syncCurrentAutopilotJob();
          setInterval(syncCurrentAutopilotJob, 15000);
        }})();
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html_body)


def _render_login_page(request: Request) -> HTMLResponse:
    notice = request.query_params.get("notice", "")
    error = request.query_params.get("error", "")
    html_body = f"""
    <!doctype html>
    <html lang="ar" dir="rtl">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>دخول لوحة التحكم</title>
      <style>
        body {{
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", sans-serif;
          background: linear-gradient(160deg, #0d5c63 0%, #1f2a24 100%);
          color: #1f2a24;
        }}
        .card {{
          width: min(480px, calc(100vw - 24px));
          background: rgba(255,253,248,.96);
          border-radius: 24px;
          padding: 28px;
          box-shadow: 0 24px 80px rgba(0,0,0,.18);
        }}
        h1 {{ margin-top: 0; }}
        label {{ display: grid; gap: 8px; font-weight: 700; }}
        input, button {{
          font: inherit;
          width: 100%;
          border-radius: 14px;
          padding: 12px 14px;
        }}
        input {{ border: 1px solid #d9d2c3; }}
        button {{
          border: 0;
          background: #0d5c63;
          color: #fff;
          font-weight: 700;
          margin-top: 14px;
        }}
        .banner {{
          margin-bottom: 14px;
          padding: 12px 14px;
          border-radius: 14px;
          font-weight: 600;
        }}
        .success {{ background: rgba(22,101,52,.10); color: #166534; }}
        .error {{ background: rgba(180,35,24,.10); color: #b42318; }}
      </style>
    </head>
    <body>
      <div class="card">
        <h1>دخول لوحة التحكم</h1>
        <p>أدخل كلمة مرور المشرف لفتح إعدادات النماذج ومختبر المقارنة.</p>
        {f"<div class='banner success'>{_escape(notice)}</div>" if notice else ""}
        {f"<div class='banner error'>{_escape(error)}</div>" if error else ""}
        <form method="post" action="/admin/login">
          <label>كلمة المرور
            <input type="password" name="password" autofocus>
          </label>
          <button type="submit">دخول</button>
        </form>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(html_body)


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not settings.admin_panel_enabled:
        return HTMLResponse("لوحة التحكم معطلة في الإعدادات الحالية.", status_code=404)
    if not _is_authenticated(request):
        return _render_login_page(request)
    return _render_dashboard(request)


@router.post("/admin/login")
async def admin_login(request: Request):
    if not settings.admin_panel_enabled:
        return HTMLResponse("لوحة التحكم معطلة في الإعدادات الحالية.", status_code=404)

    if not settings.admin_panel_password:
        return _redirect_with_message("/admin", notice="لا توجد كلمة مرور مضبوطة حاليًا.")

    form = await request.form()
    password = str(form.get("password", ""))
    if not secrets.compare_digest(password, settings.admin_panel_password):
        return _redirect_with_message("/admin", error="كلمة المرور غير صحيحة.")

    response = _redirect_with_message("/admin", notice="تم فتح لوحة التحكم بنجاح.")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        _build_session_token(),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/admin/logout")
async def admin_logout(request: Request):
    response = _redirect_with_message("/admin", notice="تم تسجيل الخروج من لوحة التحكم.")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.post("/admin/runtime-settings")
async def save_runtime_settings(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")

    form = await request.form()
    form_data = {key: str(value) for key, value in form.items()}
    update_result = get_runtime_settings_store().update_from_form(form_data)
    if update_result.openai_api_key_changed:
        await get_engine().refresh_runtime_configuration(embeddings_changed=True)

    if update_result.changed_fields:
        return _redirect_with_message("/admin", notice="تم حفظ الإعدادات الجديدة.")
    return _redirect_with_message("/admin", notice="لم تُكتشف تغييرات جديدة للحفظ.")


@router.post("/admin/actions/local-sync")
async def force_local_sync(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")
    try:
        await get_engine().sync_if_documents_changed(force=True)
        return _redirect_with_message("/admin", notice="تمت إعادة فهرسة RAG المحلي بنجاح.")
    except Exception as exc:
        return _redirect_with_message("/admin", error=f"فشلت إعادة الفهرسة المحلية: {exc}")


@router.post("/admin/actions/file-search-sync")
async def force_file_search_sync(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")
    try:
        result = await get_gemini_file_search_service().sync(force=True)
        return _redirect_with_message(
            "/admin",
            notice=f"تمت مزامنة Gemini File Search بنجاح ({result['file_count']} ملف).",
        )
    except Exception as exc:
        return _redirect_with_message("/admin", error=f"فشلت مزامنة Gemini File Search: {exc}")


@router.post("/admin/actions/official-sync")
async def force_official_sync(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")
    try:
        result = await get_official_sync_service().sync(force=True)
        return _redirect_with_message(
            "/admin",
            notice=(
                "تمت مزامنة الأنظمة الرسمية: "
                f"checked={result.checked_entries}, changed={result.changed_entries}, failed={result.failed_entries}"
            ),
        )
    except Exception as exc:
        return _redirect_with_message("/admin", error=f"فشلت المزامنة الرسمية: {exc}")


@router.post("/admin/actions/restart-service")
async def restart_service(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")

    _schedule_restart()
    return _redirect_with_message(
        "/admin",
        notice="جارٍ إعادة تشغيل الخدمة... حدّث الصفحة بعد ثوانٍ قليلة.",
    )


@router.post("/admin/article-audit/start")
async def start_article_audit_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    active_job = _get_active_article_audit_job()
    if active_job:
        return JSONResponse(
            {
                "job_id": active_job["job_id"],
                "status": active_job.get("status", "running"),
                "message": active_job.get("message", "تدقيق دقة الجمع يعمل بالفعل."),
            }
        )

    job_id = _create_article_audit_job()
    asyncio.create_task(_run_article_audit_job(job_id))
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "message": "بدأ تدقيق دقة الجمع في الخلفية.",
        }
    )


@router.get("/admin/article-audit/status/{job_id}")
async def article_audit_job_status(request: Request, job_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    job = _get_article_audit_job(job_id)
    if not job:
        return JSONResponse({"error": "تعذر العثور على مهمة تدقيق دقة الجمع المطلوبة."}, status_code=404)
    return JSONResponse(job)


@router.post("/admin/article-autopilot/start")
async def start_article_autopilot_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    try:
        form = await request.form()
    except Exception:
        form = {}
    form_get = getattr(form, "get", lambda _key, _default=None: _default)
    development_mode = str(form_get("development_mode", "")).strip() in {"1", "true", "on", "yes"}
    config = _normalize_article_autopilot_config({
        "candidate_count": _bounded_int(
            form_get("candidate_count", ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT),
            ARTICLE_AUTOPILOT_CANDIDATE_COUNT_DEFAULT,
            1,
            8,
        ),
        "max_articles_per_case": _bounded_int(
            form_get("max_articles_per_case", ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT),
            ARTICLE_AUTOPILOT_MAX_ARTICLES_PER_CASE_DEFAULT,
            1,
            6,
        ),
        "interval_seconds": _bounded_int(
            form_get("interval_seconds", ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT),
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_DEFAULT,
            ARTICLE_AUTOPILOT_INTERVAL_SECONDS_MIN,
            3600,
        ),
        "batch_round_limit": _bounded_int(
            form_get("batch_round_limit", ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT),
            ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            1,
            500,
        ),
        "continuous": True,
        "development_mode": development_mode,
        "allow_deferred_failures": development_mode,
        "deferred_min_validation_pass_rate": 0.90,
        "fast_fixed_holdout_limit": _bounded_int(
            form_get("fast_fixed_holdout_limit", ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT),
            ARTICLE_AUTOPILOT_FAST_FIXED_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        ),
        "fast_moving_holdout_limit": _bounded_int(
            form_get("fast_moving_holdout_limit", ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT),
            ARTICLE_AUTOPILOT_FAST_MOVING_HOLDOUT_LIMIT_DEFAULT,
            0,
            200,
        ),
        "full_holdout_every_batches": _bounded_int(
            form_get("full_holdout_every_batches", ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT),
            ARTICLE_AUTOPILOT_FULL_HOLDOUT_EVERY_BATCHES_DEFAULT,
            0,
            100,
        ),
    })
    if _article_autopilot_low_disk_space():
        _maybe_cleanup_article_autopilot_artifacts("start_low_disk", force=True)
    if _article_autopilot_low_disk_space():
        free_gb = round(_article_autopilot_free_bytes() / (1024**3), 2)
        _set_article_autopilot_run_state(
            enabled=False,
            config=config,
            reason="paused_low_disk_space",
            status="paused",
        )
        return JSONResponse(
            {
                "error": f"تم إيقاف التطوير التلقائي مؤقتًا لأن المساحة الحرة منخفضة جدًا ({free_gb}GB). نظّف نسخ التحسين الاحتياطية أولًا.",
                "status": "paused_low_disk_space",
            },
            status_code=507,
        )

    active_job = _get_active_article_autopilot_job()
    if active_job:
        return JSONResponse(
            {
                "job_id": active_job["job_id"],
                "status": active_job.get("status", "running"),
                "message": active_job.get("message", "جولة التحسين الآلية تعمل بالفعل."),
            }
        )

    job_id = _create_article_autopilot_job(config)
    _set_article_autopilot_run_state(
        enabled=True,
        config=config,
        job_id=job_id,
        reason="started_from_dashboard",
        status="queued",
    )
    asyncio.create_task(_run_article_autopilot_job(job_id))
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "message": (
                "بدأ التطوير المستمر السريع: دفعات أصغر بنداءات أقل، تحقق سريع، و full holdout دوري."
                if development_mode
                else "بدأ التصحيح الآلي المستمر في الخلفية."
            ),
            "config": config,
        }
    )


@router.post("/admin/article-autopilot/improve")
async def improve_article_autopilot_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    try:
        form = await request.form()
    except Exception:
        form = {}
    config = {
        "mode": "improvement",
        "batch_round_limit": _bounded_int(
            getattr(form, "get", lambda _key, _default=None: _default)("batch_round_limit", ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT),
            ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
            1,
            500,
        ),
    }
    active_job = _get_active_article_autopilot_job()
    if active_job:
        return JSONResponse(
            {
                "job_id": active_job["job_id"],
                "status": active_job.get("status", "running"),
                "message": active_job.get("message", "مهمة التحسين أو الجمع تعمل بالفعل."),
            }
        )

    job_id = _create_article_autopilot_job(config)
    _update_article_autopilot_job(
        job_id,
        steps=["deep_diagnose", "build_general_support", "retest_batch", "manual_slice", "accept_or_rollback"],
        message="بدأ تحسين RAG من الفجوات المحفوظة.",
    )
    asyncio.create_task(_run_article_autopilot_improvement_job(job_id))
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "message": "بدأ تحسين RAG في الخلفية. سيُعاد اختبار نفس دفعة الجولات قبل قبول التحسين.",
            "config": config,
        }
    )


@router.post("/admin/article-autopilot/improvement-action")
async def article_autopilot_improvement_action(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")
    form = await request.form()
    action = str(form.get("action") or "").strip()
    manifest_raw = str(form.get("manifest_path") or "").strip()
    manifest_path = Path(manifest_raw)
    if not manifest_path.is_absolute():
        manifest_path = PROJECT_ROOT / manifest_path
    if not manifest_path.exists() or manifest_path.parent != (PROJECT_ROOT / "data" / "eval" / "article_autopilot"):
        return _redirect_with_message("/admin", error="تعذر العثور على سجل التحسين المطلوب.")
    manifest = _safe_read_json(manifest_path)
    if not manifest:
        return _redirect_with_message("/admin", error="سجل التحسين غير صالح.")

    if action == "retry":
        active_job = _get_active_article_autopilot_job()
        if active_job:
            return _redirect_with_message("/admin", error="توجد مهمة تعمل بالفعل.")
        config = {
            "mode": "improvement",
            "batch_round_limit": _bounded_int(
                manifest.get("batch_rounds"),
                ARTICLE_AUTOPILOT_BATCH_ROUND_LIMIT_DEFAULT,
                1,
                500,
            ),
            "retry_attempts": 2,
        }
        job_id = _create_article_autopilot_job(config)
        asyncio.create_task(_run_article_autopilot_improvement_job(job_id))
        return _redirect_with_message("/admin", notice="بدأت إعادة التحسين والبحث عن سبب آخر.")

    if action == "carry":
        validation = manifest.get("validation_summary") or {}
        manual = manifest.get("manual_summary") or {}
        fixed_guard = manifest.get("fixed_holdout_guard") or {}
        if int(validation.get("transport_error_cases", 0) or 0):
            return _redirect_with_message("/admin", error="لا يمكن اعتماد تحسين فيه أخطاء تشغيلية.")
        if float(validation.get("pass_rate", 0.0) or 0.0) < 0.90:
            return _redirect_with_message("/admin", error="لا يعتمد الترحيل إلا إذا وصل تحقق الدفعة إلى 90% أو أكثر.")
        if int(manual.get("failed_cases", 0) or 0) or int(manual.get("transport_error_cases", 0) or 0):
            return _redirect_with_message("/admin", error="لا يمكن الاعتماد ما لم تعبر الشريحة اليدوية.")
        if not bool(fixed_guard.get("accepted")):
            return _redirect_with_message("/admin", error="لا يمكن الاعتماد ما لم يعبر التحسين هولداوت عدم التراجع الثابت.")
        try:
            _install_improvement_artifacts_from_manifest(manifest)
            deferred_count = _append_deferred_improvement_cases(
                manifest,
                manifest_path,
                "manual_accept_and_carry_failures",
            )
            manifest["decision"] = "ACCEPTED_WITH_DEFERRED_FAILURES"
            manifest["manual_action"] = {
                "action": "accept_and_carry_failures",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "deferred_failure_count": deferred_count,
                "deferred_backlog_path": str(ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH),
            }
            manifest["deferred_failure_count"] = deferred_count
            manifest["deferred_backlog_path"] = str(ARTICLE_AUTOPILOT_DEFERRED_BACKLOG_PATH)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return _redirect_with_message("/admin", notice=f"تم اعتماد التحسين وترحيل {deferred_count} إخفاقات.")
        except Exception as exc:
            logger.exception("تعذر اعتماد التحسين مع ترحيل الإخفاقات: %s", exc)
            return _redirect_with_message("/admin", error="تعذر اعتماد التحسين وترحيل الإخفاقات.")

    return _redirect_with_message("/admin", error="إجراء غير معروف.")


@router.post("/admin/article-autopilot/stop")
async def stop_article_autopilot_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    active_job = _get_active_article_autopilot_job()
    if not active_job:
        return JSONResponse(
            {
                "status": "idle",
                "message": "لا يوجد تصحيح آلي يعمل الآن.",
            }
        )
    stopped = _request_stop_article_autopilot_job(active_job["job_id"])
    if not stopped:
        return JSONResponse({"error": "تعذر إرسال أمر الإيقاف."}, status_code=404)
    return JSONResponse(
        {
            "job_id": active_job["job_id"],
            "status": "stopping",
            "message": "تم طلب إيقاف التصحيح الآلي؛ سيتوقف بعد انتهاء المرحلة الحالية.",
        }
    )


@router.get("/admin/article-autopilot/current")
async def current_article_autopilot_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    active_job = _get_active_article_autopilot_job()
    if not active_job:
        return JSONResponse(
            {
                "active": False,
                "status": "idle",
                "message": "لا يوجد تصحيح آلي يعمل الآن.",
            }
        )
    active_job["active"] = True
    return JSONResponse(_compact_article_autopilot_job_payload(active_job))


@router.get("/admin/article-autopilot/status/{job_id}")
async def article_autopilot_job_status(request: Request, job_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    job = _get_article_autopilot_job(job_id)
    if not job:
        return JSONResponse({"error": "تعذر العثور على جولة التحسين الآلية المطلوبة."}, status_code=404)
    return JSONResponse(_compact_article_autopilot_job_payload(job))


@router.post("/admin/compare", response_class=HTMLResponse)
async def compare_paths(request: Request):
    if not _is_authenticated(request):
        return _redirect_with_message("/admin", error="يلزم تسجيل الدخول أولًا.")

    form = await request.form()
    question = str(form.get("question", "")).strip()
    answer_mode = _normalize_answer_mode(form.get("answer_mode"))
    selected_providers = [
        provider
        for provider in COMPARE_PROVIDER_ORDER
        if str(form.get(f"compare_{provider}", "")).strip() == provider
    ]
    default_compare_providers = _get_compare_provider_ids(get_runtime_settings_store().get_panel_state())
    if not question:
        return _render_dashboard(
            request,
            compare_payload={
                "question": "",
                "answer_mode": answer_mode,
                "answer_mode_label": ANSWER_MODE_LABELS[answer_mode],
                "selected_providers": selected_providers or default_compare_providers,
                "results": [],
                "error": "يرجى كتابة سؤال أولاً.",
            },
        )
    if not selected_providers:
        return _render_dashboard(
            request,
            compare_payload={
                "question": question,
                "answer_mode": answer_mode,
                "answer_mode_label": ANSWER_MODE_LABELS[answer_mode],
                "selected_providers": [],
                "results": [],
                "error": "اختر مزودًا واحدًا على الأقل للمقارنة.",
            },
        )

    engine = get_engine()
    provider_results = await engine.compare_generation_providers(
        question,
        selected_providers,
        answer_mode=answer_mode,
    )
    compare_payload = {
        "question": question,
        "answer_mode": answer_mode,
        "answer_mode_label": ANSWER_MODE_LABELS[answer_mode],
        "selected_providers": selected_providers,
        "results": [
            {
                "title": COMPARE_PROVIDER_TITLES.get(
                    provider,
                    PROVIDER_METADATA.get(provider, {}).get("label", provider),
                ),
                "provider_label": provider_results[provider]["runtime"]["label"],
                "model": provider_results[provider]["runtime"].get("model", ""),
                "answer": provider_results[provider]["result"].answer,
                "confidence": provider_results[provider]["result"].confidence,
                "sources": provider_results[provider]["result"].sources,
                "error": "",
            }
            for provider in selected_providers
        ],
    }
    return _render_dashboard(request, compare_payload=compare_payload)


@router.post("/admin/compare/start")
async def start_compare_job(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    form = await request.form()
    question = str(form.get("question", "")).strip()
    answer_mode = _normalize_answer_mode(form.get("answer_mode"))
    selected_providers = [
        provider
        for provider in COMPARE_PROVIDER_ORDER
        if str(form.get(f"compare_{provider}", "")).strip() == provider
    ]
    if not question:
        return JSONResponse({"error": "يرجى كتابة سؤال أولاً."}, status_code=400)
    if not selected_providers:
        return JSONResponse({"error": "اختر مزودًا واحدًا على الأقل للمقارنة."}, status_code=400)

    job_id = _create_compare_job(question, selected_providers, answer_mode)
    asyncio.create_task(_run_compare_job(job_id, question, selected_providers, answer_mode))
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "queued",
            "question": question,
            "answer_mode": answer_mode,
            "answer_mode_label": ANSWER_MODE_LABELS[answer_mode],
            "selected_providers": selected_providers,
        }
    )


@router.get("/admin/compare/status/{job_id}")
async def compare_job_status(request: Request, job_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "يلزم تسجيل الدخول أولًا."}, status_code=401)

    job = _get_compare_job(job_id)
    if not job:
        return JSONResponse({"error": "تعذر العثور على مهمة المقارنة المطلوبة."}, status_code=404)
    return JSONResponse(job)
