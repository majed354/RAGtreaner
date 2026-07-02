"""تجهيز المستندات — تقطيع وتخزين في ChromaDB"""

import hashlib
import json
import os
import socket
import sys
import logging
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from contextlib import contextmanager
from typing import Optional
import chromadb
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.schema import Document

# إضافة المسار الجذري
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import get_settings
from app.runtime_settings import get_runtime_settings_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROMA_COLLECTION_METADATA = {"hnsw:space": "cosine"}


def get_embedding_api_key() -> str:
    """قراءة مفتاح OpenAI الفعلي من الإعدادات الحية أو ملف البيئة."""
    runtime_state = get_runtime_settings_store().get_state()
    return runtime_state["embeddings"].get("api_key") or get_settings().openai_api_key


def get_documents_dir() -> Path:
    """المجلد المعتمد لملفات المعرفة النصية."""
    settings = get_settings()
    knowledge_dir = Path(settings.knowledge_dir)
    if knowledge_dir.is_absolute():
        return knowledge_dir
    return (PROJECT_ROOT / knowledge_dir).resolve()


def get_structured_chunks_path() -> Path:
    """مسار ملف المقاطع القانونية المنظمة."""
    settings = get_settings()
    structured_chunks_path = Path(settings.structured_chunks_path)
    if structured_chunks_path.is_absolute():
        return structured_chunks_path
    return (PROJECT_ROOT / structured_chunks_path).resolve()


def get_reindex_lock_path() -> Path:
    settings = get_settings()
    return Path(settings.chroma_persist_dir) / ".rag-reindex.lock"


@contextmanager
def cross_process_reindex_lock(timeout_seconds: float = 30.0, poll_interval_seconds: float = 0.25):
    """قفل بسيط بين العمليات لمنع تعارض إعادة بناء ChromaDB."""
    lock_path = get_reindex_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()

    while True:
        try:
            lock_path.mkdir(exist_ok=False)
            break
        except FileExistsError:
            if time.monotonic() - start_time >= timeout_seconds:
                raise TimeoutError(f"تعذر الحصول على قفل إعادة الفهرسة: {lock_path}")
            time.sleep(poll_interval_seconds)

    try:
        yield
    finally:
        shutil.rmtree(lock_path, ignore_errors=True)


def list_text_files() -> list[Path]:
    """إرجاع ملفات .txt الحرة داخل مجلد المعرفة مع استبعاد النصوص القانونية المولدة عند توفر الطبقة البنيوية."""
    documents_dir = get_documents_dir()
    if not documents_dir.exists():
        return []

    structured_chunks_available = get_structured_chunks_path().exists()
    generated_regulations_dir = (documents_dir / "saudi_regulations").resolve()
    text_files = []
    for path in sorted(documents_dir.rglob("*.txt")):
        if not path.is_file():
            continue
        if structured_chunks_available and generated_regulations_dir in path.resolve().parents:
            continue
        text_files.append(path)
    return text_files


def build_documents_state() -> dict:
    """بناء بصمة سريعة لملفات documents/ لاكتشاف الإضافة أو التعديل."""
    files = []

    for filepath in list_text_files():
        stat = filepath.stat()
        files.append(
            {
                "name": str(filepath.relative_to(get_documents_dir())),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "kind": "text_file",
            }
        )

    structured_chunks_path = get_structured_chunks_path()
    if structured_chunks_path.exists():
        stat = structured_chunks_path.stat()
        files.append(
            {
                "name": str(structured_chunks_path.relative_to(PROJECT_ROOT)),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "kind": "structured_chunks",
            }
        )

    fingerprint_source = json.dumps(files, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()

    return {
        "documents_dir": str(get_documents_dir()),
        "exists": get_documents_dir().exists(),
        "files": files,
        "fingerprint": fingerprint,
    }


def try_trigger_running_service_sync() -> bool:
    """إذا كانت الخدمة الحية تعمل محلياً، اطلب منها إعادة الفهرسة داخلياً لتجنب سباق Chroma."""
    settings = get_settings()
    candidate_ports = []
    for port in [8032, settings.server_port]:
        if isinstance(port, int) and port > 0 and port not in candidate_ports:
            candidate_ports.append(port)

    for port in candidate_ports:
        url = f"http://127.0.0.1:{port}/internal/rag/reindex"
        request = urllib.request.Request(url, data=b"{}", method="POST")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("status") == "ok":
                    logger.info(
                        "🔁 تم تفويض إعادة الفهرسة إلى الخدمة الحية على المنفذ %s (%s مقطع)",
                        port,
                        payload.get("knowledge_base_chunks"),
                    )
                    return True
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            socket.timeout,
            ValueError,
            json.JSONDecodeError,
        ):
            continue

    return False


def load_structured_chunk_documents() -> list[Document]:
    """تحميل المقاطع القانونية المنظمة من JSONL."""
    documents = []
    structured_chunks_path = get_structured_chunks_path()
    if not structured_chunks_path.exists():
        return documents

    try:
        with structured_chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                text = (row.get("index_text") or row.get("text") or "").strip()
                if not text:
                    continue
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": row.get("citation_short_ar") or row.get("regulation_title_ar") or "مصدر منظم",
                            "file_path": str(structured_chunks_path),
                            "source_kind": "structured_chunk",
                            "chunk_id": row.get("chunk_id", ""),
                            "regulation_slug": row.get("regulation_slug", ""),
                            "regulation_title_ar": row.get("regulation_title_ar", ""),
                            "document_scope": row.get("document_scope", ""),
                            "related_regulations_json": json.dumps(row.get("related_regulations", []), ensure_ascii=False),
                            "article_index": row.get("article_index", ""),
                            "article_label": row.get("article_label", ""),
                            "article_label_raw": row.get("article_label_raw", ""),
                            "article_heading": row.get("article_heading", ""),
                            "article_type": row.get("article_type", ""),
                            "article_type_label_ar": row.get("article_type_label_ar", ""),
                            "legal_function_tags_json": json.dumps(row.get("legal_function_tags", []), ensure_ascii=False),
                            "legal_function_tags_ar_json": json.dumps(row.get("legal_function_tags_ar", []), ensure_ascii=False),
                            "topic_tags_json": json.dumps(row.get("topic_tags", []), ensure_ascii=False),
                            "topic_tags_ar_json": json.dumps(row.get("topic_tags_ar", []), ensure_ascii=False),
                            "contextual_header": row.get("contextual_header", ""),
                            "citation_short_ar": row.get("citation_short_ar", ""),
                            "official_source_url": row.get("official_source_url_primary", ""),
                            "version_status": row.get("version_status", ""),
                            "version_status_label_ar": row.get("version_status_label_ar", ""),
                            "effective_date_hijri": row.get("effective_date_hijri", ""),
                            "effective_date_gregorian": row.get("effective_date_gregorian", ""),
                            "historical_versions_available": row.get("historical_versions_available", False),
                            "recent_update_note": row.get("recent_update_note", ""),
                            "verbatim_text": row.get("text_verbatim", "").strip(),
                            "paragraph_ids_json": json.dumps(row.get("paragraph_ids", []), ensure_ascii=False),
                            "paragraph_indexes": ", ".join(
                                str(value) for value in (row.get("paragraph_indexes") or [])
                            ),
                            "paragraph_count": row.get("paragraph_count", 0),
                            "index_text": text,
                        },
                    )
                )
    except Exception as e:
        logger.error(f"❌ خطأ في قراءة المقاطع المنظمة: {e}")

    logger.info(f"🧱 تم تحميل {len(documents)} مقطع من الطبقة القانونية المنظمة")
    return documents


def load_text_files() -> list[Document]:
    """تحميل ملفات النص الحرة مثل ملاحظات المشرف والمحتوى اليدوي."""
    documents = []
    documents_dir = get_documents_dir()

    if not documents_dir.exists():
        logger.error(f"❌ مجلد المستندات غير موجود: {documents_dir}")
        return documents

    txt_files = list_text_files()
    if not txt_files:
        logger.warning("⚠️ لا توجد ملفات .txt في مجلد المعرفة")
        return documents

    for filepath in txt_files:
        try:
            content = filepath.read_text(encoding="utf-8")
            if content.strip():
                relative_source = filepath.relative_to(documents_dir)
                doc = Document(
                    page_content=content,
                    metadata={
                        "source": str(relative_source),
                        "file_path": str(filepath),
                        "source_kind": "text_file",
                        "citation_short_ar": str(relative_source),
                    }
                )
                documents.append(doc)
                logger.info(f"📄 تم تحميل: {relative_source} ({len(content)} حرف)")
        except Exception as e:
            logger.error(f"❌ خطأ في قراءة {filepath.name}: {e}")

    logger.info(f"📝 تم تحميل {len(documents)} ملف نصي حر")
    return documents


def load_knowledge_documents() -> list[Document]:
    """تحميل المعرفة من الطبقة المنظمة ومن ملفات النص اليدوية."""
    structured_docs = load_structured_chunk_documents()
    note_docs = load_text_files()
    all_docs = structured_docs + note_docs
    logger.info(f"📚 إجمالي المستندات/المقاطع المُحمّلة: {len(all_docs)}")
    return all_docs


def chunk_documents(documents: list[Document]) -> list[Document]:
    """تقطيع المستندات إلى مقاطع"""
    settings = get_settings()
    pre_chunked = [doc for doc in documents if doc.metadata.get("source_kind") == "structured_chunk"]
    free_text_docs = [doc for doc in documents if doc.metadata.get("source_kind") != "structured_chunk"]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ".", "،", "؟", "!", " "],
        length_function=len,
    )

    text_chunks = splitter.split_documents(free_text_docs) if free_text_docs else []
    chunks = pre_chunked + text_chunks
    logger.info(
        "✂️ المقاطع الجاهزة: %s | المقاطع المتولدة من النصوص الحرة: %s | الإجمالي: %s",
        len(pre_chunked),
        len(text_chunks),
        len(chunks),
    )
    return chunks


def store_in_chromadb(chunks: list[Document], client: Optional[chromadb.ClientAPI] = None):
    """تخزين المقاطع في ChromaDB"""
    settings = get_settings()

    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=get_embedding_api_key(),
        chunk_size=settings.embedding_batch_size,
    )

    # التأكد من وجود مجلد الحفظ قبل التخزين
    persist_dir = settings.chroma_persist_dir
    os.makedirs(persist_dir, exist_ok=True)

    logger.info("🧠 جارٍ إنشاء Embeddings وتخزينها...")

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        collection_name=settings.chroma_collection,
        persist_directory=persist_dir,
        client=client,
        collection_metadata=CHROMA_COLLECTION_METADATA,
    )

    count = vectorstore._collection.count()
    logger.info(f"✅ تم تخزين {count} مقطع في ChromaDB بنجاح!")
    return vectorstore


def rebuild_chromadb(chunks: list[Document]) -> Chroma:
    """إعادة بناء قاعدة ChromaDB من الصفر لتجنّب تكرار البيانات."""
    settings = get_settings()
    persist_dir = settings.chroma_persist_dir
    collection_name = settings.chroma_collection

    with cross_process_reindex_lock():
        os.makedirs(persist_dir, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_dir)

        try:
            client.delete_collection(collection_name)
            logger.info("🧹 تم حذف مجموعة ChromaDB السابقة")
        except Exception:
            logger.info("ℹ️ لا توجد مجموعة ChromaDB سابقة لحذفها")

        if not chunks:
            client.get_or_create_collection(collection_name, metadata=CHROMA_COLLECTION_METADATA)
            logger.info("🗂️ لا توجد مستندات حالياً، تم إنشاء قاعدة معرفة فارغة")
            return Chroma(
                collection_name=collection_name,
                embedding_function=OpenAIEmbeddings(
                    model=settings.embedding_model,
                    openai_api_key=get_embedding_api_key(),
                    chunk_size=settings.embedding_batch_size,
                ),
                persist_directory=persist_dir,
                collection_metadata=CHROMA_COLLECTION_METADATA,
            )

        return store_in_chromadb(chunks, client=client)


def main():
    """تشغيل عملية التجهيز الكاملة"""
    logger.info("=" * 60)
    logger.info("🚀 بدء تجهيز قاعدة المعرفة")
    logger.info("=" * 60)

    # 1. تحميل المستندات
    documents = load_knowledge_documents()
    if not documents:
        logger.error("❌ لا توجد مستندات لمعالجتها. ابنِ الطبقة المنظمة أو أضف ملفات نصية داخل documents/knowledge/")
        sys.exit(1)

    # 2. تقطيع
    chunks = chunk_documents(documents)

    # 3. إذا كانت الخدمة الحية تعمل، استخدم مسارها الآمن بدل حذف المجموعة من خارجها.
    if try_trigger_running_service_sync():
        logger.info("✅ أُنجزت إعادة الفهرسة عبر الخدمة الحية.")
        return

    # 3. تخزين
    rebuild_chromadb(chunks)

    logger.info("=" * 60)
    logger.info("🎉 تم تجهيز قاعدة المعرفة بنجاح!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
