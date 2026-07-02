"""نقطة الدخول الرئيسية — FastAPI + Telegram Webhook"""

import logging
import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import chromadb
from fastapi import FastAPI, Request, Response
from telegram import Update
from app.admin_panel import (
    prepare_article_autopilot_service_shutdown,
    resume_article_autopilot_if_enabled,
    router as admin_panel_router,
    watch_article_autopilot_continuity,
)
from app.config import get_settings
from app.bot import create_bot_app, set_bot_commands
from app.gemini_file_search import get_gemini_file_search_service
from app.ollama_service import get_ollama_catalog
from app.rag.engine import get_engine
from app.rag.ingest import CHROMA_COLLECTION_METADATA
from app.official_sync import get_official_sync_service
from app.runtime_settings import get_runtime_settings_store

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# تطبيق البوت (Telegram)
bot_app = create_bot_app() if settings.telegram_runtime_enabled else None


def _is_loopback_request(request: Request) -> bool:
    client_host = (request.client.host if request.client else "") or ""
    return client_host in {"127.0.0.1", "::1", "localhost"}


async def auto_sync_documents(engine):
    """مراقبة مجلد documents/ وإعادة المزامنة تلقائياً."""
    while True:
        await asyncio.sleep(settings.documents_sync_interval_seconds)
        try:
            await engine.sync_if_documents_changed()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"❌ فشل التحديث التلقائي للمستندات: {e}")


async def auto_sync_official_sources(service):
    """فحص المصادر الرسمية بشكل دوري ومزامنتها عند الحاجة."""
    while True:
        await asyncio.sleep(settings.official_sync_interval_seconds)
        try:
            result = await service.sync(force=False)
            logger.info(
                "🌐 المزامنة الرسمية الدورية: checked=%s changed=%s failed=%s build=%s",
                result.checked_entries,
                result.changed_entries,
                result.failed_entries,
                result.build_triggered,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"❌ فشل التحديث الرسمي الدوري: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """أحداث بدء وإيقاف الخادم"""
    sync_task = None
    official_sync_task = None
    initial_official_sync_task = None
    article_autopilot_watchdog_task = None
    polling_started = False

    # --- بدء التشغيل ---
    logger.info("🚀 جارٍ تشغيل الخادم...")

    # تهيئة البوت
    if settings.telegram_runtime_enabled and bot_app is not None:
        await bot_app.initialize()
        await bot_app.start()

        # تسجيل أوامر البوت
        await set_bot_commands(bot_app)
    else:
        logger.warning("⚠️ تشغيل تيليغرام معطل — وضع لوحة التحكم/الصيانة فقط")

    # تهيئة محرك RAG
    engine = get_engine()
    if settings.documents_sync_enabled:
        try:
            await engine.sync_if_documents_changed()
        except Exception as e:
            logger.error(f"❌ تعذر إجراء المزامنة الأولية للمستندات: {e}")
    else:
        logger.info("📂 المزامنة الأولية للمستندات معطلة في الإعدادات الحالية")

    count = engine.get_collection_count()
    logger.info(f"📚 قاعدة المعرفة: {count} مقطع")

    if settings.documents_sync_enabled:
        sync_task = asyncio.create_task(auto_sync_documents(engine))
        logger.info(
            "📂 المزامنة التلقائية لملفات documents/ مفعلة كل %s ثوانٍ",
            settings.documents_sync_interval_seconds,
        )

    if settings.official_sync_enabled:
        official_sync_service = get_official_sync_service()
        official_sync_task = asyncio.create_task(auto_sync_official_sources(official_sync_service))
        logger.info(
            "🌐 المزامنة الرسمية للأنظمة مفعلة كل %s ثانية",
            settings.official_sync_interval_seconds,
        )
        async def initial_official_sync():
            try:
                result = await official_sync_service.sync(force=False)
                logger.info(
                    "🌐 المزامنة الرسمية الأولية: checked=%s changed=%s failed=%s build=%s",
                    result.checked_entries,
                    result.changed_entries,
                    result.failed_entries,
                    result.build_triggered,
                )
            except Exception as e:
                logger.error(f"❌ تعذر إجراء المزامنة الرسمية الأولية: {e}")

        initial_official_sync_task = asyncio.create_task(initial_official_sync())

    # ربط Webhook
    if settings.telegram_runtime_enabled and bot_app is not None:
        if settings.webhook_url:
            await bot_app.bot.set_webhook(
                url=settings.webhook_url,
                allowed_updates=Update.ALL_TYPES,
            )
            logger.info(f"🔗 Webhook: {settings.webhook_url}")
        else:
            await bot_app.bot.delete_webhook(drop_pending_updates=False)
            await bot_app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            polling_started = True
            logger.warning("⚠️ WEBHOOK_URL غير محدد — تم تفعيل polling المحلي بدلًا من webhook")

    logger.info("✅ الخادم جاهز!")
    try:
        resumed_job_id = resume_article_autopilot_if_enabled()
        if resumed_job_id:
            logger.info("🔁 تم استئناف التطوير المستمر تلقائيًا: %s", resumed_job_id)
    except Exception as e:
        logger.error(f"❌ تعذر استئناف التطوير المستمر تلقائيًا: {e}")
    article_autopilot_watchdog_task = asyncio.create_task(watch_article_autopilot_continuity())
    logger.info("🔁 مراقب استمرارية التطوير المستمر مفعّل")

    yield

    # --- إيقاف التشغيل ---
    logger.info("🛑 جارٍ إيقاف الخادم...")
    await asyncio.to_thread(prepare_article_autopilot_service_shutdown)
    if sync_task:
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task
    if official_sync_task:
        official_sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await official_sync_task
    if initial_official_sync_task:
        initial_official_sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await initial_official_sync_task
    if article_autopilot_watchdog_task:
        article_autopilot_watchdog_task.cancel()
        with suppress(asyncio.CancelledError):
            await article_autopilot_watchdog_task
    if polling_started and bot_app is not None:
        with suppress(Exception):
            await bot_app.updater.stop()
    engine = get_engine()
    await engine.close()
    if settings.telegram_runtime_enabled and bot_app is not None:
        await bot_app.stop()
        await bot_app.shutdown()


# تطبيق FastAPI
app = FastAPI(
    title="مساعد الاستشارات القانونية",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(admin_panel_router)


@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """استقبال تحديثات تيليغرام"""
    if not settings.telegram_runtime_enabled or bot_app is None:
        return Response(status_code=503)
    try:
        data = await request.json()
        update = Update.de_json(data=data, bot=bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة التحديث: {e}")
    return Response(status_code=200)


@app.post("/internal/rag/reindex")
async def internal_rag_reindex(request: Request):
    """إعادة فهرسة آمنة من داخل نفس عملية الخدمة."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    engine = get_engine()
    synced = await engine.sync_if_documents_changed(force=True)
    return {
        "status": "ok" if synced else "error",
        "synced": synced,
        "knowledge_base_chunks": engine.get_collection_count(),
    }


@app.post("/internal/rag/query")
async def internal_rag_query(request: Request):
    """استعلام داخلي يعيد الجواب مع التشخيص الكامل لأغراض الـbenchmark المحلي."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    question = str((payload or {}).get("question", "")).strip()
    answer_mode = str((payload or {}).get("answer_mode", "")).strip() or "consultation"
    retrieval_profile = str((payload or {}).get("retrieval_profile", "")).strip()
    if not question:
        return Response(status_code=400)

    result = await get_engine().query(
        question,
        answer_mode=answer_mode,
        retrieval_profile=retrieval_profile,
    )
    return {
        "status": "ok",
        "question": question,
        "answer_mode": answer_mode,
        "retrieval_profile": result.diagnostics.get("retrieval_profile", retrieval_profile or "legal_baseline"),
        "result": {
            "answer": result.answer,
            "confidence": result.confidence,
            "sources": result.sources,
            "needs_escalation": result.needs_escalation,
            "diagnostics": result.diagnostics,
        },
    }


@app.post("/internal/rag/embedding-health")
async def internal_rag_embedding_health(request: Request):
    """فحص مسار الـ embeddings من داخل عملية الخدمة نفسها."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    probe_text = str((payload or {}).get("text", "")).strip() or "اختبار الاسترجاع الدلالي"
    engine = get_engine()
    try:
        vector = await asyncio.wait_for(
            asyncio.to_thread(engine._embeddings.embed_query, probe_text),
            timeout=30,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "embedding_ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }

    return {
        "status": "ok",
        "embedding_ok": True,
        "embedding_dimension": len(vector),
        "dense_metric": engine._get_vector_distance_metric(),
    }


async def _build_retrieval_probe_response(question: str, retrieval_profile: str):
    engine = get_engine()
    retrieval_result = await engine._hybrid_retrieve(
        question,
        answer_mode="benchmark",
        retrieval_profile=retrieval_profile,
    )
    ranked_candidates = retrieval_result.get("ranked_candidates") or []
    selected_candidates = retrieval_result.get("selected_candidates") or []
    query_data = retrieval_result.get("query_data") or {}
    profile_config = query_data.get("retrieval_profile_config") or {}

    def summarize_candidate(candidate: dict, index: int) -> dict:
        entry = candidate.get("entry") or {}
        return {
            "rank": index,
            "citation": entry.get("citation_short_ar", ""),
            "regulation_slug": entry.get("regulation_slug", ""),
            "article_index": entry.get("article_index"),
            "hybrid_score": round(float(candidate.get("hybrid_score") or 0.0), 6),
            "dense_score": round(float(candidate.get("dense_score") or 0.0), 6),
            "dense_rank": candidate.get("dense_rank"),
            "dense_hits": candidate.get("dense_hits", 0),
            "lexical_score": round(float(candidate.get("lexical_score") or 0.0), 6),
            "lexical_rank": candidate.get("lexical_rank"),
            "lexical_hits": candidate.get("lexical_hits", 0),
        }

    dense_ranked_count = sum(1 for item in ranked_candidates if item.get("dense_rank") is not None)
    dense_nonzero_count = sum(1 for item in ranked_candidates if float(item.get("dense_score") or 0.0) > 0.0)
    lexical_ranked_count = sum(1 for item in ranked_candidates if item.get("lexical_rank") is not None)
    lexical_nonzero_count = sum(1 for item in ranked_candidates if float(item.get("lexical_score") or 0.0) > 0.0)
    best_dense_score = max((float(item.get("dense_score") or 0.0) for item in ranked_candidates), default=0.0)
    best_lexical_score = max((float(item.get("lexical_score") or 0.0) for item in ranked_candidates), default=0.0)
    semantic_active = dense_ranked_count > 0 and best_dense_score > 0.0
    selected_regulations = []
    for candidate in selected_candidates:
        entry = candidate.get("entry") or {}
        slug = str(entry.get("regulation_slug") or "").strip()
        if slug and slug not in selected_regulations:
            selected_regulations.append(slug)

    return {
        "status": "ok",
        "retrieval_profile": query_data.get("retrieval_profile", retrieval_profile),
        "configured_dense_weight": profile_config.get("dense_norm_weight"),
        "configured_lexical_weight": profile_config.get("lexical_norm_weight"),
        "semantic_active": semantic_active,
        "effective_dense_weight": profile_config.get("dense_norm_weight") if semantic_active else 0.0,
        "effective_lexical_weight": profile_config.get("lexical_norm_weight"),
        "dense_metric": engine._get_vector_distance_metric(),
        "ranked_candidate_count": len(ranked_candidates),
        "selected_candidate_count": len(selected_candidates),
        "dense_ranked_count": dense_ranked_count,
        "dense_nonzero_count": dense_nonzero_count,
        "lexical_ranked_count": lexical_ranked_count,
        "lexical_nonzero_count": lexical_nonzero_count,
        "best_dense_score": round(best_dense_score, 6),
        "best_lexical_score": round(best_lexical_score, 6),
        "dominant_domain": query_data.get("dominant_domain"),
        "selected_regulations": selected_regulations,
        "required_core_regulations": query_data.get("required_core_regulations", []),
        "required_companion_regulations": query_data.get("required_companion_regulations", []),
        "matched_document_bundles": query_data.get("matched_document_bundles", []),
        "matched_issue_axis_bundles": query_data.get("matched_issue_axis_bundles", []),
        "top_ranked": [
            summarize_candidate(candidate, index)
            for index, candidate in enumerate(ranked_candidates[:24], start=1)
        ],
        "top_selected": [
            summarize_candidate(candidate, index)
            for index, candidate in enumerate(selected_candidates[:24], start=1)
        ],
    }


@app.post("/internal/rag/retrieval-probe")
async def internal_rag_retrieval_probe(request: Request):
    """فحص استرجاع فقط يعيد إحصاءات dense/lexical دون توليد جواب."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    question = str((payload or {}).get("question", "")).strip()
    retrieval_profile = str((payload or {}).get("retrieval_profile", "")).strip() or "legal_baseline"
    if not question:
        return Response(status_code=400)

    return await _build_retrieval_probe_response(question, retrieval_profile)


@app.get("/internal/rag/retrieval-probe")
async def internal_rag_retrieval_probe_get(request: Request):
    """نسخة GET داخلية للفحص عندما تمنع البيئة طلبات POST من الطرفية."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    question = str(request.query_params.get("question") or "").strip()
    retrieval_profile = str(request.query_params.get("retrieval_profile") or "").strip() or "legal_baseline"
    if not question:
        return Response(status_code=400)

    return await _build_retrieval_probe_response(question, retrieval_profile)


@app.post("/internal/rag/rebuild-vector-index-from-current")
async def internal_rag_rebuild_vector_index_from_current(request: Request):
    """إعادة بناء فهرس Chroma الدلالي من السجلات الحالية مع الحفاظ على العد."""
    if not _is_loopback_request(request):
        return Response(status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    if str((payload or {}).get("confirm", "")).strip() != "rebuild-vector-index":
        return Response(status_code=400)

    expected_count = int((payload or {}).get("expected_count") or 0)
    batch_size = max(1, min(int((payload or {}).get("batch_size") or settings.embedding_batch_size), 128))
    export_batch_size = max(100, min(int((payload or {}).get("export_batch_size") or 1000), 2500))
    probe_text = str((payload or {}).get("probe_text", "")).strip() or "اختبار الاسترجاع الدلالي"

    engine = get_engine()
    persist_dir = Path(settings.chroma_persist_dir)
    if not persist_dir.is_absolute():
        persist_dir = PROJECT_ROOT / persist_dir
    export_path = Path("/private/tmp") / f"saudi_legal_chroma_export_{int(time.time())}.jsonl"
    temp_collection_name = f"{settings.chroma_collection}_rebuild_{int(time.time())}"

    def sanitize_metadata(metadata) -> dict:
        clean = {}
        for key, value in (metadata or {}).items():
            if isinstance(value, (str, int, float, bool)):
                clean[key] = value
        return clean

    async with engine._sync_lock:
        source_collection = engine._vectorstore._collection
        current_count = source_collection.count()
        if expected_count and current_count != expected_count:
            return {
                "status": "failed",
                "reason": "count_mismatch_before_rebuild",
                "expected_count": expected_count,
                "current_count": current_count,
            }

        exported = 0
        with export_path.open("w", encoding="utf-8") as handle:
            for offset in range(0, current_count, export_batch_size):
                batch = source_collection.get(
                    limit=export_batch_size,
                    offset=offset,
                    include=["documents", "metadatas"],
                )
                ids = batch.get("ids") or []
                documents = batch.get("documents") or []
                metadatas = batch.get("metadatas") or []
                for item_id, document, metadata in zip(ids, documents, metadatas):
                    handle.write(
                        json.dumps(
                            {
                                "id": item_id,
                                "document": document or "",
                                "metadata": sanitize_metadata(metadata),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    exported += 1

        if exported != current_count:
            return {
                "status": "failed",
                "reason": "export_count_mismatch",
                "current_count": current_count,
                "exported_count": exported,
                "export_path": str(export_path),
            }

        client = chromadb.PersistentClient(path=str(persist_dir))
        try:
            client.delete_collection(temp_collection_name)
        except Exception:
            pass

        temp_collection = client.get_or_create_collection(
            temp_collection_name,
            metadata=CHROMA_COLLECTION_METADATA,
        )

        added = 0
        pending_ids: list[str] = []
        pending_documents: list[str] = []
        pending_metadatas: list[dict] = []

        def flush_batch() -> int:
            if not pending_ids:
                return 0
            embeddings = engine._embeddings.embed_documents(pending_documents)
            temp_collection.add(
                ids=list(pending_ids),
                documents=list(pending_documents),
                metadatas=list(pending_metadatas),
                embeddings=embeddings,
            )
            flushed = len(pending_ids)
            pending_ids.clear()
            pending_documents.clear()
            pending_metadatas.clear()
            return flushed

        with export_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                pending_ids.append(str(record["id"]))
                pending_documents.append(str(record.get("document") or ""))
                pending_metadatas.append(sanitize_metadata(record.get("metadata") or {}))
                if len(pending_ids) >= batch_size:
                    added += await asyncio.to_thread(flush_batch)
            added += await asyncio.to_thread(flush_batch)

        temp_count = temp_collection.count()
        if temp_count != current_count or added != current_count:
            return {
                "status": "failed",
                "reason": "temp_count_mismatch",
                "current_count": current_count,
                "added_count": added,
                "temp_count": temp_count,
                "temp_collection": temp_collection_name,
                "export_path": str(export_path),
            }

        probe_embedding = await asyncio.to_thread(engine._embeddings.embed_query, probe_text)
        probe_result = temp_collection.query(
            query_embeddings=[probe_embedding],
            n_results=min(90, temp_count),
            include=["documents", "metadatas", "distances"],
        )
        probe_result_count = len((probe_result.get("ids") or [[]])[0])
        if probe_result_count < min(90, temp_count):
            return {
                "status": "failed",
                "reason": "temp_vector_probe_incomplete",
                "probe_result_count": probe_result_count,
                "temp_collection": temp_collection_name,
                "export_path": str(export_path),
            }

        client.delete_collection(settings.chroma_collection)
        temp_collection.modify(name=settings.chroma_collection)
        engine._vectorstore = engine._create_vectorstore()

    return {
        "status": "ok",
        "rebuild_ok": True,
        "collection": settings.chroma_collection,
        "preserved_count": current_count,
        "added_count": added,
        "probe_result_count": probe_result_count,
        "embedding_dimension": len(probe_embedding),
        "dense_metric": engine._get_vector_distance_metric(),
        "export_path": str(export_path),
    }


@app.get("/health")
async def health_check():
    """فحص صحة الخادم"""
    engine = get_engine()
    official_sync_status = get_official_sync_service().get_status()
    generation_status = engine.get_generation_status()
    gemini_file_search_status = get_gemini_file_search_service().get_status()
    runtime_store = get_runtime_settings_store()
    ollama_generation = runtime_store.get_generation_for_provider("ollama")
    ollama_status = get_ollama_catalog(ollama_generation.get("base_url") or settings.ollama_base_url)
    knowledge_base_chunks = max(engine.get_collection_count(), engine.get_structured_chunk_count())
    return {
        "status": "ok",
        "instance_label": settings.instance_label,
        "project_root": str(PROJECT_ROOT),
        "configured_server_port": settings.server_port,
        "knowledge_base_chunks": knowledge_base_chunks,
        "generation_provider": generation_status["provider"],
        "generation_model": generation_status["model"],
        "telegram_mode": "disabled" if not settings.telegram_runtime_enabled else ("webhook" if settings.webhook_url else "polling"),
        "official_synced_entries": official_sync_status["synced_entries"],
        "official_catalog_entries": official_sync_status["catalog_entries"],
        "official_sync_last_run": official_sync_status["last_run_finished_at"],
        "gemini_file_search_store": gemini_file_search_status["store_name"],
        "gemini_file_search_files": gemini_file_search_status["file_count"],
        "ollama_connected": ollama_status["ok"],
        "ollama_local_models": len(ollama_status["local_models"]),
        "ollama_base_url": ollama_generation.get("base_url") or ollama_status["base_url"],
        "ollama_resolved_base_url": ollama_status["resolved_base_url"],
    }


@app.get("/")
async def root():
    return {
        "message": "⚖️ مساعد الاستشارات القانونية يعمل",
        "instance_label": settings.instance_label,
        "project_root": str(PROJECT_ROOT),
        "configured_server_port": settings.server_port,
        "admin_panel": "/admin",
        "health": "/health",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
