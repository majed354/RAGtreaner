"""إعدادات التشغيل القابلة للتغيير من لوحة التحكم."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.mlx_local_service import build_mlx_local_healthcheck
from app.ollama_service import get_ollama_catalog, resolve_preferred_ollama_model
from app.provider_catalog_service import get_gemini_catalog, get_openrouter_catalog

settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_PROVIDERS = ("openrouter", "gemini", "ollama", "mlx_local")
PROVIDER_METADATA = {
    "openrouter": {
        "label": "OpenRouter",
        "description": "مفيد للنماذج السحابية عبر مزود واحد مثل Kimi وغيره.",
        "token_field": "api_key",
        "token_label": "OpenRouter API Key",
        "model_suggestions": [
            "moonshotai/kimi-k2",
            "google/gemini-2.5-pro",
            "openai/gpt-4o-mini",
        ],
    },
    "gemini": {
        "label": "Google Gemini",
        "description": "ربط مباشر مع نماذج جيميناي من Google AI Studio.",
        "token_field": "api_key",
        "token_label": "Gemini API Key",
        "model_suggestions": [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ],
    },
    "ollama": {
        "label": "Ollama",
        "description": "تشغيل محلي أو على خادمك الخاص دون خدمة سحابية.",
        "token_field": "",
        "token_label": "",
        "model_suggestions": [
            "qwen2.5:7b-instruct",
            "llama3.1:8b",
            "mistral:7b-instruct",
        ],
    },
    "mlx_local": {
        "label": "MLX Local",
        "description": "Gemma المحلية عبر MLX مع routing بين adapters بحسب المسار القانوني.",
        "token_field": "",
        "token_label": "",
        "model_suggestions": [
            settings.mlx_local_model_path,
        ],
    },
}


def _resolve_runtime_settings_path() -> Path:
    configured = Path(settings.runtime_settings_path)
    if configured.is_absolute():
        return configured
    return (PROJECT_ROOT / configured).resolve()


def _mask_secret(value: str) -> str:
    if not value:
        return "غير مضبوط"
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * max(len(value) - 8, 4)}{value[-4:]}"


def _merge_unique_strings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            normalized = (item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


@dataclass
class RuntimeUpdateResult:
    openai_api_key_changed: bool
    changed_fields: list[str]


class RuntimeSettingsStore:
    """حفظ وقراءة إعدادات التشغيل المحلية."""

    def __init__(self):
        self._path = _resolve_runtime_settings_path()
        self._lock = threading.RLock()

    def _default_state(self) -> dict:
        provider = self._normalize_provider(settings.generation_provider_default)
        return {
            "active_provider": provider,
            "temperature": settings.generation_temperature,
            "max_tokens": settings.generation_max_tokens,
            "providers": {
                "openrouter": {
                    "model": settings.openrouter_model,
                    "api_key": settings.openrouter_api_key,
                },
                "gemini": {
                    "model": settings.gemini_model,
                    "api_key": settings.gemini_api_key,
                    "api_base_url": settings.gemini_api_base_url,
                },
                "ollama": {
                    "model": settings.ollama_model,
                    "base_url": settings.ollama_base_url,
                },
                "mlx_local": {
                    "model": settings.mlx_local_model_path,
                    "python_bin": settings.mlx_local_python_bin,
                    "default_adapter_path": settings.mlx_local_default_adapter_path,
                    "routing_policy_path": settings.mlx_local_routing_policy_path,
                    "budget_policy_path": settings.mlx_local_budget_policy_path,
                    "prompt_templates_dir": settings.mlx_local_prompt_templates_dir,
                },
            },
            "embeddings": {
                "api_key": settings.openai_api_key,
            },
            "updated_at": None,
        }

    def _normalize_provider(self, provider: Optional[str]) -> str:
        candidate = (provider or "").strip().lower()
        if candidate in SUPPORTED_PROVIDERS:
            return candidate
        return "openrouter"

    def _read_raw(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _merge(self, base: dict, override: dict) -> dict:
        merged = deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def get_state(self) -> dict:
        with self._lock:
            state = self._merge(self._default_state(), self._read_raw())
        state["active_provider"] = self._normalize_provider(state.get("active_provider"))
        return state

    def save_state(self, state: dict):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = deepcopy(state)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self._path.chmod(0o600)
        except Exception:
            pass

    def get_active_generation(self, state: Optional[dict] = None) -> dict:
        current = deepcopy(state or self.get_state())
        provider = self._normalize_provider(current.get("active_provider"))
        return self.get_generation_for_provider(provider, state=current)

    def _refresh_ollama_provider_state(self, state: dict, persist: bool = False) -> dict:
        provider_state = state["providers"]["ollama"]
        configured_base_url = provider_state.get("base_url") or settings.ollama_base_url
        configured_model = (provider_state.get("model") or "").strip()

        ollama_catalog = get_ollama_catalog(configured_base_url)
        resolved_base_url = (ollama_catalog.get("resolved_base_url") or "").strip()
        resolved_model = resolve_preferred_ollama_model(
            configured_model,
            ollama_catalog["local_model_names"],
        )

        changed = False
        if resolved_base_url and resolved_base_url != provider_state.get("base_url"):
            provider_state["base_url"] = resolved_base_url
            changed = True

        if resolved_model and resolved_model != configured_model:
            provider_state["model"] = resolved_model
            changed = True

        if persist and changed:
            self.save_state(state)

        return ollama_catalog

    def get_generation_for_provider(self, provider: str, state: Optional[dict] = None) -> dict:
        persist_runtime_fix = state is None
        current = deepcopy(state or self.get_state())
        provider = self._normalize_provider(provider)
        provider_state = deepcopy(current["providers"].get(provider, {}))
        if provider == "ollama":
            ollama_catalog = self._refresh_ollama_provider_state(
                current,
                persist=persist_runtime_fix,
            )
            provider_state = deepcopy(current["providers"]["ollama"])
        provider_state["provider"] = provider
        provider_state["label"] = PROVIDER_METADATA[provider]["label"]
        provider_state["temperature"] = float(current.get("temperature", settings.generation_temperature))
        provider_state["max_tokens"] = int(current.get("max_tokens", settings.generation_max_tokens))
        return provider_state

    def get_panel_state(self, *, refresh_catalogs: bool = True) -> dict:
        state = self.get_state()
        providers = []
        for provider_id in SUPPORTED_PROVIDERS:
            provider_state = deepcopy(state["providers"].get(provider_id, {}))
            metadata = PROVIDER_METADATA[provider_id]
            model_suggestions = list(metadata["model_suggestions"])
            available_models: list[dict] = []
            cloud_models: list[dict] = []
            connection_ok = None
            connection_message = ""
            selected_model_available = None
            if provider_id == "ollama" and refresh_catalogs:
                configured = bool(provider_state.get("base_url"))
                ollama_catalog = self._refresh_ollama_provider_state(state, persist=True)
                provider_state = deepcopy(state["providers"]["ollama"])
                connection_target = provider_state.get("base_url", "")
                available_models = ollama_catalog["local_models"]
                cloud_models = ollama_catalog["cloud_models"]
                connection_ok = ollama_catalog["ok"]
                connection_message = ollama_catalog["message"]
                model_suggestions = _merge_unique_strings(
                    [item["name"] for item in available_models],
                    model_suggestions,
                )
                current_model = (provider_state.get("model") or "").strip()
                resolved_model = resolve_preferred_ollama_model(
                    current_model,
                    ollama_catalog["local_model_names"],
                )
                if current_model:
                    selected_model_available = current_model in set(ollama_catalog["local_model_names"])
                catalog_message = ollama_catalog["message"]
            elif provider_id == "openrouter" and refresh_catalogs:
                configured = bool(provider_state.get("api_key"))
                connection_target = ""
                openrouter_catalog = get_openrouter_catalog(provider_state.get("api_key", ""))
                available_models = openrouter_catalog["models"]
                connection_ok = openrouter_catalog["ok"]
                connection_message = openrouter_catalog["message"]
                model_suggestions = _merge_unique_strings(
                    [item["id"] for item in available_models],
                    model_suggestions,
                )
                current_model = (provider_state.get("model") or "").strip()
                resolved_model = current_model
                if current_model:
                    selected_model_available = current_model in set(openrouter_catalog["model_ids"])
                catalog_message = openrouter_catalog["message"]
            elif provider_id == "gemini" and refresh_catalogs:
                configured = bool(provider_state.get("api_key"))
                connection_target = provider_state.get("api_base_url", "")
                gemini_catalog = get_gemini_catalog(
                    provider_state.get("api_key", ""),
                    connection_target or settings.gemini_api_base_url,
                )
                available_models = gemini_catalog["models"]
                connection_ok = gemini_catalog["ok"]
                connection_message = gemini_catalog["message"]
                model_suggestions = _merge_unique_strings(
                    [item["id"] for item in available_models],
                    model_suggestions,
                )
                current_model = (provider_state.get("model") or "").strip()
                resolved_model = current_model
                if current_model:
                    selected_model_available = current_model in set(gemini_catalog["model_ids"])
                catalog_message = gemini_catalog["message"]
            elif provider_id == "mlx_local":
                health = build_mlx_local_healthcheck(provider_state)
                configured = health["configured"]
                connection_target = health["connection_target"]
                connection_ok = health["connection_ok"]
                connection_message = health["connection_message"]
                model_suggestions = _merge_unique_strings(
                    health["model_suggestions"],
                    model_suggestions,
                )
                current_model = (provider_state.get("model") or "").strip()
                resolved_model = health["resolved_model"]
                selected_model_available = health["selected_model_available"]
                catalog_message = health["catalog_message"]
            else:
                configured = (
                    bool(provider_state.get("base_url"))
                    if provider_id == "ollama"
                    else bool(provider_state.get("api_key"))
                )
                connection_target = (
                    provider_state.get("base_url", "")
                    if provider_id == "ollama"
                    else provider_state.get("api_base_url", "")
                    if provider_id == "gemini"
                    else ""
                )
                catalog_message = "تُحدّث قائمة النماذج عند تشغيل عملية تحتاجها."
                resolved_model = provider_state.get("model", "")

            providers.append(
                {
                    "id": provider_id,
                    "label": metadata["label"],
                    "description": metadata["description"],
                    "configured": configured,
                    "model": provider_state.get("model", ""),
                    "model_suggestions": model_suggestions,
                    "token_label": metadata["token_label"],
                    "token_masked": _mask_secret(provider_state.get(metadata["token_field"], "")) if metadata["token_field"] else "لا ينطبق",
                    "connection_target": connection_target,
                    "available_models": available_models,
                    "cloud_models": cloud_models,
                    "connection_ok": connection_ok,
                    "connection_message": connection_message,
                    "selected_model_available": selected_model_available,
                    "catalog_message": catalog_message,
                    "resolved_model": resolved_model,
                }
            )

        return {
            "active_provider": state["active_provider"],
            "temperature": state["temperature"],
            "max_tokens": state["max_tokens"],
            "providers": providers,
            "embeddings_api_key_masked": _mask_secret(state["embeddings"].get("api_key", "")),
            "embedding_model": settings.embedding_model,
            "updated_at": state.get("updated_at"),
            "runtime_settings_path": str(self._path),
        }

    def update_from_form(self, form_data: dict) -> RuntimeUpdateResult:
        with self._lock:
            state = self.get_state()
            changed_fields: list[str] = []

            active_provider = self._normalize_provider(form_data.get("active_provider"))
            if active_provider != state["active_provider"]:
                state["active_provider"] = active_provider
                changed_fields.append("active_provider")

            try:
                temperature = float(form_data.get("temperature", state["temperature"]))
            except (TypeError, ValueError):
                temperature = state["temperature"]
            temperature = min(max(temperature, 0.0), 1.5)
            if temperature != state["temperature"]:
                state["temperature"] = temperature
                changed_fields.append("temperature")

            try:
                max_tokens = int(form_data.get("max_tokens", state["max_tokens"]))
            except (TypeError, ValueError):
                max_tokens = state["max_tokens"]
            max_tokens = min(max(max_tokens, 128), 8192)
            if max_tokens != state["max_tokens"]:
                state["max_tokens"] = max_tokens
                changed_fields.append("max_tokens")

            for provider_id in SUPPORTED_PROVIDERS:
                model_key = f"{provider_id}_model"
                new_model = (form_data.get(model_key) or "").strip()
                if new_model and new_model != state["providers"][provider_id].get("model"):
                    state["providers"][provider_id]["model"] = new_model
                    changed_fields.append(model_key)

            new_openrouter_key = (form_data.get("openrouter_api_key") or "").strip()
            if new_openrouter_key and new_openrouter_key != state["providers"]["openrouter"].get("api_key"):
                state["providers"]["openrouter"]["api_key"] = new_openrouter_key
                changed_fields.append("openrouter_api_key")

            new_gemini_key = (form_data.get("gemini_api_key") or "").strip()
            if new_gemini_key and new_gemini_key != state["providers"]["gemini"].get("api_key"):
                state["providers"]["gemini"]["api_key"] = new_gemini_key
                changed_fields.append("gemini_api_key")

            new_gemini_base_url = (form_data.get("gemini_api_base_url") or "").strip()
            if new_gemini_base_url and new_gemini_base_url != state["providers"]["gemini"].get("api_base_url"):
                state["providers"]["gemini"]["api_base_url"] = new_gemini_base_url.rstrip("/")
                changed_fields.append("gemini_api_base_url")

            new_ollama_base_url = (form_data.get("ollama_base_url") or "").strip()
            if new_ollama_base_url and new_ollama_base_url != state["providers"]["ollama"].get("base_url"):
                state["providers"]["ollama"]["base_url"] = new_ollama_base_url.rstrip("/")
                changed_fields.append("ollama_base_url")

            new_openai_key = (form_data.get("openai_api_key") or "").strip()
            openai_api_key_changed = False
            if new_openai_key and new_openai_key != state["embeddings"].get("api_key"):
                state["embeddings"]["api_key"] = new_openai_key
                changed_fields.append("openai_api_key")
                openai_api_key_changed = True

            if changed_fields:
                self.save_state(state)

        return RuntimeUpdateResult(
            openai_api_key_changed=openai_api_key_changed,
            changed_fields=changed_fields,
        )


_runtime_settings_store: Optional[RuntimeSettingsStore] = None


def get_runtime_settings_store() -> RuntimeSettingsStore:
    global _runtime_settings_store
    if _runtime_settings_store is None:
        _runtime_settings_store = RuntimeSettingsStore()
    return _runtime_settings_store
