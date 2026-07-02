"""خدمة خفيفة لاكتشاف نماذج Ollama المحلية المعروضة من نفس الخادم."""

from __future__ import annotations

import json
import logging
from urllib import error, request

logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = 1.5


def _normalize_base_url(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/")


def build_ollama_base_url_candidates(base_url: str | None) -> list[str]:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(candidate: str):
        compact = _normalize_base_url(candidate)
        if compact and compact not in seen:
            seen.add(compact)
            candidates.append(compact)

    if "host.docker.internal" in normalized:
        add_candidate(normalized.replace("host.docker.internal", "127.0.0.1"))
        add_candidate(normalized.replace("host.docker.internal", "localhost"))
        add_candidate(normalized)
    elif "127.0.0.1" in normalized:
        add_candidate(normalized)
        add_candidate(normalized.replace("127.0.0.1", "localhost"))
        add_candidate(normalized.replace("127.0.0.1", "host.docker.internal"))
    elif "localhost" in normalized:
        add_candidate(normalized)
        add_candidate(normalized.replace("localhost", "127.0.0.1"))
        add_candidate(normalized.replace("localhost", "host.docker.internal"))
    else:
        add_candidate(normalized)

    return candidates


def _compact_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text


def _extract_model_payload(model: dict) -> dict:
    details = model.get("details") or {}
    return {
        "name": model.get("name") or model.get("model") or "",
        "family": details.get("family") or "",
        "parameter_size": details.get("parameter_size") or "",
        "quantization_level": details.get("quantization_level") or "",
        "modified_at": model.get("modified_at") or "",
        "size_bytes": int(model.get("size") or 0),
        "remote_host": model.get("remote_host") or "",
        "is_cloud": bool(model.get("remote_host")),
    }


def resolve_preferred_ollama_model(selected_model: str | None, local_model_names: list[str]) -> str:
    normalized_selected = (selected_model or "").strip()
    if not local_model_names:
        return normalized_selected
    if normalized_selected in local_model_names:
        return normalized_selected

    selected_prefix = normalized_selected.split(":", 1)[0].strip().lower()
    if selected_prefix:
        for model_name in local_model_names:
            if model_name.split(":", 1)[0].strip().lower() == selected_prefix:
                return model_name

    return local_model_names[0]


def get_ollama_catalog(base_url: str | None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    normalized_base_url = _normalize_base_url(base_url)
    if not normalized_base_url:
        return {
            "ok": False,
            "base_url": "",
            "resolved_base_url": "",
            "message": "عنوان Ollama غير مضبوط.",
            "local_models": [],
            "cloud_models": [],
            "local_model_names": [],
        }

    payload = {}
    resolved_base_url = normalized_base_url
    last_error_message = "تعذر الاتصال بـ Ollama."
    for candidate in build_ollama_base_url_candidates(normalized_base_url):
        endpoint = f"{candidate}/api/tags"
        try:
            with request.urlopen(endpoint, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            resolved_base_url = candidate
            break
        except error.URLError as exc:
            last_error_message = f"تعذر الاتصال بـ Ollama: {_compact_error_message(exc)}"
            logger.info("تعذر الوصول إلى Ollama على %s: %s", endpoint, exc)
        except Exception as exc:
            last_error_message = f"فشل قراءة قائمة النماذج: {_compact_error_message(exc)}"
            logger.warning("فشل قراءة قائمة نماذج Ollama من %s: %s", endpoint, exc)
    else:
        return {
            "ok": False,
            "base_url": normalized_base_url,
            "resolved_base_url": "",
            "message": last_error_message,
            "local_models": [],
            "cloud_models": [],
            "local_model_names": [],
        }

    all_models = [_extract_model_payload(item) for item in payload.get("models", [])]
    local_models = sorted(
        [item for item in all_models if item["name"] and not item["is_cloud"]],
        key=lambda item: (item["size_bytes"], item["name"]),
        reverse=True,
    )
    cloud_models = sorted(
        [item for item in all_models if item["name"] and item["is_cloud"]],
        key=lambda item: item["name"],
    )
    local_model_names = [item["name"] for item in local_models]

    return {
        "ok": True,
        "base_url": normalized_base_url,
        "resolved_base_url": resolved_base_url,
        "message": (
            f"تم العثور على {len(local_models)} نموذج محلي جاهز"
            + (f" عبر {resolved_base_url}" if resolved_base_url != normalized_base_url else ".")
        ),
        "local_models": local_models,
        "cloud_models": cloud_models,
        "local_model_names": local_model_names,
    }
