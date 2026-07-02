"""جلب قوائم النماذج المتاحة من المزودات السحابية مع كاش خفيف."""

from __future__ import annotations

import json
import logging
import threading
import time
from urllib import error, parse, request

logger = logging.getLogger(__name__)
_CACHE_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = 600
_CATALOG_CACHE: dict[str, dict] = {}


def _normalize_base_url(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/")


def _compact_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text


def _get_cached(key: str) -> dict | None:
    with _CACHE_LOCK:
        cached = _CATALOG_CACHE.get(key)
        if not cached:
            return None
        if time.time() - cached["stored_at"] > _CACHE_TTL_SECONDS:
            _CATALOG_CACHE.pop(key, None)
            return None
        return cached["payload"]


def _set_cached(key: str, payload: dict):
    with _CACHE_LOCK:
        _CATALOG_CACHE[key] = {
            "stored_at": time.time(),
            "payload": payload,
        }


def _fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 4.0) -> dict:
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_openrouter_catalog(api_key: str | None = None, timeout: float = 4.0) -> dict:
    cache_key = "openrouter"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        payload = _fetch_json("https://openrouter.ai/api/v1/models", headers=headers, timeout=timeout)
    except error.URLError as exc:
        logger.info("تعذر جلب قائمة نماذج OpenRouter: %s", exc)
        return {
            "ok": False,
            "message": f"تعذر الاتصال بـ OpenRouter: {_compact_error_message(exc)}",
            "models": [],
            "model_ids": [],
        }
    except Exception as exc:
        logger.warning("فشل قراءة قائمة نماذج OpenRouter: %s", exc)
        return {
            "ok": False,
            "message": f"فشل قراءة قائمة نماذج OpenRouter: {_compact_error_message(exc)}",
            "models": [],
            "model_ids": [],
        }

    models = []
    for item in payload.get("data", []):
        model_id = (item.get("id") or "").strip()
        if not model_id:
            continue
        architecture = item.get("architecture") or {}
        modalities = architecture.get("output_modalities") or []
        if modalities and "text" not in modalities:
            continue
        models.append(
            {
                "id": model_id,
                "label": item.get("name") or model_id,
                "description": (item.get("description") or "").strip(),
                "context_length": item.get("context_length") or "",
            }
        )

    models.sort(key=lambda item: item["id"])
    result = {
        "ok": True,
        "message": f"تم جلب {len(models)} نموذجاً من OpenRouter.",
        "models": models,
        "model_ids": [item["id"] for item in models],
    }
    _set_cached(cache_key, result)
    return result


def get_gemini_catalog(api_key: str | None, base_url: str | None, timeout: float = 4.0) -> dict:
    normalized_base_url = _normalize_base_url(base_url)
    if not api_key:
        return {
            "ok": False,
            "message": "ضع Gemini API Key لعرض قائمة النماذج المتاحة.",
            "models": [],
            "model_ids": [],
        }

    cache_key = f"gemini::{normalized_base_url}::{hash(api_key)}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    next_page_token = ""
    collected_models = []

    try:
        for _ in range(5):
            query_params = {"key": api_key, "pageSize": 1000}
            if next_page_token:
                query_params["pageToken"] = next_page_token
            url = f"{normalized_base_url}/models?{parse.urlencode(query_params)}"
            payload = _fetch_json(url, timeout=timeout)
            for item in payload.get("models", []):
                model_name = (item.get("name") or "").strip()
                model_id = model_name.split("/", 1)[1] if "/" in model_name else model_name
                if not model_id:
                    continue
                if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                    continue
                collected_models.append(
                    {
                        "id": model_id,
                        "label": item.get("displayName") or model_id,
                        "description": (item.get("description") or "").strip(),
                        "input_token_limit": item.get("inputTokenLimit") or "",
                    }
                )

            next_page_token = (payload.get("nextPageToken") or "").strip()
            if not next_page_token:
                break
    except error.URLError as exc:
        logger.info("تعذر جلب قائمة نماذج Gemini: %s", exc)
        return {
            "ok": False,
            "message": f"تعذر الاتصال بـ Gemini: {_compact_error_message(exc)}",
            "models": [],
            "model_ids": [],
        }
    except Exception as exc:
        logger.warning("فشل قراءة قائمة نماذج Gemini: %s", exc)
        return {
            "ok": False,
            "message": f"فشل قراءة قائمة نماذج Gemini: {_compact_error_message(exc)}",
            "models": [],
            "model_ids": [],
        }

    collected_models.sort(key=lambda item: item["id"])
    result = {
        "ok": True,
        "message": f"تم جلب {len(collected_models)} نموذجاً من Gemini.",
        "models": collected_models,
        "model_ids": [item["id"] for item in collected_models],
    }
    _set_cached(cache_key, result)
    return result
