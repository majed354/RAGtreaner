"""خدمة Gemini File Search للمقارنة مع RAG المحلي."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.rag.engine import _parse_confidence
from app.rag.ingest import get_documents_dir
from app.runtime_settings import get_runtime_settings_store

logger = logging.getLogger(__name__)
settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILE_SEARCH_QUERY_PROMPT = """أنت مساعد قانوني مرجعي يعتمد على نتائج File Search فقط.

- قدّم الإجابة بالعربية بصياغة تحليلية مهنية وموجزة.
- لا تنقل النصوص النظامية حرفيًا على نحو طويل، بل لخّصها بأسلوبك.
- اذكر اسم النظام والمادة أو الإحالة عند الاستناد إلى المقاطع المسترجعة.
- إذا ظهرت أكثر من مادة ذات صلة فرتّبها بوضوح.
- إذا كانت النتائج غير كافية فقل ذلك صراحة.

في آخر سطر فقط اكتب:
CONFIDENCE: high
أو
CONFIDENCE: medium
أو
CONFIDENCE: low"""
FILE_SEARCH_FALLBACK_PROMPT = """أنت مساعد قانوني مرجعي.

- أجب بالعربية اعتمادًا على المقاطع التي يسترجعها File Search فقط.
- لا تنقل أو تكرر النصوص النظامية حرفيًا، بل أعد صياغتها بإيجاز شديد.
- اذكر اسم النظام والمادة أو الإحالة متى ظهرت في المقاطع.
- ركز على الخلاصة العملية والشروط والضوابط.
- لا تختلق معلومات خارج النصوص المسترجعة.
- إذا كانت الإجابة جزئية أو غير جازمة فاذكر ذلك بوضوح.

في آخر سطر فقط اكتب:
CONFIDENCE: high
أو
CONFIDENCE: medium
أو
CONFIDENCE: low"""

try:
    from google import genai
except Exception:  # pragma: no cover - handled at runtime
    genai = None


@dataclass
class GeminiFileSearchResult:
    answer: str
    confidence: str
    sources: list[dict]
    store_name: Optional[str]
    synced: bool
    error: Optional[str] = None


class GeminiFileSearchService:
    """فهرسة ملفات المعرفة داخل Gemini File Search وتشغيل المقارنة."""

    def __init__(self):
        self._runtime_store = get_runtime_settings_store()
        self._state_path = self._resolve_state_path()
        self._lock = asyncio.Lock()

    def _resolve_state_path(self) -> Path:
        runtime_path = Path(settings.runtime_settings_path)
        if not runtime_path.is_absolute():
            runtime_path = (PROJECT_ROOT / runtime_path).resolve()
        return runtime_path.parent / "gemini_file_search_state.json"

    def _load_state(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, payload: dict):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self._state_path.chmod(0o600)
        except Exception:
            pass

    def _get_gemini_runtime(self) -> dict:
        state = self._runtime_store.get_state()
        provider_state = state["providers"]["gemini"]
        return {
            "api_key": provider_state.get("api_key") or settings.gemini_api_key,
            "model": provider_state.get("model") or settings.gemini_model,
            "api_base_url": provider_state.get("api_base_url") or settings.gemini_api_base_url,
            "temperature": state.get("temperature", settings.generation_temperature),
            "max_tokens": state.get("max_tokens", settings.generation_max_tokens),
        }

    def _get_upload_client(self):
        if genai is None:
            raise RuntimeError("حزمة google-genai غير مثبّتة بعد.")

        gemini_runtime = self._get_gemini_runtime()
        api_key = gemini_runtime["api_key"]
        if not api_key:
            raise RuntimeError("Gemini API Key غير مضبوط.")
        return genai.Client(api_key=api_key)

    def _list_knowledge_files(self) -> list[Path]:
        knowledge_dir = get_documents_dir()
        if not knowledge_dir.exists():
            return []
        return [path for path in sorted(knowledge_dir.rglob("*.txt")) if path.is_file()]

    def _build_files_state(self) -> dict:
        knowledge_dir = get_documents_dir()
        files = []
        for path in self._list_knowledge_files():
            stat = path.stat()
            files.append(
                {
                    "relative_path": str(path.relative_to(knowledge_dir)),
                    "absolute_path": str(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )

        fingerprint_source = json.dumps(files, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        return {
            "knowledge_dir": str(knowledge_dir),
            "files": files,
            "file_count": len(files),
            "fingerprint": fingerprint,
        }

    def _api_url(self, runtime: dict, path: str) -> str:
        base_url = (runtime.get("api_base_url") or settings.gemini_api_base_url).rstrip("/")
        return f"{base_url}/{path.lstrip('/')}"

    def _request_json(
        self,
        http_client: httpx.Client,
        runtime: dict,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        query_params = dict(params or {})
        query_params["key"] = runtime["api_key"]
        response = http_client.request(
            method,
            self._api_url(runtime, path),
            params=query_params,
            json=json_body,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            details = response.text.strip()
            raise RuntimeError(f"{exc} | {details}") from exc

        if not response.content:
            return {}
        return response.json()

    def _create_store(self, http_client: httpx.Client, runtime: dict, display_name: str) -> dict:
        return self._request_json(
            http_client,
            runtime,
            "POST",
            "fileSearchStores",
            json_body={"displayName": display_name},
        )

    def _delete_store(self, http_client: httpx.Client, runtime: dict, store_name: str):
        self._request_json(
            http_client,
            runtime,
            "DELETE",
            store_name,
            params={"force": "true"},
        )

    def _wait_for_operation(
        self,
        http_client: httpx.Client,
        runtime: dict,
        operation_name: str,
        *,
        timeout_seconds: int = 300,
    ) -> dict:
        started_at = time.time()
        while True:
            payload = self._request_json(http_client, runtime, "GET", operation_name)
            if payload.get("done"):
                if payload.get("error"):
                    raise RuntimeError(f"فشلت عملية Gemini File Search: {json.dumps(payload['error'], ensure_ascii=False)}")
                return payload
            if time.time() - started_at > timeout_seconds:
                raise TimeoutError(f"انتهت مهلة انتظار العملية: {operation_name}")
            time.sleep(3)

    def _upload_file(self, client, item: dict) -> Any:
        display_name = item["relative_path"]
        return client.files.upload(
            file=item["absolute_path"],
            config={
                "display_name": display_name,
                "mime_type": "text/plain",
            },
        )

    def _import_file_to_store(self, http_client: httpx.Client, runtime: dict, store_name: str, uploaded_file_name: str, item: dict) -> dict:
        operation = self._request_json(
            http_client,
            runtime,
            "POST",
            f"{store_name}:importFile",
            json_body={
                "fileName": uploaded_file_name,
                "customMetadata": [
                    {"key": "relative_path", "stringValue": item["relative_path"]},
                    {"key": "display_name", "stringValue": Path(item["relative_path"]).name},
                ],
                "chunkingConfig": {
                    "whiteSpaceConfig": {
                        "maxTokensPerChunk": 300,
                        "maxOverlapTokens": 40,
                    }
                },
            },
        )
        return self._wait_for_operation(http_client, runtime, operation["name"])

    def _extract_response_text(self, data: dict) -> Optional[str]:
        candidates = data.get("candidates") or []
        if not candidates:
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [part.get("text", "") for part in parts if part.get("text")]
        if not text_parts:
            return None
        return "\n".join(text_parts).strip()

    def _extract_finish_reason(self, data: dict) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        return str(candidates[0].get("finishReason") or candidates[0].get("finish_reason") or "")

    def _custom_metadata_to_dict(self, raw_metadata: Any) -> dict:
        metadata = {}
        if not isinstance(raw_metadata, list):
            return metadata
        for item in raw_metadata:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not key:
                continue
            for candidate_key in ("stringValue", "string_value", "numericValue", "numeric_value"):
                if candidate_key in item:
                    metadata[key] = item[candidate_key]
                    break
        return metadata

    def _choose_source_title(self, retrieved_context: dict) -> str:
        custom_metadata = self._custom_metadata_to_dict(
            retrieved_context.get("customMetadata") or retrieved_context.get("custom_metadata")
        )
        if custom_metadata.get("display_name"):
            return str(custom_metadata["display_name"])
        if custom_metadata.get("relative_path"):
            return str(custom_metadata["relative_path"])

        title = retrieved_context.get("title") or retrieved_context.get("displayName") or ""
        if title and not re.fullmatch(r"[a-z0-9_-]{8,}", title, flags=re.IGNORECASE):
            return title
        return title or "مستند مسترجع"

    def _extract_sources(self, data: dict) -> list[dict]:
        sources = []
        candidates = data.get("candidates") or []
        if not candidates:
            return sources

        grounding_metadata = candidates[0].get("groundingMetadata") or candidates[0].get("grounding_metadata") or {}
        grounding_chunks = grounding_metadata.get("groundingChunks") or grounding_metadata.get("grounding_chunks") or []

        for index, chunk in enumerate(grounding_chunks, start=1):
            retrieved_context = chunk.get("retrievedContext") or chunk.get("retrieved_context") or {}
            if not retrieved_context:
                continue
            sources.append(
                {
                    "index": index,
                    "title": self._choose_source_title(retrieved_context),
                    "text": retrieved_context.get("text", "") or "",
                    "uri": retrieved_context.get("uri", "") or "",
                }
            )
        return sources

    def _build_grounding_only_answer(self, sources: list[dict]) -> str:
        lines = [
            "تعذر على Gemini File Search إصدار صياغة نهائية مباشرة بسبب قيد يمنع الاسترجاع الحرفي الطويل، لكن المقاطع المسترجعة تشير إلى الآتي:",
        ]
        for item in sources[:3]:
            snippet = " ".join((item.get("text") or "").split())[:260]
            lines.append(f"- المرجع المسترجع: {item.get('title') or 'مستند مسترجع'}")
            if snippet:
                lines.append(f"  المستفاد: {snippet}...")
        lines.append("CONFIDENCE: low")
        return "\n".join(lines)

    def _build_upload_cleanup_queue(self, saved_state: dict) -> list[str]:
        uploaded_files = saved_state.get("uploaded_files") or []
        return [item for item in uploaded_files if isinstance(item, str) and item]

    def _cleanup_uploaded_files(self, client, file_names: list[str]):
        for file_name in file_names:
            try:
                client.files.delete(name=file_name)
            except Exception as exc:
                logger.warning("تعذر حذف الملف المؤقت من Gemini Files %s: %s", file_name, exc)

    def _sync_store_sync(self, force: bool = False) -> dict:
        runtime = self._get_gemini_runtime()
        if not runtime["api_key"]:
            raise RuntimeError("Gemini API Key غير مضبوط.")

        files_state = self._build_files_state()
        if not files_state["files"]:
            raise RuntimeError("لا توجد ملفات نصية داخل مجلد المعرفة لمزامنتها.")

        saved_state = self._load_state()
        existing_store_name = saved_state.get("store_name")
        uploaded_files_to_cleanup = self._build_upload_cleanup_queue(saved_state)

        if (
            not force
            and saved_state.get("fingerprint") == files_state["fingerprint"]
            and existing_store_name
        ):
            return {
                "store_name": existing_store_name,
                "synced": True,
                "changed": False,
                "file_count": files_state["file_count"],
            }

        upload_client = self._get_upload_client()
        new_store_name = None
        new_uploaded_files = []
        with httpx.Client(timeout=120.0) as http_client:
            if existing_store_name:
                try:
                    self._delete_store(http_client, runtime, existing_store_name)
                except Exception as exc:
                    logger.warning("تعذر حذف File Search store السابق %s: %s", existing_store_name, exc)

            display_name = f"legal-knowledge-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            store = self._create_store(http_client, runtime, display_name)
            new_store_name = store["name"]

            try:
                for item in files_state["files"]:
                    uploaded_file = self._upload_file(upload_client, item)
                    uploaded_file_name = getattr(uploaded_file, "name", "") or ""
                    if not uploaded_file_name:
                        raise RuntimeError(f"لم يرجع رفع الملف اسماً صالحاً للملف: {item['relative_path']}")
                    new_uploaded_files.append(uploaded_file_name)
                    self._import_file_to_store(http_client, runtime, new_store_name, uploaded_file_name, item)
            except Exception:
                try:
                    self._delete_store(http_client, runtime, new_store_name)
                except Exception as cleanup_exc:
                    logger.warning("تعذر حذف File Search store الجديد بعد الفشل %s: %s", new_store_name, cleanup_exc)
                self._cleanup_uploaded_files(upload_client, new_uploaded_files)
                raise

        self._cleanup_uploaded_files(upload_client, uploaded_files_to_cleanup + new_uploaded_files)
        self._save_state(
            {
                "store_name": new_store_name,
                "display_name": display_name,
                "fingerprint": files_state["fingerprint"],
                "knowledge_dir": files_state["knowledge_dir"],
                "file_count": files_state["file_count"],
                "files": [item["relative_path"] for item in files_state["files"]],
                "uploaded_files": [],
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {
            "store_name": new_store_name,
            "synced": True,
            "changed": True,
            "file_count": files_state["file_count"],
        }

    async def sync(self, force: bool = False) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._sync_store_sync, force)

    def _query_store(self, runtime: dict, store_name: str, question: str, *, system_prompt: str) -> dict:
        with httpx.Client(timeout=120.0) as http_client:
            return self._request_json(
                http_client,
                runtime,
                "POST",
                f"models/{runtime['model']}:generateContent",
                json_body={
                    "system_instruction": {
                        "parts": [{"text": system_prompt}],
                    },
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": question}],
                        }
                    ],
                    "tools": [
                        {
                            "file_search": {
                                "file_search_store_names": [store_name],
                            }
                        }
                    ],
                    "generationConfig": {
                        "temperature": runtime["temperature"],
                        "maxOutputTokens": runtime["max_tokens"],
                    },
                },
            )

    def _query_sync(self, question: str) -> GeminiFileSearchResult:
        sync_info = self._sync_store_sync(force=False)
        store_name = sync_info.get("store_name")
        if not store_name:
            raise RuntimeError("لم يتم إنشاء File Search store بعد.")

        runtime = self._get_gemini_runtime()
        try:
            response_data = self._query_store(
                runtime,
                store_name,
                question,
                system_prompt=FILE_SEARCH_QUERY_PROMPT,
            )
        except Exception as exc:
            message = str(exc)
            if re.search(r"(404|NOT_FOUND|not found|FileSearchStore)", message, flags=re.IGNORECASE):
                logger.warning("مسار Gemini File Search يحتاج إعادة مزامنة، سأعيد الإنشاء ثم أعيد المحاولة.")
                sync_info = self._sync_store_sync(force=True)
                store_name = sync_info.get("store_name")
                response_data = self._query_store(
                    runtime,
                    store_name,
                    question,
                    system_prompt=FILE_SEARCH_QUERY_PROMPT,
                )
            else:
                raise

        answer_text = self._extract_response_text(response_data)
        if not answer_text:
            finish_reason = self._extract_finish_reason(response_data)
            logger.warning(
                "Gemini File Search أعاد مراجع دون نص (finishReason=%s)، سأعيد المحاولة ببرومبت مختصر.",
                finish_reason or "unknown",
            )
            response_data = self._query_store(
                runtime,
                store_name,
                question,
                system_prompt=FILE_SEARCH_FALLBACK_PROMPT,
            )
            answer_text = self._extract_response_text(response_data)

        if not answer_text:
            sources = self._extract_sources(response_data)
            if sources:
                answer_text = self._build_grounding_only_answer(sources)
            else:
                raise RuntimeError("Gemini File Search لم يرجع نصاً قابلاً للقراءة.")

        answer, confidence = _parse_confidence(answer_text)
        return GeminiFileSearchResult(
            answer=answer,
            confidence=confidence,
            sources=self._extract_sources(response_data),
            store_name=store_name,
            synced=True,
        )

    async def query(self, question: str) -> GeminiFileSearchResult:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._query_sync, question)
            except Exception as exc:
                logger.error("تعذر تنفيذ Gemini File Search: %s", exc)
                return GeminiFileSearchResult(
                    answer="تعذر تنفيذ مسار Gemini File Search حالياً.",
                    confidence="low",
                    sources=[],
                    store_name=self._load_state().get("store_name"),
                    synced=False,
                    error=str(exc),
                )

    def get_status(self) -> dict:
        state = self._load_state()
        gemini_runtime = self._get_gemini_runtime()
        return {
            "configured": bool(gemini_runtime["api_key"]),
            "store_name": state.get("store_name"),
            "file_count": state.get("file_count", 0),
            "synced_at": state.get("synced_at"),
            "runtime_state_path": str(self._state_path),
            "gemini_model": gemini_runtime["model"],
        }


_gemini_file_search_service: Optional[GeminiFileSearchService] = None


def get_gemini_file_search_service() -> GeminiFileSearchService:
    global _gemini_file_search_service
    if _gemini_file_search_service is None:
        _gemini_file_search_service = GeminiFileSearchService()
    return _gemini_file_search_service
