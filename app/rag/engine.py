"""محرك RAG القانوني المحلي.

هذه نسخة تشغيلية محافظة تعيد واجهات المحرك التي تعتمد عليها الخدمة، وتبني
الاسترجاع من Chroma والملفات المنظمة الموجودة على القرص. صممت خصيصا لمسار
الجولات الجامعة: استدعاء واسع للحزمة النظامية أولا، ثم ترتيب المواد.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from langchain_openai import OpenAIEmbeddings

from app.config import get_settings
from app.runtime_settings import get_runtime_settings_store


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROUTER_MODEL_CANDIDATES = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_tfidf_ovr_generalization_table.joblib",
    PROJECT_ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_tfidf_ovr_rare_mixup_gemma_gap.joblib",
    PROJECT_ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_tfidf_ovr_rare_mixup.joblib",
    PROJECT_ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_tfidf_ovr_baseline.joblib",
)
PACKAGE_ROUTER_RETRIEVAL_TABLE_PATH = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "package_router"
    / "saudi_legal_package_router_v1"
    / "package_router_retrieval_table_v1.joblib"
)
ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "article_autopilot"
    / "article_autopilot_article_support_table_v1.joblib"
)
HELDOUT_AXIS_PACKER_PATH = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "article_autopilot"
    / "heldout_axis_packer_v1.json"
)
PACKAGE_ROUTER_TOP_K = 48
PACKAGE_ROUTER_SEGMENT_TOP_K = 24
PACKAGE_ROUTER_TABLE_TOP_ROWS = 28
PACKAGE_ROUTER_CONTEXT_SEED_LIMIT = 32
ARTICLE_SUPPORT_TOP_ROWS = 8
ARTICLE_SUPPORT_MIN_SCORE = 0.45
ARTICLE_SUPPORT_MAX_ARTICLE_PAIRS = 24
STRUCTURED_BY_REGULATION_DIR = PROJECT_ROOT / "data" / "structured" / "by_regulation"
PACKAGE_ROUTER_SEGMENT_SPLIT_RE = re.compile(
    r"(?:[.!؟?؛;\n]+|،\s*|(?:\s+ثم\s+)|(?:\s+كما\s+)|"
    r"(?:\s+وفي الوقت نفسه\s+)|(?:\s+وفي المقابل\s+)|(?:\s+في المقابل\s+)|"
    r"(?:\s+بينما\s+)|(?:\s+إضافة إلى\s+)|(?:\s+بالإضافة إلى\s+))"
)
ARTICLE_MENTION_RE = re.compile(r"(?:الماده|ماده|المواد|مواد)\s+(?:رقم\s+)?(\d{1,3})")
ARTICLE_WORD_NUMBER_PATTERNS: tuple[tuple[str, int], ...] = (
    ("الاول", 1),
    ("الاولي", 1),
    ("الاولى", 1),
    ("الثاني", 2),
    ("الثانيه", 2),
    ("الثالث", 3),
    ("الثالثه", 3),
    ("الرابع", 4),
    ("الرابعه", 4),
    ("الخامس", 5),
    ("الخامسه", 5),
    ("السادس", 6),
    ("السادسه", 6),
    ("السابع", 7),
    ("السابعه", 7),
    ("الثامن", 8),
    ("الثامنه", 8),
    ("التاسع", 9),
    ("التاسعه", 9),
    ("العاشر", 10),
    ("العاشره", 10),
    ("الحادي عشر", 11),
    ("الحاديه عشر", 11),
    ("الحاديه عشره", 11),
    ("الثاني عشر", 12),
    ("الثانيه عشر", 12),
    ("الثانيه عشره", 12),
    ("الثالث عشر", 13),
    ("الثالثه عشر", 13),
    ("الثالثه عشره", 13),
    ("الرابع عشر", 14),
    ("الرابعه عشر", 14),
    ("الرابعه عشره", 14),
    ("الخامس عشر", 15),
    ("الخامسه عشر", 15),
    ("الخامسه عشره", 15),
    ("السادس عشر", 16),
    ("السادسه عشر", 16),
    ("السادسه عشره", 16),
    ("السابع عشر", 17),
    ("السابعه عشر", 17),
    ("السابعه عشره", 17),
    ("الثامن عشر", 18),
    ("الثامنه عشر", 18),
    ("الثامنه عشره", 18),
    ("التاسع عشر", 19),
    ("التاسعه عشر", 19),
    ("التاسعه عشره", 19),
    ("العشرون", 20),
    ("العشرين", 20),
    ("الثلاثون", 30),
    ("الثلاثين", 30),
    ("الاربعون", 40),
    ("الاربعين", 40),
    ("الخمسون", 50),
    ("الخمسين", 50),
    ("الستون", 60),
    ("الستين", 60),
    ("السبعون", 70),
    ("السبعين", 70),
    ("الثمانون", 80),
    ("الثمانين", 80),
    ("التسعون", 90),
    ("التسعين", 90),
)

ANSWER_MODE_CONSULTATION = "consultation"
ANSWER_MODE_LEGAL_MEMO = "legal_memo"
ANSWER_MODE_LABELS = {
    ANSWER_MODE_CONSULTATION: "استشارة قانونية",
    ANSWER_MODE_LEGAL_MEMO: "مذكرة قانونية",
    "benchmark": "اختبار Benchmark",
    "legal_analysis": "تحليل قانوني",
}

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
DIACRITICS_RE = re.compile(r"[\u064b-\u065f\u0670]")
TOKEN_RE = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
FLEXIBLE_SUFFIX_RE = r"[\w\u0600-\u06ff]*"


@dataclass
class RAGResult:
    answer: str
    confidence: str
    sources: list[str]
    needs_escalation: bool
    diagnostics: dict[str, Any]


class _CollectionVectorStore:
    def __init__(self, collection: Any):
        self._collection = collection


def _resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _normalize(text: str) -> str:
    value = (text or "").translate(ARABIC_DIGITS)
    value = DIACRITICS_RE.sub("", value)
    value = value.replace("ـ", "")
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value.lower())
    return " ".join(value.split())


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(_normalize(text)) if len(token) > 1]


def _extract_article_number_mentions(text: str) -> list[int]:
    normalized = _normalize(text)
    numbers: set[int] = set()
    for match in ARTICLE_MENTION_RE.finditer(normalized):
        try:
            value = int(match.group(1))
        except Exception:
            continue
        if 0 < value <= 500:
            numbers.add(value)

    article_prefixes = ("الماده", "ماده", "المواد", "مواد")
    for phrase, value in ARTICLE_WORD_NUMBER_PATTERNS:
        if value < 10 and any(
            f"{prefix} {phrase} عشر" in normalized or f"{prefix} {phrase} عشره" in normalized
            for prefix in article_prefixes
        ):
            continue
        if any(f"{prefix} {phrase}" in normalized for prefix in article_prefixes):
            numbers.add(value)
    return sorted(numbers)


def _router_query_segments(question: str, max_segments: int = 12) -> list[str]:
    """Split a compound legal fact pattern into retrieval-routing atoms."""
    segments: list[str] = []
    for raw_part in PACKAGE_ROUTER_SEGMENT_SPLIT_RE.split(question or ""):
        segment = " ".join(raw_part.split()).strip(" :،؛.")
        if len(segment) < 18 or segment in segments:
            continue
        segments.append(segment)
        if len(segments) >= max_segments:
            break
    return segments


def _pattern_matches(pattern: str, normalized_question: str) -> bool:
    """Match normalized text with lightweight legal wildcards.

    The `*` suffix is intentionally narrow: `ديون*` matches `ديون`,
    `ديونها`, `ديونه`, and similar attached pronouns without becoming an
    unrestricted phrase wildcard.
    """
    if not pattern:
        return False
    if "*" not in pattern:
        return _normalize(pattern) in normalized_question

    value = (pattern or "").translate(ARABIC_DIGITS)
    value = DIACRITICS_RE.sub("", value)
    value = value.replace("ـ", "")
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = value.replace("ؤ", "و").replace("ئ", "ي")
    value = re.sub(r"[^\w\u0600-\u06ff*]+", " ", value.lower())
    normalized_pattern = " ".join(value.split())
    regex = re.escape(normalized_pattern).replace(r"\*", FLEXIBLE_SUFFIX_RE)
    if not regex:
        return False
    return re.search(regex, normalized_question) is not None


def _has_any(normalized_question: str, patterns: tuple[str, ...] | list[str] | set[str]) -> bool:
    return any(_pattern_matches(pattern, normalized_question) for pattern in patterns if pattern)


def _strip_regulation_prefixes(title: str) -> str:
    value = _normalize(title)
    for prefix in (
        "اللائحه التنفيذيه لنظام",
        "اللائحه التنفيذيه",
        "لائحه تنظيم",
        "لائحه",
        "نظام",
        "القانون النظام الموحد",
        "القانون الموحد",
        "القانون",
    ):
        normalized_prefix = _normalize(prefix)
        if value.startswith(normalized_prefix + " "):
            return value[len(normalized_prefix) :].strip()
    return value


def _dedupe(values: list[Any] | tuple[Any, ...] | set[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        marker = repr(value)                                                                                        
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def _parse_confidence(answer_text: str) -> tuple[str, str]:
    """استخراج مؤشر الثقة من نص خارجي عند توفره."""
    text = answer_text or ""
    confidence = "medium"
    match = re.search(r"(?:الثقة|confidence)\s*[:：]\s*(high|medium|low|عالية|متوسطة|منخفضة)", text, re.IGNORECASE)
    if match:
        raw = match.group(1).strip().lower()
        confidence = {
            "high": "high",
            "medium": "medium",
            "low": "low",
            "عالية": "high",
            "متوسطة": "medium",
            "منخفضة": "low",
        }.get(raw, "medium")
        text = text[: match.start()] + text[match.end() :]
    return text.strip(), confidence


REGULATION_TITLE_OVERRIDES = {
    "e-commerce-law": "نظام التجارة الإلكترونية",
    "ecommerce-implementing-regulation": "اللائحة التنفيذية لنظام التجارة الإلكترونية",
    "commercial-fraud-law": "نظام مكافحة الغش التجاري",
    "personal-data-protection-law": "نظام حماية البيانات الشخصية",
    "pdpl-implementing-regulation": "اللائحة التنفيذية لنظام حماية البيانات الشخصية",
    "pdpl-transfer-regulation": "لائحة نقل البيانات الشخصية إلى خارج المملكة",
    "anti-cybercrime-law": "نظام مكافحة جرائم المعلوماتية",
    "government-tenders-and-procurement-law": "نظام المنافسات و المشتريات الحكومية",
    "government-procurement-implementing-regulation": "اللائحة التنفيذية لنظام المنافسات والمشتريات الحكومية",
    "procurement-conflict-of-interest-regulation": "لائحة تنظيم تعارض المصالح في تطبيق نظام المنافسات والمشتريات الحكومية ولائحته التنفيذية",
    "procurement-conduct-ethics-regulation": "لائحة سلوكيات وأخلاقيات القائمين على تطبيق نظام المنافسات والمشتريات الحكومية",
    "nzam-almnafsh": "نظام المنافسة",
    "companies-law": "نظام الشركات",
    "companies-implementing-regulation": "اللائحة التنفيذية لنظام الشركات",
    "nzam-alswq-almalyh": "نظام السوق المالية",
    "cma-corporate-governance-regulations": "لائحة حوكمة الشركات",
    "cma-continuing-obligations-rules": "قواعد طرح الأوراق المالية والالتزامات المستمرة",
    "cma-securities-offering-rules": "قواعد طرح الأوراق المالية والالتزامات المستمرة",
    "civil-transactions-law": "نظام المعاملات المدنية",
    "law-of-evidence": "نظام الإثبات",
    "nzam-almhakm-altjaryh": "نظام المحاكم التجارية",
    "nzam-althkym": "نظام التحكيم",
    "nzam-ttbyq-kwd-albnaa-alsawdy": "نظام تطبيق كود البناء السعودي",
    "nzam-tsnyf-almqawlyn": "نظام تصنيف المقاولين",
    "nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth": "نظام بيع وتأجير مشروعات عقارية على الخارطة",
    "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha": "نظام ملكية الوحدات العقارية وفرزها وإدارتها",
    "real-estate-brokerage-law": "نظام الوساطة العقارية",
    "nzam-altsjyl-alayny-llaqar": "نظام التسجيل العيني للعقار",
    "nzam-almwasfat-waljwdh": "نظام المواصفات والجودة",
    "nzam-aliflas": "نظام الإفلاس",
    "bankruptcy-implementing-regulation": "اللائحة التنفيذية لنظام الإفلاس",
    "labor-law": "نظام العمل",
    "labor-implementing-regulation": "اللائحة التنفيذية لنظام العمل",
    "labor-violations-penalties-table": "جدول المخالفات والعقوبات لنظام العمل",
    "wage-protection-rules": "قواعد/برنامج حماية الأجور",
    "labor-contract-documentation-rules": "قواعد توثيق عقود العمل",
    "nzam-altamyn-dd-altatl-an-alaml": "نظام التأمين ضد التعطل عن العمل",
    "nzam-mkafhh-jrymh-althrsh": "نظام مكافحة جريمة التحرش",
    "workplace-behavioral-misconduct-controls": "ضوابط الحماية من التعديات السلوكية في بيئة العمل",
    "nzam-drybh-alqymh-almdafh": "نظام ضريبة القيمة المضافة",
    "zatca-vat-implementing-regulation": "اللائحة التنفيذية لنظام ضريبة القيمة المضافة",
    "zatca-e-invoicing-bylaw": "لائحة الفوترة الإلكترونية",
    "zatca-e-invoicing-technical-controls": "الضوابط والمتطلبات والمواصفات الفنية والقواعد الإجرائية لتنفيذ أحكام لائحة الفوترة الإلكترونية",
    "electronic-transactions-law": "نظام التعاملات الإلكترونية",
    "execution-law": "نظام التنفيذ",
    "execution-implementing-regulation": "اللائحة التنفيذية لنظام التنفيذ",
    "nzam-altkalyf-alqdayyh": "نظام التكاليف القضائية",
    "law-of-sharia-procedure": "نظام المرافعات الشرعية",
    "nzam-almrafaat-amam-dywan-almzalm": "نظام المرافعات أمام ديوان المظالم",
    "nzam-dywan-almzalm": "نظام ديوان المظالم",
    "nzam-altnfydh-amam-dywan-almzalm": "نظام التنفيذ أمام ديوان المظالم",
    "nzam-alajhzh-walmstlzmat-altbyh": "نظام الأجهزة والمستلزمات الطبية",
    "nzam-altamynat-alajtmaayh": "نظام التأمينات الاجتماعية",
    "nzam-alkhdmh-almdnyh": "نظام الخدمة المدنية",
    "nzam-alandbat-alwzyfy": "نظام الانضباط الوظيفي",
    "nzam-qanwn-aljmark-almwhd-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh": "نظام الجمارك الموحد لدول مجلس التعاون",
    "nzam-jbayh-alzkah": "نظام جباية الزكاة",
    "nzam-mkafhh-altstr": "نظام مكافحة التستر",
    "product-safety-law": "نظام سلامة المنتجات",
    "nzam-alayjar-altmwyly": "نظام الإيجار التمويلي",
    "nzam-dman-alhqwq-balamwal-almnqwlh": "نظام ضمان الحقوق بالأموال المنقولة",
    "nzam-altmwyl-alaqary": "نظام التمويل العقاري",
    "nzam-alrhn-alaqary-almsjl": "نظام الرهن العقاري المسجل",
    "nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh": "نظام مكافحة الاحتيال المالي وخيانة الأمانة",
    "anti-money-laundering-law": "نظام مكافحة غسل الأموال",
    "nzam-maaljh-almnshat-almalyh-almhmh": "نظام معالجة المنشآت المالية المهمة",
    "nzam-mraqbh-albnwk": "نظام مراقبة البنوك",
    "nzam-albnk-almrkzy-alsawdy": "نظام البنك المركزي السعودي",
    "nzam-almdfwaat-wkhdmatha": "نظام المدفوعات وخدماتها",
    "nzam-mraqbh-shrkat-altmwyl": "نظام مراقبة شركات التمويل",
    "nzam-mraqbh-shrkat-altamyn-altaawny": "نظام مراقبة شركات التأمين التعاوني",
    "nzam-aldman-alshy-altaawny": "نظام الضمان الصحي التعاوني",
    "nzam-almalwmat-alaytmanyh": "نظام المعلومات الائتمانية",
    "copyright-law": "نظام حماية حقوق المؤلف",
    "nzam-alalamat-altjaryh": "نظام العلامات التجارية",
    "nzam-alasmaa-altjaryh": "نظام الأسماء التجارية",
    "nzam-alsjl-altjary": "نظام السجل التجاري",
    "nzam-alamtyaz-altjary": "نظام الامتياز التجاري",
    "nzam-alwkalat-altjaryh": "نظام الوكالات التجارية",
    "nzam-alawraq-altjaryh": "نظام الأوراق التجارية",
    "nzam-drybh-altsrfat-alaqaryh": "نظام ضريبة التصرفات العقارية",
    "qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh": "قانون نظام العلامات التجارية لدول مجلس التعاون لدول الخليج العربية",
    "nzam-braaat-alakhtraa-waltsmymat-altkhtytyh-lldarat-almtkamlh-walasnaf-alnbatyh-walnmadhj-alsnaa": "نظام براءات الاختراع والتصميمات التخطيطية والنماذج الصناعية",
    "nzam-alialam-almryy-walmsmwa": "نظام الإعلام المرئي والمسموع",
    "nzam-almtbwaat-walnshr": "نظام المطبوعات والنشر",
    "communications-and-information-technology-law": "نظام الاتصالات وتقنية المعلومات",
    "nzam-alnql-alaam-ala-altrq-balmmlkh-alarbyh-alsawdyh": "نظام النقل العام على الطرق بالمملكة العربية السعودية",
    "nzam-almrwr": "نظام المرور",
    "alnzam-alshy": "النظام الصحي",
    "nzam-almwssat-alshyh-alkhash": "نظام المؤسسات الصحية الخاصة",
    "nzam-mzawlh-almhn-alshyh": "نظام مزاولة المهن الصحية",
    "nzam-alhyyh-alaamh-llghdhaa-waldwaa": "نظام الهيئة العامة للغذاء والدواء",
    "nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh": "نظام المنشآت والمستحضرات الصيدلانية والعشبية",
    "nzam-alghdhaa": "نظام الغذاء",
    "nzam-mntjat-altjmyl": "نظام منتجات التجميل",
    "alnzam-almwhd-lidarh-nfayat-alraayh-alshyh-bdwl-mjls-altaawn-ldwl-alkhlyj-alarbyh": "النظام الموحد لإدارة نفايات الرعاية الصحية بدول مجلس التعاون",
    "nzam-albyyh": "نظام البيئة",
    "personal-status-law": "نظام الأحوال الشخصية",
    "nzam-hmayh-altfl": "نظام حماية الطفل",
    "protection-from-abuse-law": "نظام الحماية من الإيذاء",
    "criminal-procedure-law": "نظام الإجراءات الجزائية",
    "whistleblowers-witnesses-experts-and-victims-protection-law": "نظام حماية المبلغين والشهود والخبراء والضحايا",
    "nzam-hyyh-alrqabh-wmkafhh-alfsad": "نظام هيئة الرقابة ومكافحة الفساد",
    "alnzam-aljzayy-ljraym-altzwyr": "النظام الجزائي لجرائم التزوير",
    "nzam-alahdath": "نظام الأحداث",
    "nzam-astkhdam-kamyrat-almraqbh-alamnyh": "نظام استخدام كاميرات المراقبة الأمنية",
    "nzam-ijraaat-altrakhys-albldyh": "نظام إجراءات التراخيص البلدية",
}


DEFAULT_COMPANION_REGULATIONS_BY_CORE: dict[str, tuple[str, ...]] = {
    "anti-money-laundering-law": (
        "nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh",
        "nzam-mraqbh-albnwk",
        "nzam-albnk-almrkzy-alsawdy",
    ),
    "anti-cybercrime-law": ("personal-data-protection-law", "pdpl-implementing-regulation"),
    "communications-and-information-technology-law": (
        "personal-data-protection-law",
        "pdpl-implementing-regulation",
    ),
    "commercial-fraud-law": ("e-commerce-law", "law-of-evidence"),
    "companies-law": ("companies-implementing-regulation",),
    "nzam-alswq-almalyh": (
        "cma-corporate-governance-regulations",
        "cma-continuing-obligations-rules",
        "cma-securities-offering-rules",
        "companies-law",
    ),
    "copyright-law": ("law-of-evidence", "nzam-almhakm-altjaryh"),
    "civil-transactions-law": ("law-of-evidence",),
    "electronic-transactions-law": ("law-of-evidence",),
    "execution-implementing-regulation": ("execution-law",),
    "execution-law": ("execution-implementing-regulation", "law-of-evidence", "electronic-transactions-law"),
    "government-tenders-and-procurement-law": (
        "government-procurement-implementing-regulation",
        "procurement-conflict-of-interest-regulation",
        "procurement-conduct-ethics-regulation",
        "nzam-almrafaat-amam-dywan-almzalm",
        "nzam-altnfydh-amam-dywan-almzalm",
    ),
    "labor-contract-documentation-rules": ("labor-law", "labor-implementing-regulation"),
    "labor-implementing-regulation": ("labor-law", "labor-violations-penalties-table"),
    "labor-law": (
        "labor-implementing-regulation",
        "wage-protection-rules",
        "labor-contract-documentation-rules",
        "labor-violations-penalties-table",
    ),
    "law-of-evidence": ("law-of-sharia-procedure",),
    "nzam-alamtyaz-altjary": ("nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh"),
    "nzam-alajhzh-walmstlzmat-altbyh": (
        "nzam-alhyyh-alaamh-llghdhaa-waldwaa",
        "commercial-fraud-law",
        "law-of-evidence",
    ),
    "nzam-alalamat-altjaryh": (
        "qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh",
        "law-of-evidence",
        "commercial-fraud-law",
    ),
    "nzam-alawraq-altjaryh": (
        "law-of-evidence",
        "nzam-almhakm-altjaryh",
        "execution-law",
        "execution-implementing-regulation",
        "electronic-transactions-law",
    ),
    "law-of-sharia-procedure": ("law-of-evidence",),
    "nzam-almsahmat-alaqaryh": (
        "nzam-altsjyl-alayny-llaqar",
        "real-estate-brokerage-law",
        "civil-transactions-law",
        "law-of-evidence",
    ),
    "nzam-albyyh": ("civil-transactions-law", "law-of-evidence"),
    "nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth": (
        "civil-transactions-law",
        "real-estate-brokerage-law",
        "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha",
        "nzam-altmwyl-alaqary",
        "nzam-mraqbh-shrkat-altmwyl",
        "law-of-evidence",
    ),
    "nzam-alghdhaa": (
        "nzam-alhyyh-alaamh-llghdhaa-waldwaa",
        "commercial-fraud-law",
        "law-of-evidence",
    ),
    "nzam-mntjat-altjmyl": (
        "nzam-alhyyh-alaamh-llghdhaa-waldwaa",
        "commercial-fraud-law",
        "law-of-evidence",
    ),
    "nzam-alhyyh-alaamh-llghdhaa-waldwaa": (
        "commercial-fraud-law",
        "law-of-evidence",
    ),
    "nzam-aliflas": ("bankruptcy-implementing-regulation", "companies-law", "companies-implementing-regulation", "labor-law"),
    "bankruptcy-implementing-regulation": ("nzam-aliflas", "companies-law", "companies-implementing-regulation", "labor-law"),
    "nzam-almdfwaat-wkhdmatha": (
        "nzam-albnk-almrkzy-alsawdy",
        "nzam-mraqbh-albnwk",
        "personal-data-protection-law",
    ),
    "nzam-alahdath": ("criminal-procedure-law", "nzam-hmayh-altfl", "law-of-evidence"),
    "nzam-alialam-almryy-walmsmwa": ("nzam-almtbwaat-walnshr",),
    "nzam-almhakm-altjaryh": ("law-of-evidence", "nzam-altkalyf-alqdayyh"),
    "nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh": ("nzam-alhyyh-alaamh-llghdhaa-waldwaa",),
    "nzam-almwssat-alshyh-alkhash": ("alnzam-alshy", "nzam-mzawlh-almhn-alshyh"),
    "nzam-almnafsh": ("companies-law",),
    "nzam-mraqbh-albnwk": ("nzam-albnk-almrkzy-alsawdy", "law-of-evidence"),
    "nzam-alrhn-altjary": ("civil-transactions-law", "nzam-almhakm-altjaryh"),
    "nzam-alsjl-altjary": ("companies-law", "law-of-evidence"),
    "nzam-altamynat-alajtmaayh": ("labor-law", "law-of-evidence"),
    "nzam-altamyn-dd-altatl-an-alaml": ("labor-law", "nzam-altamynat-alajtmaayh", "law-of-evidence"),
    "nzam-althkym": ("execution-law", "law-of-evidence"),
    "nzam-altnfydh-amam-dywan-almzalm": ("nzam-almrafaat-amam-dywan-almzalm",),
    "nzam-alwkalat-altjaryh": ("nzam-almhakm-altjaryh", "civil-transactions-law"),
    "nzam-dman-alhqwq-balamwal-almnqwlh": (
        "civil-transactions-law",
        "execution-law",
        "law-of-evidence",
    ),
    "nzam-drybh-alqymh-almdafh": ("zatca-vat-implementing-regulation",),
    "nzam-drybh-altsrfat-alaqaryh": (
        "nzam-drybh-alqymh-almdafh",
        "zatca-vat-implementing-regulation",
        "civil-transactions-law",
        "nzam-altsjyl-alayny-llaqar",
    ),
    "nzam-drybh-aldkhl": ("law-of-evidence",),
    "nzam-jbayh-alzkah": ("law-of-evidence",),
    "nzam-hmayh-altfl": ("protection-from-abuse-law", "criminal-procedure-law", "law-of-evidence"),
    "nzam-ijraaat-altrakhys-albldyh": ("nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence", "nzam-almhakm-altjaryh"),
    "nzam-mkafhh-altstr": ("nzam-alsjl-altjary", "companies-law", "law-of-evidence"),
    "nzam-mkafhh-jrymh-althrsh": (
        "workplace-behavioral-misconduct-controls",
        "criminal-procedure-law",
        "law-of-evidence",
    ),
    "workplace-behavioral-misconduct-controls": (
        "labor-law",
        "nzam-mkafhh-jrymh-althrsh",
        "law-of-evidence",
    ),
    "nzam-mraqbh-shrkat-altamyn-altaawny": ("nzam-aldman-alshy-altaawny",),
    "nzam-mraqbh-shrkat-altmwyl": ("nzam-albnk-almrkzy-alsawdy", "civil-transactions-law"),
    "nzam-mzawlh-almhn-alshyh": ("alnzam-alshy", "law-of-evidence"),
    "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha": (
        "civil-transactions-law",
        "real-estate-brokerage-law",
        "law-of-evidence",
    ),
    "nzam-almwasfat-waljwdh": ("commercial-fraud-law", "law-of-evidence"),
    "nzam-alnql-alaam-ala-altrq-balmmlkh-alarbyh-alsawdyh": (
        "nzam-almrwr",
        "civil-transactions-law",
        "law-of-evidence",
    ),
    "nzam-almrwr": ("nzam-mraqbh-shrkat-altamyn-altaawny", "law-of-evidence"),
    "nzam-altmwyl-alaqary": ("nzam-mraqbh-shrkat-altmwyl", "civil-transactions-law", "law-of-evidence"),
    "nzam-tsnyf-almqawlyn": ("government-tenders-and-procurement-law",),
    "personal-status-law": ("law-of-evidence",),
    "personal-data-protection-law": ("pdpl-implementing-regulation", "law-of-evidence"),
    "pdpl-implementing-regulation": ("personal-data-protection-law",),
    "pdpl-transfer-regulation": ("personal-data-protection-law", "pdpl-implementing-regulation"),
    "product-safety-law": ("commercial-fraud-law", "nzam-almwasfat-waljwdh", "law-of-evidence"),
    "protection-from-abuse-law": ("nzam-hmayh-altfl", "criminal-procedure-law", "law-of-evidence"),
    "real-estate-brokerage-law": ("civil-transactions-law", "law-of-evidence"),
    "nzam-astkhdam-kamyrat-almraqbh-alamnyh": (
        "personal-data-protection-law",
        "pdpl-implementing-regulation",
        "anti-cybercrime-law",
    ),
}


FIELD_REGULATION_PACKAGES: tuple[dict[str, Any], ...] = (
    {
        "fields": ("العمل", "عقود العمل", "العمل وملحقاتها"),
        "core": ("labor-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["labor-law"],
    },
    {
        "fields": ("الدليل الإجرائي لتوثيق عقود العمل", "توثيق عقود العمل"),
        "core": ("labor-contract-documentation-rules",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["labor-contract-documentation-rules"],
    },
    {
        "fields": ("اللائحة التنفيذية لنظام العمل", "العمل وملحقاتها"),
        "core": ("labor-implementing-regulation",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["labor-implementing-regulation"],
    },
    {
        "fields": ("الإثبات", "الاثبات"),
        "core": ("law-of-evidence",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["law-of-evidence"],
    },
    {
        "fields": ("المعاملات المدنية",),
        "core": ("civil-transactions-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["civil-transactions-law"],
    },
    {
        "fields": ("المحاكم التجارية",),
        "core": ("nzam-almhakm-altjaryh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almhakm-altjaryh"],
    },
    {
        "fields": ("التحكيم",),
        "core": ("nzam-althkym",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-althkym"],
    },
    {
        "fields": ("التنفيذ أمام ديوان المظالم", "التنفيذ امام ديوان المظالم", "محكمة التنفيذ الإدارية", "محكمة التنفيذ الادارية"),
        "core": ("nzam-altnfydh-amam-dywan-almzalm",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-altnfydh-amam-dywan-almzalm"],
    },
    {
        "fields": ("التنفيذ",),
        "core": ("execution-law", "execution-implementing-regulation"),
        "companions": (),
    },
    {
        "fields": ("المنافسات و المشتريات الحكومية", "المنافسات والمشتريات الحكومية"),
        "core": ("government-tenders-and-procurement-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["government-tenders-and-procurement-law"],
    },
    {
        "fields": ("المواصفات والجودة", "المواصفات القياسية", "شهادات المطابقة", "شهادة مطابقة"),
        "core": ("nzam-almwasfat-waljwdh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almwasfat-waljwdh"],
    },
    {
        "fields": ("العلامات التجارية",),
        "core": ("nzam-alalamat-altjaryh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alalamat-altjaryh"],
    },
    {
        "fields": ("الامتياز التجاري",),
        "core": ("nzam-alamtyaz-altjary",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alamtyaz-altjary"],
    },
    {
        "fields": ("الوكالات التجارية",),
        "core": ("nzam-alwkalat-altjaryh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alwkalat-altjaryh"],
    },
    {
        "fields": ("السوق المالية", "الأوراق المالية", "الاوراق المالية"),
        "core": ("nzam-alswq-almalyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alswq-almalyh"],
    },
    {
        "fields": ("مكافحة جرائم المعلوماتية", "جرائم المعلوماتية"),
        "core": ("anti-cybercrime-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["anti-cybercrime-law"],
    },
    {
        "fields": ("الشركات", "نظام الشركات"),
        "core": ("companies-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["companies-law"],
    },
    {
        "fields": ("التجارة الإلكترونية", "التجاره الالكترونيه", "نظام التجارة الإلكترونية"),
        "core": ("e-commerce-law",),
        "companions": ("ecommerce-implementing-regulation", "personal-data-protection-law"),
    },
    {
        "fields": ("منتجات التجميل", "مستحضر تجميل", "منتج تجميلي", "مستحضرات تجميل"),
        "core": ("nzam-mntjat-altjmyl",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mntjat-altjmyl"],
    },
    {
        "fields": ("مكافحة الغش التجاري",),
        "core": ("commercial-fraud-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["commercial-fraud-law"],
    },
    {
        "fields": ("الغذاء", "نظام الغذاء"),
        "core": ("nzam-alghdhaa",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alghdhaa"],
    },
    {
        "fields": ("البيئة", "البيئه", "نظام البيئة"),
        "core": ("nzam-albyyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-albyyh"],
    },
    {
        "fields": ("مزاولة المهن الصحية", "مزاولة المهن الصحيه"),
        "core": ("nzam-mzawlh-almhn-alshyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mzawlh-almhn-alshyh"],
    },
    {
        "fields": ("المؤسسات الصحية الخاصة", "الموسسات الصحية الخاصة"),
        "core": ("nzam-almwssat-alshyh-alkhash",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almwssat-alshyh-alkhash"],
    },
    {
        "fields": ("المنشآت والمستحضرات الصيدلانية والعشبية", "المنشات والمستحضرات الصيدلانية والعشبية"),
        "core": ("nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh"],
    },
    {
        "fields": ("الرهن التجاري",),
        "core": ("nzam-alrhn-altjary",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alrhn-altjary"],
    },
    {
        "fields": (
            "مراقبة شركات التمويل",
            "شركات التمويل",
            "اشتر الآن وادفع لاحقًا",
            "اشتر الان وادفع لاحقا",
            "شراء الآن والدفع لاحقًا",
            "شراء الان والدفع لاحقا",
            "الدفع لاحقًا",
            "الدفع لاحقا",
            "BNPL",
        ),
        "core": ("nzam-mraqbh-shrkat-altmwyl",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mraqbh-shrkat-altmwyl"],
    },
    {
        "fields": (
            "المدفوعات وخدماتها",
            "نظام المدفوعات",
            "مزود الدفع",
            "مقدم خدمة الدفع",
            "خدمة الدفع",
            "خدمات الدفع",
            "بوابة الدفع",
            "بيانات البطاقة",
            "بطاقة بنكية",
            "خصم تلقائي",
        ),
        "core": ("nzam-almdfwaat-wkhdmatha",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almdfwaat-wkhdmatha"],
    },
    {
        "fields": ("مراقبة البنوك", "البنوك", "بنك"),
        "core": ("nzam-mraqbh-albnwk",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mraqbh-albnwk"],
    },
    {
        "fields": ("التمويل العقاري", "قرض عقاري"),
        "core": ("nzam-altmwyl-alaqary",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-altmwyl-alaqary"],
    },
    {
        "fields": ("الأحداث", "الاحداث"),
        "core": ("nzam-alahdath",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alahdath"],
    },
    {
        "fields": ("إجراءات التراخيص البلدية", "اجراءات التراخيص البلدية", "التراخيص البلدية"),
        "core": ("nzam-ijraaat-altrakhys-albldyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-ijraaat-altrakhys-albldyh"],
    },
    {
        "fields": ("تصنيف المقاولين",),
        "core": ("nzam-tsnyf-almqawlyn",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-tsnyf-almqawlyn"],
    },
    {
        "fields": ("ضريبة التصرفات العقارية", "ضريبه التصرفات العقاريه"),
        "core": ("nzam-drybh-altsrfat-alaqaryh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-drybh-altsrfat-alaqaryh"],
    },
    {
        "fields": ("الأحوال الشخصية", "الاحوال الشخصية"),
        "core": ("personal-status-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["personal-status-law"],
    },
    {
        "fields": ("مكافحة جريمة التحرش",),
        "core": ("nzam-mkafhh-jrymh-althrsh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mkafhh-jrymh-althrsh"],
    },
    {
        "fields": (
            "ضوابط الحماية من التعديات السلوكية",
            "ضوابط التعديات السلوكية",
            "التعديات السلوكية في بيئة العمل",
            "تعديات سلوكية",
            "تعدي سلوكي",
        ),
        "core": ("workplace-behavioral-misconduct-controls",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["workplace-behavioral-misconduct-controls"],
    },
    {
        "fields": ("التأمين ضد التعطل عن العمل", "تعطل عن العمل", "ساند"),
        "core": ("nzam-altamyn-dd-altatl-an-alaml",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-altamyn-dd-altatl-an-alaml"],
    },
    {
        "fields": (
            "مراقبة شركات التأمين التعاوني",
            "شركات التأمين التعاوني",
            "مركبة غير مؤمنة",
            "مركبات غير مؤمنة",
            "مركبة غير مومنة",
            "مركبات غير مومنة",
            "تأمين المركبة",
            "تامين المركبة",
            "مركبة مؤمنة",
            "مركبات مؤمنة",
        ),
        "core": ("nzam-mraqbh-shrkat-altamyn-altaawny",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mraqbh-shrkat-altamyn-altaawny"],
    },
    {
        "fields": ("الضمان الصحي التعاوني", "تأمين صحي", "تغطية التأمين الصحي"),
        "core": ("nzam-aldman-alshy-altaawny",),
        "companions": ("nzam-mraqbh-shrkat-altamyn-altaawny", "civil-transactions-law", "law-of-evidence"),
    },
    {
        "fields": ("الأجهزة والمستلزمات الطبية", "الاجهزة والمستلزمات الطبية", "الأجهزة الطبية"),
        "core": ("nzam-alajhzh-walmstlzmat-altbyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alajhzh-walmstlzmat-altbyh"],
    },
    {
        "fields": ("ملكية الوحدات العقارية", "جمعية ملاك", "جمعيات الملاك"),
        "core": ("nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha"],
    },
    {
        "fields": ("المنافسة", "نظام المنافسة"),
        "core": ("nzam-almnafsh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almnafsh"],
    },
    {
        "fields": (
            "النقل العام على الطرق",
            "نظام النقل العام",
            "توصيل الطلبات",
            "نشاط توصيل الطلبات",
            "تطبيقات التوصيل",
            "تطبيق توصيل",
            "سائق توصيل",
            "سائقين مستقلين",
        ),
        "core": ("nzam-alnql-alaam-ala-altrq-balmmlkh-alarbyh-alsawdyh",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alnql-alaam-ala-altrq-balmmlkh-alarbyh-alsawdyh"],
    },
    {
        "fields": (
            "نظام المرور",
            "المرور",
            "مركبة غير مؤمنة",
            "مركبات غير مؤمنة",
            "تأمين المركبة",
            "حادث سير",
            "حوادث السير",
            "حادث توصيل",
            "حوادث السائقين",
        ),
        "core": ("nzam-almrwr",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-almrwr"],
    },
    {
        "fields": ("الإعلام المرئي والمسموع", "الاعلام المرئي والمسموع"),
        "core": ("nzam-alialam-almryy-walmsmwa",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["nzam-alialam-almryy-walmsmwa"],
    },
    {
        "fields": (
            "حقوق المؤلف",
            "حماية حقوق المؤلف",
            "الملكية الفكرية",
            "الملكيه الفكريه",
            "برمجيات",
            "البرمجيات",
            "برنامج داخلي",
            "برنامج حاسب",
            "الكود المصدري",
            "كود مصدري",
            "سورس كود",
            "شفرة مصدرية",
        ),
        "core": ("copyright-law",),
        "companions": DEFAULT_COMPANION_REGULATIONS_BY_CORE["copyright-law"],
    },
)


def _bundle(
    bundle_id: str,
    intent: str,
    core: tuple[str, ...],
    companion: tuple[str, ...] = (),
    any_patterns: tuple[str, ...] = (),
    all_patterns: tuple[str, ...] = (),
    excluded_patterns: tuple[str, ...] = (),
    articles: dict[str, set[int]] | None = None,
    priority: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": bundle_id,
        "intent": intent,
        "core_regulations": core,
        "companion_regulations": companion,
        "any_patterns": any_patterns,
        "all_patterns": all_patterns,
        "excluded_patterns": excluded_patterns,
        "articles": articles or {},
        "priority": priority,
    }


LEGAL_DOCUMENT_BUNDLES: list[dict[str, Any]] = [
    _bundle(
        "jamia_ecommerce_fraud_pdpl_medical_device_bundle",
        "ecommerce_fraud_pdpl_medical_device",
        ("e-commerce-law", "commercial-fraud-law", "civil-transactions-law", "electronic-transactions-law", "nzam-alajhzh-walmstlzmat-altbyh"),
        (
            "ecommerce-implementing-regulation",
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "nzam-alhyyh-alaamh-llghdhaa-waldwaa",
            "law-of-evidence",
            "zatca-e-invoicing-bylaw",
            "zatca-e-invoicing-technical-controls",
        ),
        any_patterns=(
            "جهاز طبي",
            "جهازاً طبياً",
            "جهازا طبيا",
            "جهاز طبي منزلي",
            "ادعاءات علاجية",
            "ادعاءات غير دقيقة",
            "غير مطابق للوصف",
            "عيب في المبيع",
            "الشروط في الموقع تمنع الاسترداد",
            "فاتورة إلكترونية",
            "رسائل بريد",
            "صور للإعلان",
            "سلعة مغشوشة",
        ),
        all_patterns=("متجر",),
        articles={
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7, 14, 16, 18},
            "civil-transactions-law": {94, 95, 109, 120, 136, 321},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
            "law-of-evidence": {55, 56, 57},
            "nzam-alajhzh-walmstlzmat-altbyh": {6, 8, 16, 20, 21, 35, 37},
            "nzam-alhyyh-alaamh-llghdhaa-waldwaa": {3, 5},
            "zatca-e-invoicing-bylaw": {1, 2, 3, 4, 5},
            "zatca-e-invoicing-technical-controls": {1, 2, 3, 4, 5},
        },
        priority=9.5,
    ),
    _bundle(
        "ecommerce_digital_service_pdpl_marketing_bundle",
        "ecommerce_digital_service_pdpl_marketing",
        ("e-commerce-law",),
        ("ecommerce-implementing-regulation", "personal-data-protection-law", "pdpl-implementing-regulation"),
        any_patterns=("دورة إلكترونية", "دوره الكترونيه", "اشتراك رقمي", "خدمة رقمية", "الخدمة لم تتح", "لم تفعل الدورة", "لم تفعّل", "لم تُفعّل"),
        articles={
            "e-commerce-law": {5, 10, 13, 14, 17},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "personal-data-protection-law": {4, 5, 23, 25, 26, 31},
            "pdpl-implementing-regulation": {17, 24, 32},
        },
        priority=9.2,
    ),
    _bundle(
        "ecommerce_delivery_ad_refund_disclosure_bundle",
        "ecommerce_delivery_ad_refund",
        ("e-commerce-law",),
        ("ecommerce-implementing-regulation", "commercial-fraud-law", "civil-transactions-law", "electronic-transactions-law", "law-of-evidence"),
        any_patterns=(
            "متجر إلكتروني",
            "متجر الكتروني",
            "تأخر التسليم",
            "تاخر التسليم",
            "رفض الاسترجاع",
            "إلغاء الطلب",
            "اعلان مضلل",
            "إعلان مضلل",
            "غير مطابق للوصف",
            "عيب في المبيع",
            "الشروط في الموقع تمنع الاسترداد",
            "فاتورة إلكترونية",
            "رسائل بريد",
        ),
        excluded_patterns=("منافسة حكومية", "منصة اعتماد", "الجهة الحكومية", "ترسية", "بيع على الخارطة", "المطور العقاري"),
        articles={
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7, 14, 16, 18},
            "civil-transactions-law": {94, 95, 109, 120, 136, 321},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
            "law-of-evidence": {55, 56, 57},
        },
        priority=8.8,
    ),
    _bundle(
        "ecommerce_marketplace_installment_pdpl_trademark_axis_bundle",
        "ecommerce_marketplace_installment_pdpl_trademark_axis",
        ("e-commerce-law", "personal-data-protection-law", "commercial-fraud-law", "nzam-alalamat-altjaryh"),
        (
            "ecommerce-implementing-regulation",
            "pdpl-implementing-regulation",
            "nzam-almalwmat-alaytmanyh",
            "nzam-mraqbh-shrkat-altmwyl",
            "nzam-almdfwaat-wkhdmatha",
            "nzam-alialam-almryy-walmsmwa",
            "law-of-evidence",
            "nzam-almhakm-altjaryh",
        ),
        any_patterns=(
            "تطبيق بيع أجهزة",
            "تطبيق يبيع أجهزة",
            "تطبيق لبيع الأجهزة",
            "خصم ٧٠",
            "خصم 70",
            "رفع السعر قبل الحملة",
            "صور هويات",
            "بيانات بنكية",
            "مواقع جغرافية",
            "تسريب قاعدة البيانات",
            "شركة تمويل",
            "التحقق الائتماني",
            "بيع بالتقسيط",
            "منتجات تحمل علامة مشابهة",
            "علامة مشابهة",
            "إعلان مؤثر",
            "مؤثر",
        ),
        articles={
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "personal-data-protection-law": {5, 8, 10, 13, 15, 20, 23, 25, 26, 31},
            "pdpl-implementing-regulation": {17, 24, 32},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7},
            "nzam-alalamat-altjaryh": {1, 2, 3, 4, 5, 21, 22, 43},
            "nzam-almalwmat-alaytmanyh": {1, 2, 3, 4, 5, 6},
            "nzam-mraqbh-shrkat-altmwyl": {1, 2, 3, 4, 5, 6, 7},
            "nzam-almdfwaat-wkhdmatha": {1, 2, 3, 4, 5, 6},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.9,
    ),
    _bundle(
        "labor_harassment_retaliation_dues_bundle",
        "labor_harassment_retaliation_dues",
        ("labor-law", "nzam-mkafhh-jrymh-althrsh", "workplace-behavioral-misconduct-controls"),
        ("labor-implementing-regulation", "labor-violations-penalties-table", "law-of-evidence"),
        any_patterns=("تحرش", "تعديات سلوكية", "ابلغت عن تحرش", "أبلغت عن تحرش", "بيئة العمل", "اجيرة ابلغت", "أجيرة أبلغت", "رسائل وشهود على البلاغ"),
        articles={
            "labor-law": {75, 76, 77, 84, 88, 90, 94},
            "nzam-mkafhh-jrymh-althrsh": {1, 2, 3, 4, 5, 6, 7},
            "workplace-behavioral-misconduct-controls": {3},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.0,
    ),
    _bundle(
        "labor_overtime_termination_wage_bundle",
        "labor_overtime_termination_wage",
        ("labor-law",),
        ("labor-implementing-regulation", "labor-violations-penalties-table", "wage-protection-rules"),
        any_patterns=("ساعات إضافية", "ساعات اضافية", "العطل", "خصمت من راتبه", "خصم من راتبه", "أنهت عقده", "انهت عقده", "دون إشعار", "دون اشعار"),
        all_patterns=(),
        articles={"labor-law": {75, 76, 77, 84, 88, 90, 94, 98, 99, 100, 101, 107}},
        priority=8.9,
    ),
    _bundle(
        "government_employee_discipline_administrative_challenge_bundle",
        "government_employee_discipline_administrative_challenge",
        ("nzam-alkhdmh-almdnyh", "nzam-alandbat-alwzyfy"),
        ("nzam-dywan-almzalm", "nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"),
        any_patterns=(
            "موظف حكومي",
            "جزاء تأديبي",
            "جزاء تاديبي",
            "قرار كف يد",
            "كف يد",
            "الطعن في القرار الإداري",
            "الطعن في القرار الاداري",
            "قرار إداري أمام القضاء",
            "قرار اداري امام القضاء",
        ),
        articles={
            "nzam-alkhdmh-almdnyh": {1, 11, 12, 13},
            "nzam-alandbat-alwzyfy": {1, 3, 4, 5, 6, 7, 8, 9, 15, 16},
            "nzam-dywan-almzalm": {13},
            "nzam-almrafaat-amam-dywan-almzalm": {8, 13, 14, 15, 16},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.4,
    ),
    _bundle(
        "pdpl_breach_marketing_generic_axis_bundle",
        "pdpl_breach_marketing_generic_axis",
        ("personal-data-protection-law",),
        (
            "pdpl-implementing-regulation",
            "e-commerce-law",
            "ecommerce-implementing-regulation",
            "anti-cybercrime-law",
            "law-of-evidence",
        ),
        any_patterns=(
            "بيانات شخصية",
            "خصوصية",
            "تسرب بيانات",
            "تسريب قاعدة البيانات",
            "تسرب بيانات شمل",
            "مشاركة البيانات",
            "شركة تسويق",
            "تسويق مباشر",
            "رقم الجوال",
            "أرقام الجوالات",
            "ارقام الجوالات",
            "إعلانات واتساب",
            "اعلانات واتساب",
            "رسائل SMS",
            "رسائل sms",
            "دون موافقة واضحة",
            "إشعار الجهة المختصة",
            "اشعار الجهة المختصة",
            "إشعار صاحب البيانات",
            "اشعار صاحب البيانات",
            "صور هويات",
            "بيانات بنكية",
            "مواقع جغرافية",
            "سجل الطلبات",
        ),
        excluded_patterns=("خارج المملكة", "مزود سحابي خارج", "نقل البيانات خارج"),
        articles={
            "personal-data-protection-law": {4, 5, 8, 10, 13, 15, 20, 23, 25, 26, 31, 32},
            "pdpl-implementing-regulation": {17, 24, 32, 37},
            "e-commerce-law": {5},
            "ecommerce-implementing-regulation": {5, 18},
            "anti-cybercrime-law": {3, 4, 5},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.7,
    ),
    _bundle(
        "pdpl_health_breach_transfer_marketing_bundle",
        "pdpl_health_breach_transfer_marketing",
        ("personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation"),
        ("anti-cybercrime-law",),
        any_patterns=(
            "تطبيق صحي",
            "بيانات صحية",
            "مزود سحابي خارج",
            "نقل بيانات خارج المملكة",
            "معالجة بيانات خارج المملكة",
            "استضافة بيانات خارج المملكة",
            "إفصاح عن البيانات خارج المملكة",
            "افصاح عن البيانات خارج المملكة",
            "تسرب بيانات",
            "شركة تحليل تسويقي",
            "اختراق",
        ),
        articles={
            "personal-data-protection-law": {1, 2, 4, 5, 8, 10, 13, 15, 20, 23, 25, 26, 29, 31, 32},
            "pdpl-implementing-regulation": {17, 24, 32, 37},
            "pdpl-transfer-regulation": {2, 5, 7},
            "anti-cybercrime-law": {3, 4, 5, 6},
        },
        priority=9.5,
    ),
    _bundle(
        "pdpl_hr_employee_data_foreign_system_bundle",
        "pdpl_hr_employee_data_foreign_system",
        ("personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation"),
        ("labor-law", "law-of-evidence"),
        any_patterns=(
            "بيانات موظفي",
            "بيانات الموظفين",
            "ملفات الموظفين",
            "ملفاتهم الصحية",
            "التقييمات الوظيفية",
            "نظام موارد بشرية أجنبي",
            "نظام موارد بشرية اجنبي",
            "منصة موارد بشرية أجنبية",
            "منصة موارد بشرية اجنبية",
            "نظام hr أجنبي",
            "نظام hr اجنبي",
            "شركة استشارات",
            "مدة احتفاظ",
        ),
        articles={
            "personal-data-protection-law": {5, 8, 13, 15, 20, 23, 25, 26, 29, 31},
            "pdpl-implementing-regulation": {11, 17, 24, 32},
            "pdpl-transfer-regulation": {2, 5, 7},
            "labor-law": {12, 51, 52, 90},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.6,
    ),
    _bundle(
        "marketplace_product_safety_recall_bundle",
        "marketplace_product_safety_recall",
        ("product-safety-law",),
        ("e-commerce-law", "ecommerce-implementing-regulation", "commercial-fraud-law", "law-of-evidence"),
        any_patterns=(
            "جهاز منزلي خطير",
            "منتج خطير",
            "مخاطر السلامة",
            "سلامة المستهلك",
            "لم تعلن عن الاستدعاء",
            "إعلان الاستدعاء",
            "اعلان الاستدعاء",
            "سحب المنتج",
        ),
        articles={
            "product-safety-law": {1, 2, 3, 4, 5, 6, 7},
            "e-commerce-law": {5, 8, 10, 11, 13, 14, 17, 18},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.3,
    ),
    _bundle(
        "vat_platform_commission_einvoice_bundle",
        "vat_platform_commission_einvoice",
        ("nzam-drybh-alqymh-almdafh",),
        ("zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"),
        any_patterns=(
            "منصة وسيطة",
            "عمولات من البائعين",
            "عمولة من البائعين",
            "الضريبة على العمولة",
            "ضريبة على العمولة",
            "تصدر فواتير إلكترونية لهم",
            "تصدر فواتير الكترونية لهم",
        ),
        articles={
            "nzam-drybh-alqymh-almdafh": {1, 2, 6, 14, 21, 22, 23, 36, 41, 42},
            "zatca-vat-implementing-regulation": {1, 2, 5, 6, 7, 8, 9, 53},
            "zatca-e-invoicing-bylaw": {1, 2, 3, 4, 5, 6},
            "zatca-e-invoicing-technical-controls": {1, 2, 3, 4, 5},
        },
        priority=9.4,
    ),
    _bundle(
        "vat_ecommerce_invoice_refund_bundle",
        "vat_einvoicing_tax_invoice_compliance",
        ("nzam-drybh-alqymh-almdafh",),
        (
            "zatca-vat-implementing-regulation",
            "zatca-e-invoicing-bylaw",
            "zatca-e-invoicing-technical-controls",
        ),
        any_patterns=(
            "فاتورة ضريبية",
            "فواتير pdf",
            "فاتورة pdf",
            "فاتورة إلكترونية",
            "فاتورة الكترونية",
            "فوترة إلكترونية",
            "فوترة الكترونية",
            "الفوترة الإلكترونية",
            "الفوترة الالكترونية",
            "الرقم الضريبي",
            "إشعار خصم",
            "اشعار خصم",
            "إشعارات خصم",
            "اشعارات خصم",
            "إشعار دائن",
            "اشعار دائن",
            "إشعارات دائنة",
            "اشعارات دائنة",
            "إشعار مدين",
            "اشعار مدين",
            "إشعارات مدين",
            "اشعارات مدين",
            "إرجاع مبالغ",
            "ارجاع مبالغ",
            "حقول الفاتورة",
            "رمز الاستجابة السريعة",
            "رمز qr",
            "فواتير مبسطة",
            "فاتورة مبسطة",
            "رقم ضريبي",
            "qr",
            "زاتكا",
            "ضريبة القيمة المضافة",
            "vat",
        ),
        articles={
            "nzam-drybh-alqymh-almdafh": {3, 22, 40, 41, 42},
            "zatca-vat-implementing-regulation": {53, 54, 66},
            "zatca-e-invoicing-bylaw": {1, 2, 3, 4, 5, 7},
            "zatca-e-invoicing-technical-controls": {1, 2, 3, 4, 5},
        },
        priority=9.7,
    ),
    _bundle(
        "procurement_bid_rigging_conflict_bundle",
        "procurement_bid_irregularities",
        ("government-tenders-and-procurement-law",),
        ("government-procurement-implementing-regulation", "procurement-conflict-of-interest-regulation", "procurement-conduct-ethics-regulation"),
        any_patterns=(
            "منافسة حكومية",
            "لجنة الفحص",
            "عضو لجنة الفحص",
            "تعارض مصالح",
            "قريب من مدير الشركة",
            "المحتوى المحلي",
            "متطلبات المحتوى المحلي",
            "تواطؤ",
            "تقسيم العطاءات",
            "رفع الأسعار",
            "رفع الاسعار",
            "موردان",
        ),
        articles={
            "government-tenders-and-procurement-law": {37, 40, 46, 50, 51, 52, 53, 74, 76, 78, 86, 87, 88, 96},
            "government-procurement-implementing-regulation": {74, 75, 76, 77, 78, 87, 88, 96, 154},
            "procurement-conflict-of-interest-regulation": {5, 6, 7, 8, 9, 10, 11},
            "procurement-conduct-ethics-regulation": {2, 4, 5},
        },
        priority=9.4,
    ),
    _bundle(
        "procurement_supplier_collusion_competition_axis_bundle",
        "procurement_supplier_collusion_competition_axis",
        ("government-tenders-and-procurement-law", "nzam-almnafsh"),
        ("government-procurement-implementing-regulation", "procurement-conflict-of-interest-regulation", "law-of-evidence"),
        any_patterns=(
            "تواطؤ",
            "اتفق موردان",
            "اتفاق الموردين",
            "تقسيم العطاءات",
            "تقسيم العروض",
            "رفع الأسعار",
            "رفع الاسعار",
            "تنسيق الأسعار",
            "تنسيق الاسعار",
            "تنسيق أسعار",
            "تنسيق اسعار",
            "تنسيق بين الموردين",
        ),
        articles={
            "government-tenders-and-procurement-law": {2, 37, 40, 46, 50, 51, 52, 53, 74, 76, 78, 86, 87, 88},
            "nzam-almnafsh": {1, 2, 3, 5, 6, 14, 15, 19},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.8,
    ),
    _bundle(
        "procurement_technical_specs_conformity_fraud_axis_bundle",
        "procurement_technical_specs_conformity_fraud_axis",
        ("government-tenders-and-procurement-law",),
        (
            "government-procurement-implementing-regulation",
            "procurement-conflict-of-interest-regulation",
            "procurement-conduct-ethics-regulation",
            "nzam-almwasfat-waljwdh",
            "commercial-fraud-law",
            "law-of-evidence",
            "nzam-almrafaat-amam-dywan-almzalm",
        ),
        any_patterns=(
            "مواصفات منحازة",
            "مواصفات فنية منحازة",
            "مواصفات تفصيلية",
            "منصة اعتماد",
            "شهادات مطابقة",
            "شهادة مطابقة",
            "جهة غير معتمدة",
            "غير معتمدة",
            "أجهزة مستعملة",
            "اجهزة مستعملة",
            "أجهزة مجددة",
            "اجهزة مجددة",
            "على أنها جديدة",
            "على انها جديدة",
            "مقاول باطن",
            "مقاول من الباطن",
        ),
        all_patterns=("منافس",),
        excluded_patterns=("متجر إلكتروني", "متجر الكتروني", "مستهلك اشترى"),
        articles={
            "government-tenders-and-procurement-law": {2, 17, 22, 31, 34, 39, 41, 50, 51, 52, 53, 70, 71, 74, 76, 78, 86, 87, 88, 92, 97},
            "government-procurement-implementing-regulation": {74, 75, 76, 77, 78, 87, 88, 96, 154},
            "procurement-conflict-of-interest-regulation": {5, 6, 7, 8, 9, 10, 11},
            "procurement-conduct-ethics-regulation": {2, 4, 5},
            "nzam-almwasfat-waljwdh": {3, 4, 5, 6, 7, 15, 17},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7},
            "law-of-evidence": {2, 3, 25, 26, 55, 67},
        },
        priority=9.8,
    ),
    _bundle(
        "procurement_public_contract_subcontract_delay_bundle",
        "procurement_public_contract_subcontract_delay",
        ("government-tenders-and-procurement-law",),
        ("government-procurement-implementing-regulation", "procurement-conduct-ethics-regulation"),
        any_patterns=(
            "التعاقد من الباطن",
            "مقاول من الباطن",
            "مقاول باطن",
            "متعاقد حكومي",
            "تأخير بسبب الجهة",
            "تاخير بسبب الجهة",
            "لم تسلم الموقع",
            "أمر إيقاف",
            "امر ايقاف",
            "عقد حكومي",
            "تعاقد المتعاقد من الباطن",
            "تعاقد المقاول من الباطن",
            "تعاقد من الباطن دون موافقة",
            "الشراء المباشر",
            "حالة طارئة",
            "الحالات الطارئة",
            "استبعد من منافسات",
            "الحرمان",
        ),
        excluded_patterns=(
            "منشأة خاصة",
            "منشاه خاصه",
            "موظف",
            "موظفون",
            "العامل",
            "العمال",
            "الرواتب",
            "الأجور",
            "الاجور",
            "فترة التجربة",
            "فتره التجربه",
            "منصة قوى",
            "منصه قوي",
            "نهاية الخدمة",
            "نهايه الخدمه",
        ),
        articles={"government-tenders-and-procurement-law": {59, 61, 70, 71, 74, 76, 78, 92, 97}},
        priority=8.2,
    ),
    _bundle(
        "competition_merger_dominance_bundle",
        "competition_merger_dominance",
        ("nzam-almnafsh",),
        ("companies-law",),
        any_patterns=("تركز اقتصادي", "استحواذ", "تستحوذ", "شركة مهيمنة", "مهيمنة", "وضع مهيمن", "الوضع المهيمن", "منافس ناشئ", "حصرية", "عدم التعامل مع منصات أخرى", "منصات توصيل"),
        articles={"nzam-almnafsh": {1, 2, 3, 5, 6, 7, 10, 11, 12, 14, 15}, "companies-law": {13, 178}},
        priority=9.1,
    ),
    _bundle(
        "procurement_site_worker_wages_bundle",
        "procurement_site_worker_wages",
        ("labor-law",),
        ("labor-implementing-regulation", "wage-protection-rules", "labor-violations-penalties-table"),
        any_patterns=(
            "تأخرت أجور عمال الموقع",
            "تاخرت اجور عمال الموقع",
            "أجور عمال الموقع",
            "اجور عمال الموقع",
            "رواتب عمال الموقع",
            "عمال الموقع",
        ),
        articles={"labor-law": {84, 88, 90, 94}},
        priority=9.4,
    ),
    _bundle(
        "company_llc_manager_civil_evidence_bundle",
        "company_llc_manager_civil_evidence",
        ("companies-law",),
        ("companies-implementing-regulation", "civil-transactions-law", "law-of-evidence", "nzam-almhakm-altjaryh"),
        any_patterns=(
            "شركة ذات مسؤولية محدودة",
            "شركة ذات مسئولية محدودة",
            "شريك أقلية",
            "شريك اقليه",
            "الشريك الأقلية",
            "منع الشريك",
            "اطلاع الشريك",
            "الاطلاع على الحسابات",
            "منع الاطلاع",
            "مدير شركة",
            "مدير الشركة",
            "حوّل أصول",
            "حول أصول",
            "حول اصول",
            "باع أصل",
            "باع اصلا",
            "استغلال أصول الشركة",
            "استغلال اصول الشركة",
            "شركة يملكها قريبه",
            "شركة يملكها قريب",
            "تعارض مصالح المدير",
            "تعارض مصالح",
            "القوائم",
            "الحسابات",
            "توزيع أرباح",
            "وزع أرباح",
            "أرباح صورية",
            "ارباح صورية",
            "حقوق الشركاء",
            "دعوى مسؤولية",
            "دعوى المسئولية",
            "مسؤولية المدير",
            "طلب مستعجل",
            "طلبًا مستعجلًا",
            "طلبا مستعجلا",
            "لم تحدث عقد تأسيسها",
            "لم تحدّث عقد تأسيسها",
            "تحديث عقد تأسيسها",
        ),
        excluded_patterns=("منافسة حكومية", "المنافسات والمشتريات", "المشتريات الحكومية", "لجنة الفحص", "ترسية", "الترسية", "المحتوى المحلي"),
        articles={
            "companies-law": {8, 22, 26, 27, 28, 158, 165, 167, 168, 171, 172, 176, 177, 182, 242, 254, 260, 261, 262, 276, 281},
            "civil-transactions-law": {94, 95, 120, 136},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "nzam-almhakm-altjaryh": {16, 17, 19},
        },
        priority=9.0,
    ),
    _bundle(
        "private_construction_building_code_evidence_bundle",
        "private_construction_building_code_evidence",
        ("civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy"),
        ("law-of-evidence", "nzam-tsnyf-almqawlyn"),
        any_patterns=(
            "مقاول",
            "فيلا",
            "عيوب البناء",
            "عيوب إنشائية",
            "عيوب انشائية",
            "عيوب خرسانية",
            "أعمال خرسانية",
            "اعمال خرسانية",
            "العزل",
            "الكهرباء",
            "الخرسانة",
            "خرسانية",
            "كود البناء",
        ),
        excluded_patterns=(
            "جهة حكومية",
            "منافسة حكومية",
            "متعاقد حكومي",
            "عقد حكومي",
        ),
        articles={
            "civil-transactions-law": {94, 95, 109, 139, 321, 349, 461, 463, 465, 466, 473, 475, 476},
            "nzam-ttbyq-kwd-albnaa-alsawdy": {2, 3, 4, 5, 6, 7, 8, 9},
            "law-of-evidence": {2, 3, 25, 26, 55, 67},
            "nzam-tsnyf-almqawlyn": {2, 3, 4, 5},
        },
        priority=9.0,
    ),
    _bundle(
        "offplan_real_estate_broker_escrow_bundle",
        "offplan_real_estate_broker_escrow",
        ("nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth",),
        (
            "real-estate-brokerage-law",
            "civil-transactions-law",
            "nzam-altsjyl-alayny-llaqar",
            "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha",
            "nzam-altmwyl-alaqary",
            "nzam-mraqbh-shrkat-altmwyl",
            "nzam-almhakm-altjaryh",
            "execution-law",
            "electronic-transactions-law",
            "law-of-evidence",
        ),
        any_patterns=(
            "على الخارطة",
            "حساب الضمان",
            "حساب غير مستقل",
            "المطور",
            "غير المواصفات",
            "غيّر المواصفات",
            "غير المطور المواصفات",
            "دفعات في حساب الضمان",
            "استخدامها في مشروع آخر",
            "استخدامها في مشروع اخر",
        ),
        articles={
            "nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
            "real-estate-brokerage-law": {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 18, 19},
            "civil-transactions-law": {30, 94, 95, 109, 120, 136, 137, 138, 139, 321, 349},
            "nzam-altsjyl-alayny-llaqar": {2, 3, 4, 5},
            "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
            "nzam-altmwyl-alaqary": {1, 2, 3, 4, 5, 6, 7, 8},
            "nzam-mraqbh-shrkat-altmwyl": {1, 2, 3, 4, 5, 6, 7},
            "law-of-evidence": {2, 3, 25, 26, 55, 67},
        },
        priority=9.0,
    ),
    _bundle(
        "unit_owners_association_maintenance_axis_bundle",
        "unit_owners_association_maintenance_axis",
        ("nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha",),
        ("civil-transactions-law", "real-estate-brokerage-law", "law-of-evidence"),
        any_patterns=(
            "جمعية ملاك",
            "جمعيات الملاك",
            "اتحاد الملاك",
            "مصاريف الصيانة",
            "محاسبة المطور على مصاريف الصيانة",
            "إدارة الأجزاء المشتركة",
            "ادارة الاجزاء المشتركة",
        ),
        articles={
            "nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
            "civil-transactions-law": {94, 95, 109, 120, 136, 137, 138, 139},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.7,
    ),
    _bundle(
        "real_estate_finance_buyer_obligation_axis_bundle",
        "real_estate_finance_buyer_obligation_axis",
        ("nzam-altmwyl-alaqary",),
        ("nzam-mraqbh-shrkat-altmwyl", "civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "قرض عقاري",
            "تمويل عقاري",
            "ممول عقاري",
            "إيقاف الالتزام مع البنك",
            "ايقاف الالتزام مع البنك",
            "التزامه مع البنك",
            "أقساط البنك",
            "اقساط البنك",
        ),
        articles={
            "nzam-altmwyl-alaqary": {1, 2, 3, 4, 5, 6, 7, 8, 9, 10},
            "nzam-mraqbh-shrkat-altmwyl": {1, 2, 3, 4, 5, 6, 7, 8},
            "civil-transactions-law": {94, 95, 109, 120, 136, 137, 138, 139},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.6,
    ),
    _bundle(
        "real_estate_contribution_license_disclosure_bundle",
        "real_estate_contribution_license_disclosure",
        ("nzam-almsahmat-alaqaryh",),
        ("nzam-altsjyl-alayny-llaqar", "real-estate-brokerage-law", "civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "مساهمة عقارية",
            "مساهمات عقارية",
            "جمعت أموالاً من مستثمرين",
            "جمعت اموالا من مستثمرين",
            "وضوح في الترخيص",
            "الترخيص والإفصاح وإدارة المشروع",
            "الترخيص والافصاح وادارة المشروع",
        ),
        articles={
            "nzam-almsahmat-alaqaryh": {1, 2, 3, 4, 5, 6, 7, 8},
            "nzam-altsjyl-alayny-llaqar": {1, 2, 3, 4, 5},
            "real-estate-brokerage-law": {1, 2, 5, 11, 12},
            "civil-transactions-law": {94, 95, 120, 136, 137},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.4,
    ),
    _bundle(
        "real_estate_broker_earnest_money_ad_bundle",
        "real_estate_broker_earnest_money_ad",
        ("real-estate-brokerage-law",),
        ("civil-transactions-law", "law-of-evidence"),
        any_patterns=("وسيط عقاري", "الوساطة العقارية", "عربون", "عمولة", "إعلان عقاري", "اعلان عقاري", "مبلغ الحجز"),
        articles={
            "real-estate-brokerage-law": {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 18, 19},
            "civil-transactions-law": {94, 95, 321, 349, 361, 362},
        },
        priority=8.6,
    ),
    _bundle(
        "real_estate_title_registry_evidence_bundle",
        "real_estate_title_registry_evidence",
        ("nzam-altsjyl-alayny-llaqar",),
        ("civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "التسجيل العيني",
            "مسجل عينياً",
            "مسجل عينيا",
            "تصحيح السجل",
            "بيعين متعارضين",
            "ملكية عقار",
            "نزاع على ملكية عقار",
        ),
        priority=9.3,
    ),
    _bundle(
        "bankruptcy_preference_employees_bundle",
        "bankruptcy_preference_employees",
        ("nzam-aliflas",),
        ("bankruptcy-implementing-regulation", "companies-law"),
        any_patterns=(
            "إفلاس",
            "افلاس",
            "شركة متعثرة",
            "الشركة المتعثرة",
            "المدين المتعثر",
            "تعثر مالي",
            "تعثر الشركة عن السداد",
            "تعثر عن السداد",
            "تعثر في سداد الديون",
            "توقفت عن السداد",
            "توقف عن السداد",
            "توقفت عن سداد ديونها",
            "توقف عن سداد ديونه",
            "توقفت الشركة عن سداد ديونها",
            "توقفت عن الوفاء بديونها",
            "عجزت عن سداد ديونها",
            "تعثر الشركة",
            "تعثر الشركة عن السداد",
            "إعادة التنظيم المالي",
            "اعادة التنظيم المالي",
            "مورد قريب",
            "رواتب الموظفين",
        ),
        excluded_patterns=(
            "تعثر في الإنجاز",
            "تعثر في الانجاز",
            "تعثرت في الإنجاز",
            "تعثرت في الانجاز",
            "تعثر في عقد مشروع",
            "تعثرت في عقد مشروع",
            "تعثر عقد مشروع",
            "تعثر مشروع خاص",
            "تعثرت في مشروع خاص",
            "تعثرت الشركة في مشروع خاص",
            "تعثرت في تنفيذ مشروع",
            "تعثر في تنفيذ مشروع",
            "تأخر دفعات المالك",
            "تاخر دفعات المالك",
            "ارتفاع أسعار المواد",
            "ارتفاع اسعار المواد",
            "عقدًا مع جهة خاصة",
            "عقدا مع جهة خاصة",
            "عقد مع جهة خاصة",
            "دون وجود تعثر",
            "دون تعثر",
            "لا يوجد تعثر",
            "من غير تعثر",
            "دون وجود إفلاس",
            "دون وجود افلاس",
            "دون إفلاس",
            "دون افلاس",
            "لا يوجد إفلاس",
            "لا يوجد افلاس",
            "من غير إفلاس",
            "من غير افلاس",
        ),
        articles={
            "nzam-aliflas": {1, 2, 4, 5, 7, 42, 45, 46, 47, 196, 200, 201, 205, 210, 211},
            "bankruptcy-implementing-regulation": {1, 3, 4, 5, 10, 13, 14, 16, 20, 24, 26, 42, 44, 45, 46, 47, 53, 54, 76, 77, 78, 80, 89},
            "companies-law": {26, 27, 28},
        },
        priority=9.2,
    ),
    _bundle(
        "electronic_transactions_evidence_support_bundle",
        "electronic_transactions_evidence_support",
        ("electronic-transactions-law",),
        ("law-of-evidence", "civil-transactions-law"),
        any_patterns=(
            "توقيع إلكتروني",
            "توقيع الكتروني",
            "سجل إلكتروني",
            "سجل الكتروني",
            "رسائل واتساب",
            "رسالة واتساب",
            "محادثات واتساب",
            "عبر واتساب",
            "إشعار واتساب",
            "اشعار واتساب",
            "إبلاغ واتساب",
            "ابلاغ واتساب",
            "بريد إلكتروني",
            "بريد الكتروني",
            "رسائل بريد",
        ),
        excluded_patterns=("فاتورة ضريبية", "الرقم الضريبي", "ضريبة القيمة"),
        articles={
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
            "law-of-evidence": {55, 56, 57},
            "civil-transactions-law": {94, 95, 120, 136},
        },
        priority=6.0,
    ),
    _bundle(
        "labor_contract_wages_dues_compliance_bundle",
        "labor_contract_wages_dues_compliance",
        ("labor-law",),
        (
            "labor-implementing-regulation",
            "labor-violations-penalties-table",
            "wage-protection-rules",
            "labor-contract-documentation-rules",
        ),
        any_patterns=(
            "عقد عمل",
            "توثيق العقد",
            "توثيق العقود",
            "لم توثق",
            "منصة قوى",
            "منصه قوي",
            "حماية الأجور",
            "حماية الاجور",
            "تأخر الرواتب",
            "تاخر الرواتب",
            "تأخرت الرواتب",
            "أخرت الرواتب",
            "اخرت الرواتب",
            "الرواتب شهرين",
            "فترة التجربة",
            "فتره التجربه",
            "مددت فترة التجربة",
            "مددت فتره التجربه",
            "محدد المدة",
            "محدد المده",
            "قبل نهايته",
            "ضعف الأداء",
            "ضعف الاداء",
            "العمل عن بعد",
            "عن بعد دون توثيق",
            "شرط عدم منافسة",
            "شرط عدم منافسه",
            "شرط عدم المنافسة",
            "شرط عدم المنافسه",
            "عدم منافسة",
            "عدم منافسه",
            "عدم المنافسة",
            "عدم المنافسه",
            "لمدة سنتين",
            "لمده سنتين",
            "شهادة خبرة",
            "شهاده خبره",
            "شهادة خدمة",
            "شهاده خدمه",
            "نسخة من العقد",
            "نسخه من العقد",
            "حسم من الأجر",
            "حسم من الاجر",
            "خصم من الأجر",
            "خصم من الاجر",
            "مكافأة نهاية الخدمة",
            "مكافاه نهايه الخدمه",
            "جدول المخالفات",
            "عامل في مقاول من الباطن",
            "صاحب العمل المسجل",
            "لم يتسلم أجره",
            "لم يتسلم اجره",
            "عامل غير سعودي",
            "أجور متأخرة",
            "اجور متاخرة",
            "أجور متاخره",
            "اجور متاخره",
            "بدل إجازات",
            "بدل اجازات",
            "مخالفات صاحب العمل",
            "أنهي عقده",
            "انهي عقده",
            "أُنهي عقده",
            "تأخرت الشركة في دفع الرواتب",
            "تاخرت الشركة في دفع الرواتب",
            "تأخر دفع الرواتب",
            "تاخر دفع الرواتب",
            "إجازات بدون أجر",
            "اجازات بدون اجر",
            "نقلت عددًا منهم إلى مدينة أخرى",
            "نقلت عددا منهم الى مدينة اخرى",
            "نقل الموظفين إلى مدينة أخرى",
            "نقل الموظفين الى مدينة اخرى",
            "خصم مبالغ من الرواتب",
            "تخصم مبالغ من الرواتب",
            "بحجة السكن والنقل",
            "عمالة على غير المهن",
            "غير المهن المسجلة",
            "تجديد الإقامات",
            "تجديد الاقامات",
            "إقامات",
            "اقامات",
        ),
        articles={
            "labor-law": {12, 13, 32, 33, 36, 37, 38, 39, 40, 50, 51, 52, 53, 54, 58, 64, 75, 76, 77, 80, 83, 84, 88, 90, 92, 94},
        },
        priority=9.6,
    ),
    _bundle(
        "labor_injury_social_insurance_safety_bundle",
        "labor_injury_social_insurance_safety",
        ("labor-law", "nzam-altamynat-alajtmaayh"),
        ("labor-implementing-regulation", "labor-violations-penalties-table", "law-of-evidence"),
        any_patterns=(
            "إصابة عمل",
            "اصابة عمل",
            "إصابة مهنية",
            "اصابة مهنية",
            "الحقوق التأمينية",
            "الحقوق التامينية",
            "التأمينات الاجتماعية",
            "التامينات الاجتماعية",
            "شروط السلامة المهنية",
            "السلامة المهنية",
            "تأخر صاحب العمل في الإبلاغ",
            "تاخر صاحب العمل في الابلاغ",
            "أصيب في الموقع",
            "اصيب في الموقع",
            "أصيب العامل",
            "اصيب العامل",
            "أصيب عمال",
            "اصيب عمال",
            "سقوط معدّة",
            "سقوط معدة",
            "سقوط سقالة",
            "سقوط السقالة",
            "إجراءات السلامة لم تكن مكتملة",
            "اجراءات السلامة لم تكن مكتملة",
            "لم يحصل على تدريب كاف",
            "لم يحصل على تدريب كافٍ",
            "مواقع إنشائية خطرة",
            "مواقع انشائية خطرة",
            "موقع إنشائي خطر",
            "موقع انشائي خطر",
        ),
        articles={
            "labor-law": {121, 122, 123, 124, 125, 126, 127, 133, 134, 135, 136, 137, 138, 139},
            "nzam-altamynat-alajtmaayh": {1, 7, 8, 59},
        },
        priority=9.5,
    ),
    _bundle(
        "bankruptcy_employee_wage_claims_bundle",
        "bankruptcy_employee_wage_claims",
        ("nzam-aliflas", "labor-law"),
        ("bankruptcy-implementing-regulation", "companies-law"),
        any_patterns=(
            "رواتب الموظفين",
            "رواتب العمال",
            "أجور الموظفين",
            "اجور الموظفين",
            "أجور العمال",
            "اجور العمال",
            "حقوق الموظفين والدائنين",
            "حقوق العمال والدائنين",
            "تأخر صرف رواتب الموظفين",
            "تاخر صرف رواتب الموظفين",
            "تأخر الأجور",
            "تاخر الاجور",
            "الموظفون ضمن الدائنين",
            "العمال ضمن الدائنين",
        ),
        articles={
            "nzam-aliflas": {1, 4, 5, 42, 46, 47, 48, 49, 50, 53, 54, 55, 132, 133},
            "labor-law": {84, 88, 90, 94},
        },
        priority=9.7,
    ),
    _bundle(
        "social_insurance_false_wage_and_ghost_workers_bundle",
        "social_insurance_false_wage_and_ghost_workers",
        ("nzam-altamynat-alajtmaayh",),
        ("labor-law", "law-of-evidence"),
        any_patterns=(
            "تسجيله في التأمينات براتب أقل",
            "تسجيله في التامينات براتب اقل",
            "مسجل في التأمينات براتب أقل",
            "مسجل في التامينات براتب اقل",
            "راتب أقل من راتبه الحقيقي",
            "راتب اقل من راتبه الحقيقي",
            "التأمينات بأجور أقل",
            "التامينات باجور اقل",
            "سجلت بعض الموظفين في التأمينات",
            "سجلت بعض الموظفين في التامينات",
            "الموظفين في التأمينات بأجور أقل",
            "الموظفين في التامينات باجور اقل",
            "أسماء سعوديين آخرين",
            "اسماء سعوديين اخرين",
            "أسماء سعوديين في ملفات الشركة",
            "اسماء سعوديين في ملفات الشركة",
            "دون حضور فعلي للعمل",
            "لا يعمل لمصلحته",
            "موظف سعودي من تسجيله في التأمينات",
            "موظف سعودي من تسجيله في التامينات",
        ),
        articles={
            "nzam-altamynat-alajtmaayh": {1, 7, 8, 9, 59},
            "labor-law": {90, 94},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.8,
    ),
    _bundle(
        "gcc_social_insurance_extension_bundle",
        "gcc_social_insurance_extension",
        ("alnzam-almwhd-lmd-alhmayh-altamynyh-lmwatny-dwl-mjls-altaawn-ldwl-alkhlyj-alarbyh-alaamlyn-fy-gh",),
        ("nzam-altamynat-alajtmaayh", "nzam-altqaad-almdny", "labor-law", "law-of-evidence"),
        any_patterns=(
            "مد الحماية التأمينية",
            "مد الحماية التامينية",
            "مواطني دول مجلس التعاون",
            "العاملين في غير دولهم",
            "غير دولهم في أي دولة",
            "دول مجلس التعاون لدول الخليج",
            "أجور غير حقيقية",
            "اجور غير حقيقية",
        ),
        articles={
            "alnzam-almwhd-lmd-alhmayh-altamynyh-lmwatny-dwl-mjls-altaawn-ldwl-alkhlyj-alarbyh-alaamlyn-fy-gh": {1, 2, 3, 4, 5, 6, 7, 8},
            "nzam-altamynat-alajtmaayh": {1, 7, 8, 9, 59},
            "nzam-altqaad-almdny": {1, 2, 3},
            "labor-law": {90, 94},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.7,
    ),
    _bundle(
        "private_project_delay_payment_material_cost_claim_bundle",
        "private_project_delay_payment_material_cost_claim",
        ("civil-transactions-law",),
        ("nzam-almhakm-altjaryh", "law-of-evidence"),
        any_patterns=(
            "عقدًا مع جهة خاصة",
            "عقدا مع جهة خاصة",
            "عقد مع جهة خاصة",
            "عقد مشروع خاص",
            "عقدًا لمشروع خاص",
            "عقدا لمشروع خاص",
            "تعثرت في الإنجاز",
            "تعثرت في الانجاز",
            "تعثر في الإنجاز",
            "تعثر في الانجاز",
            "تعثرت في عقد مشروع",
            "تعثر في عقد مشروع",
            "تعثرت في تنفيذ مشروع",
            "تعثر في تنفيذ مشروع",
            "يطالب بالتعويض",
            "المطالبة بالتعويض",
            "تأخر دفعات المالك",
            "تاخر دفعات المالك",
            "دفعات المالك",
            "ارتفاع أسعار المواد",
            "ارتفاع اسعار المواد",
            "ارتفاع المواد",
            "ارتفاع تكلفة المواد",
            "ارتفاع سعر المواد",
            "طريق المطالبة",
        ),
        excluded_patterns=("جهة حكومية", "منافسة حكومية", "عقد حكومي", "متعاقد حكومي"),
        articles={
            "civil-transactions-law": {94, 95, 109, 120, 128, 136, 137, 138, 139, 461, 463, 465, 466, 473, 475, 476},
            "nzam-almhakm-altjaryh": {16, 17, 19},
            "law-of-evidence": {2, 3, 25, 26, 55, 67},
        },
        priority=9.7,
    ),
    _bundle(
        "listed_company_capital_increase_cma_bundle",
        "listed_company_capital_increase_cma",
        ("companies-law", "nzam-alswq-almalyh"),
        (
            "companies-implementing-regulation",
            "cma-corporate-governance-regulations",
            "cma-continuing-obligations-rules",
            "cma-securities-offering-rules",
        ),
        any_patterns=(
            "شركة مساهمة مدرجة",
            "الشركات المدرجة",
            "جمعية غير عادية",
            "جمعية عامة غير عادية",
            "زيادة رأس المال",
            "زيادة راس المال",
            "منح أسهم",
            "منح اسهم",
            "الالتزامات المستمرة",
            "حوكمة الشركات",
        ),
        articles={
            "companies-law": {88, 89, 90, 91, 92, 93, 94, 103, 105, 107, 112, 136, 137, 274},
            "nzam-alswq-almalyh": {4, 5, 6, 23, 25, 36, 40, 41, 42, 45, 49, 50},
        },
        priority=9.6,
    ),
    _bundle(
        "cma_private_placement_exempt_offering_bundle",
        "cma_private_placement_exempt_offering",
        ("nzam-alswq-almalyh",),
        ("cma-continuing-obligations-rules", "cma-securities-offering-rules"),
        any_patterns=(
            "طرح خاص",
            "الطرح الخاص",
            "طرحاً خاصاً",
            "طرحا خاصا",
            "طرح أوراق مالية طرحاً خاصاً",
            "طرح اوراق مالية طرحا خاصا",
            "أدوات دين",
            "ادوات دين",
            "الطرح المستثنى",
            "طرح مستثنى",
            "مستند طرح",
            "إشعار الطارح",
            "اشعار الطارح",
            "إشعار الطرح الخاص",
            "اشعار الطرح الخاص",
            "مؤسسة سوق مالية",
            "ملحق التسعير",
        ),
        articles={
            "nzam-alswq-almalyh": {5, 6, 23, 25, 49, 50},
            "cma-continuing-obligations-rules": {3, 6, 8, 9, 10, 11, 12},
            "cma-securities-offering-rules": {3, 6, 8, 9, 10, 11, 12},
        },
        priority=9.85,
    ),
    _bundle(
        "listed_company_disclosure_insider_related_party_disputes_bundle",
        "listed_company_disclosure_insider_related_party_disputes",
        ("companies-law", "nzam-alswq-almalyh"),
        (
            "companies-implementing-regulation",
            "cma-corporate-governance-regulations",
            "cma-continuing-obligations-rules",
            "cma-securities-offering-rules",
            "law-of-evidence",
        ),
        any_patterns=(
            "شركة مساهمة سعودية مدرجة",
            "شركة مساهمة مدرجة",
            "مدرجة في السوق المالية",
            "الشركات المدرجة",
            "نتائج مالية",
            "تصحيح جوهري",
            "هبوط السهم",
            "معلومات داخلية",
            "التداول بناء على معلومات داخلية",
            "التداول بناءً على معلومات داخلية",
            "باع أحد أعضاء مجلس الإدارة",
            "باع احد اعضاء مجلس الادارة",
            "أعضاء مجلس الإدارة كمية كبيرة من أسهمه",
            "اعضاء مجلس الادارة كمية كبيرة من اسهمه",
            "عقد توريد كبير",
            "حصة غير مباشرة",
            "طرف ذي علاقة",
            "تعارض المصالح",
            "حماية المساهمين",
            "المساهمين الأقلية",
            "المساهمين الاقلية",
            "منازعات الأوراق المالية",
            "منازعات الاوراق المالية",
        ),
        excluded_patterns=("منافسة حكومية", "المنافسات والمشتريات", "المشتريات الحكومية", "لجنة الفحص", "ترسية", "الترسية", "المحتوى المحلي"),
        articles={
            "companies-law": {26, 27, 28, 88, 89, 90, 91, 92, 103, 112, 136, 137},
            "nzam-alswq-almalyh": {5, 6, 20, 23, 25, 49, 50, 55, 56, 57, 58, 59},
            "cma-corporate-governance-regulations": {1, 2, 4, 5, 6, 12, 19, 20, 21, 25, 28},
            "cma-continuing-obligations-rules": {32, 42, 63, 64, 65, 66, 71},
            "cma-securities-offering-rules": {32, 42, 63, 64, 65, 66, 71},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.9,
    ),
    _bundle(
        "electronic_enforcement_evidence_costs_bundle",
        "electronic_enforcement_evidence_costs",
        ("execution-law", "law-of-evidence", "electronic-transactions-law"),
        ("execution-implementing-regulation", "nzam-altkalyf-alqdayyh", "law-of-sharia-procedure"),
        any_patterns=(
            "سند إلكتروني",
            "سند الكتروني",
            "سند لأمر إلكتروني",
            "سند لامر الكتروني",
            "سند لأمر",
            "سند لامر",
            "السند التنفيذي",
            "طلب تنفيذ",
            "تقديمه للتنفيذ",
            "محرر إلكتروني",
            "محرر الكتروني",
            "توقيع إلكتروني",
            "توقيع الكتروني",
            "موقّع رقمياً",
            "موقع رقميا",
            "إنكار المدين للتوقيع",
            "انكار المدين للتوقيع",
            "التكاليف القضائية",
        ),
        excluded_patterns=("إفلاس", "افلاس", "إعادة التنظيم المالي", "اعادة التنظيم المالي", "مطالبة تجارية", "عرض سعر"),
        articles={
            "execution-law": {1, 2, 3, 4, 6, 9, 15, 16, 34, 46, 72},
            "law-of-evidence": {2, 3, 10, 25, 26, 29, 30, 55, 56, 57},
            "electronic-transactions-law": {5, 6, 7, 8, 9, 12, 14, 23, 24},
            "nzam-altkalyf-alqdayyh": {2, 3, 7, 8, 11, 12, 14, 16},
            "law-of-sharia-procedure": {41, 42, 55, 56, 72},
        },
        priority=9.6,
    ),
    _bundle(
        "arbitration_award_enforcement_annulment_bundle",
        "arbitration_award_enforcement_annulment",
        ("nzam-althkym",),
        ("execution-law", "law-of-evidence", "civil-transactions-law", "nzam-almhakm-altjaryh"),
        any_patterns=(
            "شرط تحكيم",
            "حكم تحكيم",
            "بطلان حكم التحكيم",
            "بطلانه أو تنفيذه",
            "بطلانه او تنفيذه",
            "تنفيذ حكم التحكيم",
            "دعوى بطلان التحكيم",
        ),
        priority=9.4,
    ),
    _bundle(
        "procurement_grievance_award_board_bundle",
        "procurement_grievance_award_board",
        ("government-tenders-and-procurement-law",),
        (
            "government-procurement-implementing-regulation",
            "procurement-conflict-of-interest-regulation",
            "procurement-conduct-ethics-regulation",
            "nzam-almrafaat-amam-dywan-almzalm",
            "nzam-dywan-almzalm",
            "nzam-altnfydh-amam-dywan-almzalm",
        ),
        any_patterns=(
            "تظلم",
            "اعتراض",
            "ترسية",
            "تقييم العروض",
            "فحص العروض",
            "عضو لجنة الفحص",
            "تعارض مصالح",
            "قريب من مدير الشركة",
            "المحتوى المحلي",
            "متطلبات المحتوى المحلي",
            "لائحة تفضيل المحتوى المحلي",
            "المنشآت الصغيرة والمتوسطة",
            "الشركات المدرجة",
            "إلغاء الترسية",
            "الغاء الترسية",
            "رفض التظلم",
            "ديوان المظالم",
            "المحكمة الإدارية",
            "المحكمه الاداريه",
        ),
        all_patterns=("منافسة",),
        articles={
            "government-tenders-and-procurement-law": {37, 40, 46, 50, 51, 52, 53, 74, 76, 78, 86, 87, 88, 96},
            "government-procurement-implementing-regulation": {74, 75, 76, 77, 78, 87, 88, 96, 154},
            "procurement-conflict-of-interest-regulation": {5, 6, 7, 8, 9, 10, 11},
            "procurement-conduct-ethics-regulation": {2, 4, 5},
            "nzam-almrafaat-amam-dywan-almzalm": {1, 3, 5, 9, 13, 14},
            "nzam-dywan-almzalm": {1, 8, 13},
            "nzam-altnfydh-amam-dywan-almzalm": {1, 2, 3, 4, 5},
        },
        priority=9.6,
    ),
    _bundle(
        "procurement_grievance_award_compound_bundle",
        "procurement_grievance_award_compound",
        ("government-tenders-and-procurement-law",),
        (
            "government-procurement-implementing-regulation",
            "procurement-conflict-of-interest-regulation",
            "procurement-conduct-ethics-regulation",
            "nzam-almrafaat-amam-dywan-almzalm",
            "nzam-dywan-almzalm",
            "nzam-altnfydh-amam-dywan-almzalm",
        ),
        any_patterns=(
            "متنافس حكومي",
            "منافس حكومي",
            "مورد حكومي اعترض",
            "متنافس اعترض",
            "مقدم عرض اعترض",
            "صاحب العرض اعترض",
            "صاحب عطاء اعترض",
            "اعترض على الترسية",
            "اعتراض على الترسية",
            "اعترض على تقييم العروض",
            "اعتراض على تقييم العروض",
            "مسار التظلم بعد رفض الجهة",
            "رفض الجهة التظلم",
            "بعد رفض الجهة",
            "عضو لجنة الفحص",
            "تعارض مصالح",
            "قريب من مدير الشركة",
            "المحتوى المحلي",
            "متطلبات المحتوى المحلي",
            "لائحة تفضيل المحتوى المحلي",
            "إلغاء الترسية",
            "الغاء الترسية",
            "توقيع جزاء",
        ),
        articles={
            "government-tenders-and-procurement-law": {37, 40, 46, 50, 51, 52, 53, 74, 76, 78, 86, 87, 88, 96},
            "government-procurement-implementing-regulation": {74, 75, 76, 77, 78, 87, 88, 96, 154},
            "procurement-conflict-of-interest-regulation": {5, 6, 7, 8, 9, 10, 11},
            "procurement-conduct-ethics-regulation": {2, 4, 5},
            "nzam-almrafaat-amam-dywan-almzalm": {1, 2, 3, 4, 5, 9, 13, 14},
            "nzam-dywan-almzalm": {1, 8, 13},
            "nzam-altnfydh-amam-dywan-almzalm": {1, 2, 3, 4, 5},
        },
        priority=9.8,
    ),
    _bundle(
        "commercial_electronic_claim_evidence_bundle",
        "commercial_electronic_claim_evidence",
        ("civil-transactions-law", "law-of-evidence", "electronic-transactions-law"),
        ("nzam-almhakm-altjaryh", "nzam-altkalyf-alqdayyh"),
        any_patterns=(
            "مطالبة تجارية",
            "عرض سعر",
            "فواتير ورسائل",
            "ينكر الالتزام",
            "يطعن في صحة المراسلات",
            "منشأتين",
            "دعوى تجارية",
            "المحكمة التجارية",
            "المحكمه التجاريه",
            "عدم الاختصاص",
        ),
        excluded_patterns=("طلب تنفيذ", "السند التنفيذي", "سند تنفيذي"),
        articles={
            "civil-transactions-law": {94, 95, 120, 128, 136, 137, 138},
            "law-of-evidence": {2, 3, 25, 26, 29, 30, 55, 56, 57},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
            "nzam-almhakm-altjaryh": {16, 17, 19},
            "nzam-altkalyf-alqdayyh": {2, 3, 7, 8, 11, 12},
        },
        priority=9.7,
    ),
    _bundle(
        "commercial_papers_evidence_courts_bundle",
        "commercial_papers_evidence_courts",
        ("nzam-alawraq-altjaryh",),
        ("law-of-evidence", "nzam-almhakm-altjaryh", "execution-law", "execution-implementing-regulation", "electronic-transactions-law"),
        any_patterns=(
            "كمبيالة",
            "سند تجاري",
            "أوراق تجارية",
            "اوراق تجارية",
            "تزوير التوقيع",
            "فوات الميعاد",
            "ميعاد الرجوع",
        ),
        priority=9.5,
    ),
    _bundle(
        "civil_sale_defect_evidence_bundle",
        "civil_sale_defect_evidence",
        ("civil-transactions-law",),
        ("law-of-evidence", "commercial-fraud-law", "electronic-transactions-law"),
        any_patterns=(
            "عيب خفي",
            "عيباً خفياً",
            "عيبا خفيا",
            "عيب في المبيع",
            "غير مطابق للوصف",
            "رفض الاسترجاع",
            "الشروط في الموقع تمنع الاسترداد",
            "الفسخ أو إنقاص الثمن",
            "انقاص الثمن",
            "تعويض عن العيب",
            "أخفاه البائع",
            "سيارة مستعملة",
        ),
        articles={
            "civil-transactions-law": {94, 95, 109, 120, 136, 321, 349},
            "law-of-evidence": {55, 56, 57},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
        },
        priority=9.2,
    ),
    _bundle(
        "civil_tort_compensation_evidence_bundle",
        "civil_tort_compensation_evidence",
        ("civil-transactions-law",),
        ("law-of-evidence", "law-of-sharia-procedure"),
        any_patterns=("فعل ضار", "تعويض عن فعل", "إثبات الضرر", "اثبات الضرر", "علاقة السببية", "الضرر وعلاقة السببية", "خسائر مالية"),
        articles={
            "civil-transactions-law": {94, 95, 120, 136, 137, 138},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.2,
    ),
    _bundle(
        "commercial_franchise_register_tradename_bundle",
        "commercial_franchise_register_tradename",
        ("nzam-alamtyaz-altjary",),
        ("nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh", "companies-law", "civil-transactions-law", "nzam-almhakm-altjaryh"),
        any_patterns=("امتياز تجاري", "اتفاقية امتياز", "وثيقة الإفصاح", "وثيقة الافصاح", "قيد الامتياز", "تشغيل علامة", "فرنشايز", "مانح الامتياز"),
        articles={
            "nzam-alamtyaz-altjary": {1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 17, 19, 22, 25},
            "nzam-alsjl-altjary": {2, 3, 4, 5, 6, 7, 10},
            "nzam-alasmaa-altjaryh": {1, 2, 3, 5, 6, 7, 10, 14},
            "nzam-alalamat-altjaryh": {1, 2, 3, 10, 16, 20},
        },
        priority=9.6,
    ),
    _bundle(
        "commercial_agency_register_courts_bundle",
        "commercial_agency_register_courts",
        ("nzam-alwkalat-altjaryh",),
        ("civil-transactions-law", "nzam-almhakm-altjaryh", "nzam-alsjl-altjary"),
        any_patterns=(
            "وكيل تجاري",
            "وكالة تجارية",
            "الوكالة التجارية",
            "عقد الوكالة",
            "تسجيل الوكالة",
            "شطب الوكالة",
            "وكيل تجاري حصري",
        ),
        priority=9.4,
    ),
    _bundle(
        "trade_name_register_trademark_bundle",
        "trade_name_register_trademark",
        ("nzam-alasmaa-altjaryh", "nzam-alsjl-altjary"),
        ("nzam-alalamat-altjaryh",),
        any_patterns=("اسم تجاري", "الأسماء التجارية", "الاسم التجاري", "السجل التجاري", "تحديث بيانات السجل", "نشاط السجل"),
        articles={
            "nzam-alasmaa-altjaryh": {1, 2, 3, 5, 6, 7, 9, 10, 13, 14},
            "nzam-alsjl-altjary": {2, 3, 4, 5, 6, 7, 10},
            "nzam-alalamat-altjaryh": {1, 2, 3, 10, 16, 20},
        },
        priority=9.0,
    ),
    _bundle(
        "commercial_concealment_register_bundle",
        "commercial_concealment_register",
        ("nzam-mkafhh-altstr",),
        ("nzam-alsjl-altjary", "companies-law", "law-of-evidence"),
        any_patterns=(
            "تستر",
            "التستر",
            "وافد يدير",
            "غير سعودي يدير",
            "مكّن غير سعودي",
            "مكن غير سعودي",
            "باسم مواطن",
            "باسمه وسجله التجاري",
            "تشغيل متجر باسمه",
            "الإدارة الفعلية",
            "الادارة الفعلية",
            "حسابه البنكي مقابل نسبة",
            "فواتير وتحويلات",
            "يتحكم بالإيرادات",
            "يتحكم بالايرادات",
            "المدير الفعلي",
            "مدير فعلي غير سعودي",
            "شخص غير سعودي يدير",
            "شخص غير سعودي، يدير",
            "يدير الحسابات والعقود والمشتريات",
            "يتصرف كمالك فعلي",
            "مالك سعودي في السجلات",
            "يظهر مالك سعودي",
            "يظهر مالك سعودي في السجلات",
        ),
        articles={
            "nzam-mkafhh-altstr": {1, 2, 3, 4, 6, 10, 11, 12, 14, 15},
            "nzam-alsjl-altjary": {2, 3, 4, 5, 6, 7, 10},
            "companies-law": {26, 27, 28},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.5,
    ),
    _bundle(
        "civil_finance_lease_movable_security_bundle",
        "civil_finance_lease_movable_security",
        ("nzam-alayjar-altmwyly", "nzam-dman-alhqwq-balamwal-almnqwlh"),
        ("civil-transactions-law", "law-of-evidence", "execution-law"),
        any_patterns=(
            "تمويل إيجاري",
            "تمويل ايجاري",
            "عقد تمويل إيجاري",
            "ضمان على منقولات",
            "الأموال المنقولة",
            "الاموال المنقولة",
            "رهنت معدات",
            "رهنت مخزونا",
            "دائن مضمون",
            "الأولوية والتنفيذ",
            "الاولوية والتنفيذ",
            "نزاع بين دائن مضمون",
            "ترتيب حقه",
            "مزاحمة دائنين",
        ),
        articles={
            "civil-transactions-law": {94, 95, 120, 128, 136, 137, 138},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "execution-law": {1, 2, 3, 9, 34, 46},
        },
        priority=9.2,
    ),
    _bundle(
        "administrative_grievance_diwan_bundle",
        "administrative_grievance_diwan",
        ("nzam-almrafaat-amam-dywan-almzalm",),
        ("law-of-evidence", "civil-transactions-law"),
        any_patterns=("قرار إداري", "قرار اداري", "سحب ترخيص", "إلغاء القرار", "الغاء القرار", "المحكمة الإدارية", "المحكمه الاداريه", "ديوان المظالم", "جهة إدارية"),
        excluded_patterns=("منافسة حكومية", "ترسية", "تقييم العروض"),
        articles={
            "nzam-almrafaat-amam-dywan-almzalm": {1, 2, 3, 4, 5, 13, 14},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "civil-transactions-law": {94, 95, 120, 136, 137},
        },
        priority=9.4,
    ),
    _bundle(
        "administrative_execution_diwan_bundle",
        "administrative_execution_diwan",
        ("nzam-altnfydh-amam-dywan-almzalm",),
        ("nzam-almrafaat-amam-dywan-almzalm",),
        any_patterns=("تنفيذ أمام ديوان المظالم", "تنفيذ امام ديوان المظالم", "حكم إداري نهائي", "حكم اداري نهائي", "لم تنفذه", "المحكوم له إجراءات التنفيذ"),
        priority=9.3,
    ),
    _bundle(
        "real_estate_finance_mortgage_execution_bundle",
        "real_estate_finance_mortgage_execution",
        ("nzam-altmwyl-alaqary", "nzam-alrhn-alaqary-almsjl"),
        ("execution-law", "execution-implementing-regulation", "civil-transactions-law"),
        any_patterns=("تمويل عقاري", "رهن عقاري", "رهن عقاري مسجل", "تنفيذ رهن", "تعثر العميل", "استرداد الأصل", "الضمان العقاري"),
        priority=9.3,
    ),
    _bundle(
        "municipal_building_license_code_bundle",
        "municipal_building_license_code",
        ("nzam-ttbyq-kwd-albnaa-alsawdy",),
        ("nzam-ijraaat-altrakhys-albldyh", "civil-transactions-law", "law-of-evidence"),
        any_patterns=("رخصة بلدية", "دون رخصة", "اشتراطات كود البناء", "سلامة إنشائية", "سلامه انشائيه", "خالف اشتراطات"),
        priority=9.0,
    ),
    _bundle(
        "municipal_hazardous_activity_license_grievance_bundle",
        "municipal_hazardous_activity_license_grievance",
        ("nzam-ijraaat-altrakhys-albldyh", "nzam-alanshth-almqlqh-llrahh-aw-alkhtrh-aw-almdrh-balshh-aw-albyyh"),
        ("nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"),
        any_patterns=(
            "نشاطاً مقلقاً للراحة",
            "نشاطا مقلقا للراحة",
            "نشاط مقلق للراحة",
            "مضراً بالصحة",
            "مضرا بالصحة",
            "حي سكني دون ترخيص",
            "ترخيص بلدي واضح",
            "البلدية أوقفت النشاط",
            "البلدية اوقفت النشاط",
            "فرضت غرامة",
            "دون ترخيص بلدي",
        ),
        priority=9.6,
    ),
    _bundle(
        "vat_customs_zakat_bundle",
        "vat_customs_zakat",
        ("nzam-drybh-alqymh-almdafh",),
        ("zatca-vat-implementing-regulation",),
        any_patterns=("مكان التوريد", "توريد خارج المملكة", "التصدير", "الاستيراد", "القيمة الجمركية", "الرسوم الجمركية", "ربط زكوي", "الزكاة", "الضريبة عند الاستيراد"),
        articles={
            "nzam-drybh-alqymh-almdafh": {1, 2, 3, 10, 14, 22, 40, 41, 42},
            "zatca-vat-implementing-regulation": {15, 16, 17, 18, 25, 26, 33, 53, 54},
        },
        priority=9.1,
    ),
    _bundle(
        "real_estate_transaction_tax_vat_bundle",
        "real_estate_transaction_tax_vat",
        ("nzam-drybh-altsrfat-alaqaryh", "nzam-drybh-alqymh-almdafh"),
        ("zatca-vat-implementing-regulation", "civil-transactions-law"),
        any_patterns=(
            "ضريبة التصرفات العقارية",
            "ضريبة التصرفات العقاريه",
            "بيع عقار تجاري",
            "باعت عقاراً تجارياً",
            "باعت عقارا تجاريا",
            "عقاراً تجارياً",
            "عقارا تجاريا",
            "عقار تجاري",
            "تصرف عقاري",
            "تصرفات عقارية",
            "تصرفات عقاريه",
            "إفراغ عقار",
            "افراغ عقار",
            "تجهيزات وخدمات إدارة",
            "تجهيزات وخدمات اداره",
            "ضريبة قيمة مضافة أو كلاهما",
            "ضريبة قيمة مضافة او كلاهما",
        ),
        priority=9.6,
    ),
    _bundle(
        "customs_import_vat_bundle",
        "customs_import_vat",
        ("nzam-qanwn-aljmark-almwhd-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh", "nzam-drybh-alqymh-almdafh"),
        ("zatca-vat-implementing-regulation",),
        any_patterns=("مستورد", "المنفذ الجمركي", "القيمة الصحيحة", "الرسوم", "ضريبة عند الاستيراد", "الاستيراد"),
        priority=9.4,
    ),
    _bundle(
        "zakat_vat_assessment_bundle",
        "zakat_vat_assessment",
        ("nzam-jbayh-alzkah", "nzam-drybh-alqymh-almdafh"),
        ("zatca-vat-implementing-regulation",),
        any_patterns=("ربط زكوي", "الزكاة", "الاعتراض على ربط", "مطالبات vat", "مطالبات ضريبة القيمة"),
        priority=9.3,
    ),
    _bundle(
        "pdpl_biometrics_surveillance_bundle",
        "pdpl_biometrics_surveillance",
        ("personal-data-protection-law",),
        ("pdpl-implementing-regulation", "nzam-astkhdam-kamyrat-almraqbh-alamnyh", "anti-cybercrime-law"),
        any_patterns=("تعرف على الوجه", "التعرف على الوجه", "كاميرات", "تحليل حركة الزوار", "بيانات حيوية", "بصمات", "لوحات إفصاح"),
        priority=9.2,
    ),
    _bundle(
        "banking_fraud_aml_cyber_bundle",
        "banking_fraud_aml_cyber",
        ("nzam-mraqbh-albnwk", "anti-money-laundering-law", "nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh"),
        ("nzam-albnk-almrkzy-alsawdy", "anti-cybercrime-law", "personal-data-protection-law"),
        any_patterns=("تحويلات غير مصرح", "حساب بنكي", "احتيال", "غسل أموال", "سحب من حسابه", "رابط بنكي وهمي", "عملية مصرفية"),
        priority=9.4,
    ),
    _bundle(
        "aml_suspicious_transactions_beneficial_owner_bundle",
        "aml_suspicious_transactions_beneficial_owner",
        ("anti-money-laundering-law",),
        ("nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh", "nzam-mraqbh-albnwk"),
        any_patterns=(
            "تحويلات مجزأة",
            "تحويلات مجزاه",
            "ملاكاً مستفيدين",
            "ملاكا مستفيدين",
            "الملاك المستفيدين",
            "ملاك مستفيدين",
            "الإبلاغ عن الاشتباه",
            "الابلاغ عن الاشتباه",
            "حفظ السجلات",
            "منشأة مالية لاحظت",
            "منشاه مالية لاحظت",
        ),
        priority=9.7,
    ),
    _bundle(
        "payment_wallet_unauthorized_transfer_bundle",
        "payment_wallet_unauthorized_transfer",
        ("nzam-almdfwaat-wkhdmatha", "nzam-albnk-almrkzy-alsawdy"),
        ("nzam-mraqbh-albnwk", "personal-data-protection-law"),
        any_patterns=(
            "محفظة دفع إلكترونية",
            "محفظة دفع الكترونية",
            "محفظه دفع الكترونيه",
            "محفظة إلكترونية",
            "محفظه الكترونيه",
            "محفظة مدفوعات",
            "تحويلات غير مصرح بها",
            "تأخرت في معالجة الشكوى",
            "تاخرت في معالجة الشكوى",
            "الشركة المرخصة",
            "خدمات الدفع",
        ),
        priority=9.7,
    ),
    _bundle(
        "payments_finance_credit_insurance_bundle",
        "payments_finance_credit_insurance",
        ("nzam-almdfwaat-wkhdmatha", "nzam-mraqbh-shrkat-altmwyl", "nzam-almalwmat-alaytmanyh"),
        ("nzam-albnk-almrkzy-alsawdy", "civil-transactions-law", "law-of-evidence", "personal-data-protection-law", "pdpl-implementing-regulation"),
        any_patterns=(
            "محفظة مدفوعات",
            "أوامر الدفع",
            "شركة تمويل",
            "جدول السداد",
            "رسوم غير مفصح",
            "سجل ائتماني",
            "معلومات ائتمانية",
            "اشتر الآن وادفع لاحقًا",
            "اشتر الان وادفع لاحقا",
            "الدفع لاحقًا",
            "الدفع لاحقا",
            "مزود الدفع",
            "بوابة الدفع",
            "بيانات البطاقة",
            "بطاقة بنكية",
            "خصم تلقائي",
        ),
        priority=9.0,
    ),
    _bundle(
        "financial_important_institution_resolution_bundle",
        "financial_important_institution_resolution",
        ("nzam-maaljh-almnshat-almalyh-almhmh",),
        ("nzam-aliflas", "nzam-albnk-almrkzy-alsawdy"),
        any_patterns=("منشأة مالية مهمة", "منشاه ماليه مهمه", "الاستقرار المالي", "معالجة خاصة", "اضطراب مالي يهدد"),
        priority=9.5,
    ),
    _bundle(
        "ip_copyright_trademark_design_media_bundle",
        "ip_copyright_trademark_design_media",
        ("nzam-alalamat-altjaryh",),
        (
            "qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh",
            "e-commerce-law",
            "ecommerce-implementing-regulation",
            "commercial-fraud-law",
        ),
        any_patterns=("علامة مسجلة", "علامة مشابهة", "علامة مقلدة", "اسم قريب", "شعار مشابه", "منتجات تحمل علامة"),
        priority=9.2,
    ),
    _bundle(
        "copyright_digital_content_axis_bundle",
        "copyright_digital_content_axis",
        ("copyright-law",),
        ("law-of-evidence", "nzam-almhakm-altjaryh", "e-commerce-law", "ecommerce-implementing-regulation"),
        any_patterns=("حقوق المؤلف", "نسخت دورة", "نسخ محتوى", "دورة تدريبية", "مصنف", "محتوى محمي"),
        priority=9.4,
    ),
    _bundle(
        "industrial_design_patent_bundle",
        "industrial_design_patent",
        ("nzam-braaat-alakhtraa-waltsmymat-altkhtytyh-lldarat-almtkamlh-walasnaf-alnbatyh-walnmadhj-alsnaa",),
        ("commercial-fraud-law", "product-safety-law"),
        any_patterns=("تصميم صناعي", "نموذج صناعي", "محمي", "تقلد تصميما", "براءة اختراع", "التصميمات التخطيطية"),
        priority=9.4,
    ),
    _bundle(
        "media_telecom_privacy_bundle",
        "media_telecom_privacy",
        ("nzam-alialam-almryy-walmsmwa",),
        ("personal-data-protection-law", "pdpl-implementing-regulation", "nzam-almtbwaat-walnshr"),
        any_patterns=(
            "مؤثر",
            "إعلان مرئي",
            "اعلان مرئي",
            "إعلان مؤثر",
            "اعلان موثر",
            "إعلان عبر منصات التواصل",
            "اعلان عبر منصات التواصل",
            "موثوق",
            "إعلان مدفوع",
            "اعلان مدفوع",
        ),
        priority=9.1,
    ),
    _bundle(
        "telecom_marketing_location_privacy_axis_bundle",
        "telecom_marketing_location_privacy_axis",
        ("communications-and-information-technology-law",),
        ("personal-data-protection-law", "pdpl-implementing-regulation", "nzam-alialam-almryy-walmsmwa"),
        any_patterns=(
            "رسائل تسويقية مزعجة",
            "رسائل تسويقية مستمرة",
            "مزود خدمة اتصالات",
            "شركة اتصالات",
            "بيانات المشتركين",
            "بيانات الموقع",
            "سجل الاستخدام",
            "أغراض إعلانية",
            "اغراض اعلانية",
            "صحيفة إلكترونية",
            "صحيفة الكترونية",
            "صور خاصة دون إذن",
        ),
        priority=9.2,
    ),
    _bundle(
        "electronic_press_publication_privacy_bundle",
        "electronic_press_publication_privacy",
        ("nzam-almtbwaat-walnshr",),
        ("nzam-alialam-almryy-walmsmwa", "personal-data-protection-law", "pdpl-implementing-regulation", "law-of-evidence"),
        any_patterns=(
            "صحيفة إلكترونية",
            "صحيفة الكترونية",
            "نشرت مادة",
            "اتهامات غير موثقة",
            "صوراً خاصة دون إذن",
            "صورا خاصة دون اذن",
            "صور خاصة دون إذن",
            "صور خاصة دون اذن",
        ),
        articles={
            "nzam-almtbwaat-walnshr": {1, 2, 3, 4, 5, 6, 9, 18, 37, 38},
            "nzam-alialam-almryy-walmsmwa": {1, 5, 6, 8, 9, 11},
            "personal-data-protection-law": {5, 8, 13, 15, 20, 23, 25, 26, 31},
            "pdpl-implementing-regulation": {17, 24},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.5,
    ),
    _bundle(
        "health_medical_error_records_pdpl_bundle",
        "health_medical_error_records_pdpl",
        ("alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh"),
        (
            "personal-data-protection-law",
            "pdpl-implementing-regulation",
            "law-of-evidence",
            "civil-transactions-law",
        ),
        any_patterns=("خطأ طبي", "خطا طبي", "ملف المريض", "سجل المريض", "مستشفى خاص", "منشأة صحية", "منشاه صحيه", "نسخة من سجله"),
        articles={
            "nzam-mzawlh-almhn-alshyh": {8, 9, 11, 14, 15, 18, 19, 21, 27, 28, 29, 30, 31, 34},
            "nzam-almwssat-alshyh-alkhash": {4, 22, 34},
            "personal-data-protection-law": {5, 8, 10, 13, 15, 20, 23, 25, 26, 31},
            "pdpl-implementing-regulation": {17, 24, 32},
            "civil-transactions-law": {28, 29, 94, 95, 109, 120, 136, 137, 138, 139},
            "law-of-evidence": {2, 3, 25, 26, 55, 67},
        },
        priority=9.6,
    ),
    _bundle(
        "health_insurance_coverage_dispute_axis_bundle",
        "health_insurance_coverage_dispute_axis",
        ("nzam-aldman-alshy-altaawny", "nzam-mraqbh-shrkat-altamyn-altaawny"),
        ("civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "شركة تأمين رفضت",
            "شركة تامين رفضت",
            "مؤسسة تأمين رفضت",
            "مؤسسة تامين رفضت",
            "منشأة تأمين رفضت",
            "منشاة تامين رفضت",
            "رفضت تغطية",
            "رفضت التغطية",
            "رفضت مطالبة صحية",
            "تغطية جزء من العلاج",
            "موافقة قبل العملية",
            "وثيقة التأمين الصحي",
            "وثيقة تأمين تعاوني",
            "وثيقة تامين تعاوني",
            "الشروط والاستثناءات",
            "حقوق المؤمن له",
            "تأمين صحي",
            "تامين صحي",
        ),
        articles={
            "nzam-aldman-alshy-altaawny": {1, 2, 3, 4, 5, 6, 7, 8},
            "nzam-mraqbh-shrkat-altamyn-altaawny": {1, 2, 3, 4, 5, 6, 7},
            "civil-transactions-law": {94, 95, 109, 120, 136, 137, 138, 139},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.8,
    ),
    _bundle(
        "medical_record_publication_cyber_privacy_axis_bundle",
        "medical_record_publication_cyber_privacy_axis",
        ("personal-data-protection-law",),
        ("pdpl-implementing-regulation", "anti-cybercrime-law", "nzam-mzawlh-almhn-alshyh", "law-of-evidence"),
        any_patterns=(
            "نشر صورة من ملف المريض",
            "نشر ملف المريض",
            "صورة من ملف المريض",
            "مجموعة خاصة",
            "إفشاء ملف المريض",
            "افشاء ملف المريض",
            "سرية ملف المريض",
        ),
        articles={
            "personal-data-protection-law": {5, 8, 10, 13, 15, 20, 23, 25, 26, 31},
            "pdpl-implementing-regulation": {17, 24, 32},
            "anti-cybercrime-law": {3, 4, 5, 6},
            "nzam-mzawlh-almhn-alshyh": {15, 19, 21, 27, 28, 31},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=9.8,
    ),
    _bundle(
        "food_drug_cosmetic_sfda_fraud_bundle",
        "food_drug_cosmetic_sfda_fraud",
        ("nzam-alghdhaa", "nzam-mntjat-altjmyl", "nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"),
        ("commercial-fraud-law", "product-safety-law", "e-commerce-law", "law-of-evidence"),
        any_patterns=(
            "تسمم غذائي",
            "بلاغات تسمم",
            "مصنع غذائي",
            "منتجاً ملوثاً",
            "منتجا ملوثا",
            "منتج ملوث",
            "بطاقة بيانات مضللة",
            "السحب والعقوبات",
            "صلاحية مضللة",
            "مستحضر تجميل",
            "مستحضرات تجميل",
            "بيانات تحذيرية",
            "أدوية",
            "ادوية",
            "مستحضرات عشبية",
            "ادعاءات علاجية",
            "مطعم",
            "منتج تجميلي",
            "مقلدة",
            "حساب إلكتروني",
            "حساب الكتروني",
        ),
        priority=9.3,
    ),
    _bundle(
        "medical_device_sfda_recall_adverse_event_bundle",
        "medical_device_sfda_recall_adverse_event",
        ("nzam-alajhzh-walmstlzmat-altbyh", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"),
        ("commercial-fraud-law", "law-of-evidence", "e-commerce-law", "ecommerce-implementing-regulation"),
        any_patterns=(
            "أجهزة طبية",
            "اجهزة طبية",
            "جهاز طبي",
            "جهازاً طبياً",
            "جهازا طبيا",
            "تسوق جهازاً طبياً",
            "تسوق جهازا طبيا",
            "مستلزمات طبية",
            "مورد أجهزة طبية",
            "استيفاء متطلبات الهيئة",
            "ادعاءات علاجية غير مثبتة",
            "بلاغات أعطال",
            "بلاغات اعطال",
            "أعطال وسلامة",
            "اعطال وسلامة",
            "السحب والتعويض",
            "صيانة الجهاز",
            "صيانة قبل أيام",
            "صيانة قبل ايام",
            "وثائق المعايرة",
            "المعايرة غير مرفقة",
            "معايرة الجهاز",
        ),
        articles={
            "nzam-alajhzh-walmstlzmat-altbyh": {6, 8, 16, 20, 21, 35, 37},
            "nzam-alhyyh-alaamh-llghdhaa-waldwaa": {3, 5},
            "commercial-fraud-law": {1, 2, 3},
        },
        priority=9.8,
    ),
    _bundle(
        "environment_pollution_license_penalty_bundle",
        "environment_pollution_license_penalty",
        ("nzam-albyyh",),
        ("civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "صرف مخلفات",
            "مخلفات في واد",
            "ترخيص بيئي",
            "أضرار على سكان",
            "اضرار على سكان",
            "مزارع مجاورة",
            "مطالبة بإيقاف النشاط",
            "مطالبة بايقاف النشاط",
            "التعويض عن التلوث",
        ),
        priority=9.7,
    ),
    _bundle(
        "healthcare_waste_environment_bundle",
        "healthcare_waste_environment",
        ("alnzam-almwhd-lidarh-nfayat-alraayh-alshyh-bdwl-mjls-altaawn-ldwl-alkhlyj-alarbyh",),
        ("alnzam-alshy", "nzam-albyyh", "labor-law"),
        any_patterns=("نفايات طبية", "نفايات الرعاية الصحية", "تتخلص من نفايات", "منشأة صحية", "تعرض العاملين للخطر"),
        priority=9.2,
    ),
    _bundle(
        "family_personal_status_evidence_bundle",
        "family_personal_status_evidence",
        ("personal-status-law",),
        ("law-of-sharia-procedure", "law-of-evidence", "nzam-hmayh-altfl"),
        any_patterns=("حضانة", "منع سفر", "النفقة", "بعد الطلاق", "فسخ النكاح", "للضرر", "زوجة", "أب يطلب", "طفل"),
        priority=9.0,
    ),
    _bundle(
        "abuse_child_protection_criminal_bundle",
        "abuse_child_protection_criminal",
        ("nzam-hmayh-altfl", "protection-from-abuse-law"),
        ("criminal-procedure-law", "law-of-evidence", "civil-transactions-law"),
        any_patterns=(
            "تعرض لإيذاء",
            "تعرض لايذاء",
            "آثار إيذاء",
            "اثار ايذاء",
            "طفل ولم تبلغ",
            "مدرسة لاحظت",
            "واجب الحماية",
            "واجبات الحماية",
            "السرية والإجراءات",
            "السرية والاجراءات",
            "الحماية من الإيذاء",
            "الحماية من الايذاء",
            "مركز رعاية",
            "حماية عاجلة",
            "أبلغت الجهات",
        ),
        priority=9.3,
    ),
    _bundle(
        "cyber_extortion_fraud_criminal_bundle",
        "cyber_extortion_fraud_criminal",
        ("anti-cybercrime-law", "nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh"),
        ("criminal-procedure-law", "law-of-evidence", "civil-transactions-law", "personal-data-protection-law"),
        any_patterns=("ابتزاز", "تهديد بنشر صور", "رابط بنكي وهمي", "احتيال مالي", "سحب من حسابه", "تطبيق تواصل", "نشر صور خاصة"),
        priority=9.2,
    ),
    _bundle(
        "whistleblower_corruption_protection_bundle",
        "whistleblower_corruption_protection",
        ("whistleblowers-witnesses-experts-and-victims-protection-law",),
        ("nzam-hyyh-alrqabh-wmkafhh-alfsad", "criminal-procedure-law", "law-of-evidence"),
        any_patterns=("مبلغ عن فساد", "المبلغين", "الشهود", "الانتقام", "حماية المبلغين", "نزاهة"),
        priority=9.4,
    ),
    _bundle(
        "forgery_juvenile_criminal_bundle",
        "forgery_juvenile_criminal",
        ("alnzam-aljzayy-ljraym-altzwyr", "nzam-alahdath"),
        ("criminal-procedure-law", "law-of-evidence"),
        any_patterns=("سندات مزورة", "مزورة", "تزوير", "قاصر", "حدث جانح", "الأحداث الجانحين", "الاحداث الجانحين", "عمره أقل من", "عمره اقل من", "محاكمة حدث"),
        priority=9.3,
    ),
    _bundle(
        "compound_procurement_labor_competition_bundle",
        "compound_procurement_labor_competition",
        ("government-tenders-and-procurement-law", "nzam-almnafsh"),
        (
            "government-procurement-implementing-regulation",
            "procurement-conflict-of-interest-regulation",
            "procurement-conduct-ethics-regulation",
            "nzam-almrafaat-amam-dywan-almzalm",
            "nzam-altnfydh-amam-dywan-almzalm",
            "law-of-evidence",
        ),
        any_patterns=(
            "منافسة حكومية",
            "عضو لجنة الفحص",
            "قريب من مدير الشركة",
            "تعارض مصالح",
            "متطلبات المحتوى المحلي",
            "المحتوى المحلي",
            "إلغاء الترسية",
            "الغاء الترسية",
            "توقيع جزاء",
            "اتفاق موردين",
            "رفع الأسعار",
            "رفع الاسعار",
            "تأخرت أجور عمال الموقع",
            "تأخر تسليم الموقع",
            "تعاقد المقاول من الباطن",
        ),
        articles={
            "government-tenders-and-procurement-law": {37, 40, 46, 50, 51, 52, 53, 74, 76, 78, 86, 87, 88, 96},
            "government-procurement-implementing-regulation": {74, 75, 76, 77, 78, 87, 88, 96, 154},
            "procurement-conflict-of-interest-regulation": {5, 6, 7, 8, 9, 10, 11},
            "procurement-conduct-ethics-regulation": {2, 4, 5},
            "nzam-almrafaat-amam-dywan-almzalm": {1, 3, 5, 9, 13, 14},
            "nzam-dywan-almzalm": {1, 8, 13},
            "nzam-altnfydh-amam-dywan-almzalm": {1, 2, 3, 4, 5},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=10.8,
    ),
    _bundle(
        "compound_health_app_privacy_cyber_bundle",
        "compound_health_app_privacy_cyber",
        ("personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation", "anti-cybercrime-law"),
        ("alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh", "law-of-evidence"),
        any_patterns=(
            "تطبيق صحي",
            "بيانات صحية وهوية وموقع",
            "سجلات صحية وهوية وموقع",
            "مزود سحابي خارج المملكة",
            "منشأة استضافة خارجية",
            "شركة تسويق",
            "اختراق وتسرب",
            "لم يبلغ المستخدمين",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_bankruptcy_labor_insurance_bundle",
        "compound_bankruptcy_labor_insurance",
        ("nzam-aliflas", "labor-law"),
        ("bankruptcy-implementing-regulation", "companies-law", "labor-implementing-regulation", "wage-protection-rules", "law-of-evidence"),
        any_patterns=(
            "شركة متعثرة",
            "منشأة متعثرة",
            "توقفت عن سداد الديون",
            "طلب إعادة التنظيم",
            "دفعت لمورد قريب",
            "تأخرت رواتب العمال",
            "التأمينات بأجور أقل",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_civil_vat_einvoice_execution_bundle",
        "compound_civil_vat_einvoice_execution",
        ("civil-transactions-law", "law-of-evidence", "electronic-transactions-law", "nzam-drybh-alqymh-almdafh"),
        ("zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls", "nzam-almhakm-altjaryh", "execution-law"),
        any_patterns=(
            "عرض سعر وواتساب",
            "فواتير إلكترونية ناقصة",
            "فواتير الكترونية ناقصة",
            "ينكر الاتفاق والتوقيع",
            "يطلب الدائن لاحقاً التنفيذ",
            "قيمة توريد مثبت",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_family_child_harassment_bundle",
        "compound_family_child_harassment",
        ("personal-status-law", "nzam-hmayh-altfl", "protection-from-abuse-law", "nzam-mkafhh-jrymh-althrsh"),
        ("criminal-procedure-law", "law-of-evidence"),
        any_patterns=(
            "حضانة ونفقة",
            "تدابير عاجلة لطفل",
            "آثار إيذاء في المدرسة",
            "اثار ايذاء في المدرسة",
            "ادعاء تحرش من قريب",
            "رسائل تثبت التهديد",
            "الامتناع عن النفقة",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_media_telecom_ip_privacy_bundle",
        "compound_media_telecom_ip_privacy",
        ("copyright-law", "communications-and-information-technology-law", "personal-data-protection-law", "nzam-alialam-almryy-walmsmwa"),
        ("pdpl-implementing-regulation", "nzam-almtbwaat-walnshr", "law-of-evidence", "nzam-almhakm-altjaryh"),
        any_patterns=(
            "منصة إعلامية رقمية",
            "منصة اعلامية رقمية",
            "تبث مقاطع ودورات منسوخة",
            "دورات منسوخة دون إذن",
            "بيانات الموقع وسجل المشاهدة",
            "محتوى مرئياً دون ترخيص",
            "محتوى مرئيا دون ترخيص",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_construction_procurement_boundary_bundle",
        "compound_construction_procurement_boundary",
        ("civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy", "nzam-tsnyf-almqawlyn", "government-tenders-and-procurement-law"),
        ("government-procurement-implementing-regulation", "nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"),
        any_patterns=(
            "مقاول مصنف",
            "مشروعاً خاصاً وآخر حكومياً",
            "مشروعا خاصا وآخر حكوميا",
            "عيوب إنشائية",
            "عيوب انشائية",
            "استبعد عرضه بسبب التصنيف",
            "تظلم من قرار الترسية",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_labor_privacy_social_insurance_bundle",
        "compound_labor_privacy_social_insurance",
        ("labor-law", "personal-data-protection-law", "nzam-altamynat-alajtmaayh"),
        (
            "labor-implementing-regulation",
            "labor-contract-documentation-rules",
            "wage-protection-rules",
            "labor-violations-penalties-table",
            "pdpl-implementing-regulation",
            "law-of-evidence",
        ),
        any_patterns=(
            "العمل عن بعد دون توثيق",
            "قوى",
            "راقبت مواقعهم وأجهزتهم",
            "راقبت مواقعهم واجهزتهم",
            "أخرت أجورهم",
            "اخرت اجورهم",
            "في التأمينات بأجر مختلف",
            "في التامينات باجر مختلف",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_criminal_child_cyber_bundle",
        "compound_criminal_child_cyber",
        ("nzam-alahdath", "anti-cybercrime-law", "nzam-mkafhh-jrymh-althrsh", "nzam-hmayh-altfl"),
        ("criminal-procedure-law", "law-of-evidence", "protection-from-abuse-law"),
        any_patterns=(
            "حدث شارك في اختراق",
            "اختراق حساب زميل",
            "ابتزازه بنشر صور",
            "تحرش وتهديد",
            "المدرسة تراخت في الإبلاغ",
            "المدرسة تأخرت في الإبلاغ",
            "حفظ الأدلة",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_cma_privacy_marketing_bundle",
        "compound_cma_privacy_marketing",
        ("nzam-alswq-almalyh", "cma-securities-offering-rules", "personal-data-protection-law"),
        ("cma-continuing-obligations-rules", "cma-corporate-governance-regulations", "pdpl-implementing-regulation", "law-of-evidence"),
        any_patterns=(
            "تسوق طرح أوراق مالية",
            "تسوق طرح اوراق مالية",
            "لمستثمرين أفراد برسائل مستهدفة",
            "لمستثمرين افراد برسائل مستهدفة",
            "إعلان ناقص عن المخاطر",
            "اعلان ناقص عن المخاطر",
            "تداول سابق من مطلعين",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_listed_company_competition_bundle",
        "compound_listed_company_competition",
        ("companies-law", "nzam-alswq-almalyh", "nzam-almnafsh"),
        (
            "companies-implementing-regulation",
            "cma-corporate-governance-regulations",
            "cma-continuing-obligations-rules",
            "cma-securities-offering-rules",
            "law-of-evidence",
        ),
        any_patterns=(
            "شركة مساهمة مدرجة صححت نتائج",
            "هبط السهم",
            "باع عضو مجلس أسهماً قبل التصحيح",
            "باع عضو مجلس اسهما قبل التصحيح",
            "عقد توريد مع طرف",
            "للرئيس التنفيذي فيه مصلحة",
            "تستحوذ الشركة على منافس",
            "تفرض حصرية على العملاء",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_health_privacy_insurance_bundle",
        "compound_health_privacy_insurance",
        ("alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh", "personal-data-protection-law", "nzam-mraqbh-shrkat-altamyn-altaawny"),
        ("pdpl-implementing-regulation", "nzam-aldman-alshy-altaawny", "civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "مستشفى خاص تعرض لخطأ طبي",
            "خطأ طبي",
            "رفضت المنشأة تسليمه ملفه الطبي",
            "رفضت المنشأة تسليم الملف الطبي",
            "شاركت بياناته مع شركة تأمين",
            "رفضت المطالبة بحجة استثناءات",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_food_ecommerce_privacy_recall_bundle",
        "compound_food_ecommerce_privacy_recall",
        ("nzam-alghdhaa", "nzam-alhyyh-alaamh-llghdhaa-waldwaa", "commercial-fraud-law", "e-commerce-law", "personal-data-protection-law"),
        ("ecommerce-implementing-regulation", "pdpl-implementing-regulation", "law-of-evidence"),
        any_patterns=(
            "منتجاً غذائياً ملوثاً",
            "منتجا غذائيا ملوثا",
            "بطاقة بيانات مضللة",
            "رسائل تسويقية للمشترين",
            "بياناتهم مع شركة إعلان",
            "بلاغات تسمم",
            "تستدعي السحب",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_medical_device_tax_consumer_bundle",
        "compound_medical_device_tax_consumer",
        ("nzam-alajhzh-walmstlzmat-altbyh", "nzam-alhyyh-alaamh-llghdhaa-waldwaa", "e-commerce-law", "nzam-drybh-alqymh-almdafh"),
        ("commercial-fraud-law", "ecommerce-implementing-regulation", "zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "law-of-evidence"),
        any_patterns=(
            "مورد أجهزة طبية استورد جهازاً",
            "مورد اجهزة طبية استورد جهازا",
            "متطلبات الهيئة",
            "باعه عبر منصة إلكترونية",
            "ضمان مضلل",
            "فاتورة ضريبية ناقصة",
            "بلاغات عطل وسلامة",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_aml_real_estate_pdpl_bundle",
        "compound_aml_real_estate_pdpl",
        ("anti-money-laundering-law", "real-estate-brokerage-law", "personal-data-protection-law"),
        ("nzam-mraqbh-albnwk", "pdpl-implementing-regulation", "civil-transactions-law", "law-of-evidence"),
        any_patterns=(
            "وسيط عقاري ومؤسسة مالية",
            "تحويلات مجزأة",
            "مالكاً مستفيداً غير واضح",
            "مالكا مستفيدا غير واضح",
            "جمع بيانات هوية واسعة",
            "مشاركة بعضها مع أطراف خارجية",
            "تأخر الإبلاغ عن الاشتباه",
        ),
        priority=10.8,
    ),
    _bundle(
        "compound_ecommerce_tax_privacy_bundle",
        "compound_ecommerce_tax_privacy",
        ("e-commerce-law", "commercial-fraud-law", "personal-data-protection-law", "nzam-drybh-alqymh-almdafh"),
        (
            "ecommerce-implementing-regulation",
            "pdpl-implementing-regulation",
            "nzam-alajhzh-walmstlzmat-altbyh",
            "zatca-vat-implementing-regulation",
            "zatca-e-invoicing-bylaw",
            "zatca-e-invoicing-technical-controls",
            "law-of-evidence",
        ),
        any_patterns=(
            "جهازاً طبياً منزلياً",
            "جهازا طبيا منزليا",
            "بادعاءات علاجية",
            "تأخر التسليم ورفضت رد المبلغ",
            "غير مطابق للوصف",
            "عيب في المبيع",
            "الشروط في الموقع تمنع الاسترداد",
            "رسائل بريد",
            "صور للإعلان",
            "جمعت رقم الهوية وتاريخ الميلاد",
            "رقم الجوال في التسويق",
            "فاتورة إلكترونية ناقصة الحقول",
            "فاتورة إلكترونية",
        ),
        articles={
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7, 14, 16, 18},
            "civil-transactions-law": {94, 95, 109, 120, 136, 321},
            "nzam-alajhzh-walmstlzmat-altbyh": {6, 8, 16, 20, 21, 35, 37},
            "nzam-alhyyh-alaamh-llghdhaa-waldwaa": {3, 5},
            "zatca-e-invoicing-bylaw": {1, 2, 3, 4, 5},
            "zatca-e-invoicing-technical-controls": {1, 2, 3, 4, 5},
            "law-of-evidence": {55, 56, 57},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
        },
        priority=10.8,
    ),
]


ISSUE_AXIS_BUNDLES: list[dict[str, Any]] = [
    _bundle(
        "axis_llc_manager_assets_minority_dividends",
        "axis_llc_manager_assets_minority_dividends",
        ("companies-law",),
        ("companies-implementing-regulation", "civil-transactions-law", "law-of-evidence", "nzam-almhakm-altjaryh"),
        any_patterns=(
            "شركة ذات مسؤولية محدودة",
            "شركة ذات مسئولية محدودة",
            "الشريك الأقلية",
            "شريك أقلية",
            "منع الشريك",
            "منع الاطلاع",
            "الاطلاع على الحسابات",
            "حوّل أصول",
            "حول أصول",
            "حول اصول",
            "شركة يملكها قريبه",
            "شركة يملكها قريب",
            "استغلال أصول الشركة",
            "استغلال اصول الشركة",
            "تعارض مصالح المدير",
            "مسؤولية المدير",
            "دعوى مسؤولية",
            "توزيع أرباح",
            "وزع أرباح",
            "ديون متعثرة",
            "لم تحدث عقد تأسيسها",
            "لم تحدّث عقد تأسيسها",
            "تحديث عقد تأسيسها",
        ),
        excluded_patterns=("منافسة حكومية", "المنافسات والمشتريات", "المشتريات الحكومية", "لجنة الفحص", "ترسية", "الترسية", "المحتوى المحلي"),
        articles={
            "companies-law": {8, 22, 26, 27, 28, 158, 165, 167, 168, 171, 172, 176, 177, 182, 242, 254, 260, 261, 262, 276, 281},
            "civil-transactions-law": {94, 95, 120, 128, 136, 137, 138},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "nzam-almhakm-altjaryh": {16, 17, 19},
        },
        priority=11.6,
    ),
    _bundle(
        "axis_company_distress_bankruptcy_procedure",
        "axis_company_distress_bankruptcy_procedure",
        ("nzam-aliflas",),
        ("bankruptcy-implementing-regulation", "companies-law"),
        any_patterns=(
            "فتح إجراء إفلاس",
            "فتح اجراء افلاس",
            "إجراء إفلاس",
            "اجراء افلاس",
            "إذا ثبت التعثر",
            "اذا ثبت التعثر",
            "ديون متعثرة",
            "تعثر مالي",
            "شركة متعثرة",
            "الشركة المتعثرة",
            "توقفت عن سداد ديونها",
            "توقفت الشركة عن سداد ديونها",
            "توقفت عن الوفاء بديونها",
            "عجزت عن سداد ديونها",
            "تعثر الشركة",
            "تعثر الشركة عن السداد",
            "أحد الدائنين يريد رفع مطالبة",
            "احد الدائنين يريد رفع مطالبة",
            "لا تكفي لسداد ديونها",
            "أصولها لا تكفي لسداد ديونها",
            "اصولها لا تكفي لسداد ديونها",
        ),
        articles={
            "nzam-aliflas": {1, 4, 5, 7, 42, 46, 47, 48, 49, 50, 53, 54, 55},
            "companies-law": {182, 242, 254},
        },
        priority=11.4,
    ),
    _bundle(
        "axis_tax_zakat_debt_obligations",
        "axis_tax_zakat_debt_obligations",
        ("nzam-jbayh-alzkah", "nzam-drybh-aldkhl", "nzam-drybh-alqymh-almdafh"),
        ("zatca-vat-implementing-regulation", "law-of-evidence"),
        any_patterns=(
            "ديون زكوية",
            "ديون ضريبية",
            "زكوية وضريبية",
            "زكويه وضريبيه",
            "زكاة وضريبة",
            "زكاه وضريبه",
            "التزامات زكوية",
            "التزامات ضريبية",
            "مستحقات زكوية",
            "مستحقات ضريبية",
            "ضريبة الدخل",
        ),
        articles={
            "nzam-drybh-alqymh-almdafh": {1, 2, 3, 5, 14, 36, 41, 42, 45},
            "zatca-vat-implementing-regulation": {1, 2, 3, 7, 53, 54},
            "nzam-drybh-aldkhl": {1, 2, 3, 4, 5},
            "nzam-jbayh-alzkah": {1, 2, 3, 4},
        },
        priority=11.35,
    ),
    _bundle(
        "axis_private_commercial_contract_claim_procedure",
        "axis_private_commercial_contract_claim_procedure",
        ("civil-transactions-law",),
        ("law-of-evidence", "nzam-almhakm-altjaryh"),
        any_patterns=(
            "عقد توريد",
            "توريد أجهزة",
            "توريد اجهزة",
            "عقد مقاولة",
            "مقاول لبناء",
            "مشروع خاص",
            "منشأة خاصة",
            "شركة توريد",
            "فسخ العقد",
            "طلب التعويض",
            "يطالب بالتعويض",
            "تأخر في الإنجاز",
            "تاخر في الانجاز",
            "عيوب إنشائية",
            "عيوب انشائية",
        ),
        excluded_patterns=(
            "عامل",
            "العامل",
            "عمال",
            "موظف",
            "موظفة",
            "صاحب العمل",
            "منافسة حكومية",
            "المنافسات والمشتريات",
            "المشتريات الحكومية",
            "لجنة الفحص",
            "ترسية",
            "الترسية",
            "المحتوى المحلي",
            "ساعات إضافية",
            "ساعات اضافية",
            "مكافأة نهاية الخدمة",
            "مكافاه نهاية الخدمة",
            "خصم من الأجر",
            "خصم من الاجر",
        ),
        articles={
            "civil-transactions-law": {94, 95, 104, 105, 106, 107, 120, 128, 136, 137, 138, 321, 349},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "nzam-almhakm-altjaryh": {16, 17, 19},
        },
        priority=11.25,
    ),
    _bundle(
        "axis_family_personal_status_support_custody_visit_travel",
        "axis_family_personal_status_support_custody_visit_travel",
        ("personal-status-law", "nzam-wthayq-alsfr"),
        ("law-of-sharia-procedure", "law-of-evidence"),
        any_patterns=(
            "حضانة",
            "حاضنة",
            "المحضون",
            "نفقة",
            "زيارة",
            "الزيارة",
            "استصحابه",
            "سفر الأطفال",
            "سفر الاطفال",
            "السفر بالمحضون",
            "بعد الطلاق",
        ),
        articles={
            "personal-status-law": {
                44,
                45,
                46,
                47,
                48,
                49,
                50,
                58,
                59,
                60,
                61,
                124,
                125,
                126,
                127,
                128,
                129,
                130,
                131,
                132,
                133,
                134,
                135,
            },
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=11.2,
    ),
    _bundle(
        "axis_family_execution_orders_support_visit_custody",
        "axis_family_execution_orders_support_visit_custody",
        ("execution-law", "execution-implementing-regulation"),
        ("law-of-sharia-procedure", "law-of-evidence", "electronic-transactions-law"),
        any_patterns=(
            "صك نفقة",
            "صك سابق بالنفقة",
            "حكم نفقة",
            "سند نفقة",
            "امتنع عن النفقة",
            "امتناع عن النفقة",
            "متأخرات النفقة",
            "منع الزيارة",
            "حكم زيارة",
            "تنفيذ حكم زيارة",
            "تنفيذ أحكام الأحوال الشخصية",
            "تنفيذ احكام الاحوال الشخصية",
            "تنفيذ حكم حضانة",
            "امتنع عن تنفيذ حكم صادر بالحضانة",
            "قام بمقاومة التنفيذ",
            "تعطيل التنفيذ",
        ),
        articles={
            "execution-law": {9, 21, 34, 73, 74, 92},
            "law-of-evidence": {2, 3, 25, 26, 55},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
        },
        priority=11.3,
    ),
    _bundle(
        "axis_child_neglect_health_protection",
        "axis_child_neglect_health_protection",
        ("nzam-hmayh-altfl",),
        ("protection-from-abuse-law", "criminal-procedure-law", "law-of-evidence"),
        any_patterns=(
            "إهمال صحي للطفل",
            "اهمال صحي للطفل",
            "إهمال الطفل",
            "اهمال الطفل",
            "حماية الطفل",
            "تعرض الطفل",
            "إيذاء الطفل",
            "ايذاء الطفل",
            "عدم استكمال تطعيماته",
            "تطعيماته الصحية",
            "رعاية صحية للطفل",
        ),
        articles={
            "nzam-hmayh-altfl": {1, 2, 3, 6, 7, 18, 19, 22, 24},
            "protection-from-abuse-law": {1, 2, 3, 5, 6, 7, 8, 13},
            "law-of-evidence": {2, 3, 25, 26, 55},
        },
        priority=11.1,
    ),
    _bundle(
        "axis_ecommerce_subscription_finance_payments",
        "axis_ecommerce_subscription_finance_payments",
        (
            "e-commerce-law",
            "commercial-fraud-law",
            "personal-data-protection-law",
            "nzam-mraqbh-shrkat-altmwyl",
            "nzam-almdfwaat-wkhdmatha",
        ),
        (
            "ecommerce-implementing-regulation",
            "pdpl-implementing-regulation",
            "pdpl-transfer-regulation",
            "anti-cybercrime-law",
            "nzam-almalwmat-alaytmanyh",
            "law-of-evidence",
            "nzam-almhakm-altjaryh",
            "civil-transactions-law",
        ),
        any_patterns=(
            "تجربة مجانية",
            "تجربه مجانيه",
            "جرّب مجانًا",
            "جرب مجانا",
            "٧ أيام",
            "7 أيام",
            "سبعة أيام",
            "اشتراك شهري",
            "خصم مبلغ الاشتراك تلقائيًا",
            "خصم مبلغ الاشتراك تلقائيا",
            "خصم تلقائي",
            "إدخال بيانات البطاقة",
            "ادخال بيانات البطاقة",
            "بيانات البطاقة البنكية",
            "ضمان ممتد",
            "خدمة ضمان ممتد",
            "اشتر الآن وادفع لاحقًا",
            "اشتر الان وادفع لاحقا",
            "الشراء الآن والدفع لاحقًا",
            "الدفع لاحقًا",
            "الدفع لاحقا",
            "مزود الدفع",
            "بوابة الدفع",
            "مقدم خدمة تمويل",
            "تمويل استهلاكي",
            "رسوم وغرامات",
            "شروط السداد",
            "مراجعات مزيفة",
            "سياسة الاسترجاع",
            "منتجات رقمية",
            "عيوب في التفعيل",
            "سجل المشتريات",
        ),
        articles={
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "ecommerce-implementing-regulation": {5, 6, 7, 8, 10, 11, 18},
            "personal-data-protection-law": {5, 8, 10, 13, 15, 20, 23, 25, 26, 29, 31},
            "pdpl-implementing-regulation": {17, 24, 32},
            "commercial-fraud-law": {1, 2, 3, 4, 5, 7},
            "nzam-mraqbh-shrkat-altmwyl": {1, 2, 3, 4, 5, 6, 7},
            "nzam-almdfwaat-wkhdmatha": {1, 2, 3, 4, 5, 6},
            "nzam-almalwmat-alaytmanyh": {1, 2, 3, 4, 5, 6},
        },
        priority=11.55,
    ),
    _bundle(
        "axis_delivery_app_gig_labor_transport_competition",
        "axis_delivery_app_gig_labor_transport_competition",
        (
            "labor-law",
            "nzam-altamynat-alajtmaayh",
            "nzam-almnafsh",
            "nzam-alnql-alaam-ala-altrq-balmmlkh-alarbyh-alsawdyh",
            "nzam-almrwr",
            "e-commerce-law",
            "nzam-alamtyaz-altjary",
            "personal-data-protection-law",
        ),
        (
            "labor-implementing-regulation",
            "labor-violations-penalties-table",
            "wage-protection-rules",
            "ecommerce-implementing-regulation",
            "pdpl-implementing-regulation",
            "nzam-alialam-almryy-walmsmwa",
            "nzam-alalamat-altjaryh",
            "commercial-fraud-law",
            "civil-transactions-law",
            "law-of-evidence",
            "nzam-almhakm-altjaryh",
        ),
        any_patterns=(
            "سائقين مستقلين",
            "سائقون مستقلون",
            "شركاء مستقلون",
            "أسعار التوصيل",
            "اسعار التوصيل",
            "مناطق العمل",
            "نظام التقييم",
            "شروط قبول الطلبات",
            "قبول الطلبات",
            "عقوبات الإيقاف",
            "عقوبات الايقاف",
            "الإيقاف المؤقت",
            "الايقاف المؤقت",
            "التسجيل في التأمينات",
            "التسجيل في التامينات",
            "عمولات مرتفعة",
            "عدم عرض أسعار أقل",
            "عدم عرض اسعار اقل",
            "تطبيقات منافسة",
            "ظهورًا أفضل",
            "ظهورا افضل",
            "مطابخ سحابية",
            "امتياز تجاري",
            "المنتجات الأكثر مبيعًا",
            "المنتجات الاكثر مبيعا",
            "حوادث لسائقين",
            "حادث لسائق",
            "مركبات غير مؤمنة",
            "غير مؤمنة",
            "خلال ١٥ دقيقة",
            "خلال 15 دقيقة",
            "الطلب مجانًا",
            "الطلب مجانا",
        ),
        articles={
            "labor-law": {2, 5, 6, 50, 51, 52, 53, 74, 75, 76, 77, 84, 88, 90, 92, 94, 107},
            "nzam-altamynat-alajtmaayh": {1, 2, 3, 4, 5, 6, 7},
            "nzam-almnafsh": {1, 2, 3, 5, 6, 7, 10, 11, 12, 14, 15},
            "e-commerce-law": {5, 6, 8, 10, 11, 13, 14, 17, 18},
            "personal-data-protection-law": {5, 8, 10, 13, 15, 20, 23, 25, 26, 31},
        },
        priority=11.6,
    ),
    _bundle(
        "axis_electronic_evidence_messages",
        "axis_electronic_evidence_messages",
        ("law-of-evidence", "electronic-transactions-law"),
        (),
        any_patterns=(
            "رسائل واتساب",
            "رسالة واتساب",
            "محادثات واتساب",
            "عبر واتساب",
            "أُبلغ بالفصل عبر واتساب",
            "ابلغ بالفصل عبر واتساب",
            "إشعار واتساب",
            "اشعار واتساب",
            "إبلاغ واتساب",
            "ابلاغ واتساب",
            "رسائل إلكترونية",
            "رسائل الكترونية",
            "رسائل بريد",
            "بريد إلكتروني",
            "بريد الكتروني",
            "توقيع إلكتروني",
            "توقيع الكتروني",
            "محرر إلكتروني",
            "محرر الكتروني",
        ),
        articles={
            "law-of-evidence": {55, 56, 57},
            "electronic-transactions-law": {5, 7, 8, 9, 12, 14},
        },
        priority=10.9,
    ),
]


LEGAL_ISSUE_LEXICON_BY_BUNDLE: dict[str, dict[str, tuple[str, ...]]] = {
    "bankruptcy_preference_employees_bundle": {
        "any": (
            "توقف* عن السداد",
            "توقف* عن سداد ديون*",
            "توقف* عن الوفاء بديون*",
            "عجز* عن السداد",
            "عجز* عن سداد ديون*",
            "عجز* عن الوفاء",
            "ما عاد* يسدد*",
            "مو قادر* يسدد*",
            "غير قادر* على السداد",
            "غير قادر* على الوفاء",
            "عليه ديون* وما سدد*",
            "مطالبات دائن*",
            "دائن* يطالب*",
            "دائن* يبغى يرفع مطالبه",
            "دائن* يريد رفع مطالبه",
            "رفع مطالبه من دائن*",
            "دعوى دائن*",
            "ديون* مستحقه",
            "ديون* متاخره",
            "ديون* حاله",
            "تعثر* في السداد",
            "تعثر* عن السداد",
            "تعثر* في سداد ديون*",
            "تعثر* ماليا",
            "الشركاء يفكرون في التصفيه",
            "الشركاء يبغون يصفون الشركه",
            "تصفيه الشركه بسبب الديون",
            "تصفيه بسبب التعثر",
            "حل الشركه بسبب الديون",
            "اعاده تنظيم مالي",
            "تسويه وقائيه",
            "افتتاح اجراء",
        ),
        "excluded": (
            "تصفية حقوق عامل",
            "تصفيه حقوق عامل",
            "تصفية مستحقات العامل",
            "تصفيه مستحقات العامل",
            "تصفية حسابات بين الشركاء",
            "تصفيه حسابات بين الشركاء",
            "تعثر في الانجاز",
            "تعثر تنفيذ مشروع",
            "تأخر دفعات المالك",
            "تاخر دفعات المالك",
        ),
    },
    "axis_company_distress_bankruptcy_procedure": {
        "any": (
            "توقف* عن سداد ديون*",
            "توقف* عن الوفاء بديون*",
            "عجز* عن سداد ديون*",
            "غير قادر* على سداد ديون*",
            "دائن* يريد رفع مطالبه",
            "دائن* يبغى يرفع مطالبه",
            "مطالبه من دائن*",
            "الشركاء يفكرون في التصفيه",
            "تصفيه الشركه بسبب الديون",
            "تصفيه بسبب التعثر",
            "تعثر الشركه",
            "تعثر* ماليا",
            "اعاده التنظيم المالي",
            "افتتاح اجراء افلاس",
            "اجراء تصفيه",
        ),
        "excluded": (
            "تصفية حقوق عامل",
            "تصفيه حقوق عامل",
            "تصفية مستحقات",
            "تعثر في تنفيذ مشروع",
            "تعثر في الانجاز",
        ),
    },
    "axis_llc_manager_assets_minority_dividends": {
        "any": (
            "شركة محدوده",
            "شركه محدوده",
            "ذ م م",
            "مدير الشركه",
            "مديرها حول اصول*",
            "حوّل اموال الشركه",
            "حول اموال الشركه",
            "باع اصول الشركه",
            "نقل اصول الشركه",
            "منعني من الاطلاع",
            "ما خلاني اطلع على الحسابات",
            "الشريك الصغير",
            "شريك اقليه",
            "ارباح صوريه",
            "وزع ارباح رغم الديون",
            "تحديث عقد التاسيس",
        ),
    },
    "labor_contract_wages_dues_compliance_bundle": {
        "any": (
            "ما نزل* الراتب",
            "ما صرف* الراتب",
            "تأخير الراتب",
            "تاخير الراتب",
            "راتب* متاخر",
            "رواتب* متاخره",
            "فصلوني",
            "انهوا عقدي",
            "فترة تجربه",
            "مددوا التجربه",
            "دوام عن بعد",
            "اشتغل من البيت",
            "قوى",
            "منصة قوى",
            "شرط عدم منافسه",
            "بدون اجر",
            "اجازه بدون راتب",
            "نهاية الخدمه",
            "مكافاه نهاية الخدمه",
        ),
    },
    "labor_injury_social_insurance_safety_bundle": {
        "any": (
            "انصاب في الموقع",
            "اصابه في العمل",
            "اصابه بالموقع",
            "سقطت عليه معده",
            "حادث عمل",
            "موقع خطر",
            "سلامه غير مكتمله",
            "ما تدرب على السلامه",
            "التأمينات مسجل* براتب اقل",
            "التامينات مسجل* براتب اقل",
            "سعوده وهميه",
            "اسماء سعوديين بدون دوام",
        ),
    },
    "ecommerce_digital_service_pdpl_marketing_bundle": {
        "any": (
            "ما تفعل الاشتراك",
            "لم يتفعل الاشتراك",
            "ما اشتغلت الدوره",
            "الدوره ما فتحت",
            "الخدمه الرقميه ما تفعلت",
            "ابغى استرجع فلوسي",
            "يرفض يرجع المبلغ",
            "طلبت الغاء الاشتراك",
            "رقمي للدعايه",
            "ارسلوا لي اعلانات",
            "شاركوا بياناتي مع معلن",
            "شريك تسويقي",
            "شريك اعلاني",
        ),
    },
    "pdpl_health_breach_transfer_marketing_bundle": {
        "any": (
            "تسرب* بيانات*",
            "تهربت بيانات*",
            "انكشفت بيانات*",
            "باعوا بيانات*",
            "شاركوا بيانات*",
            "بيانات صحيه",
            "بيانات حساسه",
            "مستضافه خارج السعوديه",
            "سيرفر خارج المملكه",
            "كلاود خارج المملكه",
            "مزود سحابي اجنبي",
            "شركة تحليل تسويقي",
            "تحليل تسويقي",
        ),
    },
    "vat_platform_commission_einvoice_bundle": {
        "any": (
            "فاتوره pdf",
            "فواتير pdf",
            "فاتوره عاديه",
            "فوترة الكترونيه",
            "فوتره الكترونيه",
            "زاتكا",
            "بدون رقم ضريبي",
            "الرقم الضريبي غير موجود",
            "اشعار خصم",
            "اشعار دائن",
            "مرتجع ضريبي",
            "حقول الفاتوره",
            "qr",
        ),
    },
    "procurement_grievance_award_compound_bundle": {
        "any": (
            "اعترضنا على الترسيه",
            "اعتراض على الترسيه",
            "تقييم العروض",
            "فحص العروض",
            "كراسه الشروط",
            "منصه اعتماد",
            "اعتماد",
            "منافسه حكوميه",
            "مناقصه حكوميه",
            "المورد المعترض",
            "مقدم العرض",
            "صاحب العطاء",
            "رفضت الجهه التظلم",
        ),
    },
    "private_construction_building_code_evidence_bundle": {
        "any": (
            "المقاول تاخر",
            "المقاول تأخر",
            "عيوب بالبناء",
            "عيوب في الفيلا",
            "تشققات",
            "تسربات",
            "عزل سيء",
            "كهرباء سيئه",
            "خرسانه",
            "كود البناء",
        ),
    },
    "offplan_real_estate_broker_escrow_bundle": {
        "any": (
            "بيع على الخارطه",
            "على الخارطه",
            "المطور تاخر",
            "المطور تأخر",
            "حساب ضمان",
            "غير المواصفات",
            "جمعية ملاك",
            "جمعيه ملاك",
            "مصاريف الصيانه",
        ),
    },
    "health_medical_error_records_pdpl_bundle": {
        "any": (
            "خطا طبي",
            "خطأ طبي",
            "غلط الطبيب",
            "ملفي الطبي",
            "سجل المريض",
            "نشر ملفي",
            "التامين رفض",
            "التأمين رفض",
            "موافقه قبل العمليه",
            "جهاز طبي",
            "معايره الجهاز",
            "صيانة الجهاز",
        ),
    },
    "axis_family_personal_status_support_custody_visit_travel": {
        "any": (
            "ابوهم ما يصرف",
            "ما يدفع النفقه",
            "منع الزياره",
            "سفر الاولاد",
            "سفر الاطفال",
            "الحضانه",
            "حاضنه",
            "صك نفقه",
            "واتساب بين الاب والام",
        ),
    },
    "commercial_franchise_register_tradename_bundle": {
        "any": (
            "فرنشايز",
            "امتياز تجاري",
            "مانح الامتياز",
            "ممنوح الامتياز",
            "وثيقة الافصاح",
            "وثيقه الافصاح",
            "تشغيل علامه",
            "اسم تجاري",
            "سجل تجاري",
        ),
    },
    "competition_merger_dominance_bundle": {
        "any": (
            "استحواذ على منافس",
            "تركز اقتصادي",
            "حصة سوقية",
            "حصه سوقيه",
            "وضع مهيمن",
            "منع التعامل مع المنافسين",
            "حصرية",
            "حصريه",
            "تقسيم السوق",
            "تواطؤ اسعار",
        ),
    },
}


RETRIEVAL_PROFILES = {
    "legal_baseline": {
        "dense_norm_weight": 0.55,
        "lexical_norm_weight": 0.45,
        "dense_k": 90,
        "lexical_k": 90,
        "context_limit": 24,
    },
    "semantic70": {
        "dense_norm_weight": 0.70,
        "lexical_norm_weight": 0.30,
        "dense_k": 140,
        "lexical_k": 140,
        "context_limit": 34,
    },
    "jamia_recall": {
        "dense_norm_weight": 0.70,
        "lexical_norm_weight": 0.30,
        "dense_k": 180,
        "lexical_k": 180,
        "context_limit": 72,
        "slug_article_seed_count": 8,
        "learned_slug_article_seed_limit": 12,
        "coverage_packer_seed_limit": 36,
        "coverage_packer_per_slug": 4,
        "heldout_axis_packer_seed_limit": 18,
        "heldout_axis_packer_per_slug": 4,
        "required_article_seed_limit": 32,
        "required_article_seed_per_slug": 8,
    },
}


class LegalRAGEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._sync_lock = asyncio.Lock()
        self._embeddings = self._build_embeddings()
        self._entries: list[dict[str, Any]] = []
        self._entry_by_chunk_id: dict[str, dict[str, Any]] = {}
        self._entries_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._entries_by_article: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        self._title_by_slug: dict[str, str] = dict(REGULATION_TITLE_OVERRIDES)
        self._package_router = self._load_package_router()
        self._package_router_retrieval_table = self._load_package_router_retrieval_table()
        self._article_support_table = self._load_article_support_table()
        self._heldout_axis_packer = self._load_heldout_axis_packer()
        self._load_structured_entries()
        self._vectorstore = self._create_vectorstore()

    def _runtime_embedding_api_key(self) -> str:
        try:
            state = get_runtime_settings_store().get_state()
            return str((state.get("embeddings") or {}).get("api_key") or self.settings.openai_api_key)
        except Exception:
            return self.settings.openai_api_key

    def _build_embeddings(self) -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            model=self.settings.embedding_model,
            openai_api_key=self._runtime_embedding_api_key(),
        )

    def _load_structured_entries(self) -> None:
        chunks_path = _resolve_path(self.settings.structured_chunks_path)
        if not chunks_path.exists():
            logger.warning("Structured chunks file not found: %s", chunks_path)
            self._load_structured_entries_from_by_regulation()
            return
        with chunks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                self._register_structured_entry(row)
        logger.info("Loaded %s structured chunks", len(self._entries))

    def _register_structured_entry(self, row: dict[str, Any]) -> None:
        row["normalized_search_text"] = _normalize(
            "\n".join(
                [
                    row.get("regulation_title_ar", ""),
                    row.get("citation_short_ar", ""),
                    row.get("article_heading", ""),
                    row.get("index_text", ""),
                    row.get("text", ""),
                    row.get("text_verbatim", ""),
                ]
            )
        )
        row["token_set"] = set(_tokens(row["normalized_search_text"]))
        chunk_id = str(row.get("chunk_id") or "")
        slug = str(row.get("regulation_slug") or "")
        try:
            article = int(row.get("article_index") or 0)
        except Exception:
            article = 0
            row["article_index"] = 0
        if slug and row.get("regulation_title_ar"):
            self._title_by_slug.setdefault(slug, row["regulation_title_ar"])
        self._entries.append(row)
        if chunk_id:
            self._entry_by_chunk_id[chunk_id] = row
        if slug:
            self._entries_by_slug[slug].append(row)
        if slug and article:
            self._entries_by_article[(slug, article)].append(row)

    def _load_structured_entries_from_by_regulation(self) -> None:
        if not STRUCTURED_BY_REGULATION_DIR.exists():
            logger.warning("Structured by_regulation fallback not found: %s", STRUCTURED_BY_REGULATION_DIR)
            return
        loaded = 0
        for path in sorted(STRUCTURED_BY_REGULATION_DIR.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load structured regulation fallback %s: %s", path, exc)
                continue
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata") or {}
            slug = str(metadata.get("slug") or path.stem)
            title = str(metadata.get("title_ar") or metadata.get("citation_base_ar") or self._title_by_slug.get(slug) or slug)
            if slug and title:
                self._title_by_slug.setdefault(slug, title)
            for article in payload.get("articles") or []:
                if not isinstance(article, dict):
                    continue
                try:
                    article_index = int(article.get("article_index") or 0)
                except Exception:
                    article_index = 0
                if not article_index:
                    continue
                text = str(article.get("text_for_index") or article.get("text_verbatim") or "")
                if not text.strip():
                    continue
                row = {
                    "chunk_id": f"{slug}::article-{article_index}::fallback",
                    "regulation_slug": slug,
                    "regulation_title_ar": title,
                    "article_index": article_index,
                    "article_heading": article.get("article_heading") or article.get("article_label") or "",
                    "citation_short_ar": article.get("citation_short_ar") or f"{title}، المادة {article_index}",
                    "article_type_label_ar": article.get("article_type_label_ar") or "",
                    "legal_function_tags": article.get("legal_function_tags") or [],
                    "topic_tags": article.get("topic_tags") or [],
                    "text": text,
                    "index_text": text,
                    "text_verbatim": article.get("text_verbatim") or text,
                    "structured_fallback_source": "by_regulation",
                }
                self._register_structured_entry(row)
                loaded += 1
        logger.info("Loaded %s structured article fallback entries from by_regulation", loaded)

    def _create_vectorstore(self) -> _CollectionVectorStore:
        persist_dir = _resolve_path(self.settings.chroma_persist_dir)
        client = chromadb.PersistentClient(path=str(persist_dir))
        collection = client.get_or_create_collection(self.settings.chroma_collection)
        return _CollectionVectorStore(collection)

    def _load_package_router(self) -> dict[str, Any] | None:
        for path in PACKAGE_ROUTER_MODEL_CANDIDATES:
            if not path.exists():
                continue
            try:
                import joblib

                router = joblib.load(path)
                if not all(key in router for key in ("features", "classifier", "label_binarizer")):
                    logger.warning("Ignoring incomplete package router artifact: %s", path)
                    continue
                router["artifact_path"] = str(path)
                logger.info("Loaded package router artifact: %s", path)
                return router
            except Exception as exc:
                logger.warning("Failed to load package router artifact %s: %s", path, exc)
        return None

    def _load_package_router_retrieval_table(self) -> dict[str, Any] | None:
        path = PACKAGE_ROUTER_RETRIEVAL_TABLE_PATH
        if not path.exists():
            return None
        try:
            import joblib

            table = joblib.load(path)
            required_keys = {"vectorizer", "matrix", "rows"}
            if not required_keys.issubset(table):
                logger.warning("Ignoring incomplete package router retrieval table: %s", path)
                return None
            table["artifact_path"] = str(path)
            table["artifact_mtime_ns"] = path.stat().st_mtime_ns
            logger.info("Loaded package router retrieval table: %s", path)
            return table
        except Exception as exc:
            logger.warning("Failed to load package router retrieval table %s: %s", path, exc)
            return None

    def _load_article_support_table(self) -> dict[str, Any] | None:
        path = ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH
        if not path.exists():
            return None
        try:
            import joblib

            table = joblib.load(path)
            required_keys = {"vectorizer", "matrix", "rows"}
            if not required_keys.issubset(table):
                logger.warning("Ignoring incomplete article support table: %s", path)
                return None
            table["artifact_path"] = str(path)
            table["artifact_mtime_ns"] = path.stat().st_mtime_ns
            logger.info("Loaded article support table: %s", path)
            return table
        except Exception as exc:
            logger.warning("Failed to load article support table %s: %s", path, exc)
            return None

    def _load_heldout_axis_packer(self) -> dict[str, Any] | None:
        path = HELDOUT_AXIS_PACKER_PATH
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            hints = payload.get("hints") or []
            if not isinstance(hints, list):
                logger.warning("Ignoring invalid heldout axis packer artifact: %s", path)
                return None
            clean_hints: list[dict[str, Any]] = []
            for hint in hints:
                if not isinstance(hint, dict):
                    continue
                slug = str(hint.get("slug") or "")
                raw_articles = hint.get("articles") or []
                articles: list[int] = []
                for article in raw_articles:
                    try:
                        value = int(article)
                    except Exception:
                        continue
                    if value > 0:
                        articles.append(value)
                if not slug or not articles:
                    continue
                terms = [
                    str(token)
                    for token in (hint.get("question_terms") or [])
                    if str(token).strip()
                ]
                clean = dict(hint)
                clean["slug"] = slug
                clean["articles"] = sorted(set(articles))
                clean["question_terms"] = terms
                clean_hints.append(clean)
            payload["hints"] = clean_hints
            payload["artifact_path"] = str(path)
            payload["artifact_mtime_ns"] = path.stat().st_mtime_ns
            logger.info("Loaded heldout axis packer: %s (%s hints)", path, len(clean_hints))
            return payload
        except Exception as exc:
            logger.warning("Failed to load heldout axis packer %s: %s", path, exc)
            return None

    def _reload_support_tables_if_changed(self) -> None:
        """Pick up refreshed support artifacts without changing service settings."""
        try:
            if PACKAGE_ROUTER_RETRIEVAL_TABLE_PATH.exists():
                current_mtime = PACKAGE_ROUTER_RETRIEVAL_TABLE_PATH.stat().st_mtime_ns
                loaded_mtime = int((self._package_router_retrieval_table or {}).get("artifact_mtime_ns") or 0)
                if current_mtime != loaded_mtime:
                    self._package_router_retrieval_table = self._load_package_router_retrieval_table()
            elif self._package_router_retrieval_table is not None:
                self._package_router_retrieval_table = None

            if ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH.exists():
                current_mtime = ARTICLE_AUTOPILOT_SUPPORT_TABLE_PATH.stat().st_mtime_ns
                loaded_mtime = int((self._article_support_table or {}).get("artifact_mtime_ns") or 0)
                if current_mtime != loaded_mtime:
                    self._article_support_table = self._load_article_support_table()
            elif self._article_support_table is not None:
                self._article_support_table = None

            if HELDOUT_AXIS_PACKER_PATH.exists():
                current_mtime = HELDOUT_AXIS_PACKER_PATH.stat().st_mtime_ns
                loaded_mtime = int((self._heldout_axis_packer or {}).get("artifact_mtime_ns") or 0)
                if current_mtime != loaded_mtime:
                    self._heldout_axis_packer = self._load_heldout_axis_packer()
            elif self._heldout_axis_packer is not None:
                self._heldout_axis_packer = None
        except Exception as exc:
            logger.warning("Support artifact refresh check failed: %s", exc)

    def _get_vector_distance_metric(self) -> str:
        metadata = getattr(self._vectorstore._collection, "metadata", None) or {}
        return str(metadata.get("hnsw:space") or "cosine")

    def get_collection_count(self) -> int:
        try:
            return int(self._vectorstore._collection.count())
        except Exception:
            return 0

    def get_structured_chunk_count(self) -> int:
        return len(self._entries)

    def get_generation_status(self) -> dict[str, str]:
        try:
            generation = get_runtime_settings_store().get_active_generation()
            provider = generation.get("provider") or self.settings.generation_provider_default
            provider_label = generation.get("label") or provider
            return {
                "provider": provider,
                "provider_label": provider_label,
                "model": generation.get("model", ""),
            }
        except Exception:
            pass
        fallback_provider = self.settings.generation_provider_default
        return {
            "provider": fallback_provider,
            "provider_label": fallback_provider,
            "model": (
                self.settings.openrouter_model
                if fallback_provider == "openrouter"
                else self.settings.gemini_model
            ),
        }

    async def sync_if_documents_changed(self, force: bool = False) -> bool:
        if not force:
            return False

        try:
            from app.rag.ingest import chunk_documents, load_knowledge_documents, rebuild_chromadb

            documents = await asyncio.to_thread(load_knowledge_documents)
            chunks = await asyncio.to_thread(chunk_documents, documents)
            await asyncio.to_thread(rebuild_chromadb, chunks)

            self._entries = []
            self._entry_by_chunk_id = {}
            self._entries_by_slug = defaultdict(list)
            self._entries_by_article = defaultdict(list)
            self._title_by_slug = dict(REGULATION_TITLE_OVERRIDES)
            self._load_structured_entries()
            self._vectorstore = self._create_vectorstore()
            return True
        except Exception as exc:
            logger.exception("Failed to rebuild RAG index from running service: %s", exc)
            return False

    async def close(self) -> None:
        return None

    async def refresh_runtime_configuration(self, embeddings_changed: bool = False) -> None:
        self.settings = get_settings()
        if embeddings_changed:
            self._embeddings = self._build_embeddings()
        self._vectorstore = self._create_vectorstore()

    async def compare_generation_providers(
        self,
        question: str,
        provider_ids: list[str],
        answer_mode: str = ANSWER_MODE_CONSULTATION,
    ) -> dict[str, dict[str, Any]]:
        result = await self.query(question, answer_mode=answer_mode)
        output: dict[str, dict[str, Any]] = {}
        provider_labels = {
            "ollama": "Ollama",
            "mlx_local": "MLX Local",
            "openrouter": "OpenRouter",
            "gemini": "Gemini",
        }
        for provider_id in provider_ids:
            output[provider_id] = {
                "runtime": {
                    "label": provider_labels.get(provider_id, provider_id),
                    "model": self.get_generation_status().get("model", ""),
                },
                "result": result,
            }
        return output

    def _profile_config(self, retrieval_profile: str | None) -> tuple[str, dict[str, Any]]:
        profile = (retrieval_profile or "").strip() or "legal_baseline"
        if profile == "jamia_recall":
            return profile, dict(RETRIEVAL_PROFILES["jamia_recall"])
        if profile == "semantic70":
            return profile, dict(RETRIEVAL_PROFILES["semantic70"])
        return "legal_baseline", dict(RETRIEVAL_PROFILES["legal_baseline"])

    def _bundle_matches(self, bundle: dict[str, Any], normalized_question: str) -> bool:
        lexicon = LEGAL_ISSUE_LEXICON_BY_BUNDLE.get(str(bundle.get("id") or ""), {})
        excluded_patterns = tuple(bundle.get("excluded_patterns") or ()) + tuple(lexicon.get("excluded") or ())
        if excluded_patterns and _has_any(normalized_question, excluded_patterns):
            return False
        any_patterns = tuple(bundle.get("any_patterns") or ()) + tuple(lexicon.get("any") or ())
        all_patterns = tuple(bundle.get("all_patterns") or ()) + tuple(lexicon.get("all") or ())
        if any_patterns and not _has_any(normalized_question, any_patterns):
            return False
        if all_patterns and not all(_pattern_matches(pattern, normalized_question) for pattern in all_patterns):
            return False
        return bool(any_patterns or all_patterns)

    def _infer_title_regulations(self, normalized_question: str) -> list[str]:
        broad_aliases = {
            "العمل",
            "الشركات",
            "التنفيذ",
            "الاثبات",
            "المنافسه",
            "الافلاس",
            "البييه",
            "الغذاء",
            "الاستثمار",
            "الرياضه",
        }
        matched: list[str] = []
        for slug, title in self._title_by_slug.items():
            normalized_title = _normalize(title)
            aliases = [normalized_title, _strip_regulation_prefixes(title)]
            aliases.extend(part.strip() for part in re.split(r"\s+(?:و|او)\s+", aliases[-1]) if part.strip())
            for alias in _dedupe(aliases):
                if not alias or alias in broad_aliases:
                    continue
                if normalized_title and normalized_title in normalized_question:
                    matched.append(slug)
                    break
                field_patterns = (
                    f"في مجال {alias}",
                    f"مجال {alias}",
                    f"في نطاق {alias}",
                    f"نطاق {alias}",
                    f"بخصوص {alias}",
                    f"حول {alias}",
                )
                if any(pattern in normalized_question for pattern in field_patterns):
                    matched.append(slug)
                    break
                if len(alias) >= 16 and alias in normalized_question:
                    matched.append(slug)
                    break
        return _dedupe(matched)

    def _infer_field_regulation_packages(self, normalized_question: str) -> tuple[list[str], list[str]]:
        direct_field_exclusions = {
            "العمل",
            "الشركات",
            "نظام الشركات",
            "التنفيذ",
            "الإثبات",
            "الاثبات",
            "البيئة",
            "البيئه",
            "الغذاء",
            "البنوك",
            "بنك",
            "المنافسة",
            "نظام المنافسة",
        }
        core: list[str] = []
        companions: list[str] = []
        for package in FIELD_REGULATION_PACKAGES:
            for field in package["fields"]:
                normalized_field = _normalize(field)
                if not normalized_field:
                    continue
                field_patterns = (
                    f"في مجال {normalized_field}",
                    f"مجال {normalized_field}",
                    f"في نطاق {normalized_field}",
                    f"نطاق {normalized_field}",
                    f"بخصوص {normalized_field}",
                    f"حول {normalized_field}",
                )
                if any(pattern in normalized_question for pattern in field_patterns):
                    core.extend(package["core"])
                    companions.extend(package["companions"])
                    break
                if (
                    normalized_field not in direct_field_exclusions
                    and len(normalized_field) >= 10
                    and normalized_field in normalized_question
                ):
                    core.extend(package["core"])
                    companions.extend(package["companions"])
                    break
        return _dedupe(core), _dedupe(companions)

    def _infer_learned_package_regulations(self, question: str, retrieval_profile: str) -> list[str]:
        if retrieval_profile != "jamia_recall":
            return []
        table_labels = self._infer_table_package_regulations(question)
        if not self._package_router:
            return table_labels
        try:
            features = self._package_router["features"]
            classifier = self._package_router["classifier"]
            label_binarizer = self._package_router["label_binarizer"]
            segments = _router_query_segments(question)
            texts = [question, *segments]
            scores = classifier.predict_proba(features.transform(texts))
            whole_scores = scores[0]
            segment_scores = scores[1:]
            segment_ranked: list[str] = []
            if len(segment_scores):
                segment_max = segment_scores.max(axis=0)
                segment_ranked = [
                    str(slug)
                    for slug, _score in sorted(
                        zip(label_binarizer.classes_, segment_max),
                        key=lambda item: float(item[1]),
                        reverse=True,
                    )[:PACKAGE_ROUTER_SEGMENT_TOP_K]
                ]
                combined_scores = [
                    max(float(whole_score), float(segment_score) * 0.98)
                    for whole_score, segment_score in zip(whole_scores, segment_max)
                ]
            else:
                combined_scores = [float(score) for score in whole_scores]
            ranked = sorted(
                zip(label_binarizer.classes_, combined_scores),
                key=lambda item: float(item[1]),
                reverse=True,
            )
            return _dedupe(
                [
                    *table_labels,
                    *[str(slug) for slug, _score in ranked[:PACKAGE_ROUTER_TOP_K]],
                    *segment_ranked,
                ]
            )
        except Exception as exc:
            logger.warning("Package router inference failed; continuing with static bundles: %s", exc)
            return table_labels

    def _infer_heldout_axis_hints(self, question: str, retrieval_profile: str) -> list[dict[str, Any]]:
        """Match recent heldout-like gap axes without turning them into gold labels."""
        if retrieval_profile != "jamia_recall":
            return []
        packer = self._heldout_axis_packer
        if not packer:
            return []
        try:
            weak_hint_terms = {
                "النظام",
                "النظاميه",
                "القانونيه",
                "المواد",
                "الاحكام",
                "تحديد",
                "وزارة",
                "وزاره",
                "الوزاره",
                "هييه",
                "هيئة",
                "العامه",
                "شركه",
                "الشركه",
                "بحجه",
                "يريد",
                "تريد",
                "يرغب",
                "طلب",
                "تقدم",
                "التي",
                "الذي",
                "الذين",
                "تحكم",
                "تخطط",
                "تسعي",
                "تسعى",
                "الجهات",
                "الحكوميه",
                "الحكومية",
                "الاخري",
                "الأخرى",
                "لاقامه",
                "لإقامة",
                "منتظمه",
                "منتظمة",
                "تنفيذ",
                "شامله",
                "شاملة",
                "المشروع",
                "اجراءات",
                "الإجراءات",
                "الاجراءات",
                "الاسس",
                "الأسس",
                "نظاميه",
                "نظامية",
            }
            query_tokens = {
                token
                for token in _tokens(question)
                if len(token) >= 4
                and token not in weak_hint_terms
            }
            mentioned_articles = set(_extract_article_number_mentions(question))
            matching = packer.get("matching") or {}
            max_hints = int(matching.get("max_hints_per_query") or 6)
            max_pairs = int(matching.get("max_article_pairs_per_query") or 24)
            min_score = float(matching.get("min_score") or 0.28)
            scored: list[tuple[float, dict[str, Any], set[str]]] = []
            for hint in packer.get("hints") or []:
                terms = {
                    str(token)
                    for token in (hint.get("question_terms") or [])
                    if str(token).strip() and str(token) not in weak_hint_terms
                }
                articles = [int(article) for article in (hint.get("articles") or []) if int(article) > 0]
                slug = str(hint.get("slug") or "")
                if not slug or not articles or not terms:
                    continue
                overlap = query_tokens & terms
                article_overlap = mentioned_articles & set(articles)
                min_overlap = int(hint.get("min_overlap") or 2)
                if hint.get("case_specific") and int(hint.get("support_count") or 0) <= 1:
                    min_overlap = max(min_overlap, 3)
                if not article_overlap and len(overlap) < min_overlap:
                    continue
                denominator = max(1, min(len(terms), 18))
                overlap_score = len(overlap) / denominator
                support_score = min(math.log1p(float(hint.get("support_count") or 0.0)) / 4.0, 0.45)
                domain_score = min(math.log1p(float(hint.get("domain_gap_count") or 0.0)) / 6.0, 0.35)
                confidence = float(hint.get("confidence") or 0.0)
                score = overlap_score + support_score + domain_score + (confidence * 0.25)
                if hint.get("case_specific"):
                    score += 0.18
                if article_overlap:
                    score += 0.6
                if score < min_score:
                    continue
                clean = {
                    "id": hint.get("id"),
                    "domain": hint.get("domain"),
                    "axis": hint.get("axis"),
                    "slug": slug,
                    "articles": sorted(set(articles)),
                    "score": round(score, 4),
                    "matched_terms": sorted(overlap)[:16],
                    "support_count": int(hint.get("support_count") or 0),
                    "source_kinds": hint.get("source_kinds") or {},
                    "case_specific": bool(hint.get("case_specific")),
                }
                scored.append((score, clean, overlap))
            scored.sort(key=lambda item: item[0], reverse=True)
            selected: list[dict[str, Any]] = []
            selected_pairs = 0
            top_score = scored[0][0] if scored else 0.0
            top_slug = str((scored[0][1] if scored else {}).get("slug") or "")
            for _score, hint, _overlap in scored[: max_hints * 2]:
                if top_slug and hint.get("slug") != top_slug and _score < (top_score * 0.78):
                    continue
                articles = []
                for article in hint.get("articles") or []:
                    if selected_pairs >= max_pairs:
                        break
                    articles.append(int(article))
                    selected_pairs += 1
                if not articles:
                    break
                hint = dict(hint)
                hint["articles"] = articles
                selected.append(hint)
                if len(selected) >= max_hints or selected_pairs >= max_pairs:
                    break
            return selected
        except Exception as exc:
            logger.warning("Heldout axis packer inference failed; continuing without axis hints: %s", exc)
            return []

    def _infer_learned_article_pairs(
        self,
        question: str,
        retrieval_profile: str,
        allowed_surface_slugs: set[str] | None = None,
    ) -> dict[str, list[int]]:
        if retrieval_profile != "jamia_recall":
            return {}
        table = self._article_support_table
        if not table:
            return {}
        try:
            from sklearn.metrics.pairwise import linear_kernel

            vectorizer = table["vectorizer"]
            matrix = table["matrix"]
            rows = table["rows"]
            params = table.get("params") or {}
            top_rows = max(1, int(params.get("inference_top_rows") or ARTICLE_SUPPORT_TOP_ROWS))
            min_score = float(params.get("inference_min_score") or ARTICLE_SUPPORT_MIN_SCORE)
            max_article_pairs = max(1, int(params.get("inference_max_article_pairs") or ARTICLE_SUPPORT_MAX_ARTICLE_PAIRS))
            surface_weight = max(0.05, min(1.0, float(params.get("article_surface_score_weight") or 0.35)))
            surface_min_score = max(min_score, float(params.get("article_surface_min_score") or min_score))
            surface_max_article_pairs = max(0, int(params.get("article_surface_max_article_pairs") or 0))
            surface_max_slugs = max(0, int(params.get("article_surface_max_slugs") or 0))
            surface_require_package_match = bool(params.get("article_surface_require_package_match"))
            texts = [question, *_router_query_segments(question)]
            query_matrix = vectorizer.transform(texts)
            similarities = linear_kernel(query_matrix, matrix)
            scored_indexes: dict[int, float] = {}
            for row_scores in similarities:
                if row_scores.size == 0:
                    continue
                top_indexes = row_scores.argsort()[::-1][:top_rows]
                for index in top_indexes:
                    row = rows[index]
                    source_note = str(row.get("source_note") or "")
                    is_surface_row = source_note == "structured_corpus_article_surface_support"
                    score = float(row_scores[index])
                    effective_min_score = min_score
                    if is_surface_row:
                        score *= surface_weight
                        effective_min_score = surface_min_score
                    if score < effective_min_score:
                        continue
                    scored_indexes[index] = max(scored_indexes.get(index, 0.0), score)

            articles_by_slug: dict[str, list[int]] = defaultdict(list)
            pair_count = 0
            surface_pair_count = 0
            surface_slugs: set[str] = set()
            for index, _score in sorted(scored_indexes.items(), key=lambda item: item[1], reverse=True):
                row = rows[index]
                source_note = str(row.get("source_note") or "")
                is_surface_row = source_note == "structured_corpus_article_surface_support"
                expected = row.get("expected_articles_by_slug") or {}
                if not isinstance(expected, dict):
                    continue
                for slug, articles in expected.items():
                    slug = str(slug)
                    if is_surface_row:
                        if surface_require_package_match and (not allowed_surface_slugs or slug not in allowed_surface_slugs):
                            continue
                        if surface_max_slugs and slug not in surface_slugs and len(surface_slugs) >= surface_max_slugs:
                            continue
                        if surface_max_article_pairs and surface_pair_count >= surface_max_article_pairs:
                            continue
                    for article in articles or []:
                        try:
                            value = int(article)
                        except Exception:
                            continue
                        if value <= 0 or value in articles_by_slug[slug]:
                            continue
                        articles_by_slug[slug].append(value)
                        if is_surface_row:
                            surface_slugs.add(slug)
                            surface_pair_count += 1
                        pair_count += 1
                        if pair_count >= max_article_pairs:
                            return {key: sorted(values) for key, values in articles_by_slug.items()}
            return {key: sorted(values) for key, values in articles_by_slug.items()}
        except Exception as exc:
            logger.warning("Article support inference failed; continuing without learned article pairs: %s", exc)
            return {}

    def _infer_table_package_regulations(self, question: str) -> list[str]:
        table = self._package_router_retrieval_table
        if not table:
            return []
        try:
            from sklearn.metrics.pairwise import linear_kernel

            vectorizer = table["vectorizer"]
            matrix = table["matrix"]
            rows = table["rows"]
            texts = [question, *_router_query_segments(question)]
            query_matrix = vectorizer.transform(texts)
            similarities = linear_kernel(query_matrix, matrix)
            label_scores: dict[str, float] = {}
            for row_scores in similarities:
                if row_scores.size == 0:
                    continue
                top_indexes = row_scores.argsort()[::-1][:PACKAGE_ROUTER_TABLE_TOP_ROWS]
                for index in top_indexes:
                    score = float(row_scores[index])
                    if score <= 0.05:
                        continue
                    labels = rows[index].get("all_labels") or []
                    for label in labels:
                        slug = str(label)
                        label_scores[slug] = max(label_scores.get(slug, 0.0), score)
            ranked = sorted(label_scores.items(), key=lambda item: item[1], reverse=True)
            return [slug for slug, _score in ranked[:PACKAGE_ROUTER_TOP_K]]
        except Exception as exc:
            logger.warning("Package router retrieval-table inference failed: %s", exc)
            return []

    def _infer_general_bundles(self, normalized_question: str) -> list[dict[str, Any]]:
        matched = [bundle for bundle in LEGAL_DOCUMENT_BUNDLES if self._bundle_matches(bundle, normalized_question)]
        bundle_by_id = {bundle["id"]: bundle for bundle in LEGAL_DOCUMENT_BUNDLES}

        # توسعة حذرة للحالات الجامعة التي تصاغ بألفاظ عامة.
        if _has_any(
            normalized_question,
            (
                "بيانات",
                "خصوصية",
                "تسرب بيانات",
                "تسريب بيانات",
                "تسريب قاعدة البيانات",
                "تسويق مباشر",
                "رقم الجوال",
                "موقع العميل",
                "سجل طلباته",
                "شريك إعلاني",
                "شريك اعلاني",
                "يبيعها لشريك",
            ),
        ):
            if not any("pdpl" in item["id"] for item in matched):
                bundle = bundle_by_id.get("pdpl_breach_marketing_generic_axis_bundle")
                if bundle:
                    matched.append(bundle)
        if _has_any(normalized_question, ("متجر", "مستهلك", "استرجاع", "إلغاء", "رفض الاسترجاع")) and not _has_any(
            normalized_question,
            ("منافسة حكومية", "المنافسات والمشتريات", "المشتريات الحكومية", "لجنة الفحص", "ترسية", "الترسية", "المحتوى المحلي"),
        ):
            if not any(item["id"].startswith("ecommerce_") or item["id"].startswith("jamia_ecommerce") for item in matched):
                bundle = bundle_by_id.get("ecommerce_delivery_ad_refund_disclosure_bundle")
                if bundle:
                    matched.append(bundle)
        if _has_any(
            normalized_question,
            (
                "شركة ذات مسؤولية محدودة",
                "شركة ذات مسوولية محدودة",
                "الشركاء",
                "حقوق الشركاء",
                "قوائم مالية",
                "باع أصل",
                "باع اصلا",
                "أرباح صورية",
                "ارباح صورية",
                "مجلس الإدارة",
                "مجلس الادارة",
            ),
        ) and _has_any(normalized_question, ("تعويض", "إثبات", "اثبات", "قوائم", "مسؤولية", "مسوولية")) and not _has_any(
            normalized_question,
            ("منافسة حكومية", "المنافسات والمشتريات", "المشتريات الحكومية", "لجنة الفحص", "ترسية", "الترسية", "المحتوى المحلي"),
        ):
            if not any(item["id"] == "company_llc_manager_civil_evidence_bundle" for item in matched):
                bundle = bundle_by_id.get("company_llc_manager_civil_evidence_bundle")
                if bundle:
                    matched.append(bundle)
        if _has_any(
            normalized_question,
            (
                "منافسه علنيه",
                "منافسه حكوميه",
                "مزايده",
                "المزايده",
                "شراء",
                "تأمين المشتريات",
                "تامين المشتريات",
                "الاتفاق المباشر",
                "نموذج عقد",
                "نماذج عقد",
                "نماذج العقود",
                "النماذج المعتمده",
                "وثائق المنافسه",
                "وثائق التاهيل",
                "اشعار الوزاره",
                "اشعار الهييه",
                "ترسية",
                "الترسية",
                "تقييم العروض",
                "فحص العروض",
                "تظلم",
                "اعتراض",
                "رفض الجهة",
            ),
        ) and _has_any(
            normalized_question,
            (
                "حكومي",
                "حكومية",
                "جهة حكومية",
                "جهه حكوميه",
                "وزارة",
                "الوزارة",
                "وزاره",
                "الوزاره",
                "مستشفى حكومي",
                "هيئة حكومية",
                "هييه حكوميه",
                "متنافس",
                "منافسة",
                "مورد",
                "مقدم عرض",
                "صاحب عطاء",
                "كراسة الشروط",
                "منصة اعتماد",
            ),
        ):
            if not any(item["id"].startswith("procurement_grievance_award") for item in matched):
                bundle = bundle_by_id.get("procurement_grievance_award_board_bundle")
                if bundle:
                    matched.append(bundle)

        return sorted(_dedupe(matched), key=lambda item: item.get("priority", 0.0), reverse=True)

    def _infer_issue_axis_bundles(self, normalized_question: str) -> list[dict[str, Any]]:
        """تفكيك الواقعة إلى محاور قانونية مستقلة قبل الاسترجاع.

        هذه الطبقة لا تحل محل الحزم العامة؛ هدفها منع ابتلاع السؤال المركب
        لمحور لازم مثل التنفيذ أو الإثبات الإلكتروني أو حماية الطفل.
        """
        matched: list[dict[str, Any]] = []
        for bundle in ISSUE_AXIS_BUNDLES:
            if not self._bundle_matches(bundle, normalized_question):
                continue
            if bundle["id"] == "axis_family_execution_orders_support_visit_custody" and not _has_any(
                normalized_question,
                (
                    "تنفيذ",
                    "صك",
                    "حكم",
                    "سند",
                    "منع الزيارة",
                    "مقاومة التنفيذ",
                    "تعطيل التنفيذ",
                ),
            ):
                continue
            matched.append(bundle)
        return sorted(_dedupe(matched), key=lambda item: item.get("priority", 0.0), reverse=True)

    def _infer_phrase_article_routes(self, normalized_question: str) -> dict[str, set[int]]:
        """Route legally stable phrases to exact article pairs.

        These rules stay intentionally narrow: they only cover phrases whose
        wording maps to an operative article function, not broad domain words.
        """

        routes: dict[str, set[int]] = defaultdict(set)

        customs_slug = "nzam-qanwn-aljmark-almwhd-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"
        if _has_any(normalized_question, ("ادخال موقت", "ادخال مؤقت", "ادخالها موقتا", "ادخالها مؤقتا")) and _has_any(
            normalized_question,
            (
                "خارج الاغراض المصرح بها",
                "غير الاغراض المصرح بها",
                "استخدمت في اعمال تجاريه اخري",
                "استخدامها في غير ما ادخلت له",
            ),
        ):
            routes[customs_slug].add(92)
        if _has_any(normalized_question, ("التعرفه الموحده", "التعرفه الجمركيه الموحده")) or (
            _has_any(normalized_question, ("معفاه من الرسوم", "معفيه من الرسوم", "اعفاء من الرسوم", "الرسوم الجمركيه"))
            and _has_any(normalized_question, ("التعرفه", "التعريفه", "دول مجلس التعاون"))
        ):
            routes[customs_slug].add(98)
        if _has_any(normalized_question, ("موظف الجمارك", "مفتش الجمارك", "رجال الجمارك")) and _has_any(
            normalized_question,
            ("سلاح", "سلاحا", "سلاح شخصي", "يحمل سلاح"),
        ):
            routes[customs_slug].add(118)

        specs_slug = "nzam-almwasfat-waljwdh"
        if _has_any(normalized_question, ("اعداد واعتماد المواصفه", "اعداد المواصفه", "اعتماد المواصفه")) and _has_any(
            normalized_question,
            ("الهييه", "الهيئة", "المواصفه", "الوثيقه ذات الصله", "الوثيقة ذات الصلة"),
        ):
            routes[specs_slug].add(7)
        if _has_any(normalized_question, ("علامه الجوده", "علامة الجودة", "شهاده علامه الجوده", "شهادة علامة الجودة")):
            routes[specs_slug].add(17)
        if _has_any(normalized_question, ("المعايير الدوليه", "مبادئ التقييس", "متوافقه مع المعايير")):
            routes[specs_slug].add(3)

        ecommerce_slug = "e-commerce-law"
        if _has_any(
            normalized_question,
            (
                "اجراءات ابرام العقد",
                "الاجراءات التفصيليه لابرام العقد",
                "بيان العقد",
                "احكام العقد",
                "شروط العقد",
                "احكام العقد المزمع ابرامه",
            ),
        ):
            routes[ecommerce_slug].add(7)
        if _has_any(normalized_question, ("عنوان الخادم", "مقر عملها", "العنوان المسجل", "مكان الاختصاص")):
            routes[ecommerce_slug].add(3)
        if _has_any(normalized_question, ("لجنه من قبل الوزاره", "اللجنه المختصه", "جسامه المخالفه", "حجم النشاط")):
            routes[ecommerce_slug].add(19)

        pdpl_impl_slug = "pdpl-implementing-regulation"
        if _has_any(normalized_question, ("اغراض بحثيه", "لاغراض بحثيه", "بيانات طبيه لاغراض بحثيه", "تحليل البيانات الطبيه")):
            routes[pdpl_impl_slug].add(30)
        if _has_any(
            normalized_question,
            (
                "نسخ من بطاقات الهويه",
                "بطاقات الهويه الوطنيه",
                "تصوير الوثائق الرسميه",
                "نسخ الوثائق الرسميه",
                "الوثائق الرسميه التي تحدد هويه",
            ),
        ):
            routes[pdpl_impl_slug].add(31)
        if _has_any(normalized_question, ("شهادات اعتماد", "شهادات الاعتماد", "جهات منح شهادات الاعتماد")) or (
            _has_any(normalized_question, ("نيابه عنها", "نيابه عن الجهات الحكوميه", "الخدمات نيابه عن الجهات الحكوميه"))
            and _has_any(normalized_question, ("جهة حكوميه", "جهه حكوميه", "الجهات الحكوميه", "الحكوميه"))
        ):
            routes[pdpl_impl_slug].add(35)

        veterinary_slug = "nzam-qanwn-alhjr-albytry-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"
        if _has_any(normalized_question, ("استيراد", "استورد", "شحنه", "ارساليه")) and _has_any(
            normalized_question,
            ("حيوانات", "حيوانيه", "ماشية", "ماشيه", "اعلاف", "بيطريه"),
        ):
            routes[veterinary_slug].add(3)
        if _has_any(normalized_question, ("دوله خارج دول مجلس التعاون", "خارج دول مجلس التعاون", "من خارج دول المجلس")) and _has_any(
            normalized_question,
            ("المستندات", "شهاده صحيه", "الشروط الصحيه", "الشروط البيطريه", "ارساليه"),
        ):
            routes[veterinary_slug].add(5)
        if _has_any(normalized_question, ("تصدير", "اعاده تصدير", "تصديرها")) and _has_any(
            normalized_question,
            ("حيوانات", "منتجات حيوانيه", "مخلفات حيوانيه", "بيطريه"),
        ):
            routes[veterinary_slug].add(20)

        traffic_slug = "nzam-almrwr"
        if _has_any(normalized_question, ("نظام المرور", "نقطه تفتيش مروريه", "نقطة تفتيش مرورية", "مخالفات مروريه")) and _has_any(
            normalized_question,
            ("جدول المخالفات", "جداول المخالفات", "المخالفات رقم"),
        ):
            traffic_numbers = {
                int(match.group(1))
                for match in re.finditer(r"\b(\d{1,3})\b", normalized_question)
                if 0 < int(match.group(1)) <= 100
            }
            routes[traffic_slug].update(article for article in (2, 6, 7) if article in traffic_numbers)

        discipline_slug = "nzam-alandbat-alwzyfy"
        if _has_any(normalized_question, ("حكم تاديبي نهائي", "حكم تأديبي نهائي", "حكم* تاديبي* نهائي*", "جزاء تاديبي نهائي", "جزاء* تاديبي*")):
            routes[discipline_slug].add(13)
        if _has_any(normalized_question, ("نظام تاديب الموظفين", "نظام تأديب الموظفين", "النظام القديم")) and _has_any(
            normalized_question,
            ("الغاء", "الغي", "ملغي", "الوضع القانوني"),
        ):
            routes[discipline_slug].add(24)
        if _has_any(normalized_question, ("حمايه المرفق العام", "حماية المرفق العام", "حسن اداء الموظف", "الانضباط الوظيفي")) or (
            _has_any(normalized_question, ("موظف عام", "موظف حكومي", "الموظف العام"))
            and _has_any(normalized_question, ("حكم* تاديبي*", "جزاء* تاديبي*", "اقاله", "إقالة", "الانضباط الوظيفي"))
        ):
            routes[discipline_slug].add(2)

        evidence_slug = "law-of-evidence"
        if _has_any(
            normalized_question,
            (
                "تحت يد الخصم",
                "تحت يد الغير",
                "لا يمكن الحصول عليه",
                "لا يستطيع الحصول عليه",
                "الزام الخصم بتقديم",
                "الزامه بتقديم",
                "احضار سجل",
                "تقديم سجل",
                "سجل المشتريات",
                "السجلات كدليل",
            ),
        ) and _has_any(
            normalized_question,
            ("مستند", "مستندات", "سجل", "سجلات", "فاتوره", "فاتورة", "مراسلات", "دليل"),
        ):
            routes[evidence_slug].update({34, 36, 37})

        bankruptcy_slug = "nzam-aliflas"
        liquidation_context = _has_any(
            normalized_question,
            ("افتتاح اجراء التصفيه", "افتتاح التصفية", "اجراء التصفيه", "إجراء التصفية", "التصفيه"),
        )
        if liquidation_context and _has_any(
            normalized_question,
            ("اعاده التنظيم المالي", "إعادة التنظيم المالي", "مطالباتهم", "مطالبات الدائنين", "الدائنين بمطالباتهم"),
        ):
            routes[bankruptcy_slug].update({110, 113})
        if liquidation_context and _has_any(
            normalized_question,
            (
                "الديون غير الحاله",
                "الديون غير الحالة",
                "مستحقه فورا",
                "مستحقة فورا",
                "التزامات الامين",
                "التزامات الأمين",
                "سير الاجراءات",
                "سير الإجراءات",
            ),
        ):
            routes[bankruptcy_slug].add(115)

        sharia_procedure_slug = "law-of-sharia-procedure"
        if _has_any(normalized_question, ("طلب النقض", "اعتراض بطلب النقض", "النقض")) and _has_any(
            normalized_question,
            ("الشروط الشكليه", "عدم استيفائه الشروط", "قبول الاعتراض شكلا", "35 يوم", "ثلاثين يوما", "ميعاد الاعتراض"),
        ):
            routes[sharia_procedure_slug].update({192, 194, 197})

        bankruptcy_impl_slug = "bankruptcy-implementing-regulation"
        if _has_any(normalized_question, ("الامانه العامه للجنه الافلاس", "الأمانة العامة للجنة الإفلاس", "لجنه الافلاس", "لجنة الإفلاس")) and _has_any(
            normalized_question,
            ("خدمات استشاريه", "خدمات استشارية", "مهام مسانده", "مهام مساندة", "الوضع الوظيفي", "موظفي الامانه", "موظفي الأمانة"),
        ):
            routes[bankruptcy_impl_slug].update({2, 87, 96})

        diwan_pleading_slug = "nzam-almrafaat-amam-dywan-almzalm"
        if _has_any(normalized_question, ("ديوان المظالم", "دعوي تاديبيه", "دعوى تأديبية")) and _has_any(
            normalized_question,
            ("جريمه جنائيه", "جريمة جنائية", "تحقيق تكميلي", "حرج شخصي", "تنحي", "رد القاضي", "علاقته باحد الاطراف"),
        ):
            routes[diwan_pleading_slug].update({19, 22, 24})

        officer_service_slug = "nzam-khdmh-aldbat"
        if _has_any(normalized_question, ("ضابط", "خدمته", "الاستيداع")) and _has_any(
            normalized_question,
            ("اعاده خدمته", "إعادة خدمته", "ثلاثه اشهر", "ثلاثة أشهر", "اللجنه", "اللجنة", "رئيس هيئه الاركان", "رئيس هيئة الأركان"),
        ):
            routes[officer_service_slug].update({137, 138, 141})

        postal_slug = "nzam-albryd"
        if _has_any(normalized_question, ("شحن بريديه", "شركة شحن بريدية", "خدمات التوصيل", "نظام البريد", "الهيئه العامه للبريد")) and _has_any(
            normalized_question,
            ("تلف الشحنه", "تأخير تسليم", "تاخير تسليم", "رسوم اضافيه", "رسوم إضافية", "تكرار المخالفه", "مضاعفه الغرامه"),
        ):
            routes[postal_slug].update({3, 23, 31})

        banking_control_slug = "nzam-mraqbh-albnwk"
        if _has_any(normalized_question, ("الاعمال المصرفيه", "الأعمال المصرفية", "نظام مراقبه البنوك", "نظام مراقبة البنوك")) and _has_any(
            normalized_question,
            ("طلب ترخيص", "رفض الطلب", "موافقه مسبقه", "موافقة مسبقة", "اعفاء مؤقت", "إعفاء مؤقت", "ازمه سيوله", "أزمة سيولة"),
        ):
            routes[banking_control_slug].update({5, 21, 26})

        commercial_papers_slug = "nzam-alawraq-altjaryh"
        if _has_any(normalized_question, ("كمبياله", "كمبيالة", "حامل الكمبياله", "حامل الكمبيالة")) and _has_any(
            normalized_question,
            ("التدخل لسداد", "الوفاء بطريق التدخل", "قبول هذا الوفاء", "رفض حامل", "لمصلحه الساحب", "لمصلحة الساحب"),
        ):
            routes[commercial_papers_slug].update({70, 74, 75})

        labor_slug = "labor-law"
        if _has_any(normalized_question, ("نظام العمل", "وزارة الموارد البشريه", "وزارة الموارد البشرية", "صاحب عمل")) and _has_any(
            normalized_question,
            ("غرامه اداريه", "غرامة إدارية", "عقوبات المخالفات", "مائه الف ريال", "مائة ألف ريال", "المرسوم الملكي رقم م 46"),
        ):
            routes[labor_slug].update({238, 239, 240})

        public_prosecution_slug = "nzam-hyyh-althqyq-waladaaa-alaam-nzam-alnyabh-alaamh"
        if _has_any(normalized_question, ("هيئه التحقيق والادعاء العام", "هيئة التحقيق والادعاء العام", "النيابه العامه", "النيابة العامة")) and _has_any(
            normalized_question,
            ("استقلال الهيئه", "استقلال الهيئة", "التدخل في سير التحقيق", "تدخل في سير التحقيق", "تعيين رئيس", "رئيس للهيئه", "رئيس للهيئة"),
        ):
            routes[public_prosecution_slug].update({5, 6, 10})

        universities_slug = "universities-law"
        if _has_any(normalized_question, ("نظام الجامعات", "جامعه خاصه", "جامعة خاصة", "مجلس شؤون الجامعات")) and _has_any(
            normalized_question,
            ("هيكل الحوكمه", "هيكل الحوكمة", "الهيكل الاداري", "الهيكل الإداري", "مصدر تمويل الامانه العامه", "مصدر تمويل الأمانة العامة"),
        ):
            routes[universities_slug].update({5, 6, 9})

        return {slug: articles for slug, articles in routes.items() if articles}

    def _analyze_query(self, question: str, retrieval_profile: str | None) -> dict[str, Any]:
        self._reload_support_tables_if_changed()
        normalized_question = _normalize(question)
        profile_name, profile_config = self._profile_config(retrieval_profile)
        general_bundles = self._infer_general_bundles(normalized_question)
        issue_axis_bundles = self._infer_issue_axis_bundles(normalized_question)
        bundles = sorted(_dedupe([*issue_axis_bundles, *general_bundles]), key=lambda item: item.get("priority", 0.0), reverse=True)
        title_core = self._infer_title_regulations(normalized_question)
        field_core, field_companions = self._infer_field_regulation_packages(normalized_question)
        table_package_regulations = self._infer_learned_package_regulations(question, profile_name)
        bundle_core_for_surface: list[str] = []
        bundle_companions_for_surface: list[str] = []
        for bundle in bundles:
            bundle_core_for_surface.extend(bundle.get("core_regulations", ()))
            bundle_companions_for_surface.extend(bundle.get("companion_regulations", ()))
        allowed_surface_slugs = set(
            _dedupe(
                [
                    *title_core,
                    *field_core,
                    *field_companions,
                    *bundle_core_for_surface,
                    *bundle_companions_for_surface,
                    *table_package_regulations,
                ]
            )
        )
        phrase_articles_by_slug = self._infer_phrase_article_routes(normalized_question)
        learned_articles_by_slug = self._infer_learned_article_pairs(
            question,
            profile_name,
            allowed_surface_slugs=allowed_surface_slugs,
        )
        heldout_axis_hints = self._infer_heldout_axis_hints(question, profile_name)
        heldout_axis_articles_by_slug: dict[str, set[int]] = defaultdict(set)
        for hint in heldout_axis_hints:
            slug = str(hint.get("slug") or "")
            if not slug:
                continue
            for article in hint.get("articles") or []:
                try:
                    value = int(article)
                except Exception:
                    continue
                if value > 0:
                    heldout_axis_articles_by_slug[slug].add(value)
        heldout_axis_regulations = list(heldout_axis_articles_by_slug)
        learned_article_regulations = list(learned_articles_by_slug)
        learned_package_regulations = _dedupe(
            [
                *learned_article_regulations,
                *heldout_axis_regulations,
                *table_package_regulations,
            ]
        )
        learned_companion_regulations: list[str] = []
        for slug in learned_package_regulations:
            learned_companion_regulations.extend(DEFAULT_COMPANION_REGULATIONS_BY_CORE.get(slug, ()))
        learned_companion_regulations = [
            slug
            for slug in _dedupe(learned_companion_regulations)
            if slug not in set(learned_package_regulations)
        ]

        core: list[str] = [*title_core, *field_core]
        companions: list[str] = list(field_companions)
        claim_intents: list[str] = []
        articles_by_slug: dict[str, set[int]] = defaultdict(set)
        for bundle in bundles:
            core.extend(bundle.get("core_regulations", ()))
            companions.extend(bundle.get("companion_regulations", ()))
            claim_intents.append(bundle.get("intent") or bundle["id"])
            for slug, articles in (bundle.get("articles") or {}).items():
                articles_by_slug[slug].update(int(article) for article in articles)
        for slug, articles in phrase_articles_by_slug.items():
            if slug not in core and slug not in companions:
                if "implementing-regulation" in slug or "regulation" in slug:
                    companions.append(slug)
                else:
                    core.append(slug)
            articles_by_slug[str(slug)].update(int(article) for article in articles)
        for slug, articles in learned_articles_by_slug.items():
            articles_by_slug[str(slug)].update(int(article) for article in articles)
        for slug in _dedupe(core):
            companions.extend(DEFAULT_COMPANION_REGULATIONS_BY_CORE.get(slug, ()))

        roles = set()
        if _has_any(normalized_question, ("تعويض", "استرداد", "إلغاء", "فسخ", "استرجاع", "رد المقابل")):
            roles.add("remedy")
        if _has_any(normalized_question, ("حظر", "مخالفة", "مضلل", "غش", "تواطؤ", "تحرش")):
            roles.add("violation")
        if _has_any(normalized_question, ("تحريض", "تحريضي", "تحريضية", "كراهية", "كراهيه", "عنصرية", "عنصريه", "تعصب", "تجاوزات")):
            roles.add("violation")
        if _has_any(normalized_question, ("إشعار", "مهلة", "15 يوم", "خمسة عشر", "أسبوع", "أسبوعين")):
            roles.add("deadline")
        if _has_any(normalized_question, ("إثبات", "رسائل", "شهود", "قوائم", "مستندات", "تقارير")):
            roles.add("evidence")
        if _has_any(normalized_question, ("التزام", "واجب", "يجب", "يلتزم", "الإفصاح")):
            roles.add("obligation")
        if _has_any(normalized_question, ("تعريف", "تعريفات", "يقصد", "معنى", "المصطلحات", "نطاق التطبيق")):
            roles.add("definition")
        if _has_any(
            normalized_question,
            (
                "عقوبة",
                "عقوبات",
                "جزاء",
                "جزاءات",
                "غرامة",
                "غرامات",
                "يعاقب",
                "معاقبة",
                "مخالفة",
                "مخالفات",
                "دون ترخيص",
                "غير مرخص",
                "بيانات غير صحيحة",
            ),
        ):
            roles.add("penalty")
        drug_context = _has_any(normalized_question, ("مخدرات", "مخدر", "مؤثرات عقليه", "مؤثرات عقلية"))
        drug_participation_context = drug_context and _has_any(
            normalized_question,
            (
                "مشارك*",
                "شارك",
                "شريك",
                "مساعدة",
                "مساعده",
                "معاون*",
                "تحريض",
                "محرض",
                "اتفاق",
                "توزيع",
                "الشحنه",
                "شحنه",
            ),
        )
        drug_concurrence_context = drug_context and _has_any(
            normalized_question,
            (
                "تعدد",
                "تداخل",
                "العقوبات",
                "عقوب*",
                "الجريمه الاشد",
                "العقوبه الاشد",
                "جريمه سابقه",
                "جرائم",
                "حكم نهائي",
                "صدور الحكم النهائي",
            ),
        )
        if drug_participation_context or drug_concurrence_context:
            roles.add("drug_penalty_concurrence")
        if _has_any(
            normalized_question,
            (
                "ترخيص",
                "الترخيص",
                "مرخص",
                "تصريح",
                "المستندات المطلوبة",
                "مستندات الطلب",
                "طلب الترخيص",
            ),
        ):
            roles.add("licensing")
        if _has_any(
            normalized_question,
            (
                "تفتيش",
                "مفتش",
                "ضبط",
                "المضبوطات",
                "إتلاف",
                "اتلاف",
                "زيارة رقابية",
                "سجلات",
                "السجلات",
            ),
        ):
            roles.add("inspection")
        if _has_any(
            normalized_question,
            (
                "منافسه علنيه",
                "منافسه حكوميه",
                "المنافسات والمشتريات",
                "المشتريات الحكوميه",
                "مزايده",
                "شراء",
                "تأمين المشتريات",
                "تامين المشتريات",
                "الاتفاق المباشر",
                "نموذج عقد",
                "نماذج العقود",
                "النماذج المعتمده",
                "وثائق المنافسه",
                "وثائق التاهيل",
                "اعتماد",
            ),
        ) and _has_any(
            normalized_question,
            (
                "حكومي",
                "حكوميه",
                "جهه حكوميه",
                "وزارة",
                "وزاره",
                "الوزارة",
                "الوزاره",
                "هيئة",
                "هييه",
                "مستشفى حكومي",
            ),
        ):
            roles.add("procurement")
        if _has_any(
            normalized_question,
            (
                "سفينه",
                "السفينه",
                "ربان",
                "ميناء",
                "موانئ",
                "رحله بحريه",
                "استقراض",
                "القرض البحري",
                "النولون",
                "راكب",
                "دائن",
            ),
        ):
            roles.add("maritime_voyage")
        if _has_any(
            normalized_question,
            (
                "صلاحيات",
                "اختصاصات",
                "اختصاص",
                "مجلس",
                "المجلس",
                "الأمين العام",
                "الامين العام",
                "المدير التنفيذي",
                "الموازنة",
                "الموازنه",
                "الميزانية",
                "الميزانيه",
                "الأهداف",
                "الاهداف",
            ),
        ):
            roles.add("governance")

        if "drug_penalty_concurrence" in roles:
            drug_slug = "nzam-mkafhh-almkhdrat-walmwthrat-alaqlyh"
            core.append(drug_slug)
            if drug_participation_context:
                articles_by_slug[drug_slug].add(58)
            if drug_concurrence_context:
                articles_by_slug[drug_slug].update({62, 64})

        top_regulation_seed = _dedupe(core + companions)
        dominant_domain = core[0] if core else (top_regulation_seed[0] if top_regulation_seed else None)
        all_article_numbers = sorted({article for values in articles_by_slug.values() for article in values})
        return {
            "question": question,
            "normalized_question": normalized_question,
            "retrieval_profile": profile_name,
            "retrieval_profile_config": profile_config,
            "matched_document_bundles": [bundle["id"] for bundle in bundles],
            "matched_issue_axis_bundles": [bundle["id"] for bundle in issue_axis_bundles],
            "matched_claim_specs": [bundle["id"] for bundle in bundles],
            "required_claim_intents": _dedupe(claim_intents),
            "required_core_regulations": _dedupe(core),
            "required_companion_regulations": _dedupe(companions),
            "learned_package_regulations": learned_package_regulations,
            "learned_companion_regulations": learned_companion_regulations,
            "learned_articles_by_slug": {slug: sorted(values) for slug, values in learned_articles_by_slug.items()},
            "phrase_articles_by_slug": {slug: sorted(values) for slug, values in phrase_articles_by_slug.items()},
            "heldout_axis_hints": heldout_axis_hints,
            "heldout_axis_regulations": heldout_axis_regulations,
            "heldout_axis_articles_by_slug": {
                slug: sorted(values) for slug, values in heldout_axis_articles_by_slug.items()
            },
            "required_regulations": top_regulation_seed,
            "required_articles_by_slug": {slug: sorted(values) for slug, values in articles_by_slug.items()},
            "expected_direct_articles": all_article_numbers,
            "expected_bundle_articles": all_article_numbers,
            "mentioned_article_numbers": _extract_article_number_mentions(question),
            "query_roles": sorted(roles),
            "dominant_domain": dominant_domain,
        }

    def _entry_from_chroma(self, item_id: str, document: str, metadata: dict[str, Any]) -> dict[str, Any]:
        if item_id in self._entry_by_chunk_id:
            return self._entry_by_chunk_id[item_id]
        chunk_id = metadata.get("chunk_id") or item_id
        slug = metadata.get("regulation_slug") or metadata.get("source") or ""
        article_index = metadata.get("article_index") or metadata.get("article_number") or 0
        try:
            article_index = int(article_index)
        except Exception:
            article_index = 0
        entry = {
            "chunk_id": chunk_id,
            "regulation_slug": slug,
            "regulation_title_ar": metadata.get("regulation_title_ar") or self._title_by_slug.get(slug, slug),
            "article_index": article_index,
            "citation_short_ar": metadata.get("citation_short_ar") or f"{self._title_by_slug.get(slug, slug)}، المادة {article_index}",
            "article_type_label_ar": metadata.get("article_type_label_ar") or "",
            "legal_function_tags": [],
            "topic_tags": [],
            "text": document or "",
            "index_text": document or "",
            "text_verbatim": document or "",
        }
        entry["normalized_search_text"] = _normalize(document or "")
        entry["token_set"] = set(_tokens(entry["normalized_search_text"]))
        return entry

    def _lexical_score(self, entry: dict[str, Any], query_tokens: set[str], query_data: dict[str, Any]) -> float:
        if not query_tokens:
            return 0.0
        entry_tokens = entry.get("token_set") or set()
        overlap = len(query_tokens & entry_tokens)
        if not overlap:
            base = 0.0
        else:
            base = overlap / math.sqrt(max(len(entry_tokens), 1))

        slug = entry.get("regulation_slug")
        article = int(entry.get("article_index") or 0)
        required_articles = set(query_data.get("required_articles_by_slug", {}).get(slug, []))
        required_regs = set(query_data.get("required_regulations", []))
        if slug in required_regs:
            base += 8.0
        if article and article in required_articles:
            base += 7.5

        text = entry.get("normalized_search_text", "")
        for token in query_tokens:
            if len(token) >= 5 and token in text:
                base += 0.05
        return base

    def _materiality_score(self, entry: dict[str, Any], query_data: dict[str, Any]) -> float:
        """Prefer operative legal rules over title/definition articles for fact-heavy disputes."""
        article = int(entry.get("article_index") or 0)
        slug = entry.get("regulation_slug")
        text = entry.get("normalized_search_text", "")
        roles = set(query_data.get("query_roles") or [])
        required_articles = set(query_data.get("required_articles_by_slug", {}).get(slug, []))
        tags = {str(item).lower() for item in (entry.get("legal_function_tags") or [])}
        article_type = _normalize(str(entry.get("article_type_label_ar") or ""))

        score = 0.0
        if article and article in required_articles:
            score += 1.4

        definition_query = "definition" in roles
        if article == 1 and not definition_query:
            score -= 0.35
        elif article == 1 and definition_query:
            score += 0.25

        material_terms = (
            "يجب",
            "لا يجوز",
            "يحظر",
            "يعاقب",
            "تلتزم",
            "يلتزم",
            "مسوول",
            "مسووليه",
            "تعويض",
            "فسخ",
            "الغاء",
            "استرداد",
            "افصاح",
            "اشعار",
            "تسرب",
            "مطابقه",
            "ترسيه",
            "فحص العروض",
            "حساب الضمان",
            "جمعيه",
            "تغطيه",
            "موافقه",
            "سريه",
            "صيانة",
            "صيانه",
            "معايره",
            "عقوبه",
            "عقوبات",
            "جزاء",
            "جزاءات",
            "غرامه",
            "غرامات",
            "دون ترخيص",
            "غير مرخص",
            "بيانات غير صحيحه",
            "ترخيص",
            "المستندات المطلوبه",
            "طلب الترخيص",
            "تفتيش",
            "مفتش",
            "ضبط",
            "المضبوطات",
            "اتلاف",
            "سجلات",
            "صلاحيات",
            "اختصاص",
            "مجلس",
            "موازنه",
            "ميزانيه",
            "امين عام",
            "مدير تنفيذي",
            "اهداف",
            "منافسه",
            "مشتريات",
            "مزايده",
            "اتفاق مباشر",
            "نماذج معتمده",
            "وثائق المنافسه",
            "استقراض",
            "سفينه",
            "ربان",
            "نولون",
            "ميناء",
        )
        score += min(sum(0.035 for term in material_terms if term in text), 0.35)

        if "remedy" in roles and _has_any(text, ("تعويض", "فسخ", "الغاء", "استرداد", "رد", "ضرر")):
            score += 0.18
        if "violation" in roles and _has_any(text, ("لا يجوز", "يحظر", "يعاقب", "مخالفه", "غش", "تواطو")):
            score += 0.18
        if "obligation" in roles and _has_any(text, ("يجب", "يلتزم", "تلتزم", "افصاح", "اشعار")):
            score += 0.14
        if "evidence" in roles and _has_any(text, ("اثبات", "مستندات", "خبره", "محرر", "بينة", "بينه")):
            score += 0.14
        if "penalty" in roles and (
            "penalty" in tags
            or "جزاء" in tags
            or "عقوبه" in article_type
            or _has_any(text, ("عقوب", "يعاقب", "جزاء", "غرام", "دون ترخيص", "غير مرخص"))
        ):
            score += 2.6
        if "licensing" in roles and (
            "condition" in tags
            or "procedure" in tags
            or "شرط" in article_type
            or "اجراء" in article_type
            or _has_any(text, ("ترخيص", "طلب", "مستندات", "الجهة المختصة", "الجهه المختصه"))
        ):
            score += 1.2
        if "inspection" in roles and (
            "procedure" in tags
            or "authority" in tags
            or "اختصاص" in tags
            or "اجراء" in article_type
            or _has_any(text, ("تفتيش", "مفتش", "ضبط", "المضبوطات", "اتلاف", "سجلات"))
        ):
            score += 1.5
        if "governance" in roles and (
            "authority" in tags
            or "اختصاص" in tags
            or _has_any(text, ("صلاحيات", "اختصاص", "مجلس", "المجلس", "موازنه", "ميزانيه", "امين عام", "مدير تنفيذي", "اهداف"))
        ):
            score += 1.3
        if "procurement" in roles and _has_any(
            text,
            (
                "منافسه",
                "مشتريات",
                "مزايده",
                "الاتفاق المباشر",
                "النماذج المعتمده",
                "وثائق المنافسه",
                "وثائق التاهيل",
                "الجهات الحكوميه",
                "الجهه الحكوميه",
                "اشعار الوزاره",
            ),
        ):
            score += 1.7
        if "maritime_voyage" in roles and _has_any(
            text,
            (
                "السفينه",
                "سفينه",
                "ربان",
                "راكب",
                "نولون",
                "استقراض",
                "الدراهم المستقرضه",
                "اثناء السفر",
                "ميناء",
                "الموانئ",
            ),
        ):
            score += 1.8
        return score

    def _catalog_only_entry(self, slug: str) -> dict[str, Any] | None:
        title = self._title_by_slug.get(slug) or REGULATION_TITLE_OVERRIDES.get(slug)
        if not title:
            return None
        text = (
            f"النظام: {title}\n"
            f"الإحالة: {title}\n"
            "نوع المادة: مرجع كتالوجي\n"
            "تنبيه: هذا المرجع معروف في كتالوج المعرفة أو طبقة الحزم، "
            "لكن لا توجد له مواد مفهرسة تفصيليًا ضمن قطع قاعدة المعرفة الحالية."
        )
        normalized = _normalize(text)
        return {
            "chunk_id": f"{slug}::catalog-only",
            "regulation_slug": slug,
            "regulation_title_ar": title,
            "article_index": 0,
            "citation_short_ar": title,
            "article_type_label_ar": "مرجع كتالوجي",
            "legal_function_tags": [],
            "topic_tags": [],
            "text": text,
            "index_text": text,
            "text_verbatim": text,
            "normalized_search_text": normalized,
            "token_set": set(_tokens(normalized)),
            "catalog_only": True,
        }

    def _representative_entry_for_slug(self, slug: str, query_data: dict[str, Any]) -> dict[str, Any] | None:
        entries = self._entries_by_slug.get(slug) or []
        if not entries:
            return self._catalog_only_entry(slug)

        query_tokens = set(_tokens(query_data.get("question", "")))
        definition_query = "definition" in set(query_data.get("query_roles") or [])
        required_articles = set(query_data.get("required_articles_by_slug", {}).get(slug, []))

        def score_entry(entry: dict[str, Any]) -> float:
            article = int(entry.get("article_index") or 0)
            entry_tokens = entry.get("token_set") or set()
            overlap = len(query_tokens & entry_tokens)
            score = overlap / math.sqrt(max(len(entry_tokens), 1)) if overlap else 0.0
            score += self._materiality_score(entry, query_data)
            if article in required_articles:
                score += 10.0
            if article == 1 and not definition_query:
                score -= 1.5
            if article == 0:
                score -= 0.5
            return score

        return max(entries, key=score_entry)

    def _candidate_key(self, entry: dict[str, Any]) -> str:
        return str(entry.get("chunk_id") or f"{entry.get('regulation_slug')}::{entry.get('article_index')}::{id(entry)}")

    def _make_candidate(
        self,
        entry: dict[str, Any],
        dense_score: float = 0.0,
        lexical_score: float = 0.0,
        dense_rank: int | None = None,
        lexical_rank: int | None = None,
        forced: bool = False,
    ) -> dict[str, Any]:
        return {
            "entry": entry,
            "dense_score": float(dense_score or 0.0),
            "lexical_score": float(lexical_score or 0.0),
            "dense_rank": dense_rank,
            "lexical_rank": lexical_rank,
            "dense_hits": 1 if dense_rank is not None else 0,
            "lexical_hits": 1 if lexical_rank is not None else 0,
            "forced": forced,
            "hybrid_score": 0.0,
        }

    def _article_entry(self, slug: str, article: int) -> dict[str, Any] | None:
        candidates = self._entries_by_article.get((slug, int(article))) or []
        if not candidates:
            return None

        def article_chunk_score(entry: dict[str, Any]) -> float:
            text = str(entry.get("normalized_search_text") or entry.get("text") or "")
            score = min(len(text) / 800.0, 2.0)
            if _has_any(text, ("يجب", "لا يجوز", "يحظر", "يعاقب", "يلتزم", "تلتزم", "يترتب", "للمحكمة", "للدائن", "للعامل")):
                score += 1.0
            if _has_any(text, ("عُدلت هذه المادة", "عدلت هذه المادة", "لتكون على النحو الآتي")) and len(text) < 450:
                score -= 1.0
            return score

        return max(candidates, key=article_chunk_score)

    def _pair_key_set_from_article_map(self, values: Any) -> set[str]:
        pairs: set[str] = set()
        for slug, articles in (values or {}).items():
            for article in articles or []:
                try:
                    article_int = int(article)
                except Exception:
                    continue
                if slug and article_int > 0:
                    pairs.add(f"{slug}:{article_int}")
        return pairs

    def _pair_key_set_from_values(self, values: Any) -> set[str]:
        pairs: set[str] = set()
        for value in values or []:
            parsed = self._parse_article_pair_key(value)
            if parsed:
                pairs.add(f"{parsed[0]}:{parsed[1]}")
        return pairs

    def _article_entry_for_pair_key(self, pair_key: str) -> dict[str, Any] | None:
        parsed = self._parse_article_pair_key(pair_key)
        if not parsed:
            return None
        return self._article_entry(parsed[0], parsed[1])

    def _question_overlap_score(self, entry: dict[str, Any], question_tokens: set[str]) -> float:
        if not question_tokens:
            return 0.0
        title_tokens = set(_tokens(entry.get("regulation_title_ar") or ""))
        heading_tokens = set(_tokens(entry.get("article_heading") or ""))
        text_tokens = set(_tokens(entry.get("normalized_search_text") or entry.get("text") or ""))
        title_overlap = len(question_tokens & title_tokens)
        heading_overlap = len(question_tokens & heading_tokens)
        text_overlap = len(question_tokens & text_tokens)
        return (
            min(title_overlap * 8.0, 32.0)
            + min(heading_overlap * 5.0, 20.0)
            + min(text_overlap * 0.9, 24.0)
        )

    def _rank_selected_context(self, selected: list[dict[str, Any]], query_data: dict[str, Any]) -> list[dict[str, Any]]:
        if not selected:
            return selected
        question_tokens = set(_tokens(query_data.get("question", "")))
        required_pairs = self._pair_key_set_from_article_map(query_data.get("required_articles_by_slug"))
        learned_pairs = self._pair_key_set_from_article_map(query_data.get("learned_articles_by_slug"))
        heldout_pairs = self._pair_key_set_from_article_map(query_data.get("heldout_axis_articles_by_slug"))
        required_slugs = set(query_data.get("required_regulations") or [])
        learned_slugs = set(
            [
                *query_data.get("learned_package_regulations", []),
                *query_data.get("learned_companion_regulations", []),
            ]
        )
        heldout_slugs = set(query_data.get("heldout_axis_regulations") or [])

        def pair_key_for_candidate(candidate: dict[str, Any]) -> str:
            entry = candidate.get("entry") or {}
            try:
                article = int(entry.get("article_index") or 0)
            except Exception:
                article = 0
            slug = str(entry.get("regulation_slug") or "")
            return f"{slug}:{article}" if slug and article > 0 else ""

        def priority(candidate: dict[str, Any], index: int) -> tuple[float, float, int]:
            entry = candidate.get("entry") or {}
            slug = str(entry.get("regulation_slug") or "")
            key = pair_key_for_candidate(candidate)
            score = float(candidate.get("hybrid_score") or 0.0)
            if key in heldout_pairs:
                score += 120.0
            if key in learned_pairs:
                score += 95.0
            if key in required_pairs:
                score += 80.0
            if candidate.get("heldout_axis_packer"):
                score += 55.0
            if candidate.get("coverage_packer"):
                score += 38.0
            if candidate.get("forced"):
                score += 12.0
            if slug in heldout_slugs:
                score += 18.0
            if slug in learned_slugs:
                score += 12.0
            if slug in required_slugs:
                score += 10.0
            score += self._question_overlap_score(entry, question_tokens)
            score += self._materiality_score(entry, query_data) * 2.0
            return score, float(candidate.get("hybrid_score") or 0.0), -index

        return [
            candidate
            for index, candidate in sorted(
                enumerate(selected),
                key=lambda item: priority(item[1], item[0]),
                reverse=True,
            )
        ]

    def _topical_article_entries_for_slug(
        self,
        slug: str,
        query_data: dict[str, Any],
        *,
        limit: int,
        excluded_pairs: set[tuple[str, int]],
    ) -> list[dict[str, Any]]:
        """Pick the best distinct articles from a known-relevant regulation.

        This helps recall-first probes where the system has already identified
        the governing regulation, but the exact article numbers are not part of
        a static bundle.
        """
        if limit <= 0:
            return []
        query_tokens = set(_tokens(query_data.get("question", "")))
        roles = set(query_data.get("query_roles") or [])
        mentioned_articles = set(int(item) for item in (query_data.get("mentioned_article_numbers") or []))
        forced_by_article: dict[int, dict[str, Any]] = {}
        force_articles = [*sorted(mentioned_articles)]
        if "definition" in roles:
            force_articles.append(1)
        for article in force_articles:
            pair = (slug, int(article))
            if pair in excluded_pairs:
                continue
            entry = self._article_entry(slug, int(article))
            if entry is not None:
                forced_by_article.setdefault(int(article), entry)

        best_by_article: dict[int, tuple[float, dict[str, Any]]] = {}
        for entry in self._entries_by_slug.get(slug, []):
            article = int(entry.get("article_index") or 0)
            if not article:
                continue
            pair = (str(entry.get("regulation_slug") or ""), article)
            if pair in excluded_pairs:
                continue
            lexical = self._lexical_score(entry, query_tokens, query_data)
            materiality = self._materiality_score(entry, query_data)
            score = lexical + (materiality * 4.0)
            tags = {str(item).lower() for item in (entry.get("legal_function_tags") or [])}
            article_type = _normalize(str(entry.get("article_type_label_ar") or ""))
            if article in mentioned_articles:
                score += 80.0
            if "penalty" in roles and ("penalty" in tags or "عقوبه" in article_type or "مخالفه" in article_type):
                score += 18.0
            if "licensing" in roles and (
                "procedure" in tags
                or "condition" in tags
                or "authority" in tags
                or "اجراء" in article_type
                or "شروط" in article_type
            ):
                score += 8.0
            if "inspection" in roles and (
                "procedure" in tags
                or "authority" in tags
                or "اختصاص" in tags
                or "اجراء" in article_type
                or _has_any(text := str(entry.get("normalized_search_text") or ""), ("تفتيش", "مفتش", "ضبط", "المضبوطات", "اتلاف", "سجلات"))
            ):
                score += 10.0
            if "governance" in roles and (
                "authority" in tags
                or "اختصاص" in tags
                or _has_any(str(entry.get("normalized_search_text") or ""), ("صلاحيات", "اختصاص", "مجلس", "المجلس", "موازنه", "ميزانيه", "امين عام", "مدير تنفيذي", "اهداف"))
            ):
                score += 8.0
            if article == 1 and "definition" in roles:
                score += 14.0
            elif article == 1:
                score -= 3.0
            if score <= 0.0:
                continue
            current = best_by_article.get(article)
            if not current or score > current[0]:
                best_by_article[article] = (score, entry)
        ranked = sorted(best_by_article.values(), key=lambda item: item[0], reverse=True)
        selected: list[dict[str, Any]] = []
        selected_articles: set[int] = set()
        for article, entry in forced_by_article.items():
            if len(selected) >= limit:
                break
            selected.append(entry)
            selected_articles.add(article)
        for _score, entry in ranked:
            article = int(entry.get("article_index") or 0)
            if article in selected_articles:
                continue
            selected.append(entry)
            selected_articles.add(article)
            if len(selected) >= limit:
                break
        return selected

    def _coverage_article_entries_for_slug(
        self,
        slug: str,
        query_data: dict[str, Any],
        *,
        limit: int,
        excluded_pairs: set[tuple[str, int]],
    ) -> list[dict[str, Any]]:
        """Choose article-level coverage from inside an already relevant regulation."""
        if limit <= 0:
            return []
        entries = self._entries_by_slug.get(slug) or []
        if not entries:
            return []
        question = str(query_data.get("question") or "")
        roles = set(query_data.get("query_roles") or [])
        query_tokens = set(_tokens(question))
        generic_tokens = {
            "على",
            "الى",
            "عن",
            "في",
            "من",
            "ما",
            "هي",
            "هو",
            "هذه",
            "هذا",
            "ذلك",
            "تلك",
            "بعد",
            "قبل",
            "كما",
            "حيث",
            "بينما",
            "او",
            "اي",
            "غير",
            "مع",
            "خلال",
            "وزارة",
            "وزاره",
            "الوزاره",
            "حكومي",
            "حكوميه",
            "الحكوميه",
            "العامه",
            "الصحه",
            "اجهزه",
            "طبيه",
            "التي",
            "الذي",
            "الواجب",
            "الواجب تطبيقها",
            "النظاميه",
            "القانونيه",
            "المواد",
            "الاحكام",
            "تحديد",
        }
        signal_tokens = {token for token in query_tokens if len(token) >= 4 and token not in generic_tokens}
        segment_token_sets = [
            {token for token in _tokens(segment) if len(token) >= 4 and token not in generic_tokens}
            for segment in [question, *_router_query_segments(question)]
            if {token for token in _tokens(segment) if len(token) >= 4 and token not in generic_tokens}
        ]
        mentioned_articles = set(int(item) for item in (query_data.get("mentioned_article_numbers") or []))
        required_articles = set(query_data.get("required_articles_by_slug", {}).get(slug, []))
        heldout_axis_articles = set(
            int(item)
            for item in (query_data.get("heldout_axis_articles_by_slug") or {}).get(slug, [])
        )
        heldout_axis_terms: set[str] = set()
        for hint in query_data.get("heldout_axis_hints") or []:
            if str(hint.get("slug") or "") != slug:
                continue
            heldout_axis_terms.update(str(token) for token in hint.get("matched_terms") or [])
        normalized_question = str(query_data.get("normalized_question") or _normalize(question))

        def role_score(entry: dict[str, Any]) -> float:
            text = str(entry.get("normalized_search_text") or "")
            tags = {str(item).lower() for item in (entry.get("legal_function_tags") or [])}
            article_type = _normalize(str(entry.get("article_type_label_ar") or ""))
            score = 0.0

            def shared_phrase_score(phrases: tuple[str, ...], *, weight: float, cap: float) -> float:
                value = 0.0
                for phrase in phrases:
                    if _pattern_matches(phrase, normalized_question) and _pattern_matches(phrase, text):
                        value += weight
                return min(value, cap)

            def axis_group_score(groups: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...], *, weight: float, cap: float) -> float:
                value = 0.0
                for question_terms, article_terms in groups:
                    if _has_any(normalized_question, question_terms) and _has_any(text, article_terms):
                        value += weight
                return min(value, cap)

            if "violation" in roles and (
                "prohibition" in tags
                or "violation" in tags
                or _has_any(text, ("لا يجوز", "يحظر", "مخالفه", "تجاوزات", "كراهيه", "عنصريه", "تعصب", "تحريض"))
            ):
                score += 5.0
            if "remedy" in roles and ("remedy" in tags or _has_any(text, ("تعويض", "استرداد", "فسخ", "الغاء", "ضرر"))):
                score += 4.0
            if "deadline" in roles and ("deadline" in tags or _has_any(text, ("خلال", "مده", "ايام", "يوما", "مهله"))):
                score += 3.4
            if "evidence" in roles and (
                "burden_of_proof" in tags
                or _has_any(text, ("اثبات", "مستندات", "بينة", "بينه", "شهادة", "شهاده", "قرينه"))
            ):
                score += 3.8
            if "obligation" in roles and ("obligation" in tags or _has_any(text, ("يلتزم", "تلتزم", "يجب", "واجب"))):
                score += 3.6
            if "penalty" in roles and (
                "penalty" in tags
                or "عقوبه" in article_type
                or _has_any(text, ("يعاقب", "عقوبه", "غرامه", "سجن", "جزاء"))
            ):
                score += 7.0
            if "licensing" in roles and (
                "condition" in tags
                or "procedure" in tags
                or _has_any(text, ("ترخيص", "تصريح", "مستندات", "طلب"))
            ):
                score += 4.5
            if "inspection" in roles and (
                "authority" in tags
                or "procedure" in tags
                or _has_any(text, ("تفتيش", "مفتش", "ضبط", "المضبوطات", "اتلاف", "سجلات"))
            ):
                score += 5.0
            if "governance" in roles and (
                "authority" in tags
                or _has_any(text, ("صلاحيات", "اختصاص", "مجلس", "المجلس", "الوزاره", "الهييه", "مدير"))
            ):
                score += 4.2
            if "procurement" in roles and _has_any(
                text,
                (
                    "مزايده",
                    "المنافسه",
                    "المشتريات",
                    "الاتفاق المباشر",
                    "النماذج المعتمده",
                    "وثائق المنافسه",
                    "وثائق التاهيل",
                    "الجهات الحكوميه",
                    "اشعار الوزاره",
                ),
            ):
                score += 7.0
                score += axis_group_score(
                    (
                        (
                            ("متقدم*", "يتقدم", "مزايد*", "اعلان"),
                            ("يتقدم", "مزايد*", "اعلان", "مختص*", "جمعي*", "اهلي*", "تشعر"),
                        ),
                        (
                            ("مباشر*", "تنفيذ", "اعمال", "مستشفي", "تعاقد"),
                            ("اتفاق مباشر", "الاتفاق المباشر", "تنفيذ الاعمال", "تامين المشتريات", "تنوب"),
                        ),
                        (
                            ("نموذج*", "عقد", "معتمد*", "مركز", "وثائق"),
                            ("نموذج*", "نماذج", "معتمد*", "وثائق", "عقود", "العقود", "تاهيل"),
                        ),
                    ),
                    weight=15.0,
                    cap=45.0,
                )
                score += shared_phrase_score(
                    (
                        "يتقدم",
                        "متقدم*",
                        "مزايد*",
                        "مره",
                        "مختص*",
                        "جمعي*",
                        "اهلي*",
                        "اشعار",
                        "تشعر",
                        "وزارة",
                        "وزاره",
                        "هيئة",
                        "هييه",
                        "مباشر*",
                        "تنفيذ",
                        "اعمال",
                        "تأمين",
                        "تامين",
                        "مشتريات",
                        "حكومي*",
                        "نموذج*",
                        "نماذج",
                        "معتمد*",
                        "وثائق",
                        "تاهيل",
                        "عقد",
                        "عقود",
                    ),
                    weight=6.0,
                    cap=42.0,
                )
            if "maritime_voyage" in roles and _has_any(
                text,
                (
                    "السفينه",
                    "ربان",
                    "الراكب",
                    "نولون",
                    "استقراض",
                    "المستقرضه",
                    "اثناء السفر",
                    "ميناء",
                ),
            ):
                score += 7.5
                score += axis_group_score(
                    (
                        (
                            ("راكب", "انتظ*", "اقام*", "اصلاح*", "تعمير"),
                            ("راكب", "ينتظر", "انتظار", "اقام*", "تعمير", "نولون"),
                        ),
                        (
                            ("استقراض", "مبلغ", "دائن", "دين*", "مستقرض*"),
                            ("استقراض", "مستقرض*", "قارض*", "الدراهم المستقرضه"),
                        ),
                        (
                            ("اولويه", "سداد", "ديون", "توقف"),
                            ("تترجح", "مرجح*", "توقف", "درجه متساويه", "الاستقراض الاخير"),
                        ),
                    ),
                    weight=14.0,
                    cap=42.0,
                )
                score += shared_phrase_score(
                    (
                        "سفين*",
                        "ربان",
                        "راكب",
                        "انتظ*",
                        "اقام*",
                        "تعمير",
                        "استقراض",
                        "مستقرض*",
                        "سفر",
                        "بحري*",
                        "ميناء",
                        "موانئ",
                        "توقف",
                        "دين*",
                    ),
                    weight=5.5,
                    cap=38.0,
                )
            if "drug_penalty_concurrence" in roles and slug == "nzam-mkafhh-almkhdrat-walmwthrat-alaqlyh":
                score += 8.0
                score += axis_group_score(
                    (
                        (
                            ("مشارك*", "مساعد*", "توزيع", "شحنه"),
                            ("شريك", "مشارك*", "مساعد*", "معاون", "محرض", "فاعل اصلي"),
                        ),
                        (
                            ("تعدد", "تداخل", "عقوب*", "جريمه", "جرائم", "الحكم"),
                            ("اشد", "تداخل", "العقوبات", "عقوبه واحده", "نهائي", "جرائم متعدده"),
                        ),
                    ),
                    weight=18.0,
                    cap=48.0,
                )
                score += shared_phrase_score(
                    (
                        "مشارك*",
                        "مساعد*",
                        "توزيع",
                        "حيازه",
                        "تهريب",
                        "تعدد",
                        "تداخل",
                        "العقوبات",
                        "اشد",
                        "جريمه",
                        "جرائم",
                    ),
                    weight=6.0,
                    cap=42.0,
                )
            return score

        best_by_article: dict[int, tuple[float, dict[str, Any], float]] = {}
        segment_winners: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            article = int(entry.get("article_index") or 0)
            if not article:
                continue
            pair = (str(entry.get("regulation_slug") or ""), article)
            if pair in excluded_pairs:
                continue
            entry_tokens = entry.get("token_set") or set()
            if not entry_tokens:
                continue
            text = str(entry.get("normalized_search_text") or "")
            segment_scores: list[float] = []
            for tokens in segment_token_sets:
                overlap = tokens & entry_tokens
                if not overlap:
                    segment_scores.append(0.0)
                    continue
                segment_scores.append(len(overlap) / math.sqrt(max(len(entry_tokens), 1)))
            best_segment = max(segment_scores, default=0.0)
            full_overlap = len(query_tokens & entry_tokens) / math.sqrt(max(len(entry_tokens), 1)) if query_tokens else 0.0
            signal_overlap = len(signal_tokens & entry_tokens) / max(1, min(len(signal_tokens), 14))
            phrase_bonus = min(sum(0.06 for token in signal_tokens if len(token) >= 5 and token in text), 1.8)
            score = (best_segment * 10.0) + (full_overlap * 4.0) + (signal_overlap * 7.0)
            score += phrase_bonus
            score += self._materiality_score(entry, query_data) * 4.5
            score += role_score(entry)
            if article in required_articles:
                score += 45.0
            if article in heldout_axis_articles:
                score += 60.0
                if heldout_axis_terms:
                    score += min(len(heldout_axis_terms & entry_tokens) * 4.0, 24.0)
            if article in mentioned_articles:
                score += 80.0
            if article == 1 and "definition" not in roles:
                score -= 7.0
            if score <= 0.15:
                continue
            current = best_by_article.get(article)
            if not current or score > current[0]:
                best_by_article[article] = (score, entry, best_segment)

        ranked = sorted(best_by_article.values(), key=lambda item: item[0], reverse=True)
        axis_winners: list[tuple[float, dict[str, Any]]] = []

        def collect_axis_winners(groups: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]) -> None:
            for question_terms, article_terms in groups:
                if not _has_any(normalized_question, question_terms):
                    continue
                winner: tuple[float, dict[str, Any]] | None = None
                for score, entry, _best_segment in ranked:
                    text = str(entry.get("normalized_search_text") or "")
                    if not _has_any(text, article_terms):
                        continue
                    article_hits = sum(1 for term in article_terms if _pattern_matches(term, text))
                    question_hits = sum(1 for term in question_terms if _pattern_matches(term, normalized_question))
                    axis_score = (article_hits * 100.0) + (question_hits * 8.0) + (score * 0.05)
                    if winner is None or axis_score > winner[0]:
                        winner = (axis_score, entry)
                if winner:
                    axis_winners.append(winner)

        if "procurement" in roles:
            collect_axis_winners(
                (
                    (
                        ("متقدم*", "يتقدم", "مزايد*", "اعلان"),
                        ("لم يتقدم", "مزايد*", "مختص*", "جمعي*", "اهلي*", "تشعر الوزاره"),
                    ),
                    (
                        ("مباشر*", "تنفيذ", "اعمال", "مستشفي", "تعاقد"),
                        ("اتفاق مباشر", "الاتفاق المباشر", "تتولي بنفسها", "تامين المشتريات", "تنوب"),
                    ),
                    (
                        ("نموذج*", "عقد", "معتمد*", "مركز", "وثائق"),
                        ("النماذج المعتمده", "وثائق المنافسه", "وثائق التاهيل", "نماذج تقييم"),
                    ),
                )
            )
        if "maritime_voyage" in roles:
            collect_axis_winners(
                (
                    (
                        ("راكب", "انتظ*", "اقام*", "اصلاح*", "تعمير"),
                        ("الراكب", "ينتظر", "اقامته", "التعمير", "النولون"),
                    ),
                    (
                        ("استقراض", "مبلغ", "دائن", "دين*", "مستقرض*"),
                        ("عقود مقاولات الاستقراض", "الدراهم المستقرضه", "القارضين", "المستقرضين"),
                    ),
                    (
                        ("اولويه", "سداد", "ديون", "توقف"),
                        ("تترجح", "الاستقراض الاخير", "توقف فيها", "درجه متساويه"),
                    ),
                )
            )
        if "drug_penalty_concurrence" in roles and slug == "nzam-mkafhh-almkhdrat-walmwthrat-alaqlyh":
            collect_axis_winners(
                (
                    (
                        ("مشارك*", "مساعد*", "توزيع", "شحنه"),
                        ("شريك", "مشارك*", "مساعد*", "معاون", "محرض", "فاعل اصلي"),
                    ),
                    (
                        ("تعدد", "تداخل", "عقوب*", "جريمه", "جرائم", "الحكم"),
                        ("اشد", "تداخل", "العقوبات", "عقوبه واحده", "نهائي", "جرائم متعدده"),
                    ),
                )
            )

        for tokens in segment_token_sets[1:]:
            winner: tuple[float, dict[str, Any]] | None = None
            for score, entry, _best_segment in ranked:
                entry_tokens = entry.get("token_set") or set()
                if not (tokens & entry_tokens):
                    continue
                segment_score = len(tokens & entry_tokens) / math.sqrt(max(len(entry_tokens), 1))
                combined = score + (segment_score * 6.0)
                if winner is None or combined > winner[0]:
                    winner = (combined, entry)
            if winner:
                segment_winners.append(winner)

        selected: list[dict[str, Any]] = []
        selected_articles: set[int] = set()
        for _score, entry in sorted(axis_winners, key=lambda item: item[0], reverse=True):
            article = int(entry.get("article_index") or 0)
            if article in selected_articles:
                continue
            selected.append(entry)
            selected_articles.add(article)
            if len(selected) >= limit:
                return selected
        segment_winner_limit = max(1, len(selected) + ((limit - len(selected)) // 2))
        for _score, entry in sorted(segment_winners, key=lambda item: item[0], reverse=True):
            article = int(entry.get("article_index") or 0)
            if article in selected_articles:
                continue
            selected.append(entry)
            selected_articles.add(article)
            if len(selected) >= segment_winner_limit:
                break
        for _score, entry, _best_segment in ranked:
            article = int(entry.get("article_index") or 0)
            if article in selected_articles:
                continue
            selected.append(entry)
            selected_articles.add(article)
            if len(selected) >= limit:
                break
        return selected

    async def _dense_candidates(self, question: str, query_data: dict[str, Any]) -> list[dict[str, Any]]:
        profile_config = query_data["retrieval_profile_config"]
        n_results = int(profile_config.get("dense_k") or 90)
        try:
            embedding = await asyncio.to_thread(self._embeddings.embed_query, question)
            result = await asyncio.to_thread(
                self._vectorstore._collection.query,
                query_embeddings=[embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.warning("Dense retrieval failed: %s", exc)
            return []

        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        candidates = []
        for rank, item_id in enumerate(ids, start=1):
            distance = float(distances[rank - 1]) if rank - 1 < len(distances) else 1.0
            score = max(0.0, 1.0 - distance)
            metadata = metadatas[rank - 1] if rank - 1 < len(metadatas) and isinstance(metadatas[rank - 1], dict) else {}
            document = documents[rank - 1] if rank - 1 < len(documents) else ""
            entry = self._entry_from_chroma(str(item_id), document, metadata)
            candidates.append(self._make_candidate(entry, dense_score=score, dense_rank=rank))
        return candidates

    def _lexical_candidates(self, query_data: dict[str, Any]) -> list[dict[str, Any]]:
        query_tokens = set(_tokens(query_data.get("question", "")))
        scored = []
        for entry in self._entries:
            score = self._lexical_score(entry, query_tokens, query_data)
            if score > 0.0:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        limit = int(query_data["retrieval_profile_config"].get("lexical_k") or 90)
        candidates = [
            self._make_candidate(entry, lexical_score=score, lexical_rank=rank)
            for rank, (score, entry) in enumerate(scored[:limit], start=1)
        ]
        present_required_slugs = {
            str(candidate.get("entry", {}).get("regulation_slug") or "")
            for candidate in candidates
        }
        required_slugs = _dedupe(
            [
                *query_data.get("required_core_regulations", []),
                *query_data.get("required_companion_regulations", []),
                *query_data.get("learned_package_regulations", []),
                *query_data.get("learned_companion_regulations", []),
            ]
        )
        next_rank = len(candidates) + 1
        for slug in required_slugs:
            if slug in present_required_slugs:
                continue
            entry = self._representative_entry_for_slug(str(slug), query_data)
            if entry is None:
                continue
            anchor_score = 10.0
            anchor_score += min(self._lexical_score(entry, query_tokens, query_data), 8.0)
            anchor_score += self._materiality_score(entry, query_data)
            candidate = self._make_candidate(entry, lexical_score=anchor_score, lexical_rank=next_rank)
            candidate["package_anchor"] = True
            candidates.append(candidate)
            present_required_slugs.add(str(slug))
            next_rank += 1
        return candidates

    def _forced_required_candidates(self, query_data: dict[str, Any]) -> list[dict[str, Any]]:
        forced: list[dict[str, Any]] = []
        forced_slugs: set[str] = set()
        query_tokens = set(_tokens(query_data.get("question", "")))
        for slug, articles in query_data.get("required_articles_by_slug", {}).items():
            for article in articles:
                entry = self._article_entry(slug, int(article))
                if entry is None:
                    continue
                lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 8.0)
                forced_slugs.add(slug)
                forced.append(
                    self._make_candidate(
                        entry,
                        lexical_score=20.0 + lexical_boost,
                        lexical_rank=0,
                        forced=True,
                    )
                )
        for slug in _dedupe(
            [
                *query_data.get("required_core_regulations", []),
                *query_data.get("required_companion_regulations", []),
            ]
        ):
            if slug in forced_slugs:
                continue
            entry = self._representative_entry_for_slug(slug, query_data)
            if entry is None:
                continue
            lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 6.0)
            forced_slugs.add(slug)
            forced.append(
                self._make_candidate(
                    entry,
                    lexical_score=18.0 + lexical_boost,
                    lexical_rank=0,
                    forced=True,
                )
            )
        return forced

    def _merge_and_rank(self, candidates: list[dict[str, Any]], query_data: dict[str, Any]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            key = self._candidate_key(candidate["entry"])
            if key not in merged:
                merged[key] = candidate
                continue
            current = merged[key]
            current["dense_score"] = max(float(current.get("dense_score") or 0.0), float(candidate.get("dense_score") or 0.0))
            current["lexical_score"] = max(float(current.get("lexical_score") or 0.0), float(candidate.get("lexical_score") or 0.0))
            current["dense_rank"] = current.get("dense_rank") if current.get("dense_rank") is not None else candidate.get("dense_rank")
            current["lexical_rank"] = current.get("lexical_rank") if current.get("lexical_rank") is not None else candidate.get("lexical_rank")
            current["dense_hits"] = int(current.get("dense_hits") or 0) + int(candidate.get("dense_hits") or 0)
            current["lexical_hits"] = int(current.get("lexical_hits") or 0) + int(candidate.get("lexical_hits") or 0)
            current["forced"] = bool(current.get("forced") or candidate.get("forced"))

        values = list(merged.values())
        max_dense = max((float(item.get("dense_score") or 0.0) for item in values), default=1.0) or 1.0
        max_lexical = max((float(item.get("lexical_score") or 0.0) for item in values), default=1.0) or 1.0
        profile_config = query_data["retrieval_profile_config"]
        dense_weight = float(profile_config.get("dense_norm_weight") or 0.0)
        lexical_weight = float(profile_config.get("lexical_norm_weight") or 0.0)
        for item in values:
            dense_norm = float(item.get("dense_score") or 0.0) / max_dense
            lexical_norm = float(item.get("lexical_score") or 0.0) / max_lexical
            item["hybrid_score"] = (dense_weight * dense_norm) + (lexical_weight * lexical_norm)
            item["hybrid_score"] += self._materiality_score(item.get("entry") or {}, query_data)
            if item.get("forced"):
                item["hybrid_score"] += 2.0
        values.sort(key=lambda item: item["hybrid_score"], reverse=True)
        return values

    def _select_context(self, ranked: list[dict[str, Any]], query_data: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(query_data["retrieval_profile_config"].get("context_limit") or 24)
        selected: list[dict[str, Any]] = []
        seen_pair: set[tuple[str, int]] = set()

        required_slugs = _dedupe(
            [
                *query_data.get("required_core_regulations", []),
                *query_data.get("required_companion_regulations", []),
            ]
        )
        required_slug_set = set(required_slugs)
        required_article_slugs = {
            slug
            for slug, articles in (query_data.get("required_articles_by_slug") or {}).items()
            if articles
        }
        learned_article_slugs = {
            slug
            for slug, articles in (query_data.get("learned_articles_by_slug") or {}).items()
            if articles
        }
        heldout_axis_articles_by_slug = query_data.get("heldout_axis_articles_by_slug") or {}
        heldout_axis_article_slugs = {
            slug
            for slug, articles in heldout_axis_articles_by_slug.items()
            if articles
        }
        trusted_learned_slug_set = set(
            _dedupe(
                [
                    *query_data.get("learned_package_regulations", []),
                    *query_data.get("learned_companion_regulations", []),
                    *learned_article_slugs,
                    *query_data.get("heldout_axis_regulations", []),
                    *heldout_axis_article_slugs,
                ]
            )
        )
        contamination_guard_exempt_slugs = (
            required_slug_set | required_article_slugs | trusted_learned_slug_set | heldout_axis_article_slugs
        )
        question_text = str(query_data.get("question") or "")
        labor_context = _has_any(
            question_text,
            (
                "عامل",
                "العامل",
                "عمال",
                "العمال",
                "موظف",
                "موظفة",
                "موظفون",
                "الموظفين",
                "صاحب العمل",
                "عقد عمل",
                "الأجر",
                "الاجر",
                "الأجور",
                "الاجور",
                "راتب",
                "رواتب",
                "نهاية الخدمة",
                "فترة التجربة",
                "إصابة عمل",
                "اصابة عمل",
                "السلامة المهنية",
            ),
        )
        procurement_context = _has_any(
            question_text,
            (
                "جهة حكومية",
                "منافسة حكومية",
                "المنافسات والمشتريات",
                "المشتريات الحكومية",
                "كراسة الشروط",
                "منصة اعتماد",
                "ترسية",
                "تعاقد حكومي",
                "عقد حكومي",
                "متعاقد حكومي",
                "مقاول من الباطن",
                "مورد في منافسة",
            ),
        )

        def optional_contamination(entry: dict[str, Any]) -> bool:
            slug = str(entry.get("regulation_slug") or "")
            if slug in contamination_guard_exempt_slugs:
                return False
            if slug == "labor-law" and not labor_context:
                return True
            if slug in {
                "government-tenders-and-procurement-law",
                "government-procurement-implementing-regulation",
                "procurement-conduct-ethics-regulation",
                "procurement-conflict-of-interest-regulation",
            } and not (procurement_context or "procurement" in coverage_roles):
                return True
            return False

        required_article_pairs = {
            (str(slug), int(article))
            for slug, articles in (query_data.get("required_articles_by_slug") or {}).items()
            for article in articles
        }
        learned_article_pairs = {
            (str(slug), int(article))
            for slug, articles in (query_data.get("learned_articles_by_slug") or {}).items()
            for article in articles
        }
        heldout_axis_article_pairs = {
            (str(slug), int(article))
            for slug, articles in heldout_axis_articles_by_slug.items()
            for article in articles
        }
        topical_seed_count = int(query_data["retrieval_profile_config"].get("slug_article_seed_count") or 0)
        learned_topical_limit = int(query_data["retrieval_profile_config"].get("learned_slug_article_seed_limit") or 0)
        coverage_budget = int(query_data["retrieval_profile_config"].get("coverage_packer_seed_limit") or 0)
        coverage_per_slug = int(query_data["retrieval_profile_config"].get("coverage_packer_per_slug") or 0)
        heldout_axis_seed_limit = int(query_data["retrieval_profile_config"].get("heldout_axis_packer_seed_limit") or 0)
        heldout_axis_per_slug = int(query_data["retrieval_profile_config"].get("heldout_axis_packer_per_slug") or 0)
        required_article_seed_limit = int(query_data["retrieval_profile_config"].get("required_article_seed_limit") or 0)
        required_article_seed_per_slug = int(query_data["retrieval_profile_config"].get("required_article_seed_per_slug") or 0)
        coverage_roles = set(query_data.get("query_roles") or [])
        priority_coverage_slugs: list[str] = []
        priority_coverage_slugs.extend(query_data.get("heldout_axis_regulations", []))
        if "procurement" in coverage_roles:
            priority_coverage_slugs.extend(
                [
                    "government-tenders-and-procurement-law",
                    "government-procurement-implementing-regulation",
                ]
            )
        if "maritime_voyage" in coverage_roles:
            priority_coverage_slugs.extend(
                [
                    "alnzam-altjary-nzam-almhkmh-altjaryh",
                    "alnzam-albhry-altjary",
                ]
            )
        if "drug_penalty_concurrence" in coverage_roles:
            priority_coverage_slugs.append("nzam-mkafhh-almkhdrat-walmwthrat-alaqlyh")

        selected_slug_set: set[str] = set()
        for slug in [item for item in required_slugs if item not in required_article_slugs]:
            if slug in selected_slug_set:
                continue
            for candidate in ranked:
                entry = candidate.get("entry") or {}
                if entry.get("regulation_slug") != slug:
                    continue
                pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
                if pair in seen_pair:
                    continue
                selected.append(candidate)
                seen_pair.add(pair)
                selected_slug_set.add(str(slug))
                break
            if len(selected) >= limit:
                return selected

        if heldout_axis_article_pairs and heldout_axis_seed_limit > 0 and heldout_axis_per_slug > 0 and len(selected) < limit:
            query_tokens = set(_tokens(query_data.get("question", "")))
            heldout_added = 0

            def make_heldout_axis_candidate(entry: dict[str, Any]) -> dict[str, Any]:
                lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 8.0)
                candidate = self._make_candidate(
                    entry,
                    lexical_score=20.0 + lexical_boost,
                    lexical_rank=0,
                    forced=True,
                )
                candidate["coverage_packer"] = True
                candidate["heldout_axis_packer"] = True
                return candidate

            ordered_hint_pairs: list[tuple[str, int]] = []
            for hint in query_data.get("heldout_axis_hints") or []:
                slug = str(hint.get("slug") or "")
                for article in hint.get("articles") or []:
                    ordered_hint_pairs.append((slug, int(article)))
            ordered_hint_pairs = _dedupe(ordered_hint_pairs)
            heldout_added_by_slug: Counter[str] = Counter()
            for slug, article in ordered_hint_pairs:
                if heldout_added >= heldout_axis_seed_limit:
                    break
                if heldout_added_by_slug[str(slug)] >= heldout_axis_per_slug:
                    continue
                if (str(slug), int(article)) not in heldout_axis_article_pairs:
                    continue
                pair = (str(slug), int(article))
                if pair in seen_pair:
                    continue
                entry = self._article_entry(str(slug), int(article))
                if entry is None or optional_contamination(entry):
                    continue
                selected.append(make_heldout_axis_candidate(entry))
                seen_pair.add(pair)
                heldout_added += 1
                heldout_added_by_slug[str(slug)] += 1
                if len(selected) >= limit:
                    return selected
            if heldout_added < heldout_axis_seed_limit:
                for slug in _dedupe([*query_data.get("heldout_axis_regulations", []), *[slug for slug, _ in sorted(heldout_axis_article_pairs)]]):
                    for article in sorted(heldout_axis_articles_by_slug.get(slug) or []):
                        if heldout_added >= heldout_axis_seed_limit:
                            break
                        if heldout_added_by_slug[str(slug)] >= heldout_axis_per_slug:
                            break
                        pair = (str(slug), int(article))
                        if pair in seen_pair:
                            continue
                        entry = self._article_entry(str(slug), int(article))
                        if entry is None or optional_contamination(entry):
                            continue
                        selected.append(make_heldout_axis_candidate(entry))
                        seen_pair.add(pair)
                        heldout_added += 1
                        heldout_added_by_slug[str(slug)] += 1
                        if len(selected) >= limit:
                            return selected
                    if heldout_added >= heldout_axis_seed_limit:
                        break

        if (
            required_article_pairs
            and required_article_seed_limit > 0
            and required_article_seed_per_slug > 0
            and len(selected) < limit
        ):
            query_tokens = set(_tokens(query_data.get("question", "")))
            mentioned_articles = set(int(item) for item in (query_data.get("mentioned_article_numbers") or []))
            required_articles_by_slug = query_data.get("required_articles_by_slug") or {}
            phrase_articles_by_slug = query_data.get("phrase_articles_by_slug") or {}
            required_seed_added = 0
            required_added_by_slug: Counter[str] = Counter()

            def make_required_article_candidate(entry: dict[str, Any]) -> dict[str, Any]:
                lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 9.0)
                candidate = self._make_candidate(
                    entry,
                    lexical_score=22.0 + lexical_boost,
                    lexical_rank=0,
                    forced=True,
                )
                candidate["coverage_packer"] = True
                candidate["required_article_packer"] = True
                return candidate

            def required_article_priority(entry: dict[str, Any]) -> float:
                score = self._question_overlap_score(entry, query_tokens)
                score += self._materiality_score(entry, query_data) * 4.5
                score += min(self._lexical_score(entry, query_tokens, query_data), 12.0)
                try:
                    article = int(entry.get("article_index") or 0)
                except Exception:
                    article = 0
                entry_slug = str(entry.get("regulation_slug") or "")
                phrase_articles_for_slug = {
                    int(item)
                    for item in (phrase_articles_by_slug.get(entry_slug) or [])
                    if int(item) > 0
                }
                if article in phrase_articles_for_slug:
                    score += 120.0
                if article in mentioned_articles:
                    score += 80.0
                text = str(entry.get("normalized_search_text") or "")
                roles = set(query_data.get("query_roles") or [])
                if "licensing" in roles and _has_any(text, ("ترخيص", "تصريح", "مستندات", "طلب", "الغاء")):
                    score += 12.0
                if "deadline" in roles and _has_any(text, ("مده", "ايام", "اسبوع", "مهله", "خلال")):
                    score += 10.0
                if "penalty" in roles and _has_any(text, ("يعاقب", "عقوبه", "غرامه", "مخالفه", "جزاء")):
                    score += 12.0
                if "inspection" in roles and _has_any(text, ("تفتيش", "ضبط", "مفتش", "سجلات", "اتلاف")):
                    score += 10.0
                if "obligation" in roles and _has_any(text, ("يلتزم", "تلتزم", "يجب", "واجب")):
                    score += 8.0
                return score

            article_slug_priority = _dedupe(
                [
                    *list(phrase_articles_by_slug.keys()),
                    *list((query_data.get("learned_articles_by_slug") or {}).keys()),
                    *list((query_data.get("heldout_axis_articles_by_slug") or {}).keys()),
                    *query_data.get("learned_package_regulations", []),
                    *query_data.get("required_core_regulations", []),
                    *query_data.get("required_companion_regulations", []),
                    *query_data.get("learned_companion_regulations", []),
                    *list(required_articles_by_slug.keys()),
                ]
            )
            for slug in article_slug_priority:
                if required_seed_added >= required_article_seed_limit:
                    break
                scored_entries: list[tuple[float, int, dict[str, Any]]] = []
                required_articles_for_slug: set[int] = set()
                for item in required_articles_by_slug.get(slug, []) or []:
                    try:
                        article = int(item)
                    except Exception:
                        continue
                    if article > 0:
                        required_articles_for_slug.add(article)
                for article in sorted(required_articles_for_slug):
                    pair = (str(slug), int(article))
                    if pair not in required_article_pairs or pair in seen_pair:
                        continue
                    entry = self._article_entry(str(slug), int(article))
                    if entry is None or optional_contamination(entry):
                        continue
                    scored_entries.append((required_article_priority(entry), int(article), entry))
                for _score, article, entry in sorted(scored_entries, key=lambda item: (-item[0], item[1])):
                    if required_seed_added >= required_article_seed_limit:
                        break
                    if required_added_by_slug[str(slug)] >= required_article_seed_per_slug:
                        break
                    pair = (str(slug), int(article))
                    if pair in seen_pair:
                        continue
                    selected.append(make_required_article_candidate(entry))
                    seen_pair.add(pair)
                    required_seed_added += 1
                    required_added_by_slug[str(slug)] += 1
                    if len(selected) >= limit:
                        return selected

        if coverage_budget > 0 and coverage_per_slug > 0 and priority_coverage_slugs and len(selected) < limit:
            query_tokens = set(_tokens(query_data.get("question", "")))
            early_coverage_added = 0

            def make_priority_coverage_candidate(entry: dict[str, Any]) -> dict[str, Any]:
                lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 7.0)
                candidate = self._make_candidate(
                    entry,
                    lexical_score=18.0 + lexical_boost,
                    lexical_rank=0,
                    forced=True,
                )
                candidate["coverage_packer"] = True
                return candidate

            for slug in _dedupe(priority_coverage_slugs):
                for entry in self._coverage_article_entries_for_slug(
                    str(slug),
                    query_data,
                    limit=coverage_per_slug,
                    excluded_pairs=seen_pair,
                ):
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair in seen_pair:
                        continue
                    selected.append(make_priority_coverage_candidate(entry))
                    seen_pair.add(pair)
                    early_coverage_added += 1
                    if len(selected) >= limit or early_coverage_added >= coverage_budget:
                        break
                if len(selected) >= limit or early_coverage_added >= coverage_budget:
                    break

        if learned_article_pairs:
            learned_article_slug_priority = _dedupe(
                [
                    *query_data.get("learned_package_regulations", []),
                    *[slug for slug, _ in sorted(learned_article_pairs)],
                ]
            )
            learned_candidates_by_slug: dict[str, list[dict[str, Any]]] = {slug: [] for slug in learned_article_slug_priority}
            for article_slug in learned_article_slug_priority:
                for candidate in ranked:
                    entry = candidate.get("entry") or {}
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair[0] != article_slug or pair not in learned_article_pairs or pair in seen_pair:
                        continue
                    learned_candidates_by_slug.setdefault(article_slug, []).append(candidate)
            learned_selection_limit = limit
            if coverage_budget > 0 and coverage_per_slug > 0 and priority_coverage_slugs:
                coverage_reserve = min(
                    coverage_budget,
                    coverage_per_slug * len(_dedupe(priority_coverage_slugs)),
                )
                learned_selection_limit = max(len(selected), limit - coverage_reserve)
            progress = True
            while progress and len(selected) < learned_selection_limit:
                progress = False
                for article_slug in learned_article_slug_priority:
                    bucket = learned_candidates_by_slug.get(article_slug) or []
                    while bucket:
                        candidate = bucket.pop(0)
                        entry = candidate.get("entry") or {}
                        pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                        if pair in seen_pair:
                            continue
                        selected.append(candidate)
                        seen_pair.add(pair)
                        progress = True
                        break
                    if len(selected) >= learned_selection_limit:
                        break

        if required_article_pairs:
            article_slug_priority = _dedupe(
                [
                    *query_data.get("learned_package_regulations", []),
                    *query_data.get("required_core_regulations", []),
                    *query_data.get("required_companion_regulations", []),
                    *query_data.get("learned_companion_regulations", []),
                    *[slug for slug, _ in sorted(required_article_pairs)],
                ]
            )
            article_candidates_by_slug: dict[str, list[dict[str, Any]]] = {slug: [] for slug in article_slug_priority}
            for article_slug in article_slug_priority:
                for candidate in ranked:
                    entry = candidate.get("entry") or {}
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair[0] != article_slug or pair not in required_article_pairs or pair in seen_pair:
                        continue
                    article_candidates_by_slug.setdefault(article_slug, []).append(candidate)
            progress = True
            while progress and len(selected) < limit:
                progress = False
                for article_slug in article_slug_priority:
                    bucket = article_candidates_by_slug.get(article_slug) or []
                    while bucket:
                        candidate = bucket.pop(0)
                        entry = candidate.get("entry") or {}
                        pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                        if pair in seen_pair:
                            continue
                        selected.append(candidate)
                        seen_pair.add(pair)
                        progress = True
                        break
                    if len(selected) >= limit:
                        return selected

        if coverage_budget > 0 and coverage_per_slug > 0 and len(selected) < limit:
            query_tokens = set(_tokens(query_data.get("question", "")))
            coverage_slugs = _dedupe(
                [
                    *query_data.get("required_core_regulations", []),
                    *query_data.get("learned_package_regulations", [])[:learned_topical_limit],
                    *query_data.get("required_companion_regulations", []),
                    *query_data.get("learned_companion_regulations", [])[: max(0, learned_topical_limit // 2)],
                    *[slug for slug, _ in sorted(required_article_pairs | learned_article_pairs | heldout_axis_article_pairs)],
                ]
            )[: max(learned_topical_limit * 2, 12)]
            coverage_added = 0

            def make_coverage_candidate(entry: dict[str, Any]) -> dict[str, Any]:
                lexical_boost = min(self._lexical_score(entry, query_tokens, query_data), 7.0)
                candidate = self._make_candidate(
                    entry,
                    lexical_score=17.0 + lexical_boost,
                    lexical_rank=0,
                    forced=True,
                )
                candidate["coverage_packer"] = True
                return candidate

            coverage_slugs = _dedupe([*priority_coverage_slugs, *coverage_slugs])[
                : max(learned_topical_limit * 2, 12)
            ]
            for slug in _dedupe(priority_coverage_slugs):
                for entry in self._coverage_article_entries_for_slug(
                    str(slug),
                    query_data,
                    limit=coverage_per_slug,
                    excluded_pairs=seen_pair,
                ):
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair in seen_pair:
                        continue
                    selected.append(make_coverage_candidate(entry))
                    seen_pair.add(pair)
                    coverage_added += 1
                    if len(selected) >= limit or coverage_added >= coverage_budget:
                        break
                if len(selected) >= limit:
                    return selected
                if coverage_added >= coverage_budget:
                    break

            coverage_candidates_by_slug: dict[str, list[dict[str, Any]]] = {}
            for slug in coverage_slugs:
                bucket: list[dict[str, Any]] = []
                for entry in self._coverage_article_entries_for_slug(
                    str(slug),
                    query_data,
                    limit=coverage_per_slug,
                    excluded_pairs=seen_pair,
                ):
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair in seen_pair:
                        continue
                    bucket.append(make_coverage_candidate(entry))
                if bucket:
                    coverage_candidates_by_slug[str(slug)] = bucket

            progress = True
            while progress and len(selected) < limit and coverage_added < coverage_budget:
                progress = False
                for slug in coverage_slugs:
                    bucket = coverage_candidates_by_slug.get(str(slug)) or []
                    while bucket:
                        candidate = bucket.pop(0)
                        entry = candidate.get("entry") or {}
                        pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                        if pair in seen_pair:
                            continue
                        selected.append(candidate)
                        seen_pair.add(pair)
                        coverage_added += 1
                        progress = True
                        break
                    if len(selected) >= limit:
                        return selected
                    if coverage_added >= coverage_budget:
                        break

        topical_slugs = _dedupe(
            [
                *query_data.get("learned_package_regulations", [])[:learned_topical_limit],
                *query_data.get("learned_companion_regulations", [])[: max(0, learned_topical_limit // 2)],
                *required_slugs,
            ]
        )[:learned_topical_limit]
        for slug in topical_slugs:
            added_for_slug = 0
            for entry in self._topical_article_entries_for_slug(
                str(slug),
                query_data,
                limit=topical_seed_count,
                excluded_pairs=seen_pair,
            ):
                if optional_contamination(entry):
                    continue
                pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                if pair in seen_pair:
                    continue
                selected.append(self._make_candidate(entry, lexical_score=16.0, lexical_rank=0, forced=True))
                seen_pair.add(pair)
                added_for_slug += 1
                if len(selected) >= limit or added_for_slug >= topical_seed_count:
                    break
            if len(selected) >= limit:
                return selected

        if required_article_pairs:
            article_slug_priority = _dedupe(
                [
                    *query_data.get("required_core_regulations", []),
                    *query_data.get("learned_package_regulations", []),
                    *query_data.get("required_companion_regulations", []),
                    *query_data.get("learned_companion_regulations", []),
                    *[slug for slug, _ in sorted(required_article_pairs)],
                ]
            )
            article_candidates_by_slug: dict[str, list[dict[str, Any]]] = {slug: [] for slug in article_slug_priority}
            for article_slug in article_slug_priority:
                for candidate in ranked:
                    entry = candidate.get("entry") or {}
                    if optional_contamination(entry):
                        continue
                    pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                    if pair[0] != article_slug or pair not in required_article_pairs or pair in seen_pair:
                        continue
                    article_candidates_by_slug.setdefault(article_slug, []).append(candidate)
            progress = True
            while progress and len(selected) < limit:
                progress = False
                for article_slug in article_slug_priority:
                    bucket = article_candidates_by_slug.get(article_slug) or []
                    while bucket:
                        candidate = bucket.pop(0)
                        entry = candidate.get("entry") or {}
                        pair = (str(entry.get("regulation_slug") or ""), int(entry.get("article_index") or 0))
                        if pair in seen_pair:
                            continue
                        selected.append(candidate)
                        seen_pair.add(pair)
                        progress = True
                        break
                    if len(selected) >= limit:
                        return selected

        selected_slug_set.update({
            str(candidate.get("entry", {}).get("regulation_slug") or "")
            for candidate in selected
        })
        for slug in required_slugs:
            if slug in selected_slug_set:
                continue
            for candidate in ranked:
                entry = candidate.get("entry") or {}
                if entry.get("regulation_slug") != slug:
                    continue
                pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
                if pair in seen_pair:
                    continue
                selected.append(candidate)
                seen_pair.add(pair)
                selected_slug_set.add(str(slug))
                break
            if len(selected) >= limit:
                return selected

        learned_seed_count = 0
        learned_seed_slugs: list[str] = []
        for slug in _dedupe(query_data.get("learned_package_regulations", [])):
            if slug in required_slug_set:
                continue
            for candidate in ranked:
                entry = candidate.get("entry") or {}
                if entry.get("regulation_slug") != slug or optional_contamination(entry):
                    continue
                pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
                if pair in seen_pair:
                    continue
                selected.append(candidate)
                seen_pair.add(pair)
                learned_seed_count += 1
                learned_seed_slugs.append(str(slug))
                break
            if len(selected) >= limit or learned_seed_count >= PACKAGE_ROUTER_CONTEXT_SEED_LIMIT:
                return selected

        learned_companion_set = set(query_data.get("learned_companion_regulations", []))
        for slug in learned_seed_slugs:
            for companion_slug in DEFAULT_COMPANION_REGULATIONS_BY_CORE.get(slug, ()):
                if companion_slug not in learned_companion_set or companion_slug in selected_slug_set:
                    continue
                for candidate in ranked:
                    entry = candidate.get("entry") or {}
                    if entry.get("regulation_slug") != companion_slug or optional_contamination(entry):
                        continue
                    pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
                    if pair in seen_pair:
                        continue
                    selected.append(candidate)
                    seen_pair.add(pair)
                    selected_slug_set.add(str(companion_slug))
                    break
                if len(selected) >= limit:
                    return selected

        forced_slug_counts = Counter(str(candidate.get("entry", {}).get("regulation_slug") or "") for candidate in selected)
        forced_first_pass_limit_per_slug = 5
        for candidate in ranked:
            if not candidate.get("forced"):
                continue
            entry = candidate["entry"]
            if optional_contamination(entry):
                continue
            slug = str(entry.get("regulation_slug") or "")
            if forced_slug_counts[slug] >= forced_first_pass_limit_per_slug:
                continue
            pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
            if pair in seen_pair:
                continue
            selected.append(candidate)
            seen_pair.add(pair)
            forced_slug_counts[slug] += 1
            if len(selected) >= limit:
                return selected

        for candidate in ranked:
            if not candidate.get("forced"):
                continue
            entry = candidate["entry"]
            if optional_contamination(entry):
                continue
            pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
            if pair in seen_pair:
                continue
            selected.append(candidate)
            seen_pair.add(pair)
            if len(selected) >= limit:
                return selected

        for candidate in ranked:
            entry = candidate["entry"]
            if optional_contamination(entry):
                continue
            pair = (entry.get("regulation_slug") or "", int(entry.get("article_index") or 0))
            if pair in seen_pair:
                continue
            selected.append(candidate)
            seen_pair.add(pair)
            if len(selected) >= limit:
                break
        return selected

    async def _hybrid_retrieve(
        self,
        question: str,
        answer_mode: str = "consultation",
        retrieval_profile: str = "",
    ) -> dict[str, Any]:
        query_data = self._analyze_query(question, retrieval_profile)
        dense_candidates, lexical_candidates = await asyncio.gather(
            self._dense_candidates(question, query_data),
            asyncio.to_thread(self._lexical_candidates, query_data),
        )
        forced = self._forced_required_candidates(query_data)
        ranked = self._merge_and_rank([*forced, *dense_candidates, *lexical_candidates], query_data)
        selected = self._select_context(ranked, query_data)
        selected = self._rank_selected_context(selected, query_data)
        return {
            "ranked_candidates": ranked,
            "selected_candidates": selected,
            "query_data": query_data,
        }

    def _source_text(self, candidate: dict[str, Any]) -> str:
        entry = candidate["entry"]
        citation = entry.get("citation_short_ar") or ""
        text = entry.get("text") or entry.get("text_verbatim") or ""
        return f"{citation}\n{text}".strip()

    def _diagnostics(self, retrieval_result: dict[str, Any]) -> dict[str, Any]:
        selected = retrieval_result.get("selected_candidates") or []
        ranked = retrieval_result.get("ranked_candidates") or []
        query_data = retrieval_result.get("query_data") or {}
        required_core = query_data.get("required_core_regulations", [])
        required_companion = query_data.get("required_companion_regulations", [])
        required_regs = query_data.get("required_regulations", [])
        selected_slugs = [candidate["entry"].get("regulation_slug") for candidate in selected if candidate.get("entry")]
        selected_articles = [
            int(candidate["entry"].get("article_index") or 0)
            for candidate in selected
            if candidate.get("entry") and int(candidate["entry"].get("article_index") or 0)
        ]
        selected_article_pairs = {
            (str(candidate["entry"].get("regulation_slug") or ""), int(candidate["entry"].get("article_index") or 0))
            for candidate in selected
            if candidate.get("entry") and int(candidate["entry"].get("article_index") or 0)
        }
        selected_article_pair_keys = [f"{slug}:{article}" for slug, article in sorted(selected_article_pairs)]
        coverage_packer_pairs = sorted(
            {
                (
                    str(candidate["entry"].get("regulation_slug") or ""),
                    int(candidate["entry"].get("article_index") or 0),
                )
                for candidate in selected
                if candidate.get("coverage_packer") and candidate.get("entry") and int(candidate["entry"].get("article_index") or 0)
            }
        )
        coverage_packer_pair_keys = [f"{slug}:{article}" for slug, article in coverage_packer_pairs]
        phrase_article_pair_keys = [
            f"{slug}:{article}"
            for slug, articles in sorted((query_data.get("phrase_articles_by_slug") or {}).items())
            for article in sorted(int(item) for item in articles)
            if int(article) > 0
        ]
        heldout_axis_packer_pairs = sorted(
            {
                (
                    str(candidate["entry"].get("regulation_slug") or ""),
                    int(candidate["entry"].get("article_index") or 0),
                )
                for candidate in selected
                if candidate.get("heldout_axis_packer")
                and candidate.get("entry")
                and int(candidate["entry"].get("article_index") or 0)
            }
        )
        heldout_axis_packer_pair_keys = [f"{slug}:{article}" for slug, article in heldout_axis_packer_pairs]
        ranked_article_rank: dict[str, int] = {}
        for index, candidate in enumerate(ranked, start=1):
            entry = candidate.get("entry") or {}
            slug = str(entry.get("regulation_slug") or "")
            article = int(entry.get("article_index") or 0)
            if slug and article:
                ranked_article_rank.setdefault(f"{slug}:{article}", index)
        selected_context_position: dict[str, int] = {}
        for index, candidate in enumerate(selected, start=1):
            entry = candidate.get("entry") or {}
            slug = str(entry.get("regulation_slug") or "")
            article = int(entry.get("article_index") or 0)
            if slug and article:
                selected_context_position.setdefault(f"{slug}:{article}", index)
        selected_article_context_positions = {
            key: selected_context_position.get(key) for key in selected_article_pair_keys
        }
        top_regulations = _dedupe([*required_regs, *selected_slugs])[:12]
        top_articles = sorted(set(selected_articles))

        selected_slug_set = set(selected_slugs)
        covered_core = [slug for slug in required_core if slug in selected_slug_set]
        covered_companion = [slug for slug in required_companion if slug in selected_slug_set]
        missing_core = [slug for slug in required_core if slug not in selected_slug_set]
        missing_companion = [slug for slug in required_companion if slug not in selected_slug_set]
        expected_article_pairs = {
            (str(slug), int(article))
            for slug, articles in (query_data.get("required_articles_by_slug") or {}).items()
            for article in articles
        }
        covered_article_pairs = selected_article_pairs & expected_article_pairs
        missing_article_pairs = expected_article_pairs - selected_article_pairs
        expected_direct = sorted({article for _, article in expected_article_pairs})
        covered_direct = sorted({article for _, article in covered_article_pairs})
        missing_direct = sorted({article for _, article in missing_article_pairs})
        expected_bundle = expected_direct
        covered_bundle = covered_direct
        missing_bundle = missing_direct
        expected_article_pair_keys = [f"{slug}:{article}" for slug, article in sorted(expected_article_pairs)]
        expected_article_ranks = {key: ranked_article_rank.get(key) for key in expected_article_pair_keys}
        expected_article_context_positions = {
            key: selected_context_position.get(key) for key in expected_article_pair_keys
        }
        found_rank_values = [rank for rank in expected_article_ranks.values() if rank]
        found_context_values = [position for position in expected_article_context_positions.values() if position]
        expected_article_mrr = (
            sum((1.0 / rank) if rank else 0.0 for rank in expected_article_ranks.values())
            / max(1, len(expected_article_pair_keys))
            if expected_article_pair_keys
            else 1.0
        )
        expected_article_entered_context_rate = (
            len(found_context_values) / max(1, len(expected_article_pair_keys))
            if expected_article_pair_keys
            else 1.0
        )

        relevant_slugs = {
            str(slug)
            for slug in [
                *required_core,
                *required_companion,
                *required_regs,
                *list((query_data.get("required_articles_by_slug") or {}).keys()),
                *query_data.get("learned_package_regulations", []),
                *query_data.get("learned_companion_regulations", []),
                *list((query_data.get("learned_articles_by_slug") or {}).keys()),
                *list((query_data.get("phrase_articles_by_slug") or {}).keys()),
                *query_data.get("heldout_axis_regulations", []),
                *list((query_data.get("heldout_axis_articles_by_slug") or {}).keys()),
            ]
            if str(slug).strip()
        }
        irrelevant_selected = []
        if relevant_slugs:
            for candidate in selected:
                entry = candidate.get("entry") or {}
                slug = str(entry.get("regulation_slug") or "")
                if slug and slug not in relevant_slugs:
                    irrelevant_selected.append(slug)
        irrelevant_laws = sorted(set(irrelevant_selected))
        irrelevant_context_count = len(irrelevant_selected)
        pollution_rate = irrelevant_context_count / max(1, len(selected)) if selected and relevant_slugs else 0.0

        issue_flags = []
        if missing_core:
            issue_flags.append("fatal_core_doc_miss")
        if missing_companion:
            issue_flags.append("missing_companion_regulations")
        if missing_direct or missing_bundle:
            issue_flags.append("thin_article_coverage")

        core_doc_recall = len(covered_core) / max(1, len(required_core)) if required_core else 1.0
        companion_doc_recall = len(covered_companion) / max(1, len(required_companion)) if required_companion else 1.0
        direct_article_recall = (
            len(covered_article_pairs) / max(1, len(expected_article_pairs)) if expected_article_pairs else 1.0
        )
        bundle_article_recall = direct_article_recall
        bundle_completeness = round((core_doc_recall + companion_doc_recall + direct_article_recall + bundle_article_recall) / 4, 3)
        status = "high" if not issue_flags else ("medium" if not missing_core else "low")

        doc_counts = Counter(selected_slugs)
        return {
            "status": status,
            "quality_status": status,
            "retrieval_profile": query_data.get("retrieval_profile"),
            "retrieval_profile_config": query_data.get("retrieval_profile_config", {}),
            "question": query_data.get("question", ""),
            "issue_flags": issue_flags,
            "dominant_domain": query_data.get("dominant_domain"),
            "top_regulations": top_regulations,
            "top_articles": top_articles,
            "query_roles": query_data.get("query_roles", []),
            "covered_roles": query_data.get("query_roles", []),
            "missing_roles": [],
            "issue_count": max(1, len(query_data.get("matched_document_bundles", []))),
            "covered_issue_ids": query_data.get("matched_document_bundles", []),
            "missing_issue_ids": [],
            "missing_issue_domains": [],
            "matched_document_bundles": query_data.get("matched_document_bundles", []),
            "required_claim_intents": query_data.get("required_claim_intents", []),
            "covered_claim_intents": query_data.get("required_claim_intents", []),
            "missing_claim_intents": [],
            "expected_direct_articles": expected_direct,
            "covered_direct_articles": covered_direct,
            "missing_direct_articles": missing_direct,
            "expected_direct_article_pairs": expected_article_pair_keys,
            "selected_article_pairs": selected_article_pair_keys,
            "selected_article_context_positions": selected_article_context_positions,
            "phrase_article_pairs": phrase_article_pair_keys,
            "phrase_article_count": len(phrase_article_pair_keys),
            "coverage_packer_article_pairs": coverage_packer_pair_keys,
            "coverage_packer_article_count": len(coverage_packer_pair_keys),
            "heldout_axis_hints": query_data.get("heldout_axis_hints", []),
            "heldout_axis_hint_count": len(query_data.get("heldout_axis_hints", [])),
            "heldout_axis_article_pairs": [
                f"{slug}:{article}"
                for slug, articles in sorted((query_data.get("heldout_axis_articles_by_slug") or {}).items())
                for article in sorted(int(item) for item in articles)
            ],
            "heldout_axis_packer_article_pairs": heldout_axis_packer_pair_keys,
            "heldout_axis_packer_article_count": len(heldout_axis_packer_pair_keys),
            "covered_direct_article_pairs": [f"{slug}:{article}" for slug, article in sorted(covered_article_pairs)],
            "missing_direct_article_pairs": [f"{slug}:{article}" for slug, article in sorted(missing_article_pairs)],
            "direct_article_recall": round(direct_article_recall, 3),
            "expected_article_ranks": expected_article_ranks,
            "expected_article_context_positions": expected_article_context_positions,
            "expected_article_best_rank": min(found_rank_values) if found_rank_values else None,
            "expected_article_mean_rank": round(sum(found_rank_values) / len(found_rank_values), 1) if found_rank_values else None,
            "expected_article_mrr": round(expected_article_mrr, 4),
            "expected_article_best_context_position": min(found_context_values) if found_context_values else None,
            "expected_article_mean_context_position": (
                round(sum(found_context_values) / len(found_context_values), 1) if found_context_values else None
            ),
            "expected_article_entered_context_rate": round(expected_article_entered_context_rate, 3),
            "pollution_rate": round(pollution_rate, 3),
            "irrelevant_context_count": irrelevant_context_count,
            "irrelevant_law_count": len(irrelevant_laws),
            "irrelevant_laws": irrelevant_laws[:24],
            "expected_bundle_articles": expected_bundle,
            "covered_bundle_articles": covered_bundle,
            "missing_bundle_articles": missing_bundle,
            "expected_bundle_article_pairs": expected_article_pair_keys,
            "covered_bundle_article_pairs": [f"{slug}:{article}" for slug, article in sorted(covered_article_pairs)],
            "missing_bundle_article_pairs": [f"{slug}:{article}" for slug, article in sorted(missing_article_pairs)],
            "bundle_article_recall": round(bundle_article_recall, 3),
            "required_core_regulations": required_core,
            "covered_core_regulations": covered_core,
            "missing_core_regulations": missing_core,
            "core_doc_recall": round(core_doc_recall, 3),
            "required_companion_regulations": required_companion,
            "covered_companion_regulations": covered_companion,
            "missing_companion_regulations": missing_companion,
            "companion_doc_recall": round(companion_doc_recall, 3),
            "bundle_completeness": bundle_completeness,
            "fatal_core_doc_miss": bool(missing_core),
            "document_class_counts": dict(doc_counts),
            "missing_primary_law_anchor": bool(missing_core),
            "procedural_or_supplementary_drift": False,
            "unsupported_domain_signals": [],
            "reference_date_signals": [],
            "domain_policy": {"recall_first": True, "allow_extra_noise": True},
            "helper_failures": [],
            "primary_ratio": round(len(covered_core) / max(1, len(top_regulations)), 3) if top_regulations else 1.0,
            "dominant_concentration": round(max(doc_counts.values(), default=0) / max(1, len(selected)), 3),
            "unique_article_count": len(set(selected_articles)),
            "ranked_candidate_count": len(ranked),
            "selected_candidate_count": len(selected),
        }

    def _regulation_title(self, slug: str) -> str:
        return self._title_by_slug.get(slug) or REGULATION_TITLE_OVERRIDES.get(slug) or slug

    def _regulation_citation_title(self, slug: str) -> str:
        for entry in self._entries_by_slug.get(slug, []):
            title = str(entry.get("regulation_title_ar") or "").strip()
            if title:
                return title
        return self._regulation_title(slug)

    def _parse_article_pair_key(self, value: Any) -> tuple[str, int] | None:
        raw = str(value or "").strip()
        if ":" not in raw:
            return None
        slug, article_text = raw.rsplit(":", 1)
        slug = slug.strip()
        try:
            article = int(article_text)
        except Exception:
            return None
        if not slug or article <= 0:
            return None
        return slug, article

    def _answer_article_pair_keys(self, diagnostics: dict[str, Any]) -> list[str]:
        fields = (
            "phrase_article_pairs",
            "covered_direct_article_pairs",
            "coverage_packer_article_pairs",
            "heldout_axis_packer_article_pairs",
            "selected_article_pairs",
        )
        ordered: list[str] = []
        seen: set[str] = set()
        for field in fields:
            for value in diagnostics.get(field, []) or []:
                parsed = self._parse_article_pair_key(value)
                if not parsed:
                    continue
                key = f"{parsed[0]}:{parsed[1]}"
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(key)
        return ordered

    def _answer_pair_priority(self, pair_key: str, diagnostics: dict[str, Any]) -> float:
        entry = self._article_entry_for_pair_key(pair_key)
        if entry is None:
            return 0.0
        question_tokens = set(_tokens(diagnostics.get("question", "")))
        covered_pairs = self._pair_key_set_from_values(diagnostics.get("covered_direct_article_pairs"))
        selected_pairs = self._pair_key_set_from_values(diagnostics.get("selected_article_pairs"))
        phrase_pairs = self._pair_key_set_from_values(diagnostics.get("phrase_article_pairs"))
        coverage_pairs = self._pair_key_set_from_values(diagnostics.get("coverage_packer_article_pairs"))
        heldout_pairs = self._pair_key_set_from_values(diagnostics.get("heldout_axis_packer_article_pairs"))
        expected_pairs = self._pair_key_set_from_values(diagnostics.get("expected_direct_article_pairs"))
        context_positions = diagnostics.get("expected_article_context_positions") or {}

        score = 0.0
        if pair_key in phrase_pairs:
            score += 140.0
        if pair_key in heldout_pairs:
            score += 120.0
        if pair_key in coverage_pairs:
            score += 80.0
        if pair_key in expected_pairs:
            score += 62.0
        if pair_key in covered_pairs:
            score += 48.0
        if pair_key in selected_pairs:
            score += 25.0
        score += self._question_overlap_score(entry, question_tokens)
        score += self._materiality_score(
            entry,
            {
                "question": diagnostics.get("question", ""),
                "query_roles": diagnostics.get("query_roles", []),
                "required_articles_by_slug": {},
            },
        ) * 2.0
        try:
            position = int(context_positions.get(pair_key) or 0)
        except Exception:
            position = 0
        if position > 0:
            score += max(0.0, 90.0 - (position * 2.0))
        return score

    def _rank_answer_article_pair_keys(self, pair_keys: list[str], diagnostics: dict[str, Any]) -> list[str]:
        return sorted(
            pair_keys,
            key=lambda key: (self._answer_pair_priority(key, diagnostics), -pair_keys.index(key)),
            reverse=True,
        )

    def _article_pair_slugs(self, pair_keys: list[str]) -> list[str]:
        slugs: list[str] = []
        seen: set[str] = set()
        for key in pair_keys:
            parsed = self._parse_article_pair_key(key)
            if not parsed:
                continue
            slug = parsed[0]
            if slug in seen:
                continue
            seen.add(slug)
            slugs.append(slug)
        return slugs

    def _format_article_pair_lines(
        self,
        pair_keys: list[str],
        *,
        max_pairs: int = 96,
        max_per_regulation: int = 12,
        max_regulations: int = 16,
    ) -> list[str]:
        grouped: dict[str, list[int]] = {}
        slug_order: list[str] = []
        total_pairs = 0
        for key in pair_keys:
            if total_pairs >= max_pairs:
                break
            parsed = self._parse_article_pair_key(key)
            if not parsed:
                continue
            slug, article = parsed
            if slug not in grouped and len(slug_order) >= max_regulations:
                continue
            if slug not in grouped:
                grouped[slug] = []
                slug_order.append(slug)
            if article in grouped[slug] or len(grouped[slug]) >= max_per_regulation:
                continue
            grouped[slug].append(article)
            total_pairs += 1

        lines: list[str] = []
        for slug in slug_order:
            articles = sorted(grouped.get(slug) or [])
            if not articles:
                continue
            article_line = "، ".join(f"المادة {article}" for article in articles)
            lines.append(f"- {self._regulation_citation_title(slug)}: {article_line}")
        return lines

    def _build_answer(self, diagnostics: dict[str, Any], answer_mode: str) -> str:
        top_slugs = diagnostics.get("top_regulations", [])
        article_pair_keys = self._rank_answer_article_pair_keys(
            self._answer_article_pair_keys(diagnostics),
            diagnostics,
        )
        article_slugs = self._article_pair_slugs(article_pair_keys)
        mandatory_slugs = _dedupe(
            [
                *diagnostics.get("required_core_regulations", []),
                *diagnostics.get("required_companion_regulations", []),
            ]
        )
        title_slugs = _dedupe([*mandatory_slugs, *article_slugs, *top_slugs])
        shown_title_slugs = title_slugs[:18]
        extra_slugs = [slug for slug in top_slugs if slug not in set(shown_title_slugs)]
        titles = [self._regulation_title(slug) for slug in shown_title_slugs]
        extra_titles = [self._regulation_title(slug) for slug in extra_slugs]
        direct_articles = diagnostics.get("covered_direct_articles") or diagnostics.get("top_articles", [])
        missing_core = diagnostics.get("missing_core_regulations", [])
        missing_companion = diagnostics.get("missing_companion_regulations", [])
        article_pair_lines = self._format_article_pair_lines(article_pair_keys)
        direct_line = (
            "\n".join(article_pair_lines)
            or "، ".join(f"المادة {article}" for article in direct_articles[:32])
            or "لا توجد مواد مباشرة كافية"
        )

        if answer_mode == "benchmark":
            return "\n".join(
                [
                    "1. النظام المنطبق",
                    "؛ ".join(titles) if titles else "لم يظهر نظام منطبق بدرجة كافية.",
                    "",
                    "2. المواد الأقرب",
                    "المواد المباشرة المرتبطة بالنظام:\n" + direct_line,
                    "المواد المساندة: تظهر بحسب الحزمة النظامية المرافقة في المصادر المسترجعة.",
                    "",
                    "3. ملاحظات الاسترجاع",
                    "- الحزمة مبنية على استرجاع جامع يوازن الدلالة 70% واللفظ 30% في ملف jamia_recall.",
                    (
                        f"- مراجع إضافية ظهرت في الجمع الواسع وليست أساس الحزمة: {'؛ '.join(extra_titles)}"
                        if extra_titles
                        else "- لم تظهر مراجع إضافية خارج الحزمة الإلزامية في رأس النتيجة."
                    ),
                    (
                        f"- فجوات تشغيلية في الحزمة: core={missing_core} companion={missing_companion}"
                        if missing_core or missing_companion
                        else "- لا تظهر فجوة استرجاعية جوهرية في هذه النتيجة وفق الحزمة الحالية."
                    ),
                    "",
                    "4. الخلاصة العملية",
                    "هذه صيغة benchmark سريعة تركز على جمع الأنظمة والمواد الظاهرة، وليست صياغة استشارية موسعة.",
                ]
            )

        return "\n".join(
            [
                "1. النظام المنطبق",
                "؛ ".join(titles) if titles else "لم يظهر نظام منطبق بدرجة كافية.",
                "",
                "2. الحكم المباشر",
                "النصوص المسترجعة تشير إلى وجوب معالجة الواقعة من خلال الحزمة النظامية أعلاه، مع تنزيل المواد بحسب كل محور من محاور السؤال.",
                "",
                "3. المواد المستند إليها",
                direct_line,
                "",
                "4. القيود أو الاستثناءات",
                "يلزم التحقق من شروط الواقعة والعقد والإفصاحات والموافقات والمواعيد قبل الجزم بالنتيجة النهائية.",
                "",
                "5. ما لم يثبته النص",
                "إذا لم تظهر لائحة قطاعية متخصصة ضمن قاعدة المعرفة فلا تعد غائبة قانونيا، بل غائبة عن النصوص المسترجعة.",
                "",
                "6. الخلاصة العملية",
                "الأولوية العملية هي تثبيت النظام الحاكم ثم اللوائح والضوابط المرافقة، وبعدها تصفية النصوص غير اللازمة.",
            ]
        )

    async def query(
        self,
        question: str,
        answer_mode: str = "consultation",
        retrieval_profile: str = "",
    ) -> RAGResult:
        retrieval_result = await self._hybrid_retrieve(question, answer_mode=answer_mode, retrieval_profile=retrieval_profile)
        diagnostics = self._diagnostics(retrieval_result)
        selected = retrieval_result.get("selected_candidates") or []
        sources = [self._source_text(candidate) for candidate in selected[:30]]
        confidence = "high" if diagnostics["status"] == "high" else ("medium" if diagnostics["status"] == "medium" else "low")
        answer = self._build_answer(diagnostics, answer_mode)
        return RAGResult(
            answer=answer,
            confidence=confidence,
            sources=sources,
            needs_escalation=confidence == "low",
            diagnostics=diagnostics,
        )


_ENGINE: LegalRAGEngine | None = None


def get_engine() -> LegalRAGEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = LegalRAGEngine()
    return _ENGINE
