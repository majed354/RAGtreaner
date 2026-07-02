"""معالجات بوت تيليغرام"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from telegram import Update, BotCommand, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from app.config import get_settings
from app.rag.engine import (
    ANSWER_MODE_CONSULTATION,
    ANSWER_MODE_LABELS,
    ANSWER_MODE_LEGAL_MEMO,
    get_engine,
)
from app.escalation import (
    should_escalate_by_keywords,
    escalate_to_admin,
    notify_user_escalated,
)
from app.gemini_file_search import get_gemini_file_search_service
from app.official_sync import get_official_sync_service

logger = logging.getLogger(__name__)
settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_knowledge_dir() -> Path:
    """حل مسار مجلد المعرفة النصية من الإعدادات."""
    configured = Path(settings.knowledge_dir)
    if configured.is_absolute():
        return configured
    return (PROJECT_ROOT / configured).resolve()


DOCUMENTS_DIR = resolve_knowledge_dir()
ADMIN_NOTES_DIR = DOCUMENTS_DIR / "admin_notes"
ADMIN_STATE_KEY = "admin_state"
ADMIN_STATE_AWAITING_KNOWLEDGE_TEXT = "awaiting_knowledge_text"
ADMIN_STATE_AWAITING_COMPARE_QUESTION = "awaiting_compare_question"
MAX_TELEGRAM_MESSAGE_LENGTH = 3500
USER_ANSWER_MODE_KEY = "answer_mode"
CONSULTATION_MODE_LABEL = ANSWER_MODE_LABELS[ANSWER_MODE_CONSULTATION]
LEGAL_MEMO_MODE_LABEL = ANSWER_MODE_LABELS[ANSWER_MODE_LEGAL_MEMO]
ADMIN_ADD_TRIGGER_PHRASES = {
    "أضف للمعرفة",
    "أضف إلى المعرفة",
    "إضافة للمعرفة",
    "إضافة إلى المعرفة",
    "أضف للقاعدة",
    "إضافة للقاعدة",
}
ADMIN_STATUS_TRIGGER_PHRASES = {
    "حالة المعرفة",
    "حالة القاعدة",
    "معلومات المعرفة",
    "معلومات القاعدة",
}
ADMIN_COMPARE_TRIGGER_PHRASES = {
    "قارن المسارين",
    "قارن بين المسارين",
    "قارن الفهرستين",
    "قارن بين الفهرستين",
    "مقارنة المسارين",
    "مقارنة الفهرستين",
}
ADMIN_CANCEL_TRIGGER_PHRASES = {"إلغاء", "الغاء", "إلغاء الإضافة", "الغاء الاضافة"}
ANSWER_MODE_SWITCH_PHRASES = {
    ANSWER_MODE_CONSULTATION: {
        CONSULTATION_MODE_LABEL,
        "الاستشارة القانونية",
        "استشارة قانونية مرجعية",
        "استشارة",
        "وضع الاستشارة",
    },
    ANSWER_MODE_LEGAL_MEMO: {
        LEGAL_MEMO_MODE_LABEL,
        "رأي قانوني",
        "مذكرة",
        "مذكرة قانونية",
        "مذكرة محاماة",
        "وضع المذكرة",
    },
}


def build_answer_mode_keyboard() -> ReplyKeyboardMarkup:
    """لوحة بسيطة لاختيار نمط الإجابة من داخل تيليغرام."""
    return ReplyKeyboardMarkup(
        [[CONSULTATION_MODE_LABEL, LEGAL_MEMO_MODE_LABEL]],
        resize_keyboard=True,
        input_field_placeholder="اختر نمط الإجابة أو اكتب سؤالك",
    )


def get_selected_answer_mode(context: ContextTypes.DEFAULT_TYPE) -> str:
    """قراءة نمط الإجابة الحالي مع افتراض وضع الاستشارة."""
    answer_mode = str(context.user_data.get(USER_ANSWER_MODE_KEY, "")).strip().lower()
    if answer_mode not in ANSWER_MODE_LABELS:
        answer_mode = ANSWER_MODE_CONSULTATION
        context.user_data[USER_ANSWER_MODE_KEY] = answer_mode
    return answer_mode


def detect_requested_answer_mode(message_text: str) -> Optional[str]:
    """التعرف على رسائل تبديل النمط من أزرار تيليغرام أو الصيغ القريبة منها."""
    normalized_text = " ".join(message_text.split())
    for answer_mode, phrases in ANSWER_MODE_SWITCH_PHRASES.items():
        if normalized_text in phrases:
            return answer_mode
    return None


def build_start_message(user_first_name: str, answer_mode: str) -> str:
    """رسالة البداية مع شرح وضعي الإجابة."""
    answer_mode_label = ANSWER_MODE_LABELS.get(answer_mode, CONSULTATION_MODE_LABEL)
    return (
        f"مرحباً {user_first_name}! 👋\n\n"
        "أنا مساعد الاستشارات القانونية المرجعي ⚖️\n\n"
        "أمامك وضعان للإجابة:\n"
        f"• <b>{CONSULTATION_MODE_LABEL}</b>: جواب مرجعي مباشر ومنظم.\n"
        f"• <b>{LEGAL_MEMO_MODE_LABEL}</b>: صياغة مهنية على نمط الرأي القانوني المنظم.\n\n"
        f"🧭 الوضع الحالي: <b>{answer_mode_label}</b>\n\n"
        "يمكنك تغيير الوضع من الأزرار في الأسفل، أو كتابة سؤالك مباشرة.\n\n"
        "ℹ️ الإجابات مرجعية مبنية على النصوص المتاحة، وليست بديلاً عن الاستشارة القانونية الخاصة."
    )


def build_answer_mode_confirmation(answer_mode: str) -> str:
    """رسالة تأكيد بعد تغيير نمط الإجابة."""
    if answer_mode == ANSWER_MODE_LEGAL_MEMO:
        return (
            f"تم التحويل إلى <b>{LEGAL_MEMO_MODE_LABEL}</b>.\n\n"
            "سأصوغ الجواب كرأي قانوني منظم يتضمن: السؤال، الجواب المختصر، الوقائع المؤثرة، المسائل القانونية، التحليل، والنتيجة العملية."
        )

    return (
        f"تم التحويل إلى <b>{CONSULTATION_MODE_LABEL}</b>.\n\n"
        "سأجيبك بصيغة مرجعية مباشرة ومركزة مع الإحالات النظامية المتاحة."
    )


def is_admin_user(update: Update) -> bool:
    """التحقق من أن المستخدم هو المشرف المعتمد."""
    user = update.effective_user
    return bool(user and user.id == settings.admin_chat_id)


def get_knowledge_status_text() -> str:
    """حالة قاعدة المعرفة بشكل مختصر للمشرف."""
    engine = get_engine()
    generation_status = engine.get_generation_status()
    txt_files_count = len(list(DOCUMENTS_DIR.rglob("*.txt")))
    official_sync_status = get_official_sync_service().get_status()
    gemini_file_search_status = get_gemini_file_search_service().get_status()
    last_official_sync = official_sync_status.get("last_run_finished_at") or "لم يجرِ بعد"
    return (
        "📊 <b>حالة قاعدة المعرفة</b>\n\n"
        f"📁 ملفات النص: <b>{txt_files_count}</b>\n"
        f"📚 المقاطع المفهرسة: <b>{engine.get_collection_count()}</b>\n"
        f"🧠 المولد النشط: <b>{generation_status['provider_label']}</b> — <b>{generation_status['model']}</b>\n"
        f"⏱️ فحص التغييرات كل: <b>{settings.documents_sync_interval_seconds}</b> ثوانٍ\n"
        f"🌐 الأنظمة الرسمية المتزامنة: <b>{official_sync_status['synced_entries']}</b> / <b>{official_sync_status['catalog_entries']}</b>\n"
        f"🗂️ Gemini File Search: <b>{gemini_file_search_status['file_count']}</b> ملف\n"
        f"🕓 آخر مزامنة رسمية: <b>{last_official_sync}</b>"
    )


async def send_knowledge_add_instructions(message):
    """إرسال تعليمات إضافة نص جديد للمشرف."""
    await message.reply_text(
        "📝 أرسل الآن النص الذي تريد إضافته إلى قاعدة المعرفة.\n\n"
        "الأسرع أن ترسله في رسالة واحدة بهذا الشكل:\n"
        "<code>أضف للمعرفة\n"
        "العنوان: اسم المعلومة\n\n"
        "النص الذي تريد حفظه...</code>\n\n"
        "أو اكتب أولاً <code>أضف للمعرفة</code> ثم أرسل النص في الرسالة التالية.\n"
        "للإلغاء أرسل: <code>إلغاء</code>",
        parse_mode="HTML",
    )


async def send_compare_instructions(message):
    """إرسال تعليمات مقارنة المسارين للمشرف."""
    await message.reply_text(
        "🔬 أرسل الآن السؤال الذي تريد مقارنته بين:\n"
        "1. RAG المحلي\n"
        "2. Gemini File Search\n\n"
        "يمكنك أيضًا الإرسال في رسالة واحدة بهذا الشكل:\n"
        "<code>قارن المسارين\n"
        "ما الضوابط النظامية المتعلقة بمسؤولية المدير في نظام الشركات؟</code>\n\n"
        "أو استخدم الأمر:\n"
        "<code>/compare سؤالك هنا</code>\n\n"
        "للإلغاء أرسل: <code>إلغاء</code>",
        parse_mode="HTML",
    )


def build_knowledge_filename(title: Optional[str]) -> str:
    """توليد اسم ملف مناسب للمعلومة الجديدة."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug_source = title or "knowledge"
    slug = re.sub(r"[^\w]+", "_", slug_source, flags=re.UNICODE).strip("_")[:40]
    if not slug:
        slug = "knowledge"
    return f"admin_{timestamp}_{slug}.txt"


def parse_knowledge_submission(raw_text: str) -> Tuple[Optional[str], str]:
    """استخراج عنوان اختياري ومحتوى النص."""
    text = raw_text.strip()
    if not text:
        return None, ""

    lines = text.splitlines()
    title = None
    content = text

    match = re.match(r"^العنوان\s*:\s*(.+)$", lines[0].strip())
    if match:
        title = match.group(1).strip()
        body_lines = lines[1:]

        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)

        if body_lines and re.match(r"^(المحتوى|النص)\s*:?\s*$", body_lines[0].strip()):
            body_lines.pop(0)

        content = "\n".join(body_lines).strip()

    return title, content


def save_knowledge_text(raw_text: str) -> Path:
    """حفظ النص الجديد داخل documents/knowledge/admin_notes."""
    title, content = parse_knowledge_submission(raw_text)
    if not content:
        raise ValueError("أرسل نصاً فعلياً ليتم حفظه في قاعدة المعرفة.")

    ADMIN_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    filename = build_knowledge_filename(title)
    filepath = ADMIN_NOTES_DIR / filename
    file_content = f"{title}\n\n{content}" if title else content
    filepath.write_text(file_content.strip() + "\n", encoding="utf-8")
    return filepath


def extract_direct_knowledge_text(raw_text: str) -> Optional[str]:
    """التقاط نص مضاف مباشرة داخل نفس الرسالة."""
    lines = raw_text.strip().splitlines()
    if not lines:
        return None

    first_line = lines[0].strip()
    if first_line in ADMIN_ADD_TRIGGER_PHRASES:
        body = "\n".join(lines[1:]).strip()
        return body or None

    for prefix in (
        "أضف للمعرفة:",
        "أضف إلى المعرفة:",
        "إضافة للمعرفة:",
        "إضافة إلى المعرفة:",
        "أضف للقاعدة:",
        "إضافة للقاعدة:",
    ):
        if raw_text.startswith(prefix):
            body = raw_text[len(prefix):].strip()
            return body or None

    return None


def extract_direct_compare_question(raw_text: str) -> Optional[str]:
    """التقاط سؤال المقارنة إذا أرسله المشرف في الرسالة نفسها."""
    lines = raw_text.strip().splitlines()
    if not lines:
        return None

    first_line = lines[0].strip()
    if first_line in ADMIN_COMPARE_TRIGGER_PHRASES:
        body = "\n".join(lines[1:]).strip()
        return body or None

    for prefix in (
        "قارن:",
        "قارن المسارين:",
        "قارن بين المسارين:",
        "قارن الفهرستين:",
        "قارن بين الفهرستين:",
        "مقارنة المسارين:",
        "مقارنة الفهرستين:",
    ):
        if raw_text.startswith(prefix):
            body = raw_text[len(prefix):].strip()
            return body or None

    return None


def split_for_telegram(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    """تقسيم الرسائل الطويلة إلى أجزاء مناسبة لتيليغرام."""
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return [chunk for chunk in chunks if chunk]


async def send_chunked_text(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: Optional[int] = None,
    parse_mode: Optional[str] = None,
):
    """إرسال نص طويل على عدة رسائل عند الحاجة."""
    for index, chunk in enumerate(split_for_telegram(text), start=1):
        kwargs = {"chat_id": chat_id, "text": chunk}
        if index == 1 and reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        await context.bot.send_message(**kwargs)


def summarize_file_search_titles(sources: list[dict]) -> str:
    """تلخيص أسماء الملفات المسترجعة من Gemini File Search."""
    titles = []
    for item in sources:
        title = str(item.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return "لا توجد أسماء مصادر ظاهرة."
    return "، ".join(titles[:5])


def build_compare_messages(question: str, local_result, file_search_result, generation_status: dict, file_search_status: dict) -> list[str]:
    """بناء رسائل المقارنة الجاهزة للإرسال في تيليغرام."""
    local_best = max(local_result.similarity_scores) if local_result.similarity_scores else 0.0
    local_answer = local_result.answer or "لم يرجع المسار المحلي جوابًا مباشرًا."
    file_answer = file_search_result.answer or "لم يرجع مسار Gemini File Search جوابًا مباشرًا."

    header = (
        "🔬 مقارنة المسارين\n\n"
        f"السؤال:\n{question}"
    )
    local_block = (
        "1. RAG المحلي\n"
        f"المولد: {generation_status['provider_label']} / {generation_status['model']}\n"
        f"الثقة: {local_result.confidence}\n"
        f"عدد المقاطع المسترجعة: {len(local_result.sources)}\n"
        f"أعلى تشابه: {local_best:.2f}\n\n"
        f"{local_answer}"
    )
    file_block = (
        "2. Gemini File Search\n"
        f"النموذج: {file_search_status['gemini_model']}\n"
        f"الثقة: {file_search_result.confidence}\n"
        f"عدد المراجع المسترجعة: {len(file_search_result.sources)}\n"
        f"أبرز الملفات: {summarize_file_search_titles(file_search_result.sources)}\n\n"
        f"{file_answer}"
    )

    messages = [header, local_block, file_block]
    if file_search_result.error:
        messages.append(f"ملاحظة تقنية من Gemini File Search:\n{file_search_result.error}")
    return messages


async def handle_admin_knowledge_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: Optional[str] = None,
):
    """استقبال النص الجديد من المشرف وحفظه في قاعدة المعرفة."""
    message_text = (raw_text if raw_text is not None else update.message.text).strip()
    normalized = " ".join(message_text.split())

    if normalized in ADMIN_CANCEL_TRIGGER_PHRASES | {"cancel"}:
        context.user_data.pop(ADMIN_STATE_KEY, None)
        await update.message.reply_text("تم إلغاء إضافة النص الجديد.")
        return

    try:
        filepath = save_knowledge_text(message_text)
        engine = get_engine()
        await engine.sync_if_documents_changed(force=True)
        count = engine.get_collection_count()
    except ValueError as e:
        await update.message.reply_text(
            f"⚠️ {e}\n\n"
            "يمكنك إرسال النص من جديد، أو إرسال كلمة <code>إلغاء</code>.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.error(f"❌ فشل إضافة النص الجديد لقاعدة المعرفة: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ أثناء حفظ النص أو مزامنته.\n"
            "حاول مرة أخرى أو أرسل <code>إلغاء</code>.",
            parse_mode="HTML",
        )
        return

    context.user_data.pop(ADMIN_STATE_KEY, None)
    await update.message.reply_text(
        "✅ تم حفظ النص وإضافته إلى قاعدة المعرفة.\n\n"
        f"📄 الملف: <code>{filepath.name}</code>\n"
        f"📚 المقاطع الحالية: <b>{count}</b>\n\n"
        "يمكنك الآن سؤال البوت عنه مباشرة.",
        parse_mode="HTML",
    )


async def handle_admin_knowledge_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """معالجة أوامر إضافة المعرفة المكتوبة نصياً من المشرف."""
    normalized_text = " ".join(message_text.split())
    direct_knowledge_text = extract_direct_knowledge_text(message_text)

    if direct_knowledge_text is not None:
        await handle_admin_knowledge_submission(update, context, direct_knowledge_text)
        return True

    if normalized_text in ADMIN_ADD_TRIGGER_PHRASES:
        context.user_data[ADMIN_STATE_KEY] = ADMIN_STATE_AWAITING_KNOWLEDGE_TEXT
        await send_knowledge_add_instructions(update.message)
        return True

    if normalized_text in ADMIN_STATUS_TRIGGER_PHRASES:
        await update.message.reply_text(get_knowledge_status_text(), parse_mode="HTML")
        return True

    if context.user_data.get(ADMIN_STATE_KEY) == ADMIN_STATE_AWAITING_KNOWLEDGE_TEXT:
        await handle_admin_knowledge_submission(update, context)
        return True

    return False


async def run_admin_compare(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str):
    """تنفيذ مقارنة المسارين للمشرف وإرسال النتيجة إلى تيليغرام."""
    context.user_data.pop(ADMIN_STATE_KEY, None)

    await update.message.reply_text("🔎 جارٍ مقارنة المسارين على السؤال نفسه...")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    engine = get_engine()
    local_result, file_search_result = await asyncio.gather(
        engine.query(question),
        get_gemini_file_search_service().query(question),
    )
    generation_status = engine.get_generation_status()
    file_search_status = get_gemini_file_search_service().get_status()

    for text in build_compare_messages(
        question,
        local_result,
        file_search_result,
        generation_status,
        file_search_status,
    ):
        await send_chunked_text(
            context,
            update.effective_chat.id,
            text,
            reply_to_message_id=update.message.message_id,
        )


async def handle_admin_compare_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """معالجة أوامر المقارنة النصية للمشرف."""
    normalized_text = " ".join(message_text.split())
    direct_compare_question = extract_direct_compare_question(message_text)
    current_state = context.user_data.get(ADMIN_STATE_KEY)

    if current_state == ADMIN_STATE_AWAITING_COMPARE_QUESTION:
        if normalized_text in ADMIN_CANCEL_TRIGGER_PHRASES | {"cancel"}:
            context.user_data.pop(ADMIN_STATE_KEY, None)
            await update.message.reply_text("تم إلغاء وضع المقارنة.")
            return True

        await run_admin_compare(update, context, message_text.strip())
        return True

    if direct_compare_question is not None:
        await run_admin_compare(update, context, direct_compare_question)
        return True

    if normalized_text in ADMIN_COMPARE_TRIGGER_PHRASES:
        context.user_data[ADMIN_STATE_KEY] = ADMIN_STATE_AWAITING_COMPARE_QUESTION
        await send_compare_instructions(update.message)
        return True

    return False


# ══════════════════════════════════════
#  أوامر البوت
# ══════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
    user = update.effective_user
    selected_answer_mode = get_selected_answer_mode(context)
    await update.message.reply_text(
        build_start_message(user.first_name, selected_answer_mode),
        parse_mode="HTML",
        reply_markup=build_answer_mode_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /help"""
    help_text = (
        "📋 <b>الأوامر المتاحة:</b>\n\n"
        "/start — بدء المحادثة\n"
        "/help — عرض المساعدة\n"
        "/human — طلب التحدث مع المستشار\n"
        "/status — حالة البوت\n"
        "\n🧭 <b>أوضاع الإجابة:</b>\n"
        f"• {CONSULTATION_MODE_LABEL}\n"
        f"• {LEGAL_MEMO_MODE_LABEL}\n"
    )

    if is_admin_user(update):
        help_text += (
            "/compare — مقارنة RAG و Gemini File Search\n"
            "/reply — الرد على مستخدم\n\n"
            "🛠️ أوامر نصية للمشرف:\n"
            "• أضف للمعرفة\n"
            "• حالة المعرفة\n"
            "• قارن المسارين\n"
        )

    help_text += "\n💡 أو اكتب سؤالك مباشرة!"
    await update.message.reply_text(help_text, parse_mode="HTML")


async def cmd_human(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /human — طلب تواصل مع المستشار"""
    user = update.effective_user
    await escalate_to_admin(
        bot=context.bot,
        user_id=user.id,
        user_name=user.username,
        user_full_name=user.full_name,
        question="(طلب تواصل مباشر مع المستشار)",
        reason="طلب صريح من المستخدم",
    )
    await notify_user_escalated(context.bot, update.effective_chat.id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /status — حالة البوت"""
    engine = get_engine()
    generation_status = engine.get_generation_status()
    gemini_file_search_status = get_gemini_file_search_service().get_status()
    count = engine.get_collection_count()
    await update.message.reply_text(
        "🤖 <b>حالة البوت:</b>\n\n"
        f"📚 المقاطع في قاعدة المعرفة: <b>{count}</b>\n"
        f"🧠 مزود التوليد: <b>{generation_status['provider_label']}</b>\n"
        f"🧠 نموذج التوليد: <b>{generation_status['model']}</b>\n"
        f"📐 نموذج الـ Embedding: <b>{settings.embedding_model}</b>\n"
        f"🗂️ ملفات Gemini File Search: <b>{gemini_file_search_status['file_count']}</b>\n"
        f"✅ البوت يعمل بشكل طبيعي",
        parse_mode="HTML",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /compare — مقارنة المسارين للمشرف فقط."""
    if not is_admin_user(update):
        await update.message.reply_text("⛔ هذا الأمر متاح للمشرف فقط.")
        return

    question = " ".join(context.args).strip()
    if question:
        await run_admin_compare(update, context, question)
        return

    context.user_data[ADMIN_STATE_KEY] = ADMIN_STATE_AWAITING_COMPARE_QUESTION
    await send_compare_instructions(update.message)
# ══════════════════════════════════════
#  أمر الرد من المشرف
# ══════════════════════════════════════

async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /reply — رد المشرف على مستخدم"""
    if update.effective_user.id != settings.admin_chat_id:
        return  # فقط المشرف يمكنه استخدام هذا الأمر

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ الاستخدام:\n<code>/reply USER_ID رسالتك</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_user_id = int(context.args[0])
        reply_text = " ".join(context.args[1:])
    except ValueError:
        await update.message.reply_text("❌ معرّف المستخدم غير صحيح")
        return

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"💬 <b>رد من المستشار:</b>\n\n{reply_text}",
            parse_mode="HTML",
        )
        await update.message.reply_text("✅ تم إرسال الرد بنجاح")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")


# ══════════════════════════════════════
#  معالجة الرسائل النصية (السؤال الرئيسي)
# ══════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أي رسالة نصية — القلب النابض للبوت"""
    user = update.effective_user
    message_text = update.message.text.strip()

    if not message_text:
        return

    if is_admin_user(update):
        if await handle_admin_compare_flow(update, context, message_text):
            return
        if await handle_admin_knowledge_flow(update, context, message_text):
            return

    requested_answer_mode = detect_requested_answer_mode(message_text)
    if requested_answer_mode:
        context.user_data[USER_ANSWER_MODE_KEY] = requested_answer_mode
        await update.message.reply_text(
            build_answer_mode_confirmation(requested_answer_mode),
            parse_mode="HTML",
            reply_markup=build_answer_mode_keyboard(),
        )
        return

    logger.info(f"📩 سؤال من {user.full_name} ({user.id}): {message_text[:80]}")

    # --- 1. فحص التصعيد بالكلمات المفتاحية ---
    if should_escalate_by_keywords(message_text):
        await escalate_to_admin(
            bot=context.bot,
            user_id=user.id,
            user_name=user.username,
            user_full_name=user.full_name,
            question=message_text,
            reason="كلمة مفتاحية للتصعيد",
        )
        await notify_user_escalated(context.bot, update.effective_chat.id)
        return

    # --- 2. عرض مؤشر الكتابة ---
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    # --- 3. تشغيل RAG ---
    engine = get_engine()
    answer_mode = get_selected_answer_mode(context)
    result = await engine.query(message_text, answer_mode=answer_mode)

    # --- 4. تقييم النتيجة ---
    if result.needs_escalation:
        # إرسال ما وُجد (إن وُجد) ثم تصعيد
        if result.answer and result.confidence == "medium":
            await send_chunked_text(
                context,
                update.effective_chat.id,
                f"{result.answer}\n\n"
                "⚠️ <i>هذه الإجابة قد تكون غير مكتملة. "
                "سأحوّل سؤالك للمختص للتأكد.</i>",
                reply_to_message_id=update.message.message_id,
                parse_mode="HTML",
            )

        await escalate_to_admin(
            bot=context.bot,
            user_id=user.id,
            user_name=user.username,
            user_full_name=user.full_name,
            question=message_text,
            context=result.answer if result.answer else "",
            reason=f"ثقة: {result.confidence} | أعلى تشابه: {max(result.similarity_scores) if result.similarity_scores else 0:.2f}",
        )
        await notify_user_escalated(context.bot, update.effective_chat.id)
    else:
        # إجابة واثقة — إرسال مباشر
        await send_chunked_text(
            context,
            update.effective_chat.id,
            result.answer,
            reply_to_message_id=update.message.message_id,
        )

    logger.info(
        f"✅ رد على {user.full_name} | ثقة: {result.confidence} | "
        f"تصعيد: {result.needs_escalation}"
    )


# ══════════════════════════════════════
#  Callback للأزرار
# ══════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة ضغطات الأزرار"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("resolved:"):
        await query.edit_message_text(
            query.message.text + "\n\n✅ <b>تم الرد والإغلاق</b>",
            parse_mode="HTML",
        )
    elif data.startswith("note:"):
        await query.edit_message_text(
            query.message.text + "\n\n📌 <b>تم التعليق — بانتظار المتابعة</b>",
            parse_mode="HTML",
        )


# ══════════════════════════════════════
#  بناء التطبيق
# ══════════════════════════════════════

def create_bot_app() -> Application:
    """إنشاء تطبيق البوت مع جميع المعالجات"""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # أوامر
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("human", cmd_human))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("reply", cmd_reply))

    # أزرار
    app.add_handler(CallbackQueryHandler(handle_callback))

    # رسائل نصية (آخر شيء — catch-all)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


async def set_bot_commands(app: Application):
    """تسجيل قائمة الأوامر في تيليغرام"""
    commands = [
        BotCommand("start", "بدء المحادثة"),
        BotCommand("help", "عرض المساعدة"),
        BotCommand("human", "التحدث مع المستشار"),
        BotCommand("status", "حالة البوت"),
        BotCommand("compare", "مقارنة المسارين للمشرف"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("📋 تم تسجيل أوامر البوت")
